"""The helper as the widget runs it: NDJSON on stdout, commands on stdin."""

from __future__ import annotations

import json
import os
import select
import signal
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from daemon import Daemon

HELPER = Path(__file__).resolve().parent.parent / "helper" / "sidepulse_omarchy.py"
TIMEOUT = 15.0


class Helper:
    """Runs the helper with a private XDG_STATE_HOME and a SidePulse daemon there, and reads its events."""

    def __init__(self, state_home: Path) -> None:
        self.state_home = state_home
        self.daemon: Daemon | None = None
        self.start_daemon()
        env = {**os.environ, "XDG_STATE_HOME": str(state_home)}
        self.proc = subprocess.Popen(
            [sys.executable, "-I", str(HELPER)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            env=env,
        )
        self.buffer = b""

    def read_event(self, timeout: float = TIMEOUT) -> dict:
        deadline = time.monotonic() + timeout
        while b"\n" not in self.buffer:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("no event from the helper")
            ready, _, _ = select.select([self.proc.stdout], [], [], remaining)
            if not ready:
                continue
            chunk = os.read(self.proc.stdout.fileno(), 65536)
            if not chunk:
                raise EOFError("helper closed stdout")
            self.buffer += chunk
        line, self.buffer = self.buffer.split(b"\n", 1)
        return json.loads(line)

    def event_matching(self, predicate, timeout: float = TIMEOUT) -> dict:
        deadline = time.monotonic() + timeout
        while True:
            event = self.read_event(max(0.1, deadline - time.monotonic()))
            if predicate(event):
                return event

    def send(self, obj: dict) -> None:
        self.proc.stdin.write((json.dumps(obj) + "\n").encode())
        self.proc.stdin.flush()

    def start_daemon(self) -> None:
        self.daemon = Daemon(self.state_home / "sidepulse" / "agent-monitor")

    def stop_daemon(self) -> None:
        """The service stops: its socket goes away, as when `sidepulse service stop` ends it."""
        self.daemon.stop()
        self.daemon = None

    def set_statuses(self, statuses: list[dict]) -> None:
        if self.daemon is None:
            self.start_daemon()
        self.daemon.set(statuses)

    def close_stdin(self) -> int:
        self.proc.stdin.close()
        return self.proc.wait(timeout=TIMEOUT)


@pytest.fixture
def helper_proc(tmp_path):
    proc = Helper(tmp_path)
    yield proc
    if proc.proc.poll() is None:
        proc.proc.kill()
        proc.proc.wait()
    if proc.daemon is not None:
        proc.daemon.stop()


def status_row(mode: str, age_seconds: float = 1.0) -> dict:
    when = datetime.now(timezone.utc) - timedelta(seconds=age_seconds)
    return {
        "provider": "claude",
        "agent_id": "claude:session:abc",
        "display_name": "demo project",
        "mode": mode,
        "mode_label": mode,
        "updated_at": when.isoformat(),
        "event_name": "PreToolUse",
        "session_id": "abc",
        "cwd": "/tmp/demo",
        "stale": False,
    }


def test_hello_reports_protocol_and_capabilities(helper_proc):
    hello = helper_proc.read_event()
    assert hello["type"] == "hello"
    assert hello["protocol"] == 1
    assert hello["capabilities"]["state"]["ok"] is True
    assert "upstream" in hello


def test_first_state_and_setup_follow_hello(helper_proc):
    helper_proc.event_matching(lambda e: e["type"] == "hello")
    state = helper_proc.event_matching(lambda e: e["type"] == "state")
    assert state["state"] == "idle"
    setup = helper_proc.event_matching(lambda e: e["type"] == "setup")
    assert isinstance(setup["items"], list)


def test_exits_cleanly_when_stdin_closes(helper_proc):
    helper_proc.event_matching(lambda e: e["type"] == "state")
    assert helper_proc.close_stdin() == 0


def test_exits_cleanly_on_sigterm(helper_proc):
    # Quickshell stops a Process with SIGTERM: the helper must leave through its cleanup (handing devices
    # back, dropping the sleep lock), not die mid-loop with the default action (exit status -15).
    helper_proc.event_matching(lambda e: e["type"] == "state")
    helper_proc.proc.send_signal(signal.SIGTERM)
    assert helper_proc.proc.wait(timeout=TIMEOUT) == 0


def test_state_follows_the_service_when_it_changes(helper_proc):
    helper_proc.event_matching(lambda e: e["type"] == "state" and e["state"] == "idle")
    helper_proc.set_statuses([status_row("waiting_for_input")])
    event = helper_proc.event_matching(lambda e: e["type"] == "state" and e["state"] == "ask")
    assert event["label"] == "Ask"
    assert event["urgent"] is True
    assert event["top"]["display_name"] == "demo project"
    helper_proc.set_statuses([status_row("working")])
    event = helper_proc.event_matching(lambda e: e["type"] == "state" and e["state"] == "working")
    assert event["active_count"] == 1


def test_sessions_list_the_statuses_the_service_holds(helper_proc):
    helper_proc.event_matching(lambda e: e["type"] == "sessions")
    helper_proc.set_statuses([status_row("working")])
    event = helper_proc.event_matching(lambda e: e["type"] == "sessions" and e["items"])
    [item] = event["items"]
    assert (item["session_id"], item["project"], item["state"]) == ("abc", "demo", "working")
    assert item["resumable"] is True


def test_a_stopped_service_lists_no_sessions(helper_proc):
    helper_proc.set_statuses([status_row("working")])
    helper_proc.event_matching(lambda e: e["type"] == "sessions" and e["items"])
    helper_proc.stop_daemon()
    event = helper_proc.event_matching(lambda e: e["type"] == "sessions" and not e["items"])
    assert event == {"type": "sessions", "items": []}


def test_open_session_for_an_unlisted_session_answers_with_a_failed_result(helper_proc):
    helper_proc.event_matching(lambda e: e["type"] == "sessions")
    helper_proc.send({"cmd": "open-session", "provider": "claude", "session_id": "not-listed"})
    event = helper_proc.event_matching(lambda e: e["type"] == "result")
    assert (event["command"], event["ok"]) == ("open-session", False)
    assert "no longer listed" in event["message"]


def test_open_session_without_a_session_id_is_reported_as_an_error_event(helper_proc):
    helper_proc.event_matching(lambda e: e["type"] == "sessions")
    helper_proc.send({"cmd": "open-session", "provider": "claude"})
    event = helper_proc.event_matching(lambda e: e["type"] == "error")
    assert event["feature"] == "command"
    assert "session_id" in event["message"]


def test_unknown_command_is_reported_as_an_error_event(helper_proc):
    helper_proc.event_matching(lambda e: e["type"] == "state")
    helper_proc.send({"cmd": "no-such-command"})
    event = helper_proc.event_matching(lambda e: e["type"] == "error")
    assert event["feature"] == "command"
    assert "no-such-command" in event["message"]


def test_refresh_command_answers_with_a_fresh_state(helper_proc):
    helper_proc.event_matching(lambda e: e["type"] == "state")
    helper_proc.send({"cmd": "refresh"})
    helper_proc.event_matching(lambda e: e["type"] == "state")


def test_a_stopped_service_replaces_the_last_state_with_unavailable(helper_proc):
    # 20ca7fd's service holds the status in memory: when it stops there is no state, not the last one.
    helper_proc.set_statuses([status_row("working")])
    helper_proc.event_matching(lambda e: e["type"] == "state" and e["state"] == "working")
    helper_proc.stop_daemon()
    event = helper_proc.event_matching(lambda e: e["type"] == "state" and e["state"] == "unavailable")
    assert event["urgent"] is False
    assert event["active_count"] == 0
    assert event["top"] is None
    assert "service is not running" in event["reason"]


def test_state_error_clears_when_the_service_is_back(helper_proc):
    helper_proc.event_matching(lambda e: e["type"] == "state" and e["state"] == "idle")
    helper_proc.stop_daemon()
    helper_proc.event_matching(lambda e: e["type"] == "error" and e["feature"] == "state")
    helper_proc.set_statuses([status_row("working")])
    helper_proc.event_matching(lambda e: e["type"] == "clear" and e["feature"] == "state")
    helper_proc.event_matching(lambda e: e["type"] == "state" and e["state"] == "working")


def test_a_problem_that_comes_back_is_reported_again(helper_proc):
    helper_proc.event_matching(lambda e: e["type"] == "state" and e["state"] == "idle")
    for _ in range(2):
        helper_proc.stop_daemon()
        helper_proc.event_matching(lambda e: e["type"] == "error" and e["feature"] == "state")
        helper_proc.set_statuses([])
        helper_proc.event_matching(lambda e: e["type"] == "clear" and e["feature"] == "state")
