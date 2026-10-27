"""End-to-end demo of the Village NPC memory backend (no Dify required).

Simulates two separate conversations for the same player and shows that
the NPC's PowerContext memory (stored on seekdb) survives across them:

  conversation 1: the player plays a prank, the NPC remembers it
  conversation 2: a NEW conversation asks about old history and the
                  recalled memory contains the prank details

Run:
  pip install -r backend/requirements.txt
  NPC_SEEKDB_PATH=./data/npc-seekdb uvicorn backend.app:app --port 8000
  python samples/demo.py
"""

import asyncio
import json
import sys
from pathlib import Path

import httpx

BASE = "http://127.0.0.1:8000"

TURNS = [
    ("conversation-1", "铁蛋你好，我叫小明！"),
    ("conversation-1", "我把你家门口的灯笼换成南瓜了，哈哈！"),
    ("conversation-1", "还有，我往你的茶壶里倒了半瓶醋。"),
    ("conversation-2", "铁蛋，你还记得我是谁吗？我干过什么坏事？"),
]


async def main() -> None:
    async with httpx.AsyncClient(timeout=30) as client:
        health = (await client.get(f"{BASE}/api/health")).json()
        print(f"[health] {json.dumps(health, ensure_ascii=False)}")
        assert health.get("engine") == "powercontext"

        for conversation, message in TURNS:
            remembered = await client.post(
                f"{BASE}/api/memory/remember",
                json={"player": "小明", "text": message},
            )
            print(f"[{conversation}] remember {json.dumps(remembered.json(), ensure_ascii=False)} <- {message}")
            assert remembered.json()["ok"] is True

        # New conversation asks about the player's history.
        recall = await client.post(
            f"{BASE}/api/memory/recall",
            json={"player": "小明", "query": "恶作剧 灯笼 茶壶 醋", "limit": 5},
        )
        payload = recall.json()
        print(f"[conversation-2] recall -> {json.dumps(payload, ensure_ascii=False, indent=2)}")
        memories = payload["memories"]
        joined = " ".join(item["text"] for item in memories)
        assert "灯笼" in joined and "南瓜" in joined, "prank #1 not recalled"
        assert "醋" in joined, "prank #2 not recalled"
        print("PASS: the NPC remembers the player's pranks in a new conversation.")


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
