"""Settings section writes (D13): idle timeout, session retention and the Bar LEDs switch.

Written through upstream's load_settings and save_settings, so the values mean
the same as on the Mac. Expected values are literal numbers, never the helper's.
"""

from __future__ import annotations

import io
import json

import pytest

import sidepulse_omarchy as helper
from sidepulse.settings import load_settings


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    (tmp_path / "home").mkdir()
    return tmp_path


def started() -> tuple[helper.Helper, io.StringIO]:
    out = io.StringIO()
    h = helper.Helper(out)
    h.hello()
    return h, out


def events_since(out: io.StringIO, mark: int) -> list[dict]:
    return [json.loads(line) for line in out.getvalue()[mark:].splitlines()]


def send(h: helper.Helper, out: io.StringIO, command: dict) -> list[dict]:
    mark = len(out.getvalue())
    h.handle_line(json.dumps(command).encode())
    return events_since(out, mark)


def test_settings_event_reports_the_upstream_values(home):
    h, out = started()
    h.refresh(force=True)
    settings = [e for e in map(json.loads, out.getvalue().splitlines()) if e["type"] == "settings"][-1]
    # Upstream's defaults: 1 hour idle, 48 hours of retention, Bar LEDs off.
    assert settings == {
        "type": "settings",
        "bar_leds": False,
        "idle_timeout_seconds": 3600.0,
        "recent_session_retention_seconds": 172800.0,
    }


def test_idle_timeout_and_retention_are_saved_through_upstream(home):
    h, out = started()
    events = send(h, out, {"cmd": "set-agent-list", "idle_timeout_seconds": 1800,
                           "recent_session_retention_seconds": 86400})
    assert not [e for e in events if e["type"] == "error"]
    settings = load_settings()
    assert settings.idle_timeout_seconds == 1800
    assert settings.recent_session_retention_seconds == 86400
    last = [e for e in events if e["type"] == "settings"][-1]
    assert last["idle_timeout_seconds"] == 1800
    assert last["recent_session_retention_seconds"] == 86400


def test_one_timing_value_leaves_the_other_alone(home):
    h, out = started()
    send(h, out, {"cmd": "set-agent-list", "idle_timeout_seconds": 1800})
    send(h, out, {"cmd": "set-agent-list", "recent_session_retention_seconds": 3600})
    settings = load_settings()
    assert settings.idle_timeout_seconds == 1800
    assert settings.recent_session_retention_seconds == 3600


@pytest.mark.parametrize("command", [
    {"cmd": "set-agent-list"},
    {"cmd": "set-agent-list", "idle_timeout_seconds": 0},
    {"cmd": "set-agent-list", "idle_timeout_seconds": -5},
    {"cmd": "set-agent-list", "idle_timeout_seconds": "900"},
    {"cmd": "set-agent-list", "idle_timeout_seconds": True},
    {"cmd": "set-agent-list", "recent_session_retention_seconds": -1},
    {"cmd": "set-agent-list", "recent_session_retention_seconds": None},
])
def test_invalid_timing_is_refused_before_upstream_clamps_it_to_zero(home, command):
    h, out = started()
    events = send(h, out, command)
    assert [e["type"] for e in events] == ["error"]
    assert events[0]["feature"] == "command"
    assert not (home / "config" / "sidepulse" / "agent-monitor" / "settings.json").exists()


def test_zero_retention_is_allowed(home):
    h, out = started()
    events = send(h, out, {"cmd": "set-agent-list", "recent_session_retention_seconds": 0})
    assert not [e for e in events if e["type"] == "error"]
    assert load_settings().recent_session_retention_seconds == 0


def test_bar_leds_switch_is_saved_as_upstreams_virtual_status_device_setting(home):
    h, out = started()
    events = send(h, out, {"cmd": "set-bar-leds", "enabled": True})
    assert not [e for e in events if e["type"] == "error"]
    assert load_settings().virtual_status_device_enabled is True
    assert [e for e in events if e["type"] == "settings"][-1]["bar_leds"] is True

    send(h, out, {"cmd": "set-bar-leds", "enabled": False})
    assert load_settings().virtual_status_device_enabled is False


def test_bar_leds_switch_refuses_a_value_that_is_not_a_boolean(home):
    h, out = started()
    events = send(h, out, {"cmd": "set-bar-leds", "enabled": "yes"})
    assert [e["type"] for e in events] == ["error"]
    assert load_settings().virtual_status_device_enabled is False


def test_settings_writes_keep_keys_upstream_models(home):
    path = home / "config" / "sidepulse" / "agent-monitor" / "settings.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"led_display": "agent", "history": {"timeframe_seconds": 43200.0}}))
    h, out = started()
    send(h, out, {"cmd": "set-bar-leds", "enabled": True})
    saved = json.loads(path.read_text())
    assert saved["history"] == {"timeframe_seconds": 43200.0}
    assert saved["virtual_status_device_enabled"] is True
