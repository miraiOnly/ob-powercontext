# 一页技术说明：规有所依——SDD 上下文管理系统

## 模块结构

```
sdd/cli.py       统一入口（init-db/import/context/status/record/agent/verify/reset）
sdd/store.py     seekdb 资料层：materials 表 + 全文索引，导入与关键词检索
sdd/state.py     PowerContext 状态层：嵌入式 SQLite 运行时，任务状态记忆
sdd/context.py   上下文组装：规格条款 + 代码/测试来源 + 任务状态 -> 开发上下文
sdd/agent.py     MiniMax 接入：上下文 + 目标文件 -> 代码补丁 -> 落盘记录
```

## 上下文选择方式

1. 以任务关键词（`case-insensitive-search` 映射为 search/keyword/case/... 词表）在
   seekdb 的 `materials` 表上做全文检索（无命中时 LIKE 兜底）；
2. 检索结果按 kind 分栏：spec 条款逐条带来源输出、code/test 列出文件来源；
3. PowerContext 按任务名检索状态记忆，输出已完成/待办/验证结果；
4. 三者合成 markdown 上下文交给 MiniMax；补丁写回业务工程后跑测试，测试结果
   再次写入 PowerContext。

## 各技术分工

| 技术 | 实际作用 | 证据 |
|---|---|---|
| seekdb | 保存规格/代码/测试资料；全文检索结果直接构成上下文 | `records/db_query_record.md`、`context` 输出带来源 |
| PowerContext | 任务状态（已完成/待办/验证结果）写入与重启后读取 | `records/pc_state_record.md`、`status` 命令 |
| MiniMax | 依据上下文生成 `tracker.py` 的修改补丁 | `records/minimax_agent_log.md`、`git diff` 对应 |

## 验证闭环

修改前：目标测试失败（S4 未实现）→ 上下文 + MiniMax 补丁 → 修改后：功能与回归
测试全部通过 → 状态写回 PowerContext → 重启模块可读取。
