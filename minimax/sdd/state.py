"""PowerContext 任务状态：进度、待办、验证结果的保存与重启后复用。

使用 PowerContext 嵌入式运行时（SQLite 文件持久化，无外部服务），每个任务
一条状态记忆；重启上下文模块后按任务名检索并恢复状态。
"""

from __future__ import annotations

import json
from pathlib import Path

from powercontext.builtin.artifacts.memory import MemoryCandidateRequest, MemoryEntryInput
from powercontext.builtin.runtime import (
    BuiltinConfig,
    CaptureSource,
    GetMemoryEntryRequest,
    MemoryCitation,
    SearchMemoryRequest,
    open_builtin_runtime,
)
from powercontext.builtin.scope import ScopeDraft
from powercontext.builtin.sources import ContentSource

SCOPE_TITLE = "sdd-task-state"
TASK_PREFIX = "SDD-TASK"


class _LiteralPipeline:
    async def extract(self, request: MemoryCandidateRequest, /):
        return tuple(
            MemoryEntryInput(kind="document", text=source.content, sources=(source,))
            for source in request.sources
            if isinstance(source, ContentSource)
        )


class PowerContextState:
    """任务状态存取。每次调用都独立打开运行时，等价于模块重启后读取。"""

    def __init__(self, data_dir: str):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        db_url = f"sqlite+aiosqlite:///{self.data_dir / 'powercontext_state.db'}"
        self._config = BuiltinConfig(database={"kind": "sqlite", "url": db_url})

    async def save(self, task: str, entries: dict[str, str]) -> None:
        """entries: {done | todo | verification | notes: 文本}，整体作为一条状态记忆。"""
        async with open_builtin_runtime(self._config, candidate_pipeline=_LiteralPipeline()) as runtime:
            scope = await runtime.scopes.create(
                ScopeDraft(title=SCOPE_TITLE, summary=f"{TASK_PREFIX} {task}", idempotency_key=f"sdd-{task}")
            )
            sources = runtime.sources.for_scope(scope.scope_id)
            memory = runtime.memory.for_scope(scope.scope_id)
            payload = json.dumps({"task": task, "state": entries}, ensure_ascii=False)
            await sources.capture(
                CaptureSource(source_id=f"sdd-state-{task}", content=payload, metadata={"task": task})
            )
            while (await memory.flush()).processed:
                pass

    async def load(self, task: str) -> dict | None:
        async with open_builtin_runtime(self._config, candidate_pipeline=_LiteralPipeline()) as runtime:
            scope = await runtime.scopes.create(
                ScopeDraft(title=SCOPE_TITLE, summary=f"{TASK_PREFIX} {task}", idempotency_key=f"sdd-{task}")
            )
            memory = runtime.memory.for_scope(scope.scope_id)
            page = await memory.search(SearchMemoryRequest(query=f"{TASK_PREFIX} {task}", mode="fts", limit=8))
            if not page.hits:
                return None
            hit = page.hits[0]
            citation = MemoryCitation(
                memory_ref=hit.memory_ref,
                entry_id=hit.entry_id,
                entry_version_id=hit.entry_version_id,
            )
            entry = await memory.get(GetMemoryEntryRequest(citation=citation))
            try:
                payload = json.loads(entry.entry.text)
                if payload.get("task") == task:
                    return payload.get("state")
            except (json.JSONDecodeError, AttributeError):
                return None
            return None
