# IssueTracker 功能规格（SDD 示例业务工程）

> 本文档是上下文管理系统的"规格资料"来源之一。条款带编号，供上下文模块定位引用。

## 数据模型

- **S1**：每个 issue 有 `title`（非空字符串）、`status`（open / in_progress / closed 之一）、`tags`（字符串列表，可为空）。
- **S2**：`add(title, status, tags)` 新增 issue，title 去除首尾空白后存储。

## 行为要求

- **S3** 状态过滤：`filter_by_status(status)` 返回该状态下全部 issue，按添加顺序排列；未知状态返回空列表。
- **S4** 关键词搜索：`search(keyword)` 返回标题包含 keyword 的全部 issue；**匹配对大小写不敏感**——例如搜索 `Database` 必须命中标题 `Fix database connection pool`。
- **S5** 搜索空关键词返回空列表；`search(None)` 等价于空关键词。
- **S6** 搜索不改变任何 issue 的状态与顺序；同一 issue 的 title 不可变。

## 兼容性约束（修改时必须保持）

- **C1**：`filter_by_status` 行为不得改变（见 S3），已有回归测试必须全部通过。
- **C2**：对外函数签名 `add / list_issues / filter_by_status / search` 不得改变。
