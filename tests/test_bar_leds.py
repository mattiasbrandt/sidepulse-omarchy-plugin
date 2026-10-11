"""Bar LEDs (D6, D7, D21): upstream's sdled.wasm through wasmtime, and the frames the helper streams.

The engine is checked against the reference cases of spikes/wasm/compare.py and
upstream's own test vectors. The program each State shows is upstream's
`program_for_agent_mode`, called the way upstream's notch calls it. Constants
that upstream keeps in a module importing AppKit are read from its source and
compared with the copies here (the same method as tests/test_state.py).
"""

from __future__ import annotations

import ast
import io
import json
import math
import os
import select
import subprocess
import sys
import time
from pathlib import Path

import pytest
from sidepulse.led_status import program_for_agent_mode
from sidepulse.led_wasm import ERROR_NAMES
from sidepulse.models import AgentMode
from sidepulse.settings import AgentAnimationSetting, AgentMonitorSettings, CustomAgentAnimation

import sidepulse_omarchy as helper

ROOT = Path(__file__).resolve().parent.parent
HELPER = ROOT / "helper" / "sidepulse_omarchy.py"
SPIKE_DIR = ROOT / "spikes" / "wasm"
VECTORS = Path(__file__).resolve().parent / "barleds_vectors.json"


def upstream_virtual_device_source() -> ast.Module:
    import sidepulse

    return ast.parse((Path(sidepulse.__file__).parent / "virtual_device.py").read_text())


def upstream_constants(tree: ast.Module, names: set[str]) -> dict[str, float]:
    found: dict[str, float] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
            if isinstance(target, ast.Name) and target.id in names:
                found[target.id] = ast.literal_eval(node.value)
    return found


# --- Engine (D6) ---------------------------------------------------------------

def _compare_module():
    sys.path.insert(0, str(SPIKE_DIR))
    try:
        import compare
    finally:
        sys.path.remove(str(SPIKE_DIR))
    return compare


def test_engine_matches_every_reference_case_of_compare_py():
    """The 117 reference cases (off, every AgentMode, built-in styles, battery, invalid, 2 and 8 LEDs)."""
    compare = _compare_module()
    cases = compare.build_cases(None)
    for case in cases:
        if case["kind"] == "upstream":
            continue
        engine = helper.SdLedWasm(case["led_count"])
        assert engine.parse(case["program"], 0).ok == (case["kind"] != "invalid"), case["name"]
        for time_ms in compare.TIMES_MS:
            frame = engine.step(time_ms)
            if case["expect_all"] is not None:
                assert frame == [list(case["expect_all"])] * case["led_count"], (case["name"], time_ms)


def test_invalid_programs_report_upstreams_error_names():
    compare = _compare_module()
    # Names the engine reports for the cases whose cause is unambiguous; every case must name one of upstream's errors.
    expected = {"invalid-hex": "bad-color", "invalid-brightness": "bad-brightness",
                "invalid-too-long": "too-long", "invalid-too-many-lines": "too-many-lines"}
    engine = helper.SdLedWasm(8)
    for name, program in compare.INVALID_PROGRAMS.items():
        result = engine.parse(program, 0)
        assert result.ok is False, name
        assert result.error_name in ERROR_NAMES[1:], (name, result.error_name)
        if name in expected:
            assert result.error_name == expected[name], (name, result.error_name)


@pytest.mark.parametrize("vector", [
    "upstream/brightness-128-00FF66/8",
    "upstream/positional-2led/2",
    "upstream/core-transition/8",
])
def test_engine_matches_upstream_test_vectors(vector):
    """tests/test_sidepulse.py::test_led_wasm_controller_* in upstream, replayed through the helper's engine."""
    compare = _compare_module()
    case = next(c for c in compare.upstream_vector_cases(None) if c["name"] == vector)
    engine = helper.SdLedWasm(case["led_count"])
    frames: dict[int, list[tuple]] = {}
    for index, op in enumerate(case["ops"]):
        if op[0] == "parse":
            assert engine.parse(op[1], op[2]).ok is True, (vector, index)
            continue
        frames[index] = [tuple(t) for t in engine.step(op[1])]
        expected = case["expect"].get(index)
        if expected == "=1":  # a step after re-parsing equals step 1, as upstream's test asserts
            assert frames[index] == frames[1], (vector, index)
        elif expected is not None:
            assert frames[index] == [tuple(t) for t in expected], (vector, index)


def test_the_engine_loads_sdled_wasm_from_the_installed_upstream_package():
    from importlib.resources import files

    installed = files("sidepulse.resources").joinpath("sdled.wasm").read_bytes()
    default = helper.SdLedWasm(8)
    explicit = helper.SdLedWasm(8, wasm_bytes=installed)
    program = "off 320ms cosine\n#FF0000 100ms pulse\nrepeat"
    assert default.parse(program, 0).ok is True
    assert explicit.parse(program, 0).ok is True
    assert default.step(40) == explicit.step(40)


def test_one_step_is_far_inside_the_frame_budget():
    """Budget (spec, D6): 0.017 ms per frame measured; this bound only catches a gross regression."""
    engine = helper.SdLedWasm(8)
    engine.parse("off 320ms cosine\n0:#00E5FF 760ms pulse 0ms; 7:#00E5FF 760ms pulse 95ms\nrepeat", 0)
    samples = []
    for index in range(600):
        started = time.perf_counter()
        engine.step(index * 16)
        samples.append((time.perf_counter() - started) * 1000.0)
    samples.sort()
    mean = sum(samples) / len(samples)
    assert mean < helper.FRAME_BUDGET_MS, f"mean step {mean:.4f} ms"


# --- Constants that upstream keeps in an AppKit module (drift) -------------------

def test_frame_rates_match_upstream_virtual_device():
    tree = upstream_virtual_device_source()
    found = upstream_constants(tree, {"FRAME_RATE", "IDLE_FRAME_RATE"})
    assert found == {"FRAME_RATE": helper.FRAME_RATE, "IDLE_FRAME_RATE": helper.IDLE_FRAME_RATE}


def test_the_notch_device_id_matches_upstream_virtual_device():
    found = upstream_constants(upstream_virtual_device_source(), {"VIRTUAL_DEVICE_ID"})
    assert found == {"VIRTUAL_DEVICE_ID": helper.NOTCH_DEVICE_ID}


BLEND_NAMES = {"LED_BLEND_RADIUS_LEDS", "BLEND_COLUMN_WIDTH", "LED_GAMMA", "LED_CORE_BOOST",
               "LED_HOTLINE_BOOST", "LED_BAND_HEIGHT", "WINDOW_WIDTH"}


def test_band_constants_in_barleds_mjs_match_upstream():
    import re

    upstream = upstream_constants(upstream_virtual_device_source(), BLEND_NAMES)
    assert set(upstream) == BLEND_NAMES
    source = (ROOT / "helper" / "barleds.mjs").read_text()
    local = {m.group(1): float(m.group(2))
             for m in re.finditer(r"^export const ([A-Z_]+) = ([0-9.]+);", source, re.M)}
    for name in BLEND_NAMES:
        assert local.get(name) == pytest.approx(upstream[name]), name


def test_blend_vectors_match_upstream_functions():
    """The blend and tone-map functions, run from upstream's source, give the vectors the QML tests check."""
    tree = upstream_virtual_device_source()
    namespace: dict = {"math": math}
    namespace.update(upstream_constants(tree, BLEND_NAMES))
    wanted = {"blended_led_color_at_x", "tone_mapped_led_color"}
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in wanted:
            exec(compile(ast.Module(body=[node], type_ignores=[]), "virtual_device.py", "exec"), namespace)
    blend = namespace["blended_led_color_at_x"]
    tone = namespace["tone_mapped_led_color"]

    fixture = json.loads(VECTORS.read_text())
    colours = [tuple(c) for c in fixture["colours"]]
    for case in fixture["blend"]:
        got = blend(colours, case["x"], case["led_width"])
        assert got == pytest.approx(tuple(case["expect"])), case
    for case in fixture["tone"]:
        got = tone(*case["in"], boost=case["boost"], alpha_scale=case["alpha_scale"])
        assert got == pytest.approx(tuple(case["expect"])), case


# --- Program per State (D4, D21) ----------------------------------------------

def test_the_band_shows_upstreams_notch_program_for_every_agent_mode():
    settings = AgentMonitorSettings()
    for mode in AgentMode:
        for count in (2, 8):
            animation = settings.agent_animation(mode)
            expected = program_for_agent_mode(
                mode, led_count=count, brightness=255,
                animation_style=animation.style, custom_program=animation.custom_program)
            assert helper.bar_program(settings, mode, count) == expected, (mode, count)


def test_the_band_follows_a_custom_animation_the_user_chose():
    """Upstream's per-State animation choice (style and custom program) reaches the band."""
    settings = AgentMonitorSettings(
        agent_animations={"working": AgentAnimationSetting(style="mine")},
        custom_agent_animations={"mine": CustomAgentAnimation(name="Mine", program="#FF0000\nrepeat",
                                                              file_name="mine.LED")})
    animation = settings.agent_animation(AgentMode.WORKING)
    assert animation.style == "custom"
    assert helper.bar_program(settings, AgentMode.WORKING, 8) == program_for_agent_mode(
        AgentMode.WORKING, led_count=8, brightness=255, animation_style="custom",
        custom_program=animation.custom_program)


@pytest.mark.parametrize("devices,count", [
    ([], 8),
    ([("dot", 2)], 2),
    ([("pro", 8)], 8),
    ([("dot", 2), ("pro", 8)], 8),
])
def test_band_led_count_follows_the_connected_product(devices, count):
    """D21: a SidePulse Dot shows 2 LEDs, a Pro 8, and none or both 8 (upstream prefers the Pro)."""
    fake = [helper.Device(id=f"/m/{kind}", name=kind, path=f"/m/{kind}", target=Path("/m/LEDS.LED"),
                          kind=kind, led_count=n) for kind, n in devices]
    assert helper.bar_led_count(fake) == count


# --- Rate per State (upstream set_state: animated 60 fps, idle 12 fps) ----------

@pytest.mark.parametrize("mode", [AgentMode.WORKING, AgentMode.TOOL_RUNNING, AgentMode.LONG_TASK_PROGRESS,
                                  AgentMode.WAITING_FOR_INPUT, AgentMode.BLOCKED_ERROR])
def test_working_and_ask_frames_run_at_60_fps(mode):
    assert helper.frame_interval_for(mode) == pytest.approx(1 / 60)


@pytest.mark.parametrize("mode", [AgentMode.IDLE_READY, AgentMode.COMPLETED, AgentMode.UNKNOWN])
def test_idle_and_done_frames_run_at_12_fps(mode):
    assert helper.frame_interval_for(mode) == pytest.approx(1 / 12)


# --- The helper's frame stream -------------------------------------------------

@pytest.fixture
def scratch(tmp_path, monkeypatch):
    """A private HOME, XDG dirs and mount root: the real settings.json and devices are never read or written."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("SIDEPULSE_MOUNT_ROOTS", str(tmp_path / "media"))
    (tmp_path / "media").mkdir()
    return tmp_path


def write_latest(tmp: Path, mode: str) -> None:
    from datetime import datetime, timezone

    directory = tmp / "state" / "sidepulse" / "agent-monitor"
    directory.mkdir(parents=True, exist_ok=True)
    row = {"provider": "claude", "agent_id": "claude:session:abc", "display_name": "demo", "mode": mode,
           "mode_label": mode, "updated_at": datetime.now(timezone.utc).isoformat(),
           "event_name": "PreToolUse", "session_id": "abc", "cwd": "/tmp/demo", "stale": False}
    tmp_path = directory / "latest.json.tmp"
    tmp_path.write_text(json.dumps({"updated_at": row["updated_at"], "statuses": [row]}))
    os.replace(tmp_path, directory / "latest.json")


def write_settings(tmp: Path, bar_leds: bool) -> None:
    path = tmp / "config" / "sidepulse" / "agent-monitor" / "settings.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"virtual_status_device_enabled": bar_leds}))


def make_helper(tmp: Path, out: io.StringIO) -> helper.Helper:
    h = helper.Helper(out)
    h.hello()
    h.refresh(force=True)
    return h


def frame_events(out: io.StringIO) -> list[dict]:
    return [json.loads(line) for line in out.getvalue().splitlines() if json.loads(line)["type"] == "frame"]


def test_frames_are_emitted_only_when_the_bar_changes(scratch):
    write_latest(scratch, "working")
    write_settings(scratch, True)
    out = io.StringIO()
    h = make_helper(scratch, out)
    out.seek(0)
    out.truncate()
    h.pump_frames(1.0)
    h.pump_frames(1.0 + 1 / 60)
    first = [e for e in frame_events(out) if e["target"] == "bar"]
    assert len(first) >= 1
    assert all(len(e["leds"]) == 8 for e in first)
    out.seek(0)
    out.truncate()
    write_latest(scratch, "completed")
    h.refresh_state()
    # Upstream's solid programs open with a fade from what the LEDs showed, so frames change for a while, then stop.
    for step in range(60):
        h.pump_frames(2.0 + step / 12)
    settled = frame_events(out)
    assert settled, "the change of State is sent"
    out.seek(0)
    out.truncate()
    for step in range(12):
        h.pump_frames(7.0 + step / 12)
    assert [e for e in frame_events(out) if e["target"] == "bar"] == [], "an unchanged frame is not sent again"


def test_disabled_bar_leds_send_no_colour(scratch):
    write_latest(scratch, "working")
    write_settings(scratch, False)
    out = io.StringIO()
    h = make_helper(scratch, out)
    out.seek(0)
    out.truncate()
    h.pump_frames(1.0)
    events = [e for e in frame_events(out) if e["target"] == "bar"]
    assert events == [{"type": "frame", "target": "bar", "leds": None}]
    assert h.bar_program_text is None


def test_unreadable_state_shows_no_band(scratch):
    write_latest(scratch, "working")
    write_settings(scratch, True)
    out = io.StringIO()
    h = make_helper(scratch, out)
    (scratch / "state" / "sidepulse" / "agent-monitor" / "latest.json").write_text("not json")
    h.refresh_state()
    out.seek(0)
    out.truncate()
    h.pump_frames(3.0)
    events = [e for e in frame_events(out) if e["target"] == "bar"]
    assert events == [{"type": "frame", "target": "bar", "leds": None}]


def test_an_upstream_without_the_bar_led_names_keeps_the_rest_of_the_helper_running(scratch, monkeypatch):
    """D14: a missing Bar LED name disables only the Bar LEDs. The State and keep-awake paths must not
    import it, or the helper dies on the first tick (Codex review of #39)."""
    import sidepulse.led_status

    monkeypatch.delattr(sidepulse.led_status, "display_state_for_mode")
    write_latest(scratch, "working")
    write_settings(scratch, True)
    out = io.StringIO()
    h = make_helper(scratch, out)
    h.refresh_state()
    h.refresh_awake()
    h.pump_frames(1.0)
    events = [json.loads(line) for line in out.getvalue().splitlines()]
    kinds = {event["type"] for event in events}
    assert "state" in kinds and "awake" in kinds, kinds
    assert h.capabilities["bar_leds"]["ok"] is False
    assert "display_state_for_mode" in h.capabilities["bar_leds"]["reason"]
    assert any(e["type"] == "error" and e["feature"] == "bar_leds" for e in events)
    assert frame_events(out) == []


def test_missing_wasmtime_sends_no_frames_and_names_the_capability(scratch, monkeypatch):
    monkeypatch.setitem(sys.modules, "wasmtime", None)
    write_latest(scratch, "working")
    write_settings(scratch, True)
    out = io.StringIO()
    h = make_helper(scratch, out)
    h.pump_frames(1.0)
    assert frame_events(out) == []
    errors = [json.loads(line) for line in out.getvalue().splitlines()
              if json.loads(line)["type"] == "error"]
    assert any(e["feature"] == "bar_leds" and "wasmtime" in e["message"] for e in errors), errors
    assert h.capabilities["bar_leds"]["ok"] is False


def popup_command(helper_obj: helper.Helper, open_: object) -> None:
    helper_obj.handle_line(json.dumps({"cmd": "set-popup", "open": open_}).encode())


def mount_dot(scratch: Path) -> Path:
    mount = scratch / "media" / "PulseDot"
    mount.mkdir(parents=True, exist_ok=True)
    (mount / "LEDS.LED").write_text("#00E5FF\nrepeat\n")
    return mount


def test_no_device_frames_while_the_popup_is_closed(scratch):
    """Device frames are for the popup's rows only (#8 review): none flow while it is closed."""
    mount_dot(scratch)
    write_latest(scratch, "working")
    write_settings(scratch, True)
    out = io.StringIO()
    h = make_helper(scratch, out)
    out.seek(0)
    out.truncate()
    for step in range(5):
        h.pump_frames(1.0 + step / 60)
    assert [e for e in frame_events(out) if e["target"] == "device"] == []
    assert [e for e in frame_events(out) if e["target"] == "bar"], "the bar frames are unaffected"


def test_opening_the_popup_sends_the_device_frames_at_once(scratch):
    mount = mount_dot(scratch)
    write_latest(scratch, "idle_ready")
    write_settings(scratch, False)
    out = io.StringIO()
    h = make_helper(scratch, out)
    h.pump_frames(1.0)
    out.seek(0)
    out.truncate()
    popup_command(h, True)
    h.pump_frames(1.1)
    device = [e for e in frame_events(out) if e["target"] == "device"]
    assert len(device) == 1, "the current frames are sent, not suppressed as unchanged"
    assert device[0]["device"] == str(mount)
    assert len(device[0]["leds"]) == 2


def test_closing_the_popup_stops_device_frames_and_only_the_icon_device_steps(scratch, monkeypatch):
    mount_dot(scratch)
    write_latest(scratch, "working")
    write_settings(scratch, False)  # no bar frames, so every step counted is a device's
    out = io.StringIO()
    h = make_helper(scratch, out)
    steps = {"n": 0}
    real_step = helper.SdLedWasm.step

    def counting_step(self, now_ms):
        steps["n"] += 1
        return real_step(self, now_ms)

    monkeypatch.setattr(helper.SdLedWasm, "step", counting_step)
    popup_command(h, True)
    h.pump_frames(1.0)
    assert steps["n"] == 1
    popup_command(h, False)
    out.seek(0)
    out.truncate()
    steps["n"] = 0
    h.pump_frames(1.1)
    h.pump_frames(1.2)
    # The Dot is the icon's device (D12): only it steps, once per pump, and no row frame is sent.
    assert steps["n"] == 2, "a closed popup steps only the icon's device"
    assert [e for e in frame_events(out) if e["target"] == "device"] == []


def test_set_popup_needs_a_boolean(scratch):
    out = io.StringIO()
    h = make_helper(scratch, out)
    out.seek(0)
    out.truncate()
    popup_command(h, "yes")
    errors = [json.loads(line) for line in out.getvalue().splitlines() if json.loads(line)["type"] == "error"]
    assert errors and errors[0]["feature"] == "command"
    assert "set-popup needs open" in errors[0]["message"], errors


def test_devices_get_their_own_live_frames(scratch):
    """The popup's device rows show the frames of the program the device holds (D21, #5 review)."""
    mount = scratch / "media" / "PulseDot"
    mount.mkdir(parents=True)
    (mount / "LEDS.LED").write_text("#00E5FF\nrepeat\n")
    write_latest(scratch, "idle_ready")
    write_settings(scratch, False)
    out = io.StringIO()
    h = make_helper(scratch, out)
    out.seek(0)
    out.truncate()
    popup_command(h, True)  # the widget opens its popup: the rows' frames flow
    h.pump_frames(1.0)
    device = [e for e in frame_events(out) if e["target"] == "device"]
    assert len(device) == 1
    assert len(device[0]["leds"]) == 2
    assert device[0]["device"] == str(mount)
    assert any(v != 0 for led in device[0]["leds"] for v in led)


def test_a_program_that_does_not_parse_is_reported_not_drawn(scratch):
    mount = scratch / "media" / "SidePulsePro"
    mount.mkdir(parents=True)
    (mount / "LEDS.LED").write_text("bogus program\n")
    write_latest(scratch, "idle_ready")
    write_settings(scratch, False)
    out = io.StringIO()
    h = make_helper(scratch, out)
    popup_command(h, True)
    h.pump_frames(1.0)
    errors = [json.loads(line) for line in out.getvalue().splitlines() if '"type":"error"' in line]
    assert any(e["feature"] == "frames" and "syntax" in e["message"] for e in errors), errors
    device = [e for e in frame_events(out) if e["target"] == "device"]
    assert all(v == 0 for led in device[-1]["leds"] for v in led)


def test_an_engine_that_raises_is_reported_dark_and_recovers_on_the_next_frame(scratch, monkeypatch):
    write_latest(scratch, "working")
    write_settings(scratch, True)
    out = io.StringIO()
    h = make_helper(scratch, out)
    real_step = helper.SdLedWasm.step
    calls = {"n": 0}

    def flaky_step(self, now_ms):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("trap")
        return real_step(self, now_ms)

    monkeypatch.setattr(helper.SdLedWasm, "step", flaky_step)
    out.seek(0)
    out.truncate()
    h.pump_frames(1.0)
    errors = [json.loads(line) for line in out.getvalue().splitlines() if json.loads(line)["type"] == "error"]
    assert any(e["feature"] == "frames" and "engine failed" in e["message"] for e in errors), errors
    bar = [e for e in frame_events(out) if e["target"] == "bar"]
    assert bar[0]["leds"] == [[0, 0, 0]] * 8
    # The re-parsed program starts at its own time zero and opens with a 320 ms fade from off (upstream's
    # cyan roll), so the band lights once that fade has passed.
    h.pump_frames(1.5)
    h.pump_frames(2.0)
    later = [e for e in frame_events(out) if e["target"] == "bar"][1:]
    assert any(any(v for led in e["leds"] for v in led) for e in later), "the band comes back"


# --- Cadence: deadlines, not one wake-up per select (D5, spec) ----------------

class FakeClock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


class StopLoop(Exception):
    pass


def test_a_second_at_60_fps_runs_the_state_work_once_and_the_frames_sixty_times(scratch, monkeypatch):
    write_latest(scratch, "working")
    write_settings(scratch, True)
    out = io.StringIO()
    h = make_helper(scratch, out)
    clock = FakeClock()
    h.clock = clock
    counts = {"state": 0, "frames": 0, "devices": 0}
    monkeypatch.setattr(h, "refresh_state", lambda force=False: counts.__setitem__("state", counts["state"] + 1))
    monkeypatch.setattr(h, "refresh_devices", lambda force=False: counts.__setitem__("devices", counts["devices"] + 1))
    monkeypatch.setattr(h, "refresh_awake", lambda: None)
    monkeypatch.setattr(h, "refresh_setup", lambda force=False: None)
    monkeypatch.setattr(h, "refresh_hooks", lambda force=False: None)
    monkeypatch.setattr(h, "refresh_log", lambda force=False: None)
    monkeypatch.setattr(h, "pump_frames", lambda now: counts.__setitem__("frames", counts["frames"] + 1))
    h.frame_interval = 1 / 60

    def fake_select(fds, _w, _x, timeout):
        clock.now += timeout
        if clock.now >= 101.5:
            raise StopLoop
        return ([], [], [])

    monkeypatch.setattr(helper.select, "select", fake_select)
    with pytest.raises(StopLoop):
        h.loop(0)
    # One and a half simulated seconds: one tick (at 101.0), about 90 frames at 60 fps.
    assert counts["state"] == 1, counts
    assert counts["devices"] == 1, counts
    assert 88 <= counts["frames"] <= 92, counts


def test_a_frame_wakes_only_the_frame_work(scratch, monkeypatch):
    write_latest(scratch, "idle_ready")
    write_settings(scratch, True)
    out = io.StringIO()
    h = make_helper(scratch, out)
    clock = FakeClock()
    h.clock = clock
    h.frame_interval = 1 / 12
    calls = []
    monkeypatch.setattr(h, "refresh_state", lambda force=False: calls.append("state"))
    monkeypatch.setattr(h, "refresh_devices", lambda force=False: calls.append("devices"))
    monkeypatch.setattr(h, "refresh_awake", lambda: calls.append("awake"))
    monkeypatch.setattr(h, "refresh_setup", lambda force=False: None)
    monkeypatch.setattr(h, "refresh_hooks", lambda force=False: None)
    monkeypatch.setattr(h, "refresh_log", lambda force=False: None)
    monkeypatch.setattr(h, "pump_frames", lambda now: calls.append("frame"))

    def fake_select(fds, _w, _x, timeout):
        clock.now += timeout
        if clock.now >= 100.5:
            raise StopLoop
        return ([], [], [])

    monkeypatch.setattr(helper.select, "select", fake_select)
    with pytest.raises(StopLoop):
        h.loop(0)
    assert 5 <= calls.count("frame") <= 6, calls
    assert "state" not in calls and "devices" not in calls, calls


# --- The helper as the widget runs it -----------------------------------------

class ProtocolHelper:
    def __init__(self, tmp: Path) -> None:
        env = {**os.environ, "HOME": str(tmp / "home"), "XDG_CONFIG_HOME": str(tmp / "config"),
               "XDG_STATE_HOME": str(tmp / "state"), "SIDEPULSE_MOUNT_ROOTS": str(tmp / "media")}
        self.proc = subprocess.Popen([sys.executable, "-I", str(HELPER)], stdin=subprocess.PIPE,
                                     stdout=subprocess.PIPE, env=env)
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

    def send(self, obj: dict) -> None:
        self.proc.stdin.write((json.dumps(obj) + "\n").encode())
        self.proc.stdin.flush()

    def close(self) -> None:
        if self.proc.poll() is None:
            self.proc.stdin.close()
            self.proc.wait(timeout=15)


@pytest.fixture
def protocol(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    (tmp_path / "media").mkdir()
    proc = ProtocolHelper(tmp_path)
    yield proc, tmp_path
    if proc.proc.poll() is None:
        proc.proc.kill()
        proc.proc.wait()


def test_the_widget_receives_bar_frames_with_eight_leds_and_no_device(protocol):
    proc, tmp = protocol
    write_latest(tmp, "working")
    proc.send({"cmd": "set-bar-leds", "enabled": True})
    frames = []
    deadline = time.monotonic() + 15
    while len(frames) < 5 and time.monotonic() < deadline:
        event = proc.read_event()
        if event["type"] == "frame":
            frames.append(event)
    assert frames, "no frame event"
    assert all(f["target"] == "bar" and len(f["leds"]) == 8 for f in frames)
    assert any(any(v for led in f["leds"] for v in led) for f in frames)
    proc.close()


# --- Device rows play the program at full brightness ---------------------------

def test_a_device_row_plays_its_program_at_full_brightness():
    """A Dot at brightness 13 holds `brightness 13`; its row undoes that scaling, so a
    5 % device still shows its colours on screen (the slider shows the brightness)."""
    from sidepulse.led_status import apply_brightness

    authored = "brightness 200\n0:#00E5FF 760ms pulse 0ms\nrepeat"
    written = apply_brightness(authored, 13)
    assert written.splitlines()[0] == "brightness 10"
    assert helper.full_brightness_program(written, 13).splitlines()[0] == "brightness 196"


def test_a_program_without_a_brightness_line_is_left_as_it_is():
    from sidepulse.led_status import apply_brightness

    written = apply_brightness("0:#00E5FF 760ms pulse 0ms\nrepeat", 13)
    assert written.splitlines()[0] == "brightness 13"
    assert helper.full_brightness_program(written, 13).splitlines()[0] == "brightness 255"
    assert helper.full_brightness_program("0:#00E5FF\nrepeat", 255) == "0:#00E5FF\nrepeat"


def test_a_device_at_brightness_zero_plays_at_full_brightness():
    assert helper.full_brightness_program("brightness 0\n0:#00E5FF\nrepeat", 0).splitlines()[0] == "brightness 255"


def test_full_brightness_leaves_no_program_as_none():
    assert helper.full_brightness_program(None, 13) is None


# --- The bar icon plays its device's frames (D12) ------------------------------

def mount_pro(scratch: Path) -> Path:
    mount = scratch / "media" / "PulsePro"
    mount.mkdir(parents=True, exist_ok=True)
    (mount / "LEDS.LED").write_text("#FF3A00\nrepeat\n")
    return mount


def displays_command(helper_obj: helper.Helper, displays: dict) -> None:
    helper_obj.handle_line(json.dumps({"cmd": "set-displays", "displays": displays}).encode())


def test_the_icon_gets_its_device_frames_while_the_popup_is_closed(scratch):
    """The icon is always on show, so its device's frames flow with the popup closed; the rows' do not."""
    mount_dot(scratch)
    write_latest(scratch, "working")
    write_settings(scratch, False)
    out = io.StringIO()
    h = make_helper(scratch, out)
    out.seek(0)
    out.truncate()
    h.pump_frames(1.0)
    icon = [e for e in frame_events(out) if e["target"] == "icon"]
    assert len(icon) == 1
    assert len(icon[0]["leds"]) == 2
    assert any(any(channel > 0 for channel in led) for led in icon[0]["leds"]), "a lit program shows on the icon"
    assert [e for e in frame_events(out) if e["target"] == "device"] == []


def test_the_icon_follows_a_pro_when_both_are_connected(scratch):
    mount_dot(scratch)
    mount_pro(scratch)
    write_latest(scratch, "working")
    write_settings(scratch, False)
    out = io.StringIO()
    h = make_helper(scratch, out)
    out.seek(0)
    out.truncate()
    h.pump_frames(1.0)
    icon = [e for e in frame_events(out) if e["target"] == "icon"]
    assert len(icon[-1]["leds"]) == 8


def test_an_icon_device_that_is_not_showing_agent_sends_no_icon_frames(scratch):
    """Display Off: the icon keeps its State look (the widget draws it when the icon frame is null)."""
    mount = mount_dot(scratch)
    write_latest(scratch, "working")
    write_settings(scratch, False)
    out = io.StringIO()
    h = make_helper(scratch, out)
    displays_command(h, {str(mount): "off"})
    out.seek(0)
    out.truncate()
    h.pump_frames(1.0)
    icon = [e for e in frame_events(out) if e["target"] == "icon"]
    assert icon == [] or icon[-1]["leds"] is None


def test_no_device_means_no_icon_frames(scratch):
    write_latest(scratch, "working")
    write_settings(scratch, True)
    out = io.StringIO()
    h = make_helper(scratch, out)
    h.pump_frames(1.0)
    icon = [e for e in frame_events(out) if e["target"] == "icon"]
    assert icon == [] or icon[-1]["leds"] is None


def test_an_open_popup_steps_the_icon_device_once_per_frame(scratch, monkeypatch):
    """The row and the icon share one engine: one step per device per frame."""
    mount_dot(scratch)
    write_latest(scratch, "working")
    write_settings(scratch, False)
    out = io.StringIO()
    h = make_helper(scratch, out)
    popup_command(h, True)
    steps = {"n": 0}
    real_step = helper.SdLedWasm.step

    def counting_step(self, now_ms):
        steps["n"] += 1
        return real_step(self, now_ms)

    monkeypatch.setattr(helper.SdLedWasm, "step", counting_step)
    h.pump_frames(1.0)
    assert steps["n"] == 1
    assert [e for e in frame_events(out) if e["target"] == "device"]
    assert [e for e in frame_events(out) if e["target"] == "icon"]
