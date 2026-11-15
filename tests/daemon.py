"""A SidePulse daemon for tests: upstream's own status server, holding the statuses a test gives it.

Since 20ca7fd the service keeps the agents' status in memory and answers `get_state` on its status
socket; the helper reads it there (DaemonMonitor). This runs upstream's ProgramServer, the same server
the service runs, with a store that holds what the test sets, so the reply is upstream's real
`get_state` reply (ProgramMonitor's rules, MonitorSnapshot.to_dict) and the helper's read is the
real one.
"""

from __future__ import annotations

from pathlib import Path


class Store:
    """The one method ProgramMonitor reads from the daemon's ProgramState."""

    def __init__(self) -> None:
        self.rows: tuple = ()

    def statuses(self):
        return self.rows


class Daemon:
    def __init__(self, state_dir: Path) -> None:
        from sidepulse.program_transport import ProgramServer, status_socket_path

        self.store = Store()
        self.path = status_socket_path(state_dir)
        self.server = ProgramServer(self.store, path=self.path)
        self.server.start()

    def set(self, rows: list[dict]) -> None:
        """Statuses in upstream's dict form (AgentStatus.to_dict), as hooks would have left them."""
        from sidepulse.collector import agent_status_from_dict

        self.store.rows = tuple(s for s in (agent_status_from_dict(row) for row in rows) if s is not None)

    def stop(self) -> None:
        self.server.stop()
