"""回归测试：S3 状态过滤等既有行为，修改前后都必须通过（兼容性约束 C1/C2）。"""

import pytest

from tracker import Issue, IssueTracker


def test_filter_by_status_basic():
    tracker = IssueTracker()
    tracker.add("A", "open")
    tracker.add("B", "closed")
    tracker.add("C", "open")
    assert [i.title for i in tracker.filter_by_status("open")] == ["A", "C"]


def test_filter_by_status_unknown_returns_empty():
    tracker = IssueTracker()
    tracker.add("A", "open")
    assert tracker.filter_by_status("archived") == []


def test_add_trims_title():
    tracker = IssueTracker()
    issue = tracker.add("  spaced title  ")
    assert issue.title == "spaced title"


def test_list_issues_preserves_order():
    tracker = IssueTracker()
    tracker.add("first", "open")
    tracker.add("second", "closed")
    assert [i.title for i in tracker.list_issues()] == ["first", "second"]


def test_public_signatures_exist():
    assert callable(IssueTracker.add)
    assert callable(IssueTracker.list_issues)
    assert callable(IssueTracker.filter_by_status)
    assert callable(IssueTracker.search)
