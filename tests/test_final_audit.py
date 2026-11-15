"""Robustness and same-user edges: device keys moving to the serial id, a service that does not answer,
out-of-range hook-log times, linked settings and configs, replies of an unknown shape, and what a
rolled-back hook install leaves behind. Each against upstream's own code."""

from __future__ import annotations

import io
import json
import signal
import threading
import time
from dataclasses import replace
from pathlib import Path

import pytest
from sidepulse.settings import AgentMonitorSettings, DeviceDisplaySetting, load_settings, save_settings

import sidepulse_omarchy as helper


@pytest.fixture(autouse=True)
def no_hang():
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
    monkeypatch.setattr(helper, "status_socket_problems", lambda folders: [])
    return tmp_path


def events(out: io.StringIO, kind: str) -> list[dict]:
    return [e for e in map(json.loads, out.getvalue().splitlines()) if e["type"] == kind]


DOT = helper.Device(id="sidepulse:SPD-1", name="PulseDot", path="/run/media/u/PulseDot",
                    target=Path("/run/media/u/PulseDot/LEDS.LED"), kind="dot", led_count=2)


def saved_under_the_mount_path(brightness: int) -> None:
    save_settings(replace(AgentMonitorSettings(), devices=(
        DeviceDisplaySetting(device_id=DOT.path, name=DOT.name, path=DOT.path, led_display="agent",
                             brightness=brightness),)))


# --- A saved brightness survives the move to the serial id ---

def test_a_display_change_moves_the_mount_path_entry_to_the_id_first(home, monkeypatch):
    saved_under_the_mount_path(32)
    monkeypatch.setattr(helper, "shown_program", lambda device: "off")  # no device file here
    monkeypatch.setattr("sidepulse.device_writer.write_led_program", lambda *a, **k: None)
    helper.apply_display(DOT, "off")
    settings = load_settings()
    assert settings.brightness_for_device(DOT.id) == 32
    assert settings.display_for_device(DOT.id) == "custom"
    assert [entry.device_id for entry in settings.devices] == [DOT.id]


def test_a_brightness_change_moves_the_entry_and_sets_the_new_value(home):
    saved_under_the_mount_path(32)
    helper.set_device_brightness(DOT, 50)
    settings = load_settings()
    assert [entry.device_id for entry in settings.devices] == [DOT.id]
    assert settings.brightness_for_device(DOT.id) == 128


def test_an_entry_already_under_the_id_is_left_as_it_is(home):
    save_settings(replace(AgentMonitorSettings(), devices=(
        DeviceDisplaySetting(device_id=DOT.path, name=DOT.name, path=DOT.path, led_display="agent", brightness=32),
        DeviceDisplaySetting(device_id=DOT.id, name=DOT.name, path=DOT.path, led_display="agent", brightness=200),
    )))
    helper.adopt_device_key(DOT)
    assert load_settings().brightness_for_device(DOT.id) == 200


# --- A service that hangs does not hold the loop ---

def test_a_service_that_does_not_answer_makes_the_state_unavailable_quickly(home, monkeypatch):
    release = threading.Event()
    monkeypatch.setattr(helper, "read_snapshot", lambda: release.wait())
    out = io.StringIO()
    h = helper.Helper(out)
    h.hello()
    begun = time.monotonic()
    h.refresh_state(force=True)
    h.refresh_state(force=True)  # the first read is still out: fails at once, no second request
    assert time.monotonic() - begun < 1.0
    states = [e for e in events(out, "state") if e["state"] == "unavailable"]
    assert states and "reading the SidePulse service" in states[-1]["reason"]
    release.set()


# --- Audit 1: a hook-log time out of the local clock's range ---

@pytest.mark.parametrize("stamp", ["9999-12-31T23:59:59+00:00", "0001-01-01T00:00:00+00:00"])
def test_an_extreme_hook_log_time_is_shown_as_written(stamp, monkeypatch):
    monkeypatch.setenv("TZ", "Pacific/Kiritimati" if stamp.startswith("9999") else "America/New_York")
    time.tzset()
    try:
        assert helper._local_clock(stamp) == stamp
    finally:
        monkeypatch.delenv("TZ")
        time.tzset()


def test_a_hook_log_with_an_extreme_time_still_lists(home, monkeypatch):
    folder = home / "state" / "sidepulse" / "agent-monitor"
    folder.mkdir(parents=True)
    (folder / "event-status.jsonl").write_text(json.dumps(
        {"logged_at": "9999-12-31T23:59:59+00:00", "provider": "claude", "hook_event": "Stop",
         "status": "completed"}) + "\n")
    monkeypatch.setenv("TZ", "Pacific/Kiritimati")
    time.tzset()
    try:
        [row] = helper.hook_log_event(path=folder / "event-status.jsonl")["rows"]
        assert row["time"].startswith("9999")
    finally:
        monkeypatch.delenv("TZ")
        time.tzset()


# --- Audit 2: both path chains ---

def test_a_settings_link_in_a_folder_others_can_write_is_refused(home):
    private = home / "dotfiles"
    private.mkdir()
    (private / "settings.json").write_text("{}")
    named = home / "config" / "sidepulse" / "agent-monitor"
    named.mkdir(parents=True)
    (named / "settings.json").symlink_to(private / "settings.json")
    named.chmod(0o777)  # another account could swap the link; upstream opens it by name
    try:
        assert "can be written by another account" in helper.settings_problem()
    finally:
        named.chmod(0o755)


def test_a_linked_agent_config_is_checked_where_it_points(home):
    shared = home / "shared-dotfiles"
    shared.mkdir()
    (shared / "claude.json").write_text("{}")
    claude = home / "home" / ".claude"
    claude.mkdir()
    (claude / "settings.json").symlink_to(shared / "claude.json")
    shared.chmod(0o777)
    try:
        problems = helper.hook_install_problems("claude")
        assert any("config folder" in p and "another account" in p for p in problems)
    finally:
        shared.chmod(0o755)


# --- Audit 3: a reply of a shape the helper does not know ---

def test_a_session_without_a_text_provider_makes_the_state_unavailable(home, daemon, monkeypatch):
    from sidepulse.models import AgentMode, AgentStatus
    from datetime import datetime, timezone

    daemon.store.rows = (AgentStatus(provider=None, agent_id="x", display_name="x", mode=AgentMode.WORKING,
                                     updated_at=datetime.now(timezone.utc), event_name="Stop",
                                     session_id="x", cwd="/tmp"),)
    with pytest.raises(ValueError, match="without a text provider"):
        helper.read_snapshot()


# --- Audit 4: the rollback says what it cannot undo ---

def test_a_rolled_back_install_names_the_service_it_leaves(home, monkeypatch):
    real_check = helper.hook_install_problems
    calls = []

    def check(provider, home=None):
        calls.append(provider)
        return real_check(provider, home) + (["something"] if len(calls) > 1 else [])

    monkeypatch.setattr(helper, "hook_install_problems", check)
    ran = []
    # Codex's real installer starts `codex app-server`; the CLI is stood in for, the helper's handling is real.
    monkeypatch.setattr(helper, "run_hook_command", lambda action, provider, python: ran.append(action) or (True, "ok"))
    out = io.StringIO()
    h = helper.Helper(out)
    h.hello()
    h.handle_line(b'{"cmd": "hooks-install", "provider": "codex"}')
    assert ran == ["install", "uninstall"]
    [result] = events(out, "result")
    assert "installed and restarted its service" in result["message"]
    assert "[features] hooks = true" in result["message"]
