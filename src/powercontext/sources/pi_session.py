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

"""Pi session branch evidence collection with durable incremental progress.

A Pi v3 session file is a JSONL graph whose nodes reference parents. This
module parses the selected leaf's ancestor chain into immutable CAPTURED
events, and provides a Connector that imports only events that were not
imported by a previous run, so branch switches, appends and restarts all
resume from the persisted checkpoint.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any
from urllib.parse import quote

from pydantic import BaseModel, ConfigDict

from powercontext.sources import AdapterSourceDefinition, TEXT_EVIDENCE_PROJECTION_KEY, TextEvidence
from powercontext.sources.connectors import (
    ConnectorRunCompletion,
    ConnectorRunSession,
    ConnectorRunStatus,
)
from powercontext.sources.models import Source, SourceMaterialization

PI_EVENT_SOURCE_NAME = "pi-event"
PI_SESSION_CONNECTOR_NAME = "pi-session-history"
PI_SESSION_VERSION = "1"
MAX_PI_SOURCE_ID_LENGTH = 256

_CHECKPOINT_EVENTS_KEY = "events"


def pi_event_source_id(session_id: str, event_id: str) -> str:
    """Build the stable event identity from the session and event ids."""

    identity = (
        f"pi:{quote(session_id, safe='')}:{quote(event_id, safe='')}"
    )
    if len(identity) > MAX_PI_SOURCE_ID_LENGTH:
        raise ValueError(
            f"pi event source_id must not exceed {MAX_PI_SOURCE_ID_LENGTH} characters",
        )  # noqa: TRY003
    return identity


class PiSessionInput(BaseModel):
    """A complete Pi v3 session file plus the optional selected leaf."""

    model_config = ConfigDict(frozen=True)

    content: bytes
    leaf_id: str | None = None


class PiEventInput(BaseModel):
    """One visible event on the selected branch, kept verbatim."""

    model_config = ConfigDict(frozen=True)

    session_id: str
    event_id: str
    parent_id: str | None
    event_type: str
    role: str
    content: str
    record: dict[str, Any]


class PiEventSource(Source):
    """Captured raw Pi event identified by session and event id."""

    session_id: str
    event_id: str
    parent_id: str | None
    event_type: str
    role: str
    content: str
    record: dict[str, Any]

    @property
    def source_id(self) -> str:
        """Alias of the stable event identity carried by ``name``."""

        return self.name


def _decode_lines(content: bytes) -> list[str]:
    try:
        text = content.decode("utf-8-sig", errors="strict")
    except UnicodeDecodeError as error:
        raise ValueError("pi session must be valid UTF-8") from error  # noqa: TRY003
    return [line for line in text.splitlines() if line.strip()]


def _load_records(content: bytes) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for line in _decode_lines(content):
        try:
            record = json.loads(line)
        except json.JSONDecodeError as error:
            raise ValueError(f"malformed pi session JSONL: {error}") from error  # noqa: TRY003
        if not isinstance(record, dict):
            raise ValueError("pi session records must be JSON objects")  # noqa: TRY003
        records.append(record)
    return records


def _visible_message_body(message: dict[str, Any]) -> str:
    role = message.get("role")
    if role == "toolResult":
        tool_name = message.get("toolName", "")
        tool_call_id = message.get("toolCallId", "")
        text = _text_of(message.get("content"))
        return f"[tool_result {tool_name} {tool_call_id}] {text}"
    if role == "bashExecution":
        command = message.get("command", "")
        output = message.get("output", "")
        if output is None:
            output = _text_of(message.get("content"))
        return f"$ {command}\n{output}"
    return _text_of(message.get("content"))


def _text_of(content: object) -> str:
    """Render visible text/toolCall blocks, joined by newlines."""

    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if not isinstance(block, dict):
                continue
            block_type = block.get("type")
            if block_type == "text":
                parts.append(str(block.get("text", "")))
            elif block_type == "toolCall":
                arguments = block.get("arguments") or {}
                serialized = json.dumps(
                    arguments,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
                parts.append(f"[tool_call {block.get('name', '')} {block.get('id', '')}] {serialized}")
            # thinking / image / unknown blocks produce no visible text
        return "\n".join(parts)
    return ""


def _render_event(record: dict[str, Any], session_id: str) -> PiEventInput | None:
    event_type = record.get("type")
    if not isinstance(event_type, str):
        return None
    if event_type == "message":
        message = record.get("message")
        if not isinstance(message, dict):
            return None
        role = message.get("role")
        if not isinstance(role, str):
            return None
        body = _visible_message_body(message)
    elif event_type in {"compaction", "branch_summary"}:
        role = event_type
        summary = record.get("summary")
        body = summary if isinstance(summary, str) else ""
    elif event_type == "custom_message":
        role = "custom"
        body = _text_of(record.get("content"))
    else:
        # Control nodes (model_change, thinking_level_change, ...) keep their
        # graph position but produce no text evidence.
        return None

    if not body:
        return None
    return PiEventInput(
        session_id=session_id,
        event_id=record["id"],
        parent_id=record.get("parentId"),
        event_type=event_type,
        role=role,
        content=f"{role}: {body}",
        record=record,
    )


def parse_pi_session(session: PiSessionInput) -> tuple[PiEventInput, ...]:
    """Parse the whole file and return the selected branch's visible events."""

    records = _load_records(session.content)
    if not records:
        raise ValueError("pi session must contain a session header")  # noqa: TRY003
    header = records[0]
    if not isinstance(header, dict):
        raise ValueError("pi session header must be a JSON object")  # noqa: TRY003
    if header.get("type") != "session" or header.get("version") != 3:
        raise ValueError("pi session first record must be type=session, version=3")  # noqa: TRY003
    session_id = header.get("id")
    if not isinstance(session_id, str) or not session_id:
        raise ValueError("pi session id must be a non-empty string")  # noqa: TRY003

    nodes = records[1:]
    by_id: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for record in nodes:
        record_id = record.get("id")
        if not isinstance(record_id, str) or not record_id:
            raise ValueError("pi records must have non-empty string ids")  # noqa: TRY003
        if record_id in by_id:
            raise ValueError(f"duplicate pi record id {record_id!r}")  # noqa: TRY003
        parent_id = record.get("parentId")
        if parent_id is not None:
            if not isinstance(parent_id, str):
                raise ValueError("pi parentId must be a string or absent")  # noqa: TRY003
            if parent_id == record_id:
                raise ValueError(f"pi record {record_id!r} cannot be its own parent")  # noqa: TRY003
            if parent_id not in by_id and parent_id != session_id:
                raise ValueError(
                    f"pi record {record_id!r} references missing parent {parent_id!r}",
                )  # noqa: TRY003
        by_id[record_id] = record
        order.append(record_id)

    if session.leaf_id is None:
        if not order:
            raise ValueError("pi session contains no records after the header")  # noqa: TRY003
        leaf_id = order[-1]
    else:
        leaf_id = session.leaf_id
        if leaf_id not in by_id:
            raise ValueError(f"pi session leaf {leaf_id!r} does not exist")  # noqa: TRY003

    chain: list[str] = []
    cursor: str | None = leaf_id
    while cursor is not None and cursor != session_id:
        chain.append(cursor)
        cursor = by_id[cursor].get("parentId")
    chain.reverse()

    events: list[PiEventInput] = []
    for record_id in chain:
        event = _render_event(by_id[record_id], session_id)
        if event is not None:
            events.append(event)
    return tuple(events)


class PiEventSourceAdapter:
    """Resolve and read one Pi event as a captured Source."""

    input_class = PiEventInput
    name = PI_EVENT_SOURCE_NAME
    source_class = PiEventSource

    async def resolve(self, value: PiEventInput, /) -> PiEventSource:
        identity = pi_event_source_id(value.session_id, value.event_id)
        return PiEventSource(
            name=identity,
            materialization=SourceMaterialization.CAPTURED,
            session_id=value.session_id,
            event_id=value.event_id,
            parent_id=value.parent_id,
            event_type=value.event_type,
            role=value.role,
            content=value.content,
            record=value.record,
        )

    async def read(self, source: PiEventSource, /) -> PiEventInput:
        return PiEventInput(
            session_id=source.session_id,
            event_id=source.event_id,
            parent_id=source.parent_id,
            event_type=source.event_type,
            role=source.role,
            content=source.content,
            record=source.record,
        )


class PiEventTextEvidenceProjection:
    """Project one Pi event to the standard text-evidence shape."""

    name = TEXT_EVIDENCE_PROJECTION_KEY.name
    version = TEXT_EVIDENCE_PROJECTION_KEY.version
    source_class = PiEventSource
    output_class = TextEvidence

    def project(self, source: PiEventSource, /) -> TextEvidence:
        return TextEvidence(
            source_type=PI_EVENT_SOURCE_NAME,
            source_id=source.name,
            content=source.content,
            metadata={
                "session_id": source.session_id,
                "event_id": source.event_id,
                "parent_id": source.parent_id,
                "event_type": source.event_type,
                "role": source.role,
            },
        )


PI_EVENT_SOURCE_ADAPTER = PiEventSourceAdapter()
PI_EVENT_SOURCE_DEFINITION = AdapterSourceDefinition(
    PI_EVENT_SOURCE_ADAPTER,
    version=PI_SESSION_VERSION,
    projections=(PiEventTextEvidenceProjection(),),
)


def _record_digest(record: dict[str, Any]) -> str:
    canonical = json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class PiSessionConnector:
    """Import the selected branch incrementally with durable checkpoints."""

    name = PI_SESSION_CONNECTOR_NAME
    version = PI_SESSION_VERSION
    source_definitions = frozenset({PI_EVENT_SOURCE_NAME})

    def __init__(self, path: str | Path, leaf_id: str | None = None) -> None:
        self._path = Path(path)
        self._leaf_id = leaf_id

    async def run(self, session: ConnectorRunSession) -> ConnectorRunCompletion:
        raw = self._path.read_bytes()
        events = parse_pi_session(PiSessionInput(content=raw, leaf_id=self._leaf_id))

        previous = session.checkpoint
        if previous is None:
            known: dict[str, str] = {}
        elif isinstance(previous, dict) and isinstance(previous.get(_CHECKPOINT_EVENTS_KEY), dict):
            known = dict(previous[_CHECKPOINT_EVENTS_KEY])
        else:
            raise ValueError("pi-session-history checkpoint is invalid")  # noqa: TRY003

        for event in events:
            key = f"{event.session_id}\x00{event.event_id}"
            digest = _record_digest(event.record)
            if key in known:
                if known[key] != digest:
                    raise ValueError(
                        f"already-imported pi event {event.event_id!r} was modified",
                    )  # noqa: TRY003
                continue
            await session.submit(event.event_id, PI_EVENT_SOURCE_NAME, event)
            known[key] = digest

        return ConnectorRunCompletion(
            status=ConnectorRunStatus.COMPLETE,
            checkpoint={_CHECKPOINT_EVENTS_KEY: known},
        )


__all__ = [
    "PiEventInput",
    "PiEventSource",
    "PiSessionConnector",
    "PiSessionInput",
    "PI_EVENT_SOURCE_DEFINITION",
    "PI_EVENT_SOURCE_NAME",
    "PI_SESSION_CONNECTOR_NAME",
    "PI_SESSION_VERSION",
    "parse_pi_session",
    "pi_event_source_id",
]
