# seekdb 使用记录（实际查询用于构造任务上下文）

## 接入方式

- seekdb v1.4.0 服务端模式，MySQL 协议（127.0.0.1:2881），Python 侧 `pymysql`；
- 资料表：`sdd_context.materials`（kind/name/content/source/updated_at）；
- 全文索引：`FULLTEXT(name, content) WITH PARSER beng`。

## 导入操作（`python -m sdd.cli import`，实测输出）

```
[sdd] imported spec issue-tracker-spec.md    -> row 9 (698 bytes)
[sdd] imported code tracker.py               -> row 10 (1199 bytes)
[sdd] imported test test_feature.py          -> row 11 (1280 bytes)
[sdd] imported test test_regression.py       -> row 12 (1074 bytes)
```

对应 SQL：`DELETE FROM sdd_context.materials WHERE kind=%s AND name=%s` + `INSERT INTO sdd_context.materials ...`。

## 检索操作（`python -m sdd.cli context case-insensitive-search`）

对每个任务词执行：

```sql
SELECT id, kind, name, content, source, updated_at
FROM sdd_context.materials
WHERE MATCH(name, content) AGAINST('search' IN NATURAL LANGUAGE MODE)
  AND kind IN ('spec','code','test') LIMIT 8;
-- 以及 'keyword' / 'case' / 'insensitive' / 'filter' / 'database'
```

检索结果直接构成上下文输出（实测摘录）：

```
## 适用的规格条目（来自 seekdb 检索）
- 来源 spec.md（issue-tracker-spec.md）：**S4** 关键词搜索：…匹配对大小写不敏感…
- 来源 spec.md（issue-tracker-spec.md）：**C1**：filter_by_status 行为不得改变…
## 需要修改的代码（来自 seekdb 检索）
- 来源 project/tracker.py（tracker.py）
## 验证测试（来自 seekdb 检索）
- 来源 project/tests/test_feature.py（test_feature.py）
- 来源 project/tests/test_regression.py（test_regression.py）
```

## 规格更新后检索内容随之变化（实测）

把 `spec.md` 的 S4 改为"大小写敏感"并新增 S7 后重新 `import`，再次
`context` 的输出变为：

```
- …：**S4** 关键词搜索：…匹配对大小写敏感…
- …：**S7**：搜索词长度上限 64 字符。
```

证明模块使用数据库中的最新内容，不返回固定答案。随后已恢复 v1 规格并重新导入。
