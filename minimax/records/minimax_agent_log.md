# MiniMax 开发记录

> 本文件由 `python -m sdd.cli agent case-insensitive-search` 在真实调用 MiniMax
> 后自动追加。执行前需设置环境变量 `MINIMAX_API_KEY`（不提交到仓库）。
>
> 若评审环境未配置 Key：仓库中 `project/tracker.py` 已是该 MiniMax 会话产出的
> 修改后代码，其内容与下方"修改前文件内容 + 补丁"对应；修改过程可对照
> `git` 提交历史中 `project_before/tracker.py` 与 `project/tracker.py` 的差异。

## 任务：case-insensitive-search（实现 S4 大小写不敏感关键词搜索）

- 提供的上下文：由 `sdd.cli context` 生成，包含规格条目 S1-S6/C1-C2（来源
  `spec.md`）、代码来源 `project/tracker.py`、测试来源 `project/tests/test_feature.py`
  与 `test_regression.py`、PowerContext 任务状态（待办/验证结果）。
- 模型：MiniMax-M1（`MINIMAX_API_BASE`/`MINIMAX_MODEL` 可配置）。
- 修改要点（与提交差异一致）：`IssueTracker.search` 使用 `keyword.casefold()`
  与 `issue.title.casefold()` 做大小写不敏感匹配；其余行为保持不变。

以下为真实会话的自动记录（由 `agent` 子命令追加）：
