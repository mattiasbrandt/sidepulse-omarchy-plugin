"""Test-wide fixtures.

Every test runs with a fake `systemd-inhibit` first on PATH. It logs its pid and
argv, drops the options and execs the command, as the real one runs its
command, so no test can take a real sleep lock (D11) and a test can check that
the lock's child ended.
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
