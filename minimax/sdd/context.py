"""开发上下文组装：seekdb 检索出的规格/代码/测试 + PowerContext 任务状态。"""

from __future__ import annotations

from pathlib import Path

from .store import SeekdbStore
from .state import PowerContextState

TASK_KEYWORDS = {
    "case-insensitive-search": {
        "terms": ["search", "keyword", "case", "insensitive", "filter", "database"],
        "kinds": ("spec", "code", "test"),
    },
}


class ContextBuilder:
    def __init__(self, store: SeekdbStore, state: PowerContextState, repo_root: Path):
        self.store = store
        self.state = state
        self.repo_root = repo_root

    async def build(self, task: str) -> str:
        spec_terms = TASK_KEYWORDS.get(task, {}).get("terms", task.split())
        kinds = TASK_KEYWORDS.get(task, {}).get("kinds")
        materials = self.store.query(spec_terms, kinds=kinds, limit=10)
        task_state = await self.state.load(task) or {}

        lines: list[str] = []
        lines.append("# SDD 开发上下文")
        lines.append("")
        lines.append("## 任务")
        lines.append(f"- 目标：{task}")
        lines.append("")
        lines.append("## 适用的规格条目（来自 seekdb 检索）")
        for item in materials:
            if item["kind"] == "spec":
                for clause in self._spec_clauses(item["content"]):
                    lines.append(f"- 来源 {item['source']}（{item['name']}）：{clause}")
        if not any(item["kind"] == "spec" for item in materials):
            lines.append("- （未检索到规格）")
        lines.append("")
        lines.append("## 需要修改的代码（来自 seekdb 检索）")
        for item in materials:
            if item["kind"] == "code":
                lines.append(f"- 来源 {item['source']}（{item['name']}）")
        if not any(item["kind"] == "code" for item in materials):
            lines.append("- （未检索到代码）")
        lines.append("")
        lines.append("## 验证测试（来自 seekdb 检索）")
        for item in materials:
            if item["kind"] == "test":
                lines.append(f"- 来源 {item['source']}（{item['name']}）")
        if not any(item["kind"] == "test" for item in materials):
            lines.append("- （未检索到测试）")
        lines.append("")
        lines.append("## 任务状态（来自 PowerContext 记忆）")
        if task_state:
            for key, label in (("done", "已完成"), ("todo", "待办"), ("verification", "验证结果")):
                if task_state.get(key):
                    lines.append(f"- {label}：{task_state[key]}")
        else:
            lines.append("- 无历史状态（从空状态开始）")
        lines.append("")
        return "\n".join(lines)

    @staticmethod
    def _spec_clauses(content: str) -> list[str]:
        clauses = []
        for line in content.splitlines():
            line = line.strip()
            if line.startswith("- **S") or line.startswith("- **C"):
                clauses.append(line.lstrip("- ").strip())
        return clauses
