"""SidePulse helper for the Omarchy bar widget (D4, D5, D14, D19).

Runs under the SidePulse venv's python (`python -I helper/sidepulse_omarchy.py`)
and imports upstream's own code. It writes NDJSON events to stdout and reads
commands, one JSON object per line, from stdin. It exits when stdin closes.

Events (stdout):
  hello     protocol, helper and upstream versions, capabilities {feature: {ok, reason}}
  state     four-state label and colour, urgent (Ask), active_count, top session
  setup     ordered items still missing (install, service, device) with an action
  result    outcome of a command that changes something (start-service)
  error     a feature failed or a command was refused; the message says why

Commands (stdin): {"cmd": "refresh"}, {"cmd": "start-service"}.

It never binds events.sock: that socket belongs to upstream's service (D4).
"""

from __future__ import annotations

import ctypes
import ctypes.util
import importlib
import importlib.metadata
import json
import os
import select
import struct
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, TextIO

PROTOCOL_VERSION = 1
HELPER_VERSION = "dev"
TICK_SECONDS = 1.0
SETUP_EVERY_TICKS = 5
SERVICE_START_TIMEOUT_SECONDS = 60.0
INSTALL_COMMAND = "curl -fsSL https://sidepulse.io/setup.sh | bash"

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
    "devices": {"sidepulse.device_writer": ("discover_devices",)},
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


class Helper:
    def __init__(self, out: TextIO) -> None:
        self.out = out
        self.capabilities: dict[str, dict[str, Any]] = {}
        self.latest_path: Path | None = None
        self.watch: DirWatch | None = None
        self.last_state: dict[str, Any] | None = None
        self.last_setup: dict[str, Any] | None = None
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
            self.report("state", f"cannot read {self.latest_path}: {exc}")
            return
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
        device_count: int | None = None
        if self.capabilities.get("devices", {}).get("ok"):
            from sidepulse.device_writer import discover_devices

            try:
                device_count = len(discover_devices())
            except OSError as exc:
                self.report("devices", f"cannot look for SidePulse devices: {exc}")
        event = setup_event(installed=installed, service_running=service_running, device_count=device_count)
        if force or event != self.last_setup:
            self.last_setup = event
            self.emit(event)

    def refresh(self, force: bool = False) -> None:
        self.refresh_state(force)
        self.refresh_setup(force)

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
