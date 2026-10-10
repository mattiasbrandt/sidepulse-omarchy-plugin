"""Spike (D8): resolve an agent session to its live process and Hyprland window.

Usage: python3 -I session_lookup.py <provider> <session_id> [cwd]

Resolution, per provider:
  claude  ~/.claude/sessions/<pid>.json maps pid -> sessionId; the file's
          procStart must equal /proc/<pid>/stat starttime (pid-reuse guard).
  grok    ~/.grok/active_sessions.json lists {session_id, pid, cwd}.
  other   no pid source known: unique live process of that binary whose cwd
          equals the session cwd, else unresolved.
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

PROVIDER_BINARIES = {"codex": "codex", "cursor": "cursor-agent", "junie": "junie", "grok": "grok", "claude": "claude"}


def proc_starttime(pid: int) -> str | None:
    try:
        stat = Path(f"/proc/{pid}/stat").read_text()
    except OSError:
        return None
    # Field 22; the comm field (2) may contain spaces, so split after its ')'.
    return stat.rsplit(")", 1)[1].split()[19]


def proc_ppid(pid: int) -> int | None:
    try:
        stat = Path(f"/proc/{pid}/stat").read_text()
    except OSError:
        return None
    return int(stat.rsplit(")", 1)[1].split()[1])


def claude_pid(session_id: str) -> int | None:
    root = Path(os.environ.get("CLAUDE_CONFIG_DIR", Path.home() / ".claude")) / "sessions"
    for path in root.glob("*.json"):
        try:
            data = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        if data.get("sessionId") != session_id:
            continue
        pid = int(data["pid"])
        if proc_starttime(pid) == str(data.get("procStart")):
            return pid
    return None


def grok_pid(session_id: str) -> int | None:
    path = Path.home() / ".grok" / "active_sessions.json"
    try:
        entries = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    for entry in entries:
        if entry.get("session_id") == session_id and proc_starttime(int(entry["pid"])):
            return int(entry["pid"])
    return None


def cwd_unique_pid(binary: str, cwd: str) -> int | None:
    matches = []
    for proc in Path("/proc").iterdir():
        if not proc.name.isdigit():
            continue
        try:
            if (proc / "comm").read_text().strip() != binary[:15]:
                continue
            if os.readlink(proc / "cwd") == cwd:
                matches.append(int(proc.name))
        except OSError:
            continue
    return matches[0] if len(matches) == 1 else None


def ai_title(session_id: str, cwd: str) -> str | None:
    root = Path(os.environ.get("CLAUDE_CONFIG_DIR", Path.home() / ".claude")) / "projects"
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


def resolve(provider: str, session_id: str, cwd: str | None) -> dict:
    if provider == "claude":
        pid = claude_pid(session_id)
    elif provider == "grok":
        pid = grok_pid(session_id)
    else:
        binary = PROVIDER_BINARIES.get(provider)
        pid = cwd_unique_pid(binary, cwd) if binary and cwd else None
    if pid is None:
        return {"live": False, "action": "resume"}

    clients = hypr_clients()
    by_pid: dict[int, list[dict]] = {}
    for client in clients:
        by_pid.setdefault(client["pid"], []).append(client)
    chain, current = [], pid
    while current and current > 1:
        chain.append(current)
        if current in by_pid:
            candidates = by_pid[current]
            if len(candidates) > 1 and provider == "claude" and cwd:
                title = ai_title(session_id, cwd)
                if title:
                    candidates = [c for c in candidates if title in c["title"]]
            if len(candidates) == 1:
                return {"live": True, "pid": pid, "action": "focus", "address": candidates[0]["address"],
                        "title": candidates[0]["title"]}
            return {"live": True, "pid": pid, "action": "ambiguous", "candidates": len(candidates)}
        current = proc_ppid(current)
    return {"live": True, "pid": pid, "action": "no-window", "ancestry": chain}


if __name__ == "__main__":
    provider, session_id = sys.argv[1], sys.argv[2]
    print(json.dumps(resolve(provider, session_id, sys.argv[3] if len(sys.argv) > 3 else None)))
