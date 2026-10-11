"""Reference cases for the Bar LEDs engine (D6), moved here from the removed spikes/wasm/compare.py.

The cases are built from upstream's own program builders (program_for_agent_mode,
program_for_battery, the built-in animations) and upstream's test vectors, so
tests/test_bar_leds.py checks the helper's engine against upstream's output
rather than against a copy of it.
"""

from __future__ import annotations

from sidepulse.battery import BatterySnapshot, program_for_battery
from sidepulse.led_status import builtin_animation_program, program_for_agent_mode
from sidepulse.models import AgentMode
from sidepulse.settings import AGENT_ANIMATION_BUILT_INS

TIMES_MS = [0, 16, 100, 500, 1000, 2500]
LED_COUNTS = [2, 8]
INVALID_PROGRAMS = {
    "invalid-word": "bogus",
    "invalid-hex": "#GG0000",
    "invalid-trailing-token": "0:#FF0000 100ms pulse 0ms extra",
    "invalid-index-large": "9:#FF0000 100ms pulse 0ms\nrepeat",
    "invalid-brightness": "brightness 300\n#ffffff",
    "invalid-too-long": "#FF0000 " + "x" * 600,
    "invalid-too-many-lines": "\n".join(["#ff0000"] * 30),
    "invalid-bad-repeat": "repeat\n#ff0000",
}


def build_cases():
    """Every program the helper can send, for 2 and 8 LEDs, with the colour it must show when it is uniform."""
    custom = "off\n#FF00FF 1s pulse\nrepeat"
    cases: list[dict] = []
    for n in LED_COUNTS:
        def add(name, program, kind, expect_all=None):
            cases.append({"name": f"{name}/{n}", "kind": kind, "led_count": n, "program": program,
                          "expect_all": expect_all})

        add("off", "off", "off", expect_all=(0, 0, 0))
        add("valid-pulse-no-delay", "0:#FF0000 100ms pulse", "valid-parse")
        add("solid-00FF66", "#00FF66", "solid", expect_all=(0, 255, 102))
        for mode in AgentMode:
            prog = program_for_agent_mode(mode, led_count=n)
            add(f"agent-mode-{mode.name}", prog, "agent-mode")
            add(f"agent-mode-{mode.name}-b96", program_for_agent_mode(mode, led_count=n, brightness=96), "agent-mode")
        for style in AGENT_ANIMATION_BUILT_INS:
            if style in ("default",):
                continue
            prog = program_for_agent_mode(AgentMode.WORKING, led_count=n, animation_style=style, custom_program=custom)
            add(f"agent-style-{style}", prog, "agent-style")
        prog = program_for_agent_mode(AgentMode.WORKING, led_count=n, animation_style="custom", custom_program=custom)
        add("agent-style-custom", prog, "agent-style")
        for label, snap, watts in [
            ("battery-unplugged-5", BatterySnapshot(percent=5, is_plugged=False), None),
            ("battery-unplugged-45", BatterySnapshot(percent=45, is_plugged=False), None),
            ("battery-unplugged-80", BatterySnapshot(percent=80, is_plugged=False), None),
            ("battery-charged-100", BatterySnapshot(percent=100, is_plugged=True, is_charging=False, is_charged=True), None),
            ("battery-charging-40", BatterySnapshot(percent=40, is_plugged=True, is_charging=True, battery_watts=30.0), 60.0),
            ("battery-charging-75", BatterySnapshot(percent=75, is_plugged=True, is_charging=True, battery_watts=55.0), 60.0),
        ]:
            add(label, program_for_battery(snap, led_count=n, full_charge_watts=watts), "battery")
        add("battery-unplugged-45-b96",
            program_for_battery(BatterySnapshot(percent=45, is_plugged=False), led_count=n, brightness=96), "battery")
        for label, prog in INVALID_PROGRAMS.items():
            add(label, prog, "invalid")
    return cases


# Upstream test vectors (tests/test_sidepulse.py::test_led_wasm_controller_*), used verbatim.
def upstream_vector_cases():
    cyan_roll = builtin_animation_program("cyan-roll", 8)
    solid_green = builtin_animation_program("solid-green", 8)
    lid_closed = builtin_animation_program("lid-closed", 8)
    g = (0, 255, 102)
    k = (0, 0, 0)
    return [
        {"name": "upstream/brightness-128-00FF66/8", "led_count": 8,
         "ops": [["parse", "brightness 128\n#00FF66", 0], ["step", 0]],
         "expect": {1: [(0, 128, 51)] * 8}},
        {"name": "upstream/positional-2led/2", "led_count": 2,
         "ops": [["parse", "0:#FF0000; 1:#00FF00; 7:#FFFFFF", 0], ["step", 0]],
         "expect": {1: [(255, 0, 0), (0, 255, 0)]}},
        {"name": "upstream/core-transition/8", "led_count": 8,
         "ops": [["parse", cyan_roll, 0], ["step", 700], ["parse", solid_green, 700], ["step", 700],
                 ["step", 860], ["step", 1020], ["parse", lid_closed, 1020], ["step", 1020],
                 ["step", 1095], ["step", 1170], ["step", 1245], ["step", 1320]],
         "expect": {3: "=1", 5: [g] * 8, 7: [g] * 8,
                    8: [k, g, g, g, g, g, g, k], 9: [k, k, g, g, g, g, k, k],
                    10: [k, k, k, g, g, k, k, k], 11: [k] * 8}},
    ]
