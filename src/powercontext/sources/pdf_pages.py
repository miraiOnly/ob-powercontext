# Copyright (c) 2026 OceanBase.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Full-document PDF page evidence with page-level exact citation search."""

from __future__ import annotations

import hashlib
import io
import math
import re
from collections import Counter
from urllib.parse import quote

from pydantic import BaseModel, ConfigDict, field_validator

from powercontext.sources import AdapterSourceDefinition, TEXT_EVIDENCE_PROJECTION_KEY, TextEvidence
from powercontext.sources.models import Source, SourceMaterialization

PDF_PAGE_SOURCE_NAME = "pdf-page"
PDF_PAGE_SOURCE_VERSION = "1"
MAX_PDF_SOURCE_ID_LENGTH = 256
_PDF_SEARCH_POOL_LIMIT = 400
_PDF_TERM_POOL_LIMIT = 120
_PDF_MAX_POOL = 900
_PDF_TOKEN_PATTERN = re.compile(r"[a-z]+|\d+(?:\.\d+)?")
_PDF_STOPWORDS = frozenset(
    {
        "a", "an", "the", "of", "in", "on", "at", "to", "for", "with", "by",
        "is", "are", "was", "were", "be", "been", "being", "has", "have",
        "had", "do", "does", "did", "and", "or", "not", "no", "but", "if",
        "then", "than", "that", "this", "these", "those", "it", "its", "as",
        "from", "into", "over", "under", "about", "which", "who", "whom",
        "what", "when", "where", "how", "why", "their", "there", "they",
        "them", "he", "she", "we", "you", "i", "his", "her", "our", "their",
        "can", "could", "should", "would", "will", "may", "might", "must",
        "shall", "also", "such", "per", "between", "among", "during",
        "after", "before", "because", "while", "though", "so", "more",
        "most", "some", "any", "each", "every", "both", "other", "only",
        "very", "just", "using", "used", "use", "via", "whether", "either",
        "total", "amount", "year", "years", "fiscal", "company", "report",
        "following", "table", "million", "billion", "thousand", "net",
        "give", "given", "provided", "primarily", "based", "please",
        "assume", "answer", "question", "statement", "statements",
    }
)
# Generic financial vocabulary bridging question phrasing to report language.
_PDF_EXPANSIONS = {
    "battles": ("litigation", "proceedings", "claims", "lawsuits", "disputes", "investigations"),
    "geographies": ("segments", "divisions", "regions", "markets", "international"),
    "capex": ("expenditures", "capital", "purchases"),
    "ebitda": ("depreciation", "amortization", "profit"),
    "operates": ("operations", "operating", "businesses"),
    "litigation": ("proceedings", "claims", "disputes", "lawsuits"),
    "revenue": ("sales",),
    "workforce": ("employees", "headcount", "personnel"),
    "employees": ("headcount", "workforce", "personnel"),
    "profitability": ("margins", "margin", "profit"),
    "unadjusted": ("operating",),
    "capital": ("assets", "equipment", "expenditures", "property"),
}
# Standard financial abbreviations expanded before query analysis.
_PDF_ABBREVIATIONS = (
    (re.compile(r"\br\s*&\s*d\b"), "research development"),
    (re.compile(r"\bd\s*&\s*a\b"), "depreciation amortization"),
    (re.compile(r"\bsg\s*&\s*a\b"), "selling general administrative"),
    (re.compile(r"\bm\s*&\s*a\b"), "mergers acquisitions divestitures"),
    (re.compile(r"\bpp\s*&\s*e\b"), "property plant equipment"),
    (re.compile(r"\bppne\b"), "property plant equipment net"),
    (re.compile(r"\bcapex\b"), "capital expenditures"),
    (re.compile(r"\bebitda\b"), "operating income depreciation amortization"),
)


class PDFDocumentInput(BaseModel):
    """A complete original PDF together with its caller-stable document id."""

    model_config = ConfigDict(frozen=True)

    document_id: str
    content: bytes

    @field_validator("document_id")
    @classmethod
    def validate_document_id(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("document_id must be non-empty and non-blank")  # noqa: TRY003
        return value


class PDFPageInput(BaseModel):
    """One physical page of extracted text with its document identity."""

    model_config = ConfigDict(frozen=True)

    document_id: str
    document_sha256: str
    page_number: int
    text: str


def pdf_page_source_id(document_id: str, document_sha256: str, page_number: int) -> str:
    """Build the stable per-revision page identity."""

    identity = (
        f"{PDF_PAGE_SOURCE_NAME}:"
        f"{quote(document_id, safe='')}:"
        f"{document_sha256}:{page_number}"
    )
    if len(identity) > MAX_PDF_SOURCE_ID_LENGTH:
        # Keep the identity stable and bounded for unusually long caller ids.
        document_fingerprint = hashlib.sha256(document_id.encode("utf-8")).hexdigest()[:24]
        identity = (
            f"{PDF_PAGE_SOURCE_NAME}:"
            f"{quote(document_id, safe='')[:180]}:"
            f"{document_fingerprint}:"
            f"{document_sha256}:{page_number}"
        )
    if len(identity) > MAX_PDF_SOURCE_ID_LENGTH:  # pragma: no cover - defensive
        raise ValueError(
            f"pdf page source_id must not exceed {MAX_PDF_SOURCE_ID_LENGTH} characters",
        )  # noqa: TRY003
    return identity


def parse_pdf_document(value: PDFDocumentInput) -> tuple[PDFPageInput, ...]:
    """Extract every physical page; fail atomically on any parsing error."""

    if not value.document_id or not value.document_id.strip():
        raise ValueError("document_id must be non-empty and non-blank")  # noqa: TRY003
    if not value.content:
        raise ValueError("pdf document must not be empty")  # noqa: TRY003

    try:
        from pypdf import PdfReader
    except ImportError as error:  # pragma: no cover - locked dependency
        raise ValueError("pypdf is required to parse PDF documents") from error  # noqa: TRY003

    digest = hashlib.sha256(value.content).hexdigest()

    def bounded(text: str) -> str:
        data = text.encode("utf-8")
        if len(data) <= 8192:
            return text
        return data[:8192].decode("utf-8", errors="ignore")

    try:
        reader = PdfReader(io.BytesIO(value.content))
        if reader.is_encrypted:
            try:
                reader.decrypt("")
            except Exception as error:  # noqa: BLE001
                raise ValueError("pdf document cannot be decrypted") from error  # noqa: TRY003
        pages = reader.pages
        texts: list[str] = []
        for page in pages:
            try:
                extracted = page.extract_text()
            except Exception as error:  # noqa: BLE001
                raise ValueError("pdf document cannot be fully parsed") from error  # noqa: TRY003
            texts.append(bounded(extracted) if isinstance(extracted, str) else "")
    except ValueError:
        raise
    except Exception as error:  # noqa: BLE001
        raise ValueError("pdf document cannot be parsed") from error  # noqa: TRY003

    return tuple(
        PDFPageInput(
            document_id=value.document_id,
            document_sha256=digest,
            page_number=index + 1,
            text=text,
        )
        for index, text in enumerate(texts)
    )


class PDFPageSource(Source):
    """Captured physical page identified by document, revision and page."""

    document_id: str
    document_sha256: str
    page_number: int
    text: str

    @property
    def source_id(self) -> str:
        """Alias of the stable page identity carried by ``name``."""

        return self.name


class PDFPageSourceAdapter:
    """Resolve and read one PDF page as a captured Source."""

    input_class = PDFPageInput
    name = PDF_PAGE_SOURCE_NAME
    source_class = PDFPageSource

    async def resolve(self, value: PDFPageInput, /) -> PDFPageSource:
        identity = pdf_page_source_id(value.document_id, value.document_sha256, value.page_number)
        return PDFPageSource(
            name=identity,
            materialization=SourceMaterialization.CAPTURED,
            document_id=value.document_id,
            document_sha256=value.document_sha256,
            page_number=value.page_number,
            text=value.text,
        )

    async def read(self, source: PDFPageSource, /) -> PDFPageInput:
        return PDFPageInput(
            document_id=source.document_id,
            document_sha256=source.document_sha256,
            page_number=source.page_number,
            text=source.text,
        )


class PDFPageTextEvidenceProjection:
    """Project one page to the standard text-evidence shape."""

    name = TEXT_EVIDENCE_PROJECTION_KEY.name
    version = TEXT_EVIDENCE_PROJECTION_KEY.version
    source_class = PDFPageSource
    output_class = TextEvidence

    def project(self, source: PDFPageSource, /) -> TextEvidence:
        return TextEvidence(
            source_type=PDF_PAGE_SOURCE_NAME,
            source_id=source.name,
            content=f"{source.document_id}\n{source.text}",
            metadata={
                "document_id": source.document_id,
                "document_sha256": source.document_sha256,
                "page_number": source.page_number,
            },
        )


PDF_PAGE_SOURCE_ADAPTER = PDFPageSourceAdapter()
PDF_PAGE_SOURCE_DEFINITION = AdapterSourceDefinition(
    PDF_PAGE_SOURCE_ADAPTER,
    version=PDF_PAGE_SOURCE_VERSION,
    projections=(PDFPageTextEvidenceProjection(),),
)


_STATEMENT_QUESTION_TERMS = frozenset(
    {
        "assets", "liabilities", "equity", "cash", "flows", "income", "revenue",
        "sales", "operating", "depreciation", "amortization", "equipment",
        "expenditures", "capital", "balance", "sheet", "property", "plant",
        "stockholders", "earnings",
    }
)


async def search_pdf_pages(
    memory,
    question: str,
    limit: int = 10,
) -> tuple:
    """Search the scoped Memory and return distinct exact page citations.

    Candidate pools from several FTS queries (full question, individual
    content terms and their generic finance synonyms, plus a
    pseudo-relevance feedback expansion) are merged and re-ranked with an
    IDF-weighted BM25 score and query-bigram bonuses, so evidence pages
    whose phrasing differs from the question still rank well.
    """

    from powercontext.builtin.artifacts.memory.models import MemoryCitation
    from powercontext.builtin.runtime import GetMemoryEntryRequest, SearchMemoryRequest

    if limit < 1:
        return ()

    normalized_question = question.casefold()
    for pattern, replacement in _PDF_ABBREVIATIONS:
        normalized_question = pattern.sub(replacement, normalized_question)

    async def fetch(query: str, count: int):
        page = await memory.search(SearchMemoryRequest(query=query, mode="fts", limit=count))
        return page.hits

    ordered_terms = []
    weights: Counter = Counter()
    for token in _PDF_TOKEN_PATTERN.findall(normalized_question):
        if token in _PDF_STOPWORDS or (len(token) == 1 and not token.isdigit()):
            continue
        ordered_terms.append(token)
        weights[token] += 1
    question_bigrams = set(zip(ordered_terms, ordered_terms[1:]))

    pool: dict[tuple[str, str], object] = {}
    pool_order: list[object] = []

    def add(hits) -> None:
        for hit in hits:
            key = (hit.entry_id, hit.entry_version_id)
            if key not in pool and len(pool) < _PDF_MAX_POOL:
                pool[key] = hit
                pool_order.append(hit)

    add(await fetch(question, _PDF_SEARCH_POOL_LIMIT))
    full_order = list(pool_order)

    expansion_terms: list[str] = []
    for term in ordered_terms[:14]:
        for expansion in _PDF_EXPANSIONS.get(term, ()):
            if expansion not in expansion_terms and expansion not in weights:
                expansion_terms.append(expansion)
    term_ranks: list[tuple[str, float, dict]] = []
    for term in ordered_terms[:14]:
        result = await fetch(term, _PDF_TERM_POOL_LIMIT)
        add(result)
        term_ranks.append(
            (
                term,
                1.0,
                {
                    (hit.entry_id, hit.entry_version_id): rank
                    for rank, hit in enumerate(result, 1)
                    if rank <= 40
                },
            )
        )
    for expansion in expansion_terms:
        result = await fetch(expansion, _PDF_TERM_POOL_LIMIT)
        add(result)
        term_ranks.append(
            (
                expansion,
                0.7,
                {
                    (hit.entry_id, hit.entry_version_id): rank
                    for rank, hit in enumerate(result, 1)
                    if rank <= 40
                },
            )
        )
    if set(weights) & _STATEMENT_QUESTION_TERMS:
        result = await fetch("consolidated", _PDF_TERM_POOL_LIMIT)
        add(result)
        term_ranks.append(
            (
                "consolidated",
                0.8,
                {
                    (hit.entry_id, hit.entry_version_id): rank
                    for rank, hit in enumerate(result, 1)
                    if rank <= 40
                },
            )
        )

    # Pseudo-relevance feedback: terms shared by several top-ranked pages.
    feedback_counts: Counter = Counter()
    feedback_bodies = []
    for hit in full_order[:8]:
        text = hit.text
        body = text.split("\n", 1)[1] if "\n" in text else text
        tokens = set(_PDF_TOKEN_PATTERN.findall(body.casefold()))
        feedback_bodies.append(tokens)
        feedback_counts.update(tokens)
    feedback_terms = [
        token
        for token, frequency in feedback_counts.items()
        if frequency >= 2
        and len(token) >= 2
        and token not in _PDF_STOPWORDS
        and token not in weights
        and not (token.isdigit() and token not in weights)
    ][:10]
    if feedback_terms:
        add(
            await fetch(
                " ".join(ordered_terms + expansion_terms + feedback_terms),
                _PDF_SEARCH_POOL_LIMIT,
            )
        )

    hits = pool_order
    if not hits:
        return ()
    bodies: list[str] = []
    for hit in hits:
        text = hit.text
        bodies.append(text.split("\n", 1)[1] if "\n" in text else text)
    tokenized = [Counter(_PDF_TOKEN_PATTERN.findall(body.casefold())) for body in bodies]
    lengths = [max(1, sum(counts.values())) for counts in tokenized]
    average_length = sum(lengths) / len(lengths)
    document_frequency: Counter = Counter()
    for counts in tokenized:
        document_frequency.update(counts.keys())
    pool_size = len(hits)
    idf = {
        token: math.log((pool_size + 1) / (document_frequency[token] + 1)) + 1.0
        for token in document_frequency
    }

    scored_weights = Counter(weights)
    for term in expansion_terms:
        scored_weights[term] += 0.7
    for term in feedback_terms:
        scored_weights[term] += 0.5

    scored: list[tuple[float, int]] = []
    for index, hit in enumerate(hits):
        counts = tokenized[index]
        score = 0.0
        for token, frequency in counts.items():
            weight = scored_weights.get(token)
            if not weight:
                continue
            score += (
                weight
                * idf.get(token, 1.0)
                * (frequency * 2.2)
                / (frequency + 1.2 * (0.25 + 0.75 * lengths[index] / average_length))
            )
        body_terms = _PDF_TOKEN_PATTERN.findall(bodies[index].casefold())
        body_bigrams = set(zip(body_terms, body_terms[1:]))
        for pair in body_bigrams & question_bigrams:
            score += 2.0 * (idf.get(pair[0], 1.0) + idf.get(pair[1], 1.0))
        title_terms = set(body_terms[:12])
        for token in title_terms & set(scored_weights):
            score += 2.0 * idf.get(token, 1.0)
        if "consolidated" in body_terms[:40] and (set(scored_weights) & _STATEMENT_QUESTION_TERMS):
            score += 3.0
        key = (hit.entry_id, hit.entry_version_id)
        for term, term_weight, ranks in term_ranks:
            rank = ranks.get(key)
            if rank is not None:
                pair_idf = sum(idf.get(part, 1.0) for part in term.split())
                score += term_weight * 0.8 * pair_idf / (2 + rank)
        scored.append((score, index))
    scored.sort(key=lambda pair: -pair[0])

    seen_sources: set[str] = set()
    citations: list[MemoryCitation] = []
    for _, index in scored:
        hit = hits[index]
        citation = MemoryCitation(
            memory_ref=hit.memory_ref,
            entry_id=hit.entry_id,
            entry_version_id=hit.entry_version_id,
        )
        try:
            entry = (await memory.get(GetMemoryEntryRequest(citation=citation))).entry
        except Exception:  # noqa: BLE001 - skip candidates that cannot be resolved
            continue
        sources = entry.sources or ()
        if not sources:
            continue
        source_id = sources[0].source_id
        if source_id in seen_sources:
            continue
        seen_sources.add(source_id)
        citations.append(citation)
        if len(citations) >= limit:
            break
    return tuple(citations)


__all__ = [
    "PDFDocumentInput",
    "PDFPageInput",
    "PDFPageSource",
    "PDF_PAGE_SOURCE_DEFINITION",
    "PDF_PAGE_SOURCE_NAME",
    "PDF_PAGE_SOURCE_VERSION",
    "parse_pdf_document",
    "pdf_page_source_id",
    "search_pdf_pages",
]
