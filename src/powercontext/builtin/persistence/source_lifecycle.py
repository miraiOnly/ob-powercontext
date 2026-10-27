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

"""Persistence repository for the per-scope source lifecycle graph."""

from __future__ import annotations

import json

from sqlalchemy import delete, insert, select
from sqlalchemy.ext.asyncio import AsyncConnection

from powercontext.builtin.persistence.tables import SOURCE_LIFECYCLE_TABLE


def _dump(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=True, separators=(",", ":")).encode("utf-8")


def _load(value: object, default: object) -> object:
    if value is None:
        return default
    if isinstance(value, (bytes, bytearray, memoryview)):
        value = bytes(value).decode("utf-8")
    return json.loads(value)


class SourceLifecycleRepository:
    """Load and replace the complete lifecycle state of one scope."""

    def __init__(self, database, scope_id: str) -> None:
        self._database = database
        self._scope_id = scope_id

    async def load(self) -> dict:
        async with self._database.transaction() as connection:
            rows = (
                await connection.execute(
                    select(SOURCE_LIFECYCLE_TABLE).where(
                        SOURCE_LIFECYCLE_TABLE.c.scope_id == self._scope_id
                    )
                )
            ).all()
        state = {}
        for row in rows:
            state[row.node_id] = {
                "kind": row.kind,
                "node_id": row.node_id,
                "parents": _load(row.parents, []),
                "policy": row.policy,
                "available": bool(row.available),
                "revision": int(row.revision),
                "sequence": int(row.sequence),
                "last_event": _load(row.last_event, None),
                "reference": _load(row.reference, {}),
            }
        return state

    async def save(self, state: dict) -> None:
        rows = [
            {
                "scope_id": self._scope_id,
                "node_id": raw["node_id"],
                "kind": raw["kind"],
                "parents": _dump(raw["parents"]),
                "policy": raw["policy"],
                "available": bool(raw["available"]),
                "revision": int(raw.get("revision", 0)),
                "sequence": int(raw.get("sequence", 0)),
                "last_event": None if raw.get("last_event") is None else _dump(raw["last_event"]),
                "artifact_id": (raw["reference"].get("artifact_id") if raw["kind"] == "topic" else None),
                "reference": _dump(raw["reference"]),
            }
            for raw in state.values()
        ]
        async with self._database.transaction() as connection:
            await connection.execute(
                delete(SOURCE_LIFECYCLE_TABLE).where(
                    SOURCE_LIFECYCLE_TABLE.c.scope_id == self._scope_id
                )
            )
            if rows:
                await connection.execute(insert(SOURCE_LIFECYCLE_TABLE), rows)


__all__ = ["SourceLifecycleRepository"]
