"""The helper as the widget runs it: NDJSON on stdout, commands on stdin."""

from __future__ import annotations

import json
import os
import select
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

HELPER = Path(__file__).resolve().parent.parent / "helper" / "sidepulse_omarchy.py"
TIMEOUT = 15.0


class Helper:
    """Runs the helper with a private XDG_STATE_HOME and reads its events."""

    def __init__(self, state_home: Path) -> None:
        self.state_home = state_home
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

    def write_latest(self, statuses: list[dict]) -> None:
        directory = self.state_home / "sidepulse" / "agent-monitor"
        directory.mkdir(parents=True, exist_ok=True)
        tmp = directory / "latest.json.tmp"
        tmp.write_text(json.dumps({"updated_at": datetime.now(timezone.utc).isoformat(), "statuses": statuses}))
        os.replace(tmp, directory / "latest.json")  # tmp + rename, as the service writes it

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


def test_state_follows_latest_json_when_it_changes(helper_proc):
    helper_proc.event_matching(lambda e: e["type"] == "state" and e["state"] == "idle")
    helper_proc.write_latest([status_row("waiting_for_input")])
    event = helper_proc.event_matching(lambda e: e["type"] == "state" and e["state"] == "ask")
    assert event["label"] == "Ask"
    assert event["urgent"] is True
    assert event["top"]["display_name"] == "demo project"
    helper_proc.write_latest([status_row("working")])
    event = helper_proc.event_matching(lambda e: e["type"] == "state" and e["state"] == "working")
    assert event["active_count"] == 1


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


def test_unreadable_latest_json_replaces_the_last_state_with_unavailable(helper_proc):
    helper_proc.write_latest([status_row("working")])
    helper_proc.event_matching(lambda e: e["type"] == "state" and e["state"] == "working")
    (helper_proc.state_home / "sidepulse" / "agent-monitor" / "latest.json").write_text("{not json")
    event = helper_proc.event_matching(lambda e: e["type"] == "state" and e["state"] == "unavailable")
    assert event["urgent"] is False
    assert event["active_count"] == 0
    assert event["top"] is None
    assert "latest.json" in event["reason"]


def test_state_error_clears_when_latest_json_is_readable_again(helper_proc):
    helper_proc.event_matching(lambda e: e["type"] == "state" and e["state"] == "idle")
    latest = helper_proc.state_home / "sidepulse" / "agent-monitor" / "latest.json"
    latest.parent.mkdir(parents=True, exist_ok=True)
    latest.write_text("{not json")
    helper_proc.event_matching(lambda e: e["type"] == "error" and e["feature"] == "state")
    helper_proc.write_latest([status_row("working")])
    helper_proc.event_matching(lambda e: e["type"] == "clear" and e["feature"] == "state")
    helper_proc.event_matching(lambda e: e["type"] == "state" and e["state"] == "working")


def test_a_problem_that_comes_back_is_reported_again(helper_proc):
    helper_proc.event_matching(lambda e: e["type"] == "state" and e["state"] == "idle")
    latest = helper_proc.state_home / "sidepulse" / "agent-monitor" / "latest.json"
    latest.parent.mkdir(parents=True, exist_ok=True)
    for _ in range(2):
        latest.write_text("{not json")
        helper_proc.event_matching(lambda e: e["type"] == "error" and e["feature"] == "state")
        helper_proc.write_latest([])
        helper_proc.event_matching(lambda e: e["type"] == "clear" and e["feature"] == "state")
