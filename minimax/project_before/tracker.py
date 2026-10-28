"""IssueTracker: 任务看板的最小业务工程（修改前快照）。

本快照中 S4（大小写不敏感搜索）尚未实现：
`search` 为大小写敏感的朴素子串匹配，目标功能测试将因此失败。
"""

from __future__ import annotations


class Issue:
    def __init__(self, title: str, status: str, tags: list[str] | None = None):
        self.title = title.strip()
        self.status = status
        self.tags = tags or []

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return f"Issue(title={self.title!r}, status={self.status!r}, tags={self.tags!r})"


class IssueTracker:
    def __init__(self):
        self._issues: list[Issue] = []

    def add(self, title: str, status: str = "open", tags: list[str] | None = None) -> Issue:
        issue = Issue(title, status, tags)
        self._issues.append(issue)
        return issue

    def list_issues(self) -> list[Issue]:
        return list(self._issues)

    def filter_by_status(self, status: str) -> list[Issue]:
        return [issue for issue in self._issues if issue.status == status]

    def search(self, keyword: str | None) -> list[Issue]:
        if not keyword:
            return []
        # 修改前：大小写敏感（S4 未实现）
        return [issue for issue in self._issues if keyword in issue.title]
