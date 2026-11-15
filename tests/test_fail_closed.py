"""Fail closed on paths another local account can change, and never block the loop on them (#66).

Marketplace review round 5 and two audits found places where the helper went ahead after a check had
failed (hooks left installed after the privacy pass refused a folder), or read a file by name with a
plain open() that a FIFO would block. Each test runs against upstream's real code under a scratch HOME;
a test that would hang if the guard broke is cut off by an alarm instead.
"""

from __future__ import annotations

import io
import json
import os
import signal
import threading
import time
from pathlib import Path

import pytest

import sidepulse_omarchy as helper


@pytest.fixture(autouse=True)
def no_hang():
    """A guard that broke would block on a FIFO for ever: fail the test after 20 s instead."""
    def expired(signum, frame):
        raise AssertionError("the helper blocked")

    previous = signal.signal(signal.SIGALRM, expired)
    signal.alarm(20)
    yield
    signal.alarm(0)
    signal.signal(signal.SIGALRM, previous)


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    (tmp_path / "home").mkdir()
    return tmp_path


@pytest.fixture
def socket_in_folder(monkeypatch):
    # pytest's scratch paths are long enough to send upstream's status socket to /tmp; tests that are not
    # about that take it as in the state folder.
    monkeypatch.setattr(helper, "status_socket_problems", lambda folders: [])


def started() -> tuple[helper.Helper, io.StringIO]:
    out = io.StringIO()
    h = helper.Helper(out)
    h.hello()
    return h, out


def events(out: io.StringIO, kind: str) -> list[dict]:
    return [e for e in map(json.loads, out.getvalue().splitlines()) if e["type"] == kind]


def state_dir(home: Path) -> Path:
    return home / "state" / "sidepulse" / "agent-monitor"


def claude_hooked(home: Path) -> bool:
    settings = home / "home" / ".claude" / "settings.json"
    return settings.exists() and "hook_entry.py" in settings.read_text()


# --- trusted_folder_problem ---

def test_a_private_folder_path_is_trusted(tmp_path):
    folder = tmp_path / "a" / "b"
    folder.mkdir(parents=True)
    assert helper.trusted_folder_problem(folder) is None
    assert helper.trusted_folder_problem(folder / "missing" / "tail") is None


def test_a_folder_others_can_write_on_the_path_is_named(tmp_path):
    shared = tmp_path / "shared"
    (shared / "state").mkdir(parents=True)
    shared.chmod(0o777)
    try:
        assert "can be written by another account" in helper.trusted_folder_problem(shared / "state")
    finally:
        shared.chmod(0o755)


def test_a_sticky_shared_folder_is_fine_only_above_the_users_own_folder(tmp_path):
    shared = tmp_path / "sticky"
    shared.mkdir()
    shared.chmod(0o1777)
    try:
        (shared / "mine").mkdir()
        assert helper.trusted_folder_problem(shared / "mine") is None
        # Missing below a shared folder: another account could create it first.
        assert "could create" in helper.trusted_folder_problem(shared / "theirs")
        assert "shared with other accounts" in helper.trusted_folder_problem(shared)
    finally:
        shared.chmod(0o755)


def test_a_folder_of_another_account_is_named(tmp_path, monkeypatch):
    real = os.getuid()
    monkeypatch.setattr(helper.os, "getuid", lambda: real + 1)
    assert "another account" in helper.trusted_folder_problem(tmp_path)


# --- Hooks: checked before the install, taken out again when the pass after it fails (review round 5) ---

def test_hooks_are_not_installed_into_a_state_folder_another_account_can_change(home, socket_in_folder):
    (home / "state").mkdir()
    (home / "state").chmod(0o777)  # another account could rename sidepulse/ away and plant its own
    try:
        h, out = started()
        h.handle_line(b'{"cmd": "hooks-install", "provider": "claude"}')
        [result] = events(out, "result")
        assert result["ok"] is False
        assert result["message"].startswith("not installed:")
        assert "can be written by another account" in result["message"]
        assert not claude_hooked(home)
    finally:
        (home / "state").chmod(0o755)


def test_hooks_are_removed_again_when_the_check_after_the_install_fails(home, socket_in_folder, monkeypatch):
    # The installer itself leaves something unsafe (here: the claude log it names comes out as another
    # account's): the hooks must not stay active writing there (marketplace review round 5).
    real_check = helper.hook_install_problems
    calls = []

    def check(provider, home=None):
        calls.append(provider)
        return real_check(provider, home) + (["claude.jsonl belongs to another account"] if len(calls) > 1 else [])

    monkeypatch.setattr(helper, "hook_install_problems", check)
    h, out = started()
    h.handle_line(b'{"cmd": "hooks-install", "provider": "claude"}')
    [result] = events(out, "result")
    assert result["ok"] is False
    assert result["message"].startswith("installed, then removed again:")
    assert not claude_hooked(home)
    assert calls == ["claude", "claude"]


def test_a_device_lock_problem_does_not_take_hooks_out(home, socket_in_folder, monkeypatch):
    real_pass = helper.keep_hook_logs_private
    monkeypatch.setattr(helper, "keep_hook_logs_private",
                        lambda home=None: real_pass(home) + ["device-locks belongs to another account"])
    h, out = started()
    h.handle_line(b'{"cmd": "hooks-install", "provider": "claude"}')
    [result] = events(out, "result")
    assert result["ok"] is True
    assert claude_hooked(home)


def test_a_status_socket_in_tmp_refuses_the_install(home):
    # Not stubbed here: pytest's long path really sends the socket to /tmp/sidepulse-status-<uid>/.
    h, out = started()
    h.handle_line(b'{"cmd": "hooks-install", "provider": "claude"}')
    [result] = events(out, "result")
    assert result["ok"] is False and "/tmp/" in result["message"]
    assert not claude_hooked(home)


def test_a_link_planted_in_the_state_folder_is_named_and_refuses_the_install(home, socket_in_folder):
    folder = state_dir(home)
    folder.mkdir(parents=True)
    (home / "elsewhere.jsonl").write_text("")
    (folder / "claude.jsonl").symlink_to(home / "elsewhere.jsonl")
    assert any("symbolic link" in p for p in helper.keep_hook_logs_private())
    h, out = started()
    h.handle_line(b'{"cmd": "hooks-install", "provider": "claude"}')
    [result] = events(out, "result")
    assert result["ok"] is False and "symbolic link" in result["message"]


def test_the_service_socket_is_the_one_non_file_a_state_folder_may_hold():
    import shutil
    import socket
    import tempfile

    folder = Path(tempfile.mkdtemp(prefix="sp-", dir="/tmp"))  # a socket path must stay under 108 bytes
    try:
        os.mkfifo(folder / "event-status.jsonl")
        with socket.socket(socket.AF_UNIX) as server:
            server.bind(str(folder / "program-status.sock"))
            problems = helper.state_entry_problems(folder)
        assert problems == [f"{folder / 'event-status.jsonl'} is not a regular file"]
    finally:
        shutil.rmtree(folder)


def test_an_outside_hook_log_in_a_shared_folder_is_named(home, socket_in_folder):
    shared = home / "shared-logs"
    shared.mkdir()
    shared.chmod(0o777)
    try:
        log = shared / "claude.jsonl"
        settings = home / "home" / ".claude" / "settings.json"
        settings.parent.mkdir(parents=True)
        settings.write_text(json.dumps({"hooks": {"Stop": [{"hooks": [{"type": "command",
            "command": f"python /x/hook_entry.py --provider claude --log {log} ; true"}]}]}}))
        assert any("hook log folder" in p and "another account" in p for p in helper.keep_hook_logs_private())
    finally:
        shared.chmod(0o755)


def test_the_device_lock_folder_is_made_private(home):
    locks = home / "home" / ".cache" / "sidepulse" / "device-locks"
    locks.mkdir(parents=True)
    locks.chmod(0o755)
    helper.keep_hook_logs_private()
    assert locks.stat().st_mode & 0o777 == 0o700


# --- Reads that a FIFO would block ---

def test_a_fifo_settings_file_gives_upstreams_defaults_and_is_never_saved_through(home):
    path = home / "config" / "sidepulse" / "agent-monitor" / "settings.json"
    path.parent.mkdir(parents=True)
    os.mkfifo(path)
    assert "not a regular file" in helper.settings_problem()
    assert helper.load_own_settings() == __import__("sidepulse.settings").settings.AgentMonitorSettings()
    with pytest.raises(OSError, match="not saving"):
        helper.change_settings(lambda s: s)


def test_a_fifo_settings_file_makes_the_state_unavailable_with_the_reason(home, daemon):
    path = home / "config" / "sidepulse" / "agent-monitor" / "settings.json"
    path.parent.mkdir(parents=True)
    os.mkfifo(path)
    h, out = started()
    h.refresh_state(force=True)
    [state] = [e for e in events(out, "state") if e["state"] == "unavailable"]
    assert "not a regular file" in state["reason"]


def test_a_fifo_hook_log_is_reported_not_read(home):
    folder = state_dir(home)
    folder.mkdir(parents=True)
    os.mkfifo(folder / "event-status.jsonl")
    h, out = started()
    h.refresh_log(force=True)
    assert any(e["feature"] == "hook_log" and "regular file" in e["message"] for e in events(out, "error"))


@pytest.mark.parametrize("make", ["fifo", "huge"])
def test_a_session_record_that_is_not_a_plain_short_file_is_skipped(home, make):
    sessions = home / "home" / ".claude" / "sessions"
    sessions.mkdir(parents=True)
    record = sessions / "1.json"
    if make == "fifo":
        os.mkfifo(record)
    else:
        with record.open("wb") as handle:
            handle.truncate(helper.OWN_FILE_LIMIT + 1)
    assert helper.claude_session_ids(helper.Proc(), home / "home") == {}


def test_a_fifo_transcript_gives_no_title(home):
    folder = home / "home" / ".claude" / "projects" / "-x"
    folder.mkdir(parents=True)
    os.mkfifo(folder / "abc.jsonl")
    assert helper.ai_title("abc", "/x", home / "home") is None


def test_a_transcript_line_past_the_limit_is_not_parsed(home):
    folder = home / "home" / ".claude" / "projects" / "-x"
    folder.mkdir(parents=True)
    long = '{"type": "ai-title", "aiTitle": "' + "x" * helper.TRANSCRIPT_LINE_LIMIT + '"}\n'
    (folder / "abc.jsonl").write_text(long + '{"type": "ai-title", "aiTitle": "Short"}\n')
    assert helper.ai_title("abc", "/x", home / "home") == "Short"


def test_a_status_socket_that_is_not_a_socket_is_refused(home):
    folder = state_dir(home)
    folder.mkdir(parents=True)
    from sidepulse.program_transport import status_socket_path

    socket_path = status_socket_path(folder)
    socket_path.parent.mkdir(parents=True, exist_ok=True)
    socket_path.write_text("")
    assert "not the user's own SidePulse socket" in helper.state_source_problem()
    with pytest.raises(ValueError, match="not the user's own"):
        helper.read_snapshot()
    socket_path.unlink()


# --- Calls that can hang get a deadline ---

def test_a_call_past_its_deadline_raises_and_the_next_one_fails_at_once():
    release = threading.Event()
    deadline = helper.Deadline("test call", 0.2)
    with pytest.raises(TimeoutError, match="did not finish within 0.2 s"):
        deadline.run(release.wait)
    started_at = time.monotonic()
    with pytest.raises(TimeoutError, match="has not finished"):
        deadline.run(lambda: "never runs")
    assert time.monotonic() - started_at < 0.1
    release.set()
    deadline.worker.join(1)
    assert deadline.run(lambda: "runs again") == "runs again"


def test_a_call_that_raises_hands_its_error_back():
    with pytest.raises(OSError, match="boom"):
        helper.Deadline("test call", 1).run(lambda: (_ for _ in ()).throw(OSError("boom")))


def test_a_discovery_that_hangs_is_reported_and_the_loop_goes_on(home, monkeypatch):
    release = threading.Event()
    monkeypatch.setattr(helper, "discover_device_rows", lambda: release.wait() and ([], []))
    h, out = started()
    h.discovery.seconds = 0.2
    begun = time.monotonic()
    h.refresh_devices(force=True)
    h.refresh_devices(force=True)
    assert time.monotonic() - begun < 2
    assert any(e["feature"] == "devices" and "device discovery" in e["message"] for e in events(out, "error"))
    release.set()


def test_a_service_check_that_hangs_is_reported(home, monkeypatch):
    import sidepulse.service_launch as launch

    release = threading.Event()
    monkeypatch.setattr(launch, "service_is_running", release.wait)
    h, out = started()
    h.service_check.seconds = 0.2
    h.refresh_setup(force=True)
    assert any(e["feature"] == "service" and "did not finish" in e["message"] for e in events(out, "error"))
    release.set()


# --- Devices: the folders above the volume ---

def test_a_device_under_a_folder_others_can_write_is_left_alone(home, monkeypatch):
    mounts = home / "media"
    (mounts / "PulseDot").mkdir(parents=True)
    mounts.chmod(0o777)  # another account could rename PulseDot away and put its own volume folder there
    try:
        monkeypatch.setenv("SIDEPULSE_MOUNT_ROOTS", str(mounts))
        rows, refused = helper.discover_device_rows()
        assert rows == []
        [(name, problem)] = refused
        assert name == "PulseDot" and "can be written by another account" in problem
    finally:
        mounts.chmod(0o755)
