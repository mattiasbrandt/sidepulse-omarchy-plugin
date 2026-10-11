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

The popup's Setup section lists anything still missing: SidePulse itself, a
stopped service (Start service runs `sidepulse service start`), or a device.

## Usage

Click the SidePulse icon in the bar to open the popup. `j` and `k` (or the arrow
keys) move the highlight, Enter runs the highlighted action, `r` refreshes, and
Esc or a click outside closes it. The sections are empty until their slices
land: sessions (#4), devices (#5), settings (#6) and keep-awake (#7). A keybind
for the popup lands in #12.

## Requirements

- Omarchy v4.0.4, the release the plugin is tested against.
- SidePulse at upstream commit `6d55225fed20a82521b23d86a4df2c4a15dff295`, the
  commit the plugin is tested against. The helper runs under that install's
  Python.
- Later slices add their own requirements here: `wasmtime` in the SidePulse
  venv for the Bar LEDs (#8), `systemd-inhibit` for keep-awake (#7), and
  `xdg-terminal-exec`, plus optional `herdr` or `tmux`, to jump into sessions
  (#4).

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
  `sidepulse service start` only when you choose Start service.

It makes no network calls and needs no sudo. The list grows with each feature
and is complete at #12.

## License

MIT. See [LICENSE](LICENSE).
