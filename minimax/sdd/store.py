"""seekdb 资料存储与检索：工程规格 / 代码 / 测试的导入与查询。

使用 seekdb 服务端模式（MySQL 协议，pymysql 接入），工程资料存入
`materials` 表，全文索引支持关键词检索；查询结果用于构造任务上下文。
"""

from __future__ import annotations

import hashlib
import time

import pymysql

SCHEMA_SQL = """
CREATE DATABASE IF NOT EXISTS sdd_context DEFAULT CHARSET utf8mb4;
USE sdd_context;
CREATE TABLE IF NOT EXISTS materials (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  kind VARCHAR(32) NOT NULL,
  name VARCHAR(255) NOT NULL,
  content TEXT NOT NULL,
  source VARCHAR(255) NOT NULL,
  updated_at BIGINT NOT NULL,
  KEY idx_kind (kind)
) DEFAULT CHARSET utf8mb4 COMMENT='SDD engineering materials';
"""

FULLTEXT_SQL = """
USE sdd_context;
ALTER TABLE materials ADD FULLTEXT INDEX ft_materials(name, content) WITH PARSER beng;
"""


class SeekdbStore:
    def __init__(self, host="127.0.0.1", port=2881, user="root", password="", database="sdd_context"):
        self._cfg = dict(host=host, port=port, user=user, password=password)
        self.database = database

    def _conn(self):
        return pymysql.connect(**self._cfg, charset="utf8mb4", autocommit=True)

    def init_schema(self) -> None:
        # Execute statement by statement: some servers surface multi-statement
        # errors (e.g. a pre-existing fulltext index) only when the cursor
        # drains its remaining result sets, outside a single execute call.
        statements = [
            "CREATE DATABASE IF NOT EXISTS sdd_context DEFAULT CHARSET utf8mb4",
            "CREATE TABLE IF NOT EXISTS sdd_context.materials ("
            " id BIGINT AUTO_INCREMENT PRIMARY KEY,"
            " kind VARCHAR(32) NOT NULL,"
            " name VARCHAR(255) NOT NULL,"
            " content TEXT NOT NULL,"
            " source VARCHAR(255) NOT NULL,"
            " updated_at BIGINT NOT NULL,"
            " KEY idx_kind (kind)"
            ") DEFAULT CHARSET utf8mb4 COMMENT='SDD engineering materials'",
            "ALTER TABLE sdd_context.materials "
            "ADD FULLTEXT INDEX ft_materials(name, content) WITH PARSER beng",
        ]
        with self._conn() as conn:
            with conn.cursor() as cur:
                for statement in statements:
                    try:
                        cur.execute(statement)
                        while cur.nextset():
                            pass
                    except pymysql.err.OperationalError as exc:
                        # fulltext index may already exist or unsupported on a
                        # given deployment; materials table remains usable.
                        print(f"[store] schema statement skipped: {exc}")

    def import_material(self, kind: str, name: str, content: str, source: str) -> int:
        updated_at = int(time.time() * 1000)
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM sdd_context.materials WHERE kind=%s AND name=%s",
                    (kind, name),
                )
                cur.execute(
                    "INSERT INTO sdd_context.materials (kind, name, content, source, updated_at) "
                    "VALUES (%s, %s, %s, %s, %s)",
                    (kind, name, content, source, updated_at),
                )
                return int(cur.lastrowid)

    def query(self, terms: list[str], kinds: tuple[str, ...] | None = None, limit: int = 8) -> list[dict]:
        """关键词检索：对每个词做全文 MATCH，无命中时按 LIKE 兜底。"""
        seen: dict[str, dict] = {}
        with self._conn() as conn:
            with conn.cursor(pymysql.cursors.DictCursor) as cur:
                for term in terms:
                    match_where = "MATCH(name, content) AGAINST(%s IN NATURAL LANGUAGE MODE)"
                    args: list = [term]
                    if kinds:
                        match_where += " AND kind IN (%s)" % ",".join(["%s"] * len(kinds))
                        args.extend(kinds)
                    try:
                        cur.execute(
                            f"SELECT id, kind, name, content, source, updated_at "
                            f"FROM sdd_context.materials WHERE {match_where} LIMIT {limit}",
                            args,
                        )
                    except pymysql.err.OperationalError:
                        cur.execute(
                            f"SELECT id, kind, name, content, source, updated_at "
                            f"FROM sdd_context.materials WHERE content LIKE %s LIMIT {limit}",
                            (f"%{term}%",),
                        )
                    for row in cur.fetchall():
                        key = (row["kind"], row["name"])
                        if key not in seen:
                            seen[key] = {
                                "kind": row["kind"],
                                "name": row["name"],
                                "content": row["content"],
                                "source": row["source"],
                                "updated_at": row["updated_at"],
                            }
        return sorted(seen.values(), key=lambda item: (item["kind"], item["name"]))

    def get(self, kind: str, name: str) -> dict | None:
        with self._conn() as conn:
            with conn.cursor(pymysql.cursors.DictCursor) as cur:
                cur.execute(
                    "SELECT id, kind, name, content, source, updated_at FROM sdd_context.materials "
                    "WHERE kind=%s AND name=%s LIMIT 1",
                    (kind, name),
                )
                row = cur.fetchone()
                return dict(row) if row else None
