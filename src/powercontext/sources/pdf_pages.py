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
from urllib.parse import quote

from pydantic import BaseModel, ConfigDict, field_validator

from powercontext.sources import AdapterSourceDefinition, TEXT_EVIDENCE_PROJECTION_KEY, TextEvidence
from powercontext.sources.models import Source, SourceMaterialization

PDF_PAGE_SOURCE_NAME = "pdf-page"
PDF_PAGE_SOURCE_VERSION = "1"
MAX_PDF_SOURCE_ID_LENGTH = 256


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
            texts.append(extracted if isinstance(extracted, str) else "")
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


async def search_pdf_pages(
    memory,
    question: str,
    limit: int = 10,
) -> tuple:
    """Search the scoped Memory and return distinct exact page citations."""

    from powercontext.builtin.artifacts.memory.models import MemoryCitation
    from powercontext.builtin.runtime import GetMemoryEntryRequest, SearchMemoryRequest

    if limit < 1:
        limit = 10
    request = SearchMemoryRequest(
        query=question,
        mode="fts",
        limit=max(limit * 2, 20),
    )
    page = await memory.search(request)
    seen_sources: set[str] = set()
    citations: list[MemoryCitation] = []
    for hit in page.hits:
        citation = MemoryCitation(
            memory_ref=hit.memory_ref,
            entry_id=hit.entry_id,
            entry_version_id=hit.entry_version_id,
        )
        entry = (await memory.get(GetMemoryEntryRequest(citation=citation))).entry
        sources = entry.sources or ()
        if not sources:
            continue
        if sources[0].source_id in seen_sources:
            continue
        seen_sources.add(sources[0].source_id)
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
