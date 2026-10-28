"""IssueTracker: 任务看板的最小业务工程（SDD 验证样例的目标工程）。

本次开发任务：实现 S4 的大小写不敏感关键词搜索。
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
        # S4：大小写不敏感匹配
        needle = keyword.casefold()
        return [issue for issue in self._issues if needle in issue.title.casefold()]
