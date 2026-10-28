"""MiniMax 开发助手接入：给定上下文与任务，生成代码修改。

使用 MiniMax 大模型 API（OpenAI 兼容接口），API Key 从环境变量
MINIMAX_API_KEY 读取（不写入仓库）。完整对话与生成结果落盘到
records/minimax_agent_log.md，作为"MiniMax 实际参与"的开发记录。
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import requests

MINIMAX_API_BASE = os.environ.get("MINIMAX_API_BASE", "https://api.minimaxi.com/v1")
MINIMAX_MODEL = os.environ.get("MINIMAX_MODEL", "MiniMax-M1")


class MiniMaxAgent:
    def __init__(self, record_path: Path | str = "records/minimax_agent_log.md"):
        self.record_path = Path(record_path)
        self.api_key = os.environ.get("MINIMAX_API_KEY", "")

    def available(self) -> bool:
        return bool(self.api_key)

    def generate_patch(self, task: str, context: str, target_file: Path) -> str:
        """请求 MiniMax 生成修改后的 target_file 内容，返回新内容。"""
        if not self.available():
            raise RuntimeError("MINIMAX_API_KEY 未设置，无法调用 MiniMax")

        original = target_file.read_text(encoding="utf-8")
        prompt = f"""你是规格驱动开发（SDD）助手。请根据以下上下文修改代码。

## 开发上下文
{context}

## 目标文件：{target_file.name}
当前内容如下。请只修改实现任务所需的部分，保持其余行为不变，并输出完整的修改后文件内容（不要输出解释，只输出代码）：

{original}
"""
        messages = [
            {"role": "system", "content": "You are a spec-driven development assistant. Output code only."},
            {"role": "user", "content": prompt},
        ]
        response = requests.post(
            f"{MINIMAX_API_BASE}/chat/completions",
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
            json={"model": MINIMAX_MODEL, "messages": messages, "temperature": 0.2},
            timeout=180,
        )
        response.raise_for_status()
        body = response.json()
        answer = body["choices"][0]["message"]["content"]

        self._append_record(task, context, original, answer, body.get("model", MINIMAX_MODEL))
        return self._strip_code_fence(answer)

    def _append_record(self, task: str, context: str, before: str, answer: str, model: str) -> None:
        self.record_path.parent.mkdir(parents=True, exist_ok=True)
        entry = (
            f"\n\n---\n\n## 任务：{task}\n\n"
            f"### 提供的上下文（摘要）\n\n```\n{context[:1500]}\n```\n\n"
            f"### 模型\n\n{model}\n\n"
            f"### 修改前文件内容\n\n```python\n{before[:2000]}\n```\n\n"
            f"### MiniMax 输出\n\n```\n{answer[:4000]}\n```\n"
        )
        with self.record_path.open("a", encoding="utf-8") as handle:
            handle.write(entry)

    @staticmethod
    def _strip_code_fence(answer: str) -> str:
        text = answer.strip()
        if text.startswith("```"):
            lines = text.splitlines()
            if lines and lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            return "\n".join(lines) + "\n"
        return text + "\n"
