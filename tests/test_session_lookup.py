"""Tests for the D8 lookup states against a fake /proc tree.

Run: uv run --locked pytest (see CONTRIBUTING.md)
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from sidepulse_omarchy import Proc, resolve

SESSION = "019a0000-aaaa-7bbb-8ccc-000000000001"
CWD = "/work/project"


class FakeRun:
    """Stands in for run_command: answers the commands a test lists and fails any other."""

    def __init__(self, answers: dict[str, tuple[int, str, str]]) -> None:
        self.answers = answers
        self.asked: list[str] = []

    def __call__(self, argv: list[str]) -> subprocess.CompletedProcess:
        line = " ".join(argv)
        self.asked.append(line)
        code, out, err = self.answers.get(line, (127, "", f"not expected in this test: {line}"))
        return subprocess.CompletedProcess(argv, code, out, err)


def ok(stdout: str) -> tuple[int, str, str]:
    return (0, stdout, "")


def fail(stderr: str) -> tuple[int, str, str]:
    return (1, "", stderr)


def herdr_pane(pane_id: str) -> dict:
    return {"pane_id": pane_id}


def herdr_process_info(*pids: int) -> str:
    return json.dumps({"id": "x", "result": {"process_info": {"foreground_processes": [
        {"pid": pid, "name": "x", "argv": [], "cmdline": "", "cwd": "/"} for pid in pids]}}})


class FakeSystem:
    def __init__(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        base = Path(self._tmp.name)
        self.proc_root = base / "proc"
        self.home = base / "home"
        self.proc_root.mkdir()
        self.home.mkdir()

    def close(self) -> None:
        self._tmp.cleanup()

    def process(self, pid: int, comm: str, *, cwd: str, args: list[str], ppid: int = 1,
                starttime: int = 1000, tty: int = 34816) -> None:
        d = self.proc_root / str(pid)
        d.mkdir()
        (d / "comm").write_text(comm + "\n")
        (d / "cmdline").write_bytes(b"\0".join(a.encode() for a in args) + b"\0")
        # Fields after "(comm)": state ppid pgrp session tty_nr ... starttime at index 19.
        rest = ["S", str(ppid), "0", "0", str(tty)] + ["0"] * 14 + [str(starttime)]
        (d / "stat").write_text(f"{pid} ({comm}) " + " ".join(rest) + "\n")
        os.symlink(cwd, d / "cwd")

    def claude_session_file(self, pid: int, session_id: str, proc_start: int) -> None:
        sessions = self.home / ".claude" / "sessions"
        sessions.mkdir(parents=True, exist_ok=True)
        (sessions / f"{pid}.json").write_text(json.dumps(
            {"pid": pid, "sessionId": session_id, "procStart": str(proc_start)}))

    def resolve(self, provider: str, cwd: str | None = CWD, clients: list[dict] | None = None,
                answers: dict[str, tuple[int, str, str]] | None = None) -> dict:
        return resolve(provider, SESSION, cwd, proc=Proc(self.proc_root), home=self.home,
                       clients=clients or [], run=FakeRun(answers or {}))


class LookupStates(unittest.TestCase):
    def setUp(self) -> None:
        os.environ.pop("CLAUDE_CONFIG_DIR", None)
        self.sys = FakeSystem()

    def tearDown(self) -> None:
        self.sys.close()

    def test_no_process_is_stopped_and_resumes(self) -> None:
        result = self.sys.resolve("codex")
        self.assertEqual((result["state"], result["action"]), ("stopped", "resume"))

    def test_two_processes_in_cwd_are_unresolved_not_resumed(self) -> None:
        self.sys.process(101, "codex", cwd=CWD, args=["codex"])
        self.sys.process(102, "codex", cwd=CWD, args=["codex"])
        result = self.sys.resolve("codex")
        self.assertEqual((result["state"], result["action"]), ("unresolved", "explain"))
        self.assertEqual(result["candidates"], [101, 102])

    def test_one_unrelated_process_in_cwd_is_unresolved_not_focused(self) -> None:
        self.sys.process(101, "codex", cwd=CWD, args=["codex"])
        result = self.sys.resolve("codex")
        self.assertEqual((result["state"], result["action"]), ("unresolved", "explain"))
        self.assertNotIn("address", result)

    def test_process_elsewhere_does_not_block_resume(self) -> None:
        self.sys.process(101, "codex", cwd="/other", args=["codex"])
        self.assertEqual(self.sys.resolve("codex")["state"], "stopped")

    def test_session_id_in_cmdline_is_proof(self) -> None:
        self.sys.process(101, "codex", cwd=CWD, args=["codex", "resume", SESSION])
        self.sys.process(102, "codex", cwd=CWD, args=["codex"])
        result = self.sys.resolve("codex")
        self.assertEqual((result["state"], result["pid"], result["proof"]), ("live", 101, "cmdline"))

    def test_session_id_in_two_cmdlines_is_unresolved(self) -> None:
        self.sys.process(101, "codex", cwd=CWD, args=["codex", "resume", SESSION])
        self.sys.process(102, "codex", cwd=CWD, args=["codex", "resume", SESSION])
        self.assertEqual(self.sys.resolve("codex")["state"], "unresolved")

    def test_daemon_without_terminal_does_not_block_resume(self) -> None:
        self.sys.process(101, "codex", cwd=CWD, args=["codex", "app-server"], tty=0)
        self.assertEqual(self.sys.resolve("codex")["state"], "stopped")

    def test_session_id_substring_is_not_proof(self) -> None:
        self.sys.process(101, "codex", cwd="/other", args=["codex", "resume", SESSION + "-other"])
        self.assertEqual(self.sys.resolve("codex")["state"], "stopped")

    def test_flag_equals_session_id_is_proof(self) -> None:
        self.sys.process(101, "junie", cwd=CWD, args=["junie", f"--session-id={SESSION}", "--resume"])
        self.assertEqual(self.sys.resolve("junie")["proof"], "cmdline")

    def test_claude_session_file_is_proof(self) -> None:
        self.sys.process(201, "claude", cwd=CWD, args=["claude"], starttime=5555)
        self.sys.claude_session_file(201, SESSION, 5555)
        result = self.sys.resolve("claude")
        self.assertEqual((result["state"], result["proof"]), ("live", "session-file"))

    def test_claude_reused_pid_is_not_proof(self) -> None:
        # Same pid, different start time: the file belongs to a dead process.
        self.sys.process(201, "claude", cwd=CWD, args=["claude"], starttime=7777)
        self.sys.claude_session_file(201, SESSION, 5555)
        self.assertEqual(self.sys.resolve("claude")["state"], "unresolved")

    def test_resume_argument_of_a_process_that_moved_on_is_not_proof(self) -> None:
        # `claude --resume OTHER` started, then the process took up SESSION (its session file says so).
        # The argument no longer tells us what runs there, so OTHER has stopped.
        other = "019a0000-aaaa-7bbb-8ccc-0000000000ff"
        self.sys.process(201, "claude", cwd=CWD, args=["claude", "--resume", other], starttime=5555)
        self.sys.claude_session_file(201, SESSION, 5555)
        result = resolve("claude", other, CWD, proc=Proc(self.sys.proc_root), home=self.sys.home, clients=[])
        self.assertEqual((result["state"], result["action"]), ("stopped", "resume"))

    def test_claude_without_session_file_or_process_resumes(self) -> None:
        self.assertEqual(self.sys.resolve("claude")["action"], "resume")

    def test_live_process_without_window_is_reported(self) -> None:
        self.sys.process(300, "herdr", cwd="/", args=["herdr"])
        self.sys.process(301, "claude", cwd=CWD, args=["claude"], ppid=300, starttime=42)
        self.sys.claude_session_file(301, SESSION, 42)
        result = self.sys.resolve("claude")
        self.assertEqual((result["state"], result["action"]), ("live", "explain"))
        self.assertEqual(result["ancestry"], [301, 300])
        self.assertEqual(result["where"], "herdr (pid 300)")

    def test_live_process_focuses_its_terminal_window(self) -> None:
        self.sys.process(400, "ghostty", cwd="/", args=["ghostty"])
        self.sys.process(401, "claude", cwd=CWD, args=["claude"], ppid=400, starttime=9)
        self.sys.claude_session_file(401, SESSION, 9)
        clients = [{"pid": 400, "address": "0xabc", "title": "anything"}]
        result = self.sys.resolve("claude", clients=clients)
        self.assertEqual((result["action"], result["address"]), ("focus", "0xabc"))

    def test_herdr_pane_holding_the_session_is_focused_by_pane(self) -> None:
        self.sys.process(300, "herdr", cwd="/", args=["/usr/bin/herdr", "server"], ppid=1)
        self.sys.process(301, "zsh", cwd=CWD, args=["zsh"], ppid=300)
        self.sys.process(302, "claude", cwd=CWD, args=["claude"], ppid=301, starttime=42)
        self.sys.claude_session_file(302, SESSION, 42)
        answers = {
            "herdr pane list": ok(json.dumps({"result": {"panes": [herdr_pane("w1:p1"), herdr_pane("w2:p1")]}})),
            "herdr pane process-info --pane w1:p1": ok(herdr_process_info(999)),
            "herdr pane process-info --pane w2:p1": ok(herdr_process_info(302)),
        }
        result = self.sys.resolve("claude", answers=answers)
        self.assertEqual((result["action"], result["pane"]), ("herdr", "w2:p1"))
        self.assertIsNone(result["client"])  # no herdr client runs: attach one

    def test_herdr_client_window_is_focused_with_the_pane(self) -> None:
        self.sys.process(300, "herdr", cwd="/", args=["/usr/bin/herdr", "server"], ppid=1)
        self.sys.process(302, "claude", cwd=CWD, args=["claude"], ppid=300, starttime=42)
        self.sys.claude_session_file(302, SESSION, 42)
        self.sys.process(400, "herdr", cwd="/", args=["herdr"], ppid=410)
        self.sys.process(410, "zsh", cwd="/", args=["zsh"], ppid=420)
        self.sys.process(420, "ghostty", cwd="/", args=["ghostty"], ppid=1)
        answers = {
            "herdr pane list": ok(json.dumps({"result": {"panes": [herdr_pane("w2:p1")]}})),
            "herdr pane process-info --pane w2:p1": ok(herdr_process_info(302)),
        }
        clients = [{"pid": 420, "address": "0xdef", "title": "herdr"}]
        result = self.sys.resolve("claude", clients=clients, answers=answers)
        self.assertEqual(result["action"], "herdr")
        self.assertEqual(result["client"], {"action": "focus", "address": "0xdef", "title": "herdr"})

    def test_herdr_failure_is_reported_not_hidden(self) -> None:
        self.sys.process(301, "claude", cwd=CWD, args=["claude"], starttime=42)
        self.sys.claude_session_file(301, SESSION, 42)
        answers = {"herdr pane list": fail("herdr server is not running")}
        result = self.sys.resolve("claude", answers=answers)
        self.assertEqual(result["action"], "explain")
        self.assertIn("herdr: herdr server is not running", result["notes"])

    def test_tmux_pane_holding_the_session_is_focused_by_pane(self) -> None:
        self.sys.process(300, "tmux: server", cwd="/", args=["tmux: server"], ppid=1)
        self.sys.process(301, "zsh", cwd=CWD, args=["zsh"], ppid=300)
        self.sys.process(302, "claude", cwd=CWD, args=["claude"], ppid=301, starttime=42)
        self.sys.claude_session_file(302, SESSION, 42)
        answers = {
            "herdr pane list": fail("herdr is not installed"),
            "tmux list-panes -a -F #{pane_pid} #{pane_id} #{session_name}":
                ok("999 %1 other\n301 %3 work\n"),
            "tmux list-clients -F #{client_pid}": ok(""),
        }
        result = self.sys.resolve("claude", answers=answers)
        self.assertEqual((result["action"], result["pane"], result["session"]), ("tmux", "%3", "work"))
        self.assertIsNone(result["client"])

    def test_tmux_without_server_is_not_an_error(self) -> None:
        self.sys.process(301, "claude", cwd=CWD, args=["claude"], starttime=42)
        self.sys.claude_session_file(301, SESSION, 42)
        answers = {
            "herdr pane list": fail("herdr server is not running"),
            "tmux list-panes -a -F #{pane_pid} #{pane_id} #{session_name}":
                fail("no server running on /tmp/tmux-1000/default"),
        }
        result = self.sys.resolve("claude", answers=answers)
        self.assertEqual(result["action"], "explain")
        self.assertEqual(result["notes"], ["herdr: herdr server is not running"])

    def test_two_herdr_panes_holding_the_session_are_ambiguous(self) -> None:
        self.sys.process(302, "claude", cwd=CWD, args=["claude"], starttime=42)
        self.sys.claude_session_file(302, SESSION, 42)
        answers = {
            "herdr pane list": ok(json.dumps({"result": {"panes": [herdr_pane("w1:p1"), herdr_pane("w2:p1")]}})),
            "herdr pane process-info --pane w1:p1": ok(herdr_process_info(302)),
            "herdr pane process-info --pane w2:p1": ok(herdr_process_info(302)),
        }
        result = self.sys.resolve("claude", answers=answers)
        self.assertEqual((result["action"], result["candidates"]), ("ambiguous-pane", ["w1:p1", "w2:p1"]))


if __name__ == "__main__":
    unittest.main()
