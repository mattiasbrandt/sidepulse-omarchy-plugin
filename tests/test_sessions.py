"""The popup's session list (D13): upstream's recent-session rules and the sessions event.

Upstream's status_bar.py cannot be imported on Linux (it needs PyObjC), so its
recent-session functions are read from its source and run here against the
same AgentStatus type. The helper's copy must give the same answer on every
fixture; upstream drift fails these tests.
"""

from __future__ import annotations

import ast
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from sidepulse.models import AgentMode, AgentStatus
from sidepulse.settings import DEFAULT_RECENT_SESSION_RETENTION_SECONDS

import sidepulse_omarchy as helper

NOW = datetime(2026, 10, 11, 12, 0, 0, tzinfo=timezone.utc)
UPSTREAM_FUNCTIONS = {
    "recent_statuses",
    "menu_statuses",
    "coalesced_menu_statuses",
    "coalesce_statuses_by_key",
    "menu_session_coalesce_key",
    "menu_status_sort_key",
}


def _upstream_recent_statuses():
    import sidepulse

    source = (Path(sidepulse.__file__).parent / "status_bar.py").read_text()
    tree = ast.parse(source)
    namespace: dict = {
        "AgentMode": AgentMode,
        "DEFAULT_RECENT_SESSION_RETENTION_SECONDS": DEFAULT_RECENT_SESSION_RETENTION_SECONDS,
    }
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "STATUS_BAR_SESSION_HISTORY_LIMIT" for t in node.targets
        ):
            exec(compile(ast.Module(body=[node], type_ignores=[]), "status_bar.py", "exec"), namespace)
        elif isinstance(node, ast.FunctionDef) and node.name in UPSTREAM_FUNCTIONS:
            exec(compile(ast.Module(body=[node], type_ignores=[]), "status_bar.py", "exec"), namespace)
    assert UPSTREAM_FUNCTIONS <= set(namespace)
    return namespace["recent_statuses"], namespace["STATUS_BAR_SESSION_HISTORY_LIMIT"]


def _status(agent_id: str, mode: AgentMode, age_seconds: float, *, session_id: str | None,
            provider: str = "claude", cwd: str = "/work/project", message: str | None = None) -> AgentStatus:
    return AgentStatus(
        provider=provider,
        agent_id=agent_id,
        display_name=agent_id,
        mode=mode,
        updated_at=NOW - timedelta(seconds=age_seconds),
        event_name="Stop",
        session_id=session_id,
        cwd=cwd,
        message=message,
    )


def _snapshot(statuses, stale_statuses=()) -> SimpleNamespace:
    return SimpleNamespace(statuses=tuple(statuses), stale_statuses=tuple(stale_statuses), collected_at=NOW)


def _fixture() -> SimpleNamespace:
    fresh = [
        _status("claude:s1", AgentMode.WORKING, 10, session_id="s1", message="running tests"),
        # The same session again, less urgent: the session shows once, as its most urgent status.
        _status("claude:s1:b", AgentMode.IDLE_READY, 5, session_id="s1"),
        _status("codex:s2", AgentMode.WAITING_FOR_INPUT, 60, session_id="s2", provider="codex"),
        # A subagent of s3 is coalesced into its session: the session shows once, as its Working subagent.
        _status("claude:s3:agent:x", AgentMode.WORKING, 20, session_id="s3"),
        _status("claude:s3", AgentMode.COMPLETED, 20, session_id="s3"),
        _status("no-session", AgentMode.IDLE_READY, 30, session_id=None),
    ]
    stale = [
        _status("claude:s4", AgentMode.COMPLETED, 3600, session_id="s4"),
        _status("claude:s5", AgentMode.COMPLETED, 3 * 86400, session_id="s5"),
        _status("claude:s6", AgentMode.WORKING, 3600, session_id="s6"),
    ]
    for index in range(7, 12):
        fresh.append(_status(f"claude:s{index}", AgentMode.IDLE_READY, 100 + index, session_id=f"s{index}"))
    return _snapshot(fresh, stale)


def _ids(statuses) -> list[str]:
    return [status.agent_id for status in statuses]


@pytest.mark.parametrize("retention", [3600.0, DEFAULT_RECENT_SESSION_RETENTION_SECONDS])
def test_helper_matches_upstream_recent_statuses(retention):
    upstream, limit = _upstream_recent_statuses()
    snapshot = _fixture()
    expected = upstream(snapshot, SimpleNamespace(recent_session_retention_seconds=retention))
    assert helper.recent_statuses(snapshot, retention) == expected
    assert helper.SESSION_LIMIT == limit


def test_list_is_capped_at_ten_sessions():
    assert len(helper.recent_statuses(_fixture(), DEFAULT_RECENT_SESSION_RETENTION_SECONDS)) == 10


def test_completed_sessions_past_retention_are_dropped():
    listed = _ids(helper.recent_statuses(_fixture(), 7200.0))
    assert "claude:s4" in listed
    assert "claude:s5" not in listed


def test_stale_working_sessions_are_never_listed():
    assert "claude:s6" not in _ids(helper.recent_statuses(_fixture(), DEFAULT_RECENT_SESSION_RETENTION_SECONDS))


def test_one_session_shows_once_as_its_most_urgent_status():
    listed = helper.recent_statuses(_fixture(), DEFAULT_RECENT_SESSION_RETENTION_SECONDS)
    s1 = [status for status in listed if status.session_id == "s1"]
    assert [status.mode for status in s1] == [AgentMode.WORKING]


def test_sessions_event_carries_the_row_fields_the_popup_draws():
    status = _status("claude:s1", AgentMode.WORKING, 10, session_id="s1", message="running tests")
    event = helper.sessions_event([status])
    assert event["type"] == "sessions"
    [item] = event["items"]
    assert item["provider"] == "claude"
    assert item["session_id"] == "s1"
    assert item["project"] == "project"
    assert item["state"] == "working"
    assert item["label"] == "Working"
    assert item["colour"] == helper.STATE_COLOURS["working"]
    assert item["message"] == "running tests"
    assert item["updated_ms"] == int(status.updated_at.timestamp() * 1000)
    assert item["resumable"] is True


def test_session_without_id_is_listed_but_not_resumable():
    [item] = helper.sessions_event([_status("no-session", AgentMode.IDLE_READY, 5, session_id=None)])["items"]
    assert item["resumable"] is False
    assert item["session_id"] is None


def test_unavailable_state_lists_no_sessions():
    assert helper.sessions_event([]) == {"type": "sessions", "items": []}
