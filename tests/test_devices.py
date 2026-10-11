"""Devices (D9, D13): discovery, Display (Agent / Off), brightness, settings round-trip.

Upstream's own code does the work: discover_devices, LED counts, load_settings,
save_settings, write_led_program. Expected values come from upstream's
resource files and literal numbers, never from the helper itself.
"""

from __future__ import annotations

import json
import os
import select
import subprocess
import sys
import time
from importlib.resources import files
from pathlib import Path

import pytest

import sidepulse_omarchy as helper
from sidepulse.settings import load_settings

HELPER = Path(__file__).resolve().parent.parent / "helper" / "sidepulse_omarchy.py"


def off_program() -> str:
    """Upstream's off.LED for the device, read straight from its resources."""
    return files("sidepulse.resources").joinpath("animations", "off.LED").read_text(encoding="utf-8").strip()


@pytest.fixture
def home(tmp_path, monkeypatch):
    """A private HOME and XDG dirs, so no test touches the real user's files."""
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    (tmp_path / "home").mkdir()
    return tmp_path


@pytest.fixture
def mounts(home, monkeypatch):
    """Two mounted devices: a SidePulse Pro and a SidePulse Dot (the PulseDot drive)."""
    root = home / "media"
    (root / "SidePulsePro").mkdir(parents=True)
    (root / "PulseDot").mkdir(parents=True)
    monkeypatch.setenv("SIDEPULSE_MOUNT_ROOTS", str(root))
    return root


def settings_file(home: Path) -> Path:
    return home / "config" / "sidepulse" / "agent-monitor" / "settings.json"


def read_settings_json(home: Path) -> dict:
    return json.loads(settings_file(home).read_text())


def upstream_entry(home: Path, device_id: str) -> dict:
    return next(entry for entry in read_settings_json(home)["devices"] if entry["id"] == device_id)


def device_by_name(name: str) -> helper.Device:
    for device in helper.discover_device_rows():
        if device.name == name:
            return device
    raise AssertionError(f"no device named {name}")


def test_discovery_reports_each_product_with_its_led_count(mounts):
    pro = device_by_name("SidePulsePro")
    dot = device_by_name("PulseDot")
    assert (pro.kind, pro.led_count) == ("pro", 8)
    assert (dot.kind, dot.led_count) == ("dot", 2)
    assert pro.id == str(mounts / "SidePulsePro")
    assert dot.path == str(mounts / "PulseDot")


def test_save_keeps_the_keys_upstream_models(home, mounts):
    device = device_by_name("PulseDot")
    settings_file(home).parent.mkdir(parents=True)
    settings_file(home).write_text(json.dumps({
        "led_display": "agent",
        "agent_list": {"idle_timeout_seconds": 120.0, "recent_session_retention_seconds": 600.0},
        "devices": [
            {"id": device.id, "name": "PulseDot", "path": device.path,
             "led_display": "agent", "brightness": 255},
            {"id": "/elsewhere/Other", "name": "Other", "path": "/elsewhere/Other",
             "led_display": "custom", "brightness": 10},
        ],
    }))
    helper.set_device_brightness(device, 40)
    data = read_settings_json(home)
    assert data["agent_list"]["idle_timeout_seconds"] == 120.0
    assert data["agent_list"]["recent_session_retention_seconds"] == 600.0
    other = next(entry for entry in data["devices"] if entry["id"] == "/elsewhere/Other")
    assert other["led_display"] == "custom"
    assert other["brightness"] == 10
    assert upstream_entry(home, device.id)["brightness"] == 102


def test_save_drops_keys_upstream_does_not_model_as_upstream_does(home, mounts):
    # D13: unknown-key handling stays upstream's (CODING_STANDARDS).
    device = device_by_name("PulseDot")
    settings_file(home).parent.mkdir(parents=True)
    settings_file(home).write_text(json.dumps({"led_display": "agent", "a_key_from_a_newer_upstream": 1}))
    helper.set_device_brightness(device, 40)
    assert "a_key_from_a_newer_upstream" not in read_settings_json(home)


def test_off_takes_the_device_to_manual_and_writes_upstreams_off_program(home, mounts):
    device = device_by_name("PulseDot")
    helper.apply_display(device, "off")
    assert load_settings().display_for_device(device.id) == "custom"
    assert (mounts / "PulseDot" / "LEDS.LED").read_text().strip() == off_program()


def test_agent_hands_the_device_back_to_the_service_without_writing(home, mounts):
    device = device_by_name("PulseDot")
    helper.apply_display(device, "off")
    (mounts / "PulseDot" / "LEDS.LED").unlink()
    helper.apply_display(device, "agent")
    assert load_settings().display_for_device(device.id) == "agent"
    assert not (mounts / "PulseDot" / "LEDS.LED").exists()


def test_a_remembered_display_the_helper_does_not_offer_falls_back_to_agent():
    # Upstream's "custom" is not a Display; a remembered "battery" on a machine without a battery means Agent (D9).
    assert helper.display_of({"dev": "custom"}, "dev") == "agent"
    assert helper.display_of({"dev": "battery"}, "dev", battery_offered=False) == "agent"
    assert helper.display_of({"dev": "battery"}, "dev") == "battery"
    assert helper.display_of({"dev": "off"}, "dev") == "off"
    assert helper.display_of({}, "dev") == "agent"


def test_a_device_left_manual_by_a_crash_is_repaired_to_agent(home, mounts):
    device = device_by_name("PulseDot")
    # The helper died mid-takeover: upstream says custom, nothing is remembered.
    helper.change_settings(
        lambda s: s.with_device_display(device.id, "custom", name=device.name, path=device.path))
    helper.apply_display(device, helper.display_of({}, device.id))
    assert load_settings().display_for_device(device.id) == "agent"


def test_off_repairs_a_program_written_after_it(home, mounts, monkeypatch):
    # The service read settings before Off was saved, then wrote its agent
    # program after the helper's off program (upstream service.sync_outputs).
    device = device_by_name("PulseDot")
    helper.apply_display(device, "off")
    led = mounts / "PulseDot" / "LEDS.LED"
    led.write_text("an agent program the service planned before Off\n")
    helper.apply_display(device, "off")
    assert led.read_text().strip() == off_program()


def test_off_is_not_rewritten_while_the_device_shows_it(home, mounts, monkeypatch):
    device = device_by_name("PulseDot")
    helper.apply_display(device, "off")
    writes = []
    import sidepulse.device_writer as device_writer
    monkeypatch.setattr(device_writer, "write_led_program", lambda *a, **k: writes.append(a))
    helper.apply_display(device, "off")
    assert writes == []


def test_shown_colour_is_the_first_colour_of_the_program_on_the_device(home, mounts):
    # What the LEDs show, not the menu-bar legend: upstream's Done program is
    # cyan while its legend colour for Done is green.
    device = device_by_name("PulseDot")
    led = mounts / "PulseDot" / "LEDS.LED"
    led.write_text("brightness 128\noff 320ms cosine\n0:#00e5ff 760ms pulse 0ms; 1:#00E5FF 760ms pulse 260ms\nrepeat\n")
    assert helper.shown_colour(device) == "#00E5FF"
    helper.apply_display(device, "off")
    assert helper.shown_colour(device) is None  # upstream's off program has no colour
    led.unlink()
    assert helper.shown_colour(device) is None


def test_brightness_percent_maps_to_upstreams_0_to_255(home, mounts):
    device = device_by_name("SidePulsePro")
    helper.set_device_brightness(device, 40)
    assert load_settings().brightness_for_device(device.id) == 102
    helper.set_device_brightness(device, 100)
    assert load_settings().brightness_for_device(device.id) == 255


# Protocol: the helper as the widget runs it.

class Helper:
    def __init__(self, home: Path) -> None:
        env = {
            **os.environ,
            "HOME": str(home / "home"),
            "XDG_CONFIG_HOME": str(home / "config"),
            "XDG_STATE_HOME": str(home / "state"),
            "SIDEPULSE_MOUNT_ROOTS": str(home / "media"),
        }
        self.proc = subprocess.Popen(
            [sys.executable, "-I", str(HELPER)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, env=env,
        )
        self.buffer = b""

    def read_event(self, timeout: float = 15.0) -> dict:
        deadline = time.monotonic() + timeout
        while b"\n" not in self.buffer:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("no event from the helper")
            ready, _, _ = select.select([self.proc.stdout], [], [], remaining)
            if ready:
                chunk = os.read(self.proc.stdout.fileno(), 65536)
                if not chunk:
                    raise EOFError("helper closed stdout")
                self.buffer += chunk
        line, self.buffer = self.buffer.split(b"\n", 1)
        return json.loads(line)

    def event_matching(self, predicate, timeout: float = 15.0) -> dict:
        deadline = time.monotonic() + timeout
        while True:
            event = self.read_event(max(0.1, deadline - time.monotonic()))
            if predicate(event):
                return event

    def send(self, obj: dict) -> None:
        self.proc.stdin.write((json.dumps(obj) + "\n").encode())
        self.proc.stdin.flush()

    def close(self) -> None:
        if self.proc.poll() is None:
            self.proc.stdin.close()
            self.proc.wait(timeout=15)


@pytest.fixture
def helper_proc(home, mounts):
    proc = Helper(home)
    yield proc
    proc.close()


def test_devices_event_lists_the_mounted_products(helper_proc):
    event = helper_proc.event_matching(lambda e: e["type"] == "devices")
    by_name = {item["name"]: item for item in event["items"]}
    assert by_name["SidePulsePro"]["kind"] == "pro"
    assert by_name["SidePulsePro"]["led_count"] == 8
    assert by_name["PulseDot"]["kind"] == "dot"
    assert by_name["PulseDot"]["led_count"] == 2
    assert by_name["PulseDot"]["display"] == "agent"
    assert by_name["PulseDot"]["percent"] == 100
    assert "colour" in by_name["PulseDot"]


def test_set_displays_off_then_agent_round_trips_through_the_helper(helper_proc, home):
    dot_id = str(home / "media" / "PulseDot")
    helper_proc.event_matching(lambda e: e["type"] == "devices")
    helper_proc.send({"cmd": "set-displays", "displays": {dot_id: "off"}})
    event = helper_proc.event_matching(lambda e: e["type"] == "devices" and any(
        i["id"] == dot_id and i["display"] == "off" for i in e["items"]))
    assert event is not None
    assert upstream_entry(home, dot_id)["led_display"] == "custom"
    assert (home / "media" / "PulseDot" / "LEDS.LED").read_text().strip() == off_program()
    helper_proc.send({"cmd": "set-displays", "displays": {dot_id: "agent"}})
    helper_proc.event_matching(lambda e: e["type"] == "devices" and any(
        i["id"] == dot_id and i["display"] == "agent" for i in e["items"]))
    assert upstream_entry(home, dot_id)["led_display"] == "agent"


def test_set_brightness_writes_upstream_settings(helper_proc, home):
    pro_id = str(home / "media" / "SidePulsePro")
    helper_proc.event_matching(lambda e: e["type"] == "devices")
    helper_proc.send({"cmd": "set-brightness", "device": pro_id, "percent": 40})
    helper_proc.event_matching(lambda e: e["type"] == "devices" and any(
        i["id"] == pro_id and i["percent"] == 40 for i in e["items"]))
    assert upstream_entry(home, pro_id)["brightness"] == 102


def test_unknown_display_is_refused_as_an_error(helper_proc):
    helper_proc.event_matching(lambda e: e["type"] == "devices")
    helper_proc.send({"cmd": "set-displays", "displays": {"x": "sparkles"}})
    event = helper_proc.event_matching(lambda e: e["type"] == "error")
    assert event["feature"] == "command"
    assert "sparkles" in event["message"]
