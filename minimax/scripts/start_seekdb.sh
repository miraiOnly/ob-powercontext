#!/usr/bin/env bash
# 启动 seekdb 服务端（MySQL 协议，127.0.0.1:2881）
# 用法: bash scripts/start_seekdb.sh <seekdb 解压目录> <数据目录>
set -euo pipefail

RELEASE_DIR="${1:?usage: start_seekdb.sh <seekdb-release-dir> <base-dir>}"
BASE_DIR="${2:?usage: start_seekdb.sh <seekdb-release-dir> <base-dir>}"

BINARY="$RELEASE_DIR/seekdb"
[[ -x "$BINARY" ]] || {
  echo "找不到可执行文件: $BINARY" >&2
  echo "请从 seekdb 项目说明下载 v1.4.0 Linux 发行包并解压。" >&2
  exit 2
}

# libaio 动态库路径（发行包自带 deps）
if [[ -d "$RELEASE_DIR/deps/3rd/usr/local/oceanbase/deps/devel/lib" ]]; then
  export LD_LIBRARY_PATH="$RELEASE_DIR/deps/3rd/usr/local/oceanbase/deps/devel/lib:$LD_LIBRARY_PATH"
fi

mkdir -p "$BASE_DIR"
pkill -f "$BASE_DIR.*seekdb" 2>/dev/null || true
sleep 1
rm -f "$BASE_DIR/run/seekdb.pid" "$BASE_DIR/run/sql.sock" 2>/dev/null || true

nohup "$BINARY" --base-dir "$BASE_DIR" > "$BASE_DIR/seekdb.log" 2>&1 &
echo "seekdb 启动中，等待端口 2881 ..."
for _ in $(seq 1 60); do
  if python3 - <<'PY'
import socket
s = socket.socket()
s.settimeout(1)
try:
    s.connect(("127.0.0.1", 2881)); print("ok")
except Exception:
    raise SystemExit(1)
PY
  then
    echo "seekdb 已就绪: 127.0.0.1:2881"
    exit 0
  fi
  sleep 2
done
echo "seekdb 启动超时，日志尾部："
tail -20 "$BASE_DIR/seekdb.log"
exit 1
