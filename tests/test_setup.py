"""Setup items (D19): what is missing, in order, with the action each one offers."""

from __future__ import annotations

import sidepulse_omarchy as helper


def ids(event: dict) -> list[str]:
    return [item["id"] for item in event["items"]]


def test_everything_missing_lists_install_service_device_in_order():
    event = helper.setup_event(installed=False, service_running=False, device_count=0)
    assert event["type"] == "setup"
    assert ids(event) == ["install", "service", "device"]


def test_nothing_missing_lists_no_items():
    event = helper.setup_event(installed=True, service_running=True, device_count=1)
    assert event == {"type": "setup", "items": []}


def test_stopped_service_offers_start_service_and_nothing_else():
    event = helper.setup_event(installed=True, service_running=False, device_count=1)
    assert ids(event) == ["service"]
    assert event["items"][0]["action"] == "start-service"
    assert event["items"][0]["action_label"] == "Start service"


def test_install_item_shows_the_official_installer_and_has_no_action():
    event = helper.setup_event(installed=False, service_running=None, device_count=None)
    assert ids(event) == ["install"]
    assert "https://sidepulse.io/setup.sh" in event["items"][0]["detail"]
    assert event["items"][0]["action"] is None


def test_checks_that_could_not_run_are_skipped_not_reported_as_missing():
    event = helper.setup_event(installed=True, service_running=None, device_count=None)
    assert event["items"] == []


def test_no_device_is_missing_and_says_how_to_fix_it():
    event = helper.setup_event(installed=True, service_running=True, device_count=0)
    assert ids(event) == ["device"]
    assert "SidePulse Dot or SidePulse Pro" in event["items"][0]["detail"]
