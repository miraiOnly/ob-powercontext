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

"""Query-driven literal evidence fragment selection for prepared context."""

from __future__ import annotations

import json
import math
import re
from collections import Counter
from dataclasses import dataclass

from powercontext.builtin.runtime.prepared_text import TRUST_POLICY

_EVIDENCE_BEGIN = "BEGIN_POWERCONTEXT_PREPARED_CONTEXT_V1"
_EVIDENCE_END = "END_POWERCONTEXT_PREPARED_CONTEXT_V1"
_TOKEN_PATTERN = re.compile(r"[a-z]+|\d+(?:\.\d+)?")
_SENTENCE_PATTERN = re.compile(r"[^.!?]+[.!?]*")
_STOPWORDS = frozenset(
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
    }
)


@dataclass(frozen=True)
class PreparedContextFragment:
    """One literal evidence fragment with exact entry coordinates."""

    address: object
    start: int
    end: int
    text: str


@dataclass(frozen=True)
class _Candidate:
    hit: int
    start: int
    end: int
    score: float


def _tokens(text: str) -> list[str]:
    return _TOKEN_PATTERN.findall(text.casefold())


def _query_weights(query: str) -> Counter:
    weights: Counter = Counter()
    for token in _tokens(query):
        if token in _STOPWORDS or (len(token) == 1 and not token.isdigit()):
            continue
        weights[token] += 1
    return weights


def _sentence_spans(text: str) -> tuple[tuple[int, int], ...]:
    spans = []
    for match in _SENTENCE_PATTERN.finditer(text):
        if not match.group().strip():
            continue
        spans.append((match.start(), match.end()))
    return tuple(spans)


def render_evidence_context(fragments) -> str:
    """Render selected fragments inside the prepared-context envelope."""

    items: list[dict[str, object]] = []
    for fragment in fragments:
        address = fragment.address
        items.append(
            {
                "citation": {
                    "scope_id": address.memory.scope_id,
                    "memory_ref": address.memory.artifact.model_dump(mode="json"),
                    "entry_id": address.entry_id,
                    "entry_version_id": address.entry_version_id,
                },
                "content": fragment.text,
                "start": fragment.start,
                "end": fragment.end,
            }
        )
    payload = json.dumps(
        {"trust": "untrusted_history", "items": items},
        ensure_ascii=True,
        separators=(",", ":"),
    )
    return "\n".join((_EVIDENCE_BEGIN, TRUST_POLICY, payload)) + "\n" + _EVIDENCE_END


def select_evidence_fragments(
    query: str,
    candidates,
    max_bytes: int,
) -> tuple[PreparedContextFragment, ...]:
    """Select literal fragments from candidate texts to fit the byte budget.

    ``candidates`` is a tuple of ``(text, address)`` pairs. The top-ranked
    candidate (almost always the claim's cited abstract) receives a large
    share of the budget: its best-scoring sentences become anchors that are
    expanded into contiguous runs, so evidence located in the middle or end
    of the document is covered. The remaining candidates each contribute
    their strongest sentence with what budget is left.
    """

    weights = _query_weights(query)
    texts = [text for text, _ in candidates]
    if not texts or not any(weights):
        return ()
    tokenized = [Counter(_tokens(text)) for text in texts]
    document_frequency: Counter = Counter()
    for counts in tokenized:
        document_frequency.update(counts.keys())
    count = len(texts)
    idf = {token: math.log((count + 1) / (document_frequency[token] + 1)) + 1.0 for token in document_frequency}

    def score_span(hit: int, start: int, end: int) -> float:
        counts = Counter(_tokens(texts[hit][start:end]))
        total = 0.0
        for token, frequency in counts.items():
            weight = weights.get(token)
            if weight:
                total += weight * idf.get(token, 1.0) * (1 + math.log(frequency))
        return total

    per_hit: list[list[_Candidate]] = []
    for hit, text in enumerate(texts):
        sentences = [
            _Candidate(hit, start, end, score_span(hit, start, end))
            for start, end in _sentence_spans(text)
            if score_span(hit, start, end) > 0
        ]
        sentences.sort(key=lambda item: -item.score)
        per_hit.append(sentences)

    selected: list[PreparedContextFragment] = []
    taken: dict[int, list[tuple[int, int]]] = {}

    def overlaps(hit: int, start: int, end: int) -> bool:
        return any(start < span_end and span_start < end for span_start, span_end in taken.get(hit, ()))

    def fits(candidate: _Candidate, cap: int, char_limit: int | None = None) -> PreparedContextFragment | None:
        def trial(start: int, end: int) -> PreparedContextFragment:
            return PreparedContextFragment(
                address=candidates[candidate.hit][1],
                start=start,
                end=end,
                text=texts[candidate.hit][start:end],
            )

        current = list(selected)
        whole_end = candidate.end
        if char_limit is not None and whole_end - candidate.start > char_limit:
            whole_end = candidate.start + char_limit
        whole = trial(candidate.start, whole_end)
        if len(render_evidence_context(tuple(current + [whole])).encode("utf-8")) <= cap:
            return whole
        lower, upper = candidate.start + 80, whole_end
        best = None
        while lower <= upper:
            middle = (lower + upper) // 2
            trimmed = trial(candidate.start, middle)
            if len(render_evidence_context(tuple(current + [trimmed])).encode("utf-8")) <= cap:
                best = trimmed
                lower = middle + 1
            else:
                upper = middle - 1
        return best

    def accept(fragment: PreparedContextFragment, hit: int) -> None:
        selected.append(fragment)
        taken.setdefault(hit, []).append((fragment.start, fragment.end))

    share = int(max_bytes * 0.72)

    # Primary: anchor-run expansion on the top-ranked candidate. The title
    # (first sentence) is excluded from anchor candidates so the run starts
    # inside the abstract body instead of the front matter.
    if texts and texts[0]:
        spans = _sentence_spans(texts[0])
        anchors = sorted(
            (
                _Candidate(0, start, end, score_span(0, start, end))
                for start, end in spans[1:]
            ),
            key=lambda item: -item.score,
        )
        for anchor in anchors:
            if overlaps(0, anchor.start, anchor.end):
                continue
            fragment = fits(anchor, max_bytes)
            if fragment is None:
                continue
            accept(fragment, 0)
            # grow into a contiguous run around the anchor while budget allows
            while True:
                grown = False
                for sentence_start, sentence_end in reversed(spans):
                    if sentence_end <= fragment.start:
                        if any(
                            other_start < fragment.end and sentence_start < other_end
                            for other_start, other_end in taken.get(0, ())[:-1]
                        ):
                            break
                        trial_fragment = PreparedContextFragment(
                            address=candidates[0][1],
                            start=sentence_start,
                            end=fragment.end,
                            text=texts[0][sentence_start : fragment.end],
                        )
                        current = list(selected)
                        current[-1] = trial_fragment
                        if len(render_evidence_context(tuple(current)).encode("utf-8")) <= max_bytes:
                            selected[-1] = trial_fragment
                            taken[0][-1] = (sentence_start, fragment.end)
                            fragment = trial_fragment
                            grown = True
                        break
                for sentence_start, sentence_end in spans:
                    if sentence_start >= fragment.end:
                        if any(
                            other_start < sentence_end and fragment.start < other_end
                            for other_start, other_end in taken.get(0, ())[:-1]
                        ):
                            break
                        trial_fragment = PreparedContextFragment(
                            address=candidates[0][1],
                            start=fragment.start,
                            end=sentence_end,
                            text=texts[0][fragment.start : sentence_end],
                        )
                        current = list(selected)
                        current[-1] = trial_fragment
                        if len(render_evidence_context(tuple(current)).encode("utf-8")) <= max_bytes:
                            selected[-1] = trial_fragment
                            taken[0][-1] = (fragment.start, sentence_end)
                            fragment = trial_fragment
                            grown = True
                        break
                if not grown:
                    break

    # Secondary: each remaining candidate contributes its best sentence
    # within the reserved share, then leftovers are filled by score.
    secondary = [hit for hit in range(1, len(per_hit))]
    picked_any = True
    while picked_any:
        picked_any = False
        for hit in secondary:
            for candidate in per_hit[hit]:
                if overlaps(hit, candidate.start, candidate.end):
                    continue
                fragment = fits(candidate, max_bytes, char_limit=600)
                if fragment is None:
                    continue
                accept(fragment, hit)
                picked_any = True
                break

    remaining = [
        candidate
        for hit in secondary
        for candidate in per_hit[hit]
    ]
    remaining.sort(key=lambda item: -item.score)
    for candidate in remaining:
        if overlaps(candidate.hit, candidate.start, candidate.end):
            continue
        fragment = fits(candidate, max_bytes, char_limit=600)
        if fragment is None:
            continue
        accept(fragment, candidate.hit)

    # Widen secondary fragments by adjacent positive sentences when possible.
    def hit_of(fragment: PreparedContextFragment) -> int:
        for index, (_, address) in enumerate(candidates):
            if address == fragment.address:
                return index
        return -1

    extended = True
    while extended:
        extended = False
        for position, fragment in enumerate(selected):
            hit = hit_of(fragment)
            if hit <= 0:
                continue
            spans = _sentence_spans(texts[hit])
            others = [
                (other.start, other.end)
                for other_position, other in enumerate(selected)
                if other_position != position and hit_of(other) == hit
            ]

            def trial_fits(start: int, end: int) -> bool:
                if any(start < other_end and other_start < end for other_start, other_end in others):
                    return False
                trial_fragment = PreparedContextFragment(
                    address=candidates[hit][1],
                    start=start,
                    end=end,
                    text=texts[hit][start:end],
                )
                current = list(selected)
                current[position] = trial_fragment
                return len(render_evidence_context(tuple(current)).encode("utf-8")) <= max_bytes

            for sentence_start, sentence_end in reversed(spans):
                if sentence_end <= fragment.start and score_span(hit, sentence_start, sentence_end) > 0:
                    if trial_fits(sentence_start, fragment.end):
                        selected[position] = PreparedContextFragment(
                            address=candidates[hit][1],
                            start=sentence_start,
                            end=fragment.end,
                            text=texts[hit][sentence_start : fragment.end],
                        )
                        extended = True
                    break
            for sentence_start, sentence_end in spans:
                if sentence_start >= fragment.end and score_span(hit, sentence_start, sentence_end) > 0:
                    if trial_fits(fragment.start, sentence_end):
                        selected[position] = PreparedContextFragment(
                            address=candidates[hit][1],
                            start=fragment.start,
                            end=sentence_end,
                            text=texts[hit][fragment.start : sentence_end],
                        )
                        extended = True
                    break

    return tuple(selected)


__all__ = [
    "PreparedContextFragment",
    "render_evidence_context",
    "select_evidence_fragments",
]
