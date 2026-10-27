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
import re
from dataclasses import dataclass

from powercontext.builtin.runtime.prepared_text import TRUST_POLICY

_EVIDENCE_BEGIN = "BEGIN_POWERCONTEXT_PREPARED_CONTEXT_V1"
_EVIDENCE_END = "END_POWERCONTEXT_PREPARED_CONTEXT_V1"
_EVIDENCE_STOPWORDS = frozenset(
    {
        "a", "an", "the", "of", "in", "on", "at", "to", "for", "with", "by",
        "is", "are", "was", "were", "be", "been", "has", "have", "had", "do",
        "does", "did", "and", "or", "not", "no", "but", "if", "then", "than",
        "that", "this", "these", "those", "it", "its", "as", "from", "into",
        "over", "under", "about", "which", "who", "whom", "what", "when",
        "where", "how", "why", "their", "there", "they", "them", "he", "she",
        "we", "you", "i", "can", "could", "should", "would", "will", "may",
        "might", "must", "shall", "also", "such", "per", "between", "among",
        "during", "after", "before", "because", "while", "though", "so",
    }
)


@dataclass(frozen=True)
class PreparedContextFragment:
    """One literal evidence fragment with exact entry coordinates."""

    address: object
    start: int
    end: int
    text: str


def _evidence_terms(query: str) -> tuple[str, ...]:
    tokens = re.findall(r"\w+", query.lower())
    meaningful = [
        token
        for token in tokens
        if token not in _EVIDENCE_STOPWORDS
        and (len(token) >= 2 or any(not char.isascii() for char in token))
    ]
    return tuple(meaningful or tokens)


def _snap_start(text: str, index: int) -> int:
    for marker in (". ", "? ", "! "):
        found = text.rfind(marker, 0, index)
        if found >= 0 and index - found <= 160:
            return found + len(marker)
    return index


def _snap_end(text: str, index: int) -> int:
    for marker in (". ", "? ", "! "):
        found = text.find(marker, index)
        if found >= 0 and found - index <= 260:
            return found + 1
    return index


def evidence_windows(text: str, terms: tuple[str, ...]) -> tuple[tuple[int, int], ...]:
    """Literal windows around query-term matches, snapped to sentence edges."""

    lowered = text.lower()
    spans: list[tuple[int, int]] = []
    for term in terms:
        pattern = re.escape(term)
        for match in re.finditer(pattern, lowered):
            spans.append((match.start(), match.end()))
    if not spans:
        return ()
    spans.sort()
    clusters: list[list[int]] = []
    for start, end in spans:
        if clusters and start - clusters[-1][1] <= 80:
            clusters[-1][1] = max(clusters[-1][1], end)
        else:
            clusters.append([start, end])
    windows: list[tuple[int, int]] = []
    for start, end in clusters[:4]:
        window_start = max(0, start - 240)
        window_end = min(len(text), end + 560)
        window_start = _snap_start(text, window_start)
        window_end = _snap_end(text, window_end)
        windows.append((window_start, window_end))
    return tuple(windows)


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


def fit_evidence_fragment(selected, fragment, max_bytes: int):
    """Return the longest fitting prefix of a fragment, or None."""

    if len(render_evidence_context(tuple(selected) + (fragment,)).encode("utf-8")) <= max_bytes:
        return fragment
    lower, upper = fragment.start + 40, fragment.end
    best = None
    while lower <= upper:
        middle = (lower + upper) // 2
        trial = PreparedContextFragment(
            address=fragment.address,
            start=fragment.start,
            end=middle,
            text=fragment.text[: middle - fragment.start],
        )
        if len(render_evidence_context(tuple(selected) + (trial,)).encode("utf-8")) <= max_bytes:
            best = trial
            lower = middle + 1
        else:
            upper = middle - 1
    return best


__all__ = [
    "PreparedContextFragment",
    "evidence_windows",
    "fit_evidence_fragment",
    "render_evidence_context",
]
