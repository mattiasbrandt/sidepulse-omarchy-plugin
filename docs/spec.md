# SidePulse Omarchy plugin — Spec & Plan

Status: **spec agreed 2026-10-10, plan review 2026-10-11** (decisions D1–D24 below). Implementation not started.

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
| SidePulse Notch virtual LED overlay | Bar LEDs along the bar edge | In — D6, D7, D21 |
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
  - Bar LEDs, the Omarchy counterpart of SidePulse Notch;
  - in-popup hook decision log viewer;
  - **laptop features** — battery LED display mode, show battery on
    plug/unplug, lid-closed behaviour — even though `protoarchy` has no
    battery or lid: the plugin must work on the user's laptop and for others.
  - Not selected: per-state animation editor (settings JSON by hand for now).
- **D3 Distribution:** own public GitHub repo first, installed into
  `~/.config/omarchy/plugins/<id>/` (laptop installs it like any other user);
  offer it upstream to `inteliwear/sidepulse` once stable. Consequence: no
  machine-specific paths or assumptions; README + license + install step.
  Plugin id: `io.github.mattiasbrandt.sidepulse`.
- **D4 Architecture: thin UI, upstream does logic.**
  - QML displays and controls only; it never reimplements aggregation, the
    LED interpreter, or the keep-awake rules.
  - All upstream access goes through one swappable adapter: today
    `latest.json` + upstream's reader and rules, `doctor --json` and the
    CLI; later the Rust versioned socket protocol (PR #52).
  - *Spike #2 (state source):* reading `latest.json` with upstream's own
    `agent_status_from_dict` + `snapshot_from_statuses` gives the identical
    aggregate, counts and session ids as `agent-monitor status --json`, in
    0.4 ms vs ~120 ms (which rescans a 2.4 MB `claude.jsonl` and grows with
    it). The service writes `latest.json` (tmp + rename) within the same second
    as the hook line. Watch the **directory** for `MOVED_TO latest.json`, and
    re-run the rules on a timer so ages and staleness advance between writes.
    Probe: was `spikes/state_reader.py`; since #10 its code is the helper's state
    path in `helper/sidepulse_omarchy.py`.
  - *Amended in #10:* the four-state mapping and its colours (`state_for_mode`
    and the Ask/Working/Done/Idle legend, `status_bar.py` at 6d55225) are copied
    into the helper, because that module needs PyObjC and cannot be imported on
    Linux. `tests/test_state.py` reads the upstream source and fails when the
    mapping, the state labels or the colours drift. Everything else calls
    upstream's rules.
  - Linux gaps (battery reader, keep-awake/lid, Bar LED frames) live in a small
    Python helper in the plugin repo that imports upstream's own code from
    the SidePulse venv (aggregate rules, settings load/save, keep-awake
    predicate, `sdled.wasm`). Each gap is shaped as a future upstream PR.
- **D5 Helper lifecycle:** the widget spawns the helper as a child process and
  reads NDJSON from its stdout. No extra unit or install step. Shell restart
  restarts the helper and releases any keep-awake inhibitor (safe failure).
  Engineering rule (not a user decision): exactly one helper per shell even
  with one widget copy per monitor.
  *Spike #2 (single helper), measured live on 3 monitors:* a plugin-local
  `pragma Singleton` (declared in a `qmldir`, imported with `import "."`) is
  shared by every widget copy and owns one `Process`. Consequences:
  - The singleton **survives plugin hot-reload and plugin disable** (lives as
    long as the shell). Its child process outlived `omarchy plugin disable`.
    So the singleton refcounts widget copies and stops the helper when the
    count stays at 0 (debounced — a reload drops it to 0 and back within
    milliseconds), and restarts it when a copy registers again.
  - Edits to the singleton's own QML do **not** hot-reload (old code kept
    running until `omarchy restart shell`). Keep the singleton a thin process
    owner and line dispatcher; logic lives in the helper and in ordinary
    components. Note in AGENTS.md's live-check step.
  - `bar` is still null in a widget's `Component.onCompleted` (injected after).
- **D6 Bar LEDs engine:** the helper runs upstream's `sdled.wasm` via the
  `wasmtime` Python package (49.0.0, `py3-none-manylinux1_x86_64` wheel,
  verified downloadable for the venv's Python 3.14).
  *Spike #2 (wasm parity):* `spikes/wasm/sdled_wasmtime.py` (a line-for-line
  port of upstream `led_wasm.py`'s call sequence) and an independent Deno
  driver give byte-identical frames in 117/117 cases (off, all agent modes,
  26 built-in animations, battery programs, 8 invalid programs; 2 and 8
  LEDs; t = 0…2500 ms), and match upstream's own test vectors
  (`tests/test_sidepulse.py`). Both drive the same wasm, so this proves the
  port, not engine-vs-device; upstream's vectors are the engine reference.
  Cost 0.017 ms/frame (60 fps budget 16.7 ms), 4 ms to instantiate. Re-run:
  `spikes/wasm/compare.py`. README requirement:
  `~/.local/share/sidepulse/venv/bin/pip install wasmtime`. Survives
  `sidepulse update` (it force-reinstalls only the `sidepulse` wheel set).
  If wasmtime is missing, the helper reports a capability error and the
  Bar LEDs are unavailable; they do not silently fall back.
- **D7 Bar LEDs placement:** thin row of LEDs drawn at the bar's inner edge
  (bottom edge for a top bar), centered, on every monitor; never overlaps
  windows.
  *Spike #2 (verified live):* the bar is one layer-shell `PanelWindow` per
  monitor with no clipping. A widget child reparented to
  `root.QsWindow.window.contentItem` (high `z`, anchored to the bottom edge)
  draws across the full bar width on every monitor (3440, 2560, 1920 px
  windows; screenshot-checked). No companion `panel` kind is needed.
- **D8 Session click:** focus the terminal window of the process that
  provably runs the session; resume (user's default terminal via
  `xdg-terminal-exec`, in the session cwd, upstream's resume command) only
  when no process can own it; otherwise explain where it may be running. No per-provider
  "Open with..." chooser / VS Code opener in v1.
  *Spike #2 (session → process → window), `spikes/session_lookup.py`,
  tests `spikes/test_session_lookup.py`:*
  - Every lookup ends in one of three states. **stopped**: no process can own
    the session → resume. **live**: a process provably owns it → focus its
    window, or say where it runs. **unresolved**: a process might own it but
    nothing proves it → show the candidates; never resume (could duplicate a
    live session) and never focus (could be the wrong session).
  - Proof, strongest first. Claude writes `~/.claude/sessions/<pid>.json`
    (`sessionId`, `procStart` = `/proc/<pid>/stat` starttime, a pid-reuse
    guard; honours `CLAUDE_CONFIG_DIR`). Grok writes
    `~/.grok/active_sessions.json` (`session_id`, `pid`). Any provider: the
    session id as a whole command-line argument (`codex resume <id>`,
    `junie --session-id=<id>`). Upstream hook lines carry no pid.
  - Without proof, a process of the provider's binary in the session cwd makes
    the session **unresolved**; none makes it **stopped**. Only processes
    holding a terminal count: `codex app-server` daemons (ten live here, most
    in other cwds) have none. A plain `codex` with no session argument (live
    here in protoR2) therefore leaves every Codex session in that cwd
    unresolved until one is proven. That's correct, but it means clicking an
    unproven Codex/Cursor/Junie session in a busy cwd explains instead of acting.
  - Window: walk ancestors to a pid owning Hyprland clients. **Ghostty is
    single-instance: all its windows share one pid**, so narrow by title —
    Claude titles its window with the transcript's `ai-title` (verified:
    picked the right one of two Ghostty windows). Focus by address.
  - Hyprland 0.56 is Lua: `hyprctl dispatch 'hl.dsp.focus({ window =
    "address:0x…" })'`. A bad address still exits 0 and prints
    `hl.focus: window not found` — parse the output. Focusing a window on
    another workspace not yet tested (live check in #4).
  - **Live but no window** (agent inside herdr/tmux — both live Claude and
    Grok sessions here): *amended in the plan review* — jump into the
    multiplexer. A small adapter per multiplexer finds the pane holding the
    process, focuses it, then focuses the window running a client of that
    multiplexer, or opens a terminal attached to it when none runs. v1:
    **herdr** (verified read-only: `herdr pane process-info --pane <id>` lists
    the pane's foreground pids; `herdr agent focus <pane>`; the attached client
    runs in a Ghostty window) and **tmux** (`list-panes -a -F '#{pane_pid} …'`,
    `select-window`/`select-pane`, `switch-client`; not exercised live). Any
    other ancestor, or an ambiguous title match: show "running in <ancestor>
    (pid)", no guess.
- **D9 LED on/off:** per-device. Off = set that device's `led_display` to
  `custom` in upstream settings.json (service skips it; re-read each 1 s)
  and write `off`; On = set back to `agent`. Persists; uses upstream's own
  mechanism. Written through upstream `load_settings`/`save_settings` in the
  helper (never hand-edited JSON, which would drop or corrupt keys).
  *Amended in the plan review:* on Linux upstream can store only Agent or
  Manual for a device, but users choose between **Agent, Battery and Off**
  (Battery is helper-driven, D10). So the **plugin remembers each device's
  chosen display** in its widget settings (keyed by device id), and the
  helper re-derives upstream's value from it (Agent → `agent`; Battery, Off →
  `custom`) at start and after every takeover. That also repairs a device
  left Manual by a crash mid-takeover. This one per-device value is the
  deliberate exception to "settings through upstream".
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
  collapsible Settings section (hooks per provider, Bar LEDs on/off, idle
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
- **D17 Versioning and releases (#14):** semantic versioning from
  Conventional Commits. `main` takes squash merges only and the PR title is the
  commit (checked in CI). release-please keeps a release PR with the version,
  generated `CHANGELOG.md` and `manifest.json` `version`; merging it tags
  `vX.Y.Z` and publishes the GitHub Release. Before 1.0.0, `feat` and breaking
  changes bump the minor, `fix` the patch. CI on every PR: Omarchy's own
  plugin validator at the pinned Omarchy tag, Python tests, qmllint against
  the Omarchy shell in an Arch container, actionlint; SHA-pinned actions moved
  by Dependabot. Reused from protoR2: the type → version table, SHA pins with
  Dependabot, the checksum-verified actionlint step. Rules: `CONTRIBUTING.md`.
- **D18 Popup frame first (plan review):** #10 ships the popup itself, empty,
  keyboard-navigable, with its section layout; #4–#7 each fill one section and
  stay independent of each other.
- **D19 Setup state (plan review):** the icon stays visible with a distinct
  "needs setup" look; the popup's top section lists what's missing, in order:
  SidePulse not installed (show the install command), service stopped ("start
  service" runs `sidepulse service start`), no device ("plug in a SidePulse").
  Agent State keeps working without a device.
  *Amended in #10:* the helper's `setup` event lists the items in that order
  (`install`, `service`, `device`). The install item shows upstream's installer
  line (`curl -fsSL https://sidepulse.io/setup.sh | bash`, from its README); the
  service item carries the `start-service` action, which runs
  `python -I -m sidepulse service start` in the SidePulse venv. A missing venv
  is reported by the shell, since no helper can run there.
- **D20 Ask notification (plan review):** opt-in setting "Notify when a
  session asks", off by default; clicking the notification jumps to the
  session (D8). Beyond Mac parity: the Mac app sends no agent notifications.
- **D21 The look follows the product (plan review):** popup device rows draw
  a SidePulse Dot (2 LEDs) or SidePulse Pro (8 LEDs) lit live; the Bar LEDs
  match the connected product (2 or 8; 8 with none or both, since upstream
  prefers the Pro); the bar icon is a Dot or Pro glyph, neutral when none is
  connected.
- **D22 Own artwork (plan review):** product drawings are our own vector
  shapes in QML, themed, with live LED colours. No SidePulse brand assets.
- **D23 SidePulse Pro supported, untested (plan review):** the Pro runs through
  upstream's device code like the Dot. README marks it untested on hardware;
  the verification ticket asks for a Pro tester. A Pro that unmounts on
  suspend shows "not connected" until it remounts; no eject prevention.
- **D24 Names follow upstream (plan review):** "SidePulse", "SidePulse Dot",
  "SidePulse Pro" in all prose and UI, including the manifest's display name;
  lowercase `sidepulse` only inside identifiers. Our virtual LEDs are **Bar
  LEDs**, the Omarchy counterpart of upstream's SidePulse Notch.
  Vocabulary: [`GLOSSARY.md`](../GLOSSARY.md).

## Spec

### Components

```
sidepulse-omarchy-plugin/                    (repo root = plugin root)
├── manifest.json        id io.github.mattiasbrandt.sidepulse, kinds [bar-widget]
├── qmldir               declares the singleton below
├── HelperProcess.qml    pragma Singleton: owns the one helper Process, refcounts
│                        widget copies (stop at 0, debounced), parses NDJSON (D5)
├── Panel.qml            root Panel: BarIconButton + KeyboardPanel popup + IpcHandler
├── BarLeds.qml          Bar LEDs, reparented into the bar window (D7, D21)
├── *.qml / Model.js     popup sections: Sessions, Devices, KeepAwake, Settings, HookLog
├── helper/
│   └── sidepulse_omarchy.py   runs under SidePulse's venv python
├── tests/               pytest for the helper
├── spikes/              #2 probes; their code moves into helper/ in #10, #4, #8
├── README.md  LICENSE (MIT)  preview.png  docs/spec.md
```

### Helper (`helper/sidepulse_omarchy.py`)

- Launched by the `HelperProcess` singleton as
  `~/.local/share/sidepulse/venv/bin/python -I <plugin>/helper/sidepulse_omarchy.py`
  (venv path overridable via widget setting `sidepulsePython`; default also
  tries `sidepulse`'s shebang interpreter).
- **Protocol:** NDJSON. Stdout events, each `{"type": ..., ...}`:
  `hello` (protocol 1, helper version, upstream version, capabilities map with
  reasons), `state` (four-state label, colour, urgent for Ask, active_count,
  stale_count, top session), `setup` (missing items in order, D19), `result`
  (outcome of a command), `error` (feature, message),
  `devices` (id, name, path, led_display, brightness, kind Dot/Pro, led count),
  `hooks` (doctor --json payload), `power` (battery %, plugged, lid),
  `awake` (holding bool, reason), `frame` (RGB per Bar LED),
  `log` (recent hook decision rows).
  Stdin commands: `refresh`, `start-service` (*amended in #10*: `setup` and
  `result` are new), `set-device-enabled`, `set-brightness`, `set-display`,
  `set-policy`, `set-bar-leds`, `set-agent-list`, `hooks-install`,
  `hooks-uninstall`, `open-session`. `open-session` answers with
  the D8 lookup state (stopped → resumed, live → focused or located,
  unresolved → explanation).
  *Amended in #10:* `state` and `setup` are re-sent on change, not on a fixed
  schedule. The helper re-runs the rules every second and re-checks setup every
  five seconds, so a stopped service or an unplugged device shows up without a
  restart.
- **Upstream reuse (D4):** state = watch the `latest.json` directory
  (inotify `MOVED_TO latest.json`; the service writes tmp + rename) and run
  upstream's `snapshot_from_statuses` on it with
  `settings.idle_timeout_seconds` and retention, the same rules the Mac
  status bar applies, re-evaluated on a timer so ages and staleness advance.
  Not polling `status --json`: it rescans the provider JSONL logs (measured
  in #2: 0.4 ms for `latest.json` vs ~120 ms, growing with the logs).
  Never binds `events.sock` (it would stop the service driving LEDs); settings via `load_settings`/`save_settings`/`with_*`;
  devices via `discover_devices`; LED writes via `write_led_program`; keep-
  awake predicate via `KeepAwakeController.should_hold_for_mode`; battery
  programs via `program_for_battery`; Bar LED frames via `sdled.wasm` + upstream
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

- Icon (D12, D21): always visible; a SidePulse Dot or Pro glyph for the
  connected product (neutral with none); colour by State; Ask → `active`;
  a distinct "needs setup" look while D19 applies. Tooltip: State, active
  count, top session.
- Popup (D13, D18), keyboard navigable (j/k, Enter, Esc):
  0. **Setup** (only while something is missing, D19).
  1. **Sessions** — up to 10, upstream ordering; row = provider mark,
     project (cwd basename), state, last message, age. Enter/click = D8.
  2. **Devices** — per device, drawn as its product with live LEDs (D21, D22):
     display Agent / Battery / Off (D9), brightness.
  3. **Keep awake** — Never / When Agents Work / Always; shows holding state.
  4. **Settings** (collapsible) — hooks per provider (status from doctor,
     toggle install/uninstall), Bar LEDs on/off, idle timeout, retention,
     hook log viewer (last N decision rows), "Notify when a session asks"
     (D20), capability problems.
- Bar LEDs (D7, D21) when `virtual_status_device_enabled` (upstream's SidePulse
  Notch setting, so it means the same on both).
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
venv for the Bar LEDs; `xdg-terminal-exec` (Omarchy default); optional `herdr` or
`tmux` for jumping into sessions that run inside them (D8); `systemd-inhibit`
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

First, **CI and release automation** (#14, D17), before any plugin code.

0. **Spikes** (#2) — answered; results are the *Spike #2* notes under D4–D8.
   - Band: widget child reparented into the bar window, full width (D7).
   - Single helper: plugin QML singleton, refcounted; survives reload/disable (D5).
   - wasm: wasmtime port byte-identical to an independent driver, 117/117 (D6).
   - Session → window: stopped / live / unresolved lookup (D8).
   - State source: `latest.json` + upstream rules, 0.4 ms vs ~120 ms (D4).
1. **Scaffold** (#3) — repo, manifest, MIT LICENSE, README skeleton, dev test
   environment and runner (command in CONTRIBUTING.md's Checks table), static icon
   in the bar; `omarchy plugin validate` passes; installs via
   `omarchy plugin add <local path>`.
2. **Helper core + live icon** (#10) — capability probe, `hello`/`state` events,
   `HelperProcess` singleton (refcounted), icon colour + tooltip follow real
   agents, the empty popup frame (D18) and the setup state (D19).
3. **Sessions popup + open** (#4) — list, keyboard nav, three-state open (D8).
4. **Devices** (#5) — discovery, on/off via Manual mode, brightness, display mode.
5. **Settings section** (#6) — hooks toggles, timeouts/retention, hook log
   viewer, capability problems.
6. **Keep awake** (#7) — policy, predicate, inhibitor lifecycle.
7. **Laptop** (#11) — battery reader, battery display, 7 s on plug/unplug, lid
   animations, LED handover.
8. **Bar LEDs** (#8) — wasm frames → Bar LEDs rendering.
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
  keep-awake (`caffeinate`) and lid (`pmset`, `ioreg`), SidePulse Notch (`sdled.wasm`
  run in JavaScriptCore via PyObjC), power-source notifications, `sidepulse
  settings`. Linux equivalents: `/sys/class/power_supply`, `systemd-inhibit`,
  logind lid state.
- **SidePulse Notch engine (upstream):** `resources/sdled.wasm` (34 KB, no imports; exports
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
