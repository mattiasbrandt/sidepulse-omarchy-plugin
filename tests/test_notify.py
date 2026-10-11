"""Notify when a session asks (D20): opt-in, once per entry into Ask, click opens the session.

The helper runs libnotify's notify-send with a "default" action and reads its
stdout: the action's name is printed when the user clicks. A fake notify-send
on a scratch PATH stands in for the desktop, so the pipe handling is real.
"""

from __future__ import annotations

import io
import json
import os
import stat
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

import sidepulse_omarchy as helper


def status(session_id: str, mode: str, *, provider: str = "claude", cwd: str = "/tmp/project",
           message: str = "Proceed?"):
    """A stand-in with the attributes the helper reads from upstream's AgentStatus."""
    return type("Status", (), {
        "provider": provider,
        "session_id": session_id,
        "agent_id": f"{provider}:{session_id}",
        "display_name": "Claude",
        "mode": type("Mode", (), {"value": mode})(),
        "cwd": cwd,
        "message": message,
        "updated_at": datetime.now(timezone.utc),
    })()


def test_only_asking_statuses_with_a_session_id_are_asks():
    statuses = [
        status("a", "waiting_for_input"),
        status("b", "working"),
        status("", "waiting_for_input"),
    ]
    asks = helper.asking_sessions(statuses)
    assert list(asks) == [("claude", "a")]


def test_notify_send_gets_an_action_named_default_and_waits_for_the_click():
    argv = helper.notify_argv("Claude Code needs attention", "project: Proceed?")
    assert argv[0] == "notify-send"
    assert "--wait" in argv
    assert f"--action={helper.NOTIFY_ACTION}=Open session" in argv
    assert argv[-2:] == ["Claude Code needs attention", "project: Proceed?"]


def test_notification_text_names_the_agent_and_the_project_and_flattens_the_message():
    summary, body = helper.notification_text(status("a", "waiting_for_input", message="line one\n\n  line two"))
    assert summary == "Claude Code needs attention"
    assert body == "project: line one line two"


def test_notification_body_is_cut_to_a_short_line():
    summary, body = helper.notification_text(status("a", "waiting_for_input", message="x" * 500))
    assert len(body) <= 200
    assert body.endswith("...")


def test_an_asking_session_notifies_once_when_it_enters_ask(monkeypatch):
    sent: list[tuple[str, str]] = []
    h = helper.Helper(io.StringIO())
    h.capabilities = {"notify": {"ok": True, "reason": ""}}
    h.notify_on_ask = True
    monkeypatch.setattr(h, "notify", lambda s: sent.append((s.session_id, s.mode.value)))

    h.note_asks(helper.asking_sessions([status("a", "working")]))  # first read: seeds, no notice
    assert sent == []
    h.note_asks(helper.asking_sessions([status("a", "waiting_for_input")]))
    h.note_asks(helper.asking_sessions([status("a", "waiting_for_input")]))  # still asking: no repeat
    assert sent == [("a", "waiting_for_input")]
    h.note_asks(helper.asking_sessions([status("a", "working")]))
    h.note_asks(helper.asking_sessions([status("a", "waiting_for_input")]))  # asks again
    assert len(sent) == 2


def test_sessions_already_asking_when_the_helper_starts_do_not_notify(monkeypatch):
    sent: list[str] = []
    h = helper.Helper(io.StringIO())
    h.capabilities = {"notify": {"ok": True, "reason": ""}}
    h.notify_on_ask = True
    monkeypatch.setattr(h, "notify", lambda s: sent.append(s.session_id))
    h.note_asks(helper.asking_sessions([status("a", "waiting_for_input")]))
    assert sent == []


def test_nothing_notifies_while_the_switch_is_off(monkeypatch):
    sent: list[str] = []
    h = helper.Helper(io.StringIO())
    h.capabilities = {"notify": {"ok": True, "reason": ""}}
    monkeypatch.setattr(h, "notify", lambda s: sent.append(s.session_id))
    h.note_asks(helper.asking_sessions([status("a", "working")]))
    h.note_asks(helper.asking_sessions([status("a", "waiting_for_input")]))
    assert sent == []


def test_set_notify_switch_is_stored_and_refuses_non_booleans():
    out = io.StringIO()
    h = helper.Helper(out)
    h.hello()
    h.handle_line(b'{"cmd": "set-notify", "enabled": true}')
    assert h.notify_on_ask is True
    h.handle_line(b'{"cmd": "set-notify", "enabled": "on"}')
    assert h.notify_on_ask is True
    errors = [json.loads(line) for line in out.getvalue().splitlines() if '"error"' in line]
    assert errors and errors[-1]["feature"] == "command"


@pytest.fixture
def fake_notify_send(tmp_path, monkeypatch):
    """A notify-send that prints the clicked action's name, then exits, like the real one on a click."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    script = bin_dir / "notify-send"
    script.write_text(
        "#!/bin/sh\n"
        "echo \"$@\" >> \"$FAKE_NOTIFY_LOG\"\n"
        "if [ \"$FAKE_NOTIFY_CLICK\" = 1 ]; then echo default; fi\n"
        "exit \"${FAKE_NOTIFY_EXIT:-0}\"\n"
    )
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("FAKE_NOTIFY_LOG", str(tmp_path / "notify.log"))
    monkeypatch.setenv("FAKE_NOTIFY_CLICK", "0")
    return tmp_path


def pump_until(h: helper.Helper, predicate, timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        h.pump_notifications(0.1)
        if predicate():
            return
    raise AssertionError("notification pump did not finish in time")


def test_a_click_on_the_notification_opens_its_session(fake_notify_send, monkeypatch):
    monkeypatch.setenv("FAKE_NOTIFY_CLICK", "1")
    out = io.StringIO()
    h = helper.Helper(out)
    h.hello()
    h.session_statuses = [status("a", "waiting_for_input")]
    opened: list[dict] = []
    monkeypatch.setattr(h, "open_session", lambda command: opened.append(command))

    h.notify(h.session_statuses[0])
    pump_until(h, lambda: opened and not h.notifications)
    assert opened == [{"provider": "claude", "session_id": "a"}]
    assert "--action=default=Open session" in (fake_notify_send / "notify.log").read_text()


def test_a_dismissed_notification_opens_nothing(fake_notify_send, monkeypatch):
    out = io.StringIO()
    h = helper.Helper(out)
    h.hello()
    opened: list[dict] = []
    monkeypatch.setattr(h, "open_session", lambda command: opened.append(command))
    h.notify(status("a", "waiting_for_input"))
    pump_until(h, lambda: not h.notifications)
    assert opened == []


def test_a_notify_send_that_fails_is_reported(fake_notify_send, monkeypatch):
    monkeypatch.setenv("FAKE_NOTIFY_EXIT", "3")
    out = io.StringIO()
    h = helper.Helper(out)
    h.hello()
    h.notify(status("a", "waiting_for_input"))
    pump_until(h, lambda: not h.notifications)
    errors = [json.loads(line) for line in out.getvalue().splitlines() if '"type":"error"' in line]
    assert errors and errors[-1]["feature"] == "notify"
    assert "exited 3" in errors[-1]["message"]


def test_missing_notify_send_is_a_capability_problem(monkeypatch, tmp_path):
    monkeypatch.setenv("PATH", str(tmp_path))
    caps = helper.probe_capabilities()
    assert caps["notify"]["ok"] is False
    assert "notify-send" in caps["notify"]["reason"]
