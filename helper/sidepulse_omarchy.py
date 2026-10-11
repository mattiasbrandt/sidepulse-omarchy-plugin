"""SidePulse helper for the Omarchy bar widget (D4, D5, D14, D19).

Runs under the SidePulse venv's python (`python -I helper/sidepulse_omarchy.py`)
and imports upstream's own code. It writes NDJSON events to stdout and reads
commands, one JSON object per line, from stdin. It exits when stdin closes.

Events (stdout):
  hello     protocol, helper and upstream versions, capabilities {feature: {ok, reason}}
  state     four-state label and colour, urgent (Ask), active_count, top session;
            "unavailable" with a reason while latest.json cannot be read
  setup     ordered items still missing (install, service, device) with an action
  result    outcome of a command that changes something (start-service)
  error     a feature failed or a command was refused; the message says why
  clear     a feature reported by error has recovered
  devices   connected SidePulse devices: id, product, LED count, Display (agent,
            battery, off; the effective one), brightness percent and the colour
            its LEDs show now (null when dark)
  power     the battery (present, percent, plugged), the lid (closed, null when
            unknown) and whether the Battery Display is offered (D10)
  hooks     each agent's hook config: whether it exists and has SidePulse's hooks
  log       the last hook decision rows: provider, event, the State it became
  settings  idle timeout, session retention and the Bar LEDs switch (upstream's)
  result    outcome of a command: start-service, hooks-install, hooks-uninstall,
            open-session
  awake     the keep-awake policy (never, agents, always), whether the sleep lock
            is held now, and why (D11)
  frame     the RGB of each LED, 0-255, for the Bar LEDs ("target": "bar") or for
            one device's program ("target": "device", "device": id). Sent only
            when the colours change; "leds": null hides the Bar LEDs (D6, D7)

Commands (stdin): {"cmd": "refresh"}, {"cmd": "start-service"},
{"cmd": "set-policy", "policy": "never" | "agents" | "always"} (D11),
{"cmd": "set-displays", "displays": {device id: display}} (the widget's full
remembered map; the helper reconciles every connected device to it, D9), and
{"cmd": "set-brightness", "device": id, "percent": 0-100},
{"cmd": "hooks-install" | "hooks-uninstall", "provider": name} (D13),
{"cmd": "set-agent-list", "idle_timeout_seconds"?, "recent_session_retention_seconds"?},
{"cmd": "set-bar-leds", "enabled": bool} and {"cmd": "set-notify", "enabled": bool} (D20),
{"cmd": "set-popup", "open": bool}: whether a popup is open; device frames flow only while it is.

It never binds events.sock: that socket belongs to upstream's service (D4).
"""

from __future__ import annotations

import ctypes
import ctypes.util
import importlib
import importlib.metadata
import json
import math
import os
import re
import select
import shutil
import signal
import struct
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, TextIO

PROTOCOL_VERSION = 1
HELPER_VERSION = "dev"
TICK_SECONDS = 1.0
SETUP_EVERY_TICKS = 5
SERVICE_START_TIMEOUT_SECONDS = 60.0
# Hook log rows the popup keeps (newest first), and how long one hook command may run.
HOOK_LOG_ROWS = 8
HOOK_COMMAND_TIMEOUT_SECONDS = 60.0
# The libnotify action a click invokes. Omarchy's notification server invokes
# only this one, and only while the sender is alive (D20).
NOTIFY_ACTION = "default"
NOTIFY_BODY_LIMIT = 200
# Upstream's agent names as the popup writes them (GLOSSARY: Agent).
PROVIDER_NAMES = {"claude": "Claude Code", "codex": "Codex", "grok": "Grok", "cursor": "Cursor", "junie": "Junie"}
NOTIFY_REASON = "notify-send is not installed; the Ask notifications need it (libnotify)"
INSTALL_COMMAND = "curl -fsSL https://sidepulse.io/setup.sh | bash"
# A device's Display (D9). Upstream stores only Agent or Manual ("custom") on
# Linux, so Battery and Off hold the device in Manual while the helper drives
# it (Battery, D10) or the user keeps it dark (Off).
DISPLAYS = ("agent", "battery", "off")
DEFAULT_DISPLAY = "agent"
UPSTREAM_DISPLAY = {"agent": "agent", "battery": "custom", "off": "custom"}
PRODUCT_NAMES = {"dot": "SidePulse Dot", "pro": "SidePulse Pro"}
# LED frames (D6): upstream's virtual_device.py FRAME_RATE and IDLE_FRAME_RATE. A
# drift test in tests/test_bar_leds.py reads both from upstream's source.
FRAME_RATE = 60.0
IDLE_FRAME_RATE = 12.0
FRAME_BUDGET_MS = 1000.0 / FRAME_RATE
# The device id upstream gives its SidePulse Notch (virtual_device.VIRTUAL_DEVICE_ID); its brightness is the Notch's.
NOTCH_DEVICE_ID = "virtual:status-bar"

# Upstream's four-state UI mapping is `state_for_mode` in status_bar.py (lines
# 461-472 at upstream 6d55225). That module needs PyObjC, so it cannot be
# imported on Linux and the mapping is copied here. tests/test_state.py reads
# the upstream source and fails when the mapping or the colours drift.
MODE_STATES = {
    "waiting_for_input": "ask",
    "blocked_error": "ask",
    "working": "working",
    "tool_running": "working",
    "long_task_progress": "working",
    "completed": "done",
    "idle_ready": "idle",
    "unknown": "idle",
}
STATE_LABELS = {"ask": "Ask", "working": "Working", "done": "Done", "idle": "Idle"}
# Upstream's colours (status_bar.py, the Ask/Working/Done/Idle legend).
STATE_COLOURS = {"ask": "#FF8A00", "working": "#00AEEF", "done": "#22B14C", "idle": "#A8A8A8"}

# Upstream names each feature needs. A missing one disables only that feature.
REQUIRED_NAMES = {
    "state": {
        "sidepulse.collector": (
            "agent_status_from_dict",
            "snapshot_from_statuses",
            "COMPLETED_VISIBLE_SECONDS",
            "IDLE_VISIBLE_SECONDS",
            "POST_TOOL_WORKING_VISIBLE_SECONDS",
        ),
        "sidepulse.ipc": ("default_latest_state_path",),
        "sidepulse.settings": ("load_settings",),
    },
    "sessions": {
        "sidepulse.session_actions": ("session_resume_command",),
        "sidepulse.models": ("AgentMode",),
    },
    "service": {"sidepulse.service_launch": ("service_is_running",)},
    "devices": {
        "sidepulse.device_writer": ("discover_devices", "write_led_program"),
        "sidepulse.settings": ("load_settings", "save_settings"),
        "sidepulse.led_status": ("led_count_for_target", "builtin_animation_program"),
    },
    "hooks": {
        "sidepulse.providers": ("detect_provider_configs", "HOOK_PROVIDERS"),
        "sidepulse.cli": ("sidepulse_main",),
    },
    "hook_log": {
        "sidepulse.audit": ("default_status_audit_log_path", "read_recent_lines", "AUDIT_COLUMNS"),
    },
    "settings": {
        "sidepulse.settings": ("load_settings", "save_settings",
                               "AgentMonitorSettings.with_agent_list_timing",
                               "AgentMonitorSettings.with_virtual_status_device",
                               "AgentMonitorSettings.with_sleep_prevention_policy"),
    },
    # The sleep lock's predicate (D11): upstream's controller, its grace and the battery reader's type.
    # The systemd-inhibit binary is probed separately, in probe_capabilities.
    "keep_awake": {
        "sidepulse.keep_awake": ("KeepAwakeController", "KeepAwakeController.should_hold_for_mode",
                                 "AWAKE_GRACE_SECONDS", "format_duration"),
        "sidepulse.battery": ("BatterySnapshot",),
        "sidepulse.settings": ("load_settings", "SLEEP_PREVENTION_CHOICES"),
    },
    # The Battery display and its 7 s preview (D9, D10): upstream's battery program and its settings.
    "battery": {
        "sidepulse.battery": ("BatterySnapshot", "program_for_battery", "DEFAULT_POWER_CHANGE_PREVIEW_SECONDS"),
        "sidepulse.settings": ("LED_DISPLAY_BATTERY", "AgentMonitorSettings.battery_show_on_power_change",
                               "AgentMonitorSettings.battery_power_change_preview_seconds"),
    },
    # The lid animations (D10): upstream's lid settings and LED text helpers. busctl is probed in probe_capabilities.
    "lid": {
        "sidepulse.settings": ("LID_ANIMATION_CLOSED", "LID_ANIMATION_OPEN", "AgentMonitorSettings.lid_animation",
                               "AgentMonitorSettings.sleep_prevention_policy"),
        "sidepulse.device_writer": ("normalize_led_text", "validate_led_text", "DeviceWriteError"),
        "sidepulse.led_status": ("apply_brightness", "builtin_animation_program"),
    },
    # The Bar LEDs' frames (D6): upstream's engine resource and error names, its programs and its animation settings.
    # The wasmtime package and the packaged sdled.wasm are probed separately, in probe_capabilities.
    "bar_leds": {
        "sidepulse.led_wasm": ("load_packaged_wasm", "ERROR_NAMES"),
        "sidepulse.led_status": ("program_for_agent_mode", "display_state_for_mode", "LedDisplayState"),
        "sidepulse.settings": ("AgentMonitorSettings.agent_animation", "AgentMonitorSettings.brightness_for_device"),
    },
}
WASMTIME_REASON = "wasmtime is not installed in the SidePulse venv; the Bar LEDs need it"

# Keep awake (D11). The policy values are upstream's settings values; tests/test_keep_awake.py checks them.
SLEEP_PREVENTION_NEVER = "never"
SLEEP_PREVENTION_AGENTS = "agents"
SLEEP_PREVENTION_ALWAYS = "always"
SLEEP_PREVENTION_CHOICES = (SLEEP_PREVENTION_NEVER, SLEEP_PREVENTION_AGENTS, SLEEP_PREVENTION_ALWAYS)
POWER_SUPPLY_ROOT = Path("/sys/class/power_supply")
# logind's LidClosed property (D10); busctl prints "b true" or "b false".
LID_COMMAND = ("busctl", "get-property", "org.freedesktop.login1", "/org/freedesktop/login1",
               "org.freedesktop.login1.Manager", "LidClosed")
LID_REASON = "busctl is not installed; the lid animations need it (systemd)"
# Upstream holds a lid animation this long past its duration (status_bar.py LID_ANIMATION_RESTORE_FUDGE_SECONDS).
LID_ANIMATION_FUDGE_SECONDS = 0.15
INHIBIT_WHAT = "sleep:handle-lid-switch"
INHIBIT_WHO = "SidePulse"
INHIBIT_WHY = "Keeping the machine awake while agents work"
INHIBIT_REASON = "systemd-inhibit is not installed; keeping the machine awake needs it (systemd)"
NO_AGENT = "no agent is working"
# Upstream's modes that hold at once; the finished ones (completed, ask, error) hold for the grace.
WORKING_MODE_VALUES = frozenset({"working", "tool_running", "long_task_progress"})

IN_MOVED_TO = 0x80
IN_CLOSE_WRITE = 0x08
IN_CREATE = 0x100
IN_NONBLOCK = 0o4000
IN_CLOEXEC = 0o2000000


def state_for_mode(mode: str) -> str:
    return MODE_STATES.get(mode, "idle")


def upstream_version() -> str | None:
    try:
        return importlib.metadata.version("sidepulse")
    except importlib.metadata.PackageNotFoundError:
        return None


def probe_capabilities() -> dict[str, dict[str, Any]]:
    """Import and attribute checks for each feature (D14). Nothing is run."""
    caps: dict[str, dict[str, Any]] = {}
    for feature, modules in REQUIRED_NAMES.items():
        caps[feature] = _probe_names(modules)
    if caps["keep_awake"]["ok"]:
        caps["keep_awake"] = _probe_inhibit()
    if caps["bar_leds"]["ok"]:
        caps["bar_leds"] = _probe_bar_leds()
    if caps["lid"]["ok"] and shutil.which(LID_COMMAND[0]) is None:
        caps["lid"] = {"ok": False, "reason": LID_REASON}
    caps["notify"] = _probe_notify()
    return caps


def _probe_inhibit() -> dict[str, Any]:
    if shutil.which(inhibit_argv()[0]) is None:
        return {"ok": False, "reason": INHIBIT_REASON}
    return {"ok": True, "reason": ""}


def _probe_names(modules: dict[str, tuple[str, ...]]) -> dict[str, Any]:
    for module_name, names in modules.items():
        try:
            module = importlib.import_module(module_name)
        except ImportError as exc:
            return {"ok": False, "reason": f"{module_name} cannot be imported: {exc}"}
        for name in names:
            # "Class.attribute" checks a method or class attribute, e.g. AgentMonitorSettings.with_...
            target: Any = module
            for part in name.split("."):
                target = getattr(target, part, None)
            if target is None:
                return {"ok": False, "reason": f"{module_name} has no {name}; upstream changed"}
    return {"ok": True, "reason": ""}


def _probe_bar_leds() -> dict[str, Any]:
    """The Bar LEDs need wasmtime (D6) and upstream's sdled.wasm, read from the installed package."""
    try:
        importlib.import_module("wasmtime")
    except ImportError:
        return {"ok": False, "reason": WASMTIME_REASON}
    from sidepulse.led_wasm import load_packaged_wasm

    try:
        load_packaged_wasm()
    except OSError as exc:
        return {"ok": False, "reason": f"upstream's sdled.wasm cannot be read from the SidePulse install: {exc}"}
    return {"ok": True, "reason": ""}


def _probe_notify() -> dict[str, Any]:
    if shutil.which("notify-send") is None:
        return {"ok": False, "reason": NOTIFY_REASON}
    return {"ok": True, "reason": ""}


def snapshot_from_statuses(statuses: list[Any], *, now: datetime, idle_timeout_seconds: float) -> Any:
    """Upstream's aggregate rules, with the arguments status_bar.py passes (build_monitor)."""
    from sidepulse.collector import (
        COMPLETED_VISIBLE_SECONDS,
        IDLE_VISIBLE_SECONDS,
        POST_TOOL_WORKING_VISIBLE_SECONDS,
        snapshot_from_statuses as upstream_snapshot,
    )

    return upstream_snapshot(
        tuple(statuses),
        sources=(),
        collected_at=now,
        stale_after_seconds=idle_timeout_seconds,
        tool_running_timeout_seconds=0.0,
        completed_visible_seconds=COMPLETED_VISIBLE_SECONDS,
        idle_visible_seconds=IDLE_VISIBLE_SECONDS,
        post_tool_working_visible_seconds=POST_TOOL_WORKING_VISIBLE_SECONDS,
    )


def read_snapshot(latest_path: Path, *, now: datetime, idle_timeout_seconds: float) -> Any:
    """Read latest.json (the service writes it) and apply upstream's rules.

    A missing file is an empty snapshot. Unreadable or malformed JSON raises.
    """
    from sidepulse.collector import agent_status_from_dict

    try:
        text = latest_path.read_text()
    except FileNotFoundError:
        text = '{"statuses": []}'
    data = json.loads(text)
    if not isinstance(data, dict) or not isinstance(data.get("statuses", []), list):
        raise ValueError(f"{latest_path} is not a SidePulse state file")
    statuses = [s for s in (agent_status_from_dict(row) for row in data.get("statuses", [])) if s is not None]
    return snapshot_from_statuses(statuses, now=now, idle_timeout_seconds=idle_timeout_seconds)


def state_event(snapshot: Any) -> dict[str, Any]:
    aggregate = snapshot.aggregate
    state = state_for_mode(aggregate.mode.value)
    return {
        "type": "state",
        "state": state,
        "label": STATE_LABELS[state],
        "colour": STATE_COLOURS[state],
        "urgent": state == "ask",
        "active_count": aggregate.active_count,
        "stale_count": aggregate.stale_count,
        "top": _top_session(aggregate.representative),
    }


def unavailable_state_event(reason: str) -> dict[str, Any]:
    """Replaces the last state when latest.json cannot be read, so the bar never shows a stale one."""
    return {
        "type": "state",
        "state": "unavailable",
        "label": "Unavailable",
        "colour": STATE_COLOURS["idle"],
        "urgent": False,
        "active_count": 0,
        "stale_count": 0,
        "top": None,
        "reason": reason,
    }


def _top_session(status: Any) -> dict[str, Any] | None:
    if status is None:
        return None
    return {
        "provider": status.provider,
        "agent_id": status.agent_id,
        "display_name": status.display_name,
        "mode": status.mode.value,
        "mode_label": status.mode_label,
        "message": status.message,
        "cwd": status.cwd,
        "updated_at": status.updated_at.isoformat(),
    }


def setup_event(*, installed: bool, service_running: bool | None, device_count: int | None) -> dict[str, Any]:
    """What is missing, in order (D19). None means the check could not run and is skipped."""
    items: list[dict[str, Any]] = []
    if not installed:
        items.append({
            "id": "install",
            "title": "SidePulse is not installed",
            "detail": f"Install it with: {INSTALL_COMMAND}",
            "action": None,
        })
    if service_running is False:
        items.append({
            "id": "service",
            "title": "The SidePulse service is stopped",
            "detail": "Agent status and LEDs need it running",
            "action": "start-service",
            "action_label": "Start service",
        })
    if device_count == 0:
        items.append({
            "id": "device",
            "title": "No SidePulse device",
            "detail": "Plug in a SidePulse Dot or SidePulse Pro",
            "action": None,
        })
    return {"type": "setup", "items": items}


def start_service(python: str) -> tuple[bool, str]:
    """`sidepulse service start`, run with the same interpreter as the helper."""
    result = subprocess.run(
        [python, "-I", "-m", "sidepulse", "service", "start"],
        capture_output=True,
        text=True,
        timeout=SERVICE_START_TIMEOUT_SECONDS,
        check=False,
    )
    output = (result.stdout + result.stderr).strip()
    return result.returncode == 0, output or f"exit {result.returncode}"


@dataclass(frozen=True)
class Device:
    """A mounted SidePulse device, as upstream's discover_devices finds it (D21, D23)."""

    id: str  # upstream's device id: the mount root, as the service keys it
    name: str
    path: str
    target: Path  # the LEDS.LED file the program is written to
    kind: str  # "dot" or "pro"
    led_count: int

    @property
    def product(self) -> str:
        return PRODUCT_NAMES[self.kind]


def discover_device_rows() -> list[Device]:
    """Upstream's discovery. The LED count is upstream's own rule (by the mount's name);
    a device it cannot name is reported as a Pro, the 8-LED default."""
    from sidepulse.device_writer import discover_devices
    from sidepulse.led_status import led_count_for_target

    rows: list[Device] = []
    for candidate in discover_devices():
        root = candidate.root.expanduser()
        led_count = led_count_for_target(candidate.target)
        rows.append(Device(
            id=str(root),
            name=root.name,
            path=str(root),
            target=candidate.target,
            kind="dot" if led_count == 2 else "pro",
            led_count=led_count,
        ))
    return rows


def change_settings(change: Any) -> None:
    """Load, change and save upstream settings.json (D9, D13).

    Upstream's own load_settings/save_settings do the work, so keys upstream
    models survive and keys it does not model are dropped, as upstream's CLI
    drops them. A file that is not a JSON object raises here, before anything
    is overwritten.
    """
    from sidepulse.settings import default_settings_path, load_settings, save_settings

    path = default_settings_path()
    if path.exists() and not isinstance(json.loads(path.read_text()), dict):
        raise ValueError(f"{path} is not a JSON object; not overwriting it")
    save_settings(change(load_settings(path)), path)


def settings_event(settings: Any) -> dict[str, Any]:
    """The Settings section's upstream values (D13)."""
    return {
        "type": "settings",
        "bar_leds": settings.virtual_status_device_enabled,
        "idle_timeout_seconds": settings.idle_timeout_seconds,
        "recent_session_retention_seconds": settings.recent_session_retention_seconds,
    }


def timing_change(command: dict[str, Any]) -> tuple[float | None, float | None] | str:
    """The idle timeout and retention a set-agent-list command asks for, or why it is refused.

    Upstream clamps a negative or non-numeric value to 0 without saying so, so
    the helper refuses it here. An idle timeout of 0 would mark every session
    stale at once, so it must be positive; retention may be 0.
    """
    idle = command.get("idle_timeout_seconds")
    retention = command.get("recent_session_retention_seconds")
    if idle is None and retention is None:
        return "set-agent-list needs idle_timeout_seconds, recent_session_retention_seconds or both"
    for name, value in (("idle_timeout_seconds", idle), ("recent_session_retention_seconds", retention)):
        if value is None:
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            return f"{name} must be a number of seconds"
        if value < 0:
            return f"{name} must be 0 or more seconds"
    if idle is not None and idle <= 0:
        return "idle_timeout_seconds must be more than 0 seconds"
    return (None if idle is None else float(idle), None if retention is None else float(retention))


def hooks_event(home: Path | None = None) -> dict[str, Any]:
    """Each agent's hook config (D13), one row per agent upstream lists (HOOK_PROVIDERS).

    The rows are doctor's fields plus `installed`: SidePulse's own hooks are
    present, which is what its log paths show (an unrelated hook on a known
    event sets hooks_enabled but not installed). Upstream's detectors read
    whatever JSON a config holds, so one that raises becomes that agent's
    `error` row; the other agents are still reported (D14).
    """
    from sidepulse import providers

    rows: list[dict[str, Any]] = []
    for name in providers.HOOK_PROVIDERS:
        try:
            config = getattr(providers, f"detect_{name}_config")(home)
        except Exception as exc:  # the boundary to upstream: report it, keep the other agents
            rows.append({"provider": name, "error": f"{type(exc).__name__}: {exc}"})
            continue
        row = config.to_dict()
        row["installed"] = bool(config.log_paths)
        rows.append(row)
    return {"type": "hooks", "providers": rows}


def run_hook_command(action: str, provider: str, python: str) -> tuple[bool, str]:
    """`agent-monitor install|uninstall <provider>` in the SidePulse venv (D4: hooks go through the CLI).

    Installing or removing a hook edits the user's agent config, so this runs
    only where the caller means it; tests run it under a scratch HOME.
    """
    result = subprocess.run(
        [python, "-I", "-m", "sidepulse", "agent-monitor", action, provider],
        capture_output=True,
        text=True,
        timeout=HOOK_COMMAND_TIMEOUT_SECONDS,
        check=False,
    )
    output = (result.stdout + result.stderr).strip()
    return result.returncode == 0, output or f"exit {result.returncode}"


def hook_log_event(limit: int = HOOK_LOG_ROWS, path: Path | None = None) -> dict[str, Any]:
    """The last `limit` hook decisions, newest first (D13).

    Read from upstream's event-status.jsonl (the audit log the hooks write). Each
    row shows the State the decision left the session in, as the menu bar names it.
    """
    from sidepulse.audit import AUDIT_COLUMNS, default_status_audit_log_path, read_recent_lines

    source = path or default_status_audit_log_path()
    try:
        lines = read_recent_lines(source, limit)
    except FileNotFoundError:
        return {"type": "log", "rows": []}  # no hook has run yet; anything else is reported by the caller
    rows: list[dict[str, Any]] = []
    for line in lines:
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if not isinstance(record, dict):
            continue
        row = {column: str(record.get(column, "")) for column in AUDIT_COLUMNS}
        state = state_for_mode(row["status"]) if row["status"] else None
        rows.append({
            "time": _local_clock(row["logged_at"]),
            "provider": row["provider"],
            "event": row["hook_event"],
            "label": STATE_LABELS[state] if state else "",
            "colour": STATE_COLOURS[state] if state else "",
        })
    rows.reverse()
    return {"type": "log", "rows": rows[:limit]}


def _local_clock(stamp: str) -> str:
    try:
        return datetime.fromisoformat(stamp).astimezone().strftime("%H:%M:%S")
    except ValueError:
        return stamp


# --- Notify when a session asks (D20) -----------------------------------------

def asking_sessions(statuses: list[Any]) -> dict[tuple[str, str], Any]:
    """The fresh statuses that are in Ask, keyed by (provider, session id)."""
    return {
        (status.provider.lower(), status.session_id): status
        for status in statuses
        if status.session_id and state_for_mode(status.mode.value) == "ask"
    }


def notification_text(status: Any) -> tuple[str, str]:
    """The summary and body of the notice for a session that asks."""
    name = PROVIDER_NAMES.get(status.provider.lower(), status.provider)
    project = Path(status.cwd).name if status.cwd else ""
    message = " ".join(str(status.message or "").split())
    body = ": ".join(part for part in (project, message) if part)
    if len(body) > NOTIFY_BODY_LIMIT:
        body = body[: NOTIFY_BODY_LIMIT - 3].rstrip() + "..."
    return f"{name} needs attention", body


def notify_argv(summary: str, body: str) -> list[str]:
    """notify-send with a default action: it waits, and prints the action's name when clicked."""
    return ["notify-send", "--app-name=SidePulse", "--wait", f"--action={NOTIFY_ACTION}=Open session", summary, body]


def plain_notify_argv(summary: str) -> list[str]:
    """notify-send for a notice with no action (D11): it returns at once, so its exit is all there is to read."""
    return ["notify-send", "--app-name=SidePulse", summary]


def display_of(displays: dict[str, str], device_id: str, battery_offered: bool = True) -> str:
    """The widget's remembered Display for a device: Agent when none is
    remembered, when the value is not one this helper offers, or when it is
    Battery and there is no battery (D9)."""
    display = displays.get(device_id, DEFAULT_DISPLAY)
    if display not in DISPLAYS or (display == "battery" and not battery_offered):
        return DEFAULT_DISPLAY
    return display


def apply_display(device: Device, display: str, program: str | None = None) -> None:
    """Bring one device to its Display (D9), driving it with `program` while the helper takes its LEDs (D10).

    Agent with no program: upstream's `agent`, so the service repaints within its next poll.
    Off: upstream's `custom` (Manual) with the off program. A Battery or lid takeover
    passes its program: upstream's `custom`, with that program written.
    Whenever the device is Manual and no longer shows what it should, the program is
    written again on the next tick: the service may write an agent program it planned
    before reading `custom`.
    """
    from sidepulse.led_status import builtin_animation_program
    from sidepulse.device_writer import normalize_led_text, write_led_program
    from sidepulse.settings import load_settings

    if display == "off":
        program = builtin_animation_program("off", device.led_count)
    upstream = "agent" if program is None else "custom"
    if load_settings().display_for_device(device.id) != upstream:
        change_settings(
            lambda s: s.with_device_display(device.id, upstream, name=device.name, path=device.path))
    if upstream == "custom":
        text = normalize_led_text(program).strip()
        if shown_program(device) != text:
            write_led_program(text, device_path=device.target)


COLOUR = re.compile(r"#([0-9A-Fa-f]{6})")


def shown_colour(device: Device) -> str | None:
    """The first colour of the program the device holds now: what its LEDs
    show, which can differ from the menu-bar legend colour (Done is cyan on
    the device, green in the legend). None when dark or holding no program."""
    match = COLOUR.search(shown_program(device) or "")
    return "#" + match.group(1).upper() if match else None


def shown_program(device: Device) -> str | None:
    """The LED program the device holds now; None when it holds none."""
    try:
        return Path(device.target).read_text().strip()
    except FileNotFoundError:
        return None


def icon_device(devices: list[Device]) -> Device | None:
    """The device the bar icon is drawn as (D12, D21): a SidePulse Pro wins, else the first device."""
    for device in devices:
        if device.kind == "pro":
            return device
    return devices[0] if devices else None


def full_brightness_program(text: str | None, brightness: int) -> str | None:
    """The device's program as its row plays it: at full brightness (D21).

    Upstream writes the program through apply_brightness, which scales every
    `brightness N` line by the device's brightness (0-255). A screen pixel at a few
    percent is black where a real LED still shines, so the row undoes that scaling;
    the brightness slider shows the brightness. A device at 0 plays at full.
    """
    if text is None:
        return None
    lines = []
    for line in text.splitlines():
        match = re.fullmatch(r"\s*brightness\s+(\d+)\s*", line, re.IGNORECASE)
        if match is None:
            lines.append(line)
        elif brightness <= 0:
            lines.append("brightness 255")
        else:
            lines.append(f"brightness {min(255, round(int(match.group(1)) * 255 / brightness))}")
    return "\n".join(lines)


def set_device_brightness(device: Device, percent: float) -> None:
    """Upstream's brightness is 0-255 per device; the widget speaks percent."""
    from sidepulse.settings import normalize_brightness

    value = normalize_brightness(percent * 255 / 100)
    change_settings(
        lambda s: s.with_device_brightness(device.id, value, name=device.name, path=device.path))


class DirWatch:
    """inotify on a directory, for MOVED_TO latest.json (the service's tmp + rename)."""

    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self.fd: int | None = None

    def open(self) -> None:
        if self.fd is not None or not self.directory.is_dir():
            return
        libc = ctypes.CDLL(ctypes.util.find_library("c") or "libc.so.6", use_errno=True)
        fd = libc.inotify_init1(IN_NONBLOCK | IN_CLOEXEC)
        if fd < 0:
            raise OSError(ctypes.get_errno(), "inotify_init1 failed")
        mask = IN_MOVED_TO | IN_CLOSE_WRITE | IN_CREATE
        if libc.inotify_add_watch(fd, os.fsencode(str(self.directory)), mask) < 0:
            error = ctypes.get_errno()
            os.close(fd)
            raise OSError(error, f"inotify_add_watch failed for {self.directory}")
        self.fd = fd

    def names(self) -> list[str]:
        if self.fd is None:
            return []
        try:
            data = os.read(self.fd, 65536)
        except BlockingIOError:
            return []
        names = []
        offset = 0
        while offset + 16 <= len(data):
            _wd, _mask, _cookie, length = struct.unpack_from("iIII", data, offset)
            names.append(data[offset + 16 : offset + 16 + length].rstrip(b"\0").decode(errors="replace"))
            offset += 16 + length
        return names


# --- Bar LEDs and device frames (D6, D7, D21) ---------------------------------

INPUT_SIZE = 512  # the engine's input buffer (sdled.wasm)
INPUT_TOO_LONG = 513  # upstream's length for a program over the buffer (led_wasm.py)


def _i32(value: int) -> int:
    """JavaScript's ToInt32, for values passed as i32 to the engine."""
    return ((int(value) + 2**31) % 2**32) - 2**31


def _engine_input(text: str) -> tuple[bytes, int]:
    """The UTF-16 code units of `text` written the way upstream's writeInput() writes them: returns (buffer, length)."""
    data = str(text).encode("utf-16-le", "surrogatepass")
    units = [data[i] | (data[i + 1] << 8) for i in range(0, len(data), 2)]
    buffer = bytearray(INPUT_SIZE)
    length = 0
    for code in units:
        if code <= 127:
            if length < INPUT_SIZE:
                buffer[length] = code
            length += 1
        elif code <= 2047:
            if length < INPUT_SIZE:
                buffer[length] = (192 | (code >> 6)) & 0xFF
            if length + 1 < INPUT_SIZE:
                buffer[length + 1] = (128 | (code & 63)) & 0xFF
            length += 2
        else:
            if length < INPUT_SIZE:
                buffer[length] = (224 | (code >> 12)) & 0xFF
            if length + 1 < INPUT_SIZE:
                buffer[length + 1] = (128 | ((code >> 6) & 63)) & 0xFF
            if length + 2 < INPUT_SIZE:
                buffer[length + 2] = (128 | (code & 63)) & 0xFF
            length += 3
    return bytes(buffer), length


class SdLedWasm:
    """Upstream's sdled.wasm run by wasmtime (D6).

    The call sequence of upstream's SdLedWasmController (led_wasm.py), which
    runs the same module in JavaScriptCore on macOS: the engine is reset on
    first use, a program is parsed into the input buffer, and each step writes
    count x 3 RGB bytes at the output pointer. tests/test_bar_leds.py replays the
    reference cases of tests/bar_leds_reference.py and upstream's test vectors.
    """

    def __init__(self, led_count: int = 8, wasm_bytes: bytes | None = None) -> None:
        import wasmtime
        from sidepulse.led_wasm import load_packaged_wasm

        self.led_count = 2 if int(led_count) == 2 else 8
        if wasm_bytes is None:
            wasm_bytes = load_packaged_wasm()
        engine = wasmtime.Engine()
        self._store = wasmtime.Store(engine)
        module = wasmtime.Module(engine, wasm_bytes)
        exports = wasmtime.Instance(self._store, module, []).exports(self._store)
        self._memory = exports["memory"]
        self._reset_fn = exports["sdled_reset"]
        self._parse_fn = exports["sdled_parse"]
        self._step_fn = exports["sdled_step"]
        self._input_ptr = exports["sdled_input_ptr"](self._store)
        self._output_ptr = exports["sdled_output_ptr"](self._store)
        self._initialized = False

    def _ensure_initialized(self, now_ms: float) -> None:
        if not self._initialized:
            self._reset_fn(self._store, self.led_count, _i32(math.floor(now_ms)))
            self._initialized = True

    def parse(self, text: str, now_ms: float):
        """Parse a program; returns upstream's LedWasmParseResult (ok, error, error_name, line, column)."""
        from sidepulse.led_wasm import ERROR_NAMES, LedWasmParseResult

        self._ensure_initialized(now_ms)
        buffer, length = _engine_input(text)
        self._memory.write(self._store, buffer, self._input_ptr)
        packed = self._parse_fn(self._store, self.led_count,
                                INPUT_TOO_LONG if length > INPUT_SIZE else length,
                                _i32(math.floor(now_ms))) & 0xFFFFFFFF
        error = (packed >> 8) & 255
        return LedWasmParseResult(
            ok=(packed & 1) == 1,
            error=error,
            error_name=ERROR_NAMES[error] if error < len(ERROR_NAMES) else f"error-{error}",
            line=(packed >> 16) & 255,
            column=(packed >> 24) & 255,
        )

    def step(self, now_ms: float) -> list[list[int]]:
        """The RGB of each LED at this time, 0-255."""
        self._ensure_initialized(now_ms)
        self._step_fn(self._store, self.led_count, _i32(math.floor(now_ms)))
        raw = self._memory.read(self._store, self._output_ptr, self._output_ptr + self.led_count * 3)
        raw = bytes(raw)
        return [[raw[i], raw[i + 1], raw[i + 2]] for i in range(0, len(raw), 3)]


class LedProgram:
    """One LED program on its own engine (D6): the frames it plays.

    Parsed again only when its text changes, as upstream's setProgram_ does. A
    program that does not parse, or no program, is dark: the frames are zeros,
    and load() returns the reason so the helper reports it (D14), never a
    stand-in animation.
    """

    def __init__(self, led_count: int) -> None:
        self.led_count = 2 if int(led_count) == 2 else 8
        self.engine: SdLedWasm | None = None
        self.text: str | None = None
        self.lit = False
        self.problem: str | None = None

    def load(self, text: str | None, now_ms: float) -> str | None:
        """Parse `text` when it is new. Returns why it does not play, or None."""
        if text == self.text:
            return self.problem
        self.text = text
        self.lit = False
        self.problem = None
        if text is None:
            return None
        if self.engine is None:
            self.engine = SdLedWasm(self.led_count)
        result = self.engine.parse(text, now_ms)
        if not result.ok:
            self.problem = (f"a program does not parse: {result.error_name} at line {result.line}, "
                            f"column {result.column}")
            return self.problem
        self.lit = True
        return None

    def frame(self, now_ms: float) -> list[list[int]]:
        """The LEDs now; zeros while dark."""
        if not self.lit or self.engine is None:
            return [[0, 0, 0] for _ in range(self.led_count)]
        return self.engine.step(now_ms)


def bar_led_count(devices: list["Device"]) -> int:
    """D21: 2 LEDs for a lone SidePulse Dot; 8 for a Pro, for none, and for both (upstream prefers the Pro)."""
    return 2 if devices and all(device.kind == "dot" for device in devices) else 8


def bar_program(settings: Any, mode: Any, led_count: int) -> str:
    """The program the Bar LEDs play for a State: the call upstream's Notch makes (status_bar.py
    sync_virtual_status_device): the State's program with the user's animation for it, at the
    Notch's brightness (upstream's per-device value, 255 unless set)."""
    from sidepulse.led_status import program_for_agent_mode

    animation = settings.agent_animation(mode)
    return program_for_agent_mode(
        mode,
        led_count=led_count,
        brightness=settings.brightness_for_device(NOTCH_DEVICE_ID),
        animation_style=animation.style,
        custom_program=animation.custom_program,
    )


def frame_interval_for(mode: Any) -> float:
    """Seconds between frames for a State: upstream's set_state runs Working and Ask at FRAME_RATE,
    Idle at IDLE_FRAME_RATE. Done is a solid colour, so it only needs the slow rate to see a change."""
    from sidepulse.led_status import LedDisplayState, display_state_for_mode

    animated = display_state_for_mode(mode) in (LedDisplayState.WORKING, LedDisplayState.ASK)
    return 1.0 / (FRAME_RATE if animated else IDLE_FRAME_RATE)


# --- Sessions (D8): a session's process, window and the three-state lookup ----

PROVIDER_BINARIES = {
    "claude": "claude",
    "codex": "codex",
    "cursor": "cursor-agent",
    "grok": "grok",
    "junie": "junie",
}
# Processes that only host other processes: a session's owner is never one of them.
SHELLS = frozenset({"bash", "dash", "fish", "sh", "zsh"})
COMMAND_TIMEOUT_SECONDS = 10.0
HYPRLAND_ADDRESS = re.compile(r"0x[0-9a-fA-F]+")


class CommandFailed(Exception):
    """A command the session lookup or open needs (herdr, tmux, hyprctl) failed; the message says why."""


class Proc:
    """Reads process facts from a /proc tree, so tests can supply a fake one."""

    def __init__(self, root: Path = Path("/proc")) -> None:
        self.root = root

    def _stat_fields(self, pid: int) -> list[str] | None:
        try:
            stat = (self.root / str(pid) / "stat").read_text()
        except OSError:
            return None
        # The comm field may contain spaces and parens; fields resume after the last ')'.
        return stat.rsplit(")", 1)[1].split()

    def starttime(self, pid: int) -> str | None:
        fields = self._stat_fields(pid)
        return fields[19] if fields else None

    def ppid(self, pid: int) -> int | None:
        fields = self._stat_fields(pid)
        return int(fields[1]) if fields else None

    def has_terminal(self, pid: int) -> bool:
        fields = self._stat_fields(pid)
        return bool(fields) and fields[4] != "0"

    def comm(self, pid: int) -> str | None:
        try:
            return (self.root / str(pid) / "comm").read_text().strip()
        except OSError:
            return None

    def chain(self, pid: int) -> list[int]:
        """pid and its ancestors, nearest first."""
        found: list[int] = []
        current: int | None = pid
        while current is not None and current > 1 and current not in found:
            found.append(current)
            current = self.ppid(current)
        return found

    def pids_of(self, binary: str) -> list[int]:
        found = []
        for entry in self.root.iterdir():
            if not entry.name.isdigit():
                continue
            try:
                if (entry / "comm").read_text().strip() == binary[:15]:
                    found.append(int(entry.name))
            except OSError:
                continue
        return sorted(found)

    def cwd(self, pid: int) -> str | None:
        try:
            return os.readlink(self.root / str(pid) / "cwd")
        except OSError:
            return None

    def cmdline(self, pid: int) -> list[str]:
        try:
            raw = (self.root / str(pid) / "cmdline").read_bytes()
        except OSError:
            return []
        return [part.decode(errors="replace") for part in raw.split(b"\0") if part]


def claude_session_ids(proc: Proc, home: Path) -> dict[int, str]:
    """pid -> the session Claude's session file says it runs, for pids that still have the recorded start time."""
    root = Path(os.environ.get("CLAUDE_CONFIG_DIR", home / ".claude")) / "sessions"
    owners: dict[int, str] = {}
    for path in root.glob("*.json"):
        try:
            data = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        pid = int(data["pid"])
        if proc.starttime(pid) == str(data.get("procStart")):
            owners[pid] = data.get("sessionId")
    return owners


def claude_pid(session_id: str, proc: Proc, home: Path) -> int | None:
    return next((pid for pid, owned in claude_session_ids(proc, home).items() if owned == session_id), None)


def grok_pid(session_id: str, proc: Proc, home: Path) -> int | None:
    try:
        entries = json.loads((home / ".grok" / "active_sessions.json").read_text())
    except (OSError, ValueError):
        return None
    for entry in entries:
        if entry.get("session_id") == session_id and proc.starttime(int(entry["pid"])):
            return int(entry["pid"])
    return None


EXACT_SOURCES = {"claude": claude_pid, "grok": grok_pid}


def names_session(args: list[str], session_id: str) -> bool:
    """True when an argument is the session id itself or `--flag=<id>`."""
    return any(arg == session_id or arg.endswith("=" + session_id) for arg in args)


def find_process(provider: str, session_id: str, cwd: str | None, proc: Proc, home: Path) -> dict:
    """Return {"state": stopped|live|unresolved, ...} for the session's process."""
    exact = EXACT_SOURCES.get(provider)
    if exact is not None:
        pid = exact(session_id, proc, home)
        if pid is not None:
            return {"state": "live", "pid": pid, "proof": "session-file"}

    binary = PROVIDER_BINARIES.get(provider)
    if binary is None:
        return {"state": "unresolved", "reason": f"unknown provider {provider!r}", "candidates": []}
    # Interactive agents hold a terminal; daemons such as `codex app-server` do not.
    pids = [pid for pid in proc.pids_of(binary) if proc.has_terminal(pid)]
    # A Claude process whose session file names another session is not a candidate for this one,
    # whatever its command line says: `claude --resume X` still shows X after the process moved on.
    if provider == "claude":
        elsewhere = {pid for pid, owned in claude_session_ids(proc, home).items() if owned != session_id}
        pids = [pid for pid in pids if pid not in elsewhere]
    by_cmdline = [pid for pid in pids if names_session(proc.cmdline(pid), session_id)]
    if len(by_cmdline) == 1:
        return {"state": "live", "pid": by_cmdline[0], "proof": "cmdline"}
    if len(by_cmdline) > 1:
        return {"state": "unresolved", "reason": "several processes name this session", "candidates": by_cmdline}
    if cwd is None:
        return {"state": "unresolved", "reason": "session has no cwd to check", "candidates": pids}
    in_cwd = [pid for pid in pids if proc.cwd(pid) == cwd]
    if in_cwd:
        return {"state": "unresolved", "reason": f"{binary} runs in {cwd} but nothing ties it to this session",
                "candidates": in_cwd}
    return {"state": "stopped"}


def ai_title(session_id: str, cwd: str, home: Path) -> str | None:
    root = Path(os.environ.get("CLAUDE_CONFIG_DIR", home / ".claude")) / "projects"
    transcript = root / cwd.replace("/", "-") / f"{session_id}.jsonl"
    title = None
    try:
        with transcript.open() as handle:
            for line in handle:
                if '"ai-title"' in line:
                    title = json.loads(line).get("aiTitle") or title
    except OSError:
        return None
    return title


def run_command(argv: list[str]) -> subprocess.CompletedProcess:
    """Runs one command for the session lookup. A binary that cannot run raises CommandFailed."""
    try:
        return subprocess.run(argv, capture_output=True, text=True, timeout=COMMAND_TIMEOUT_SECONDS, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        raise CommandFailed(f"{argv[0]} cannot run: {exc}") from exc


def hypr_clients(run: Callable[[list[str]], subprocess.CompletedProcess]) -> list[dict]:
    done = run(["hyprctl", "clients", "-j"])
    if done.returncode != 0:
        raise CommandFailed(f"hyprctl clients: {(done.stderr or done.stdout).strip() or 'exit ' + str(done.returncode)}")
    return json.loads(done.stdout)


def find_window(provider: str, session_id: str, cwd: str | None, pid: int, proc: Proc, home: Path,
                clients: list[dict]) -> dict:
    """Walk the ancestors to the first pid that owns Hyprland clients, then pick one of them."""
    by_pid: dict[int, list[dict]] = {}
    for client in clients:
        by_pid.setdefault(client["pid"], []).append(client)
    chain = proc.chain(pid)
    for owner in chain:
        if owner not in by_pid:
            continue
        candidates = by_pid[owner]
        if len(candidates) > 1 and provider == "claude" and cwd:
            title = ai_title(session_id, cwd, home)
            if title:
                candidates = [c for c in candidates if title in c["title"]]
        if len(candidates) == 1:
            return {"action": "focus", "address": candidates[0]["address"], "title": candidates[0]["title"]}
        return {"action": "ambiguous-window", "candidates": len(candidates)}
    return {"action": "no-window", "ancestry": chain}


Run = Callable[[list[str]], subprocess.CompletedProcess]


def herdr_panes_holding(chain: list[int], run: Run) -> list[str]:
    """The herdr panes whose foreground processes include the session's process or an ancestor."""
    panes = _json_output(run(["herdr", "pane", "list"]))["result"]["panes"]
    holding = []
    for pane in panes:
        info = _json_output(run(["herdr", "pane", "process-info", "--pane", pane["pane_id"]]))
        pids = {entry["pid"] for entry in info["result"]["process_info"]["foreground_processes"]}
        if pids.intersection(chain):
            holding.append(pane["pane_id"])
    return holding


def herdr_client_pids(proc: Proc) -> list[int]:
    """Attached herdr clients: a herdr with a terminal that is not the server."""
    return [pid for pid in proc.pids_of("herdr")
            if proc.has_terminal(pid) and "server" not in proc.cmdline(pid)[1:]]


def tmux_panes_holding(chain: list[int], run: Run) -> list[tuple[str, str]]:
    """(pane id, session name) of the tmux panes whose shell is the session's process or an ancestor."""
    done = run(["tmux", "list-panes", "-a", "-F", "#{pane_pid} #{pane_id} #{session_name}"])
    if done.returncode != 0:
        if "no server running" in done.stderr:
            return []
        raise CommandFailed(f"tmux list-panes: {(done.stderr or done.stdout).strip()}")
    holding = []
    for line in done.stdout.splitlines():
        pane_pid, pane_id, session = line.split(" ", 2)
        if int(pane_pid) in chain:
            holding.append((pane_id, session))
    return holding


def tmux_client_pids(run: Run) -> list[int]:
    done = run(["tmux", "list-clients", "-F", "#{client_pid}"])
    if done.returncode != 0:
        raise CommandFailed(f"tmux list-clients: {(done.stderr or done.stdout).strip()}")
    return [int(line) for line in done.stdout.split()]


def _json_output(done: subprocess.CompletedProcess) -> Any:
    if done.returncode != 0:
        raise CommandFailed((done.stderr or done.stdout).strip() or f"exit {done.returncode}")
    return json.loads(done.stdout)


def client_window(pids: list[int], proc: Proc, home: Path, clients: list[dict]) -> dict | None:
    """Where a multiplexer's client runs. None when no client is attached (open one, D8)."""
    if not pids:
        return None
    windows = [find_window("multiplexer", "", None, pid, proc, home, clients) for pid in pids]
    focus = [window for window in windows if window["action"] == "focus"]
    if len(focus) == 1:
        return focus[0]
    if len(pids) == 1:
        return windows[0]
    return {"action": "ambiguous-window", "candidates": len(pids)}


def running_in(chain: list[int], proc: Proc) -> str:
    """The nearest ancestor that is not a shell: where an agent with no window runs."""
    for ancestor in chain[1:]:
        name = proc.comm(ancestor)
        if name and name not in SHELLS:
            return f"{name} (pid {ancestor})"
    nearest = chain[1] if len(chain) > 1 else chain[0]
    return f"{proc.comm(nearest) or 'an unknown parent'} (pid {nearest})"


def live_route(provider: str, session_id: str, cwd: str | None, pid: int, proc: Proc, home: Path,
               clients: list[dict], run: Run) -> dict:
    """Where a live session runs (D8): its window, else its herdr pane, else its tmux pane, else nowhere known.

    Each multiplexer that cannot be asked is reported in `notes` and the lookup goes on; the
    result is never a guess.
    """
    window = find_window(provider, session_id, cwd, pid, proc, home, clients)
    if window["action"] != "no-window":
        return window
    chain = window["ancestry"]
    notes: list[str] = []
    try:
        panes = herdr_panes_holding(chain, run)
    except CommandFailed as exc:
        panes = []
        notes.append(f"herdr: {exc}")
    if len(panes) > 1:
        return {"action": "ambiguous-pane", "multiplexer": "herdr", "candidates": panes, "ancestry": chain}
    if panes:
        return {"action": "herdr", "pane": panes[0],
                "client": client_window(herdr_client_pids(proc), proc, home, clients)}
    try:
        tmux = tmux_panes_holding(chain, run)
        if len(tmux) > 1:
            return {"action": "ambiguous-pane", "multiplexer": "tmux", "candidates": [p for p, _ in tmux],
                    "ancestry": chain}
        if tmux:
            pane, session = tmux[0]
            return {"action": "tmux", "pane": pane, "session": session,
                    "client": client_window(tmux_client_pids(run), proc, home, clients)}
    except CommandFailed as exc:
        notes.append(f"tmux: {exc}")
    return {"action": "explain", "where": running_in(chain, proc), "ancestry": chain, "notes": notes}


def resolve(provider: str, session_id: str, cwd: str | None, proc: Proc | None = None,
            home: Path | None = None, clients: list[dict] | None = None, run: Run = run_command) -> dict:
    proc = proc or Proc()
    home = home or Path.home()
    found = find_process(provider, session_id, cwd, proc, home)
    if found["state"] == "stopped":
        return {**found, "action": "resume"}
    if found["state"] == "unresolved":
        return {**found, "action": "explain"}
    try:
        window_clients = hypr_clients(run) if clients is None else clients
    except CommandFailed as exc:
        return {**found, "action": "explain", "where": running_in(proc.chain(found["pid"]), proc),
                "notes": [str(exc)]}
    return {**found, **live_route(provider, session_id, cwd, found["pid"], proc, home, window_clients, run)}


# --- Open a session (D8): act on the lookup's plan ---------------------------

Spawn = Callable[[list[str], str | None], None]


SPAWN_SETTLE_SECONDS = 1.0


def spawn_detached(argv: list[str], cwd: str | None) -> None:
    """Starts a terminal (or any command) that outlives the helper.

    Raises CommandFailed when it cannot start, or when it exits with an error
    within SPAWN_SETTLE_SECONDS (no default terminal, a resume command that
    fails at once), with what it printed (D14). Its stderr goes to a temporary
    file, not a pipe: a terminal that keeps running would block once a pipe
    nobody reads fills up. A clean exit is success, since single-instance
    terminals hand the window to a running one and exit 0.
    """
    with tempfile.TemporaryFile() as err:
        try:
            child = subprocess.Popen(argv, cwd=cwd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                     stderr=err, start_new_session=True)
        except OSError as exc:
            raise CommandFailed(f"{argv[0]} cannot start: {exc}") from exc
        try:
            code = child.wait(timeout=SPAWN_SETTLE_SECONDS)
        except subprocess.TimeoutExpired:
            return  # still running: the terminal is up
        if code != 0:
            err.seek(0)
            said = err.read().decode(errors="replace").strip().splitlines()
            raise CommandFailed(f"{argv[0]} exited {code}" + (f": {said[-1]}" if said else ""))


def focus_window(address: str, title: str, run: Run) -> tuple[bool, str]:
    """Raise a Hyprland window by address (spike #2: hyprctl exits 0 even when the address is gone)."""
    if not HYPRLAND_ADDRESS.fullmatch(address):
        return False, f"Not focusing {title}: {address!r} is not a window address"
    done = run(["hyprctl", "dispatch", f'hl.dsp.focus({{ window = "address:{address}" }})'])
    output = (done.stdout + done.stderr).strip()
    if done.returncode != 0 or "window not found" in output:
        return False, f"Could not focus {title}: {output or 'exit ' + str(done.returncode)}"
    return True, f"Focused {title}."


def _open_client(client: dict | None, attach: list[str], name: str, spawn: Spawn, run: Run) -> tuple[bool, str]:
    """A multiplexer's client: raise its window, or start one attached when none runs."""
    if client is None:
        spawn(attach, None)
        return True, f"Opened a terminal attached to {name}."
    if client["action"] == "focus":
        return focus_window(client["address"], client["title"], run)
    return False, f"{name} is attached, but its window is not known: {describe_window(client)}."


def describe_window(window: dict) -> str:
    if window["action"] == "ambiguous-window":
        return f"{window['candidates']} windows match"
    return "no Hyprland window owns its process"


def explain_message(plan: dict) -> str:
    if plan.get("state") == "unresolved":
        return f"Not resumed or focused: {plan['reason']}."
    if plan["action"] == "ambiguous-window":
        return f"{plan['candidates']} windows match this session; not guessing which to focus."
    if plan["action"] == "ambiguous-pane":
        return f"{len(plan['candidates'])} {plan['multiplexer']} panes hold this session; not guessing which to focus."
    notes = "".join(f" {note}." for note in plan.get("notes", []))
    return f"Running in {plan['where']}; no window or pane to focus.{notes}"


def perform_open(plan: dict, status: Any, run: Run, spawn: Spawn) -> tuple[bool, str]:
    """Carry out the plan from resolve(): resume (upstream's command, D8), focus or explain. Never both."""
    action = plan["action"]
    if action == "resume":
        from sidepulse.session_actions import session_resume_command

        command = session_resume_command(status)
        if command is None:
            return False, f"{status.display_name} has no session id to resume."
        try:
            spawn(["xdg-terminal-exec", "--", "sh", "-c", command], status.cwd)
        except CommandFailed as exc:
            return False, f"Could not resume {status.display_name}: {exc}"
        return True, f"Resuming {status.display_name} in a terminal."
    if action == "focus":
        return focus_window(plan["address"], plan["title"], run)
    if action == "herdr":
        _checked(run(["herdr", "agent", "focus", plan["pane"]]), "herdr agent focus")
        return _open_client(plan["client"], ["xdg-terminal-exec", "--", "herdr"], "herdr", spawn, run)
    if action == "tmux":
        _checked(run(["tmux", "select-window", "-t", plan["pane"]]), "tmux select-window")
        _checked(run(["tmux", "select-pane", "-t", plan["pane"]]), "tmux select-pane")
        attach = ["xdg-terminal-exec", "--", "tmux", "attach-session", "-t", plan["session"]]
        return _open_client(plan["client"], attach, "tmux", spawn, run)
    return False, explain_message(plan)


def _checked(done: subprocess.CompletedProcess, what: str) -> None:
    if done.returncode != 0:
        raise CommandFailed(f"{what}: {(done.stderr or done.stdout).strip() or 'exit ' + str(done.returncode)}")


# --- The popup's session list (D13): upstream's recent-session rules -----------

# Upstream's STATUS_BAR_SESSION_HISTORY_LIMIT (status_bar.py). tests/test_sessions.py
# reads the upstream value and fails when it drifts.
SESSION_LIMIT = 10


def menu_status_sort_key(status: Any) -> tuple[int, int, float]:
    subagent_penalty = 1 if ":agent:" in status.agent_id else 0
    return (status.priority, subagent_penalty, -status.updated_at.timestamp())


def recent_statuses(snapshot: Any, retention_seconds: float, limit: int = SESSION_LIMIT) -> list[Any]:
    """Upstream's recent_statuses (status_bar.py): the fresh statuses and the completed
    stale ones within retention, one per session, most urgent and newest first.

    The menu-bar code cannot be imported on Linux, so the rules are copied here and
    tests/test_sessions.py runs upstream's own functions against them.
    """
    from sidepulse.models import AgentMode

    now = snapshot.collected_at
    listed = list(snapshot.statuses)
    listed.extend(
        status for status in snapshot.stale_statuses
        if status.mode == AgentMode.COMPLETED and status.age_seconds(now) <= retention_seconds
    )
    best: dict[tuple[str, ...], Any] = {}
    passthrough: list[Any] = []
    for status in listed:
        if not status.session_id:
            passthrough.append(status)
            continue
        key = ("session", status.provider.lower(), status.session_id)
        previous = best.get(key)
        if previous is None or menu_status_sort_key(status) < menu_status_sort_key(previous):
            best[key] = status
    ordered = [*best.values(), *passthrough]
    ordered.sort(key=lambda status: (status.priority, -status.updated_at.timestamp()))
    return ordered[:limit]


def sessions_event(statuses: list[Any]) -> dict[str, Any]:
    """The popup's Sessions rows. updated_ms lets the popup age them; the helper sends nothing per second."""
    items = []
    for status in statuses:
        state = state_for_mode(status.mode.value)
        items.append({
            "provider": status.provider.lower(),
            "session_id": status.session_id,
            "agent_id": status.agent_id,
            "name": status.display_name,
            "project": Path(status.cwd).name if status.cwd else "",
            "cwd": status.cwd,
            "state": state,
            "label": STATE_LABELS[state],
            "colour": STATE_COLOURS[state],
            "message": status.message or "",
            "updated_ms": int(status.updated_at.timestamp() * 1000),
            "resumable": bool(status.session_id),
        })
    return {"type": "sessions", "items": items}


# --- Keep awake (D11) ---

def awake_policy_should_hold(policy: str, *, agents_active: bool) -> bool:
    # Copied from upstream status_bar.py (PyObjC, not importable here); tests/test_keep_awake.py compares the AST.
    if policy == SLEEP_PREVENTION_ALWAYS:
        return True
    if policy == SLEEP_PREVENTION_AGENTS:
        return agents_active
    return False


def sleep_prevention_battery_safeguard(
    snapshot: BatterySnapshot | None,
    threshold_percent: float,
) -> tuple[bool, str]:
    # Copied from upstream status_bar.py; tests/test_keep_awake.py compares the AST.
    threshold = max(0.0, min(100.0, float(threshold_percent)))
    threshold_text = format_percent_value(threshold)
    if threshold <= 0:
        return False, "disabled"
    if snapshot is None:
        return False, f"battery unknown, threshold {threshold_text}"
    if not snapshot.battery_present:
        return False, f"no battery, threshold {threshold_text}"
    battery_text = format_percent_value(snapshot.percent)
    if snapshot.is_plugged:
        return False, f"battery {battery_text}, plugged in, threshold {threshold_text}"
    if snapshot.percent < threshold:
        return True, f"battery {battery_text}, threshold {threshold_text}"
    return False, f"battery {battery_text}, threshold {threshold_text}"


def format_percent_value(value: float | int) -> str:
    # Copied from upstream status_bar.py; tests/test_keep_awake.py compares the AST.
    number = float(value)
    if number.is_integer():
        return f"{int(number)}%"
    return f"{number:.1f}%"


def _sysfs(path: Path) -> str:
    return path.read_text().strip()


def read_battery(root: Path = POWER_SUPPLY_ROOT) -> Any:
    """Upstream's BatterySnapshot from /sys/class/power_supply (D10).

    The first battery gives the percent. An online supply that is not a battery
    (an adapter) or a charging battery means plugged in. No directory or no
    battery is a snapshot with battery_present False. An unreadable file raises.
    """
    from sidepulse.battery import BatterySnapshot

    if not root.is_dir():
        return BatterySnapshot(battery_present=False)
    batteries: list[Path] = []
    plugged = False
    for supply in sorted(root.iterdir()):
        if _sysfs(supply / "type") == "Battery":
            present = supply / "present"
            if not present.exists() or _sysfs(present) == "1":
                batteries.append(supply)
        elif (supply / "online").exists() and _sysfs(supply / "online") == "1":
            plugged = True
    if not batteries:
        return BatterySnapshot(battery_present=False, is_plugged=plugged)
    battery = batteries[0]
    status = _sysfs(battery / "status")
    # An online charger wins over a status that still says Discharging: some firmware (a ThinkPad in #9's
    # laptop pass) turns status to Charging about 5 s after the adapter goes online. "Not charging" (a charge
    # limit) and "Full" keep their own look.
    charging = status == "Charging" or (plugged and status == "Discharging")
    full = status == "Full"
    # Linux has no adapter wattage: while charging, the battery's intake (power_now, microwatts) stands in
    # for it, which is what upstream's charge speed (the charging program's pulse) reads.
    adapter_watts = 0
    if charging and (battery / "power_now").exists():
        adapter_watts = round(int(_sysfs(battery / "power_now")) / 1_000_000)
    return BatterySnapshot(
        percent=int(_sysfs(battery / "capacity")),
        is_charging=charging,
        is_charged=full,
        is_plugged=plugged or charging or full,
        battery_present=True,
        adapter_watts=adapter_watts,
    )


def read_lid_closed(run: Callable[..., subprocess.CompletedProcess] = subprocess.run) -> bool:
    """logind's LidClosed (D10). An answer it does not recognise raises: it is not guessed."""
    done = run(list(LID_COMMAND), check=True, capture_output=True, text=True, timeout=2)
    answer = done.stdout.strip()
    if answer == "b true":
        return True
    if answer == "b false":
        return False
    raise ValueError(f"busctl answered {answer!r} for LidClosed")


# Copied from upstream status_bar.py (PyObjC, not importable here); tests/test_power.py compares the AST.
def lid_closed_led_state_should_hold(
    *,
    lid_closed: bool | None,
    sleep_prevention_policy: str,
    agents_active: bool | None,
    battery_safeguard_active: bool = False,
) -> bool:
    if lid_closed is not True or agents_active is None:
        return False
    sleep_prevention_requested = awake_policy_should_hold(
        sleep_prevention_policy,
        agents_active=agents_active,
    ) and not battery_safeguard_active
    return not sleep_prevention_requested


# Copied from upstream status_bar.py; tests/test_power.py compares the AST.
def lid_close_animation_should_run(
    *,
    sleep_prevention_policy: str,
    agents_active: bool | None,
) -> bool:
    if sleep_prevention_policy == SLEEP_PREVENTION_ALWAYS:
        return False
    if sleep_prevention_policy == SLEEP_PREVENTION_AGENTS:
        return agents_active is False
    return True


def agents_awake(controller: Any, mode: Any, now: float) -> tuple[bool, str]:
    """Whether the agents hold sleep now, and why in words: upstream's should_hold_for_mode (300 s grace)."""
    if mode is None:
        return False, NO_AGENT
    active = controller.should_hold_for_mode(mode, now)
    # should_hold_for_mode compares with the previous mode; upstream records it in update_requested.
    controller.last_mode = mode
    if not active:
        return False, NO_AGENT
    if mode.value in WORKING_MODE_VALUES:
        return True, "agents are working"
    from sidepulse.keep_awake import format_duration

    return True, f"{format_duration(int(controller.grace_until_monotonic - now))} grace after an agent finished"


def awake_request(policy: str, *, agents_active: bool, agents_reason: str, battery: Any,
                  threshold: float) -> tuple[bool, str]:
    """Whether to hold sleep, and why: the policy, then the battery safeguard (upstream's sync_keep_awake)."""
    if policy == SLEEP_PREVENTION_NEVER:
        return False, "never"
    safeguard_active, text = sleep_prevention_battery_safeguard(battery, threshold)
    if not awake_policy_should_hold(policy, agents_active=agents_active):
        return False, NO_AGENT
    if safeguard_active:
        return False, f"battery safeguard, {text}"
    return True, "always" if policy == SLEEP_PREVENTION_ALWAYS else agents_reason


SAFEGUARD_PAUSED = "Keep awake paused by the battery safeguard: {} %, unplugged"
SAFEGUARD_RESUMED = "Keep awake resumed: plugged in"


def safeguard_blocks(policy: str, *, agents_active: bool, safeguard_active: bool) -> bool:
    """Whether the battery safeguard is what keeps the lock off: the policy wants it and the safeguard says no."""
    return policy != SLEEP_PREVENTION_NEVER and awake_policy_should_hold(policy, agents_active=agents_active) \
        and safeguard_active


def percent_number(value: float | int) -> str:
    """A percent's number as format_percent_value writes it (a whole number, or one decimal), without its sign."""
    number = float(value)
    return str(int(number)) if number.is_integer() else f"{number:.1f}"


def safeguard_notice(previous: bool | None, blocking: bool, holding: bool, percent: float) -> str | None:
    """The notice for a change of the safeguard's block (D11), or None. The first read (previous None) is silent,
    since a lock that was never held is not let go; so is a block that ends because the policy stopped wanting one."""
    if previous is None or blocking == previous:
        return None
    if blocking:
        return SAFEGUARD_PAUSED.format(percent_number(percent))
    return SAFEGUARD_RESUMED if holding else None


def inhibit_argv() -> list[str]:
    """The command that takes the sleep lock (D11).

    systemd-inhibit holds the lock while `cat` runs. `cat` ends when its stdin
    closes, which the helper does on release and the kernel does when the helper
    dies, so the lock cannot outlive the helper.
    """
    return ["systemd-inhibit", f"--what={INHIBIT_WHAT}", "--mode=block", f"--who={INHIBIT_WHO}",
            f"--why={INHIBIT_WHY}", "cat"]


class SleepInhibitor:
    """The sleep lock: one systemd-inhibit child while it is wanted, none otherwise (D11)."""

    def __init__(self, argv: Callable[[], list[str]] | None = None) -> None:
        self.argv = argv
        self.proc: subprocess.Popen | None = None
        self.err: Any = None
        # Why the lock could not be held, kept while it is still wanted, so a failing command is not started every tick.
        self.failed: str | None = None

    def holding(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def update(self, wanted: bool) -> str | None:
        """Take or drop the lock to match `wanted`. Returns why it is not held, or None."""
        if not wanted:
            self.release()
            self.failed = None
            return None
        if self.holding():
            return None
        if self.proc is not None:
            self.failed = self._ended()
        elif self.failed is None:
            self.failed = self._start()
        return self.failed

    def _start(self) -> str | None:
        command = self.argv() if self.argv is not None else inhibit_argv()
        # stderr goes to a file, not a pipe nobody reads: a child that writes a lot must not block.
        err = tempfile.TemporaryFile()
        try:
            self.proc = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=err)
        except OSError as exc:
            err.close()
            return f"cannot run systemd-inhibit: {exc}"
        self.err = err
        return None

    def _ended(self) -> str:
        """The child ended while the lock was wanted: say how (D14)."""
        proc, err = self.proc, self.err
        self.proc = None
        self.err = None
        code = proc.wait()
        err.seek(0)
        said = err.read().decode(errors="replace").strip().splitlines()
        err.close()
        return f"systemd-inhibit exited {code}" + (f": {said[-1]}" if said else "")

    def release(self) -> None:
        """Drop the lock: closing stdin ends `cat`, and systemd-inhibit with it."""
        proc, err = self.proc, self.err
        self.proc = None
        self.err = None
        if proc is None:
            return
        proc.stdin.close()
        try:
            proc.wait(timeout=COMMAND_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
        err.close()


class Helper:
    def __init__(self, out: TextIO) -> None:
        self.out = out
        self.capabilities: dict[str, dict[str, Any]] = {}
        self.latest_path: Path | None = None
        self.watch: DirWatch | None = None
        self.last_state: dict[str, Any] | None = None
        self.last_setup: dict[str, Any] | None = None
        self.last_devices: dict[str, Any] | None = None
        self.last_sessions: dict[str, Any] | None = None
        # The statuses the popup lists, from the last good read of latest.json (D19 rule: none while unreadable).
        self.session_statuses: list[Any] = []
        self.devices: list[Device] = []
        self.device_count: int | None = None
        # The widget's remembered displays. None until it sends them: a fresh
        # helper must not reset devices before it knows what the user chose.
        self.displays: dict[str, str] | None = None
        self.reported: set[tuple[str, str]] = set()
        self.buffer = b""
        self.ticks = 0
        self.last_hooks: dict[str, Any] | None = None
        self.last_log: dict[str, Any] | None = None
        self.last_settings: dict[str, Any] | None = None
        # Notify when a session asks (D20). `asking` is the set of sessions in Ask at the last good read;
        # None until the first read, so sessions already asking at start-up do not notify.
        self.notify_on_ask = False
        self.asking: set[tuple[str, str]] | None = None
        # Keys are the session a click opens; None for a notice with no action (the battery safeguard, D11).
        self.notifications: list[tuple[tuple[str, str] | None, subprocess.Popen, Any]] = []
        # Keep awake (D11): the agents' State as the last read saw it (None without a read), the lock,
        # the predicate's grace clock and the battery's sysfs root. The clock and root are injectable for tests.
        self.mode: Any = None
        self.inhibitor = SleepInhibitor()
        self.awake_controller: Any = None
        self.last_awake: dict[str, Any] | None = None
        # Whether the battery safeguard is what keeps the lock off (D11), as the last read saw it: None until the
        # first read, so a helper that starts blocked does not announce a lock it never held.
        self.safeguard_blocking: bool | None = None
        # The safeguard let go but the lock is not held yet (systemd-inhibit failed): "resumed" waits for it.
        self.resume_pending = False
        self.power_root = POWER_SUPPLY_ROOT
        self.clock: Callable[[], float] = time.monotonic
        # Laptop (D9, D10). The battery as the last tick read it (None: unreadable) and whether the Battery
        # Display is offered; the plugged state the 7 s preview compares with, and the preview's end; the lid
        # as it last read (None: never) and its reader (injectable); the lid animation (program text, end);
        # the active agents as the last read counted them (None: unknown); the next deadline that changes a device.
        self.battery: Any = None
        self.battery_offered = False
        self.last_plugged: bool | None = None
        self.preview_until = 0.0
        self.lid_closed: bool | None = None
        self.lid_reader: Callable[[], bool] = read_lid_closed
        self.lid_animation: tuple[str, float] | None = None
        self.active_count: int | None = None
        self.next_handover: float | None = None
        self.last_power: dict[str, Any] | None = None
        # Bar LEDs (D6, D7). The settings and State as the last read saw them; the Bar LEDs' program text
        # (None: nothing to draw); each device's program text as the last device scan read it.
        self.settings: Any = None
        self.state_readable = False
        self.bar_leds_on = False
        self.bar_program_text: str | None = None
        self.bar_problem: str | None = None
        self.bar: LedProgram | None = None
        self.device_programs: dict[str, str | None] = {}
        self.device_sources: dict[str, LedProgram] = {}
        self.frame_interval = 1.0 / IDLE_FRAME_RATE
        self.frame_problem: str | None = None
        # The popup is open in some widget copy (set-popup, D5). Device frames flow only then.
        self.popup_open = False
        self.last_frames: dict[str, Any] = {}
        self.next_tick = 0.0
        self.next_frame = 0.0

    def emit(self, event: dict[str, Any]) -> None:
        self.out.write(json.dumps(event, separators=(",", ":")) + "\n")
        self.out.flush()

    def error(self, feature: str, message: str) -> None:
        self.emit({"type": "error", "feature": feature, "message": message})

    def report(self, feature: str, message: str) -> None:
        """Like error(), but once per distinct message, for problems a tick would repeat."""
        if (feature, message) not in self.reported:
            self.reported.add((feature, message))
            self.error(feature, message)

    def resolve(self, feature: str) -> None:
        """The feature works again: clear what report() sent, so a recurrence is reported anew."""
        recovered = {entry for entry in self.reported if entry[0] == feature}
        if recovered:
            self.reported -= recovered
            self.emit({"type": "clear", "feature": feature})

    def hello(self) -> None:
        self.capabilities = probe_capabilities()
        self.emit({
            "type": "hello",
            "protocol": PROTOCOL_VERSION,
            "helper": HELPER_VERSION,
            "upstream": upstream_version(),
            "capabilities": self.capabilities,
        })
        for feature, cap in self.capabilities.items():
            if not cap["ok"]:
                self.error(feature, cap["reason"])
        if self.capabilities["state"]["ok"]:
            from sidepulse.ipc import default_latest_state_path

            self.latest_path = default_latest_state_path()
            self.watch = DirWatch(self.latest_path.parent)

    def refresh_state(self, force: bool = False) -> None:
        if self.latest_path is None:
            return
        from sidepulse.settings import load_settings

        statuses: list[Any] = []
        settings = load_settings()
        self.refresh_settings(settings, force)
        self.settings = settings
        self.bar_leds_on = settings.virtual_status_device_enabled
        try:
            snapshot = read_snapshot(
                self.latest_path,
                now=datetime.now(timezone.utc),
                idle_timeout_seconds=settings.idle_timeout_seconds,
            )
        except (OSError, ValueError) as exc:
            message = f"cannot read {self.latest_path}: {exc}"
            self.report("state", message)
            event = unavailable_state_event(message)
            # Upstream's refresh error path holds sleep as if an agent had errored (status_bar.py refresh_).
            from sidepulse.models import AgentMode

            self.mode = AgentMode.BLOCKED_ERROR
            self.state_readable = False
            self.active_count = None
        else:
            self.resolve("state")
            event = state_event(snapshot)
            self.mode = snapshot.aggregate.mode
            self.active_count = snapshot.aggregate.active_count
            self.state_readable = True
            if self.capabilities["sessions"]["ok"]:
                statuses = recent_statuses(snapshot, settings.recent_session_retention_seconds)
            self.note_asks(asking_sessions(snapshot.statuses))
        if force or event != self.last_state:
            self.last_state = event
            self.emit(event)
        self.session_statuses = statuses
        self.refresh_sessions(force)
        # frame_interval_for reads upstream names the Bar LEDs probe checks: only with that capability on (D14).
        animated = self.state_readable and self.capabilities.get("bar_leds", {}).get("ok") is True
        self.frame_interval = frame_interval_for(self.mode) if animated else 1.0 / IDLE_FRAME_RATE
        self.refresh_bar()

    def refresh_settings(self, settings: Any, force: bool = False) -> None:
        if not self.capabilities.get("settings", {}).get("ok"):
            return
        event = settings_event(settings)
        if force or event != self.last_settings:
            self.last_settings = event
            self.emit(event)

    def refresh_hooks(self, force: bool = False) -> None:
        """Each agent's hook config (D13). Read from the config files, so it is cheap to re-read."""
        if not self.capabilities.get("hooks", {}).get("ok"):
            return
        try:
            event = hooks_event()
        except (OSError, ValueError) as exc:
            self.report("hooks", f"cannot read the agent hook configs: {exc}")
            return
        problems = [f"{row['provider']}: {row['error']}" for row in event["providers"] if "error" in row]
        if problems:
            self.report("hooks", "cannot read " + "; ".join(problems))
        else:
            self.resolve("hooks")
        if force or event != self.last_hooks:
            self.last_hooks = event
            self.emit(event)

    def refresh_log(self, force: bool = False) -> None:
        if not self.capabilities.get("hook_log", {}).get("ok"):
            return
        try:
            event = hook_log_event()
        except OSError as exc:
            self.report("hook_log", f"cannot read the hook log: {exc}")
            return
        self.resolve("hook_log")
        if force or event != self.last_log:
            self.last_log = event
            self.emit(event)

    def refresh_sessions(self, force: bool = False) -> None:
        event = sessions_event(self.session_statuses)
        if force or event != self.last_sessions:
            self.last_sessions = event
            self.emit(event)

    def refresh_setup(self, force: bool = False) -> None:
        installed = self.capabilities.get("state", {}).get("ok", False)
        service_running: bool | None = None
        if self.capabilities.get("service", {}).get("ok"):
            from sidepulse.service_launch import service_is_running

            try:
                service_running = service_is_running()
            except (OSError, subprocess.SubprocessError) as exc:
                self.report("service", f"cannot check the SidePulse service: {exc}")
            else:
                self.resolve("service")
        event = setup_event(installed=installed, service_running=service_running, device_count=self.device_count)
        if force or event != self.last_setup:
            self.last_setup = event
            self.emit(event)

    def refresh_devices(self, force: bool = False) -> None:
        """Discover the devices, bring each to its remembered display (D9), and report them."""
        if not self.capabilities.get("devices", {}).get("ok"):
            self.device_count = None
            return
        try:
            self.devices = discover_device_rows()
        except OSError as exc:
            self.report("devices", f"cannot look for SidePulse devices: {exc}")
            self.devices = []
            self.device_count = None
        else:
            from sidepulse.device_writer import DeviceWriteError
            from sidepulse.settings import load_settings

            self.device_count = len(self.devices)
            failed = False
            # Nothing is reconciled until the widget has sent its remembered displays.
            pending = self.devices if self.displays is not None else []
            settings = load_settings()
            now = self.clock()
            for device in pending:
                display = display_of(self.displays, device.id, self.battery_offered)
                try:
                    apply_display(device, display, self.takeover_text(device, display, settings, now))
                except (OSError, ValueError, DeviceWriteError) as exc:
                    failed = True
                    self.report("devices", f"{device.name}: {exc}")
            # Cleared only by a pass that reached every device.
            if not failed:
                self.resolve("devices")
        # The program each device holds now, at full brightness: its LED frames play it (the popup's device rows, D21).
        from sidepulse.settings import load_settings

        settings = load_settings()
        self.device_programs = {
            device.id: full_brightness_program(shown_program(device), settings.brightness_for_device(device.id))
            for device in self.devices
        }
        self.refresh_bar()
        event = self.devices_event()
        if force or event != self.last_devices:
            self.last_devices = event
            self.emit(event)

    def devices_event(self) -> dict[str, Any]:
        from sidepulse.settings import load_settings

        settings = load_settings()
        items = []
        for device in self.devices:
            items.append({
                "id": device.id,
                "name": device.name,
                "path": device.path,
                "product": device.product,
                "kind": device.kind,
                "led_count": device.led_count,
                "display": display_of(self.displays or {}, device.id, self.battery_offered),
                "percent": round(settings.brightness_for_device(device.id) * 100 / 255),
                "colour": shown_colour(device),
            })
        return {"type": "devices", "items": items}

    def refresh(self, force: bool = False) -> None:
        self.refresh_state(force)
        self.refresh_awake()
        self.refresh_power()
        self.refresh_devices(force)
        self.refresh_setup(force)
        self.refresh_hooks(force)
        self.refresh_log(force)

    def tick(self) -> None:
        """One second's work: the rules re-run, the keep-awake grace, the battery and lid, the devices."""
        self.ticks += 1
        self.refresh_state()  # re-run the rules so ages and staleness advance
        self.refresh_awake()  # the grace runs out with the clock, not with a write
        self.refresh_power()
        self.refresh_devices()
        if self.ticks % SETUP_EVERY_TICKS == 0:
            self.refresh_setup()
            self.refresh_hooks()
            self.refresh_log()

    # --- Laptop (D9, D10): the battery, the 7 s preview and the lid ---

    def agents_active(self) -> bool | None:
        """Whether an agent is working, as upstream's lid rules read it: None when the State is unknown."""
        return None if self.active_count is None else self.active_count > 0

    def refresh_power(self) -> None:
        """Read the battery and the lid once a tick (D10). Emits `power`; a plug or unplug starts the
        7 s preview (upstream's battery_show_on_power_change); the lid's change starts its animation."""
        from sidepulse.settings import load_settings

        settings = load_settings()
        now = self.clock()
        battery_cap = self.capabilities.get("battery", {}).get("ok") is True
        battery = self.read_battery_or_none()
        self.battery = battery
        self.battery_offered = battery_cap and (battery is None or battery.battery_present)
        if battery is not None and battery.battery_present:
            if (battery_cap and self.last_plugged is not None and battery.is_plugged != self.last_plugged
                    and settings.battery_show_on_power_change):
                self.preview_until = now + settings.battery_power_change_preview_seconds
            self.last_plugged = battery.is_plugged
        if self.capabilities.get("lid", {}).get("ok") is True:
            self.refresh_lid(settings, now)
        self.emit_power(battery)
        due = [t for t in (self.preview_until, self.lid_animation[1] if self.lid_animation else 0.0) if t > now]
        self.next_handover = min(due) if due else None

    def refresh_lid(self, settings: Any, now: float) -> None:
        try:
            closed = self.lid_reader()
        except (OSError, subprocess.SubprocessError, ValueError) as exc:
            self.report("lid", f"cannot read the lid state from logind: {exc}")
            # Unknown, not the last reading: a stale "closed" would keep the devices dark (D14).
            self.lid_closed = None
            return
        self.resolve("lid")
        if self.lid_closed is not None and closed != self.lid_closed:
            self.start_lid_animation(closed, settings, now)
        self.lid_closed = closed

    def start_lid_animation(self, closed: bool, settings: Any, now: float) -> None:
        """Play the lid's animation (upstream's play_lid_animation). Closing plays only where upstream
        lets the Mac sleep (lid_close_animation_should_run); opening always plays."""
        if closed and not lid_close_animation_should_run(
                sleep_prevention_policy=settings.sleep_prevention_policy, agents_active=self.agents_active()):
            return
        from sidepulse.device_writer import DeviceWriteError, normalize_led_text, validate_led_text
        from sidepulse.settings import LID_ANIMATION_CLOSED, LID_ANIMATION_OPEN

        kind, label = (LID_ANIMATION_CLOSED, "closed") if closed else (LID_ANIMATION_OPEN, "open")
        animation = settings.lid_animation(kind)
        text = normalize_led_text(animation.program)
        try:
            validate_led_text(text)
        except DeviceWriteError as exc:
            # Its own feature: the lid reads clear "lid" every tick, which must not hide this.
            self.report("lid_animation", f"the {label} lid animation is invalid: {exc}")
            return
        self.resolve("lid_animation")
        self.lid_animation = (text, now + animation.duration_seconds + LID_ANIMATION_FUDGE_SECONDS)

    def lid_led_hold(self, settings: Any) -> bool:
        """Upstream's lid_closed_led_state_should_hold: a closed lid whose Mac may sleep keeps the LEDs dark."""
        safeguard, _ = sleep_prevention_battery_safeguard(self.battery, settings.sleep_prevention_min_battery_percent)
        return lid_closed_led_state_should_hold(
            lid_closed=self.lid_closed,
            sleep_prevention_policy=settings.sleep_prevention_policy,
            agents_active=self.agents_active(),
            battery_safeguard_active=safeguard,
        )

    def takeover_text(self, device: Device, display: str, settings: Any, now: float) -> str | None:
        """The program the helper drives a device with while it takes the LEDs (D9, D10), or None when the
        service drives it (Agent). Off is always dark. A lid animation wins, then the closed-lid hold, then
        the Battery display, then the 7 s preview on an Agent device. The battery and lid functions are
        imported only where their capability put them in use, so a missing one turns off only its feature."""
        from sidepulse.led_status import builtin_animation_program

        brightness = settings.brightness_for_device(device.id)
        dark = builtin_animation_program("off", device.led_count)
        if display == "off":
            return dark
        if self.lid_animation is not None and now < self.lid_animation[1]:
            from sidepulse.led_status import apply_brightness

            return apply_brightness(self.lid_animation[0], brightness)
        if self.lid_led_hold(settings):
            return dark
        battery = self.battery
        if display == "battery":
            # An unreadable battery leaves the device dark; PROBLEMS says why (D14: never a stale value).
            if battery is None:
                return dark
            from sidepulse.battery import program_for_battery

            return program_for_battery(battery, led_count=device.led_count, brightness=brightness)
        if (self.battery_offered and battery is not None and battery.battery_present
                and now < self.preview_until):
            from sidepulse.battery import program_for_battery

            return program_for_battery(battery, led_count=device.led_count, brightness=brightness)
        return None

    def emit_power(self, battery: Any) -> None:
        event = {
            "type": "power",
            "battery_present": None if battery is None else battery.battery_present,
            "percent": battery.percent if battery is not None and battery.battery_present else None,
            "plugged": battery.is_plugged if battery is not None else None,
            "lid_closed": self.lid_closed,
            "battery_offered": self.battery_offered,
        }
        if event != self.last_power:
            self.last_power = event
            self.emit(event)

    def release_takeovers(self) -> None:
        """On exit, hand every device this helper drives back to the service (CODING_STANDARDS: a takeover is
        released on exit). Off is the user's own choice and stays. Nothing is done before the widget has sent
        its displays, since the helper does not know what the user chose."""
        if self.displays is None:
            return
        from sidepulse.device_writer import DeviceWriteError

        for device in self.devices:
            if display_of(self.displays, device.id, self.battery_offered) == "off":
                continue
            try:
                apply_display(device, "agent")
            except (OSError, ValueError, DeviceWriteError) as exc:
                sys.stderr.write(f"cannot hand {device.name} back to the service: {exc}\n")

    # --- Bar LEDs and device frames (D6, D7, D21) ---

    def refresh_bar(self) -> None:
        """The Bar LEDs' program for the current State and product (D6, D21). Nothing is drawn when the
        switch is off, the State cannot be read, or the feature is unavailable (CODING_STANDARDS: shown
        state follows its source)."""
        if not self.capabilities.get("bar_leds", {}).get("ok"):
            self.bar_program_text = None
            return
        count = bar_led_count(self.devices)
        if self.bar is None or self.bar.led_count != count:
            self.bar = LedProgram(count)
        text = None
        self.bar_problem = None
        if self.bar_leds_on and self.state_readable and self.settings is not None:
            from sidepulse.device_writer import DeviceWriteError

            try:
                text = bar_program(self.settings, self.mode, count)
            except (DeviceWriteError, ValueError) as exc:  # e.g. an empty custom animation (upstream's check)
                self.bar_problem = f"Bar LEDs: {exc}"
        self.bar_program_text = text

    def pump_frames(self, now: float) -> None:
        """Step every LED program and send each frame whose colours changed (D6)."""
        if not self.capabilities.get("bar_leds", {}).get("ok"):
            return
        ms = now * 1000.0
        problems: list[str] = [self.bar_problem] if self.bar_problem else []
        if self.bar is None or self.bar_program_text is None:
            self.emit_frame("bar", None, target="bar")
        else:
            problem, leds = self.play(self.bar, self.bar_program_text, ms)
            if problem:
                problems.append(f"Bar LEDs: {problem}")
            self.emit_frame("bar", leds, target="bar")
        # Device frames are for the popup's rows (D21), sent only while it is open, and for the bar
        # icon (D12), which is always on show: its device steps even with the popup closed, while it
        # shows Agent. Each device steps once per frame, whoever reads it.
        icon = icon_device(self.devices)
        if icon is not None and display_of(self.displays or {}, icon.id, self.battery_offered) == "off":
            icon = None
        icon_leds: list[list[int]] | None = None
        for device in self.devices:
            if not self.popup_open and device is not icon:
                continue
            source = self.device_sources.get(device.id)
            if source is None or source.led_count != device.led_count:
                source = self.device_sources[device.id] = LedProgram(device.led_count)
            problem, leds = self.play(source, self.device_programs.get(device.id), ms)
            if problem:
                problems.append(f"{device.name}: {problem}")
            if self.popup_open:
                self.emit_frame(f"device:{device.id}", leds, target="device", device=device.id)
            if device is icon:
                icon_leds = leds
        # A null is sent only to clear an icon that was lit; with no device ever, nothing is sent.
        if icon_leds is not None or "icon" in self.last_frames:
            self.emit_frame("icon", icon_leds, target="icon")
        self.set_frame_problem("; ".join(problems) if problems else None)

    def set_popup(self, command: dict[str, Any]) -> None:
        """The popup opened or closed (the widget sends the OR of its copies, D5). On open, the device
        rows get their frames at the next pump, so none is suppressed as unchanged."""
        is_open = command.get("open")
        if not isinstance(is_open, bool):
            self.error("command", "set-popup needs open: true or false")
            return
        if is_open and not self.popup_open:
            for device in self.devices:
                self.last_frames.pop(f"device:{device.id}", None)
            self.next_frame = self.clock()
        self.popup_open = is_open

    def play(self, source: LedProgram, text: str | None, ms: float) -> tuple[str | None, list[list[int]]]:
        """One source's problem and LEDs now. An engine that raises is reported and its LEDs go dark; the
        engine is rebuilt on the next frame, so a transient failure recovers (D14: fail loudly, keep running)."""
        try:
            problem = source.load(text, ms)
            return problem, source.frame(ms)
        except Exception as exc:  # the boundary to upstream's wasm engine, through wasmtime
            source.engine, source.text, source.lit = None, None, False
            return f"the engine failed: {type(exc).__name__}: {exc}", source.frame(ms)

    def emit_frame(self, key: str, leds: list[list[int]] | None, *, target: str, device: str | None = None) -> None:
        """Send a frame when its colours differ from the last one sent for this key."""
        if key in self.last_frames and self.last_frames[key] == leds:
            return
        self.last_frames[key] = leds
        event: dict[str, Any] = {"type": "frame", "target": target, "leds": leds}
        if device is not None:
            event["device"] = device
        self.emit(event)

    def set_frame_problem(self, message: str | None) -> None:
        """A program that does not play is reported under PROBLEMS (D14); the report changes with the problem."""
        if message == self.frame_problem:
            return
        self.frame_problem = message
        if message is None:
            self.emit({"type": "clear", "feature": "frames"})
        else:
            self.error("frames", message)

    # --- Keep awake (D11) ---

    def refresh_awake(self) -> None:
        """Hold or release the sleep lock to match the policy, and report what it does."""
        from sidepulse.settings import load_settings

        settings = load_settings()
        policy = settings.sleep_prevention_policy
        battery = self.read_battery_or_none()
        threshold = settings.sleep_prevention_min_battery_percent
        if self.capabilities.get("keep_awake", {}).get("ok"):
            if self.awake_controller is None:
                from sidepulse.keep_awake import KeepAwakeController

                self.awake_controller = KeepAwakeController()
            agents_active, agents_reason = agents_awake(self.awake_controller, self.mode, self.clock())
            hold, reason = awake_request(
                policy,
                agents_active=agents_active,
                agents_reason=agents_reason,
                battery=battery,
                threshold=threshold,
            )
            safeguard_active, _ = sleep_prevention_battery_safeguard(battery, threshold)
            blocking = safeguard_blocks(policy, agents_active=agents_active, safeguard_active=safeguard_active)
        else:
            hold, reason, blocking = False, "keep awake is unavailable", False
        error = self.inhibitor.update(hold)
        if error is None:
            self.resolve("keep_awake")
        else:
            self.report("keep_awake", error)
        event = {"type": "awake", "policy": policy, "holding": self.inhibitor.holding(), "reason": reason}
        if event != self.last_awake:
            self.last_awake = event
            self.emit(event)
        # "Resumed" says the lock is held again, so it waits for the inhibitor, not the request; a request
        # that ends first drops it. The PROBLEMS line says why a lock cannot be taken (D14).
        notice = safeguard_notice(self.safeguard_blocking, blocking, hold,
                                  battery.percent if battery is not None else 0.0)
        self.safeguard_blocking = blocking
        if notice == SAFEGUARD_RESUMED:
            self.resume_pending, notice = True, None
        elif notice is not None or not hold:
            self.resume_pending = False
        if self.resume_pending and self.inhibitor.holding():
            self.resume_pending, notice = False, SAFEGUARD_RESUMED
        if notice is not None:
            self.notify_safeguard(notice)

    def notify_safeguard(self, text: str) -> None:
        """A plain desktop notice about the battery safeguard (D11): not the Ask switch, no action, no wait."""
        if not self.capabilities.get("notify", {}).get("ok"):
            return  # the Notifications problem line already says why (hello)
        self.start_notice(plain_notify_argv(text), None)

    def read_battery_or_none(self) -> Any:
        """The battery for the safeguard. Unreadable is reported (D14) and counts as unknown, as upstream does."""
        try:
            battery = read_battery(self.power_root)
        except (OSError, ValueError) as exc:
            self.report("power", f"cannot read the battery: {exc}")
            return None
        self.resolve("power")
        return battery

    def set_policy(self, command: dict[str, Any]) -> None:
        """Save the keep-awake policy through upstream's settings (D11, D13), then apply it."""
        if not self.capabilities.get("settings", {}).get("ok"):
            self.error("settings", "the settings are unavailable: " + self.capabilities.get("settings", {}).get("reason", ""))
            return
        policy = command.get("policy")
        if policy not in SLEEP_PREVENTION_CHOICES:
            self.error("command", "set-policy needs policy: never, agents or always")
            return
        try:
            change_settings(lambda s: s.with_sleep_prevention_policy(policy))
        except (OSError, ValueError) as exc:
            self.error("settings", f"cannot save the keep-awake policy: {exc}")
            return
        self.refresh_awake()

    def set_agent_list(self, command: dict[str, Any]) -> None:
        if not self.capabilities.get("settings", {}).get("ok"):
            self.error("settings", "the settings are unavailable: " + self.capabilities.get("settings", {}).get("reason", ""))
            return
        change = timing_change(command)
        if isinstance(change, str):
            self.error("command", change)
            return
        idle, retention = change
        try:
            change_settings(lambda s: s.with_agent_list_timing(
                idle_timeout_seconds=idle, recent_session_retention_seconds=retention))
        except (OSError, ValueError) as exc:
            self.error("settings", f"cannot save the timeouts: {exc}")
            return
        self.refresh_state(force=True)

    def set_bar_leds(self, command: dict[str, Any]) -> None:
        if not self.capabilities.get("settings", {}).get("ok"):
            self.error("settings", "the settings are unavailable: " + self.capabilities.get("settings", {}).get("reason", ""))
            return
        enabled = command.get("enabled")
        if not isinstance(enabled, bool):
            self.error("command", "set-bar-leds needs enabled: true or false")
            return
        try:
            change_settings(lambda s: s.with_virtual_status_device(enabled))
        except (OSError, ValueError) as exc:
            self.error("settings", f"cannot save the Bar LEDs switch: {exc}")
            return
        self.refresh_state(force=True)

    def set_notify(self, command: dict[str, Any]) -> None:
        enabled = command.get("enabled")
        if not isinstance(enabled, bool):
            self.error("command", "set-notify needs enabled: true or false")
            return
        self.notify_on_ask = enabled

    def hook_command(self, action: str, command: dict[str, Any]) -> None:
        """Install or remove one agent's hooks (D13), then re-read what the configs say now."""
        if not self.capabilities.get("hooks", {}).get("ok"):
            self.error("hooks", "the hooks are unavailable: " + self.capabilities.get("hooks", {}).get("reason", ""))
            return
        from sidepulse.providers import HOOK_PROVIDERS

        provider = command.get("provider")
        if provider not in HOOK_PROVIDERS:
            self.error("command", f"{action} needs a provider: one of {', '.join(HOOK_PROVIDERS)}")
            return
        try:
            ok, message = run_hook_command(action, provider, sys.executable)
        except (OSError, subprocess.SubprocessError) as exc:
            ok, message = False, f"cannot run the SidePulse CLI: {exc}"
        self.emit({"type": "result", "command": f"hooks-{action}", "provider": provider, "ok": ok,
                   "message": message})
        self.refresh_hooks(force=True)
        self.refresh_log(force=True)

    # --- Notify when a session asks (D20) ---

    def note_asks(self, asking: dict[tuple[str, str], Any]) -> None:
        """Notify for each session that entered Ask since the last good read (once per entry)."""
        previous = self.asking
        self.asking = set(asking)
        if previous is None or not self.notify_on_ask:
            return
        if not self.capabilities.get("notify", {}).get("ok"):
            return
        for key in asking:
            if key not in previous:
                self.notify(asking[key])

    def notify(self, status: Any) -> None:
        summary, body = notification_text(status)
        self.start_notice(notify_argv(summary, body), (status.provider.lower(), status.session_id))

    def start_notice(self, argv: list[str], key: tuple[str, str] | None) -> None:
        """Start one notify-send child; key names the session a click opens, None for a notice with no action."""
        # stderr goes to a file, not a pipe nobody reads: a notice that stays up must not block.
        err = tempfile.TemporaryFile()
        try:
            proc = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=err)
        except OSError as exc:
            err.close()
            self.report("notify", f"cannot run notify-send: {exc}")
            return
        os.set_blocking(proc.stdout.fileno(), False)
        self.notifications.append((key, proc, err))

    def pump_notifications(self, timeout: float = 0.0) -> None:
        """Read the notices: a click prints the action's name and opens the session; the end of output ends the notice."""
        if not self.notifications:
            if timeout > 0:
                time.sleep(timeout)
            return
        ready, _, _ = select.select([proc.stdout.fileno() for _, proc, _ in self.notifications], [], [], timeout)
        for key, proc, err in list(self.notifications):
            if proc.stdout.fileno() not in ready:
                continue
            try:
                data = os.read(proc.stdout.fileno(), 4096)
            except BlockingIOError:
                continue
            if data:
                if key is not None and NOTIFY_ACTION in data.decode(errors="replace").split():
                    self.open_session({"provider": key[0], "session_id": key[1]})
                continue
            self.finish_notification(proc, err)

    def finish_notification(self, proc: subprocess.Popen, err: Any) -> None:
        """The notice is gone: its exit code says whether notify-send itself failed (D14)."""
        self.notifications = [entry for entry in self.notifications if entry[1] is not proc]
        proc.stdout.close()
        try:
            code = proc.wait(timeout=COMMAND_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired:
            proc.kill()
            code = proc.wait()
        if code != 0:
            err.seek(0)
            said = err.read().decode(errors="replace").strip().splitlines()
            self.error("notify", f"notify-send exited {code}" + (f": {said[-1]}" if said else ""))
        err.close()

    def close_notifications(self) -> None:
        """The helper is ending: a notice still up goes with it, so no click can reach a dead helper."""
        for _, proc, err in self.notifications:
            if proc.poll() is None:
                proc.terminate()
            proc.wait()
            err.close()
        self.notifications = []

    def set_displays(self, command: dict[str, Any]) -> None:
        displays = command.get("displays")
        if not isinstance(displays, dict):
            self.error("command", "set-displays needs an object of device id to display")
            return
        unknown = [value for value in displays.values() if value not in DISPLAYS]
        if unknown:
            self.error("command", f"unknown display {unknown[0]!r}; use agent or off")
            return
        self.displays = {str(key): value for key, value in displays.items()}
        self.refresh_devices(force=True)

    def set_brightness(self, command: dict[str, Any]) -> None:
        device_id = command.get("device")
        percent = command.get("percent")
        valid = isinstance(device_id, str) and isinstance(percent, (int, float)) and not isinstance(percent, bool)
        if not valid or not 0 <= percent <= 100:
            self.error("command", "set-brightness needs a device id and a percent from 0 to 100")
            return
        device = next((d for d in self.devices if d.id == device_id), None)
        if device is None:
            self.error("command", f"no connected SidePulse device {device_id}")
            return
        try:
            set_device_brightness(device, percent)
        except (OSError, ValueError) as exc:
            self.error("devices", f"{device.name}: {exc}")
        self.refresh_devices(force=True)

    def open_session(self, command: dict[str, Any]) -> None:
        """Answer with a `result`: the D8 lookup's outcome for one listed session (ok closes the popup)."""
        provider = command.get("provider")
        session_id = command.get("session_id")
        if not isinstance(provider, str) or not isinstance(session_id, str):
            self.error("command", "open-session needs a provider and a session_id")
            return
        if not self.capabilities.get("sessions", {}).get("ok"):
            self.error("command", "open-session is unavailable: " + self.capabilities.get("sessions", {}).get("reason", ""))
            return
        status = next((s for s in self.session_statuses
                       if s.provider.lower() == provider and s.session_id == session_id), None)
        if status is None:
            self.emit({"type": "result", "command": "open-session", "ok": False,
                       "message": "That session is no longer listed; refresh the popup and try again."})
            return
        plan: dict[str, Any] = {"action": "explain"}
        try:
            plan = resolve(provider, session_id, status.cwd)
            ok, message = perform_open(plan, status, run_command, spawn_detached)
        except CommandFailed as exc:
            ok, message = False, f"Could not open {status.display_name}: {exc}"
        self.emit({"type": "result", "command": "open-session", "ok": ok, "action": plan["action"],
                   "message": message})

    def handle_line(self, raw: bytes) -> None:
        try:
            command = json.loads(raw)
        except ValueError as exc:
            self.error("command", f"not JSON: {exc}")
            return
        name = command.get("cmd") if isinstance(command, dict) else None
        if name == "refresh":
            self.refresh(force=True)
        elif name == "start-service":
            ok, message = start_service(sys.executable)
            self.emit({"type": "result", "command": "start-service", "ok": ok, "message": message})
            self.refresh_setup(force=True)
        elif name == "set-displays":
            self.set_displays(command)
        elif name == "set-brightness":
            self.set_brightness(command)
        elif name == "open-session":
            self.open_session(command)
        elif name in ("hooks-install", "hooks-uninstall"):
            self.hook_command(name.removeprefix("hooks-"), command)
        elif name == "set-agent-list":
            self.set_agent_list(command)
        elif name == "set-bar-leds":
            self.set_bar_leds(command)
        elif name == "set-notify":
            self.set_notify(command)
        elif name == "set-popup":
            self.set_popup(command)
        elif name == "set-policy":
            self.set_policy(command)
        else:
            self.error("command", f"unknown command {name!r}")

    def run(self, stdin_fd: int) -> None:
        try:
            self.hello()
            self.refresh(force=True)
            self.loop(stdin_fd)
        finally:
            self.close_notifications()
            self.release_takeovers()
            self.inhibitor.release()

    def loop(self, stdin_fd: int) -> None:
        # Two deadlines: the one-second tick (state, awake, devices) and the frame (D6). A wake-up runs only
        # the work whose deadline has passed, so 60 frames a second cost 60 pumps, not 60 full ticks.
        now = self.clock()
        self.next_tick = now + TICK_SECONDS
        self.next_frame = now + self.frame_interval
        while True:
            fds = [stdin_fd]
            if self.watch is not None:
                try:
                    self.watch.open()
                except OSError as exc:
                    # Ticks still re-read the file every second, so only latency is lost.
                    self.report("watch", f"inotify unavailable, polling every second: {exc}")
                    self.watch = None
                else:
                    if self.watch.fd is not None:
                        fds.append(self.watch.fd)
            fds.extend(proc.stdout.fileno() for _, proc, _ in self.notifications)
            deadlines = [self.next_tick, self.next_frame]
            if self.next_handover is not None:
                deadlines.append(self.next_handover)
            timeout = max(0.0, min(deadlines) - self.clock())
            ready, _, _ = select.select(fds, [], [], timeout)
            if stdin_fd in ready:
                chunk = os.read(stdin_fd, 65536)
                if not chunk:
                    return  # the widget closed stdin: the shell is gone
                self.buffer += chunk
                while b"\n" in self.buffer:
                    line, self.buffer = self.buffer.split(b"\n", 1)
                    if line.strip():
                        self.handle_line(line)
            self.pump_notifications()
            if self.watch is not None and self.watch.fd in ready and "latest.json" in self.watch.names():
                self.refresh_state()
            now = self.clock()
            if self.next_handover is not None and now >= self.next_handover:
                # A battery preview or lid animation ends: its device hands back now, not at the next tick.
                self.next_handover = None
                self.refresh_devices()
            if now >= self.next_tick:
                self.next_tick = now + TICK_SECONDS
                self.tick()
            if now >= self.next_frame:
                self.next_frame = now + self.frame_interval
                self.pump_frames(now)


def stop_on_sigterm(signum: int, frame: Any) -> None:
    """Quickshell stops the helper with SIGTERM: leave through run()'s cleanup, which hands taken devices
    back and drops the sleep lock. A second SIGTERM during that cleanup is ignored."""
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    sys.exit(0)


def main() -> int:
    signal.signal(signal.SIGTERM, stop_on_sigterm)
    try:
        Helper(sys.stdout).run(sys.stdin.fileno())
    except BrokenPipeError:
        pass  # the widget stopped reading: nothing left to tell
    return 0


if __name__ == "__main__":
    sys.exit(main())
