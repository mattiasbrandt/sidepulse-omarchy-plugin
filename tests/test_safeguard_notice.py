"""Desktop notices for the battery safeguard (D11, amended): one when it lets go of a wanted lock, one when Keep awake holds again.

The notice fires only when the safeguard's block changes (never on the reason text, which
changes with every percent), not at the first read, under policy Never, or when it blocks
nothing. A fake notify-send on a scratch PATH stands in for the desktop (test_notify.py).
"""

from __future__ import annotations

import sidepulse_omarchy as helper
from test_keep_awake import awake_events, battery_tree, clocked, errors_for, send  # noqa: F401  (fixture)
from test_notify import fake_notify_send, pump_until  # noqa: F401  (fixture)

PAUSED = "Keep awake paused by the battery safeguard: {} %, unplugged"
RESUMED = "Keep awake resumed: plugged in"


def summaries(h: helper.Helper, fake_notify_send) -> list[str]:
    """The text of each notice the fake notify-send was given (its argv minus the app name), once every
    notice has ended: the notice runs as a child, so its log line is there when the pump has read it."""
    pump_until(h, lambda: not h.notifications)
    log = fake_notify_send / "notify.log"
    if not log.exists():
        return []
    return [line.removeprefix("--app-name=SidePulse ") for line in log.read_text().splitlines() if line]


# --- The rule itself (pure) ---


def test_the_first_read_never_notifies_even_when_the_safeguard_already_blocks():
    assert helper.safeguard_notice(None, True, False, 15) is None
    assert helper.safeguard_notice(None, False, True, 15) is None


def test_a_new_block_says_paused_with_the_percent():
    assert helper.safeguard_notice(False, True, False, 19) == PAUSED.format("19")


def test_the_percent_keeps_a_decimal_as_upstream_formats_it():
    assert helper.safeguard_notice(False, True, False, 19.5) == PAUSED.format("19.5")


def test_holding_again_says_resumed_plugged_in():
    assert helper.safeguard_notice(True, False, True, 80) == RESUMED


def test_a_block_that_ends_because_agents_stopped_is_silent():
    assert helper.safeguard_notice(True, False, False, 80) is None


def test_no_change_is_silent_so_a_falling_percent_does_not_renotify():
    assert helper.safeguard_notice(True, True, False, 14) is None
    assert helper.safeguard_notice(False, False, True, 80) is None


def test_policy_never_is_never_blocked_by_the_safeguard():
    assert helper.safeguard_blocks("never", agents_active=True, safeguard_active=True) is False


def test_the_safeguard_blocks_only_a_lock_the_policy_wants():
    assert helper.safeguard_blocks("agents", agents_active=False, safeguard_active=True) is False
    assert helper.safeguard_blocks("agents", agents_active=True, safeguard_active=True) is True
    assert helper.safeguard_blocks("always", agents_active=False, safeguard_active=True) is True
    assert helper.safeguard_blocks("always", agents_active=False, safeguard_active=False) is False


# --- The helper, with a fake battery and a fake notify-send ---


def test_pause_then_resume_send_one_notice_each_and_nothing_between(clocked, tmp_path, fake_notify_send):
    h, _ = clocked
    h.power_root = battery_tree(tmp_path / "power", percent=50, plugged=False)
    send(h, {"cmd": "set-policy", "policy": "always"})
    assert summaries(h, fake_notify_send) == []

    h.power_root = battery_tree(tmp_path / "power", percent=15, plugged=False)
    h.refresh_awake()
    h.power_root = battery_tree(tmp_path / "power", percent=14, plugged=False)
    h.refresh_awake()
    h.power_root = battery_tree(tmp_path / "power", percent=15, plugged=True)
    h.refresh_awake()
    h.refresh_awake()

    assert summaries(h, fake_notify_send) == [PAUSED.format("15"), RESUMED]


def test_the_notices_are_plain_notices_without_an_action(clocked, tmp_path, fake_notify_send):
    h, _ = clocked
    h.power_root = battery_tree(tmp_path / "power", percent=50, plugged=False)
    send(h, {"cmd": "set-policy", "policy": "always"})
    h.power_root = battery_tree(tmp_path / "power", percent=15, plugged=False)
    h.refresh_awake()
    assert summaries(h, fake_notify_send) == [PAUSED.format("15")]
    argv = (fake_notify_send / "notify.log").read_text().split("\n")[0]
    assert "--action" not in argv and "--wait" not in argv


def test_a_helper_that_starts_already_blocked_stays_quiet_until_it_changes(clocked, tmp_path, fake_notify_send):
    h, out = clocked
    h.power_root = battery_tree(tmp_path / "power", percent=15, plugged=False)
    send(h, {"cmd": "set-policy", "policy": "always"})
    assert awake_events(out)[-1]["holding"] is False
    assert summaries(h, fake_notify_send) == []


def test_agents_policy_with_no_agent_working_is_silent_under_the_safeguard(clocked, tmp_path, fake_notify_send):
    h, _ = clocked
    h.power_root = battery_tree(tmp_path / "power", percent=50, plugged=False)
    send(h, {"cmd": "set-policy", "policy": "agents"})
    h.power_root = battery_tree(tmp_path / "power", percent=15, plugged=False)
    h.refresh_awake()
    assert summaries(h, fake_notify_send) == []


def test_policy_never_sends_nothing_even_when_the_battery_is_low(clocked, tmp_path, fake_notify_send):
    h, _ = clocked
    h.power_root = battery_tree(tmp_path / "power", percent=15, plugged=False)
    send(h, {"cmd": "set-policy", "policy": "never"})
    h.refresh_awake()
    assert summaries(h, fake_notify_send) == []


def test_the_notices_ignore_the_ask_notice_switch(clocked, tmp_path, fake_notify_send):
    h, _ = clocked
    assert h.notify_on_ask is False
    h.power_root = battery_tree(tmp_path / "power", percent=50, plugged=False)
    send(h, {"cmd": "set-policy", "policy": "always"})
    h.power_root = battery_tree(tmp_path / "power", percent=15, plugged=False)
    h.refresh_awake()
    assert summaries(h, fake_notify_send) == [PAUSED.format("15")]


def test_missing_notify_send_sends_nothing_and_the_problem_line_says_why(clocked, tmp_path, monkeypatch):
    h, out = clocked
    monkeypatch.setenv("PATH", str(tmp_path))
    h.hello()
    h.power_root = battery_tree(tmp_path / "power", percent=50, plugged=False)
    send(h, {"cmd": "set-policy", "policy": "always"})
    h.power_root = battery_tree(tmp_path / "power", percent=15, plugged=False)
    h.refresh_awake()
    assert h.notifications == []
    assert any("notify-send" in message for message in errors_for(out, "notify"))
