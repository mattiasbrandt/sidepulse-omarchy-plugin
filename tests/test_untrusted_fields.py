"""Fields read from files other processes write stay in their lane (audit for #62).

latest.json and the session records are written from hook payloads, and /proc lists every account's
processes: a provider name never reaches a command line, a session id never becomes a path, and
another account's process is never one of the user's sessions.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

import sidepulse_omarchy as helper


def status(provider: str):
    return type("Status", (), {
        "provider": provider, "session_id": "a", "agent_id": f"{provider}:a", "display_name": "x",
        "mode": type("Mode", (), {"value": "waiting_for_input"})(), "cwd": "/tmp/p", "message": "?",
        "updated_at": datetime.now(timezone.utc),
    })()


def test_an_unknown_provider_name_never_reaches_the_notice():
    summary, _ = helper.notification_text(status("/home/user/acme-merger --secret"))
    assert summary == "An agent needs attention"


def test_a_known_provider_keeps_its_name():
    assert helper.notification_text(status("codex"))[0].startswith("Codex")


def test_a_session_id_that_is_a_path_reads_no_file(tmp_path):
    home = tmp_path / "home"
    secret = home / ".claude" / "secret.jsonl"
    secret.parent.mkdir(parents=True)
    secret.write_text('{"type": "ai-title", "aiTitle": "private"}\n')
    (home / ".claude" / "projects" / "-x").mkdir(parents=True)  # so ../../ resolves to the file
    with mock.patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": str(home / ".claude")}):
        assert helper.ai_title("../../secret", "/x", home) is None
        assert helper.ai_title("..", "/x", home) is None


def test_a_uuid_session_id_still_finds_its_title(tmp_path):
    home = tmp_path / "home"
    transcript = home / ".claude" / "projects" / "-work" / "019a0000-aaaa-7bbb-8ccc-000000000001.jsonl"
    transcript.parent.mkdir(parents=True)
    transcript.write_text('{"type": "ai-title", "aiTitle": "Mine"}\n')
    with mock.patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": str(home / ".claude")}):
        assert helper.ai_title("019a0000-aaaa-7bbb-8ccc-000000000001", "/work", home) == "Mine"


def make_process(root: Path, pid: int, comm: str) -> None:
    d = root / str(pid)
    d.mkdir(parents=True)
    (d / "comm").write_text(comm + "\n")


def test_another_accounts_process_is_not_listed(tmp_path, monkeypatch):
    make_process(tmp_path, 101, "claude")
    proc = helper.Proc(tmp_path)
    assert proc.pids_of("claude") == [101]
    real_getuid = os.getuid
    monkeypatch.setattr(helper.os, "getuid", lambda: real_getuid() + 1)
    assert proc.pids_of("claude") == []
