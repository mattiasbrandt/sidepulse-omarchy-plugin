# AGENTS.md

Omarchy shell plugin (`io.github.mattiasbrandt.sidepulse`) that gives SidePulse
devices the macOS menu-bar app's functionality on Omarchy: a QML bar widget and
popup, plus a Python helper that runs inside the user's SidePulse install.

Write names exactly as `GLOSSARY.md` defines them: "SidePulse", "SidePulse
Dot", "SidePulse Pro", "Bar LEDs"; lowercase `sidepulse` only in identifiers.

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
  widget entry, plus one deliberate exception: each device's chosen display
  (Agent / Battery / Off), which upstream can't express on Linux; the helper
  re-derives upstream's value from it (D9).
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

Before calling a change done, run every check in the **Checks** table of
`CONTRIBUTING.md` locally; CI runs the same set on the PR. `scripts/qmllint.sh`
fails on any qmllint warning, and resolves Omarchy's `qs.*` imports the way
Quickshell does.

Live check: install the working tree into the **center** section of the bar
(the local test placement; the manifest default for users stays `right`),
exercise the change, then put the bar back exactly as it was:

```sh
cp ~/.config/omarchy/shell.json /tmp/shell.json.before-check
rsync -a --delete --exclude .git --exclude .claude --exclude .pytest_cache \
  ./ ~/.config/omarchy/plugins/io.github.mattiasbrandt.sidepulse/
omarchy-shell shell rescanPlugins
omarchy plugin enable io.github.mattiasbrandt.sidepulse --section center
# ... check the change; the shell hot-reloads on every rsync ...
omarchy plugin disable io.github.mattiasbrandt.sidepulse
rm -rf ~/.config/omarchy/plugins/io.github.mattiasbrandt.sidepulse
omarchy-shell shell rescanPlugins
diff /tmp/shell.json.before-check ~/.config/omarchy/shell.json
```

The center section also exercises the bar's zero-size anchor copy of a center
widget, which the single-helper refcount (D5) has to count correctly.

Hot reload skips the plugin's QML singleton (it lives as long as the shell),
so a change to the singleton needs `omarchy restart shell` before the check.

Hardware a change needs but this machine lacks (battery, lid) goes on the
verification ticket's laptop checklist (#9).

## Git, versioning and releases

`CONTRIBUTING.md` owns the commit rules, type → version table and scopes.

- Branch per issue with `gh issue develop <n> --name <branch> --checkout`.
- **The PR title is the release record.** `main` takes squash merges only; the
  title becomes the single commit release-please reads. Write it as a
  Conventional Commit typed by what the change means for a user (`feat`,
  `fix`, `!` for breaking), not by the files touched. The body says
  `Fixes #<n>`.
- Branch commits: imperative subject, body says why; Conventional form
  welcome, not required. No tool or model attribution trailers.
- release-please owns `CHANGELOG.md`, `manifest.json`'s `version`,
  `.release-please-manifest.json` and the `v*` tags; leave them to its release
  PR.
- New or bumped actions are pinned to a full commit SHA with `# vX.Y.Z`.
- Issue and PR prose ends with a one-line `//<harness> <model>` signature.
