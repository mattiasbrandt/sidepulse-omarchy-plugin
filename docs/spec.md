# SidePulse Omarchy plugin — Spec & Plan

Status: **spec agreed 2026-10-10** (decisions D1–D16 below). Implementation not started.

## Goal

Give SidePulse devices (Dot, Pro) on Omarchy the same day-to-day
functionality the macOS SidePulse menu-bar app has, as a native Omarchy shell
plugin. Developed on a desktop (`protoarchy`, Dot) and a laptop.

## Context (development machine, verified 2026-10-10)

- Device: SidePulse Dot, USB `1a86:fe10`, firmware 1.0.4.
  Mounts as vfat `PulseDot` at `/run/media/mattias/PulseDot`; LEDs are driven by
  writing `LEDS.LED`. Writable, no udev rule needed.
- SidePulse installed via the official installer:
  - venv `~/.local/share/sidepulse/venv`, CLI `~/.local/bin/sidepulse`
  - hooks for claude, codex, grok, cursor, junie
  - user unit `~/.config/systemd/user/sidepulse.service` (active, enabled)
- On Linux the headless service already maps aggregate agent state to LED
  animations and honors `~/.config/sidepulse/agent-monitor/settings.json`
  (display mode, per-device brightness, animations).
- Live state snapshot: `~/.local/state/sidepulse/agent-monitor/latest.json`
  (per-session: provider, cwd, display_name, mode, mode_label, message,
  updated_at, stale, priority). Decision log: `event-status.jsonl`.
- Omarchy shell is Quickshell; user plugins live in
  `~/.config/omarchy/plugins/<id>/` with a `manifest.json` (kinds: bar-widget,
  service, panel, overlay, menu). Omarchy ships its own `omarchy.agents`
  bar widget (usage/limits, not live state).
- This machine has no battery (`/sys/class/power_supply` empty) and no lid.

## macOS feature inventory → Omarchy mapping

| macOS feature | Omarchy candidate | Decision |
|---|---|---|
| Menu-bar icon, state tooltip (Idle/Working/Done/Ask) | Bar widget | In — D1, D12 |
| Dropdown of recent sessions | Popup panel | In — D13 |
| Click session → open/resume (remembered opener per provider) | Focus window / resume in terminal | In, no opener chooser — D8 |
| Connect to Device toggle | LED on/off toggle | In, per-device Manual mode — D9 |
| LED display: agent / battery | agent / battery / manual | In — D10 |
| Per-device brightness | Slider → settings.json | In — D13 |
| Settings: hook install/uninstall per provider | Toggles via `sidepulse agent-monitor` | In — D13 |
| Settings: animations per state | — | Out (hand-edit JSON) — D2 |
| Hook decision log export (CSV/HTML) | In-popup log viewer | In, viewer not export — D2 |
| Show battery 7s on plug/unplug | Helper | In — D10 |
| SidePulse Notch virtual LED overlay | Band at bar edge | In — D6, D7 |
| Keep awake with lid closed | systemd-inhibit sleep + lid | In — D11 |
| Eject prevention | — | Out (Dot doesn't need it; Pro on Linux unverified) |
| Setup window / run at login | Upstream systemd unit + plugin enable | Out (covered) |
| iPhone linking | `sidepulse link` CLI | Out (CLI covers it) |

## Decisions log

_Filled in during grilling, one entry per answered question._

- **D1 Placement:** standalone bar-widget plugin with its own icon and popup.
  Not integrated into the stock `omarchy.agents` panel (that panel is
  usage/limits and read-only; coupling to its internals is fragile).
- **D2 Scope:** in addition to the core (bar icon, session list, click to
  open, LED on/off, brightness, hook toggles), v1 includes:
  - keep-awake while agents work (Never / When Agents Work / Always);
  - Notch-style on-screen virtual LEDs;
  - in-popup hook decision log viewer;
  - **laptop features** — battery LED display mode, show battery on
    plug/unplug, lid-closed behaviour — even though `protoarchy` has no
    battery or lid: the plugin must work on the user's laptop and for others.
  - Not selected: per-state animation editor (settings JSON by hand for now).
- **D3 Distribution:** own public GitHub repo first, installed into
  `~/.config/omarchy/plugins/<id>/` (laptop installs it like any other user);
  offer it upstream to inteliwear/sidepulse once stable. Consequence: no
  machine-specific paths or assumptions; README + license + install step.
  Plugin id: `io.github.mattiasbrandt.sidepulse`.
- **D4 Architecture: thin UI, upstream does logic.**
  - QML displays and controls only; it never reimplements aggregation, the
    LED interpreter, or the keep-awake rules.
  - All upstream access goes through one swappable adapter: today
    `sidepulse agent-monitor status --json` / `doctor --json` and the CLI;
    later the Rust versioned socket protocol (PR #52).
  - Linux gaps (battery reader, keep-awake/lid, Notch frames) live in a small
    Python helper in the plugin repo that imports upstream's own code from
    the SidePulse venv (aggregate rules, settings load/save, keep-awake
    predicate, `sdled.wasm`). Each gap is shaped as a future upstream PR.
- **D5 Helper lifecycle:** the widget spawns the helper as a child process and
  reads NDJSON from its stdout. No extra unit or install step. Shell restart
  restarts the helper and releases any keep-awake inhibitor (safe failure).
  Engineering rule (not a user decision): exactly one helper per shell even
  with one widget copy per monitor — owned by a single designated copy.
- **D6 Notch engine:** the helper runs upstream's `sdled.wasm` via the
  `wasmtime` Python package (49.0.0, `py3-none-manylinux1_x86_64` wheel,
  verified downloadable for the venv's Python 3.14). README requirement:
  `~/.local/share/sidepulse/venv/bin/pip install wasmtime`. Survives
  `sidepulse update` (it force-reinstalls only the sidepulse wheel set).
  If wasmtime is missing, the helper reports a capability error and the
  Notch feature is unavailable; it does not silently fall back.
- **D7 Notch placement:** thin 8-LED band drawn at the bar's inner edge
  (bottom edge for a top bar), centered, on every monitor; never overlaps
  windows. Feasibility of drawing a full-width band from a bar-widget slot is
  **unverified** — first spike in the plan (fallback: a companion `panel`
  kind anchored to the bar edge).
- **D8 Session click:** focus the live terminal window if the agent process
  still runs (walk the process's parents to a Hyprland client pid, then
  `hyprctl dispatch focuswindow pid:<pid>`), else open the user's default
  terminal via `xdg-terminal-exec` in the session cwd running upstream's resume
  command. No per-provider "Open with..." chooser / VS Code opener in v1.
- **D9 LED on/off:** per-device. Off = set that device's `led_display` to
  `custom` in upstream settings.json (service skips it; re-read each 1 s)
  and write `off`; On = set back to `agent`. Persists; uses upstream's own
  mechanism. Written through upstream `load_settings`/`save_settings` in the
  helper (never hand-edited JSON, which would drop or corrupt keys).
- **D10 Laptop features: helper only, no upstream PR.** The helper reads
  `/sys/class/power_supply` and logind lid state, builds upstream's battery
  snapshot and calls upstream's `program_for_battery` / lid animation
  settings. It drives the LEDs itself only while battery/lid output is
  showing, with the device in Manual (`custom`) mode so the service doesn't
  fight it, then hands back. This overrides D4's "shape each gap as an
  upstream PR" for these features.
- **D11 Keep-awake scope (Mac parity):** helper holds
  `systemd-inhibit --what=sleep:handle-lid-switch` while upstream's predicate
  says hold (Working/Tool Running/Long Task, 300 s grace after
  Done/Ask/Error, battery safeguard < 20 % unplugged), per
  `sleep_prevention_policy` never/agents/always in upstream settings. Screen
  lock is **not** blocked. Never touches `omarchy toggle idle` (user's own
  global state). Laptop check: lid inhibitor only works where logind handles
  the lid switch — verify on the laptop.
- **D12 Bar icon:** always shown. Muted when Idle; upstream state colours
  (Working #00AEEF, Done #22B14C, Ask #FF8A00) so the icon matches the
  physical LED; Ask also sets `active` (urgent highlight). Tooltip: state,
  active count, top session.
- **D13 Settings UI:** all inside the one popup — Sessions (top), Devices
  (per-device on/off, display mode, brightness), Keep awake (policy), and a
  collapsible Settings section (hooks per provider, Notch on/off, idle
  timeout, session retention, hook log viewer). Keyboard navigable.
  Engineering rule: SidePulse behaviour settings are stored in upstream's
  settings.json (`virtual_status_device_enabled`, `sleep_prevention_policy`,
  `agent_list.*`, `devices[]`) via the helper, so they mean the same thing as
  on the Mac; only plugin-presentation settings go in the shell.json entry.
- **D14 Upstream compatibility:** feature-detect, fail loudly. The helper
  probes imports / CLI subcommands / JSON keys at start; each missing
  capability disables only its feature with a visible reason in the popup.
  README records the upstream commit tested against. A Rust-protocol adapter
  is added behind the same interface when the Rust build ships. That build
  also ships upstream's own KSNI tray icon: README will say to keep only one
  (disable the KSNI tray on Omarchy, or hide this widget).
- **D15 Verification scope:** pytest for the helper against upstream's own
  code (state mapping, settings round-trip, keep-awake predicate, battery
  parsing from fixture `/sys` trees, wasm frames vs upstream Python);
  `omarchy plugin validate` + `qmllint` for QML; manual pass in the live
  shell on protoarchy with the Dot; laptop checklist for battery/lid. No QML
  test harness.
- **D16 Tracking:** public repo `mattiasbrandt/sidepulse-omarchy-plugin`, local
  `~/Documents/GitHub/sidepulse-omarchy-plugin`; this spec moves in as `docs/spec.md`; one
  GitHub issue per plan slice plus one verification ticket.

## Spec

### Components

```
sidepulse-omarchy-plugin/                    (repo root = plugin root)
├── manifest.json        id io.github.mattiasbrandt.sidepulse, kinds [bar-widget]
├── Panel.qml            root Panel: BarIconButton + KeyboardPanel popup + IpcHandler
├── Service.qml          owns the helper Process (singleton), parses NDJSON, exposes model
├── Notch.qml            8-LED band renderer (frames from helper)
├── *.qml / Model.js     popup sections: Sessions, Devices, KeepAwake, Settings, HookLog
├── helper/
│   └── sidepulse_omarchy.py   runs under SidePulse's venv python
├── tests/               pytest for the helper
├── README.md  LICENSE (MIT)  preview.png  docs/spec.md
```

### Helper (`helper/sidepulse_omarchy.py`)

- Launched by `Service.qml` as
  `~/.local/share/sidepulse/venv/bin/python -I <plugin>/helper/sidepulse_omarchy.py`
  (venv path overridable via widget setting `sidepulsePython`; default also
  tries `sidepulse`'s shebang interpreter).
- **Protocol:** NDJSON. Stdout events, each `{"type": ..., ...}`:
  `hello` (helper version, upstream version, capabilities map with reasons),
  `state` (aggregate mode, four-state label, colour, active_count, sessions[]),
  `devices` (id, name, path, led_display, brightness, kind Dot/Pro, led count),
  `hooks` (doctor --json payload), `power` (battery %, plugged, lid),
  `awake` (holding bool, reason), `frame` (8×RGB for the Notch band),
  `log` (recent hook decision rows), `error` (feature, message).
  Stdin commands: `set-device-enabled`, `set-brightness`, `set-display`,
  `set-policy`, `set-notch`, `set-agent-list`, `hooks-install`,
  `hooks-uninstall`, `open-session`, `refresh`.
- **Upstream reuse (D4):** state = watch `latest.json` (inotify) and run
  upstream's `snapshot_from_statuses` on it with
  `settings.idle_timeout_seconds` and retention, the same rules the Mac
  status bar applies, re-evaluated on a timer so ages and staleness advance.
  Not polling `status --json`: it rescans the provider JSONL logs, and
  `claude.jsonl` was already 2.2 MB after one evening (confirmed in spike 0).
  Never binds `events.sock` (it would stop the service driving LEDs); settings via `load_settings`/`save_settings`/`with_*`;
  devices via `discover_devices`; LED writes via `write_led_program`; keep-
  awake predicate via `KeepAwakeController.should_hold_for_mode`; battery
  programs via `program_for_battery`; Notch frames via `sdled.wasm` + upstream
  blending constants. Hook install/uninstall via the `sidepulse agent-monitor`
  CLI.
- **Linux-only additions (D10, D11):** `/sys/class/power_supply` reader that
  produces upstream's battery snapshot type; logind lid state; holds a
  `systemd-inhibit --what=sleep:handle-lid-switch --mode=block` child while
  the predicate says hold; LED handover for battery/lid display (device to
  `custom`, write program, restore afterwards).
- **Capability probe (D14)** at start; each feature independently on/off with
  a reason. No silent fallbacks.
- Exits when stdin closes (shell gone) and releases the inhibitor and any
  LED handover.
- **LED handback (verified):** the service drops Manual (`custom`) devices
  from its skip-unchanged-writes cache (`service.py:216-218`), so when the helper sets
  a device back to `agent` the service repaints on its next 1 s poll. The
  helper never needs to repaint agent programs itself.

### Widget behaviour

- Icon (D12): always visible; colour by four-state label; Ask → `active`.
  Tooltip: state, active count, top session.
- Popup (D13), keyboard navigable (j/k, Enter, Esc):
  1. **Sessions** — up to 10, upstream ordering; row = provider mark,
     project (cwd basename), state, last message, age. Enter/click = D8.
  2. **Devices** — per device: on/off (D9), display agent/battery, brightness.
  3. **Keep awake** — Never / When Agents Work / Always; shows holding state.
  4. **Settings** (collapsible) — hooks per provider (status from doctor,
     toggle install/uninstall), Notch on/off, idle timeout, retention,
     hook log viewer (last N decision rows), capability problems.
- Notch band (D7) when `virtual_status_device_enabled`.
- IPC: `omarchy-shell shell toggle io.github.mattiasbrandt.sidepulse` opens the
  popup; README suggests a keybind (the plugin installs none).
- Exactly one helper per shell (D5); all widget copies render from the shared
  model.

### Out of scope (v1)

Per-state animation editor and profiles; "Open with…" chooser / VS Code
opener; eject prevention; iPhone linking UI; hook-log CSV/HTML export;
upstream PRs for the Linux battery/lid code (D10); QML test harness.

### Requirements documented in README

SidePulse installed via the official installer; `wasmtime` in the SidePulse
venv for the Notch; `xdg-terminal-exec` (Omarchy default); `systemd-inhibit`
(systemd); the upstream commit tested against.

**Security section** (marketplace W8; plugins are unsandboxed): list exactly
what the plugin touches — runs a Python helper from the SidePulse venv;
rewrites agent configs through the upstream CLI on hook toggles
(`~/.claude/settings.json`, `~/.codex/config.toml`, `~/.grok/hooks/`,
`~/.cursor/hooks.json`, `~/.junie/config.json`); writes upstream
`settings.json`; writes `LEDS.LED` on mounted SidePulse drives; holds a
logind sleep/lid inhibitor; reads `/proc` for session→window lookup; runs
`hyprctl dispatch focuswindow` and `xdg-terminal-exec`. No network, no sudo.

## Plan

Thin vertical slices; each ends with validate + qmllint + pytest green and a
live check in the shell. One GitHub issue per slice (D16), all sub-issues of
#1 with native blocked-by edges.

0. **Spikes** (#2) (answer before building on them)
   - Can a bar-widget draw a full-width band at the bar edge? (D7; fallback:
     companion `panel` kind.)
   - Single helper across per-monitor widget copies (D5): owner election.
   - `sdled.wasm` under wasmtime produces the same frames as upstream's
     JavaScriptCore path for the built-in programs.
   - **Session → process (D8):** upstream hook lines carry no pid (keys
     verified: `session_id`, `cwd`, `transcript_path`, origin strings only;
     `hook.py` walks the process tree at hook time but doesn't record the pid).
     Find the live agent by a `/proc` scan (cmdline contains the session id, or
     cwd match + open `transcript_path` fd). If no unique match: resume-new.
   - **State source (D4):** confirm `latest.json` + `snapshot_from_statuses`
     tracks the live state as fast as `status --json` at a fraction of the cost.
1. **Scaffold** (#3) — repo, manifest, MIT LICENSE, README skeleton, static icon
   in the bar; `omarchy plugin validate` passes; installs via
   `omarchy plugin add <local path>`.
2. **Helper core + live icon** (#10) — capability probe, `hello`/`state` events,
   Service.qml singleton, icon colour + tooltip follow real agents.
3. **Sessions popup + open** (#4) — list, keyboard nav, focus-or-resume (D8).
4. **Devices** (#5) — discovery, on/off via Manual mode, brightness, display mode.
5. **Settings section** (#6) — hooks toggles, timeouts/retention, hook log
   viewer, capability problems.
6. **Keep awake** (#7) — policy, predicate, inhibitor lifecycle.
7. **Laptop** (#11) — battery reader, battery display, 7 s on plug/unplug, lid
   animations, LED handover.
8. **Notch band** (#8) — wasm frames → band rendering.
9. **Release prep** (#12) — README (install/usage/settings/requirements/remove/
   security), preview.png, push public, marketplace submission.
10. **Verification ticket** (#9) — protoarchy live pass with the Dot; laptop
    checklist (battery, plug/unplug, lid inhibitor, lid animations).

## Appendix: research (2026-10-10)

Snapshot of the facts the decisions rest on. Line numbers refer to upstream
commit 6d55225 and the Omarchy shell as installed on that date.

### Omarchy plugin contract (researched 2026-10-10, paths under /usr/share/omarchy/shell/)

- Manifest validated in `services/PluginRegistry.qml:43-90` and
  `omarchy plugin validate`: `schemaVersion: 1`, `id` matching
  `^[A-Za-z0-9][A-Za-z0-9._-]*$` (not `omarchy.*`), `kinds`, `entryPoints`
  (`barWidget` required for kind `bar-widget`), no symlinks in plugin dir.
- `barWidget.defaults` / `schema` are registry metadata only — **not** merged
  into injected `settings`; always read with `setting(name, fallback)`.
  `activation` is not parsed.
- Bar injects only `bar` (PluginBarApi facade), `moduleName`, `settings`
  (`plugins/bar/Bar.qml:1999-2005`). Plugin dir is not injected; use
  `Qt.resolvedUrl()`.
- Widget shape: root `Panel` (qs.Ui) → `BarIconButton` (glyph or
  `iconComponent`, `tooltipText`, `active` = urgent colour) + `KeyboardPanel`
  popup + `PanelKeyCatcher`. `visible: false` collapses the slot.
- Data: `FileView { watchChanges: true }` (call `reload()` on change),
  `Process` + `StdioCollector`/`SplitParser`, `Quickshell.execDetached`.
- Per-widget settings live inline in `~/.config/omarchy/shell.json` bar layout
  entry; write via `bar.shell.updateEntryInline(moduleName, mergedObj)`
  (replaces whole entry) or `omarchy bar set <id> <key> <value>`.
- Install: drop dir in `~/.config/omarchy/plugins/`, `omarchy-shell shell
  rescanPlugins`, `omarchy plugin enable <id> --section right`. Hot-reloads on
  any save under the plugins dir.
- Theme: `Color` singleton (`foreground`, `background`, `accent`, `urgent`,
  `muted`, `popups.*`); no "warning" colour. `Style` for fonts/spacing.
- IPC: `omarchy-shell shell toggle <id>` opens the popup on the focused
  monitor (keybind-able). Custom `IpcHandler` possible; one widget copy per
  monitor, so guard with `enabled: root.bar !== null`.
- Template to follow: `~/.config/omarchy/plugins/io.github.thisisgm.omapods/`
  (Panel + Service + FileView + IpcHandler). Subject-closest:
  `njpatel.omabot` (streaming watcher script).

### Omarchy plugin guidelines (local docs + plugins.omarchy.org, 2026-10-10)

MUST (enforced by `omarchy plugin validate` / shell):
- `manifest.json` at repo root; `schemaVersion: 1`; `id`, `name`, `version`,
  `kinds`, `entryPoints`; id regex, not `omarchy.*`; entry points relative,
  existing; `bar-widget` → `entryPoints.barWidget`; `defaultSection` ∈
  left|center|right; **no symlinks**.
- Installed at `~/.config/omarchy/plugins/<id>/` by
  `omarchy plugin add <git-url> [--enable --yes]`; updates are ff-only git
  pulls with validate + rollback.
- **The installer never runs plugin code, install hooks, or sudo**
  (shell/README.md:126). Anything outside the plugin dir (e.g. SidePulse
  itself) must be a documented prerequisite the user installs.

SHOULD / marketplace (plugins.omarchy.org publish + develop pages):
- Public GitHub repo, README, LICENSE; namespaced id —
  **`io.github.mattiasbrandt.sidepulse`**; `author` + short `description`;
  full `barWidget` block (displayName, category, allowMultiple,
  defaultSection, defaults, schema).
- Validate with `omarchy plugin validate` and
  `qmllint -I "$OMARCHY_PATH/shell" *.qml`.
- Document every external dependency, setup step, privilege boundary,
  service and helper. Plugins are unsandboxed in the omarchy-shell process.
- Ships disabled; user runs `omarchy plugin enable <id>`.
- Submission: GitHub issue form → automated validation → maintainer approval.

Conventions (11 installed plugins): root holds manifest, README, LICENSE,
preview.png, QML + JS model; helper scripts in a subdir (`bin/`,
`scripts/`); tests in `tests/`; README sections Install / Usage / Settings /
Requirements / Remove / Security / License.

### SidePulse internals (researched 2026-10-10, upstream commit 6d55225)

Paths relative to `src/sidepulse/`.

- **State.** `latest.json` = `{updated_at, statuses[]}`, written by the
  service only when an event changes a status (collector.py:487-503). No
  aggregate field; `age_seconds`/`stale` frozen at write time. Aggregate
  rules live in `snapshot_from_statuses` (collector.py:1231-1281): PostToolUse
  working >120 s → completed; completed stale after 20 min; idle_ready stale
  immediately; others after 3600 s; lowest priority number wins.
- **Machine-readable CLI (verified):**
  - `sidepulse agent-monitor status --json` → `{collected_at, sources,
    aggregate{mode, mode_label, active_count, stale_count, representative},
    statuses, stale_statuses}` — upstream computes the aggregate (rebuilds
    from provider JSONL logs, same rules).
  - `sidepulse agent-monitor doctor --json` → per-provider
    `{provider, config_path, exists, hooks_enabled, hook_events, log_paths}`.
  - `install|uninstall <provider>` plain text only; `service
    start|stop|status` via `systemctl --user`, exit code = running.
- **Four-state UI mapping** (status_bar.py:461-472): Ask = waiting_for_input |
  blocked_error (#FF8A00); Working = working | tool_running |
  long_task_progress (#00AEEF); Done = completed (#22B14C); Idle = rest
  (#A8A8A8). Session list = fresh + completed within 48 h retention, merged
  per (provider, session_id), sorted by priority, max 10 (status_bar.py:6317-6386).
- **Connect to Device:** in-memory flag only, and the current macOS menu no
  longer shows it (README still documents it). Linux service has no
  equivalent; options are per-device `led_display: "custom"` (service skips
  the device) or stopping the service.
- **Service gate (verified):** the service writes no LEDs while anything
  accepts on `~/.local/state/sidepulse/agent-monitor/events.sock`
  (service.py:224-235) — the macOS app takes over LEDs that way. A Linux UI
  must not bind that socket unless it takes over LED driving.
- **Settings:** `~/.config/sidepulse/agent-monitor/settings.json` (absent here
  → defaults). Re-read by the service every 1 s poll. No general settings
  CLI; `save_settings` rewrites from `to_dict`, dropping unknown keys. Service
  honours only per-device display, brightness and `agent_animation(mode)`;
  ignores idle timeout, retention, sleep policy, lid animations.
- **Session open** (session_actions.py:38-78): resume commands `claude --resume
  <id>`, `codex resume <id>`, `grok --resume <id>`, `junie --session-id=<id>
  --resume`, run in `cwd`; VS Code `vscode://anthropic.claude-code/open?session=<id>`.
  All terminal launch/focus code is AppleScript (macOS-only).
- **macOS-only today:** battery reader (`ioreg`; verified failing here),
  keep-awake (`caffeinate`) and lid (`pmset`, `ioreg`), Notch (`sdled.wasm`
  run in JavaScriptCore via PyObjC), power-source notifications, `sidepulse
  settings`. Linux equivalents: `/sys/class/power_supply`, `systemd-inhibit`,
  logind lid state.
- **Notch engine:** `resources/sdled.wasm` (34 KB, no imports; exports
  `sdled_reset/parse/step`, RGB bytes at output ptr, 2 or 8 LEDs). QML has no
  WebAssembly; would need a node/deno/wasmtime helper streaming frames, or a
  JS port of the `virtual_led_colors` fallback.
- **Keep-awake predicate** (keep_awake.py:80-98): hold while Working/Tool
  Running/Long Task; 300 s grace after Completed/Waiting/Error; battery
  safeguard below 20 % unplugged.

### Upstream SidePulse alignment (researched 2026-10-10)

- **No releases, no tags, not on PyPI.** Install = git `main`. Pin a SHA.
- **Upstream is migrating to Rust**: PR #52 "Rust migration checkpoint: core,
  service, hooks, and tray" (open, 89 commits, last push 2026-10-06, no
  review yet). Python stays operational until parity
  (`docs/design/rust-architecture.md`, pr52). Rust brings:
  - a **Linux tray via KSNI** (StatusNotifierItem, `crates/sidepulse-tray`);
  - a versioned NDJSON socket protocol (`PROTOCOL_VERSION=1`, `Snapshot`,
    `Subscribe`, `RenderLedProgram`, `VirtualDisplayFrame`);
  - Tauri/WebKitGTK settings window on Linux (proposal);
  - rule: keep settings/JSONL/link/relay JSON compatible, migrate only with a
    versioned reader. Linux native UI explicitly **not validated** upstream.
- **Third-party clients:** issue #55 (open, 0 replies) — Rust socket breaks
  the Python `{"provider","line"}` event message; asks for a documented
  client interface. No other upstream statement on third-party frontends.
- **No CONTRIBUTING / AGENTS.md**; MIT; pytest; design docs in `docs/design/`
  are the contributor contract. Single main maintainer; PRs/issues mostly
  unanswered → upstreaming will be slow and uncertain.
- **Surfaces:** `status --json` works headless but is undocumented and
  unversioned; `latest.json` and `settings.json` have no version key;
  `LEDS.LED` DSL is public (`LEDS_FORMAT.md`); `sdled.wasm` is internal.
- **Drift risk high**: socket paths/state dirs expected to change at the Rust
  cutover; open PRs #53, #56, #57, #58 touch integration surfaces.
- Agent recommendation: consume upstream through a thin, swappable adapter
  (CLI JSON now, Rust protocol later); don't reimplement aggregation or the
  LED interpreter; reuse upstream parity fixtures as conformance tests.
