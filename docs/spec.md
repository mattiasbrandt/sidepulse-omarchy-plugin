# SidePulse Omarchy plugin — Spec & Plan

Status: the plugin is built. Decisions D1–D25 below describe its current behaviour.

## Goal

Give SidePulse devices (Dot, Pro) on Omarchy the same day-to-day
functionality the macOS SidePulse menu-bar app has, as a native Omarchy shell
plugin. Developed on a desktop (`protoarchy`, Dot) and a laptop.

## Context (development machine, as of 2026-10-10)

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
- Live state: the service's status socket, `program-status.sock` in
  `~/.local/state/sidepulse/agent-monitor/`, answers `get_state` (D4). At
  6d55225 the service wrote `latest.json` there instead. At 20ca7fd nothing starts
  `LiveAgentMonitor`, so that file is no longer written. Hook decision log:
  `event-status.jsonl`, in the same folder.
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
| Settings: hook install/uninstall per provider | Toggles via `sidepulse setup` | In — D13 |
| Settings: animations per state | — | Out (hand-edit JSON) — D2 |
| Hook decision log export (CSV/HTML) | In-popup log viewer | In, viewer not export — D2 |
| Show battery 7s on plug/unplug | Helper | In — D10 |
| SidePulse Notch virtual LED overlay | Bar LEDs along the bar edge | In — D6, D7, D21 |
| Keep awake with lid closed | systemd-inhibit sleep + lid | In — D11 |
| Eject prevention | — | Out (Dot doesn't need it; Pro on Linux unverified) |
| Setup window / run at login | Upstream systemd unit + plugin enable | Out (covered) |
| iPhone linking | `sidepulse link` CLI | Out (CLI covers it) |

## Decisions log

_One entry per decision. Each entry states the current decision and why._

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
  - All upstream access goes through one swappable adapter. Today it reads the
    service's state (`program_presentation.DaemonMonitor`), uses upstream's
    rules and hook-config detection (`detect_provider_configs`), and calls the
    CLI. The Rust versioned socket protocol (upstream PR #52) goes behind the
    same adapter when it ships.
  - State source: the helper calls `DaemonMonitor().snapshot(include_stale=False)`
    on each one-second tick. That is the call upstream's own menu-bar app makes
    (`status_bar.py` `build_monitor`). The service holds the agents' status in
    memory, answers `get_state` on its status socket, and applies upstream's
    aggregate rules. The helper does not run those rules itself, and it does not
    watch a directory. A stopped service gives State "unavailable" with the
    reason, never the last State. The service forgets a finished session after
    20 minutes (`ProgramMonitor`) and does not read the idle timeout, so both
    settings reach only as far as the service keeps them. The popup shows what
    upstream's own menu shows.
  - The four-state mapping and its colours are copied into the helper: `state_for_mode`
    and the Ask/Working/Done/Idle legend, from `status_bar.py`. That module needs
    PyObjC and cannot be imported on Linux. `tests/test_state.py` reads the
    upstream source and fails when the mapping, the state labels or the colours
    drift. Everything else calls upstream's rules.
  - Code copied or ported from upstream keeps upstream's MIT notice
    (`Copyright (c) 2026 Peter Kuhar`): in `LICENSE`, in `THIRD_PARTY_NOTICES.md`,
    which lists each piece, and in the file holding it.
  - Linux gaps (battery reader, keep-awake/lid, Bar LED frames) live in a small
    Python helper in the plugin repo. The helper imports upstream's own code from
    the SidePulse venv: aggregate rules, settings load/save, keep-awake predicate,
    `sdled.wasm`. Each gap is shaped as a future upstream PR, except the laptop
    features, which stay in the helper (D10).
- **D5 Helper lifecycle:** the widget spawns the helper as a child process and
  reads NDJSON from its stdout. No extra unit or install step. Shell restart
  restarts the helper and releases any keep-awake inhibitor (safe failure).
  Engineering rule (not a user decision): exactly one helper per shell even
  with one widget copy per monitor.
  - A plugin-local `pragma Singleton` in `helper/`, imported with
    `import "helper"`, is shared by every widget copy and owns one `Process`.
    Measured live on 3 monitors. A `qmldir` in the plugin root, imported with
    `import "."`, made the widget extend itself (Panel.qml is also the base
    type's name), so the singleton does not live there.
  - The singleton **survives plugin hot-reload and plugin disable** (lives as
    long as the shell). Its child process outlives `omarchy plugin disable`.
    So the singleton refcounts widget copies, stops the helper when the count
    stays at 0 (debounced: a reload drops it to 0 and back within milliseconds),
    and restarts it when a copy registers again.
  - `omarchy plugin add`, `update` and `enable` only rescan. After an update the
    new widget runs against the old singleton, and on a shell that loaded an
    older copy the widget starts no helper at all. Installing or updating needs
    `omarchy restart shell` (README). `scripts/live-check.sh` restarts the shell
    on every install.
  - The lifecycle (refcount, debounced stop, restart backoff) is pure functions
    in `helper/lifecycle.mjs` that return actions, tested by `node --test`. The
    singleton only applies them. The QML engine imports the ECMAScript module
    (`import "lifecycle.mjs" as Lifecycle`) and calls its exports. Measured with
    Qt's `qml` runner (the engine Quickshell uses) and live in the bar; qmllint
    6.11 and 6.12 accept it. A copy leaving during a restart backoff is a
    regression test there.
  - Edits to the singleton's own QML do **not** hot-reload: the old code keeps
    running until `omarchy restart shell`. Keep the singleton a thin process
    owner and line dispatcher; logic lives in the helper and in ordinary
    components.
  - `bar` is still null in a widget's `Component.onCompleted` (injected after).
- **D6 Bar LEDs engine:** the helper runs upstream's `sdled.wasm` through the
  `wasmtime` Python package (49.0.0, `py3-none-manylinux1_x86_64` wheel; a dry
  run resolves it for the venv's Python 3.14).
  - The engine is `SdLedWasm` in `helper/sidepulse_omarchy.py`: a line-for-line
    port of upstream's `led_wasm.py` call sequence. The resource is read with
    upstream's `load_packaged_wasm` (importlib.resources). The probe checks
    wasmtime and the resource together. Error names are upstream's `ERROR_NAMES`.
  - Parity: the port and an independent Deno driver gave byte-identical frames
    in 117/117 cases (off, all agent modes, 26 built-in animations, battery
    programs, 8 invalid programs; 2 and 8 LEDs; t = 0…2500 ms). The Deno driver
    is no longer kept. Both drive the same wasm, so this proves the port, not
    engine against device. Upstream's test vectors (`tests/test_sidepulse.py`)
    are the engine reference, and they match. The reference cases are
    `tests/bar_leds_reference.py`, which imports `SdLedWasm`, so they run the
    shipped code. `tests/test_bar_leds.py` checks them. The spike files are not
    kept in the repo.
  - Cost: 0.017 ms per frame (60 fps budget 16.7 ms), 4 ms to instantiate.
  - The frame rate is upstream's: `FRAME_RATE` 60 for Working and Ask,
    `IDLE_FRAME_RATE` 12 otherwise, read from its source by a drift test. A
    frame is sent only when its colours change.
  - Measured in a scratch HOME, 20 s samples, helper process CPU: Bar LEDs off,
    working 0.5 %; bar only, working 1.05 %; bar and two devices, idle 1.0 % and
    working 1.75 % (133 frame events/s, 27 KB/s on stdout); one step of 8 LEDs
    0.02 ms (budget 16.7 ms).
  - Install: `~/.local/share/sidepulse/venv/bin/python -m pip install wasmtime==49.0.0`.
    Upstream's README lists no wasmtime step and its METADATA has no extra for
    it, so the requirement is a plain pip install into the venv. Keeping wasmtime
    through `sidepulse update` is unverified: `update.py` runs the installer
    fetched from sidepulse.io, which is not in the installed code.
  - If wasmtime is missing, the helper reports a capability error and the
    Bar LEDs are unavailable. They do not fall back silently.
- **D7 Bar LEDs placement:** thin row of LEDs drawn at the bar's inner edge
  (bottom edge for a top bar), centered, on every monitor; never overlaps
  windows.
  - The bar is one layer-shell `PanelWindow` per monitor, with no clipping. A
    widget child reparented to `root.QsWindow.window.contentItem` (high `z`,
    anchored to the bottom edge) draws across the full bar width on every
    monitor (3440, 2560 and 1920 px windows; screenshot-checked). No companion
    `panel` kind is needed.
  - The band is centred at upstream's notch width (`WINDOW_WIDTH`, 220 px) for
    both LED counts. The blend footprint then matches the Mac: a full-width band
    of two LEDs would be one wash. It is drawn on the bar's inner edge: the bottom
    of a top bar, the top of a bottom bar. Upstream's 11 px glow is not drawn,
    since it would sit over the bar's own widgets. The 5 px band and its 1.15 px
    outer hotline are drawn, with upstream's blend and tone map
    (`helper/barleds.mjs`).
  - One band per bar window: the first copy of the widget that asks for it holds
    it (`HelperProcess.wantBand`). The position comes from the window, so which
    copy holds it does not show.
  - The band sits below the bar's widgets (negative `z`, above the window's
    background colour). Marks the bar draws on the same edge, such as the
    open-popup line under a centre widget, stay visible on top of it.
  - A left or right bar gets the band too, running down its inner edge (the right
    of a left bar, the left of a right bar), centred on the bar's length. Its
    canvas is turned a quarter so the LEDs run top to bottom and the hotline faces
    the desktop (`Band.bandLayout`, node-tested per edge). The Settings row does
    not limit Bar LEDs to top or bottom bars. On a vertical bar the bar icon stacks
    the device's LEDs down its glyph; the popup's hero keeps the horizontal shape.
- **D8 Session click:** focus the terminal window of the process that
  provably runs the session; resume (user's default terminal via
  `xdg-terminal-exec`, in the session cwd, upstream's resume command) only when
  no process can own it; otherwise explain where it may be running. No
  per-provider "Open with..." chooser and no VS Code opener in v1.
  - Every lookup ends in one of three states. **stopped**: no process can own
    the session, so it resumes. **live**: a process provably owns it, so its
    window is focused, or the popup says where it runs. **unresolved**: a process
    might own it but nothing proves it, so the popup shows the candidates. It
    never resumes (that could duplicate a live session) and never focuses (that
    could be the wrong session).
  - Proof, strongest first. Claude writes `~/.claude/sessions/<pid>.json`
    (`sessionId`, `procStart` = `/proc/<pid>/stat` starttime, a pid-reuse guard;
    honours `CLAUDE_CONFIG_DIR`). Grok writes `~/.grok/active_sessions.json`
    (`session_id`, `pid`). For any provider, the session id as a whole
    command-line argument (`codex resume <id>`, `junie --session-id=<id>`).
    Upstream hook lines carry no pid.
  - Without proof, a process of the provider's binary in the session cwd makes
    the session **unresolved**; none makes it **stopped**. Only processes holding
    a terminal count: `codex app-server` daemons have none. A plain `codex` with
    no session argument leaves every Codex session in its cwd unresolved until one
    is proven. That is correct, but clicking an unproven Codex, Cursor or Junie
    session in a busy cwd explains instead of acting.
  - Window: walk the ancestors to a pid that owns Hyprland clients. Ghostty is
    single-instance, so all its windows share one pid; narrow by title. Claude
    titles its window with the transcript's `ai-title` (verified: it picked the
    right one of two Ghostty windows). Focus by address.
  - Hyprland 0.56 is Lua: `hyprctl dispatch 'hl.dsp.focus({ window =
    "address:0x…" })'`. A bad address still exits 0 and prints
    `hl.focus: window not found`, so the output is parsed. Focusing a window on
    another workspace is not yet tested live.
  - Live but no window (the agent runs inside herdr or tmux): jump into the
    multiplexer. A small adapter per multiplexer finds the pane holding the
    process, focuses it, then focuses the window running a client of that
    multiplexer, or opens a terminal attached to it when none runs. v1 has two.
    **herdr**: verified read-only; `herdr pane process-info --pane <id>` lists the
    pane's foreground pids, `herdr agent focus <pane>` focuses it, and the attached
    client runs in a Ghostty window. **tmux**: `list-panes -a -F '#{pane_pid} …'`,
    `select-window`/`select-pane`, `switch-client`; not exercised live. Any other
    ancestor, or an ambiguous title match, shows "running in <ancestor> (pid)", with
    no guess.
  - The herdr pane is the one whose foreground processes include the session's
    process or an ancestor (two panes: ambiguous, no guess). tmux matches
    `pane_pid` the same way. The client is a herdr process without `server` in its
    argv, or a tmux client, and its window is found by ancestry. With no client, a
    terminal attached to the multiplexer opens (`xdg-terminal-exec -- herdr`,
    `… -- tmux attach-session -t <session>`). A multiplexer that cannot be asked is
    named in the explanation, not hidden. herdr focus and tmux selection are not
    exercised live, since they move the user's panes.
  - A stopped session resumes with upstream's `session_resume_command`, run as
    `xdg-terminal-exec -- sh -c <fixed script>` in the session cwd. The command
    travels in the environment (`SIDEPULSE_RESUME`), not in argv. The script runs
    the variable, unsets it before the agent starts, and fails with a message held
    on screen when the variable did not arrive. A terminal keeps its argv for the
    window's life and any local account can read it, while `/proc/<pid>/environ`
    is the owner's alone. For the same reason tmux attaches by session id (`$N`),
    not by name. Only the helper's current sessions list is searched, so a session
    that has dropped out of it answers "no longer listed".
  - The lookup is tested by `tests/test_session_lookup.py`.
  - The process lookup takes only the user's own processes from `/proc`: another
    account's `claude --resume <id>` is never this session. A session id becomes
    part of a file name (the Claude transcript title) only when it is a plain id,
    never a path or a dots-only name.
  - The list leaves out a finished (Done) session whose folder no longer exists,
    such as a deleted worktree: it cannot be resumed, and Codex review runs in
    removed worker worktrees would crowd the ten rows. It is dropped before the
    limit (`listed_sessions`, around upstream's `recent_statuses`), so it takes no
    row. Running and Ask sessions always show, and so does a session with no
    folder recorded.
- **D9 LED on/off:** per device. A device's **Display** (GLOSSARY.md) is
  Agent, Battery or Off. Agent leaves the device to upstream's service. Off and
  Battery take the device over (below).
  - Upstream stores only Agent or Manual (`led_display`) per device on Linux, so
    the helper derives that value from the Display: Agent → `agent`; Battery and
    Off → `custom`. The helper writes it through upstream's `load_settings` and
    `save_settings`, never by hand-editing JSON, which would drop or corrupt keys.
  - The Display is remembered in the widget's `shell.json` entry as
    `deviceDisplays` (device id → `agent`, `off` or `battery`). That is the one
    per-device value kept outside upstream's settings, the exception to "settings
    through upstream". The widget sends the whole map to the helper on each helper
    hello and on each change (`set-displays`). The helper reconciles every
    connected device to it on each tick, at start, and after every takeover. That
    also repairs a device left Manual by a crash mid-takeover.
  - Upstream's service skips a Manual device, so the helper's own `off` program is
    the one the device shows. Off writes upstream's `off` program once per helper, not
    on every tick. While
    a device is Off, the helper checks it every tick and writes the off program
    again whenever the device shows anything else. The service can write an agent
    program it planned before it read `custom`; `service.sync_outputs` reads the
    settings, then writes (researched at 6d55225).
  - Off is the user's saved choice, not a takeover. It stays when the plugin is
    disabled or the helper exits (README says how to undo it).
  - Battery is offered only where the helper reads a battery (`power` event,
    `battery_offered`). A remembered `battery` without one means Agent. Battery is
    a takeover like Off: the helper sets `custom` and writes upstream's
    `program_for_battery` at the device's brightness whenever the device shows
    anything else. It hands the device back to `agent` when the Display changes or
    the helper exits (`release_takeovers`). Off stays Off on exit. Plugging or
    unplugging takes an Agent device the same way for upstream's preview window
    (`battery_show_on_power_change`, 7 s). An unreadable battery under Battery
    shows the device dark and reports `power` (D14), never the last value. A crash
    mid-takeover is repaired by the next reconcile, which re-derives upstream's
    value from the remembered Display.
  - Brightness goes through upstream's `with_device_brightness` (0-255; the widget
    sends percent).
  - Before the helper reads or writes a device's LED file, it checks that the file
    is a regular file no longer than upstream's `MAX_LED_BYTES`, or that it is
    absent. Upstream's discovery takes any auto-mounted volume named like a
    SidePulse or holding an LED file, and its writer follows symbolic links. So a
    stick someone else made could turn an LED write into a write elsewhere, or hang
    the helper on a FIFO. A device that fails the check is left out and named in the
    popup. The read itself never follows a link, never blocks, and stops one byte
    past the limit. This is a safety check on input, not a re-derived upstream rule
    (D4). The folder holding the file must also be the user's own and writable by no
    one else. Upstream's writer reopens the file by name, and another account could
    swap in a link between the check and the write. An error during the check names
    the device and skips it.
  - When the LED file cannot be opened or created for writing (a read-only volume),
    upstream's writer falls back to the device's read channel. It reads `STATUS.TXT`
    with a plain `open()` and maps `setup.html` (Pro) or sends USB control transfers
    (Dot). Those two files must be regular files or absent, never a link, a FIFO or a
    device. Otherwise the device is left out like a bad LED file. Upstream's service
    (`service.py`) discovers and writes devices on its own, without these checks. So a
    rejected device's problem line also says the service may still write it, and asks
    to eject the volume. The helper cannot constrain another process.
  - The service keys a device's settings by upstream's `device_id`
    (`sidepulse:<serial>` from `STATUS.TXT`, else the mount root) and migrates
    path-keyed entries to it. The helper takes the same id, and reads a Display
    remembered under the mount path as the fallback. Upstream's `discover_devices`
    reads each volume's `STATUS.TXT` with a plain `open()` before anything can check
    it. So the helper calls its two steps apart (`discover_mounted_devices`, then
    `describe_mounted_device`), with the LED and control-file check between them. A
    FIFO there does not block the helper. This also skips upstream's USB scan: a Dot
    reached only over USB has no LED file to read back, so it stays with the service.
    The product is the model upstream read from `STATUS.TXT`, else its LED count rule.
  - Before the helper writes a Display or brightness under a device's id, it moves
    an entry kept under the mount path to that id (`adopt_device_key`), as upstream's
    migration does. Upstream keeps an entry already under the id, so writing one
    first would replace the saved brightness with upstream's default 255.
  - The volume's folder must be the user's own. Every folder above it must pass
    `trusted_folder_problem`: owned by the user or root, writable by no other account,
    and a sticky shared folder only above the user's own folder. Renaming a folder on
    the way would swap the volume between the check and upstream's write by name.
    Upstream's device-lock folder is kept 700 by the privacy pass, since an account
    able to open a lock file could hold `flock` and stall every write. Discovery runs
    under a 3 s deadline on a worker thread (`Deadline`). It stats every mount-root
    entry, and an automount whose server is gone would block the helper's serial
    loop. While one call is still running, the next call fails at once.
  - `settings.json` is saved without group and other access (it names the devices
    and their mount paths).
- **D10 Laptop features: helper only.** The helper reads
  `/sys/class/power_supply` and logind lid state, builds upstream's battery
  snapshot, and calls upstream's `program_for_battery` and lid animation
  settings. It drives the LEDs itself only while battery or lid output is showing,
  with the device in Manual (`custom`) mode so the service doesn't fight it, then
  hands back. These features stay in the helper, with no upstream PR (see Out of
  scope). They are the exception to D4's "one upstream PR per gap".
  - Lid: logind's `LidClosed`, read with `busctl get-property` (the same logind the
    keep-awake lock uses). No other lid reader exists. Upstream's two lid rules
    (`lid_closed_led_state_should_hold`, `lid_close_animation_should_run`) are
    upstream's own functions in `daemon_runtime.py` (20ca7fd), and the helper calls
    them rather than copying them. Upstream's daemon runs its battery, keep-awake and
    lid code only on macOS (`is_mac`). On Linux the helper does all three, so nothing
    runs twice.
  - Animations: upstream's lid settings (`settings.lid_animation`), played at the
    device's brightness for their duration plus upstream's 0.15 s. Closing plays only
    where upstream lets the machine sleep. While the closed lid may sleep, the LEDs
    stay dark. Upstream latches that state at the transition. The helper recomputes it
    each tick, so it cannot latch.
  - Battery reader: the first present battery, as D11. Linux has no adapter wattage,
    so while charging, `power_now` stands in for it. It sets upstream's charging pulse
    speed.
  - An online charger wins over a battery `status` that still says `Discharging`.
    On a ThinkPad, `status` turns `Charging` about 5 s after the adapter goes online,
    so the 7 s preview would show the unplugged look for most of its time.
    `Not charging` (a charge limit) and `Full` keep their own look. Until
    `power_now` reports the intake (0 W), the pulse is upstream's 180 ms one, as
    `charging_pulse_length_ms(0)` gives. It changes once the wattage arrives.
  - The battery display, the plug preview and the lid animations were tested on a
    ThinkPad (README, Laptop section).
- **D11 Keep-awake scope (Mac parity):** the helper holds
  `systemd-inhibit --what=sleep:handle-lid-switch` while upstream's predicate says
  hold: Working, Tool Running or Long Task; a 300 s grace after Done, Ask or Error;
  and the battery safeguard below 20 % unplugged. Which states hold is set by
  `sleep_prevention_policy` in upstream settings: `never`, `agents` (the default) or
  `always`. Screen lock is **not** blocked. Keep awake never touches
  `omarchy toggle idle` (the user's own global state). Laptop check: the lid
  inhibitor only works where logind handles the lid switch. Verify on the laptop.
  - Predicate: upstream's `KeepAwakeController.should_hold_for_mode` gives the
    300 s grace, and the helper records `last_mode` as upstream's `update_requested`
    does. Upstream's `awake_policy_should_hold` and
    `sleep_prevention_battery_safeguard` come from `daemon_runtime.py` (20ca7fd) and
    are called, not copied. The controller's caffeinate child is macOS-bound and is
    not used. The helper formats the percent as upstream's `format_percent_value`
    does, since `status_bar.py` needs PyObjC and cannot be imported.
  - Policy: `set-policy` (never, agents, always), saved through upstream's
    `with_sleep_prevention_policy`.
  - Lock: `systemd-inhibit --what=sleep:handle-lid-switch --mode=block` wraps `cat`,
    whose stdin the helper closes on release. So the lock ends with the helper, even
    when the helper is killed. A child that cannot start, or dies while wanted, is
    reported in the popup (D14) and not restarted until the request turns off.
  - Battery: the first `/sys/class/power_supply` battery. An online adapter or a
    charging battery counts as plugged in. An unreadable battery is reported and
    counted as unknown (no safeguard), as upstream does.
    - Tests: `tests/test_keep_awake.py`.
  - A State that cannot be read holds sleep as `BLOCKED_ERROR`, as upstream's
    refresh error path does. The lid state belongs to D10.
  - Desktop notices (D11 safeguard): when the safeguard changes whether the lock is
    held, the helper sends a desktop notice (`notify-send`, no action, no wait).
    `Keep awake paused by the battery safeguard: <N> %, unplugged` is sent when the
    safeguard blocks a lock the policy wants. `Keep awake resumed: plugged in` is sent
    when Keep awake holds again. "Holds again" means the lock is actually held, not
    only requested. A `systemd-inhibit` that cannot start sends no "resumed": the Keep
    awake problem line says why. A request that ends before the lock is held drops it.
    The trigger is the block itself (`safeguard_blocks`: the policy wants a hold and
    the safeguard is active), so a falling percent does not re-notify. Under policy
    Never nothing is evaluated. With no agent working under When Agents Work, nothing
    is blocked and nothing is sent. The first read is silent (`safeguard_notice` takes
    `None` as "not read yet"), so a helper that starts on a low battery does not
    announce a lock it never held. The notices ignore the Ask switch (`notifyOnAsk`).
    A missing `notify-send` is the Notifications problem line (the capability probe),
    and a failed child is reported there too. The notice reads the percent with its
    decimal as `format_percent_value` does, and writes the unit with a space. Tests:
    `tests/test_safeguard_notice.py`.
- **D12 Bar icon:** always shown. Muted when Idle. Without a device, the state
  colours are upstream's menu-bar legend colours: Working #00AEEF, Done #22B14C,
  Ask #FF8A00. Ask also sets `active` (urgent highlight). Tooltip: state, active
  count, top session.
  - With a device connected and showing Agent, the icon takes the colour its LEDs
    show now, read from the program on the device (a SidePulse Pro wins, D21). The
    popup's device rows do the same. The device programs use other colours: Done
    is solid cyan #00E5FF on the device, Working a cyan roll, Ask amber #FF3A00,
    Idle dim teal #006060. That is why, with a device showing Agent, the icon
    reads the device's colour rather than the legend: it then matches the physical LED.
  - The icon's LEDs animate with that device's live frames, as its popup row draws
    them (at full brightness, D21), while the device shows Agent. The icon follows
    the product it is drawn as: a SidePulse Pro if one is connected, else the first
    device.
  - The icon follows its device's program, not its Display. A device on Battery (and
    during a lid or preview takeover) draws its battery or animation frames, as its
    row does. The legend colours apply when no device is connected, when the device
    is Off, or when there are no frames (the Bar LEDs' engine is unavailable).
- **D13 Settings UI:** all inside the one popup. Sessions come first, then Devices
  (per-device on/off, display mode, brightness), then a collapsible Settings section
  (hooks per provider, Bar LEDs on/off, idle timeout, session retention, hook log
  viewer, the Ask notice, Keep awake policy). Keyboard navigable.
  Engineering rule: SidePulse behaviour settings are stored in upstream's
  `settings.json` (`virtual_status_device_enabled`, `sleep_prevention_policy`,
  `agent_list.*`, `devices[]`) via the helper, so they mean the same thing as on
  the Mac. Only plugin-presentation settings go in the `shell.json` entry, plus the
  Display map (D9).
  - `save_settings` rewrites `settings.json` from upstream's own model. Keys upstream
    models survive a change. Keys it does not model (a newer upstream's, or
    hand-added ones) are dropped. The helper keeps that behaviour, as upstream's own
    CLI does. It only refuses to save over a file that is not a JSON object.
  - The Settings section is collapsed until opened. It holds:
    - Hooks per agent. The status is upstream's `detect_provider_configs`, the
      fields `sidepulse doctor --json` prints. "On" means SidePulse's own hooks are
      installed. The helper's `installed` is upstream's log paths, which only
      SidePulse's hooks carry. An unrelated hook on a known event shows "Other hooks
      only" and is not counted. The switch runs `sidepulse setup <agent>` (on) or
      `sidepulse setup <agent> --uninstall` (off) in the SidePulse venv, the only
      writer of agent configs. Turning hooks on also installs and restarts upstream's
      service, which the hooks report to. Upstream's Codex trust step runs in the home
      folder. Hooks installed outside the plugin are named, not removed.
    - Bar LEDs (`virtual_status_device_enabled` through `with_virtual_status_device`).
      The helper does not register upstream's Notch device the way the Mac app does,
      since that would list it in `devices[]`.
    - Idle timeout and session retention (`with_agent_list_timing`). Values are
      refused when negative or non-numeric, because upstream clamps those to 0
      silently. Presets are in upstream's units: idle 15 min to 4 h, retention 1 h to
      7 days (`helper/settings.mjs`).
    - The Ask notice (D20).
    - Keep awake, with its policy row after "Notify when a session asks" (D11).
    - The last hook decisions (`event-status.jsonl`, read from the end).
    - Problems (D14) sit above the section header, visible when it is collapsed.
  - Hook log privacy: upstream's hook installer and hook create the agent-monitor
    state folder and the agents' logs, which hold complete prompts, with the process
    umask. After a hook install from the popup, and whenever the helper starts, the
    helper takes group and other access off each state folder upstream may resolve
    (`candidate_state_dirs`). So every file in it, those created later included, is
    the user's alone. A hook log configured outside it loses group and other access,
    and its folder is left alone. A link, a folder or file owned by another account,
    or an error is reported, not followed. The files are upstream's; the plugin only
    closes them.
  - While an agent's SidePulse hooks are installed, the helper creates a missing state
    folder with mode 700. Upstream's hook writes its audit log, `event-status.jsonl`,
    there wherever the agent's log goes. A SidePulse log kept elsewhere is created
    empty at 600. This happens at start, after a hook install, and every 5 s while the
    helper runs. Only SidePulse's own hooks count. Upstream lists the `--log` and `>>`
    targets of every hook, so a log path counts only on a config line carrying
    upstream's markers (`hook_entry.py`, `agent-monitor hook-log`,
    `sidepulse.cursor_hook`).
  - The plugin runs SidePulse's CLI (hook install and uninstall, service start) with
    umask 077. So the backups of agent configs it makes, which may hold API keys, are
    private. Older
    backups (`<config>.bak.<UTC stamp>`, and Grok's backup folder) lose group and
    other access.
  - A state folder whose path makes upstream move its status socket
    (`program_transport.status_socket_path`) to a fixed name in `/tmp` is named as a
    problem. Problems show once on their own line ("Hook log
    privacy") and clear when solved. While no helper runs (no Omarchy shell), upstream's
    own behaviour applies. That is upstream's to change.
  - Hook installs fail closed. Before upstream's installer runs, `hook_install_problems`
    checks each state folder and every folder above it. It checks the entries already
    in each folder, because a link or another account's file would be written through,
    and a 700 folder does not undo what was planted while it was open. It also checks
    the agent's config folder, the folder of a SidePulse hook log kept outside, and the
    status socket's place. Any problem refuses the install and gives the reason. After
    the install and the privacy pass, the same check runs again over what the installer
    created; a problem then uninstalls the hooks again, and the result says so and names
    what `setup` leaves (its service stays installed and restarted; Codex keeps
    `[features] hooks = true`). A linked config is checked where it points, too. The
    privacy pass also names those folder and entry problems on each sweep.
  - Reads follow the same rule. The agents' state comes only from a socket the user
    owns, in a trusted folder: another account could otherwise answer with sessions
    and folders that a click would resume. Upstream's settings come only from the
    user's own regular file in a trusted folder. Otherwise the helper uses upstream's
    defaults, as upstream's own `load_settings` does for an unreadable file, and saves
    are refused. The hook log is checked the same way. Files the helper parses itself
    are opened non-blocking and must be the user's own regular files (`open_own`: 16 MiB
    for configs and records, 1 MiB lines for transcripts). The service check and the
    sessions' folder check run under deadlines (3 s and 2 s).
  - The daemon read also runs under a deadline (0.25 s). Upstream's client waits 0.5 s
    per receive and tries up to two sockets, so a service that hangs would otherwise
    hold the loop on each tick. A reply whose sessions lack text fields makes the State
    unavailable, rather than ending the helper. A hook-log time out of the local clock's
    range shows as written.
  - Both path chains are checked: the folder holding a linked `settings.json` or agent
    config, and the folder of what it names.
  - A rolled-back install says what `setup` left. Its service stays installed and
    restarted, and Codex keeps `[features] hooks = true`.
- **D14 Upstream compatibility:** feature-detect, fail loudly. The helper
  probes imports, CLI subcommands and JSON keys at start. Each missing capability
  disables only its feature, with a visible reason in the popup. README records
  the upstream commit tested against: 20ca7fd. Upstream's installer tracks `main`,
  and the marketplace review audits against it. So a pinned commit that the
  installer cannot give new users is not a support target.
  - Every probe that guards a 20ca7fd contract names something 6d55225 lacks
    (`program_presentation.DaemonMonitor`, `program_transport.status_socket_path`,
    `device_writer.discover_mounted_devices`). So on an older install, hooks and
    devices fail with a reason. They do not read a file nothing updates, or run a
    removed command. At 6d55225 every probe passed while state, hooks and device
    keys were broken. A probe must check the contract a feature relies on, not only
    that a module imports.
  - A Rust-protocol adapter goes behind the same interface when the Rust build ships
    (upstream PR #52). README says a future
    build with a Rust tray ships its own KSNI tray icon, and that only one icon should
    run: disable that tray on Omarchy, or hide this widget.
- **D15 Verification scope:** pytest for the helper against upstream's own
  code (state mapping, settings round-trip, keep-awake predicate, battery
  parsing from fixture `/sys` trees, wasm frames vs upstream Python);
  `omarchy plugin validate` + `qmllint` for QML; manual pass in the live
  shell on protoarchy with the Dot; laptop checklist for battery/lid. No QML
  test harness.
- **D16 Tracking:** public repo `mattiasbrandt/sidepulse-omarchy-plugin`, local
  `~/Documents/GitHub/sidepulse-omarchy-plugin`. This spec is `docs/spec.md`. One
  GitHub issue per plan slice plus one verification ticket, all under one parent
  issue, with native blocked-by edges.
- **D17 Versioning and releases:** semantic versioning from Conventional
  Commits. `main` takes squash merges only, and the PR title is the commit
  (checked in CI). release-please keeps a release PR with the version, generated
  `CHANGELOG.md` and `manifest.json` `version`; merging it tags `vX.Y.Z` and
  publishes the GitHub Release. Before 1.0.0, `feat` and breaking changes bump
  the minor, and `fix` bumps the patch. CI on every PR: Omarchy's own plugin
  validator at the pinned Omarchy tag, Python tests, qmllint against the Omarchy
  shell in an Arch container, and actionlint. SHA-pinned actions are moved by
  Dependabot. The type → version table, the SHA pins with Dependabot, and the
  checksum-verified actionlint step are reused from protoR2. Rules:
  `CONTRIBUTING.md`.
- **D18 Popup frame first:** the popup shipped first as an empty frame:
  keyboard-navigable, with its section layout. Each section was filled in
  separately, and the sections stay independent of each other.
  - Keep awake is not a top-level section. Its Policy row sits inside Settings,
    after "Notify when a session asks". So the popup's top level holds only what
    changes while agents work: Setup, Sessions, Devices.
- **D19 Setup state:** the icon stays visible with a distinct "needs setup"
  look. The popup's top section lists what is missing, in order: SidePulse not
  installed (points to SidePulse's setup page, `https://sidepulse.io/setup`, not
  to a `curl … | bash` line, a pattern the marketplace flags); service stopped
  ("start service" runs `sidepulse service start`); no device ("plug in a
  SidePulse"). Agent State keeps working without a device.
  - The helper's `setup` event lists the items in that order (`install`,
    `service`, `device`). The service item carries the `start-service` action,
    which runs `python -I -m sidepulse service start` in the SidePulse venv. A
    missing venv is reported by the shell, since no helper can run there.
- **D20 Ask notification:** opt-in setting "Notify when a session asks", off by
  default. Clicking the notification jumps to the session (D8). This goes beyond
  Mac parity: the Mac app sends no agent notifications.
  - The helper runs `notify-send --wait --action=default=Open session` for each
    session that enters Ask: once per entry, none for sessions already asking when
    the helper starts, and none while the switch is off. Omarchy's notification
    server invokes a libnotify `default` action only while the sender is alive, so
    the notice's exit is the signal. A click prints `default` and opens the session
    through the D8 path. A dismissed notice opens nothing. A non-zero exit is reported
    in the popup (D14). The helper ends any notice still up when it exits.
  - The battery safeguard's notices (D11) use the same child list, with no action and
    no wait, keyed `None`. A click on them does nothing, and their exit is read as
    D14 reads it. They are not gated by this switch.
  - The notice names only the agent: "Claude Code needs attention", or "An agent" for
    an agent the plugin does not know. The body is fixed. The project and the agent's
    question stay out, because notify-send's arguments can be read by any local
    account through `/proc/<pid>/cmdline` while the notice waits. For the same reason,
    no provider string from the service's state reaches the command line.
- **D21 The look follows the product:** popup device rows draw a SidePulse Dot
  (2 LEDs) or SidePulse Pro (8 LEDs), lit live. The Bar LEDs match the connected
  product (2 or 8; 8 with none or both, since upstream prefers the Pro). The bar
  icon is a Dot or Pro glyph, neutral when none is connected.
  - The Bar LEDs play the program upstream's Notch plays for the agent State
    (`sync_virtual_status_device`: `program_for_agent_mode` with the user's animation
    for that State, at the Notch's brightness, 255 unless upstream says otherwise).
    They do not play the file on the device. The Notch's own Display does not apply
    to them: they follow the State even when a device is Off.
  - Each device row plays the program its device holds (`LEDS.LED`) through its own
    engine, at the device's LED count. So an Off device shows dark, and a Pro shows its
    own frames. The row plays it at full brightness. It undoes upstream's
    `apply_brightness` scaling by the device's brightness, since a screen pixel at a
    few percent is black where a real LED still shines. The brightness slider shows
    the brightness.
  - The row plays what its device holds. So a device on Battery (or in a lid or preview
    takeover) shows its battery program at full brightness too. The Bar LEDs stay on
    the State.
  - The device's file is read on each tick, so a change of program reaches the row
    within a second. Its animation frames run live. Those frames flow only while a
    popup is open (the `set-popup` gate, under Helper).
- **D22 Own artwork:** product drawings are our own vector shapes in QML,
  themed, with live LED colours. No SidePulse brand assets.
- **D23 SidePulse Pro supported, untested:** the Pro runs through upstream's
  device code like the Dot. README marks it untested on hardware; the
  verification ticket asks for a Pro tester. A Pro that unmounts on suspend shows
  "not connected" until it remounts; no eject prevention.
- **D24 Names follow upstream:** "SidePulse", "SidePulse Dot", "SidePulse Pro" in
  all prose and UI, including the manifest's display name; lowercase `sidepulse`
  only inside identifiers. Our virtual LEDs are **Bar LEDs**, the Omarchy
  counterpart of upstream's SidePulse Notch.
  Vocabulary: [`GLOSSARY.md`](../GLOSSARY.md).

- **D25 Unofficial hobby project:** the plugin is a personal contribution to the
  Omarchy community, not made by, affiliated with or endorsed by the SidePulse
  project. The README's opening and the manifest description say so, and the README
  credits Peter Kuhar and the SidePulse contributors.

## Spec

### Components

```
sidepulse-omarchy-plugin/                    (repo root = plugin root)
├── manifest.json        id io.github.mattiasbrandt.sidepulse, kinds [bar-widget]
├── Panel.qml            root Panel: BarIconButton + KeyboardPanel popup, with the
│                        popup sections Setup, Sessions, Devices, Settings (D13, D18)
├── BarLeds.qml          Bar LEDs, reparented into the bar window (D7, D21)
├── helper/
│   ├── qmldir           declares the singleton below (imported as "helper", not
│   │                    from the root: see D5)
│   ├── HelperProcess.qml  pragma Singleton: owns the one helper Process, applies
│   │                    lifecycle.mjs's actions, parses NDJSON (D5)
│   ├── lifecycle.mjs    refcount, debounced stop, restart backoff as pure functions
│   ├── sessions.mjs     the Sessions rows' age, provider label and cursor name
│   ├── settings.mjs     the Settings section's presets, hook and log wording
│   ├── barleds.mjs      the Bar LEDs' blend and tone map, upstream's constants
│   ├── popup.mjs        which widget copies have their popup open: set-popup's OR
│   ├── status.mjs       the tooltip text and the IPC status() line
│   ├── ipc.mjs          which widget copy owns the IPC target
│   └── sidepulse_omarchy.py   runs under SidePulse's venv python
├── tests/               pytest for the helper; node --test for *.mjs
├── README.md  LICENSE (MIT)  THIRD_PARTY_NOTICES.md  preview.png  docs/spec.md
```

### Helper (`helper/sidepulse_omarchy.py`)

- Launched by the `HelperProcess` singleton as
  `~/.local/share/sidepulse/venv/bin/python -I <plugin>/helper/sidepulse_omarchy.py`
  (venv path overridable via widget setting `sidepulsePython`; default also
  tries `sidepulse`'s shebang interpreter).
- **Protocol:** NDJSON. Stdout events, each `{"type": ..., ...}`:
  - `hello`: protocol 1, helper version, upstream version, capabilities map with
    reasons.
  - `state`: four-state label, colour, urgent for Ask, active_count, stale_count,
    top session. Re-sent on change. While the State cannot be read it is
    `unavailable`, with a `reason` shown dimmed, instead of the last good State.
  - `setup`: missing items in order (D19). Re-sent on change.
  - `result`: outcome of a command (`start-service`, `hooks-install`,
    `hooks-uninstall`, `open-session`).
  - `error`: feature and message. `clear`: the feature recovered. The error is
    cleared by a `clear` event once the feature reads again. Each new helper
    re-reports what still fails, so the widget drops its errors when the helper
    ends.
  - `devices`: id, name, path, product and kind (Dot or Pro), LED count, Display
    (agent, battery or off, the effective one), brightness percent, and the colour
    its LEDs show now (null when dark). Sent on change.
  - `hooks`: each agent's hook config, the fields `sidepulse doctor --json`
    prints: whether it exists and has SidePulse's hooks.
  - `power`: battery present, percent, plugged, lid closed (null when unknown),
    `battery_offered`. Re-sent on change.
    - `awake`: `policy`, `holding` (bool), `reason`. Re-sent on change; the predicate
    re-runs every tick.
  - `frame`: `target` is `bar`, `device` (with `device`: id), or `icon`. `leds` is
    `[r, g, b]` per LED, 0-255, or `null` to draw nothing. Sent only when that
    target's colours change.
  - `log`: the last hook decision rows: provider, event, the State it became.
  - `settings`: idle timeout, session retention and the Bar LEDs switch.
  - `sessions`: the popup's Sessions rows, at most 10, using upstream's
    recent-session rules: provider, session_id, name, project (cwd basename), state,
    label, colour, message, `updated_ms`, and `resumable` (has a session id). The
    popup ages the rows itself, so the helper sends nothing per second. Re-sent on
    change. Empty while the State is unavailable.
  - `hooks` and `log` are re-sent on refresh and every five ticks. `result` also
    answers `hooks-install` and `hooks-uninstall`.
  - Stdin commands: `refresh`, `start-service`, `set-displays`, `set-brightness`,
    `set-policy`, `set-bar-leds`, `set-agent-list`, `set-notify`, `set-popup`,
    `hooks-install`, `hooks-uninstall`, `open-session`.
    - `set-displays`: the widget's full Display map (D9).
    - `set-brightness`: `device` and `percent`.
    - `set-policy`: `policy` (never, agents, always).
    - `set-bar-leds`: `enabled`. It answers with `settings` and the next frame.
    - `set-agent-list`: `idle_timeout_seconds` and/or `recent_session_retention_seconds`.
    - `hooks-install` and `hooks-uninstall`: `provider` (D13).
    - `set-notify`: `enabled` (D20).
    - `set-popup`: `open`, true or false (below).
    - `open-session` (`provider`, `session_id`) answers with the D8 lookup state:
      stopped resumes, live focuses or locates, unresolved explains. The answer is a
      `result` (`command: "open-session"`, `ok`, `action`, `message`) and no state
      change. `ok` closes the popup.
  - The helper reads the State every second and re-checks setup every five
    seconds. So a stopped service or an unplugged device shows up without a restart.
  - The helper never binds `events.sock`. That socket belongs to upstream's service
    (D4).
- **Upstream reuse (D4):** state comes from `DaemonMonitor().snapshot()` on each
  tick. Settings go through `load_settings`, `save_settings` and the `with_*`
  functions. Devices come from `discover_mounted_devices` and
  `describe_mounted_device` (D9). LED writes go through `write_led_program`. The
  keep-awake grace comes from `KeepAwakeController.should_hold_for_mode`, and the
  sleep rules from `daemon_runtime` (D11). Battery programs come from
  `program_for_battery`. Bar LED frames come from `sdled.wasm` and upstream's
  blending constants. Hook install and uninstall go through the
  `sidepulse setup <agent>` CLI.
- **Capabilities** `hooks`, `hook_log`, `settings`, and `notify` (notify-send on
  PATH) are probed at start, like the others.
- **Linux-only additions (D10, D11):** `/sys/class/power_supply` reader that
  produces upstream's battery snapshot type; logind lid state; a
  `systemd-inhibit --what=sleep:handle-lid-switch --mode=block` child, held while
  the predicate says hold; LED handover for battery and lid display (device to
  `custom`, write program, restore afterwards).
  - The `power` event (`battery_present`, `percent`, `plugged`, `lid_closed`,
    `battery_offered`), the `battery` and `lid` capabilities (`lid` also needs
    `busctl` on PATH), and the `battery` value of `set-displays`.
  - Each tick reads the battery and the lid once. Devices are reconciled right after.
    A preview or lid animation's end wakes the loop, so a takeover ends on time.
    Takeovers are released on exit.
  - The battery feeds the safeguard, and its problems come as `error` with feature
    `power`.
  - Keep-awake: the `awake` event, the `set-policy` command, and the `keep_awake`
    capability (upstream's controller and settings names, plus `systemd-inhibit` on
    PATH). Tests put a fake `systemd-inhibit` on PATH (`tests/conftest.py`).
- **Capability probe (D14)** at start: each feature is independently on or off, with
  a reason. No silent fallbacks.
- Exits when stdin closes (shell gone), and releases the inhibitor and any LED
  handover.
- **LED handback (researched at 6d55225):** the service drops Manual (`custom`)
  devices from its skip-unchanged-writes cache (`service.py:216-218`). So when the
  helper sets a device back to `agent`, the service repaints on its next 1 s poll.
  The helper never needs to repaint agent programs itself.
- **Frames (D6, D7, D21):**
  - The loop runs on two deadlines: the one-second tick, and the frame. The frame
    runs at 60 fps while the bar's State is Working or Ask, and at 12 fps otherwise.
    So a frame costs one step and one line, not a full tick.
  - A program that does not parse is reported as `error` with feature `frames`
    (PROBLEMS: "LED programs"). It plays dark. `clear` follows when it is fixed.
  - Popup gate: device frames are sent only while a popup is open. The widget sends
    `set-popup` (`open`: true or false) with the OR of its copies' popups
    (`helper/popup.mjs`). It sends on each change, and again after each hello, since
    a fresh helper starts closed. While closed, no device engine steps and no device
    frame is sent. The bar frames are unaffected. On open, the device frames are sent
    at the next pump, not suppressed as unchanged.
  - Icon: `frame` with `target: icon` carries the bar icon's device's frames (D12).
    That device steps even while every popup is closed. Every other device steps only
    while one is open, and each steps once per frame, whoever reads it. `null` clears
    an icon that was lit. With no device ever, no icon frame is sent.

### Widget behaviour

- Icon (D12, D21): always visible. A SidePulse Dot or Pro glyph for the connected
  product (neutral with none). Colour by State. Ask sets `active`. A distinct "needs
  setup" look applies while D19 does. Tooltip: State, active count, top session, and
  `· click for details`. The left click is the only action; no right or middle click
  is added.
- **IPC:** the IPC target is the plugin id `io.github.mattiasbrandt.sidepulse`
  (IPC targets are global in the shell). Calls: `open`, `close`, `show`, `hide`,
  `toggle` (as the base Panel's), and `status(): string`, one JSON line: `state`
  (Working, Ask, Done, Idle, or null), `label`, `urgent` (Ask), `active_count`, `top`
  (the top session's name, or null), and `needs_setup`. Before the helper reports,
  `state` is null and `label` is `not installed` or `starting`, as the tooltip reads.
  `status()` is answered by the widget from its last helper events, not by the helper.
  Only one copy owns the target: the first drawn copy (`helper/ipc.mjs`). The center
  section's zero-size placeholder never does, since Quickshell would keep only one
  handler and log that the other is unused.
  Live check: `qs ipc -p /usr/share/omarchy/shell call io.github.mattiasbrandt.sidepulse status`.
- Popup (D13, D18), keyboard navigable (j/k, Enter, Esc):
  0. **Setup** (only while something is missing, D19).
  1. **Sessions**: up to 10, upstream ordering. A row is the provider mark, project
     (cwd basename), state, last message and age. Enter or click runs D8.
  2. **Devices**: per device, drawn as its product with live LEDs (D21, D22). Shows
     display Agent, Battery or Off (D9), and brightness.
  3. **Settings** (collapsible): hooks per provider (status from doctor, toggle
     install or uninstall), Bar LEDs on/off, idle timeout, retention, hook log viewer
     (last N decision rows), "Notify when a session asks" (D20), Keep awake (Never /
     When Agents Work / Always, with the holding state and reason; D11), and
     capability problems.
- Bar LEDs (D7, D21) appear when `virtual_status_device_enabled` is on. That is
  upstream's SidePulse Notch setting, so it means the same on both.
- `omarchy-shell shell toggle io.github.mattiasbrandt.sidepulse` opens the popup.
  README suggests a keybind; the plugin installs none.
- Exactly one helper per shell (D5). All widget copies render from the shared model.

### Out of scope (v1)

Per-state animation editor and profiles; "Open with…" chooser / VS Code opener;
eject prevention; iPhone linking UI; hook-log CSV/HTML export; upstream PRs for the
Linux battery/lid code (D10); QML test harness.

### Requirements documented in README

SidePulse installed via the official installer; `wasmtime` in the SidePulse venv for
the Bar LEDs; `xdg-terminal-exec` (Omarchy default); optional `herdr` or `tmux` for
jumping into sessions that run inside them (D8); `systemd-inhibit` (systemd); the
upstream commit tested against (D14).

**Security section** (required for the plugin marketplace; plugins are
unsandboxed). README's Security section is the current, detailed list. Keep this
summary in step with it. The plugin lists what it touches: it runs a Python helper from the SidePulse
venv; rewrites agent configs through the upstream CLI on hook toggles
(`~/.claude/settings.json`, `~/.codex/config.toml`, `~/.grok/hooks/`,
`~/.cursor/hooks.json`, `~/.junie/config.json`); writes upstream `settings.json`; writes
`LEDS.LED` on mounted SidePulse drives; sets the mode of SidePulse's state folder and
hook logs (D13); writes its own entry in `~/.config/omarchy/shell.json`; holds a logind sleep/lid inhibitor; reads
`/proc` for session→window lookup; reads the service's status socket; runs
`hyprctl dispatch` (window focus) and `xdg-terminal-exec`. It makes no network
requests of its own and needs no sudo.

## Plan

Thin vertical slices. Each slice ends with validate, qmllint and pytest green, and a
live check in the shell. Each slice was one GitHub issue (D16). CI and release
automation (D17) came first, before any plugin code.

0. **Spikes** — answered. Results are recorded under D4–D8:
   - Band: widget child reparented into the bar window, full width (D7).
   - Single helper: plugin QML singleton, refcounted; survives reload and disable (D5).
   - wasm: wasmtime port byte-identical to an independent driver, 117/117 (D6).
   - Session → window: stopped / live / unresolved lookup (D8).
   - State source: the service's `get_state` reply, polled each tick (D4).
1. **Scaffold** — repo, manifest, MIT LICENSE, README skeleton, dev test environment
   and runner (command in CONTRIBUTING.md's Checks table), static icon in the bar;
   `omarchy plugin validate` passes; installs via `omarchy plugin add <local path>`.
2. **Helper core and live icon** — capability probe, `hello`/`state` events,
   `HelperProcess` singleton (refcounted), icon colour and tooltip follow real agents,
   the empty popup frame (D18), and the setup state (D19).
3. **Sessions popup and open** — list, keyboard navigation, three-state open (D8).
4. **Devices** — discovery, on/off via Manual mode, brightness, display mode.
5. **Settings section** — hook toggles, timeouts and retention, hook log viewer,
   capability problems.
6. **Keep awake** — policy, predicate, inhibitor lifecycle.
7. **Laptop** — battery reader, battery display, 7 s on plug/unplug, lid animations,
   LED handover.
8. **Bar LEDs** — wasm frames → Bar LEDs rendering.
9. **Release prep** — README (install, usage, settings, requirements, remove,
   security), preview.png, public push, marketplace submission.
10. **Verification ticket** — protoarchy live pass with the Dot; laptop checklist
    (battery, plug/unplug, lid inhibitor, lid animations).

## Appendix: research (2026-10-10)

Snapshot of the facts the decisions rested on at research time. Line numbers refer
to upstream commit 6d55225 and the Omarchy shell as installed on that date. Where
upstream commit 20ca7fd changed a fact, the bullet says so. Each bullet marked
"researched at 6d55225" was not rechecked at 20ca7fd.

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

- **State (researched at 6d55225).** `latest.json` = `{updated_at, statuses[]}`,
  written by the service only when an event changes a status (collector.py:487-503).
  No aggregate field; `age_seconds`/`stale` frozen at write time. Aggregate rules
  live in `snapshot_from_statuses` (collector.py:1231-1281): PostToolUse working
  >120 s → completed; completed stale after 20 min; idle_ready stale immediately;
  others after 3600 s; lowest priority number wins. At 20ca7fd the service no longer
  writes `latest.json`. It holds the statuses in memory, answers `get_state`, and
  applies the rules itself (D4).
- **Machine-readable CLI (verified at 6d55225):**
  - `sidepulse agent-monitor status --json` → `{collected_at, sources,
    aggregate{mode, mode_label, active_count, stale_count, representative},
    statuses, stale_statuses}` — upstream computes the aggregate (rebuilds
    from provider JSONL logs, same rules). At 20ca7fd the `agent-monitor`
    command is gone (D13).
  - `sidepulse agent-monitor doctor --json` → per-provider
    `{provider, config_path, exists, hooks_enabled, hook_events, log_paths}`.
    At 20ca7fd the command is `sidepulse doctor --json`.
  - `sidepulse agent-monitor install|uninstall <provider>` plain text only; `service
    start|stop|status` via `systemctl --user`, exit code = running.
- **Four-state UI mapping** (status_bar.py:461-472 at 6d55225; lines 455-466 at
  20ca7fd): Ask = waiting_for_input | blocked_error (#FF8A00); Working = working |
  tool_running | long_task_progress (#00AEEF); Done = completed (#22B14C); Idle = rest
  (#A8A8A8). Session list = fresh + completed within 48 h retention, merged per
  (provider, session_id), sorted by priority, max 10 (status_bar.py:6317-6386 at
  6d55225).
- **Connect to Device (researched at 6d55225):** in-memory flag only. The macOS menu of
  that time no longer showed it (README still documented it). The Linux service has no
  equivalent; the options were per-device `led_display: "custom"` (service skips the
  device) or stopping the service.
- **Service gate (researched at 6d55225):** the service writes no LEDs while anything
  accepts on `~/.local/state/sidepulse/agent-monitor/events.sock` (service.py:224-235).
  The macOS app takes over LEDs that way. A Linux UI must not bind that socket unless
  it takes over LED driving. The helper never binds it (D4).
- **Settings (researched at 6d55225):** `~/.config/sidepulse/agent-monitor/settings.json`
  (absent on the development machine → defaults). Re-read by the service every 1 s
  poll. No general settings CLI; `save_settings` rewrites from `to_dict`, dropping
  unknown keys. The service honoured only per-device display, brightness and
  `agent_animation(mode)`. It ignored idle timeout, retention, sleep policy and lid
  animations. At 20ca7fd the service no longer reads the idle timeout (D4).
- **Session open (session_actions.py:38-78, researched at 6d55225):** resume commands
  `claude --resume <id>`, `codex resume <id>`, `grok --resume <id>`,
  `junie --session-id=<id> --resume`, run in `cwd`; VS Code
  `vscode://anthropic.claude-code/open?session=<id>`. All terminal launch and focus
  code was AppleScript (macOS-only).
- **macOS-only at 6d55225:** battery reader (`ioreg`; it failed on the development
  machine), keep-awake (`caffeinate`) and lid (`pmset`, `ioreg`), SidePulse Notch
  (`sdled.wasm` run in JavaScriptCore via PyObjC), power-source notifications,
  `sidepulse settings`. Linux equivalents: `/sys/class/power_supply`, `systemd-inhibit`,
  logind lid state. At 20ca7fd the daemon's battery, keep-awake and lid code run only
  on macOS. The rules themselves import on Linux (D10, D11).
- **SidePulse Notch engine (researched at 6d55225):** `resources/sdled.wasm` (34 KB, no
  imports; exports `sdled_reset/parse/step`, RGB bytes at output ptr, 2 or 8 LEDs). QML
  has no WebAssembly. The options were a node, deno or wasmtime helper streaming
  frames, or a JS port of the `virtual_led_colors` fallback. The plugin uses wasmtime
  (D6).
- **Keep-awake predicate (keep_awake.py:80-98, researched at 6d55225):** hold while
  Working/Tool Running/Long Task; 300 s grace after Completed/Waiting/Error; battery
  safeguard below 20 % unplugged. At 20ca7fd the policy and safeguard functions are in
  `daemon_runtime.py`, and the grace is `KeepAwakeController.should_hold_for_mode` (D11).

### Upstream SidePulse alignment (researched at 6d55225)

- **No releases, no tags, not on PyPI.** Install = git `main`. The plugin records the
  commit it is tested against (D14).
- **Upstream was migrating to Rust:** upstream PR #52 "Rust migration checkpoint: core,
  service, hooks, and tray" (open at research time, 89 commits, last push 2026-10-06,
  no review yet). Python stays operational until parity
  (`docs/design/rust-architecture.md`, pr52). Rust brings:
  - a **Linux tray via KSNI** (StatusNotifierItem, `crates/sidepulse-tray`);
  - a versioned NDJSON socket protocol (`PROTOCOL_VERSION=1`, `Snapshot`,
    `Subscribe`, `RenderLedProgram`, `VirtualDisplayFrame`);
  - a Tauri/WebKitGTK settings window on Linux (proposal);
  - rule: keep settings/JSONL/link/relay JSON compatible, migrate only with a
    versioned reader. Linux native UI explicitly **not validated** upstream.
- **Third-party clients:** upstream issue #55 (open, 0 replies at research time).
  The Rust socket breaks the Python `{"provider","line"}` event message, and the issue
  asks for a documented client interface. No other upstream statement on third-party
  frontends.
- **No CONTRIBUTING / AGENTS.md; MIT; pytest; design docs in `docs/design/`** are the
  contributor contract. Single main maintainer; PRs and issues mostly unanswered, so
  upstreaming will be slow and uncertain.
- **Surfaces:** `status --json` works headless but is undocumented and unversioned;
  `latest.json` and `settings.json` have no version key; `LEDS.LED` DSL is public
  (`LEDS_FORMAT.md`); `sdled.wasm` is internal.
- **Drift risk high:** socket paths and state dirs expected to change at the Rust
  cutover; open upstream PRs #53, #56, #57, #58 touch integration surfaces (open at research
  time).
- **Recommendation from the research:** consume upstream through a thin, swappable
  adapter (CLI JSON now, Rust protocol later); don't reimplement aggregation or the LED
  interpreter; reuse upstream parity fixtures as conformance tests (D4, D6).
