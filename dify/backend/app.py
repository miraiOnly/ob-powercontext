"""Village NPC memory backend.

FastAPI service that keeps one PowerContext Scope per player. Every turn is
captured as a Source, turned into Memory by a literal pipeline, and stored
in PowerContext's SQLite-backed runtime, so the NPC can recall a player's
pranks in later conversations.

Dify chatflow endpoints:
  POST /api/memory/remember  {"player": ..., "text": ...}      -> {"ok": true}
  POST /api/memory/recall    {"player": ..., "query": ..., "limit": N}
      -> {"player": ..., "memories": [{"text": ..., "score": ...}, ...]}
  GET  /api/health           -> {"ok": true, "storage": "sqlite", "engine": "powercontext"}
"""

from __future__ import annotations

import hashlib
import os
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from powercontext.builtin.artifacts.memory import MemoryCandidateRequest, MemoryEntryInput
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime import (
    BuiltinConfig,
    CaptureSource,
    SearchMemoryRequest,
    open_builtin_runtime,
)
from powercontext.builtin.scope import ScopeDraft
from powercontext.builtin.sources import ContentSource

DB_PATH = os.environ.get("NPC_DB_PATH", "./data/npc-memory.db")
MAX_PLAYER = 64
MAX_TEXT = 8000


class LiteralPipeline:
    """One memory entry per captured turn, no model needed."""

    async def extract(self, request: MemoryCandidateRequest, /):
        return tuple(
            MemoryEntryInput(kind="interaction", text=source.content, sources=(source,))
            for source in request.sources
            if isinstance(source, ContentSource)
        )


class RememberInput(BaseModel):
    player: str = Field(min_length=1, max_length=MAX_PLAYER)
    text: str = Field(min_length=1, max_length=MAX_TEXT)


class RecallInput(BaseModel):
    player: str = Field(min_length=1, max_length=MAX_PLAYER)
    query: str = Field(default="", max_length=4000)
    limit: int = Field(default=8, ge=1, le=50)


class MemoryStore:
    def __init__(self) -> None:
        self.contexts = None

    @asynccontextmanager
    async def open(self):
        os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)
        config = BuiltinConfig(
            database=SQLiteConfig(url=f"sqlite+aiosqlite:///{DB_PATH}")
        )
        async with open_builtin_runtime(config, candidate_pipeline=LiteralPipeline()) as runtime:
            self.contexts = runtime
            yield
        self.contexts = None

    @staticmethod
    def player_key(player: str) -> str:
        digest = hashlib.sha256(player.strip().casefold().encode("utf-8")).hexdigest()
        return "npc-" + digest[:16]

    async def scope_id(self, player: str):
        if self.contexts is None:
            raise HTTPException(status_code=503, detail="backend is starting")
        scope = await self.contexts.scopes.create(
            ScopeDraft(
                title=f"NPC village player {player.strip()}",
                summary="Village NPC memory for one player",
                idempotency_key=self.player_key(player),
            )
        )
        return scope.scope_id

    async def remember(self, player: str, text: str) -> None:
        sid = await self.scope_id(player)
        await self.contexts.sources.for_scope(sid).capture(
            CaptureSource(
                source_id=f"turn-{uuid.uuid4().hex}",
                content=text,
                metadata={"player": player.strip()},
            )
        )
        memory = self.contexts.memory.for_scope(sid)
        while (await memory.flush()).processed:
            pass

    async def recall(self, player: str, query: str, limit: int):
        sid = await self.scope_id(player)
        memory = self.contexts.memory.for_scope(sid)
        effective = query.strip() or player.strip()
        page = await memory.search(
            SearchMemoryRequest(query=effective, mode="fts", limit=limit)
        )
        return [
            {
                "text": hit.text,
                "score": round(float(hit.score), 4) if hit.score is not None else None,
            }
            for hit in page.hits
        ]


store = MemoryStore()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    async with store.open():
        yield


app = FastAPI(title="Village NPC Memory", version="1.0.0", lifespan=lifespan)


@app.get("/api/health")
async def health():
    return {"ok": True, "storage": "sqlite", "engine": "powercontext"}


@app.post("/api/memory/remember")
async def remember(item: RememberInput):
    await store.remember(item.player, item.text)
    return {"ok": True}


@app.post("/api/memory/recall")
async def recall(item: RecallInput):
    memories = await store.recall(item.player, item.query, item.limit)
    return {"player": item.player.strip(), "memories": memories}
