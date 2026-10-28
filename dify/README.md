# 记仇的村口 NPC · Dify × PowerContext

Just for Fun 参赛作品：**一个会记住玩家恶作剧的村口 NPC**。你干的每件坏事，都会被写进 PowerContext 记忆；换一个新对话再去找他，他照样翻旧账。

## 1. 作品创意与玩法

村口 NPC「铁蛋」爱记仇。玩家的每次互动（自我介绍、恶作剧、求饶）都会作为一条记忆写入 PowerContext，并按**玩家昵称**隔离。开一个新会话、报上同一个名字，NPC 会从记忆里检索出你以前干过的坏事并当场阴阳你。

- 核心体验：跨会话记忆——「新对话里 NPC 记得你上一个对话干的事」。
- 玩法：对话 1 里做 1~2 件恶作剧 → 结束对话 → 开一个新对话报同一昵称 → 问「还记得我吗」，NPC 翻出旧账。
- 趣味点：NPC 人设是市井碎嘴、爱翻旧账、刀子嘴豆腐心。

## 2. 技术分工

| 组件 | 职责 |
| --- | --- |
| Dify Chatflow | 应用核心流程：开始（玩家昵称）→ HTTP 记忆检索 → LLM 生成 NPC 回应 → HTTP 记忆写入 → 回答 |
| PowerContext | 记忆引擎：每个玩家一个 Scope；互动作为 Source 捕获并流入 Memory；回忆通过 `memory.search` 全文检索返回 |
| 记忆后端（配套 FastAPI 服务） | 把 Dify 的 HTTP 调用翻译成 PowerContext 调用，运行 `powercontext[builtin]==1.2.0` |

未使用 seekdb（题目允许二者选一，本项目选择 PowerContext）。PowerContext 记忆持久化在 SQLite 文件，重启服务后记忆仍在。

## 3. 运行环境

- Python 3.12，Linux/macOS
- Dify 社区版/云端版（支持导入 DSL 的 Chatflow，`version: 0.1.5`）
- 任一可用的 Chat 模型（导入后把 LLM 节点绑定到你的模型即可，默认模板为 `openai/gpt-4o-mini`）
- 依赖：`fastapi`、`uvicorn`、`pydantic`、`powercontext[builtin]==1.2.0`（PyPI）

## 4. 启动步骤

### 4.1 启动记忆后端

```bash
cd backend
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
# 数据文件默认 ./data/npc-memory.db，可用 NPC_DB_PATH 覆盖
uvicorn app:app --host 0.0.0.0 --port 8000
```

验证：

```bash
curl http://127.0.0.1:8000/api/health
# {"ok":true,"storage":"sqlite","engine":"powercontext"}
```

### 4.2 导入 DSL

1. Dify → 创建应用 → 导入 DSL 文件 → 选择 `dsl/village-npc-chatflow.yml`。
2. 打开应用编排，把 `NPC 回应` LLM 节点的模型改为你已配置的模型。
3. 工作流环境变量 `backend_url` 默认 `http://127.0.0.1:8000`；如后端在别的地址，改成实际地址（Dify 所在机器必须能访问该地址）。
4. 发布并打开「运行」。

## 5. 验证样例（可重复执行）

### 样例 A：跨会话记忆

| 步骤 | 操作 | 预期 |
| --- | --- | --- |
| 1 | 新对话，输入玩家昵称 `小明` | NPC 打招呼，询问来意 |
| 2 | 输入：「我把你家门口的灯笼换成南瓜了」 | NPC 表现出「记下了这笔账」，并威胁下次算账 |
| 3 | 输入：「我还往你茶壶里倒了半瓶醋」 | NPC 继续记账，碎碎念 |
| 4 | **结束对话，开一个新对话**，昵称仍填 `小明` | — |
| 5 | 输入：「铁蛋，你还记得我是谁吗？」 | NPC 翻出旧账：提到**灯笼/南瓜**和**茶壶/醋**（PowerContext 检索结果注入提示词） |

### 样例 B：记忆隔离

开新对话用昵称 `小红` 问同样的问题，NPC 不记得小明的旧账（每个玩家独立 Scope）。

### 样例 C：本地端到端证据（无需 Dify 也可复现记忆链路）

```bash
pip install -r backend/requirements.txt
uvicorn app:app --port 8000 &   # 后端
python samples/demo.py          # 两段对话 + 召回断言
```

`demo.py` 输出含 `PASS: the NPC remembers the player's pranks in a new conversation.`。实际运行日志见 `samples/demo_output.log`。

## 6. 已知限制

- NPC 记忆按昵称区分，不校验身份；同名玩家共享记忆（村口 NPC 只认名字）。
- 记忆检索为全文检索（fts），按关键词召回；完全换一种说法的旧账可能召回较弱。
- 后端单进程内存缓存 Scope 映射，重启后自动重建；记忆数据持久化于 SQLite。
- 未配置 Embedding/Rerank 服务（本玩法全文检索足够）。

## 7. 来源与改动

- Dify Chatflow 的节点结构参考了公开示例 [dzh-gitcode/dify-workflows](https://github.com/dzh-gitcode/dify-workflows)（Apache-2.0 许可的 DSL 模板集合），本项目为全新编排：仅沿用其 DSL 0.1.5 结构约定，节点与流程全部重写。
- PowerContext 使用 PyPI 发布的 `powercontext[builtin]==1.2.0`（Apache-2.0）。
