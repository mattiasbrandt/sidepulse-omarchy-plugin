#!/usr/bin/env bash
# Lint the plugin's QML against the Omarchy shell it runs in.
#
# Usage: scripts/qmllint.sh [omarchy-shell-dir]
#   omarchy-shell-dir defaults to $OMARCHY_PATH/shell (/usr/share/omarchy/shell).
#
# Omarchy's modules are imported as `qs.Commons`, `qs.Ui`, ...: Quickshell maps
# the `qs` prefix to the shell's root directory at runtime. qmllint resolves a
# module URI from an import path, so the shell is exposed as `<tmp>/qs` through
# a symlink in a temporary directory (never in the repo) and `<tmp>` becomes
# the import path. Any warning fails the run; relax a category in .qmllint.ini
# only with a reason next to it.
set -euo pipefail

shell_dir=${1:-${OMARCHY_PATH:-/usr/share/omarchy}/shell}
[ -f "$shell_dir/shell.qml" ] || { echo "qmllint.sh: no Omarchy shell at $shell_dir" >&2; exit 2; }

qmllint=$(command -v qmllint || true)
[ -n "$qmllint" ] || qmllint=/usr/lib/qt6/bin/qmllint
[ -x "$qmllint" ] || { echo "qmllint.sh: qmllint not found (install qt6-declarative)" >&2; exit 2; }

cd "$(git rev-parse --show-toplevel)"
mapfile -t files < <(git ls-files -- '*.qml')
# A singleton is only recognised when qmllint reads the qmldir that declares
# it; linting the singleton's own file does not look one up.
mapfile -t qmldirs < <(git ls-files -- '*qmldir')
if [ "${#files[@]}" -eq 0 ]; then
  echo "qmllint.sh: no QML files to lint"
  exit 0
fi

root=$(mktemp -d)
trap 'rm -rf "$root"' EXIT
ln -s "$(realpath "$shell_dir")" "$root/qs"

qmldir_args=()
for qmldir in "${qmldirs[@]}"; do qmldir_args+=(-i "$qmldir"); done
args=(-I "$root" "${qmldir_args[@]}" "${files[@]}")
if ! "$qmllint" --max-warnings 0 "${args[@]}"; then
  # The text output can leave out a warning that --max-warnings still counts
  # (Qt 6.12's block-scope-var-declaration exited 255 with nothing printed).
  # The JSON report lists every one, so print those before failing.
  echo "qmllint.sh: qmllint failed; every warning it counted:" >&2
  "$qmllint" --json - "${args[@]}" | python3 -c '
import json, sys
for f in json.load(sys.stdin)["files"]:
    for w in f["warnings"]:
        if w.get("type") != "info":
            rule = w.get("id") or w.get("type")
            print("%s:%s: [%s] %s" % (f["filename"], w.get("line", "?"), rule, w["message"].splitlines()[0]))
' >&2 || echo "qmllint.sh: could not read qmllint's JSON report" >&2
  exit 1
fi
echo "qmllint.sh: ${#files[@]} file(s) clean"
