"""SidePulse's hook logs are closed to other accounts (#58).

Upstream's hook installer and hook create the agent-monitor state folder and the agents' logs with the
process umask, and the logs hold complete prompts. The helper takes group and other access off that
folder after a hook install from the popup and whenever it starts. Every test runs with a private HOME
and XDG dirs.
"""

from __future__ import annotations

import io
import json
import os
import stat
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


def state_dir(home: Path) -> Path:
    return home / "state" / "sidepulse" / "agent-monitor"


def mode(path: Path) -> int:
    return stat.S_IMODE(os.lstat(path).st_mode)


def hooks_errors(out: io.StringIO) -> list[str]:
    events = [json.loads(line) for line in out.getvalue().splitlines()]
    return [e["message"] for e in events if e["type"] == "error" and e["feature"] == "hooks"]


def started(out: io.StringIO | None = None) -> helper.Helper:
    h = helper.Helper(out or io.StringIO())
    h.hello()
    return h


def test_a_hook_install_from_the_popup_leaves_the_logs_private(home):
    old = os.umask(0o022)  # the umask the finding assumes
    try:
        h = started()
        h.handle_line(b'{"cmd": "hooks-install", "provider": "claude"}')
    finally:
        os.umask(old)
    log = state_dir(home) / "claude.jsonl"
    assert log.exists()
    assert mode(state_dir(home)) == 0o700


def test_an_open_state_folder_is_closed_when_the_helper_starts(home):
    state_dir(home).mkdir(parents=True)
    state_dir(home).chmod(0o755)
    (state_dir(home) / "claude.jsonl").write_text("{}\n")
    out = io.StringIO()
    started(out)
    assert mode(state_dir(home)) == 0o700
    assert hooks_errors(out) == []


def test_the_fallback_state_folder_is_closed_too(home):
    # Hooks run in the user's shell and may resolve ~/.local/state when the helper has XDG_STATE_HOME set.
    fallback = home / "home" / ".local" / "state" / "sidepulse" / "agent-monitor"
    fallback.mkdir(parents=True)
    fallback.chmod(0o755)
    started()
    assert mode(fallback) == 0o700


def test_no_state_folder_is_created_when_there_is_none(home):
    out = io.StringIO()
    started(out)
    assert not state_dir(home).exists()
    assert hooks_errors(out) == []


def test_a_log_outside_the_state_folder_is_made_private_and_its_folder_left_alone(home):
    logs = home / "home" / "logs"
    logs.mkdir()
    logs.chmod(0o755)
    log = logs / "claude.jsonl"
    log.write_text("{}\n")
    log.chmod(0o644)
    settings = home / "home" / ".claude" / "settings.json"
    settings.parent.mkdir()
    command = f"python -m sidepulse agent-monitor hook-log claude --log {log}"
    settings.write_text(json.dumps({"hooks": {"Stop": [{"matcher": "*", "hooks": [{"type": "command", "command": command}]}]}}))
    assert str(log) in helper.hooks_event()["providers"][1]["log_paths"]
    started()
    assert mode(log) == 0o600
    assert mode(logs) == 0o755


def test_a_linked_state_folder_is_left_alone_and_reported(home):
    elsewhere = home / "elsewhere"
    elsewhere.mkdir()
    elsewhere.chmod(0o755)
    state_dir(home).parent.mkdir(parents=True)
    state_dir(home).symlink_to(elsewhere)
    out = io.StringIO()
    started(out)
    assert mode(elsewhere) == 0o755
    assert any("symbolic link" in message for message in hooks_errors(out))


def test_a_folder_owned_by_another_account_is_left_alone(home, monkeypatch):
    state_dir(home).mkdir(parents=True)
    state_dir(home).chmod(0o755)
    real_getuid = os.getuid
    monkeypatch.setattr(helper.os, "getuid", lambda: real_getuid() + 1)
    assert "another account" in helper.make_private(state_dir(home), folder=True)
    assert mode(state_dir(home)) == 0o755


def test_an_error_is_reported_and_the_helper_carries_on(home, monkeypatch):
    state_dir(home).mkdir(parents=True)

    def refuse(fd, mode):
        raise PermissionError(1, "Operation not permitted")

    monkeypatch.setattr(helper.os, "fchmod", refuse)
    out = io.StringIO()
    h = started(out)
    assert any("cannot make" in message and "not permitted" in message for message in hooks_errors(out))
    assert h.capabilities["hooks"]["ok"] is True


def test_the_guard_reads_upstreams_state_folders():
    assert "candidate_state_dirs" in helper.REQUIRED_NAMES["hooks"]["sidepulse.providers"]


def test_a_linked_log_outside_the_state_folder_is_left_alone(home, tmp_path):
    target = tmp_path / "target"
    target.write_text("{}\n")
    target.chmod(0o644)
    link = home / "home" / "claude.jsonl"
    link.symlink_to(target)
    assert "symbolic link" in helper.make_private(link, folder=False)
    assert mode(target) == 0o644
