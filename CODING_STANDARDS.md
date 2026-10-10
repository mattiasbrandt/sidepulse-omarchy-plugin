# Coding standards

Applied when reviewing a diff. Each rule is a judgement call that no check can
make; anything mechanical lives in CI instead (`.github/workflows/ci.yml`).
Every rule traces to a decision in `docs/spec.md`. A diff that breaks one
either changes or names the decision it departs from.

## Architecture

- **Upstream does the logic (D4).** The helper imports SidePulse's own code
  (state rules, settings, devices, LED programs, keep-awake predicate,
  `sdled.wasm`) from the user's SidePulse venv. QML renders and sends commands.
  Code that re-derives an upstream rule calls the upstream rule instead.
- **One adapter (D4, D14).** Every upstream touchpoint goes through the
  helper's adapter layer, so the Rust protocol (upstream PR #52) can replace it
  later. Upstream capabilities are probed at start; a missing one disables only
  its feature and surfaces the reason in the popup. Failures stay loud.
- **Settings through upstream (D9, D13).** SidePulse settings are written with
  upstream `load_settings`/`save_settings`, so unknown-key handling and the
  schema stay upstream's. Only presentation settings live in the shell.json
  widget entry, plus one deliberate exception: each device's chosen display
  (Agent / Battery / Off), which upstream can't express on Linux; the helper
  re-derives upstream's value from it (D9).
- **The service keeps the LEDs.** `sidepulse.service` drives agent LEDs. The
  helper takes a device over only by setting it to Manual (`custom`) and
  hands it back by restoring the mode. The service stops writing LEDs whenever
  something accepts on `events.sock`, so that socket belongs to upstream alone.
- **One helper per shell (D5),** spawned by the widget, exiting when stdin
  closes, releasing any inhibitor and LED takeover on exit.

## Distribution

- **Omarchy installs plain files.** `omarchy plugin add` runs no plugin code,
  hooks or sudo: every requirement is a documented prerequisite in the
  README's Requirements section, and every file or system state the plugin
  touches is listed in its Security section.

## Language

- **Names per `GLOSSARY.md` (D24).** Prose, UI strings and code identifiers
  that name a domain concept use the glossary's term, never one it lists under
  _Avoid_. CI catches misspelled forms of "SidePulse"; a review catches the
  rest: lowercase `sidepulse` outside identifiers, "Notch" for our Bar LEDs,
  "provider" or "custom" leaking from upstream's code into the UI.

## Versioning

- **The PR title is typed by user impact (D17).** `feat` adds behaviour a user
  sees, `fix` repairs it, `!` marks a change an existing install needs action
  for; files touched decide nothing.
