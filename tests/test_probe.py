"""Capability probe (D14): each feature is independently on or off, with a reason."""

from __future__ import annotations

import sys
import types

import sidepulse_omarchy as helper


def test_everything_present_in_the_test_environment_is_reported_ok():
    caps = helper.probe_capabilities()
    assert set(caps) == {"state", "sessions", "service", "devices", "bar_leds"}
    for name, cap in caps.items():
        assert cap["ok"] is True, f"{name}: {cap['reason']}"
        assert cap["reason"] == ""


def test_missing_wasmtime_disables_only_the_bar_leds(monkeypatch):
    monkeypatch.setitem(sys.modules, "wasmtime", None)
    caps = helper.probe_capabilities()
    assert caps["bar_leds"]["ok"] is False
    assert "wasmtime" in caps["bar_leds"]["reason"]
    assert caps["state"]["ok"] is True
    assert caps["devices"]["ok"] is True


def test_upstream_without_the_state_rules_disables_state_with_a_reason(monkeypatch):
    fake = types.ModuleType("sidepulse.collector")  # has none of upstream's names
    monkeypatch.setitem(sys.modules, "sidepulse.collector", fake)
    caps = helper.probe_capabilities()
    assert caps["state"]["ok"] is False
    assert caps["state"]["reason"].startswith("sidepulse.collector has no ")
    assert "upstream changed" in caps["state"]["reason"]
    assert caps["bar_leds"]["ok"] is True


def test_missing_service_module_disables_only_service(monkeypatch):
    monkeypatch.setitem(sys.modules, "sidepulse.service_launch", None)
    caps = helper.probe_capabilities()
    assert caps["service"]["ok"] is False
    assert "sidepulse.service_launch" in caps["service"]["reason"]
    assert caps["state"]["ok"] is True


def test_missing_session_resume_disables_only_sessions(monkeypatch):
    monkeypatch.setitem(sys.modules, "sidepulse.session_actions", None)
    caps = helper.probe_capabilities()
    assert caps["sessions"]["ok"] is False
    assert "sidepulse.session_actions" in caps["sessions"]["reason"]
    assert caps["state"]["ok"] is True
    assert caps["bar_leds"]["ok"] is True
