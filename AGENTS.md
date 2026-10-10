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

## Standards

Before writing helper or QML code, read `CODING_STANDARDS.md`: the
architecture invariants every change keeps (upstream does the logic, one
adapter, settings through upstream, the service keeps the LEDs, one helper per
shell) and the naming rules. Reviews check diffs against it.

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
  Work not tied to one slice (plan, docs) takes a plain branch: a branch
  linked to the parent #1 closes it when its PR merges.
- **One PR per slice, plus at most one maintenance PR.** Small finds ride
  along in the current slice's PR as their own commits. Docs, CI and process
  fixes between slices accumulate on the single open maintenance PR
  (`chore: …`) until the next slice starts; they never get a PR each.
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
