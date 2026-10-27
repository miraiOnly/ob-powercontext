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

"""Source dependency lifecycle: withdrawal and restoration propagation."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from powercontext.artifacts import ArtifactRef, MemoryCitation
from powercontext.sources import SourceRef


class SourceLifecycleError(ValueError):
    """Invalid lifecycle registration or event."""


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class SourceLifecycleEvent(_StrictModel):
    """One validated lifecycle event for a registered Source."""

    node_id: str
    reason: str
    revision: int
    sequence: int
    available: bool

    @field_validator("node_id", "reason")
    @classmethod
    def validate_non_blank(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("must be non-empty and not only whitespace")  # noqa: TRY003
        return value

    @field_validator("revision", "sequence")
    @classmethod
    def validate_positive(cls, value: int) -> int:
        if type(value) is not int or isinstance(value, bool) or value < 1:
            raise ValueError("must be a positive integer")  # noqa: TRY003
        return value


@dataclass
class LifecycleNode:
    kind: Literal["source", "memory", "topic"]
    node_id: str
    reference: Any
    parents: tuple[str, ...]
    policy: str
    available: bool
    revision: int = 0
    sequence: int = 0
    last_event: dict | None = None
    applied: bool | None = None


def serialize_reference(node: LifecycleNode) -> dict:
    reference = node.reference
    if node.kind == "source":
        return {"kind": "source", "source_type": reference.source_type, "source_id": reference.source_id}
    if node.kind == "memory":
        return {
            "kind": "memory",
            "memory_ref": {
                "family": reference.memory_ref.family,
                "artifact_id": reference.memory_ref.artifact_id,
                "revision": reference.memory_ref.revision,
            },
            "entry_id": reference.entry_id,
            "entry_version_id": reference.entry_version_id,
        }
    return {
        "kind": "topic",
        "family": reference.family,
        "artifact_id": reference.artifact_id,
        "revision": reference.revision,
    }


def deserialize_reference(raw: dict) -> Any:
    if raw["kind"] == "source":
        return SourceRef(source_type=raw["source_type"], source_id=raw["source_id"])
    if raw["kind"] == "memory":
        return MemoryCitation(
            memory_ref=ArtifactRef(
                family=raw["memory_ref"]["family"],
                artifact_id=raw["memory_ref"]["artifact_id"],
                revision=raw["memory_ref"]["revision"],
            ),
            entry_id=raw["entry_id"],
            entry_version_id=raw["entry_version_id"],
        )
    return ArtifactRef(family=raw["family"], artifact_id=raw["artifact_id"], revision=raw["revision"])


@dataclass
class LifecycleGraph:
    """A per-scope DAG of evidence dependencies with availability states."""

    nodes: dict[str, LifecycleNode] = field(default_factory=dict)

    def register(
        self,
        node_id: str,
        kind: str,
        reference: Any,
        parents: tuple[str, ...],
        policy: str,
        source_revision: int = 0,
    ) -> None:
        if not node_id or not node_id.strip():
            raise SourceLifecycleError("node_id must be non-blank")
        if kind == "source":
            if parents:
                raise SourceLifecycleError("source nodes cannot have parents")
            existing = self.nodes.get(node_id)
            if existing is not None:
                if existing.kind != "source" or existing.reference != reference:
                    raise SourceLifecycleError("conflicting source registration")
                return
            self.nodes[node_id] = LifecycleNode(
                kind="source",
                node_id=node_id,
                reference=reference,
                parents=(),
                policy="all",
                available=True,
                revision=source_revision,
            )
            return
        if kind not in {"memory", "topic"}:
            raise SourceLifecycleError("unknown node kind")
        if not parents:
            raise SourceLifecycleError("derived nodes require at least one parent")
        if len(set(parents)) != len(parents):
            raise SourceLifecycleError("parents must not repeat")
        for parent in parents:
            if parent == node_id or parent not in self.nodes:
                raise SourceLifecycleError("unknown or self parent")
        visited: set[str] = set()
        stack = list(parents)
        while stack:
            current = stack.pop()
            if current == node_id:
                raise SourceLifecycleError("dependency cycle")
            if current in visited:
                continue
            visited.add(current)
            stack.extend(self.nodes[current].parents)
        if policy not in {"all", "any"}:
            raise SourceLifecycleError("policy must be all or any")
        existing = self.nodes.get(node_id)
        if existing is not None:
            if (
                existing.kind != kind
                or existing.reference != reference
                or existing.parents != parents
                or existing.policy != policy
            ):
                raise SourceLifecycleError("conflicting derived registration")
            return
        support = [self.nodes[parent].available for parent in parents]
        available = all(support) if policy == "all" else any(support)
        self.nodes[node_id] = LifecycleNode(
            kind=kind,
            node_id=node_id,
            reference=reference,
            parents=parents,
            policy=policy,
            available=available,
        )

    def recompute(self) -> list[str]:
        changed: list[str] = []
        for node_id in self._topological_order():
            node = self.nodes[node_id]
            if node.kind == "source":
                continue
            support = [self.nodes[parent].available for parent in node.parents]
            available = all(support) if node.policy == "all" else any(support)
            if available != node.available:
                node.available = available
                node.applied = None
                changed.append(node_id)
        return changed

    def apply_event(self, event: SourceLifecycleEvent) -> None:
        node = self.nodes.get(event.node_id)
        if node is None or node.kind != "source":
            raise SourceLifecycleError("event node must be a registered source")
        if event.revision != node.revision:
            raise SourceLifecycleError("event revision does not match the source revision")
        if event.sequence < node.sequence:
            raise SourceLifecycleError("event sequence is not monotonic")
        if event.sequence == node.sequence:
            if node.last_event is None:
                raise SourceLifecycleError("same-sequence replay without a previous event")
            previous = node.last_event
            if (
                previous["revision"] != event.revision
                or previous["available"] != event.available
                or previous["reason"] != event.reason
            ):
                raise SourceLifecycleError("same-sequence event with different content")
            return
        if node.available != event.available:
            node.available = event.available
            node.applied = None
        node.sequence = event.sequence
        node.last_event = {
            "revision": event.revision,
            "available": event.available,
            "reason": event.reason,
            "sequence": event.sequence,
        }
        self.recompute()

    def active_ids(self) -> list[str]:
        return sorted(node_id for node_id, node in self.nodes.items() if node.available)

    def _topological_order(self) -> list[str]:
        indegree = {node_id: len(node.parents) for node_id, node in self.nodes.items()}
        children: dict[str, list[str]] = {}
        for node_id, node in self.nodes.items():
            for parent in node.parents:
                children.setdefault(parent, []).append(node_id)
        queue = deque(sorted(node_id for node_id, degree in indegree.items() if degree == 0))
        order: list[str] = []
        while queue:
            node_id = queue.popleft()
            order.append(node_id)
            for child in sorted(children.get(node_id, ())):
                indegree[child] -= 1
                if indegree[child] == 0:
                    queue.append(child)
        return order

    def to_state(self) -> dict:
        return {
            node_id: {
                "kind": node.kind,
                "node_id": node.node_id,
                "parents": list(node.parents),
                "policy": node.policy,
                "available": node.available,
                "revision": node.revision,
                "sequence": node.sequence,
                "last_event": node.last_event,
                "reference": serialize_reference(node),
            }
            for node_id, node in self.nodes.items()
        }

    @classmethod
    def from_state(cls, state: dict) -> "LifecycleGraph":
        graph = cls()
        for node_id, raw in state.items():
            graph.nodes[node_id] = LifecycleNode(
                kind=raw["kind"],
                node_id=raw["node_id"],
                reference=deserialize_reference(raw["reference"]),
                parents=tuple(raw["parents"]),
                policy=raw["policy"],
                available=raw["available"],
                revision=raw.get("revision", 0),
                sequence=raw.get("sequence", 0),
                last_event=raw.get("last_event"),
            )
        return graph


__all__ = [
    "LifecycleGraph",
    "LifecycleNode",
    "SourceLifecycleError",
    "SourceLifecycleEvent",
]
