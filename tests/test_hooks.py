"""Hooks (D13, D14): per-provider status, install and uninstall, and the hook log.

Upstream's own code does the work: detect_provider_configs (doctor's payload),
the agent-monitor CLI's install and uninstall, and the audit log reader. Every
test runs with a private HOME and XDG dirs, so the user's real agent configs
are never touched.
"""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

import sidepulse_omarchy as helper


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    (tmp_path / "home").mkdir()
    return tmp_path


def provider(payload: dict, name: str) -> dict:
    return next(row for row in payload["providers"] if row["provider"] == name)


def test_doctor_payload_lists_every_provider_and_finds_no_config_in_a_bare_home(home):
    payload = helper.hooks_event()
    assert payload["type"] == "hooks"
    assert [row["provider"] for row in payload["providers"]] == ["codex", "claude", "grok", "cursor", "junie"]
    claude = provider(payload, "claude")
    assert claude["exists"] is False
    assert claude["hooks_enabled"] is False


def test_install_then_uninstall_claude_hooks_in_a_scratch_home(home):
    ok, message = helper.run_hook_command("install", "claude", helper.sys.executable)
    assert ok, message
    assert "claude: updated" in message
    settings = home / "home" / ".claude" / "settings.json"
    assert settings.exists()
    installed = provider(helper.hooks_event(), "claude")
    assert installed["exists"] is True
    assert installed["hooks_enabled"] is True

    ok, message = helper.run_hook_command("uninstall", "claude", helper.sys.executable)
    assert ok, message
    assert "claude: removed" in message
    assert provider(helper.hooks_event(), "claude")["hooks_enabled"] is False


def test_a_hook_command_that_fails_is_reported_with_its_output(home, monkeypatch):
    # An unknown provider makes the CLI exit non-zero: the reason must come back.
    ok, message = helper.run_hook_command("install", "nope", helper.sys.executable)
    assert ok is False
    assert "invalid choice" in message


def test_hook_commands_refuse_unknown_providers_without_running_anything(home):
    out = io.StringIO()
    h = helper.Helper(out)
    h.hello()
    out.seek(0), out.truncate()
    h.handle_line(b'{"cmd": "hooks-install", "provider": "../../etc"}')
    event = json.loads(out.getvalue().splitlines()[-1])
    assert event["type"] == "error"
    assert event["feature"] == "command"
    assert not (home / "home" / ".claude").exists()


def test_install_result_reaches_the_popup_and_the_status_is_reread(home):
    out = io.StringIO()
    h = helper.Helper(out)
    h.hello()
    out.seek(0), out.truncate()
    h.handle_line(b'{"cmd": "hooks-install", "provider": "claude"}')
    events = [json.loads(line) for line in out.getvalue().splitlines()]
    result = next(e for e in events if e["type"] == "result")
    assert result["command"] == "hooks-install"
    assert result["provider"] == "claude"
    assert result["ok"] is True
    hooks = [e for e in events if e["type"] == "hooks"]
    assert hooks and provider(hooks[-1], "claude")["hooks_enabled"] is True


def write_log(state_home: Path, rows: list[dict]) -> Path:
    path = state_home / "sidepulse" / "agent-monitor" / "event-status.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))
    return path


def audit_row(**overrides) -> dict:
    row = {
        "audited_at": "2026-10-11T10:00:00Z",
        "logged_at": "2026-10-11T10:00:00+00:00",
        "provider": "claude",
        "hook_event": "Stop",
        "status": "completed",
        "status_label": "Completed",
        "origin": "",
        "display_name": "Claude",
        "session_id": "s1",
        "agent_id": "claude:s1",
        "cwd": "/tmp/project",
        "tool_name": "",
        "message": "All done",
        "raw_preview": "{}",
    }
    row.update(overrides)
    return row


def test_log_rows_show_the_provider_event_and_the_state_it_became(home):
    write_log(home / "state", [
        audit_row(hook_event="UserPromptSubmit", status="working"),
        audit_row(hook_event="Stop", status="completed"),
        audit_row(hook_event="Notification", status="waiting_for_input"),
    ])
    rows = helper.hook_log_event(limit=10)["rows"]
    # Newest first.
    assert [(r["event"], r["label"]) for r in rows] == [
        ("Notification", "Ask"),
        ("Stop", "Done"),
        ("UserPromptSubmit", "Working"),
    ]
    assert rows[0]["colour"] == helper.STATE_COLOURS["ask"]


def test_log_keeps_only_the_last_n_rows(home):
    write_log(home / "state", [audit_row(hook_event=f"E{i}", status="working") for i in range(30)])
    rows = helper.hook_log_event(limit=5)["rows"]
    assert [r["event"] for r in rows] == ["E29", "E28", "E27", "E26", "E25"]


def test_a_log_row_without_a_status_has_no_state(home):
    write_log(home / "state", [audit_row(hook_event="SessionStart", status="", status_label="")])
    rows = helper.hook_log_event(limit=5)["rows"]
    assert rows[0]["label"] == ""
    assert rows[0]["colour"] == ""


def test_no_log_yet_is_an_empty_log_not_an_error(home):
    assert helper.hook_log_event(limit=5) == {"type": "log", "rows": []}


def test_damaged_lines_are_skipped(home):
    path = write_log(home / "state", [audit_row(hook_event="Stop", status="completed")])
    path.write_text("not json\n" + path.read_text() + "[1, 2]\n")
    rows = helper.hook_log_event(limit=5)["rows"]
    assert [r["event"] for r in rows] == ["Stop"]


def test_a_config_that_is_json_but_not_an_object_is_a_problem_not_a_crash(home):
    # Upstream's detector calls .get() on whatever JSON decodes; a list used to raise AttributeError.
    (home / "home" / ".claude").mkdir()
    (home / "home" / ".claude" / "settings.json").write_text("[]")
    payload = helper.hooks_event()
    claude = provider(payload, "claude")
    assert "error" in claude
    assert claude["error"]
    # The other agents are still reported.
    assert provider(payload, "codex")["exists"] is False


def test_the_helper_reports_a_hooks_problem_and_keeps_running(home):
    (home / "home" / ".claude").mkdir()
    (home / "home" / ".claude" / "settings.json").write_text("[]")
    out = io.StringIO()
    h = helper.Helper(out)
    h.hello()
    out.seek(0), out.truncate()
    h.refresh_hooks(force=True)  # must not raise
    events = [json.loads(line) for line in out.getvalue().splitlines()]
    assert [e["type"] for e in events][-1] == "hooks"
    assert provider(events[-1], "codex")["exists"] is False
    h.handle_line(b'{"cmd": "refresh"}')  # the helper still answers commands

