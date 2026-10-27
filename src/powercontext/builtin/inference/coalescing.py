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

"""Cross-call embedding coalescing with bounded LRU vector caching."""

from __future__ import annotations

import asyncio
from collections import OrderedDict
from typing import Any

from powercontext.builtin.inference import EmbeddingResult, InferenceUsage


class CoalescingEmbeddingModel:
    """Share in-flight and recent embedding work across concurrent calls."""

    def __init__(
        self,
        model: Any,
        *,
        max_batch_size: int = 64,
        coalesce_ms: float = 5.0,
        cache_size: int = 1024,
    ) -> None:
        if not isinstance(max_batch_size, int) or isinstance(max_batch_size, bool) or max_batch_size <= 0:
            raise ValueError("max_batch_size must be a positive integer")  # noqa: TRY003
        if coalesce_ms < 0:
            raise ValueError("coalesce_ms must not be negative")  # noqa: TRY003
        if not isinstance(cache_size, int) or isinstance(cache_size, bool) or cache_size < 0:
            raise ValueError("cache_size must be a non-negative integer")  # noqa: TRY003
        self._model = model
        self._max_batch_size = max_batch_size
        self._window = coalesce_ms / 1000.0
        self._cache_size = cache_size
        self._cache: OrderedDict[str, tuple[float, ...]] = OrderedDict()
        self._pending: dict[str, tuple[asyncio.Future, dict]] = {}
        self._queue: list[str] = []
        self._pump: asyncio.Task | None = None

    @property
    def profile(self) -> Any:
        """Expose the underlying model profile."""

        return self._model.profile

    def _cache_get(self, text: str) -> tuple[float, ...] | None:
        if self._cache_size <= 0:
            return None
        vector = self._cache.get(text)
        if vector is not None:
            self._cache.move_to_end(text)
        return vector

    def _cache_put(self, text: str, vector: tuple[float, ...]) -> None:
        if self._cache_size <= 0:
            return
        self._cache[text] = vector
        self._cache.move_to_end(text)
        while len(self._cache) > self._cache_size:
            self._cache.popitem(last=False)

    def _ensure_pump(self) -> None:
        if self._pump is None or self._pump.done():
            self._pump = asyncio.get_running_loop().create_task(self._pump_loop())

    async def _pump_loop(self) -> None:
        try:
            while self._queue:
                if self._window > 0:
                    await asyncio.sleep(self._window)
                texts = list(self._queue)
                self._queue.clear()
                await self._flush(texts)
        finally:
            self._pump = None

    async def _flush(self, texts: list[str]) -> None:
        unique: list[str] = []
        for text in texts:
            if text not in unique:
                unique.append(text)
        for start in range(0, len(unique), self._max_batch_size):
            batch = unique[start : start + self._max_batch_size]
            try:
                result = await self._model.embed(tuple(batch))
                vectors = result.vectors
                if len(vectors) != len(batch):
                    raise ValueError("embedding model returned a vector count mismatch")  # noqa: TRY003
                usage = result.usage if result.usage is not None else InferenceUsage(requests=1)
                batch_usage = {"usage": usage}
            except asyncio.CancelledError:
                for text in batch:
                    entry = self._pending.get(text)
                    if entry is not None and not entry[0].done():
                        entry[0].cancel()
                raise
            except BaseException as error:
                for text in batch:
                    entry = self._pending.pop(text, None)
                    if entry is not None and not entry[0].done():
                        entry[0].set_exception(error)
                continue
            for text, vector in zip(batch, vectors, strict=True):
                entry = self._pending.pop(text, None)
                if entry is None or entry[0].done():
                    continue
                entry[1]["batch"] = batch_usage
                self._cache_put(text, vector)
                entry[0].set_result(vector)

    async def embed(self, texts: Any) -> EmbeddingResult:
        if not texts:
            return EmbeddingResult(vectors=(), usage=InferenceUsage(requests=0))
        slots: list[tuple[str, object, object]] = []
        for text in texts:
            cached = self._cache_get(text)
            if cached is not None:
                slots.append(("hit", cached, None))
                continue
            entry = self._pending.get(text)
            if entry is None:
                future = asyncio.get_running_loop().create_future()
                holder: dict = {}
                self._pending[text] = (future, holder)
                self._queue.append(text)
                self._ensure_pump()
            else:
                future, holder = entry
            slots.append(("wait", text, (future, holder)))

        vectors: list[tuple[float, ...]] = []
        waited_ids: set[int] = set()
        usages: list[InferenceUsage] = []
        for kind, value, payload in slots:
            if kind == "hit":
                vectors.append(value)
                continue
            text, (future, holder) = value, payload
            vector = await asyncio.shield(future)
            batch = holder.get("batch")
            if batch is not None and id(batch) not in waited_ids:
                waited_ids.add(id(batch))
                usages.append(batch["usage"])
            self._cache_put(text, vector)
            vectors.append(vector)

        requests = 0
        input_tokens: int | None = None
        output_tokens: int | None = None
        for usage in usages:
            requests += usage.requests
            if usage.input_tokens is not None:
                input_tokens = (input_tokens or 0) + usage.input_tokens
            if usage.output_tokens is not None:
                output_tokens = (output_tokens or 0) + usage.output_tokens
        return EmbeddingResult(
            vectors=tuple(vectors),
            usage=InferenceUsage(requests=requests, input_tokens=input_tokens, output_tokens=output_tokens),
        )


__all__ = ["CoalescingEmbeddingModel"]
