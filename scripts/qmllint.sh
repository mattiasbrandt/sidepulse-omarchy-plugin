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
mapfile -t files < <(git ls-files -- '*.qml' ':!:spikes/**')
if [ "${#files[@]}" -eq 0 ]; then
  echo "qmllint.sh: no QML files to lint"
  exit 0
fi

root=$(mktemp -d)
trap 'rm -rf "$root"' EXIT
ln -s "$(realpath "$shell_dir")" "$root/qs"

"$qmllint" --max-warnings 0 -I "$root" "${files[@]}"
echo "qmllint.sh: ${#files[@]} file(s) clean"
