"""Keep awake while agents work (D11): the policy, the predicate and the sleep lock.

The predicate is upstream's KeepAwakeController.should_hold_for_mode (grace
after an agent finishes) plus upstream's battery safeguard, which lives in
status_bar.py (PyObjC, so it is copied and checked here against the source).
The lock is systemd-inhibit, faked by conftest.py on PATH.
"""

from __future__ import annotations

import ast
import copy
import io
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest
import sidepulse
from sidepulse import keep_awake
from sidepulse import settings as upstream_settings
from sidepulse.battery import BatterySnapshot
from sidepulse.models import AgentMode

import sidepulse_omarchy as helper
from test_protocol import Helper as ProtocolHelper, status_row

HELPER_DIR = Path(__file__).resolve().parent.parent / "helper"
GRACE = keep_awake.AWAKE_GRACE_SECONDS


def upstream_status_bar() -> Path:
    return Path(sidepulse.__file__).parent / "status_bar.py"


def function_ast(path: Path, name: str, *, drop_docstring: bool) -> str:
    tree = ast.parse(path.read_text())
    (node,) = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == name]
    node = copy.deepcopy(node)
    first = node.body[0] if node.body else None
    if drop_docstring and isinstance(first, ast.Expr) and isinstance(getattr(first, "value", None), ast.Constant):
        node.body = node.body[1:]
    return ast.dump(node)


def alive(pid: int) -> bool:
    """False once the process has exited, or is a zombie waiting for its parent."""
    try:
        with open(f"/proc/{pid}/stat") as stat:
            return stat.read().split(")")[-1].split()[0] != "Z"
    except FileNotFoundError:
        return False


def wait_until(predicate, timeout: float = 10.0, what: str = "condition") -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.05)
    raise AssertionError(f"timed out after {timeout}s waiting for {what}")


def locks_taken(log: Path) -> list[tuple[int, str]]:
    if not log.exists():
        return []
    rows = []
    for line in log.read_text().splitlines():
        pid, _, argv = line.partition(" ")
        rows.append((int(pid), argv))
    return rows


def taken(log: Path, count: int = 1) -> list[tuple[int, str]]:
    """The locks the fake logged, once there are `count` of them: the shim writes its line just after the spawn."""
    wait_until(lambda: len(locks_taken(log)) >= count, what=f"{count} lock(s) to be taken")
    return locks_taken(log)


def make_supply(root: Path, name: str, **files: str) -> None:
    directory = root / name
    directory.mkdir(parents=True, exist_ok=True)
    for key, value in files.items():
        (directory / key).write_text(value + "\n")


def battery_tree(root: Path, *, percent: int, plugged: bool) -> Path:
    make_supply(root, "BAT0", type="Battery", present="1", capacity=str(percent),
                status="Charging" if plugged else "Discharging")
    make_supply(root, "AC", type="Mains", online="1" if plugged else "0")
    return root


# --- Policy ---

def test_policy_values_are_upstream_settings_values():
    assert helper.SLEEP_PREVENTION_NEVER == upstream_settings.SLEEP_PREVENTION_NEVER == "never"
    assert helper.SLEEP_PREVENTION_AGENTS == upstream_settings.SLEEP_PREVENTION_AGENTS == "agents"
    assert helper.SLEEP_PREVENTION_ALWAYS == upstream_settings.SLEEP_PREVENTION_ALWAYS == "always"


@pytest.mark.parametrize("name", ["awake_policy_should_hold", "sleep_prevention_battery_safeguard", "format_percent_value"])
def test_copied_upstream_functions_are_unchanged(name):
    upstream = function_ast(upstream_status_bar(), name, drop_docstring=True)
    ours = function_ast(Path(helper.__file__), name, drop_docstring=True)
    assert ours == upstream, f"{name} drifted from upstream status_bar.py"


# --- The predicate: grace and transitions (upstream's should_hold_for_mode) ---

def test_working_agents_are_held_under_the_agents_policy():
    ctl = keep_awake.KeepAwakeController()
    assert helper.agents_awake(ctl, AgentMode.WORKING, 0.0) == (True, "agents are working")
    assert helper.agents_awake(ctl, AgentMode.TOOL_RUNNING, 1.0)[0] is True
    assert helper.agents_awake(ctl, AgentMode.LONG_TASK_PROGRESS, 2.0)[0] is True


def test_a_finished_agent_is_held_for_exactly_the_grace():
    ctl = keep_awake.KeepAwakeController()
    active, reason = helper.agents_awake(ctl, AgentMode.COMPLETED, 1000.0)
    assert active is True and "grace" in reason
    assert helper.agents_awake(ctl, AgentMode.COMPLETED, 1000.0 + GRACE - 0.1)[0] is True
    assert helper.agents_awake(ctl, AgentMode.COMPLETED, 1000.0 + GRACE) == (False, "no agent is working")


@pytest.mark.parametrize("mode", [AgentMode.WAITING_FOR_INPUT, AgentMode.BLOCKED_ERROR])
def test_ask_and_error_get_the_same_grace(mode):
    ctl = keep_awake.KeepAwakeController()
    assert helper.agents_awake(ctl, mode, 0.0)[0] is True
    assert helper.agents_awake(ctl, mode, GRACE - 0.1)[0] is True
    assert helper.agents_awake(ctl, mode, GRACE)[0] is False


def test_a_new_turn_cancels_the_grace_and_the_next_finish_starts_a_new_one():
    ctl = keep_awake.KeepAwakeController()
    helper.agents_awake(ctl, AgentMode.COMPLETED, 0.0)
    assert helper.agents_awake(ctl, AgentMode.WORKING, 100.0) == (True, "agents are working")
    assert helper.agents_awake(ctl, AgentMode.COMPLETED, 200.0)[0] is True
    assert helper.agents_awake(ctl, AgentMode.COMPLETED, 200.0 + GRACE - 0.1)[0] is True
    assert helper.agents_awake(ctl, AgentMode.COMPLETED, 200.0 + GRACE)[0] is False


def test_moving_between_finished_modes_restarts_the_grace_as_upstream_does():
    ctl = keep_awake.KeepAwakeController()
    helper.agents_awake(ctl, AgentMode.COMPLETED, 0.0)
    assert helper.agents_awake(ctl, AgentMode.WAITING_FOR_INPUT, 100.0)[0] is True
    assert helper.agents_awake(ctl, AgentMode.WAITING_FOR_INPUT, 100.0 + GRACE - 0.1)[0] is True
    assert helper.agents_awake(ctl, AgentMode.WAITING_FOR_INPUT, 100.0 + GRACE)[0] is False


def test_idle_agents_are_not_held():
    ctl = keep_awake.KeepAwakeController()
    assert helper.agents_awake(ctl, AgentMode.IDLE_READY, 0.0) == (False, "no agent is working")
    assert helper.agents_awake(ctl, None, 0.0) == (False, "no agent is working")


# --- The request: policy, then the battery safeguard (upstream's sync_keep_awake) ---

def request(policy, *, agents_active=False, battery=None, threshold=20.0):
    return helper.awake_request(policy, agents_active=agents_active,
                                agents_reason="agents are working", battery=battery, threshold=threshold)


def test_never_holds_even_while_agents_work():
    assert request("never", agents_active=True) == (False, "never")


def test_always_holds_without_any_agent():
    assert request("always") == (True, "always")


def test_agents_policy_holds_only_while_an_agent_is_active():
    assert request("agents", agents_active=True) == (True, "agents are working")
    assert request("agents", agents_active=False) == (False, "no agent is working")


def test_unplugged_battery_under_the_threshold_releases_the_lock():
    battery = BatterySnapshot(percent=19, is_plugged=False, battery_present=True)
    hold, reason = request("agents", agents_active=True, battery=battery)
    assert hold is False
    assert reason.startswith("battery safeguard")


def test_the_threshold_itself_does_not_release_the_lock():
    battery = BatterySnapshot(percent=20, is_plugged=False, battery_present=True)
    assert request("agents", agents_active=True, battery=battery)[0] is True


def test_a_plugged_battery_below_the_threshold_keeps_the_lock():
    battery = BatterySnapshot(percent=5, is_plugged=True, battery_present=True)
    assert request("agents", agents_active=True, battery=battery)[0] is True


def test_the_safeguard_also_stops_always():
    battery = BatterySnapshot(percent=10, is_plugged=False, battery_present=True)
    assert request("always", battery=battery)[0] is False


@pytest.mark.parametrize("battery", [None, BatterySnapshot(battery_present=False, percent=0)],
                         ids=["unknown", "no-battery"])
def test_no_readable_battery_means_no_safeguard(battery):
    assert request("agents", agents_active=True, battery=battery)[0] is True


# --- Battery reader: upstream's BatterySnapshot from a fake /sys tree ---

def test_battery_and_adapter_are_read_from_sysfs(tmp_path):
    root = battery_tree(tmp_path, percent=15, plugged=False)
    battery = helper.read_battery(root)
    assert (battery.percent, battery.battery_present, battery.is_plugged, battery.is_charging) == (15, True, False, False)


def test_an_online_adapter_or_a_charging_battery_means_plugged(tmp_path):
    root = battery_tree(tmp_path, percent=40, plugged=True)
    battery = helper.read_battery(root)
    assert battery.is_plugged is True and battery.is_charging is True


def test_a_desktop_without_a_battery_reads_as_no_battery(tmp_path):
    make_supply(tmp_path, "AC", type="Mains", online="0")
    battery = helper.read_battery(tmp_path)
    assert battery.battery_present is False and battery.is_plugged is False


def test_a_missing_power_supply_directory_reads_as_no_battery(tmp_path):
    assert helper.read_battery(tmp_path / "absent").battery_present is False


def test_an_unreadable_capacity_is_an_error_not_a_guess(tmp_path):
    make_supply(tmp_path, "BAT0", type="Battery", present="1", capacity="lots", status="Discharging")
    with pytest.raises(ValueError):
        helper.read_battery(tmp_path)


# --- The sleep lock (systemd-inhibit, faked on PATH) ---

def test_the_lock_command_blocks_sleep_and_the_lid_switch_only():
    argv = helper.inhibit_argv()
    assert argv[:6] == [
        "systemd-inhibit", "--what=sleep:handle-lid-switch", "--mode=block",
        "--who=SidePulse", f"--why={helper.INHIBIT_WHY}", "cat",
    ]
    # Screen lock and idle are never blocked (D11).
    assert "idle" not in " ".join(argv) and "handle-power-key" not in " ".join(argv)


def test_the_lock_is_taken_while_wanted_and_released_at_once(fake_systemd_inhibit):
    inhibitor = helper.SleepInhibitor()
    assert inhibitor.update(True) is None
    assert inhibitor.holding()
    ((pid, argv),) = taken(fake_systemd_inhibit)
    assert "--what=sleep:handle-lid-switch" in argv and "--mode=block" in argv
    assert inhibitor.update(True) is None  # still wanted: no second lock
    assert len(taken(fake_systemd_inhibit)) == 1
    assert inhibitor.update(False) is None
    assert not inhibitor.holding()
    assert not alive(pid)


def test_the_lock_outlives_nothing_when_its_helper_is_killed(fake_systemd_inhibit, tmp_path, monkeypatch):
    monkeypatch.setenv("HELPER_DIR", str(HELPER_DIR))
    code = (
        "import os, sys; sys.path.insert(0, os.environ['HELPER_DIR'])\n"
        "import sidepulse_omarchy as h\n"
        "h.SleepInhibitor().update(True)\n"
        "print('held', flush=True)\n"
        "sys.stdin.read()\n"
    )
    child = subprocess.Popen([sys.executable, "-I", "-c", code], stdin=subprocess.PIPE, stdout=subprocess.PIPE)
    try:
        assert child.stdout.readline().strip() == b"held"
        (pid, _), = taken(fake_systemd_inhibit)
        assert alive(pid)
        child.kill()
        child.wait()
    finally:
        if child.poll() is None:
            child.kill()
            child.wait()
    wait_until(lambda: not alive(pid), what="the lock's child to end with its helper")


def test_a_lock_that_cannot_start_is_reported_not_retried_every_tick(monkeypatch):
    monkeypatch.setattr(helper, "inhibit_argv", lambda: ["/nonexistent/systemd-inhibit"])
    inhibitor = helper.SleepInhibitor()
    message = inhibitor.update(True)
    assert message is not None and "cannot run systemd-inhibit" in message
    assert inhibitor.update(True) == message
    assert not inhibitor.holding()


def test_a_lock_that_dies_while_wanted_is_reported_with_its_reason(monkeypatch):
    monkeypatch.setattr(helper, "inhibit_argv",
                        lambda: [sys.executable, "-c", "import sys; sys.stderr.write('no logind\\n'); sys.exit(3)"])
    inhibitor = helper.SleepInhibitor()
    inhibitor.update(True)
    wait_until(lambda: inhibitor.update(True) is not None, what="the child to exit")
    message = inhibitor.update(True)
    assert "exited 3" in message and "no logind" in message
    assert not inhibitor.holding()


def test_a_missing_systemd_inhibit_is_a_capability_problem(monkeypatch):
    monkeypatch.setattr(helper, "inhibit_argv", lambda: ["no-such-systemd-inhibit-here"])
    caps = helper.probe_capabilities()
    assert caps["keep_awake"]["ok"] is False
    assert "systemd-inhibit" in caps["keep_awake"]["reason"]


def test_the_helper_probes_keep_awake_ok_with_the_fake_on_path():
    assert helper.probe_capabilities()["keep_awake"] == {"ok": True, "reason": ""}


# --- The helper: events, grace over time, battery and policy changes ---

@pytest.fixture
def clocked(tmp_path, monkeypatch):
    """A started helper with its own settings, no battery and a clock the test moves."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    out = io.StringIO()
    h = helper.Helper(out)
    h.hello()
    h.power_root = tmp_path / "no-power"
    now = [1000.0]
    h.clock = lambda: now[0]
    h.set_now = lambda seconds: now.__setitem__(0, seconds)
    return h, out


def awake_events(out: io.StringIO) -> list[dict]:
    return [e for e in map(json.loads, out.getvalue().splitlines()) if e["type"] == "awake"]


def errors_for(out: io.StringIO, feature: str) -> list[str]:
    return [e["message"] for e in map(json.loads, out.getvalue().splitlines())
            if e["type"] == "error" and e["feature"] == feature]


def send(h, cmd):
    h.handle_line(json.dumps(cmd).encode())


def test_working_holds_then_the_grace_releases_the_lock_after_300_seconds(clocked, fake_systemd_inhibit):
    h, out = clocked
    h.mode = AgentMode.WORKING
    h.refresh_awake()
    assert awake_events(out)[-1]["holding"] is True
    assert awake_events(out)[-1]["reason"] == "agents are working"

    h.set_now(1100.0)
    h.mode = AgentMode.COMPLETED
    h.refresh_awake()
    assert awake_events(out)[-1]["holding"] is True
    assert "grace" in awake_events(out)[-1]["reason"]

    h.set_now(1100.0 + GRACE - 0.1)
    h.refresh_awake()
    assert awake_events(out)[-1]["holding"] is True

    h.set_now(1100.0 + GRACE)
    h.refresh_awake()
    last = awake_events(out)[-1]
    assert last["holding"] is False and last["reason"] == "no agent is working"
    ((pid, _),) = taken(fake_systemd_inhibit)
    assert not alive(pid)


def test_the_lock_is_taken_once_for_a_whole_turn(clocked, fake_systemd_inhibit):
    h, _ = clocked
    h.mode = AgentMode.WORKING
    for second in range(5):
        h.set_now(1000.0 + second)
        h.refresh_awake()
    assert len(taken(fake_systemd_inhibit)) == 1


def test_never_releases_the_lock_at_once(clocked, fake_systemd_inhibit):
    h, out = clocked
    h.mode = AgentMode.WORKING
    h.refresh_awake()
    send(h, {"cmd": "set-policy", "policy": "never"})
    last = awake_events(out)[-1]
    assert last == {"type": "awake", "policy": "never", "holding": False, "reason": "never"}
    ((pid, _),) = taken(fake_systemd_inhibit)
    assert not alive(pid)


def test_always_holds_with_no_agent_and_the_policy_is_reported(clocked):
    h, out = clocked
    send(h, {"cmd": "set-policy", "policy": "always"})
    last = awake_events(out)[-1]
    assert last["policy"] == "always" and last["holding"] is True


def test_set_policy_refuses_an_unknown_policy_and_keeps_the_saved_one(clocked):
    h, out = clocked
    h.refresh_awake()
    send(h, {"cmd": "set-policy", "policy": "sometimes"})
    assert errors_for(out, "command") and "never, agents or always" in errors_for(out, "command")[-1]
    assert awake_events(out)[-1]["policy"] == "agents"


def test_a_battery_under_20_percent_unplugged_releases_the_lock_until_plugged(clocked, tmp_path, fake_systemd_inhibit):
    h, out = clocked
    h.power_root = battery_tree(tmp_path / "power", percent=15, plugged=False)
    send(h, {"cmd": "set-policy", "policy": "always"})
    last = awake_events(out)[-1]
    assert last["holding"] is False and "battery safeguard" in last["reason"]

    battery_tree(tmp_path / "power", percent=15, plugged=True)
    h.refresh_awake()
    assert awake_events(out)[-1]["holding"] is True


def test_an_unreadable_battery_is_reported_and_does_not_block_the_policy(clocked, tmp_path):
    h, out = clocked
    make_supply(tmp_path / "power", "BAT0", type="Battery", present="1", capacity="lots", status="Discharging")
    h.power_root = tmp_path / "power"
    send(h, {"cmd": "set-policy", "policy": "always"})
    assert any("cannot read the battery" in m for m in errors_for(out, "power"))
    assert awake_events(out)[-1]["holding"] is True


def test_a_lock_that_cannot_start_is_surfaced_and_reported_as_not_holding(clocked, monkeypatch):
    h, out = clocked
    monkeypatch.setattr(helper, "inhibit_argv", lambda: ["/nonexistent/systemd-inhibit"])
    h.mode = AgentMode.WORKING
    h.refresh_awake()
    last = awake_events(out)[-1]
    assert last["holding"] is False and last["reason"] == "agents are working"
    assert any("cannot run systemd-inhibit" in m for m in errors_for(out, "keep_awake"))


def test_the_helper_releases_the_lock_when_it_exits(clocked, fake_systemd_inhibit):
    h, _ = clocked
    h.mode = AgentMode.WORKING
    h.refresh_awake()
    ((pid, _),) = taken(fake_systemd_inhibit)
    h.inhibitor.release()
    assert not alive(pid)


# --- Through the real loop, as the widget runs it ---

def test_the_widget_sees_the_lock_held_and_released_with_the_helper(tmp_path, monkeypatch, fake_systemd_inhibit):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    proc = ProtocolHelper(tmp_path)
    proc.read_event()  # hello
    proc.write_latest([status_row("working")])
    proc.send({"cmd": "set-policy", "policy": "agents"})
    awake = proc.event_matching(lambda e: e["type"] == "awake" and e["holding"] is True)
    assert awake["reason"] == "agents are working"
    ((pid, _),) = taken(fake_systemd_inhibit)
    assert proc.close_stdin() == 0
    wait_until(lambda: not alive(pid), what="the lock to end with the helper")
