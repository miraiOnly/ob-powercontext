"""SDD 上下文模块入口：init-db / import / context / status / record / agent / verify / reset。"""

from __future__ import annotations

import argparse
import asyncio
import subprocess
import sys
from pathlib import Path

from sdd.agent import MiniMaxAgent
from sdd.context import ContextBuilder
from sdd.state import PowerContextState
from sdd.store import SeekdbStore

REPO_ROOT = Path(__file__).resolve().parents[1]


def _store() -> SeekdbStore:
    import os

    return SeekdbStore(
        host=os.environ.get("SEEKDB_HOST", "127.0.0.1"),
        port=int(os.environ.get("SEEKDB_PORT", "2881")),
        user=os.environ.get("SEEKDB_USER", "root"),
        password=os.environ.get("SEEKDB_PASSWORD", ""),
    )


def _state() -> PowerContextState:
    return PowerContextState(str(REPO_ROOT / "state"))


def cmd_init_db(args: argparse.Namespace) -> int:
    _store().init_schema()
    print("[sdd] seekdb schema ready (sdd_context.materials)")
    return 0


def cmd_import(args: argparse.Namespace) -> int:
    store = _store()
    store.init_schema()
    files = [
        ("spec", "issue-tracker-spec.md", REPO_ROOT / "spec.md", "spec.md (v1/v2 以当前文件为准)"),
        ("code", "tracker.py", REPO_ROOT / "project" / "tracker.py", "project/tracker.py"),
        ("test", "test_feature.py", REPO_ROOT / "project" / "tests" / "test_feature.py", "project/tests/test_feature.py"),
        ("test", "test_regression.py", REPO_ROOT / "project" / "tests" / "test_regression.py", "project/tests/test_regression.py"),
    ]
    for kind, name, path, source in files:
        content = path.read_text(encoding="utf-8")
        row_id = store.import_material(kind, name, content, source)
        print(f"[sdd] imported {kind:<4} {name:<24} -> row {row_id} ({len(content)} bytes)")
    return 0


def cmd_context(args: argparse.Namespace) -> int:
    async def run() -> None:
        builder = ContextBuilder(_store(), _state(), REPO_ROOT)
        print(await builder.build(args.task))

    asyncio.run(run())
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    async def run() -> None:
        state = await _state().load(args.task)
        if not state:
            print(f"[sdd] 任务 {args.task} 无历史状态")
            return
        for key, label in (("done", "已完成"), ("todo", "待办"), ("verification", "验证结果"), ("notes", "备注")):
            if state.get(key):
                print(f"- {label}：{state[key]}")

    asyncio.run(run())
    return 0


def cmd_record(args: argparse.Namespace) -> int:
    async def run() -> None:
        entries = {"done": args.done or "", "todo": args.todo or "", "verification": args.verification or ""}
        await _state().save(args.task, entries)
        print(f"[sdd] 已保存任务状态：{args.task}")

    asyncio.run(run())
    return 0


def cmd_agent(args: argparse.Namespace) -> int:
    async def run() -> None:
        builder = ContextBuilder(_store(), _state(), REPO_ROOT)
        context = await builder.build(args.task)
        agent = MiniMaxAgent(REPO_ROOT / "records" / "minimax_agent_log.md")
        target = REPO_ROOT / "project" / "tracker.py"
        new_content = agent.generate_patch(args.task, context, target)
        backup = target.with_suffix(".py.bak")
        if not backup.exists():
            backup.write_text(target.read_text(encoding="utf-8"), encoding="utf-8")
        target.write_text(new_content, encoding="utf-8")
        print("[sdd] MiniMax 补丁已写入 project/tracker.py（原文件备份于 tracker.py.bak）")

    asyncio.run(run())
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "tests", "-v"],
        cwd=REPO_ROOT / "project",
    )
    return result.returncode


def cmd_verify_before(args: argparse.Namespace) -> int:
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "tests", "-v"],
        cwd=REPO_ROOT / "project_before",
    )
    return result.returncode


def cmd_reset(args: argparse.Namespace) -> int:
    import shutil

    before = REPO_ROOT / "project_before"
    after = REPO_ROOT / "project"
    if after.exists():
        shutil.rmtree(after)
    shutil.copytree(before, after)
    bak = after / "tracker.py.bak"
    if bak.exists():
        bak.unlink()
    print("[sdd] 已重置 project/ 为修改前快照")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="sdd", description="SDD 工程上下文模块")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init-db").set_defaults(func=cmd_init_db)
    sub.add_parser("import").set_defaults(func=cmd_import)

    context = sub.add_parser("context")
    context.add_argument("task", help="任务关键词，例如 case-insensitive-search")
    context.set_defaults(func=cmd_context)

    status = sub.add_parser("status")
    status.add_argument("task")
    status.set_defaults(func=cmd_status)

    record = sub.add_parser("record")
    record.add_argument("task")
    record.add_argument("--done")
    record.add_argument("--todo")
    record.add_argument("--verification")
    record.set_defaults(func=cmd_record)

    agent = sub.add_parser("agent")
    agent.add_argument("task")
    agent.set_defaults(func=cmd_agent)

    sub.add_parser("verify").set_defaults(func=cmd_verify)
    sub.add_parser("verify-before").set_defaults(func=cmd_verify_before)
    sub.add_parser("reset").set_defaults(func=cmd_reset)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
