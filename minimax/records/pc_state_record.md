# PowerContext 使用记录（状态写入与重启后复用）

## 接入方式

- PowerContext 冻结版（commit 9db1753），嵌入式运行时 + SQLite 文件持久化
  （`state/powercontext_state.db`），无外部服务；
- 每个任务一条状态记忆（scope 幂等键 `sdd-<task>`），内容为 JSON：
  `{"task": ..., "state": {"done" | "todo" | "verification" | "notes": ...}}`；
- `save` / `load` 每次调用独立打开运行时，`load` 与 `save` 分属不同进程，
  等价于"模块重启后读取"。

## 写入（`python -m sdd.cli record case-insensitive-search --todo "..." --verification "..."`）

```
[sdd] 已保存任务状态：case-insensitive-search
```

## 重启读取（新进程执行 `python -m sdd.cli status case-insensitive-search`）

```
- 待办：实现 S4 大小写不敏感搜索
- 验证结果：目标测试待跑
```

状态进入后续上下文：`python -m sdd.cli context case-insensitive-search`
输出中包含：

```
## 任务状态（来自 PowerContext 记忆）
- 待办：实现 S4 大小写不敏感搜索
- 验证结果：目标测试待跑
```

（首次为空状态时输出"无历史状态（从空状态开始）"。）

## 验证结果写回（修改后）

```
python -m sdd.cli record case-insensitive-search \
  --done "S4 已实现（casefold 匹配）" --verification "pytest 9 passed"
python -m sdd.cli status case-insensitive-search
- 已完成：S4 已实现（casefold 匹配）
- 验证结果：pytest 9 passed
```
