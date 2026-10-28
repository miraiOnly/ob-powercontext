# 验证样例：大小写不敏感关键词搜索

## 任务描述

业务工程 `project/`（IssueTracker 任务看板）的规格 S4 要求：`search(keyword)`
对大小写不敏感。**当前实现是大小写敏感的子串匹配，目标功能未实现，需要修改。**

- **要改什么**：`project/tracker.py` 中 `IssueTracker.search`，使匹配不区分大小写（例如 `Database` 命中 `Fix database connection pool`）。
- **哪些原有行为必须保持**：
  - `filter_by_status` 的过滤与顺序（S3，回归测试保护）；
  - 空关键词返回空列表（S5）；
  - 搜索不改变任何 issue 状态与顺序（S6）；
  - 公开函数签名 `add / list_issues / filter_by_status / search` 不变（C2）。

## 初始数据与操作步骤

1. **环境准备**（详见 README）：
   ```bash
   cd minimax
   python3 -m venv .venv && . .venv/bin/activate
   pip install -r requirements.txt
   bash scripts/start_seekdb.sh /path/to/seekdb-release /tmp/seekdb-sdd-run
   export PYTHONPATH="$PWD" SEEKDB_HOST=127.0.0.1 SEEKDB_PORT=2881 SEEKDB_USER=root SEEKDB_PASSWORD=""
   ```
2. **初始化与导入资料**：
   ```bash
   python -m sdd.cli init-db
   python -m sdd.cli import
   ```
3. **修改前验证（目标测试必须失败）**：
   ```bash
   python -m sdd.cli verify-before ; echo "exit=$?"
   ```
   预期：`test_search_is_case_insensitive` 与 `test_search_mixed_case_keyword` **失败**（功能未实现），回归测试全部通过，命令退出码非 0。
4. **生成上下文**：
   ```bash
   python -m sdd.cli context case-insensitive-search
   ```
   预期输出包含：
   - 规格条目：S3、S4、S5、S6、C1、C2，来源 `spec.md`；
   - 代码来源 `project/tracker.py`、测试来源 `project/tests/test_feature.py` 与 `test_regression.py`；
   - 任务状态：从空状态开始时显示"无历史状态"。
5. **保存任务状态**：
   ```bash
   python -m sdd.cli record case-insensitive-search \
     --todo "实现 S4 大小写不敏感搜索" --verification "目标测试待跑"
   ```
6. **重启后状态复用**（新开终端，重新加载模块）：
   ```bash
   python -m sdd.cli status case-insensitive-search
   ```
   预期：读回"待办：实现 S4 大小写不敏感搜索""验证结果：目标测试待跑"。
7. **MiniMax 完成代码修改**（需要 Key；评审可通过记录核对）：
   ```bash
   export MINIMAX_API_KEY=<你的 Key>
   python -m sdd.cli agent case-insensitive-search
   ```
   模块把上下文与 `tracker.py` 现状发给 MiniMax，把返回的补丁写入 `project/tracker.py`，并记录到 `records/minimax_agent_log.md`。若评审环境无 Key，仓库中的 `project/tracker.py` 已是 MiniMax 会话产出的修改后版本。
8. **修改后验证（必须全部通过）**：
   ```bash
   python -m sdd.cli verify ; echo "exit=$?"
   ```
   预期：8 个测试全部通过（4 个目标功能 + 4 个回归 + 空关键词场景），退出码 0。
9. **记录验证结果并再次读取**：
   ```bash
   python -m sdd.cli record case-insensitive-search \
     --done "S4 已实现（casefold 匹配）" --verification "pytest 8 passed"
   python -m sdd.cli status case-insensitive-search
   ```

## 规格更新检查（上下文正确性验收）

1. 编辑 `spec.md`：把 S4 的匹配行为改为"**大小写敏感**"并加一条 S7（例如"搜索词长度上限 64"）；
2. 重新导入：`python -m sdd.cli import`；
3. 再次 `python -m sdd.cli context case-insensitive-search`；
4. 预期：输出中的 S4 描述随之更新，并出现 S7——证明模块使用更新后的规格，不返回固定答案；
5. 恢复：`git checkout -- spec.md`（或改回原文）后重新 `import`。

## 重置方法

```bash
python -m sdd.cli reset          # project/ 恢复为修改前快照
# 清空状态与资料（如需完全重置）：
rm -rf state records/minimax_agent_log.md
python -m sdd.cli init-db && python -m sdd.cli import
```

## 必须包含在上下文中的来源（评审核对清单）

| 类别 | 条目 | 来源 |
|---|---|---|
| 规格 | S3/S4/S5/S6、C1/C2 | `spec.md` |
| 代码 | `IssueTracker.search` 所在文件 | `project/tracker.py` |
| 测试 | 目标测试 + 回归测试 | `project/tests/test_feature.py`、`project/tests/test_regression.py` |
| 状态 | 已完成/待办/验证结果 | PowerContext（`state/` SQLite） |
