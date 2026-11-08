"""A device's LED file is read and written only when it is a plain, short file (#55).

Upstream's discovery takes any auto-mounted volume named like a SidePulse, or holding an LED file, as a
device, so a stick someone else made can carry a symbolic link, a FIFO or a huge file where LEDS.LED
should be. The helper must neither write through the link nor hang or flood on the read.
"""

from __future__ import annotations

import io
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from sidepulse.device_writer import MAX_LED_BYTES

import sidepulse_omarchy as helper


@pytest.fixture
def world(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    mounts = tmp_path / "media"
    (mounts / "PulseDot").mkdir(parents=True)
    monkeypatch.setenv("SIDEPULSE_MOUNT_ROOTS", str(mounts))
    out = io.StringIO()
    h = helper.Helper(out)
    h.hello()
    h.power_root = tmp_path / "power"
    h.lid_reader = lambda: False
    led = mounts / "PulseDot" / "LEDS.LED"
    return SimpleNamespace(h=h, out=out, home=home, led=led, dot_id=str(mounts / "PulseDot"))


def events(out: io.StringIO, kind: str) -> list[dict]:
    return [e for e in map(json.loads, out.getvalue().splitlines()) if e["type"] == kind]


def device_errors(out: io.StringIO) -> list[str]:
    return [e["message"] for e in events(out, "error") if e["feature"] == "devices"]


def set_off(w) -> None:
    w.h.handle_line(json.dumps({"cmd": "set-displays", "displays": {w.dot_id: "off"}}).encode())


def listed_ids(w) -> list[str]:
    return [item["id"] for item in events(w.out, "devices")[-1]["items"]]


def test_a_symlinked_led_file_is_never_written_through(world):
    victim = world.home / ".bashrc"
    victim.write_text("export PATH=$HOME/bin:$PATH\n")
    world.led.symlink_to(victim)
    set_off(world)
    assert victim.read_text() == "export PATH=$HOME/bin:$PATH\n"
    assert world.dot_id not in listed_ids(world)
    assert any("symbolic link" in message for message in device_errors(world.out))


def test_a_fifo_led_file_does_not_block_the_helper(world):
    os.mkfifo(world.led)
    set_off(world)  # would block for ever on open() or read() of the FIFO
    assert world.dot_id not in listed_ids(world)
    assert any("not a regular file" in message for message in device_errors(world.out))


def test_an_oversized_led_file_is_left_alone(world):
    world.led.write_bytes(b"x" * (MAX_LED_BYTES * 4))
    set_off(world)
    assert world.led.read_bytes() == b"x" * (MAX_LED_BYTES * 4)
    assert any("more than an LED program" in message for message in device_errors(world.out))


def test_a_led_file_that_is_not_text_does_not_crash_the_helper(world):
    world.led.write_bytes(b"\xff\xfe\x00 brightness 128\n")
    world.h.refresh_devices(force=True)
    assert world.dot_id in listed_ids(world)


def test_a_plain_led_file_still_works(world):
    world.led.write_text("brightness 128\n0:#00e5ff 760ms pulse 0ms\nrepeat\n")
    set_off(world)
    assert world.dot_id in listed_ids(world)
    assert device_errors(world.out) == []


def test_shown_program_refuses_a_link_swapped_in_after_discovery(world, tmp_path):
    world.led.write_text("brightness 128\n")
    device = helper.discover_device_rows()[0]
    world.led.unlink()
    (tmp_path / "elsewhere").write_text("secret\n")
    world.led.symlink_to(tmp_path / "elsewhere")
    with pytest.raises(OSError):
        helper.shown_program(device)


def test_the_guard_reads_upstreams_program_limit():
    assert "MAX_LED_BYTES" in helper.REQUIRED_NAMES["devices"]["sidepulse.device_writer"]


def test_a_folder_others_can_write_is_left_alone(world):
    # Another account able to write there could swap LEDS.LED for a link between the check and the write.
    world.led.write_text("brightness 128\n")
    world.led.parent.chmod(0o777)
    try:
        set_off(world)
        assert world.led.read_text() == "brightness 128\n"
        assert world.dot_id not in listed_ids(world)
        assert any("another account" in message for message in device_errors(world.out))
    finally:
        world.led.parent.chmod(0o755)


def test_a_folder_owned_by_someone_else_is_left_alone(world, monkeypatch):
    real_getuid = os.getuid
    monkeypatch.setattr(helper.os, "getuid", lambda: real_getuid() + 1)
    assert "another account" in helper.led_file_problem(world.led)


def test_an_error_while_checking_names_the_device_and_keeps_the_helper(world, monkeypatch):
    # A permission or I/O error on one stick must not end the helper (Codex review of #56).
    (world.led.parent.parent / "SidePulsePro").mkdir()
    real_lstat = os.lstat

    def flaky(path, *args, **kwargs):
        if Path(path).name == "LEDS.LED" and Path(path).parent.name == "PulseDot":
            raise PermissionError(13, "Permission denied", str(path))
        return real_lstat(path, *args, **kwargs)

    monkeypatch.setattr(helper.os, "lstat", flaky)
    world.h.refresh_devices(force=True)
    assert listed_ids(world) == [str(world.led.parent.parent / "SidePulsePro")]
    assert any("cannot check LEDS.LED" in message for message in device_errors(world.out))


def test_an_absent_led_file_is_fine(world):
    assert helper.led_file_problem(Path(world.led)) is None
