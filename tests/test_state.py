"""State mapping and aggregate rules, checked against upstream SidePulse.

Upstream's `status_bar.py` cannot be imported on Linux (it needs PyObjC), so the
four-state mapping is read from its source: `state_for_mode` and the
`STATE_*` constants are extracted with `ast` and executed against upstream's
own `AgentMode`. Any drift upstream fails these tests.
"""

from __future__ import annotations

import ast
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


def _read(daemon, statuses) -> dict:
    """The state event for these statuses, as the service holds them and the helper reads them."""
    daemon.store.rows = tuple(statuses)
    return helper.state_event(helper.read_snapshot())


def test_mixed_sessions_show_the_most_urgent_state_and_count_active_ones(daemon):
    now = datetime.now(timezone.utc)
    statuses = (
        _status("a", AgentMode.WORKING, now - timedelta(seconds=5), event="UserPromptSubmit"),
        _status("b", AgentMode.WAITING_FOR_INPUT, now - timedelta(seconds=30)),
        _status("c", AgentMode.COMPLETED, now - timedelta(seconds=10)),
        _status("d", AgentMode.IDLE_READY, now - timedelta(seconds=1)),
    )
    event = _read(daemon, statuses)
    assert event["state"] == "ask"
    assert event["label"] == "Ask"
    assert event["colour"] == "#FF8A00"
    assert event["urgent"] is True
    # Waiting and working count; completed and idle sessions do not.
    assert event["active_count"] == 2
    assert event["top"]["agent_id"] == "b"


def test_only_completed_sessions_show_done_and_are_not_active(daemon):
    now = datetime.now(timezone.utc)
    event = _read(daemon, (_status("c", AgentMode.COMPLETED, now - timedelta(seconds=10)),))
    assert (event["state"], event["label"], event["active_count"], event["urgent"]) == ("done", "Done", 0, False)


def test_no_sessions_is_idle_with_no_top_session(daemon):
    event = _read(daemon, ())
    assert (event["state"], event["label"], event["colour"], event["top"]) == ("idle", "Idle", "#A8A8A8", None)


def test_a_long_finished_session_is_dropped_by_the_service(daemon):
    # The service owns the rules since 20ca7fd (ProgramMonitor): a session completed 20 min ago is gone.
    old = _status("old", AgentMode.COMPLETED, datetime.now(timezone.utc) - timedelta(minutes=30))
    assert _read(daemon, (old,))["state"] == "idle"


def test_a_stopped_service_is_a_reason_not_an_empty_state():
    # No daemon runs in this test's scratch state folder.
    with pytest.raises(ValueError, match="service is not running"):
        helper.read_snapshot()


def test_an_error_reply_is_a_reason(daemon, monkeypatch):
    import sidepulse.program_transport as transport

    monkeypatch.setattr(transport, "submit_report", lambda *a, **k: {"ok": False, "error": "Daemon is busy"})
    with pytest.raises(ValueError, match="sent no snapshot"):
        helper.read_snapshot()


def test_agent_status_from_dict_is_upstream_parsing():
    # The helper relies on upstream to parse statuses; a bad row is dropped, not fatal.
    assert agent_status_from_dict({"provider": "claude"}) is None
