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

"""HTTP scoring-service adapter for listwise Memory reranking."""

from __future__ import annotations

import math

import httpx

from powercontext.builtin.artifacts.memory.models import MemoryHit
from powercontext.builtin.artifacts.memory.reranking import (
    MemoryRerankDecision,
    _validate_bounds,
)
from powercontext.builtin.inference import InferenceUsage

HTTP_RERANK_POLICY_ID = "http-cross-encoder-v1"


class HTTPMemoryReranker:
    """Score candidates with a remote service, batching by the service limit."""

    policy_id = HTTP_RERANK_POLICY_ID

    def __init__(
        self,
        endpoint: str,
        *,
        client: httpx.AsyncClient,
        batch_size: int = 32,
        policy_id: str = HTTP_RERANK_POLICY_ID,
    ) -> None:
        if batch_size <= 0:
            raise ValueError("batch_size must be positive")  # noqa: TRY003
        self._endpoint = endpoint
        self._client = client
        self._batch_size = batch_size
        self.policy_id = policy_id

    async def rerank(
        self,
        query: str,
        candidates: tuple[MemoryHit, ...],
        limit: int,
        /,
    ) -> MemoryRerankDecision:
        """Request batches, validate responses, and order by service scores."""

        _validate_bounds(len(candidates), limit)
        scores: dict[int, float] = {}
        requests = 0
        for batch_start in range(0, len(candidates), self._batch_size):
            batch = candidates[batch_start : batch_start + self._batch_size]
            global_ranks = tuple(range(batch_start + 1, batch_start + len(batch) + 1))
            payload = {
                "query": query,
                "candidates": [
                    {"rank": rank, "text": candidate.text}
                    for rank, candidate in zip(global_ranks, batch, strict=True)
                ],
            }
            response = await self._client.post(self._endpoint, json=payload)
            response.raise_for_status()
            requests += 1
            try:
                data = response.json()
            except ValueError as error:
                raise ValueError("reranking service returned invalid JSON") from error  # noqa: TRY003

            if not isinstance(data, dict) or set(data.keys()) != {"scores"}:
                raise ValueError("reranking service response must contain exactly a scores field")  # noqa: TRY003
            score_items = data["scores"]
            if not isinstance(score_items, list) or len(score_items) != len(global_ranks):
                raise ValueError("reranking service must return exactly one score per batch candidate")  # noqa: TRY003
            batch_seen: set[int] = set()
            for item in score_items:
                if not isinstance(item, dict) or set(item.keys()) != {"rank", "score"}:
                    raise ValueError("reranking score records must contain exactly rank and score")  # noqa: TRY003
                rank = item["rank"]
                score = item["score"]
                if type(rank) is not int or rank not in global_ranks or rank in batch_seen:
                    raise ValueError("reranking response ranks must cover the batch exactly once")  # noqa: TRY003
                if isinstance(score, bool) or not isinstance(score, (int, float)) or not math.isfinite(score):
                    raise ValueError("reranking scores must be finite numbers")  # noqa: TRY003
                batch_seen.add(rank)
                scores[rank] = float(score)

        selected = sorted(scores, key=lambda rank: (-scores[rank], rank))[:limit]
        return MemoryRerankDecision(
            selected_ranks=tuple(selected),
            usage=InferenceUsage(requests=requests),
        )


__all__ = ["HTTPMemoryReranker", "HTTP_RERANK_POLICY_ID"]
