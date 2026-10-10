"""Spike (D8): resolve an agent session to its live process and Hyprland window.

Usage: python3 -I session_lookup.py <provider> <session_id> [cwd]

Every lookup ends in exactly one state:
  stopped     no process can own the session: resume is safe.
  live        a process provably owns the session: focus its window, or say
              where it runs when it has none (herdr, tmux).
  unresolved  a process might own the session but nothing proves it: show
              the candidates, never resume (that could duplicate a live
              session) and never focus (that could be the wrong session).

Proof that a process owns a session, strongest first:
  claude  ~/.claude/sessions/<pid>.json maps pid -> sessionId; the file's
          procStart must equal /proc/<pid>/stat starttime (pid-reuse guard).
  grok    ~/.grok/active_sessions.json lists {session_id, pid, cwd}.
  any     the session id is a whole argument of the process's command line
          (`codex resume <id>`, `junie --session-id=<id>`, ...).
Only processes holding a terminal count, which drops daemons such as
`codex app-server`. Without proof, such a process of the provider's binary
whose cwd is the session cwd makes the session unresolved; none makes it
stopped.

Window: walk the process's ancestors to a pid that owns Hyprland clients.
Several clients on one pid (single-instance terminals such as Ghostty) are
narrowed by title; Claude titles its window with the transcript's ai-title.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

PROVIDER_BINARIES = {
    "claude": "claude",
    "codex": "codex",
    "cursor": "cursor-agent",
    "grok": "grok",
    "junie": "junie",
}


class Proc:
    """Reads process facts from a /proc tree, so tests can supply a fake one."""

    def __init__(self, root: Path = Path("/proc")) -> None:
        self.root = root

    def _stat_fields(self, pid: int) -> list[str] | None:
        try:
            stat = (self.root / str(pid) / "stat").read_text()
        except OSError:
            return None
        # The comm field may contain spaces and parens; fields resume after the last ')'.
        return stat.rsplit(")", 1)[1].split()

    def starttime(self, pid: int) -> str | None:
        fields = self._stat_fields(pid)
        return fields[19] if fields else None

    def ppid(self, pid: int) -> int | None:
        fields = self._stat_fields(pid)
        return int(fields[1]) if fields else None

    def has_terminal(self, pid: int) -> bool:
        fields = self._stat_fields(pid)
        return bool(fields) and fields[4] != "0"

    def pids_of(self, binary: str) -> list[int]:
        found = []
        for entry in self.root.iterdir():
            if not entry.name.isdigit():
                continue
            try:
                if (entry / "comm").read_text().strip() == binary[:15]:
                    found.append(int(entry.name))
            except OSError:
                continue
        return sorted(found)

    def cwd(self, pid: int) -> str | None:
        try:
            return os.readlink(self.root / str(pid) / "cwd")
        except OSError:
            return None

    def cmdline(self, pid: int) -> list[str]:
        try:
            raw = (self.root / str(pid) / "cmdline").read_bytes()
        except OSError:
            return []
        return [part.decode(errors="replace") for part in raw.split(b"\0") if part]


def claude_pid(session_id: str, proc: Proc, home: Path) -> int | None:
    root = Path(os.environ.get("CLAUDE_CONFIG_DIR", home / ".claude")) / "sessions"
    for path in root.glob("*.json"):
        try:
            data = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        if data.get("sessionId") != session_id:
            continue
        pid = int(data["pid"])
        if proc.starttime(pid) == str(data.get("procStart")):
            return pid
    return None


def grok_pid(session_id: str, proc: Proc, home: Path) -> int | None:
    try:
        entries = json.loads((home / ".grok" / "active_sessions.json").read_text())
    except (OSError, ValueError):
        return None
    for entry in entries:
        if entry.get("session_id") == session_id and proc.starttime(int(entry["pid"])):
            return int(entry["pid"])
    return None


EXACT_SOURCES = {"claude": claude_pid, "grok": grok_pid}


def names_session(args: list[str], session_id: str) -> bool:
    """True when an argument is the session id itself or `--flag=<id>`."""
    return any(arg == session_id or arg.endswith("=" + session_id) for arg in args)


def find_process(provider: str, session_id: str, cwd: str | None, proc: Proc, home: Path) -> dict:
    """Return {"state": stopped|live|unresolved, ...} for the session's process."""
    exact = EXACT_SOURCES.get(provider)
    if exact is not None:
        pid = exact(session_id, proc, home)
        if pid is not None:
            return {"state": "live", "pid": pid, "proof": "session-file"}

    binary = PROVIDER_BINARIES.get(provider)
    if binary is None:
        return {"state": "unresolved", "reason": f"unknown provider {provider!r}", "candidates": []}
    # Interactive agents hold a terminal; daemons such as `codex app-server` do not.
    pids = [pid for pid in proc.pids_of(binary) if proc.has_terminal(pid)]
    by_cmdline = [pid for pid in pids if names_session(proc.cmdline(pid), session_id)]
    if len(by_cmdline) == 1:
        return {"state": "live", "pid": by_cmdline[0], "proof": "cmdline"}
    if len(by_cmdline) > 1:
        return {"state": "unresolved", "reason": "several processes name this session", "candidates": by_cmdline}
    if cwd is None:
        return {"state": "unresolved", "reason": "session has no cwd to check", "candidates": pids}
    in_cwd = [pid for pid in pids if proc.cwd(pid) == cwd]
    if in_cwd:
        return {"state": "unresolved", "reason": f"{binary} runs in {cwd} but nothing ties it to this session",
                "candidates": in_cwd}
    return {"state": "stopped"}


def ai_title(session_id: str, cwd: str, home: Path) -> str | None:
    root = Path(os.environ.get("CLAUDE_CONFIG_DIR", home / ".claude")) / "projects"
    transcript = root / cwd.replace("/", "-") / f"{session_id}.jsonl"
    title = None
    try:
        with transcript.open() as handle:
            for line in handle:
                if '"ai-title"' in line:
                    title = json.loads(line).get("aiTitle") or title
    except OSError:
        return None
    return title


def hypr_clients() -> list[dict]:
    out = subprocess.run(["hyprctl", "clients", "-j"], capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


def find_window(provider: str, session_id: str, cwd: str | None, pid: int, proc: Proc, home: Path,
                clients: list[dict]) -> dict:
    by_pid: dict[int, list[dict]] = {}
    for client in clients:
        by_pid.setdefault(client["pid"], []).append(client)
    chain, current = [], pid
    while current and current > 1:
        chain.append(current)
        if current in by_pid:
            candidates = by_pid[current]
            if len(candidates) > 1 and provider == "claude" and cwd:
                title = ai_title(session_id, cwd, home)
                if title:
                    candidates = [c for c in candidates if title in c["title"]]
            if len(candidates) == 1:
                return {"action": "focus", "address": candidates[0]["address"], "title": candidates[0]["title"]}
            return {"action": "ambiguous-window", "candidates": len(candidates)}
        current = proc.ppid(current)
    return {"action": "no-window", "ancestry": chain}


def resolve(provider: str, session_id: str, cwd: str | None, proc: Proc | None = None,
            home: Path | None = None, clients: list[dict] | None = None) -> dict:
    proc = proc or Proc()
    home = home or Path.home()
    found = find_process(provider, session_id, cwd, proc, home)
    if found["state"] == "stopped":
        return {**found, "action": "resume"}
    if found["state"] == "unresolved":
        return {**found, "action": "explain"}
    window = find_window(provider, session_id, cwd, found["pid"], proc, home,
                         hypr_clients() if clients is None else clients)
    return {**found, **window}


if __name__ == "__main__":
    provider, session_id = sys.argv[1], sys.argv[2]
    print(json.dumps(resolve(provider, session_id, sys.argv[3] if len(sys.argv) > 3 else None)))
