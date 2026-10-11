# Contributing

Design decisions, the spec and the build plan live in [`docs/spec.md`](docs/spec.md);
work is tracked as sub-issues of [#1](https://github.com/mattiasbrandt/sidepulse-omarchy-plugin/issues/1).

## Workflow

1. Pick an issue whose blockers are closed and branch from it:
   `gh issue develop <n> --name <branch> --checkout`.
2. Commit on the branch however suits the work.
3. Open a PR whose **title is a Conventional Commit** (below) and whose body says
   `Fixes #<n>`. CI and the PR-title check must pass.
   The merge closes the issue (the branch is linked to it), so every
   acceptance box on the issue is ticked before the merge; the
   `closes only finished issues` check fails while one is open. Ticking a
   box does not re-run it: re-run the check afterwards.
4. The PR is **squash-merged**: its title becomes the one commit on `main`, with
   an empty body. That commit is what versioning reads, so the title has to say
   what the change means for users.

## Conventional Commits

PR titles follow [Conventional Commits 1.0.0](https://www.conventionalcommits.org/en/v1.0.0/):

```
<type>(<scope>): <subject>
```

The subject is imperative and lowercase, has no trailing period, and the whole
title stays within 72 characters.

| Type | Use for | Version effect (from 1.0.0) | Before 1.0.0 |
|---|---|---|---|
| `feat` | New user-facing behaviour | MINOR | MINOR |
| `fix` | Bug fix | PATCH | PATCH |
| `feat!` / `fix!` | Breaking change | MAJOR | MINOR |
| `perf` | Faster, same behaviour | PATCH | PATCH |
| `refactor` | Restructure, same behaviour | none | none |
| `test` | Tests only | none | none |
| `docs` | Spec, README, comments, agent instructions | none | none |
| `ci` | Workflows, CI scripts | none | none |
| `build` | Packaging, dev environment | none | none |
| `chore` | Dependencies, housekeeping | none | none |
| `revert` | Reverts an earlier commit | as the reverted change | |

A **breaking change** is anything that makes an existing install stop working
without the user acting: a new required prerequisite, a renamed or removed
widget setting in `shell.json`, a dropped SidePulse or Omarchy version, a
changed plugin id. Mark it with `!` after the type or scope:
`feat(helper)!: require wasmtime for the bar LEDs`.

### Scopes

Optional, one of:

| Scope | Covers |
|---|---|
| `widget` | Bar widget and popup QML |
| `helper` | The Python helper and its upstream adapter |
| `bar-leds` | The Bar LEDs along the Omarchy bar edge |
| `spec` | `docs/spec.md` decisions and plan |
| `ci` | Workflows and CI scripts |
| `deps` | Dependency and pin updates (Dependabot uses it) |
| `release` | Release PRs (release-please uses it) |

## Releases

[release-please](https://github.com/googleapis/release-please) runs on every push
to `main`. It keeps one release PR open with the next version, the generated
`CHANGELOG.md` section and the bumped `version` in `manifest.json`. Merging that
PR tags `vX.Y.Z` and publishes the GitHub Release. Nobody tags or edits
`CHANGELOG.md` by hand.

GitHub holds the release PR's CI as "awaiting approval" because the Actions bot
opened it: click **Approve and run** and merge once it's green.

Omarchy installs plugins as a git checkout of the default branch
(`omarchy plugin add` / `update`), so a release is a named, documented point on
`main`, not a separate artifact.

## Checks

CI (`.github/workflows/ci.yml`) runs on every PR and on `main`:

| Check | Locally |
|---|---|
| Plugin validation (Omarchy's own validator at the pinned Omarchy tag) | `omarchy plugin validate .` |
| SidePulse naming (D24) | `python3 -I scripts/check_names.py` |
| Python tests | `UV_PROJECT_ENVIRONMENT="$HOME/.cache/sidepulse-omarchy-plugin/venv" uv run --locked pytest` |
| qmllint against the Omarchy shell | `scripts/qmllint.sh` |
| actionlint | `actionlint` |

The live check on the bar runs only locally, because CI has no Omarchy session:
`scripts/live-check.sh up`, exercise the change, `scripts/live-check.sh down`
(AGENTS.md has the details).

Every action is pinned to a full commit SHA with its `# vX.Y.Z`; Dependabot
moves the pins weekly.

The Python tests run in a uv environment defined by `pyproject.toml` and
`uv.lock`: pytest, `wasmtime`, and upstream SidePulse at the pinned commit. It
never touches the SidePulse venv under `~/.local/share/sidepulse`. The local
command keeps the environment out of the repo: uv's venv holds symlinks, and
`omarchy plugin validate` and the live-check rsync both walk the whole tree.
CI runs `uv run --locked pytest` (`tests/` and the spikes) on Python 3.10 (upstream's floor) and
3.14 in a fresh checkout. Locally, add `--python 3.10` to check the floor; uv
rebuilds the environment when the interpreter changes.
