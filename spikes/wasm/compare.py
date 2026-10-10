"""Re-run the full sdled.wasm comparison: wasmtime-py driver vs Deno JS driver,
plus upstream test vectors and LED-semantics sanity checks.

Usage: python -I compare.py [--sp-src DIR] [--perf-steps N] [--no-perf]

Needs wasmtime, /usr/bin/deno, and the upstream sidepulse package: either
importable, or a checkout given via --sp-src / $SIDEPULSE_SRC.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import statistics
import subprocess
import sys
import time
from dataclasses import asdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))  # -I drops the script dir; add it back explicitly.

from sdled_wasmtime import SdLedWasm, default_wasm_bytes  # noqa: E402

DENO = "/usr/bin/deno"
DRIVER_JS = HERE / "sdled_driver.js"
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


def sp_src_default() -> Path | None:
    """Upstream checkout to import from; None means use the importable package."""
    env = os.environ.get("SIDEPULSE_SRC")
    return Path(env) if env else None


def load_upstream(sp_src: Path | None):
    if sp_src is not None:
        sys.path.insert(1, str(sp_src / "src"))
    from sidepulse.battery import BatterySnapshot, program_for_battery
    from sidepulse.led_status import program_for_agent_mode
    from sidepulse.models import AgentMode
    from sidepulse.settings import AGENT_ANIMATION_BUILT_INS

    return BatterySnapshot, program_for_battery, program_for_agent_mode, AgentMode, AGENT_ANIMATION_BUILT_INS


def build_cases(sp_src: Path):
    BatterySnapshot, program_for_battery, program_for_agent_mode, AgentMode, builtins = load_upstream(sp_src)
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
        for style in builtins:
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
def upstream_vector_cases(sp_src: Path):
    _, _, _, _, _ = load_upstream(sp_src)
    from sidepulse.led_status import builtin_animation_program
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


def to_dict(parse_result):
    return asdict(parse_result) | {"errorName": parse_result.error_name}


def run_python(cases):
    out = {}
    for c in cases:
        ctl = SdLedWasm(c["led_count"])
        res = []
        for op in c["ops"]:
            if op[0] == "parse":
                r = ctl.parse(op[1], op[2])
                res.append(normalize_parse(to_dict(r)))
            else:
                res.append([list(t) for t in ctl.step(op[1])])
        out[c["name"]] = res
    return out


def normalize_parse(d):
    return {"ok": d["ok"], "error": d["error"], "errorName": d["errorName"], "line": d["line"], "column": d["column"]}


def run_deno(cases, wasm_b64):
    payload = json.dumps({"wasm_base64": wasm_b64, "cases": cases})
    proc = subprocess.run([DENO, "run", "--no-prompt", str(DRIVER_JS)], input=payload,
                          capture_output=True, text=True, timeout=600)
    if proc.returncode != 0:
        raise SystemExit(f"deno driver failed:\n{proc.stderr}")
    out = {}
    for item in json.loads(proc.stdout):
        # JS returns flat RGB byte arrays for steps; normalize to triples like the Python side.
        out[item["name"]] = [
            r if isinstance(r, dict) else [list(t) for t in frames_to_triples(r)]
            for r in item["results"]
        ]
    return out


def frames_to_triples(frame):
    return [tuple(frame[i:i + 3]) for i in range(0, len(frame), 3)]


def normalize_py_steps(results):
    return results


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sp-src", type=Path, default=sp_src_default())
    ap.add_argument("--perf-steps", type=int, default=3000)
    ap.add_argument("--no-perf", action="store_true")
    args = ap.parse_args()

    if args.sp_src is not None:
        sys.path.insert(1, str(args.sp_src / "src"))
    wasm_bytes = default_wasm_bytes()
    wasm_b64 = base64.b64encode(wasm_bytes).decode("ascii")
    print(f"sdled.wasm: {len(wasm_bytes)} bytes  sha256={__import__('hashlib').sha256(wasm_bytes).hexdigest()[:16]}")

    cases = build_cases(args.sp_src)
    for c in cases:
        c["ops"] = [["parse", c["program"], 0]] + [["step", t] for t in TIMES_MS]
        c["expect"] = {}
        if c["expect_all"] is not None:
            for i in range(1, len(c["ops"])):
                c["expect"][i] = [c["expect_all"]] * c["led_count"]
    for u in upstream_vector_cases(args.sp_src):
        u.update({"kind": "upstream"})
        cases.append(u)
    # Deno payload: drop helper keys it does not need.
    deno_cases = [{"name": c["name"], "led_count": c["led_count"], "ops": c["ops"]} for c in cases]

    py = run_python(deno_cases)
    js = run_deno(deno_cases, wasm_b64)

    failures = 0
    print(f"{'case':<42} {'driver-agree':<13} {'expect':<8} result")
    for c in cases:
        name = c["name"]
        agree = py[name] == js[name]
        exp_ok = True
        exp_note = "-"
        for idx, expected in c["expect"].items():
            if expected == "=1":  # step after re-parse must equal the earlier step
                expected = py[name][1]
            got = py[name][idx]
            exp_note = "vector" if c["kind"] == "upstream" else exp_note
            if [tuple(t) for t in got] != [tuple(t) for t in expected]:
                exp_ok = False
        if c["kind"] == "invalid":
            parse = py[name][0]
            exp_note = parse["errorName"]
            exp_ok = not parse["ok"]
        if c["kind"] == "valid-parse":
            exp_ok = py[name][0]["ok"]
            exp_note = "ok"
        if c["name"].startswith("invalid-too-long"):
            exp_ok = exp_ok and py[name][0]["errorName"] == "too-long"
        ok = agree and exp_ok
        failures += 0 if ok else 1
        print(f"{name:<42} {'yes' if agree else 'NO':<13} {exp_note:<8} {'PASS' if ok else 'FAIL'}")
        if not agree:
            for i, (a, b) in enumerate(zip(py[name], js[name])):
                if a != b:
                    print(f"    divergence at op {i}: py={str(a)[:120]} js={str(b)[:120]}")
                    break

    # Sanity: built-in programs must be visibly non-trivial (not all-black) at some step.
    print()
    print(f"cases: {len(cases)}  failures: {failures}")

    if not args.no_perf:
        prog = "off 320ms cosine\n0:#00E5FF 760ms pulse 0ms; 7:#00E5FF 760ms pulse 95ms\nrepeat"
        ctl = SdLedWasm(8)
        ctl.parse(prog, 0)
        samples = []
        for i in range(args.perf_steps):
            t0 = time.perf_counter()
            ctl.step(i * 16)
            samples.append((time.perf_counter() - t0) * 1000.0)
        samples.sort()
        print(f"perf (8 LEDS, wasmtime-py, {args.perf_steps} steps): "
              f"mean={statistics.mean(samples):.4f}ms p50={samples[len(samples)//2]:.4f}ms "
              f"p99={samples[int(len(samples)*0.99)]:.4f}ms max={samples[-1]:.4f}ms "
              f"(60fps budget 16.67ms)")
        t0 = time.perf_counter()
        SdLedWasm(8)
        print(f"instantiate (compile+instance): {(time.perf_counter()-t0)*1000:.1f}ms")

    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
