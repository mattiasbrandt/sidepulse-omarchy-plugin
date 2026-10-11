"""Laptop features (D10, D9, D12): the battery display, the 7 s battery preview on plug and unplug,
and the lid animations, with the LEDs handed over to the helper while it drives them.

Upstream does the logic: program_for_battery, the lid programs and their defaults, the
settings and the LED writer. The lid and battery readers are the helper's own (logind through
busctl, /sys/class/power_supply); tests inject a fake lid state and fake /sys trees, and a
fake busctl sits on PATH so the capability probe passes everywhere. Expected values come from
upstream's literals, never from the helper.
"""

from __future__ import annotations

import ast
import copy
import io
import json
import subprocess
from importlib.resources import files
from pathlib import Path
from types import SimpleNamespace

import pytest
import sidepulse
from sidepulse import settings as upstream_settings
from sidepulse.battery import BatterySnapshot
from sidepulse.device_writer import MAX_LED_LINES
from sidepulse.settings import load_settings

import sidepulse_omarchy as helper

LID_ANIMATION_FUDGE = 0.15  # upstream's LID_ANIMATION_RESTORE_FUDGE_SECONDS


def upstream_status_bar() -> Path:
    return Path(sidepulse.__file__).parent / "status_bar.py"


def function_ast(path: Path, name: str) -> str:
    tree = ast.parse(path.read_text())
    (node,) = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == name]
    node = copy.deepcopy(node)
    first = node.body[0] if node.body else None
    if isinstance(first, ast.Expr) and isinstance(getattr(first, "value", None), ast.Constant):
        node.body = node.body[1:]
    return ast.dump(node)


def off_program() -> str:
    return files("sidepulse.resources").joinpath("animations", "off.LED").read_text(encoding="utf-8").strip()


def make_supply(root: Path, name: str, **files_: str) -> None:
    directory = root / name
    directory.mkdir(parents=True, exist_ok=True)
    for key, value in files_.items():
        (directory / key).write_text(value + "\n")


def write_battery(root: Path, *, percent: int, status: str, plugged: bool, power_now: int | None = None) -> None:
    """One battery and the AC adapter, as /sys/class/power_supply shows them."""
    battery = {"type": "Battery", "present": "1", "capacity": str(percent), "status": status}
    if power_now is not None:
        battery["power_now"] = str(power_now)
    make_supply(root, "BAT0", **battery)
    make_supply(root, "AC", type="Mains", online="1" if plugged else "0")


@pytest.fixture
def world(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    bin_dir = tmp_path / "fake-bin"
    bin_dir.mkdir()
    (bin_dir / "busctl").write_text("#!/bin/sh\nexit 0\n")
    (bin_dir / "busctl").chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}:{__import__('os').environ['PATH']}")
    mounts = tmp_path / "media"
    (mounts / "PulseDot").mkdir(parents=True)
    monkeypatch.setenv("SIDEPULSE_MOUNT_ROOTS", str(mounts))

    out = io.StringIO()
    h = helper.Helper(out)
    h.hello()
    lid = {"closed": False}
    now = [1000.0]
    h.power_root = tmp_path / "power"  # no battery until a test makes one
    h.lid_reader = lambda: lid["closed"]
    h.clock = lambda: now[0]
    dot = helper.discover_device_rows()[0]
    return SimpleNamespace(h=h, out=out, now=now, lid=lid, power=tmp_path / "power", dot=dot,
                           led_file=dot.target, dot_id=dot.id)


def events(out: io.StringIO, kind: str) -> list[dict]:
    return [e for e in map(json.loads, out.getvalue().splitlines()) if e["type"] == kind]


def errors_for(out: io.StringIO, feature: str) -> list[str]:
    return [e["message"] for e in events(out, "error") if e["feature"] == feature]


def send(h, cmd: dict) -> None:
    h.handle_line(json.dumps(cmd).encode())


def set_display(w, display: str) -> None:
    send(w.h, {"cmd": "set-displays", "displays": {w.dot_id: display}})


def upstream_value(w) -> str:
    return load_settings().display_for_device(w.dot_id)


def shown(w) -> str:
    return w.led_file.read_text().strip()


# --- Readers: /sys/class/power_supply fixtures ---

def test_a_charging_battery_is_plugged_and_its_intake_is_the_adapter_power(tmp_path):
    write_battery(tmp_path, percent=40, status="Charging", plugged=True, power_now=65_000_000)
    battery = helper.read_battery(tmp_path)
    assert (battery.percent, battery.is_plugged, battery.is_charging, battery.is_charged) == (40, True, True, False)
    assert battery.adapter_power == 65


def test_a_discharging_battery_is_not_plugged(tmp_path):
    write_battery(tmp_path, percent=15, status="Discharging", plugged=False)
    battery = helper.read_battery(tmp_path)
    assert (battery.percent, battery.is_plugged, battery.is_charging) == (15, False, False)
    assert battery.adapter_power == 0


def test_a_full_battery_on_the_adapter_is_charged_and_plugged(tmp_path):
    write_battery(tmp_path, percent=100, status="Full", plugged=True)
    battery = helper.read_battery(tmp_path)
    assert (battery.is_charged, battery.is_plugged, battery.percent) == (True, True, 100)


def test_an_ac_only_tree_is_no_battery(tmp_path):
    make_supply(tmp_path, "AC", type="Mains", online="1")
    battery = helper.read_battery(tmp_path)
    assert battery.battery_present is False and battery.is_plugged is True


def test_two_batteries_the_first_present_one_is_read(tmp_path):
    write_battery(tmp_path, percent=80, status="Discharging", plugged=False)
    make_supply(tmp_path, "BAT1", type="Battery", present="1", capacity="20", status="Discharging")
    assert helper.read_battery(tmp_path).percent == 80


def test_a_first_battery_that_is_not_present_is_skipped(tmp_path):
    make_supply(tmp_path, "BAT0", type="Battery", present="0", capacity="5", status="Discharging")
    make_supply(tmp_path, "BAT1", type="Battery", present="1", capacity="66", status="Discharging")
    assert helper.read_battery(tmp_path).percent == 66


# --- The lid: logind's LidClosed through busctl ---

@pytest.mark.parametrize(("stdout", "closed"), [("b true\n", True), ("b false\n", False)])
def test_the_lid_state_is_logind_lid_closed(stdout, closed):
    seen = []

    def run(argv, **kwargs):
        seen.append(argv)
        return subprocess.CompletedProcess(argv, 0, stdout=stdout, stderr="")

    assert helper.read_lid_closed(run=run) is closed
    assert seen[0][0] == "busctl" and seen[0][-1] == "LidClosed"


def test_unexpected_lid_output_is_an_error_not_a_guess():
    def run(argv, **kwargs):
        return subprocess.CompletedProcess(argv, 0, stdout="s nonsense\n", stderr="")

    with pytest.raises(ValueError):
        helper.read_lid_closed(run=run)


def test_a_missing_busctl_disables_only_the_lid(monkeypatch):
    monkeypatch.setenv("PATH", "/nonexistent")
    caps = helper.probe_capabilities()
    assert caps["lid"]["ok"] is False and "busctl" in caps["lid"]["reason"]
    assert caps["battery"]["ok"] is True


def test_the_battery_and_lid_capabilities_are_probed():
    caps = helper.probe_capabilities()
    assert caps["battery"]["ok"] is True, caps["battery"]["reason"]
    assert caps["lid"]["ok"] is True, caps["lid"]["reason"]


def test_copied_lid_rules_are_unchanged_from_upstream():
    for name in ("lid_closed_led_state_should_hold", "lid_close_animation_should_run"):
        assert function_ast(Path(helper.__file__), name) == function_ast(upstream_status_bar(), name), name


# --- The Battery display (D9): the LEDs show upstream's battery program ---

def test_the_battery_display_drives_the_device_with_upstreams_battery_program(world):
    write_battery(world.power, percent=50, status="Discharging", plugged=False)
    world.h.tick()
    set_display(world, "battery")
    assert upstream_value(world) == "custom"
    # 50 %: LED 0 is amber (<= 50 %), LED 1 dark; upstream's segment syntax, at full brightness.
    assert shown(world) == "0:#FFB000 360ms ease;1:#000000 360ms ease"


def test_battery_without_a_battery_falls_back_to_agent_and_leaves_the_device_alone(world):
    set_display(world, "battery")
    world.h.tick()
    assert upstream_value(world) == "agent"
    assert not world.led_file.exists()
    assert events(world.out, "devices")[-1]["items"][0]["display"] == "agent"


def test_an_unreadable_battery_shows_the_device_dark_and_says_why(world):
    make_supply(world.power, "BAT0", type="Battery", present="1", capacity="lots", status="Discharging")
    set_display(world, "battery")
    world.h.tick()
    assert upstream_value(world) == "custom"
    assert shown(world) == off_program()
    assert any("cannot read the battery" in m for m in errors_for(world.out, "power"))


def test_the_power_event_says_whether_the_battery_display_is_offered(world):
    world.h.tick()
    assert events(world.out, "power")[-1]["battery_offered"] is False
    write_battery(world.power, percent=50, status="Discharging", plugged=False)
    world.h.tick()
    last = events(world.out, "power")[-1]
    assert last["battery_offered"] is True and last["percent"] == 50 and last["plugged"] is False


def test_off_stays_off_under_a_battery_reading(world):
    write_battery(world.power, percent=50, status="Discharging", plugged=False)
    set_display(world, "off")
    world.h.tick()
    assert upstream_value(world) == "custom"
    assert shown(world) == off_program()


# --- The 7 s preview on plug and unplug (upstream's battery_show_on_power_change) ---

def test_plugging_in_shows_the_battery_for_seven_seconds_on_an_agent_device(world):
    set_display(world, "agent")
    write_battery(world.power, percent=50, status="Discharging", plugged=False)
    world.h.tick()
    write_battery(world.power, percent=51, status="Charging", plugged=True, power_now=30_000_000)
    world.h.tick()
    assert upstream_value(world) == "custom"
    assert "repeat" in shown(world)  # the charging program, upstream's
    world.now[0] += 6.9
    world.h.tick()
    assert upstream_value(world) == "custom"
    world.now[0] += 0.2
    world.h.tick()
    assert upstream_value(world) == "agent"


def test_the_preview_is_seven_seconds_by_upstreams_default():
    assert load_settings().battery_power_change_preview_seconds == 7.0


def test_no_preview_when_upstream_turns_it_off(world):
    upstream_settings.save_settings(load_settings().with_battery_power_change_preview(enabled=False))
    write_battery(world.power, percent=50, status="Discharging", plugged=False)
    world.h.tick()
    write_battery(world.power, percent=51, status="Charging", plugged=True, power_now=30_000_000)
    world.h.tick()
    assert upstream_value(world) == "agent"


def test_the_preview_never_touches_an_off_device(world):
    write_battery(world.power, percent=50, status="Discharging", plugged=False)
    set_display(world, "off")
    world.h.tick()
    write_battery(world.power, percent=51, status="Charging", plugged=True, power_now=30_000_000)
    world.h.tick()
    assert shown(world) == off_program()


# --- The lid animations (upstream's lid handling, D10) ---

def closed_program() -> str:
    return upstream_settings.DEFAULT_LID_CLOSED_ANIMATION_PROGRAM


def open_program() -> str:
    return upstream_settings.DEFAULT_LID_OPEN_ANIMATION_PROGRAM


def test_closing_the_lid_plays_the_closed_animation_then_holds_dark(world):
    set_display(world, "agent")
    upstream_settings.save_settings(load_settings().with_sleep_prevention_policy("never"))
    world.h.active_count = 0  # the State is known: upstream holds the LEDs only when it is
    world.h.tick()  # first read: the lid is open, no animation
    world.lid["closed"] = True
    world.h.tick()
    assert upstream_value(world) == "custom"
    assert shown(world) == closed_program()
    assert world.h.next_handover == pytest.approx(1000.0 + 1.3 + LID_ANIMATION_FUDGE)
    world.now[0] += 1.3 + LID_ANIMATION_FUDGE + 0.01
    world.h.tick()
    assert upstream_value(world) == "custom"  # held: the lid is still closed and sleep is allowed
    assert shown(world) == off_program()


def test_opening_the_lid_plays_the_open_animation_then_hands_back(world):
    set_display(world, "agent")
    upstream_settings.save_settings(load_settings().with_sleep_prevention_policy("never"))
    world.lid["closed"] = True
    world.h.tick()
    world.now[0] += 2.0
    world.h.tick()
    world.lid["closed"] = False
    world.h.tick()
    assert shown(world) == open_program()
    world.now[0] += 1.0 + LID_ANIMATION_FUDGE + 0.01
    world.h.tick()
    assert upstream_value(world) == "agent"


def test_a_closed_lid_under_always_keeps_the_device_lit_and_plays_nothing(world):
    set_display(world, "agent")
    upstream_settings.save_settings(load_settings().with_sleep_prevention_policy("always"))
    world.h.tick()
    world.lid["closed"] = True
    world.h.tick()
    assert upstream_value(world) == "agent"
    assert not world.led_file.exists()


def test_under_agents_a_closed_lid_with_agents_working_plays_nothing(world):
    set_display(world, "agent")
    upstream_settings.save_settings(load_settings().with_sleep_prevention_policy("agents"))
    world.h.tick()
    # A tick re-reads the State, so the working agent is set after it and the lid steps run on their own.
    world.h.active_count = 1
    world.lid["closed"] = True
    world.h.refresh_power()
    world.h.refresh_devices()
    assert world.h.lid_animation is None
    assert upstream_value(world) == "agent"


def test_the_lid_animation_does_not_touch_an_off_device(world):
    set_display(world, "off")
    world.h.tick()
    world.lid["closed"] = True
    world.h.tick()
    assert shown(world) == off_program()


def test_an_invalid_lid_animation_is_reported_and_stays_reported_until_it_plays(world):
    upstream_settings.save_settings(load_settings().with_sleep_prevention_policy("never"))
    # Upstream's own check (validate_led_text) refuses a program over its line limit; a blank one is replaced by the default on load.
    too_many_lines = "\n".join(["#000000 1ms"] * (MAX_LED_LINES + 1))
    upstream_settings.save_settings(load_settings().with_lid_animation(
        upstream_settings.LID_ANIMATION_CLOSED, program=too_many_lines, duration_seconds=1.0))
    set_display(world, "agent")
    world.h.tick()
    world.lid["closed"] = True
    world.h.tick()
    world.h.tick()
    assert any("invalid" in m for m in errors_for(world.out, "lid_animation"))
    assert not any("invalid" in m for m in errors_for(world.out, "lid"))
    # No animation, but the closed lid may sleep, so the hold keeps the device dark.
    assert upstream_value(world) == "custom"
    assert shown(world) == off_program()


def test_a_lid_read_failure_is_reported_and_plays_nothing(world):
    def broken():
        raise OSError("no busctl answer")

    world.h.lid_reader = broken
    set_display(world, "agent")
    world.h.tick()
    assert any("no busctl answer" in m for m in errors_for(world.out, "lid"))
    assert upstream_value(world) == "agent"


@pytest.mark.parametrize(("module", "name"), [("battery", "program_for_battery"), ("led_status", "apply_brightness")])
def test_a_missing_battery_or_lid_function_leaves_agent_and_off_working(world, monkeypatch, module, name):
    # Upstream version skew turns off only the feature that needs the function (D14), not the device reconcile.
    monkeypatch.delattr(getattr(sidepulse, module), name)
    world.h.capabilities = helper.probe_capabilities()
    assert world.h.capabilities["devices"]["ok"] is True
    write_battery(world.power, percent=50, status="Discharging", plugged=False)
    set_display(world, "off")
    world.h.tick()
    assert shown(world) == off_program()
    set_display(world, "agent")
    world.h.tick()
    assert upstream_value(world) == "agent"
    assert not errors_for(world.out, "devices")


def test_the_battery_display_shows_the_lid_closed_hold_too(world):
    write_battery(world.power, percent=50, status="Discharging", plugged=False)
    set_display(world, "battery")
    upstream_settings.save_settings(load_settings().with_sleep_prevention_policy("never"))
    world.h.active_count = 0
    world.h.tick()
    world.lid["closed"] = True
    world.h.tick()
    world.now[0] += 1.3 + LID_ANIMATION_FUDGE + 0.01
    world.h.tick()
    assert shown(world) == off_program()


# --- The icon and the rows (D12): the battery program flows while the popup is closed ---

def test_the_icon_plays_the_battery_program_with_the_popup_closed(world):
    write_battery(world.power, percent=50, status="Discharging", plugged=False)
    set_display(world, "battery")
    world.h.tick()
    world.h.popup_open = False
    # The program fades in from dark from its first frame, so a frame well after the first shows the battery's colour.
    world.h.pump_frames(world.now[0])
    world.h.pump_frames(world.now[0] + 2.0)
    icon = [e for e in events(world.out, "frame") if e["target"] == "icon"]
    assert icon and icon[-1]["leds"] is not None
    assert icon[-1]["leds"][0] == [255, 176, 0]  # #FFB000 at full brightness


# --- Hand-over: exit and crash repair (CODING_STANDARDS: one helper releases its takeover) ---

def test_exit_hands_a_battery_device_back_and_keeps_off(world):
    write_battery(world.power, percent=50, status="Discharging", plugged=False)
    set_display(world, "battery")
    world.h.tick()
    assert upstream_value(world) == "custom"
    world.h.release_takeovers()
    assert upstream_value(world) == "agent"


def test_exit_keeps_an_off_device_off(world):
    set_display(world, "off")
    world.h.tick()
    world.h.release_takeovers()
    assert upstream_value(world) == "custom"


def test_a_takeover_left_by_a_crash_is_repaired_on_start(world):
    upstream_settings.save_settings(load_settings().with_device_display(world.dot_id, "custom"))
    set_display(world, "agent")
    assert upstream_value(world) == "agent"
