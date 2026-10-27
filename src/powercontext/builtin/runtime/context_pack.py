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

"""Chunked context pack and incremental delta transfer protocol."""

from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import dataclass

PACK_SCHEMA = "powercontext.context-pack.v1"
DELTA_SCHEMA = "powercontext.context-pack-delta.v1"


class ContextPackError(ValueError):
    """Protocol or chunking validation failure."""


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _digest(schema: str, items: tuple[bytes, ...]) -> str:
    payload = {
        "schema": schema,
        "items": [base64.b64encode(item).decode("ascii") for item in items],
    }
    return "sha256:" + hashlib.sha256(_canonical(payload)).hexdigest()


@dataclass(frozen=True)
class ContextPack:
    """A complete content pack with chunk items, digest and byte count."""

    schema: str = PACK_SCHEMA
    items: tuple[bytes, ...] = ()
    digest: str = ""
    byte_count: int = 0

    @classmethod
    def build(cls, items) -> ContextPack:
        packed = tuple(items)
        if not all(isinstance(item, bytes) for item in packed):
            raise ContextPackError("pack items must be bytes")
        digest = _digest(PACK_SCHEMA, packed)
        byte_count = 0
        while True:
            pack = cls(items=packed, digest=digest, byte_count=byte_count)
            total = len(pack.to_bytes())
            if total == byte_count:
                return pack
            byte_count = total

    @classmethod
    def from_content(cls, content, *, chunk_bytes: int = 512) -> ContextPack:
        if isinstance(content, str):
            data = content.encode("utf-8")
        elif isinstance(content, bytes):
            data = content
        else:
            raise ContextPackError("content must be str or bytes")
        if not isinstance(chunk_bytes, int) or isinstance(chunk_bytes, bool) or chunk_bytes <= 0:
            raise ContextPackError("chunk_bytes must be a positive integer")
        chunks: list[bytes] = []
        position = 0
        while position < len(data):
            end = min(position + chunk_bytes, len(data))
            while end > position and end < len(data) and (data[end] & 0xC0) == 0x80:
                end -= 1
            if end == position:
                raise ContextPackError("chunk limit too small for a UTF-8 character")
            chunks.append(data[position:end])
            position = end
        return cls.build(tuple(chunks))

    def to_bytes(self) -> bytes:
        payload = {
            "schema": self.schema,
            "items": [base64.b64encode(item).decode("ascii") for item in self.items],
            "digest": self.digest,
            "byte_count": self.byte_count,
        }
        return _canonical(payload)

    @classmethod
    def from_bytes(cls, data: bytes) -> ContextPack:
        try:
            payload = json.loads(data)
        except (ValueError, UnicodeDecodeError) as error:
            raise ContextPackError("pack must be valid JSON") from error
        if not isinstance(payload, dict) or set(payload.keys()) != {"schema", "items", "digest", "byte_count"}:
            raise ContextPackError("pack must contain exactly schema, items, digest and byte_count")
        if payload["schema"] != PACK_SCHEMA:
            raise ContextPackError("unknown pack schema")
        items = payload["items"]
        if not isinstance(items, list):
            raise ContextPackError("pack items must be a list")
        decoded: list[bytes] = []
        for item in items:
            if not isinstance(item, str):
                raise ContextPackError("pack items must be base64 strings")
            try:
                decoded.append(base64.b64decode(item, validate=True))
            except ValueError as error:
                raise ContextPackError("pack items must be valid base64") from error
        digest = payload["digest"]
        if digest != _digest(PACK_SCHEMA, tuple(decoded)):
            raise ContextPackError("pack digest mismatch")
        byte_count = payload["byte_count"]
        if not isinstance(byte_count, int) or isinstance(byte_count, bool):
            raise ContextPackError("byte_count must be an integer")
        pack = cls(items=tuple(decoded), digest=digest, byte_count=byte_count)
        if byte_count != len(pack.to_bytes()):
            raise ContextPackError("pack byte_count mismatch")
        return pack


@dataclass(frozen=True)
class ContextPackDelta:
    """Incremental transfer reusing a validated base-prefix."""

    base_digest: str
    target_digest: str
    prefix_items: int
    appended: tuple[bytes, ...]

    @property
    def schema(self) -> str:
        return DELTA_SCHEMA

    @property
    def byte_count(self) -> int:
        return len(self.to_bytes())

    @classmethod
    def between(cls, base: ContextPack, target: ContextPack) -> ContextPackDelta:
        prefix = 0
        limit = min(len(base.items), len(target.items))
        while prefix < limit and base.items[prefix] == target.items[prefix]:
            prefix += 1
        return cls(
            base_digest=base.digest,
            target_digest=target.digest,
            prefix_items=prefix,
            appended=target.items[prefix:],
        )

    def to_bytes(self) -> bytes:
        payload = {
            "schema": self.schema,
            "base_digest": self.base_digest,
            "target_digest": self.target_digest,
            "prefix_items": self.prefix_items,
            "appended": [base64.b64encode(item).decode("ascii") for item in self.appended],
        }
        return _canonical(payload)

    @classmethod
    def from_bytes(cls, data: bytes) -> ContextPackDelta:
        try:
            payload = json.loads(data)
        except (ValueError, UnicodeDecodeError) as error:
            raise ContextPackError("delta must be valid JSON") from error
        expected = {"schema", "base_digest", "target_digest", "prefix_items", "appended"}
        if not isinstance(payload, dict) or set(payload.keys()) != expected:
            raise ContextPackError("delta must contain exactly schema, base_digest, target_digest, prefix_items and appended")
        if payload["schema"] != DELTA_SCHEMA:
            raise ContextPackError("unknown delta schema")
        base_digest, target_digest = payload["base_digest"], payload["target_digest"]
        if not isinstance(base_digest, str) or not isinstance(target_digest, str):
            raise ContextPackError("delta digests must be strings")
        prefix = payload["prefix_items"]
        if type(prefix) is not int or prefix < 0:
            raise ContextPackError("prefix_items must be a non-negative integer")
        appended = payload["appended"]
        if not isinstance(appended, list):
            raise ContextPackError("delta appended must be a list")
        decoded: list[bytes] = []
        for item in appended:
            if not isinstance(item, str):
                raise ContextPackError("delta appended items must be base64 strings")
            try:
                decoded.append(base64.b64decode(item, validate=True))
            except ValueError as error:
                raise ContextPackError("delta appended items must be valid base64") from error
        return cls(base_digest=base_digest, target_digest=target_digest, prefix_items=prefix, appended=tuple(decoded))


def apply_context_pack_delta(base: ContextPack, delta: ContextPackDelta) -> ContextPack:
    """Validate the delta against the base and rebuild the target pack."""

    if not isinstance(base, ContextPack):
        raise ContextPackError("base must be a ContextPack")
    if delta.base_digest != base.digest:
        raise ContextPackError("delta base digest does not match the base pack")
    if delta.prefix_items > len(base.items):
        raise ContextPackError("delta prefix exceeds the base pack")
    target_items = base.items[: delta.prefix_items] + delta.appended
    target = ContextPack.build(target_items)
    if target.digest != delta.target_digest:
        raise ContextPackError("delta target digest does not match the rebuilt pack")
    return target


__all__ = [
    "ContextPack",
    "ContextPackDelta",
    "ContextPackError",
    "PACK_SCHEMA",
    "DELTA_SCHEMA",
    "apply_context_pack_delta",
]
