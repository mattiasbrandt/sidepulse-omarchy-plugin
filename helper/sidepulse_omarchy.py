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
  devices   connected SidePulse devices: id, product, LED count, remembered
            Display (agent, off), brightness percent and the colour its LEDs
            show now (null when dark)

Commands (stdin): {"cmd": "refresh"}, {"cmd": "start-service"},
{"cmd": "set-displays", "displays": {device id: display}} (the widget's full
remembered map; the helper reconciles every connected device to it, D9), and
{"cmd": "set-brightness", "device": id, "percent": 0-100}.

It never binds events.sock: that socket belongs to upstream's service (D4).
"""

from __future__ import annotations

import ctypes
import ctypes.util
import importlib
import importlib.metadata
import json
import os
import re
import select
import struct
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, TextIO

PROTOCOL_VERSION = 1
HELPER_VERSION = "dev"
TICK_SECONDS = 1.0
SETUP_EVERY_TICKS = 5
SERVICE_START_TIMEOUT_SECONDS = 60.0
INSTALL_COMMAND = "curl -fsSL https://sidepulse.io/setup.sh | bash"
# A device's Display (D9). Upstream stores only Agent or Manual ("custom") on
# Linux, so Off holds the device in Manual. Battery joins with #11.
DISPLAYS = ("agent", "off")
DEFAULT_DISPLAY = "agent"
UPSTREAM_DISPLAY = {"agent": "agent", "off": "custom"}
PRODUCT_NAMES = {"dot": "SidePulse Dot", "pro": "SidePulse Pro"}

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
    "service": {"sidepulse.service_launch": ("service_is_running",)},
    "devices": {
        "sidepulse.device_writer": ("discover_devices", "write_led_program"),
        "sidepulse.settings": ("load_settings", "save_settings"),
        "sidepulse.led_status": ("led_count_for_target", "builtin_animation_program"),
    },
}
WASMTIME_REASON = "wasmtime is not installed in the SidePulse venv; the Bar LEDs need it"

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
    caps["bar_leds"] = _probe_wasmtime()
    return caps


def _probe_names(modules: dict[str, tuple[str, ...]]) -> dict[str, Any]:
    for module_name, names in modules.items():
        try:
            module = importlib.import_module(module_name)
        except ImportError as exc:
            return {"ok": False, "reason": f"{module_name} cannot be imported: {exc}"}
        for name in names:
            if not hasattr(module, name):
                return {"ok": False, "reason": f"{module_name} has no {name}; upstream changed"}
    return {"ok": True, "reason": ""}


def _probe_wasmtime() -> dict[str, Any]:
    try:
        importlib.import_module("wasmtime")
    except ImportError:
        return {"ok": False, "reason": WASMTIME_REASON}
    return {"ok": True, "reason": ""}


def idle_timeout_seconds() -> float:
    from sidepulse.settings import load_settings

    return load_settings().idle_timeout_seconds


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


def save_device_settings(change: Any) -> None:
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


def display_of(displays: dict[str, str], device_id: str) -> str:
    """The widget's remembered Display for a device: Agent when none is
    remembered, or when the value is not one this helper offers."""
    display = displays.get(device_id, DEFAULT_DISPLAY)
    return display if display in DISPLAYS else DEFAULT_DISPLAY


def apply_display(device: Device, display: str) -> None:
    """Bring one device to its remembered Display (D9).

    Agent: upstream's `agent`, so the service repaints within its next poll.
    Off: upstream's `custom` (Manual), so the service stops writing, and the
    device is checked on every tick: the service may write an agent program it
    planned before reading `custom`, so whenever the device no longer shows
    upstream's off program it is written again.
    """
    from sidepulse.led_status import builtin_animation_program
    from sidepulse.device_writer import write_led_program
    from sidepulse.settings import load_settings

    upstream = UPSTREAM_DISPLAY[display]
    if load_settings().display_for_device(device.id) != upstream:
        save_device_settings(
            lambda s: s.with_device_display(device.id, upstream, name=device.name, path=device.path))
    if upstream == "custom":
        program = builtin_animation_program("off", device.led_count)
        if shown_program(device) != program.strip():
            write_led_program(program, device_path=device.target)


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


def set_device_brightness(device: Device, percent: float) -> None:
    """Upstream's brightness is 0-255 per device; the widget speaks percent."""
    from sidepulse.settings import normalize_brightness

    value = normalize_brightness(percent * 255 / 100)
    save_device_settings(
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


def claude_pid(session_id: str, proc: Proc, home: Path) -> int | None:
    root = Path(os.environ.get("CLAUDE_CONFIG_DIR", home / ".claude")) / "sessions"
    for path in root.glob("*.json"):
        try:
            data = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        if data.get("sessionId") != session_id:
            continue
        pid = int(data["pid"])
        if proc.starttime(pid) == str(data.get("procStart")):
            return pid
    return None


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


class Helper:
    def __init__(self, out: TextIO) -> None:
        self.out = out
        self.capabilities: dict[str, dict[str, Any]] = {}
        self.latest_path: Path | None = None
        self.watch: DirWatch | None = None
        self.last_state: dict[str, Any] | None = None
        self.last_setup: dict[str, Any] | None = None
        self.last_devices: dict[str, Any] | None = None
        self.devices: list[Device] = []
        self.device_count: int | None = None
        # The widget's remembered displays. None until it sends them: a fresh
        # helper must not reset devices before it knows what the user chose.
        self.displays: dict[str, str] | None = None
        self.reported: set[tuple[str, str]] = set()
        self.buffer = b""
        self.ticks = 0

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
        try:
            snapshot = read_snapshot(
                self.latest_path,
                now=datetime.now(timezone.utc),
                idle_timeout_seconds=idle_timeout_seconds(),
            )
        except (OSError, ValueError) as exc:
            message = f"cannot read {self.latest_path}: {exc}"
            self.report("state", message)
            event = unavailable_state_event(message)
        else:
            self.resolve("state")
            event = state_event(snapshot)
        if force or event != self.last_state:
            self.last_state = event
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

            self.device_count = len(self.devices)
            failed = False
            # Nothing is reconciled until the widget has sent its remembered displays.
            pending = self.devices if self.displays is not None else []
            for device in pending:
                try:
                    apply_display(device, display_of(self.displays, device.id))
                except (OSError, ValueError, DeviceWriteError) as exc:
                    failed = True
                    self.report("devices", f"{device.name}: {exc}")
            # Cleared only by a pass that reached every device.
            if not failed:
                self.resolve("devices")
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
                "display": display_of(self.displays or {}, device.id),
                "percent": round(settings.brightness_for_device(device.id) * 100 / 255),
                "colour": shown_colour(device),
            })
        return {"type": "devices", "items": items}

    def refresh(self, force: bool = False) -> None:
        self.refresh_state(force)
        self.refresh_devices(force)
        self.refresh_setup(force)

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
        else:
            self.error("command", f"unknown command {name!r}")

    def run(self, stdin_fd: int) -> None:
        self.hello()
        self.refresh(force=True)
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
            ready, _, _ = select.select(fds, [], [], TICK_SECONDS)
            if stdin_fd in ready:
                chunk = os.read(stdin_fd, 65536)
                if not chunk:
                    return  # the widget closed stdin: the shell is gone
                self.buffer += chunk
                while b"\n" in self.buffer:
                    line, self.buffer = self.buffer.split(b"\n", 1)
                    if line.strip():
                        self.handle_line(line)
            if self.watch is not None and self.watch.fd in ready and "latest.json" in self.watch.names():
                self.refresh_state()
            self.ticks += 1
            self.refresh_state()  # re-run the rules so ages and staleness advance
            self.refresh_devices()
            if self.ticks % SETUP_EVERY_TICKS == 0:
                self.refresh_setup()


def main() -> int:
    try:
        Helper(sys.stdout).run(sys.stdin.fileno())
    except BrokenPipeError:
        pass  # the widget stopped reading: nothing left to tell
    return 0


if __name__ == "__main__":
    sys.exit(main())
