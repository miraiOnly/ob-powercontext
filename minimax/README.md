# 规有所依——SDD 上下文管理系统

面向软件持续演进的规格驱动开发（SDD）上下文模块：把工程规格、代码、测试存入
**seekdb**（全文检索），用 **PowerContext** 保存任务进度，按开发任务组装上下文并交给
**MiniMax** 完成一次真实代码修改，最后用测试验证。

## 目录结构

```
minimax/
├── spec.md                    # 业务规格（本次样例的规格资料，v1；可更新后重新导入）
├── sdd/                       # 上下文模块（Python 包）
│   ├── cli.py                 # 调用入口（见下）
│   ├── store.py               # seekdb 资料存储与检索（pymysql + 全文索引）
│   ├── state.py               # PowerContext 任务状态保存/读取（嵌入式 SQLite 运行时）
│   ├── context.py             # 上下文组装（规格条目 + 代码 + 测试 + 状态）
│   └── agent.py               # MiniMax 开发助手接入（API Key 从环境变量读取）
├── project/                   # 业务工程（修改后状态）
│   ├── tracker.py             # IssueTracker 业务代码
│   └── tests/                 # 目标功能测试 + 回归测试
├── project_before/            # 业务工程（修改前快照，目标测试失败）
├── samples/SAMPLE.md          # 验证样例（含"要改什么/必须保持什么"）
├── records/                   # 测试日志、MiniMax 记录、数据库与状态记录
├── scripts/                   # seekdb 启动、一键验证脚本
├── requirements.txt
└── ONEPAGE.md                 # 一页技术说明
```

## 环境要求

| 组件 | 版本 | 用途 |
|---|---|---|
| Python | ≥3.11 | 模块与测试 |
| seekdb | v1.4.0（server 模式，MySQL 协议） | 工程资料存储与全文检索 |
| PowerContext | 冻结版 9db1753（pip 安装，见下） | 任务状态记忆 |
| MiniMax | MiniMax-M1（OpenAI 兼容 API） | 代码修改生成 |

### 安装依赖

```bash
cd minimax
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
```

`requirements.txt` 通过 git URL 固定 PowerContext 冻结版；`pymysql` 连接 seekdb；
`pytest` 跑测试；`requests` 调 MiniMax。

### 启动 seekdb

下载 seekdb v1.4.0 Linux 发行包（见 seekdb 项目说明，例如
`seekdb-1.4.0-linux-x86_64.tar.gz`），解压后：

```bash
bash scripts/start_seekdb.sh /path/to/seekdb-release /tmp/seekdb-sdd-run
```

脚本会在 127.0.0.1:2881 启动 seekdb 服务端（无需 root，需要 libaio，见脚本内注释）。

## 调用入口

```bash
. .venv/bin/activate
export PYTHONPATH="$PWD"          # 让 sdd 包可导入
export SEEKDB_HOST=127.0.0.1 SEEKDB_PORT=2881 SEEKDB_USER=root SEEKDB_PASSWORD=""

python -m sdd.cli init-db                    # 初始化 seekdb 库表与全文索引
python -m sdd.cli import                     # 导入规格/代码/测试资料
python -m sdd.cli context case-insensitive-search   # 输出开发上下文
python -m sdd.cli status case-insensitive-search    # 读取 PowerContext 任务状态
python -m sdd.cli record case-insensitive-search --done "..." --todo "..." --verification "..."
python -m sdd.cli verify                    # 跑修改后的功能+回归测试
python -m sdd.cli verify-before             # 跑修改前的测试（目标测试应失败）
python -m sdd.cli reset                     # 把 project/ 重置为修改前快照
python -m sdd.cli agent case-insensitive-search   # 调用 MiniMax 生成补丁（需 MINIMAX_API_KEY）
```

- `context` 的输出定位：适用的规格条目（带来源）、需修改的代码文件、验证用测试、任务状态；
- 状态与资料均持久化：重启 seekdb / 重开终端后 `status`、`context` 依然可用。

## 验证样例

完整步骤、预期结果与重置方法见 [`samples/SAMPLE.md`](samples/SAMPLE.md)。

## 技术使用记录

- MiniMax 对话与补丁：`records/minimax_agent_log.md`（由 `agent` 子命令真实调用生成）；
- 数据库查询记录：`records/db_query_record.md`；
- PowerContext 状态写入/复用记录：`records/pc_state_record.md`；
- 修改前后测试日志：`records/test_log_before.txt` / `records/test_log_after.txt`。

## 已知限制

- seekdb 全文索引使用 beng 解析器，主要面向英文/数字检索；中文规格检索可降级为 LIKE 兜底（store.py 已处理）。
- `agent` 子命令需要有效的 MiniMax API Key；评审环境无 Key 时可用 `records/minimax_agent_log.md` 核对参与过程，并直接使用仓库中的修改后代码。
