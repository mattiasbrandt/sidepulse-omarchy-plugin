"""Open a session (D8): resume a stopped one, focus or locate a live one, explain an unresolved one.

Commands and terminals are fakes: nothing here focuses a window or starts a terminal.
"""

from __future__ import annotations

import subprocess
from datetime import datetime, timezone

import pytest
from sidepulse.models import AgentMode, AgentStatus
from sidepulse.session_actions import session_resume_command

import sidepulse_omarchy as helper

SESSION = "019a0000-aaaa-7bbb-8ccc-000000000001"
CWD = "/work/project"


class FakeRun:
    def __init__(self, answers: dict[str, tuple[int, str, str]] | None = None) -> None:
        self.answers = answers or {}
        self.asked: list[str] = []

    def __call__(self, argv: list[str]) -> subprocess.CompletedProcess:
        line = " ".join(argv)
        self.asked.append(line)
        code, out, err = self.answers.get(line, (127, "", f"not expected in this test: {line}"))
        return subprocess.CompletedProcess(argv, code, out, err)


class FakeSpawn:
    def __init__(self, error: Exception | None = None) -> None:
        self.started: list[tuple[list[str], str | None]] = []
        self.error = error

    def __call__(self, argv: list[str], cwd: str | None) -> None:
        if self.error is not None:
            raise self.error
        self.started.append((argv, cwd))


def status(provider: str = "claude", session_id: str | None = SESSION) -> AgentStatus:
    return AgentStatus(
        provider=provider,
        agent_id=f"{provider}:{session_id}",
        display_name="demo",
        mode=AgentMode.COMPLETED,
        updated_at=datetime(2026, 10, 11, tzinfo=timezone.utc),
        event_name="Stop",
        session_id=session_id,
        cwd=CWD,
    )


@pytest.mark.parametrize(
    ("provider", "expected"),
    [
        ("claude", f"cd {CWD} && claude --resume {SESSION}"),
        ("codex", f"cd {CWD} && codex resume {SESSION}"),
        ("grok", f"cd {CWD} && grok --resume {SESSION}"),
        ("junie", f"cd {CWD} && junie --session-id={SESSION} --resume"),
    ],
)
def test_stopped_session_resumes_with_upstreams_command_in_a_terminal(provider, expected):
    spawn = FakeSpawn()
    ok, message = helper.perform_open({"action": "resume"}, status(provider), FakeRun(), spawn)
    assert ok is True
    assert session_resume_command(status(provider)) == expected
    assert spawn.started == [(["xdg-terminal-exec", "--", "sh", "-c", expected], CWD)]
    assert "Resuming" in message


def test_session_without_an_id_cannot_resume():
    spawn = FakeSpawn()
    ok, message = helper.perform_open({"action": "resume"}, status(session_id=None), FakeRun(), spawn)
    assert ok is False
    assert spawn.started == []
    assert "no session id" in message


def test_missing_terminal_helper_is_reported_not_hidden():
    spawn = FakeSpawn(error=helper.CommandFailed("xdg-terminal-exec cannot run: not found"))
    ok, message = helper.perform_open({"action": "resume"}, status(), FakeRun(), spawn)
    assert ok is False
    assert "xdg-terminal-exec cannot run" in message


def test_focus_raises_the_window_by_address():
    run = FakeRun({
        "hyprctl dispatch hl.dsp.focus({ window = \"address:0xabc\" })": (0, "", ""),
    })
    ok, message = helper.perform_open({"action": "focus", "address": "0xabc", "title": "demo"}, status(), run,
                                      FakeSpawn())
    assert ok is True
    assert "demo" in message


def test_focus_reports_window_not_found_even_though_hyprctl_exits_zero():
    run = FakeRun({
        "hyprctl dispatch hl.dsp.focus({ window = \"address:0xabc\" })": (0, "hl.focus: window not found", ""),
    })
    ok, message = helper.perform_open({"action": "focus", "address": "0xabc", "title": "demo"}, status(), run,
                                      FakeSpawn())
    assert ok is False
    assert "window not found" in message


def test_focus_refuses_an_address_that_is_not_hex():
    run = FakeRun()
    ok, message = helper.perform_open({"action": "focus", "address": "0x1 }); os.exit(0) --", "title": "x"},
                                      status(), run, FakeSpawn())
    assert ok is False
    assert run.asked == []


def test_herdr_pane_is_focused_and_a_terminal_attached_when_no_client_runs():
    run = FakeRun({"herdr agent focus w2:p1": (0, "", "")})
    spawn = FakeSpawn()
    ok, message = helper.perform_open({"action": "herdr", "pane": "w2:p1", "client": None}, status(), run, spawn)
    assert ok is True
    assert run.asked == ["herdr agent focus w2:p1"]
    assert spawn.started == [(["xdg-terminal-exec", "--", "herdr"], None)]


def test_herdr_pane_and_its_client_window_are_focused():
    run = FakeRun({
        "herdr agent focus w2:p1": (0, "", ""),
        "hyprctl dispatch hl.dsp.focus({ window = \"address:0xdef\" })": (0, "", ""),
    })
    spawn = FakeSpawn()
    client = {"action": "focus", "address": "0xdef", "title": "herdr"}
    ok, _ = helper.perform_open({"action": "herdr", "pane": "w2:p1", "client": client}, status(), run, spawn)
    assert ok is True
    assert spawn.started == []


def test_herdr_pane_focused_but_client_window_unknown_says_so():
    run = FakeRun({"herdr agent focus w2:p1": (0, "", "")})
    client = {"action": "no-window", "ancestry": [1]}
    ok, message = helper.perform_open({"action": "herdr", "pane": "w2:p1", "client": client}, status(), run,
                                      FakeSpawn())
    assert ok is False
    assert "herdr" in message


def test_tmux_pane_is_selected_and_its_client_attached_when_none_runs():
    run = FakeRun({
        "tmux select-window -t %3": (0, "", ""),
        "tmux select-pane -t %3": (0, "", ""),
    })
    spawn = FakeSpawn()
    ok, _ = helper.perform_open({"action": "tmux", "pane": "%3", "session": "work", "client": None}, status(),
                                run, spawn)
    assert ok is True
    assert spawn.started == [(["xdg-terminal-exec", "--", "tmux", "attach-session", "-t", "work"], None)]


def test_explain_runs_nothing_and_says_where_the_session_runs():
    run = FakeRun()
    plan = {"action": "explain", "where": "herdr (pid 300)", "ancestry": [301, 300], "notes": []}
    ok, message = helper.perform_open(plan, status(), run, FakeSpawn())
    assert ok is False
    assert run.asked == []
    assert "herdr (pid 300)" in message


def test_unresolved_session_is_explained_and_never_resumed():
    spawn = FakeSpawn()
    plan = {"action": "explain", "state": "unresolved", "reason": "codex runs in /work but nothing ties it"}
    ok, message = helper.perform_open(plan, status("codex"), FakeRun(), spawn)
    assert ok is False
    assert spawn.started == []
    assert "nothing ties it" in message


def test_ambiguous_window_is_not_guessed():
    ok, message = helper.perform_open({"action": "ambiguous-window", "candidates": 2}, status(), FakeRun(),
                                      FakeSpawn())
    assert ok is False
    assert "2" in message
