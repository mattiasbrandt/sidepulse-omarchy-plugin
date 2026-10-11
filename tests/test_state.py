"""State mapping and aggregate rules, checked against upstream SidePulse.

Upstream's `status_bar.py` cannot be imported on Linux (it needs PyObjC), so the
four-state mapping is read from its source: `state_for_mode` and the
`STATE_*` constants are extracted with `ast` and executed against upstream's
own `AgentMode`. Any drift upstream fails these tests.
"""

from __future__ import annotations

import ast
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sidepulse.models import AgentMode, AgentStatus
from sidepulse.collector import agent_status_from_dict

import sidepulse_omarchy as helper

def _upstream_status_bar_path() -> Path:
    import sidepulse

    return Path(sidepulse.__file__).parent / "status_bar.py"


def _upstream_state_for_mode():
    source = _upstream_status_bar_path().read_text()
    tree = ast.parse(source)
    namespace: dict = {"AgentMode": AgentMode}

    class StatusBarState:
        def __init__(self, label, symbol, priority):
            self.label = label

    namespace["StatusBarState"] = StatusBarState
    wanted = {"state_for_mode"}
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id.startswith("STATE_") for t in node.targets
        ):
            module = ast.Module(body=[node], type_ignores=[])
            exec(compile(module, "status_bar.py", "exec"), namespace)
        elif isinstance(node, ast.FunctionDef) and node.name in wanted:
            module = ast.Module(body=[node], type_ignores=[])
            exec(compile(module, "status_bar.py", "exec"), namespace)
    return namespace["state_for_mode"]


def _status(agent_id: str, mode: AgentMode, updated_at: datetime, *, event: str = "Stop") -> AgentStatus:
    return AgentStatus(
        provider="claude",
        agent_id=agent_id,
        display_name=agent_id,
        mode=mode,
        updated_at=updated_at,
        event_name=event,
        session_id=agent_id,
        cwd="/tmp/project",
    )


@pytest.mark.parametrize("mode", list(AgentMode), ids=lambda m: m.value)
def test_every_upstream_mode_maps_to_the_upstream_state_label(mode):
    upstream = _upstream_state_for_mode()
    expected = upstream(mode).label
    assert helper.STATE_LABELS[helper.state_for_mode(mode.value)] == expected


def test_upstream_state_colours_are_the_ones_the_helper_uses():
    source = _upstream_status_bar_path().read_text()
    for state, colour in helper.STATE_COLOURS.items():
        assert f'"{colour}"' in source, f"{state} colour {colour} no longer appears in upstream status_bar.py"


def test_mode_mapping_covers_every_upstream_mode():
    assert {m.value for m in AgentMode} <= set(helper.MODE_STATES)


def test_unknown_mode_is_idle():
    assert helper.state_for_mode("not-a-mode") == "idle"


def test_mixed_sessions_show_the_most_urgent_state_and_count_active_ones():
    now = datetime(2026, 10, 11, 12, 0, tzinfo=timezone.utc)
    statuses = (
        _status("a", AgentMode.WORKING, now - timedelta(seconds=5), event="UserPromptSubmit"),
        _status("b", AgentMode.WAITING_FOR_INPUT, now - timedelta(seconds=30)),
        _status("c", AgentMode.COMPLETED, now - timedelta(seconds=10)),
        _status("d", AgentMode.IDLE_READY, now - timedelta(seconds=1)),
    )
    event = helper.state_event(helper.snapshot_from_statuses(statuses, now=now, idle_timeout_seconds=3600.0))
    assert event["state"] == "ask"
    assert event["label"] == "Ask"
    assert event["colour"] == "#FF8A00"
    assert event["urgent"] is True
    # Waiting and working count; completed and idle sessions do not.
    assert event["active_count"] == 2
    assert event["top"]["agent_id"] == "b"


def test_only_completed_sessions_show_done_and_are_not_active():
    now = datetime(2026, 10, 11, 12, 0, tzinfo=timezone.utc)
    statuses = (_status("c", AgentMode.COMPLETED, now - timedelta(seconds=10)),)
    event = helper.state_event(helper.snapshot_from_statuses(statuses, now=now, idle_timeout_seconds=3600.0))
    assert (event["state"], event["label"], event["active_count"], event["urgent"]) == ("done", "Done", 0, False)


def test_no_sessions_is_idle_with_no_top_session():
    now = datetime(2026, 10, 11, 12, 0, tzinfo=timezone.utc)
    event = helper.state_event(helper.snapshot_from_statuses((), now=now, idle_timeout_seconds=3600.0))
    assert (event["state"], event["label"], event["colour"], event["top"]) == ("idle", "Idle", "#A8A8A8", None)


def test_status_older_than_idle_timeout_is_stale_and_not_shown_as_working():
    now = datetime(2026, 10, 11, 12, 0, tzinfo=timezone.utc)
    statuses = (_status("old", AgentMode.WORKING, now - timedelta(hours=2), event="UserPromptSubmit"),)
    event = helper.state_event(helper.snapshot_from_statuses(statuses, now=now, idle_timeout_seconds=3600.0))
    assert event["state"] == "idle"
    assert event["active_count"] == 0
    assert event["stale_count"] == 1


def test_read_latest_json_uses_upstream_rules(tmp_path):
    now = datetime(2026, 10, 11, 12, 0, tzinfo=timezone.utc)
    latest = tmp_path / "latest.json"
    recent = _status("live", AgentMode.TOOL_RUNNING, now - timedelta(seconds=2), event="PreToolUse")
    latest.write_text(json.dumps({"updated_at": now.isoformat(), "statuses": [recent.to_dict(now)]}))
    snapshot = helper.read_snapshot(latest, now=now, idle_timeout_seconds=3600.0)
    assert snapshot is not None
    assert helper.state_event(snapshot)["label"] == "Working"


def test_missing_latest_json_is_an_empty_snapshot(tmp_path):
    now = datetime(2026, 10, 11, 12, 0, tzinfo=timezone.utc)
    snapshot = helper.read_snapshot(tmp_path / "latest.json", now=now, idle_timeout_seconds=3600.0)
    assert helper.state_event(snapshot)["state"] == "idle"


def test_malformed_latest_json_raises_so_the_caller_can_report_it(tmp_path):
    latest = tmp_path / "latest.json"
    latest.write_text("{not json")
    with pytest.raises(ValueError):
        helper.read_snapshot(latest, now=datetime.now(timezone.utc), idle_timeout_seconds=3600.0)


def test_agent_status_from_dict_is_upstream_parsing():
    # The helper relies on upstream to parse statuses; a bad row is dropped, not fatal.
    assert agent_status_from_dict({"provider": "claude"}) is None
