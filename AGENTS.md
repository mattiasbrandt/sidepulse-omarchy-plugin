# AGENTS.md

Omarchy shell plugin (`io.github.mattiasbrandt.sidepulse`) that gives SidePulse
devices the macOS menu-bar app's functionality on Omarchy: a QML bar widget and
popup, plus a Python helper that runs inside the user's SidePulse install.

## Source of truth

- **`docs/spec.md`** holds the decisions (D1–D16), the spec, the plan, and the
  research appendix with file:line citations. Read the decisions that touch your
  change before writing code. A change that alters a decision amends that
  decision in `docs/spec.md` in the same PR.
- **Issues:** parent #1; each plan slice is a sub-issue with native blocked-by
  edges. Work an issue only once its blockers are closed. Spike answers (#2)
  land in `docs/spec.md` as amendments to the decision they settle.

## Invariants

These hold in every change. Each traces to a decision in `docs/spec.md`.

- **Upstream does the logic (D4).** The helper imports SidePulse's own code
  (state rules, settings, devices, LED programs, keep-awake predicate,
  `sdled.wasm`) from the user's SidePulse venv. QML renders and sends commands.
  Code that would duplicate an upstream rule calls the upstream rule instead.
- **One adapter (D4, D14).** Every upstream touchpoint goes through the helper's
  adapter layer, so the Rust protocol (upstream PR #52) can replace it later.
  Upstream capabilities are probed at start; a missing one disables only its
  feature and surfaces the reason in the popup. Failures stay loud.
- **Settings through upstream (D9, D13).** SidePulse settings are written with
  upstream `load_settings`/`save_settings`, so unknown-key handling and the
  schema stay upstream's. Only presentation settings live in the shell.json
  widget entry.
- **The service keeps the LEDs.** `sidepulse.service` drives agent LEDs. The
  helper takes a device over only by setting it to Manual (`custom`) and
  hands it back by restoring the mode. The service stops writing LEDs whenever
  something accepts on `events.sock`, so that socket belongs to upstream alone.
- **One helper per shell (D5),** spawned by the widget, exiting when stdin
  closes, releasing any inhibitor and LED takeover on exit.
- **Omarchy installs plain files.** `omarchy plugin add` runs no plugin code,
  hooks or sudo: every requirement is a documented prerequisite in the
  README's Requirements section, and every file or system state the plugin
  touches is listed in its Security section. No symlinks anywhere in the repo.

## Reference material

- **Omarchy shell** (Quickshell): `$OMARCHY_PATH/shell/` (`/usr/share/omarchy`),
  read-only. Pattern after
  `~/.config/omarchy/plugins/io.github.thisisgm.omapods/` (Panel + Service +
  FileView + IpcHandler). Plugin contract and marketplace rules: spec appendix.
- **Upstream SidePulse:** the code the helper actually imports is
  `~/.local/share/sidepulse/venv/lib/python3.*/site-packages/sidepulse/`.
  Upstream repo: `inteliwear/sidepulse` (no releases; installs track `main`).
  The commit the plugin is tested against is recorded in the README.

## Verify

Run before calling a change done:

```sh
omarchy plugin validate .
/usr/lib/qt6/bin/qmllint -I "$OMARCHY_PATH/shell" *.qml   # qmllint is not on PATH
```

The helper test runner arrives with #3; record its command here when it lands.

Live check: sync the tree into the plugin dir (the shell hot-reloads on save),
then exercise the change in the real bar:

```sh
rsync -a --delete --exclude .git ./ ~/.config/omarchy/plugins/io.github.mattiasbrandt.sidepulse/
```

Hardware a change needs but this machine lacks (battery, lid) goes on the
verification ticket's laptop checklist (#9).

## Git and GitHub

- Branch per issue with `gh issue develop <n> --name <branch> --checkout`; PRs
  close their issue with `Fixes #<n>`.
- Commit messages: imperative subject, body says why; no tool or model
  attribution trailers.
- Issue and PR prose ends with a one-line `//<harness> <model>` signature.
