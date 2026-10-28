#!/usr/bin/env bash
# 启动本地 seekdb server 并创建 edgekb 数据库（供 SEEKDB_MODE=server 使用）。
# seekdb 二进制获取见 https://github.com/oceanbase/seekdb（或使用嵌入式模式跳过本脚本）。
set -e
SEEKDB_BIN="${SEEKDB_BIN:-seekdb}"
BASE_DIR="${SEEKDB_BASE_DIR:-./seekdb-run}"
PORT="${SEEKDB_PORT:-2881}"
export LD_LIBRARY_PATH="${SEEKDB_LIB_PATH:-}"
mkdir -p "$BASE_DIR"
if ! "$SEEKDB_BIN" --version >/dev/null 2>&1; then
  echo "未找到 seekdb 二进制（SEEKDB_BIN=$SEEKDB_BIN），请先安装 seekdb 或改用嵌入式模式（SEEKDB_MODE=embedded）。"
  exit 1
fi
pkill -f "$SEEKDB_BIN" 2>/dev/null || true
sleep 1
(cd "$BASE_DIR" && nohup "$SEEKDB_BIN" --base-dir "$(pwd)" -P "$PORT" > seekdb-start.log 2>&1 &)
sleep 5
MYSQL_CLIENT="${MYSQL_CLIENT:-mysql}"
"$MYSQL_CLIENT" -h127.0.0.1 -P"$PORT" -uroot -e "CREATE DATABASE IF NOT EXISTS edgekb;" 2>&1 | tail -1 || \
  echo "请用任意 MySQL 客户端执行: CREATE DATABASE IF NOT EXISTS edgekb;"
echo "seekdb server 已启动（127.0.0.1:$PORT），数据库 edgekb 就绪。"
