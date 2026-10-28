"""目标功能测试：S4 大小写不敏感关键词搜索。

修改前：test_search_is_case_insensitive 与 test_search_mixed_case 失败（功能未实现）。
修改后：全部通过。
"""

import pytest

from tracker import IssueTracker


def build_tracker() -> IssueTracker:
    tracker = IssueTracker()
    tracker.add("Fix database connection pool", "open", ["backend"])
    tracker.add("Database backup strategy", "closed", ["ops"])
    tracker.add("Refactor auth module", "in_progress", ["security"])
    return tracker


def test_search_is_case_insensitive():
    tracker = build_tracker()
    hits = tracker.search("Database")
    assert [issue.title for issue in hits] == [
        "Fix database connection pool",
        "Database backup strategy",
    ]


def test_search_mixed_case_keyword():
    tracker = build_tracker()
    hits = tracker.search("dAtAbAsE")
    assert len(hits) == 2


def test_search_empty_keyword_returns_empty():
    tracker = build_tracker()
    assert tracker.search("") == []
    assert tracker.search(None) == []


def test_search_does_not_mutate():
    tracker = build_tracker()
    before = [(issue.title, issue.status, issue.tags) for issue in tracker.list_issues()]
    tracker.search("database")
    after = [(issue.title, issue.status, issue.tags) for issue in tracker.list_issues()]
    assert before == after
