"""SidePulse's hook logs are closed to other accounts.

Upstream's hook installer and hook create the agent-monitor state folder and the agents' logs with the
process umask, and the logs hold complete prompts. The helper takes group and other access off that
folder after a hook install from the popup and whenever it starts. Every test runs with a private HOME
and XDG dirs.
"""

from __future__ import annotations

import io
import json
import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

import sidepulse_omarchy as helper


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    (tmp_path / "home").mkdir()
    # pytest's scratch paths are long enough to send upstream's status socket to /tmp; that check has its
    # own tests below.
    monkeypatch.setattr(helper, "status_socket_problems", lambda folders: [])
    return tmp_path


def state_dir(home: Path) -> Path:
    return home / "state" / "sidepulse" / "agent-monitor"


def mode(path: Path) -> int:
    return stat.S_IMODE(os.lstat(path).st_mode)


def hooks_errors(out: io.StringIO) -> list[str]:
    events = [json.loads(line) for line in out.getvalue().splitlines()]
    return [e["message"] for e in events if e["type"] == "error" and e["feature"] == "hook_privacy"]


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


def install_claude_hooks() -> None:
    ok, message = helper.run_hook_command("install", "claude", helper.sys.executable)
    assert ok, message


def claude_hook_command(home: Path) -> str:
    settings = json.loads((home / "home" / ".claude" / "settings.json").read_text())
    return settings["hooks"]["UserPromptSubmit"][-1]["hooks"][0]["command"]


def test_a_missing_folder_is_created_private_when_hooks_are_installed(home):
    # Hooks enabled earlier, the state folder gone before the helper starts.
    install_claude_hooks()
    shutil.rmtree(state_dir(home))
    out = io.StringIO()
    started(out)
    assert state_dir(home).is_dir()
    assert mode(state_dir(home)) == 0o700
    assert hooks_errors(out) == []


def test_upstreams_hook_then_writes_the_prompt_into_the_private_folder(home):
    install_claude_hooks()
    shutil.rmtree(state_dir(home))
    started()
    prompt = json.dumps({"hook_event_name": "UserPromptSubmit", "session_id": "s1", "prompt": "private words"})
    env = dict(os.environ, SIDEPULSE_DISABLE_EVENT_SOCKET="1")
    subprocess.run(["sh", "-c", "umask 022; " + claude_hook_command(home)], input=prompt, text=True,
                   env=env, check=True, timeout=60)
    log = state_dir(home) / "claude.jsonl"
    assert "private words" in log.read_text()
    assert mode(state_dir(home)) == 0o700  # the log's own mode is the umask's; the folder keeps it private


def test_nothing_is_created_without_sidepulse_hooks(home):
    settings = home / "home" / ".claude" / "settings.json"
    settings.parent.mkdir()
    settings.write_text(json.dumps({"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "echo done"}]}]}}))
    started()
    assert not state_dir(home).exists()
    assert not (home / "home" / ".local" / "state" / "sidepulse").exists()


def test_a_missing_log_outside_the_state_folder_is_created_private(home):
    log = home / "home" / "logs" / "claude.jsonl"
    settings = home / "home" / ".claude" / "settings.json"
    settings.parent.mkdir()
    command = f"python -m sidepulse agent-monitor hook-log claude --log {log}"
    settings.write_text(json.dumps({"hooks": {"Stop": [{"matcher": "*", "hooks": [{"type": "command", "command": command}]}]}}))
    started()
    assert log.read_text() == ""
    assert mode(log) == 0o600


def test_a_folder_removed_while_the_helper_runs_comes_back_private(home):
    install_claude_hooks()
    h = started()
    shutil.rmtree(state_dir(home))
    for _ in range(helper.SETUP_EVERY_TICKS):
        h.tick()
    assert mode(state_dir(home)) == 0o700


def test_a_problem_is_shown_once_and_cleared_when_it_is_gone(home, monkeypatch):
    state_dir(home).mkdir(parents=True)
    state_dir(home).chmod(0o755)
    real_fchmod = os.fchmod

    def refuse(fd, mode):
        raise PermissionError(1, "Operation not permitted")

    monkeypatch.setattr(helper.os, "fchmod", refuse)
    out = io.StringIO()
    h = started(out)
    h.keep_hook_logs_private()
    assert len(hooks_errors(out)) == 1
    monkeypatch.setattr(helper.os, "fchmod", real_fchmod)
    h.keep_hook_logs_private()
    events = [json.loads(line) for line in out.getvalue().splitlines()]
    assert {"type": "clear", "feature": "hook_privacy"} in events
    assert mode(state_dir(home)) == 0o700


def test_a_status_socket_sent_to_tmp_is_named():
    long = Path("/home") / ("u" * 60) / ".local" / "state" / "sidepulse" / "agent-monitor"
    [problem] = helper.status_socket_problems((long,))
    assert "/tmp/" in problem and "another account" in problem


def test_a_status_socket_in_the_state_folder_is_fine():
    assert helper.status_socket_problems((Path("/home/u/.local/state/sidepulse/agent-monitor"),)) == []


def test_upstreams_cli_runs_with_a_private_umask(home):
    # The installer's backup of a 600 agent config, and the folder and log it creates, are the user's alone.
    settings = home / "home" / ".claude" / "settings.json"
    settings.parent.mkdir()
    settings.write_text('{"env": {"ANTHROPIC_API_KEY": "sk-test"}}\n')
    settings.chmod(0o600)
    old = os.umask(0o022)
    try:
        install_claude_hooks()
    finally:
        os.umask(old)
    [backup] = [p for p in settings.parent.iterdir() if ".bak." in p.name]
    assert mode(backup) == 0o600
    assert mode(state_dir(home)) == 0o700
    assert mode(state_dir(home) / "claude.jsonl") == 0o600
    assert mode(settings) == 0o600


def test_existing_config_backups_lose_group_and_other_access(home):
    claude = home / "home" / ".claude"
    claude.mkdir()
    (claude / "settings.json").write_text("{}\n")
    backup = claude / "settings.json.bak.20261010T212138Z"
    backup.write_text("{}\n")
    backup.chmod(0o644)
    other = claude / "settings.json.bak"  # not upstream's name: someone else's file
    other.write_text("{}\n")
    other.chmod(0o644)
    started()
    assert mode(backup) == 0o600
    assert mode(other) == 0o644


def test_another_tools_redirect_target_is_not_taken_for_a_sidepulse_log(home):
    shared = home / "home" / "shared.log"
    settings = home / "home" / ".claude" / "settings.json"
    settings.parent.mkdir()
    settings.write_text(json.dumps({"hooks": {"Stop": [{"matcher": "*", "hooks": [
        {"type": "command", "command": f"other-tool >> {shared}"}]}]}}))
    assert str(shared) in helper.hooks_event()["providers"][1]["log_paths"]  # upstream lists it
    started()
    assert not shared.exists()
    assert not state_dir(home).exists()


def test_the_state_folder_is_created_when_the_agent_log_lives_elsewhere(home):
    # The hook writes its audit log (event-status.jsonl, prompt previews) to the state folder regardless.
    log = home / "home" / "logs" / "claude.jsonl"
    settings = home / "home" / ".claude" / "settings.json"
    settings.parent.mkdir()
    command = f"python hook_entry.py --provider claude --log {log}"
    settings.write_text(json.dumps({"hooks": {"Stop": [{"matcher": "*", "hooks": [{"type": "command", "command": command}]}]}}))
    started()
    assert mode(state_dir(home)) == 0o700
