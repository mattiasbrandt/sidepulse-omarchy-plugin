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
  - *Live check on #10:* `omarchy plugin add`, `update` and `enable` only
    rescan, so after an update the new widget runs against the old singleton;
    on a shell that had loaded an older copy, the widget started no helper at
    all. Installing or updating needs `omarchy restart shell` (README), and
    `scripts/live-check.sh` restarts the shell on every install.
  - *Spike #28 (QML logic under test):* the QML engine imports an ECMAScript
    module (`import "lifecycle.mjs" as Lifecycle`), calls its exports and
    reads its constants; measured with Qt's `qml` runner (same engine as
    Quickshell) and live in the bar, and qmllint 6.11 and 6.12 accept it.
    So the lifecycle (refcount, debounced stop, restart backoff) is pure
    functions in `helper/lifecycle.mjs` that return actions, tested by
    `node --test`; the singleton only applies them. The #26 restart race is a
    regression test there.
  - Edits to the singleton's own QML do **not** hot-reload (old code kept
    running until `omarchy restart shell`). Keep the singleton a thin process
    owner and line dispatcher; logic lives in the helper and in ordinary
    components. Note in AGENTS.md's live-check step.
  - *Amended in #10:* the singleton lives in `helper/` and Panel.qml imports it with
    `import "helper"`. A qmldir in the plugin root, imported with `import "."`,
    made the widget extend itself (Panel.qml is also the base type's name).
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
  *Amended in #8:* the engine is `SdLedWasm` in `helper/sidepulse_omarchy.py`
  (the port of the spike, not a copy kept under `spikes/`); `spikes/wasm/compare.py`
  imports that class, so the reference check runs the shipped code. The resource
  is read with upstream's `load_packaged_wasm` (importlib.resources), and the
  probe checks wasmtime and the resource together. Error names are upstream's
  `ERROR_NAMES`. The frame rate is upstream's: `FRAME_RATE` 60 for Working and
  Ask, `IDLE_FRAME_RATE` 12 otherwise, read from its source by a drift test. A
  frame is sent only when its colours change. Measured in #8 (scratch HOME, 20 s
  samples, helper process CPU): Bar LEDs off, working 0.5 %; bar only, working
  1.05 %; bar and two devices, idle 1.0 % and working 1.75 % (133 frame events/s,
  27 KB/s on stdout); one step of 8 LEDs 0.02 ms (budget 16.7 ms).
  Install: upstream's README lists no wasmtime step and its METADATA has no
  extra for it, so the requirement is a plain pip install into the venv:
  `~/.local/share/sidepulse/venv/bin/python -m pip install wasmtime==49.0.0`
  (a dry run resolves it; nothing was installed). *This supersedes the earlier
  "survives `sidepulse update`" note above:* `update.py` runs the installer
  fetched from sidepulse.io, which is not in the installed code, so keeping
  wasmtime through an update is unverified. A lost wasmtime shows as the Bar
  LEDs' capability problem, not as a silent fallback.
- **D7 Bar LEDs placement:** thin row of LEDs drawn at the bar's inner edge
  (bottom edge for a top bar), centered, on every monitor; never overlaps
  windows.
  *Spike #2 (verified live):* the bar is one layer-shell `PanelWindow` per
  monitor with no clipping. A widget child reparented to
  `root.QsWindow.window.contentItem` (high `z`, anchored to the bottom edge)
  draws across the full bar width on every monitor (3440, 2560, 1920 px
  windows; screenshot-checked). No companion `panel` kind is needed.
  *Amended in #8:* that spike showed the reparenting works; it did not set the
  look. The band is centred at upstream's notch width (`WINDOW_WIDTH`, 220 px) for
  both LED counts, so the blend footprint matches the Mac (a full-width band of
  two LEDs would be one wash). It is drawn on the bar's inner edge: the bottom of
  a top bar, the top of a bottom bar. Upstream's 11 px glow is not drawn, since
  it would sit over the bar's own widgets; the 5 px band and its 1.15 px outer
  hotline are, with upstream's blend and tone map (`helper/barleds.mjs`). One band
  per bar window: the first copy of the widget that asks for it holds it
  (`HelperProcess.wantBand`). The position comes from the window, so which copy
  holds it does not show. A left or right bar draws nothing and the Settings row
  says so. The band sits below the bar's widgets (negative `z`, above the
  window's background colour), so marks the bar draws on the same edge, such as
  the open-popup line under a centre widget, stay visible on top of it.
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
  *Amended in #4:* implemented in `helper/sidepulse_omarchy.py`. The herdr
  pane is the one whose foreground processes include the session's process or
  an ancestor (two panes: ambiguous, no guess); tmux matches `pane_pid` the same
  way. The client is a herdr process without `server` in its argv, or a tmux
  client, and its window is found by ancestry. No client: a terminal attached
  to the multiplexer opens (`xdg-terminal-exec -- herdr`, `… -- tmux
  attach-session -t <session>`). A multiplexer that cannot be asked is named in
  the explanation, not hidden. herdr focus and tmux selection are not exercised
  live, since they move the user's panes (#4 checklist).
  *Amended in #4:* a stopped session resumes with upstream's
  `session_resume_command` run as `xdg-terminal-exec -- sh -c <command>` in the
  session cwd. Only the helper's own latest list is searched, so a session that
  has dropped out of it answers "no longer listed".
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
  *Amended in #5:* the value is the device's **Display** (GLOSSARY.md) and
  lives in the widget's shell.json entry as `deviceDisplays` (device id →
  `agent` | `off`). The widget sends
  the whole map to the helper on each helper hello and on each change
  (`set-displays`); the helper reconciles every connected device to it on each
  tick. Off writes upstream's `off` program once per helper, not on every tick.
  Battery is not offered until #11 builds it, and then only on a machine with
  a battery; a remembered value the helper does not offer (such as `battery`
  without one) means Agent. Off is the user's saved choice, not a takeover: it
  stays when the plugin is disabled or the helper exits (README says how to
  undo it). While a device is Off the helper checks it every tick and writes
  the off program again whenever the device shows anything else, because the
  service can write an agent program it planned before reading `custom`
  (`service.sync_outputs` reads settings, then writes). Brightness goes
  through upstream's `with_device_brightness` (0-255; the widget sends percent).
  *Amended in #11 (Battery):* `battery` is a third Display, offered only where the
  helper reads a battery (`power` event, `battery_offered`); a remembered `battery`
  without one means Agent. Battery is a takeover like Off: the helper sets `custom`
  and writes upstream's `program_for_battery` at the device's brightness whenever
  the device shows anything else, and hands the device back to `agent` when the
  Display changes or the helper exits (`release_takeovers`). Plugging or unplugging
  takes an Agent device the same way for upstream's preview window
  (`battery_show_on_power_change`, 7 s). An unreadable battery under Battery shows
  the device dark and reports `power` (D14), never the last value. Off stays Off on
  exit. A crash mid-takeover is repaired by the next reconcile, which re-derives
  upstream's value from the remembered Display.
- **D10 Laptop features: helper only, no upstream PR.** The helper reads
  `/sys/class/power_supply` and logind lid state, builds upstream's battery
  snapshot and calls upstream's `program_for_battery` / lid animation
  settings. It drives the LEDs itself only while battery/lid output is
  showing, with the device in Manual (`custom`) mode so the service doesn't
  fight it, then hands back. This overrides D4's "shape each gap as an
  upstream PR" for these features.
  *Amended in #11:* the lid state is logind's `LidClosed`, read with
  `busctl get-property` (the same logind the keep-awake lock uses); no other lid
  reader exists. Upstream's two lid rules (`lid_closed_led_state_should_hold`,
  `lid_close_animation_should_run`) are copied and AST-checked, since status_bar.py
  is PyObjC. The animations are upstream's lid settings (`settings.lid_animation`),
  played at the device's brightness for their duration plus upstream's 0.15 s. Closing
  plays only where upstream lets the machine sleep. While the closed lid may sleep the
  LEDs stay dark; upstream latches that state at the transition, the helper
  recomputes it each tick, so it cannot latch. Battery reader: the first present
  battery, as D11. Linux has no adapter wattage, so while charging `power_now` stands
  in for it (it sets upstream's charging pulse speed). Not checked on hardware: the
  laptop checklist for #9.
- **D11 Keep-awake scope (Mac parity):** helper holds
  `systemd-inhibit --what=sleep:handle-lid-switch` while upstream's predicate
  says hold (Working/Tool Running/Long Task, 300 s grace after
  Done/Ask/Error, battery safeguard < 20 % unplugged), per
  `sleep_prevention_policy` never/agents/always in upstream settings. Screen
  lock is **not** blocked. Never touches `omarchy toggle idle` (user's own
  global state). Laptop check: lid inhibitor only works where logind handles
  the lid switch — verify on the laptop.
  *Amended in #7:* the predicate is upstream's `KeepAwakeController.should_hold_for_mode`
  (the 300 s grace; the helper records `last_mode` as upstream's `update_requested`
  does) plus upstream's battery safeguard, and the policy comes from
  `sleep_prevention_policy` (`agents` is the default). The controller's caffeinate
  child is macOS-bound and is not used. `status_bar.py` needs PyObjC, so
  `awake_policy_should_hold`, `sleep_prevention_battery_safeguard` and
  `format_percent_value` are copied, and `tests/test_keep_awake.py` fails when their
  AST drifts from upstream. The lock is `systemd-inhibit --what=sleep:handle-lid-switch
  --mode=block` wrapping `cat`, whose stdin the helper closes on release, so the
  lock ends with the helper even when it is killed. A child that cannot start, or
  dies while wanted, is reported in the popup (D14) and not restarted until the
  request turns off. Battery: the first `/sys/class/power_supply` battery; an
  online adapter or a charging battery is plugged in. An unreadable battery is
  reported and counted as unknown (no safeguard), as upstream does. A failed read
  of `latest.json` holds as `BLOCKED_ERROR`, as upstream's refresh error path does.
  The lid state that D10 needs is read in #11, not here.
- **D12 Bar icon:** always shown. Muted when Idle; upstream state colours
  (Working #00AEEF, Done #22B14C, Ask #FF8A00) so the icon matches the
  physical LED; Ask also sets `active` (urgent highlight). Tooltip: state,
  active count, top session.
  *Amended in #5:* those are upstream's menu-bar legend colours, and the
  device programs use others (Done is solid cyan #00E5FF on the device,
  Working a cyan roll, Ask amber #FF3A00, Idle dim teal #006060), so the icon
  did not match the LED. With a device connected and showing Agent, the icon
  takes the colour its LEDs show now, read from the program on the device (a
  SidePulse Pro wins, D21); the popup's device rows do the same. Without one,
  the legend colours above apply.
  *Amended in #8:* the icon's LEDs animate with that device's live frames, as its
  popup row draws them (at full brightness, D21), while the device shows Agent.
  The icon follows the product it is drawn as: a SidePulse Pro if one is
  connected, else the first device. With that device Off, or no device, or no
  frames (the Bar LEDs' engine is unavailable), the colours above apply.
  *Amended in #11:* the icon follows its device's program, not its Display, so a
  device on Battery (and during a lid or preview takeover) draws its battery or
  animation frames as the row does. Only Off, or no device, falls back.
- **D13 Settings UI:** all inside the one popup — Sessions (top), Devices
  (per-device on/off, display mode, brightness), Keep awake (policy), and a
  collapsible Settings section (hooks per provider, Bar LEDs on/off, idle
  timeout, session retention, hook log viewer). Keyboard navigable.
  Engineering rule: SidePulse behaviour settings are stored in upstream's
  settings.json (`virtual_status_device_enabled`, `sleep_prevention_policy`,
  `agent_list.*`, `devices[]`) via the helper, so they mean the same thing as
  on the Mac; only plugin-presentation settings go in the shell.json entry.
  *Amended in #5:* upstream's `save_settings` rewrites settings.json from its
  own model, so keys it models survive a change and keys it does not model
  (a newer upstream's, hand-added ones) are dropped. The helper keeps that
  behaviour, as upstream's own CLI does; it only refuses to save over a file
  that is not a JSON object.
  *Amended in #6:* the Settings section (collapsed until opened) holds the hook
  switch per agent (`hooks` event: upstream's `detect_provider_configs`, the
  payload `agent-monitor doctor --json` prints; the switch runs the
  `agent-monitor install|uninstall <agent>` CLI, the only writer of agent
  configs; "on" means SidePulse's own hooks are installed: the helper's
  `installed` is upstream's log paths, which only SidePulse's hooks carry. An
  unrelated hook on a known event shows "Other hooks only" and is not counted),
  Bar LEDs (`virtual_status_device_enabled` through
  `with_virtual_status_device`; the helper does not register upstream's Notch
  device the way the Mac app does, since that would list it in `devices[]`),
  idle timeout and session retention (`with_agent_list_timing`; refused when
  negative or non-numeric, because upstream clamps those to 0 silently), the Ask
  notice (D20) and the last hook decisions (`event-status.jsonl`, read from the
  end). Problems (D14) sit above the section header, visible when it is
  collapsed. Presets are in upstream's units: idle 15 min to 4 h, retention 1 h
  to 7 days (`helper/settings.mjs`).
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
  *Amended in #7:* Keep awake is not a top-level section; its Policy row sits
  inside Settings (after "Notify when a session asks"), so the popup's top
  level holds only what changes while agents work: Setup, Sessions, Devices.
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
  session (D8). Beyond Mac parity: the Mac app sends no agent notifications. *Amended in #6:*
  the helper runs `notify-send --wait --action=default=Open session` for each
  session that enters Ask (once per entry, none for sessions already asking when
  it starts, none while the switch is off). Omarchy's notification server
  invokes a libnotify `default` action only while the sender is alive, so the
  notice's exit is the signal: a click prints `default` and opens the session
  through the D8 path; a dismissed notice opens nothing; a non-zero exit is
  reported in the popup (D14). The helper ends any notice still up when it exits.
- **D21 The look follows the product (plan review):** popup device rows draw
  a SidePulse Dot (2 LEDs) or SidePulse Pro (8 LEDs) lit live; the Bar LEDs
  match the connected product (2 or 8; 8 with none or both, since upstream
  prefers the Pro); the bar icon is a Dot or Pro glyph, neutral when none is
  connected.
  *Amended in #8:* the Bar LEDs play the program upstream's Notch plays for the
  agent State (`sync_virtual_status_device`: `program_for_agent_mode` with the
  user's animation for that State, at the Notch's brightness, 255 unless upstream
  says otherwise), not the file on the device. The Notch's own Display does not
  apply to them: they follow the State even when a device is Off. Each device row
  plays the program its device holds (`LEDS.LED`) through its own engine at the
  device's LED count, so an Off device shows dark and a Pro shows its own frames.
  The row plays it at full brightness: it undoes upstream's `apply_brightness`
  scaling by the device's brightness, since a screen pixel at a few percent is
  black where a real LED still shines; the brightness slider shows the brightness.
  Those frames flow only while a popup is open (the `set-popup` gate, below).
  *Amended in #11:* the row plays what its device holds, so a device on Battery
  (or in a lid or preview takeover) shows its battery program at full brightness
  too; the Bar LEDs stay on the State.
  The device's file is read on each tick, so a change of program reaches the row
  within a second; its animation frames run live.
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
├── Panel.qml            root Panel: BarIconButton + KeyboardPanel popup
├── BarLeds.qml          Bar LEDs, reparented into the bar window (D7, D21)
├── *.qml / Model.js     popup sections: Sessions, Devices, KeepAwake, Settings, HookLog
├── helper/
│   ├── qmldir           declares the singleton below (*amended in #10*: imported
│   │                    as "helper", not from the root: see the note under D5)
│   ├── HelperProcess.qml  pragma Singleton: owns the one helper Process, applies
│   │                    lifecycle.mjs's actions, parses NDJSON (D5)
│   ├── lifecycle.mjs    refcount, debounced stop, restart backoff as pure
│   │                    functions (*amended in #28*)
│   ├── sessions.mjs     the Sessions rows' age, provider label and cursor name (*added in #4*)
│   ├── settings.mjs     the Settings section's presets, hook and log wording (*added in #6*)
│   ├── barleds.mjs      the Bar LEDs' blend and tone map, upstream's constants (*added in #8*)
│   ├── popup.mjs        which widget copies have their popup open: set-popup's OR (*added in #8*)
│   └── sidepulse_omarchy.py   runs under SidePulse's venv python
├── tests/               pytest for the helper; node --test for *.mjs
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
  (outcome of a command), `error` (feature, message), `clear` (feature
  recovered; *amended in #10*),
  `devices` (*amended in #5*: id, name, path, product and kind Dot/Pro, LED
  count, remembered Display, brightness percent; sent on change),
  `hooks` (doctor --json payload), `power` (battery %, plugged, lid),
  `awake` (holding bool, reason), `frame` (RGB per Bar LED),
  `log` (recent hook decision rows). *Amended in #6:* `settings` (idle timeout,
  retention, Bar LEDs switch); `hooks` and `log` are re-sent on refresh and every
  five ticks; `result` also answers `hooks-install` and `hooks-uninstall`.
  Stdin commands: `refresh`, `start-service` (*amended in #10*: `setup` and
  `result` are new), `set-displays`, `set-brightness`, `set-policy`, `set-bar-leds`, `set-agent-list`, `hooks-install`,
  `hooks-uninstall`, `open-session`. `open-session` answers with
  the D8 lookup state (stopped → resumed, live → focused or located,
  unresolved → explanation).
  *Amended in #10:* while `latest.json` cannot be read, `state` is
  `unavailable` (with a `reason`, shown dimmed) instead of the last good
  state, and the `error` is cleared by a `clear` event once it reads again.
  Each new helper re-reports what still fails, so the widget drops its
  errors when the helper ends.
  *Amended in #10:* `state` and `setup` are re-sent on change, not on a fixed
  schedule. The helper re-runs the rules every second and re-checks setup every
  five seconds, so a stopped service or an unplugged device shows up without a
  restart.
  *Amended in #4:* `sessions` (the popup's Sessions rows, at most 10, upstream's
  recent-session rules, D13): provider, session_id, name, project (cwd
  basename), state, label, colour, message, `updated_ms` (the popup ages the
  rows itself, so the helper sends nothing per second), `resumable` (has a
  session id). Re-sent on change; empty while `latest.json` is unreadable.
  `open-session` (`provider`, `session_id`) answers with a `result`
  (`command: "open-session"`, `ok`, `action`, `message`) and no state change;
  `ok` closes the popup.
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
- **Amended in #6 (Settings):** further stdin commands: `set-bar-leds` (`enabled`),
  `set-agent-list` (`idle_timeout_seconds` and/or `recent_session_retention_seconds`),
  `hooks-install` and `hooks-uninstall` (`provider`), and `set-notify` (`enabled`,
  D20). Capabilities `hooks`, `hook_log`, `settings` and `notify` (notify-send on
  PATH) are probed at start like the others.
- **Linux-only additions (D10, D11):** `/sys/class/power_supply` reader that
  produces upstream's battery snapshot type; logind lid state; holds a
  `systemd-inhibit --what=sleep:handle-lid-switch --mode=block` child while
  the predicate says hold; LED handover for battery/lid display (device to
  `custom`, write program, restore afterwards).
  *Amended in #11:* the `power` event (`battery_present`, `percent`, `plugged`,
  `lid_closed`, `battery_offered`; re-sent on change), the `battery` and `lid`
  capabilities (`lid` also needs `busctl` on PATH), and the `battery` value of
  `set-displays`. Each tick reads the battery and the lid once; the devices are
  reconciled right after, and a preview or lid animation's end wakes the loop, so a
  takeover ends on time. Takeovers are released on exit.
  *Amended in #7:* the keep-awake part is in place: the `awake` event (`policy`,
  `holding`, `reason`; re-sent on change, the predicate re-runs every tick), the
  `set-policy` command (`policy`: never, agents, always; saved through upstream's
  `with_sleep_prevention_policy`), and the `keep_awake` capability (upstream's
  controller and settings names plus `systemd-inhibit` on PATH). The `power` event
  and the lid state wait for #11, since no popup row needs them yet; the battery
  feeds the safeguard, and its problems come as `error` with feature `power`.
  Tests put a fake `systemd-inhibit` on PATH (`tests/conftest.py`).
- **Capability probe (D14)** at start; each feature independently on/off with
  a reason. No silent fallbacks.
- Exits when stdin closes (shell gone) and releases the inhibitor and any
  LED handover.
- **LED handback (verified):** the service drops Manual (`custom`) devices
  from its skip-unchanged-writes cache (`service.py:216-218`), so when the helper sets
  a device back to `agent` the service repaints on its next 1 s poll. The
  helper never needs to repaint agent programs itself.
- **Amended in #8 (frames):** `frame` (`target`: `bar`, or `device` with
  `device`: id; `leds`: `[r, g, b]` per LED, 0-255, or `null` to draw nothing).
  Sent only when the colours of that target change. The loop runs on two
  deadlines, the one-second tick and the frame (60 fps while the bar's State is
  Working or Ask, 12 fps otherwise), so a frame costs one step and one line, not
  a full tick. A program that does not parse is reported as `error` with feature
  `frames` (PROBLEMS: "LED programs") and plays dark; `clear` follows when it is
  fixed. `set-bar-leds` answers with `settings` and the next frame.
  *Amended in #8 (popup gate):* device frames are sent only while a popup is
  open. The widget sends `set-popup` (`open`: true or false) with the OR of its
  copies' popups (`helper/popup.mjs`): on each change, and again after each hello,
  since a fresh helper starts closed. While closed no device engine steps and no
  device frame is sent; the bar frames are unaffected. On open, the device frames
  are sent at the next pump, not suppressed as unchanged.
  *Amended in #8 (icon):* `frame` with `target: icon` carries the bar icon's
  device's frames (D12). That device steps even while every popup is closed;
  every other device steps only while one is open, and each steps once per frame
  whoever reads it. `null` clears an icon that was lit; with no device ever, no
  icon frame is sent.

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
  3. **Settings** (collapsible) — hooks per provider (status from doctor,
     toggle install/uninstall), Bar LEDs on/off, idle timeout, retention,
     hook log viewer (last N decision rows), "Notify when a session asks"
     (D20), Keep awake (Never / When Agents Work / Always, with the holding
     state and reason; D11), capability problems.
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
