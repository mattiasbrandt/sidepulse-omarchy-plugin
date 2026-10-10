"""Spike (D4): aggregate from latest.json with upstream's own reader and rules,
compared against `sidepulse agent-monitor status --json`.

Usage: ~/.local/share/sidepulse/venv/bin/python -I spikes/state_reader.py
"""
import json, subprocess, sys, time
from datetime import datetime, timezone
from pathlib import Path
from sidepulse.collector import (agent_status_from_dict, snapshot_from_statuses,
    COMPLETED_VISIBLE_SECONDS, IDLE_VISIBLE_SECONDS, POST_TOOL_WORKING_VISIBLE_SECONDS)
from sidepulse.ipc import default_latest_state_path

def snapshot(stale_after=3600.0):
    data = json.loads(default_latest_state_path().read_text())
    statuses = tuple(s for s in (agent_status_from_dict(d) for d in data["statuses"]) if s)
    return snapshot_from_statuses(statuses, sources=(), collected_at=datetime.now(timezone.utc),
        stale_after_seconds=stale_after, tool_running_timeout_seconds=0.0,
        completed_visible_seconds=COMPLETED_VISIBLE_SECONDS, idle_visible_seconds=IDLE_VISIBLE_SECONDS,
        post_tool_working_visible_seconds=POST_TOOL_WORKING_VISIBLE_SECONDS)

t=time.perf_counter(); snap=snapshot(); dt=time.perf_counter()-t
cli=json.loads(subprocess.run(["sidepulse","agent-monitor","status","--json"],capture_output=True,text=True).stdout)
mine=snap.to_dict() if hasattr(snap,"to_dict") else None
a=snap.aggregate
print("path", default_latest_state_path())
print(f"latest.json snapshot: {dt*1000:.1f} ms  mode={a.mode.value} active={a.active_count} stale={a.stale_count}")
ca=cli["aggregate"]; print(f"status --json:        mode={ca['mode']} active={ca['active_count']} stale={ca['stale_count']}")
print("fresh ids latest:", sorted(s.agent_id for s in snap.statuses))
print("fresh ids cli:   ", sorted(s["agent_id"] for s in cli["statuses"]))
