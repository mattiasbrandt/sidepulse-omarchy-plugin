# SidePulse for Omarchy

[![CI](https://github.com/mattiasbrandt/sidepulse-omarchy-plugin/actions/workflows/ci.yml/badge.svg)](https://github.com/mattiasbrandt/sidepulse-omarchy-plugin/actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/mattiasbrandt/sidepulse-omarchy-plugin)](https://github.com/mattiasbrandt/sidepulse-omarchy-plugin/releases/latest)
[![License: MIT](https://img.shields.io/github/license/mattiasbrandt/sidepulse-omarchy-plugin)](LICENSE)

An Omarchy bar widget for SidePulse devices and AI coding agents: it shows
agent status in the bar and drives SidePulse devices, the way the macOS
SidePulse menu-bar app does.

This is an early build. The bar icon follows your agents' state and the popup is
an empty frame; the features land slice by slice, tracked in the
[plan](docs/spec.md) and its issues.

## Install

```sh
omarchy plugin add https://github.com/mattiasbrandt/sidepulse-omarchy-plugin
```

It asks whether to enable the widget and which bar section to put it in. Then
install SidePulse with its official installer:

```sh
curl -fsSL https://sidepulse.io/setup.sh | bash
```

After installing or updating the plugin (`omarchy plugin update`), restart the
shell so it loads the new version's helper; it keeps the old one otherwise:

```sh
omarchy restart shell
```

The popup's Setup section lists anything still missing: SidePulse itself, a
stopped service (Start service runs `sidepulse service start`), or a device.

## Usage

Click the SidePulse icon in the bar to open the popup. `j` and `k` (or the arrow
keys) move the highlight, Enter runs the highlighted action, `r` refreshes, and
Esc or a click outside closes it. Settings opens on Enter or a click: agent
hooks, Bar LEDs, the timeouts, the Ask notice, the Keep awake policy and the
hook log. A keybind for the popup lands in #12.

Sessions lists up to ten recent agent sessions, newest and most urgent first.
Enter or a click on one jumps to it: a session that is running focuses its
window, or its herdr or tmux pane (attaching a terminal to it when none is
open); a session that has stopped resumes in a new terminal, in its folder. When
SidePulse cannot tell which process runs a session, the popup says so and does
nothing.

Bar LEDs (in Settings) draw the agent State's LEDs along the inner edge of a
top or bottom bar, on every monitor: 2 LEDs for a SidePulse Dot, 8 for a
SidePulse Pro.

Devices lists each connected SidePulse Dot or SidePulse Pro with its Display
(Agent or Off) and brightness; each device's LEDs show what its program plays. Off is saved in SidePulse's own settings, so it
outlasts the plugin: set a device back to Agent before removing the plugin, or
afterwards set its `led_display` back to `agent` in
`~/.config/sidepulse/agent-monitor/settings.json`.

## Requirements

- Omarchy v4.0.4, the release the plugin is tested against.
- SidePulse at upstream commit `6d55225fed20a82521b23d86a4df2c4a15dff295`, the
  commit the plugin is tested against. The helper runs under that install's
  Python.
- `notify-send` (libnotify) for the Ask notices (D20), optional: without it
  the notices are off and the popup says why.
- `xdg-terminal-exec` (your default terminal) to resume a stopped session, and
  `hyprctl`, which Omarchy ships, to raise a session's window. Optional: `herdr`
  or `tmux`, when your agents run inside one.
- `systemd-inhibit` (systemd, which Omarchy ships) for keep-awake (D11). Without
  it the Keep awake policy cannot hold sleep and the popup says why.
- `wasmtime` in the SidePulse venv, for the Bar LEDs and the device rows' LED
  frames (D6). Pinned to the version the tests run against:

  ```sh
  ~/.local/share/sidepulse/venv/bin/python -m pip install wasmtime==49.0.0
  ```

  SidePulse's own README names no such step, so a `sidepulse update` may not
  keep it. Without it the popup's PROBLEMS says the Bar LEDs are unavailable, and
  the device rows show the program's colour without animation.

## Remove

```sh
omarchy plugin remove io.github.mattiasbrandt.sidepulse
```

What removal leaves behind in SidePulse and the agents' configs (for example
installed hooks) is documented with those features, in #6 and #12.

## Security

Plugins run unsandboxed inside `omarchy-shell`. The plugin:

- starts one helper process, `python -I helper/sidepulse_omarchy.py`, with the
  Python of SidePulse's venv (`~/.local/share/sidepulse/venv`), and stops it
  when no widget copy is left;
- reads `~/.local/state/sidepulse/agent-monitor/latest.json` (SidePulse's state
  file), SidePulse's `settings.json` for the idle timeout, and looks for SidePulse
  devices on mounted volumes, without writing to any of them;
- checks `sidepulse.service` with `systemctl --user is-active`, and runs
  `sidepulse service start` only when you choose Start service;
- finds a session's process from `~/.claude/sessions/`, `~/.grok/active_sessions.json`,
  the transcript titles under `~/.claude/projects/`, and the process table
  (`/proc`), and reads only those;
- runs `hyprctl` to list and raise windows, and `herdr` or `tmux` to find and
  select a session's pane, and starts `xdg-terminal-exec` to open a terminal
  that resumes a session or attaches to a multiplexer, only when you choose a
  session in the popup;
- runs `sidepulse agent-monitor install|uninstall <agent>` in the SidePulse
  venv when you switch an agent's hooks in the Settings section. That edits the
  agent's own config (`~/.claude/settings.json`, `~/.codex/config.toml`,
  `~/.grok/hooks/`, `~/.cursor/hooks.json`, `~/.junie/config.json`) through
  SidePulse's CLI, which also writes backups beside them;
- reads SidePulse's hook log (`event-status.jsonl`) for the Settings section;
- writes SidePulse's `settings.json` (idle timeout, session retention, the Bar
  LEDs switch) through SidePulse's own settings code;
- runs `notify-send` (libnotify) for each notice when a session asks, only
  while "Notify when a session asks" is on, and opens the session when you click
  the notice;
- holds a sleep lock while the Keep awake policy says so: it runs
  `systemd-inhibit --what=sleep:handle-lid-switch --mode=block` (a `cat` child
  that ends with the helper), so the machine does not suspend or sleep on lid
  close. Screen lock and idle are not blocked, and no idle setting changes. It
  reads the battery from `/sys/class/power_supply` to release the lock when it
  is unplugged and below SidePulse's threshold (20 % by default), and saves the
  policy in SidePulse's `settings.json` (`sleep_prevention_policy`).

It makes no network calls and needs no sudo. The list grows with each feature
and is complete at #12.

## License

MIT. See [LICENSE](LICENSE).
