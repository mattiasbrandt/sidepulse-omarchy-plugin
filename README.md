# SidePulse for Omarchy

[![CI](https://github.com/mattiasbrandt/sidepulse-omarchy-plugin/actions/workflows/ci.yml/badge.svg)](https://github.com/mattiasbrandt/sidepulse-omarchy-plugin/actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/mattiasbrandt/sidepulse-omarchy-plugin)](https://github.com/mattiasbrandt/sidepulse-omarchy-plugin/releases/latest)
[![License: MIT](https://img.shields.io/github/license/mattiasbrandt/sidepulse-omarchy-plugin)](LICENSE)

An Omarchy bar widget for SidePulse devices and AI coding agents: it shows
agent status in the bar and drives SidePulse devices, the way the macOS
SidePulse menu-bar app does.

This is an early skeleton. The bar icon is a neutral glyph and the popup is
empty; the features land slice by slice, tracked in the
[plan](docs/spec.md) and its issues.

## Install

```sh
omarchy plugin add https://github.com/mattiasbrandt/sidepulse-omarchy-plugin
```

It asks whether to enable the widget and which bar section to put it in. The
SidePulse prerequisites that the helper needs land with the helper in #10.

## Usage

Click the SidePulse icon in the bar to open the popup; Esc or a click outside
closes it. The popup has no content yet: sessions (#4), devices (#5), settings
(#6) and keep-awake (#7) fill it in. A keybind for the popup lands in #12.

## Requirements

- Omarchy v4.0.4, the release the plugin is tested against.
- SidePulse at upstream commit `6d55225fed20a82521b23d86a4df2c4a15dff295`, the
  commit the plugin is tested against. The helper that uses it lands in #10.
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

Plugins run unsandboxed inside `omarchy-shell`. This skeleton touches only its
own plugin directory: it starts no helper process, makes no network calls and
needs no sudo. The full list of files and system state the plugin touches
lands with each feature and is complete at #12.

## License

MIT. See [LICENSE](LICENSE).
