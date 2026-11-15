"""Test-wide fixtures.

Every test runs with a fake `systemd-inhibit` first on PATH. It logs its pid and
argv, drops the options and execs the command, as the real one runs its
command, so no test can take a real sleep lock (D11) and a test can check that
the lock's child ended.

Every test also gets a fake `systemctl` (see fake_systemctl) and starts with HOME and the XDG config and state dirs in a scratch
folder: the helper changes files there when it starts (#58), and a test that
does not set its own must never reach the user's real ones.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

SHIM = """#!/bin/sh
# Fake systemd-inhibit: log the pid and argv, then exec the command after the options.
echo "$$ $*" >> "$FAKE_INHIBIT_LOG"
while [ $# -gt 0 ]; do
  case "$1" in --*) shift ;; *) break ;; esac
done
exec "$@"
"""


@pytest.fixture(autouse=True)
def fake_systemd_inhibit(tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Returns the log the fake writes: one `pid argv` line per lock taken."""
    # Its own directory, so tests that read a fake /sys tree under tmp_path never see it.
    work = tmp_path_factory.mktemp("inhibit")
    bin_dir = work / "fake-bin"
    bin_dir.mkdir()
    shim = bin_dir / "systemd-inhibit"
    shim.write_text(SHIM)
    shim.chmod(0o755)
    log = work / "inhibit.log"
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("FAKE_INHIBIT_LOG", str(log))
    return log


SYSTEMCTL = """#!/bin/sh
# Fake systemctl: log the argv; the user service is never running here.
echo "$*" >> "$FAKE_SYSTEMCTL_LOG"
case " $* " in *" is-active "*) exit 3 ;; esac  # silent: upstream passes --quiet, and stdout is the helper's
exit 0
"""


@pytest.fixture(autouse=True)
def fake_systemctl(tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch) -> Path:
    """`systemctl --user` reaches the session's real user manager whatever HOME says: upstream's
    `sidepulse setup` restarts sidepulse.service, which once restarted the real service until systemd's
    start limit stopped it. Every test gets this fake first on PATH; it returns the log of calls."""
    work = tmp_path_factory.mktemp("systemctl")
    bin_dir = work / "fake-bin"
    bin_dir.mkdir()
    shim = bin_dir / "systemctl"
    shim.write_text(SYSTEMCTL)
    shim.chmod(0o755)
    log = work / "systemctl.log"
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("FAKE_SYSTEMCTL_LOG", str(log))
    return log


@pytest.fixture(autouse=True)
def scratch_home(tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A private HOME with XDG config and state dirs under it; tests may set their own over it."""
    home = tmp_path_factory.mktemp("home")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))
    monkeypatch.setenv("XDG_STATE_HOME", str(home / ".local" / "state"))
    return home


@pytest.fixture
def daemon(scratch_home: Path):
    """Upstream's status server in the scratch state folder, as SidePulse's service runs it (tests/daemon.py)."""
    from daemon import Daemon

    server = Daemon(Path(os.environ["XDG_STATE_HOME"]) / "sidepulse" / "agent-monitor")
    yield server
    server.stop()


@pytest.fixture
def service():
    """Starts upstream's status server in the state folder XDG_STATE_HOME names when called (a fixture that
    sets its own XDG_STATE_HOME calls it after); stopped at teardown."""
    from daemon import Daemon

    started = []

    def start() -> Daemon:
        server = Daemon(Path(os.environ["XDG_STATE_HOME"]) / "sidepulse" / "agent-monitor")
        started.append(server)
        return server

    yield start
    for server in started:
        server.stop()
