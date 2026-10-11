#!/usr/bin/env bash
# Live check: run the working tree in the bar's center section, then put the
# bar back exactly as it was (AGENTS.md).
#
# Usage: scripts/live-check.sh up | sync | down
#   up    back up shell.json, install the working tree, enable it in the
#         center section, restart the shell, wait for exactly one helper
#   sync  install the working tree again and restart the shell
#   down  disable and remove the plugin, restore shell.json from the backup if
#         it still differs, wait for the helper to stop
#
# A plugin already installed (a release you run day to day) is never touched:
# up refuses, since down removes the directory it installed.
#
# Every step restarts the shell instead of relying on hot reload: the shell
# never reloads the plugin's singleton, so a long-running shell keeps an old
# one, and a widget next to it starts no helper at all.
set -euo pipefail

id=io.github.mattiasbrandt.sidepulse
plugin_dir=$HOME/.config/omarchy/plugins/$id
config=$HOME/.config/omarchy/shell.json
backup=/tmp/shell.json.before-check
timeout_s=20

die() {
  echo "live-check.sh: $*" >&2
  exit 1
}

helpers() {
  pgrep -f -- "$plugin_dir/helper/sidepulse_omarchy.py" || true
}

# Wait until exactly $1 helpers run; report what was there on timeout.
wait_helpers() {
  local want=$1 count=0 i
  for ((i = 0; i < timeout_s * 2; i++)); do
    count=$(helpers | wc -l)
    [ "$count" -eq "$want" ] && return 0
    sleep 0.5
  done
  die "expected $want helper(s) after ${timeout_s}s, found $count: $(helpers | tr '\n' ' ')"
}

# The shell stops answering IPC while it restarts or rescans.
wait_shell() {
  local i
  for ((i = 0; i < timeout_s * 2; i++)); do
    omarchy-shell shell ping >/dev/null 2>&1 && return 0
    sleep 0.5
  done
  die "omarchy-shell did not answer for ${timeout_s}s"
}

# A rescan returns before the shell has read the plugin directory.
wait_known() {
  local i
  for ((i = 0; i < timeout_s * 2; i++)); do
    omarchy-shell shell listPlugins 2>/dev/null | grep -qF "\"id\":\"$id\"" && return 0
    sleep 0.5
  done
  die "the shell does not list $id after ${timeout_s}s; run down to clean up"
}

install_tree() {
  rsync -a --delete --exclude .git --exclude .claude --exclude .pytest_cache \
    ./ "$plugin_dir/"
}

rescan() {
  wait_shell
  omarchy-shell shell rescanPlugins >/dev/null
}

restart_shell() {
  omarchy restart shell >/dev/null
  sleep 1
  wait_shell
}

cd "$(git rev-parse --show-toplevel)"

case ${1:-} in
up)
  [ ! -e "$backup" ] || die "a check is already running ($backup exists); run down first"
  [ ! -e "$plugin_dir" ] || die "$plugin_dir already exists (an installed copy?); remove or disable it yourself first"
  cp "$config" "$backup"
  install_tree
  rescan
  wait_known
  omarchy plugin enable "$id" --section center || die "enable failed; run down to clean up"
  restart_shell
  wait_helpers 1
  echo "live-check.sh: up; helper $(helpers)"
  ;;
sync)
  [ -e "$backup" ] || die "no check is running; run up first"
  install_tree
  restart_shell
  wait_helpers 1
  echo "live-check.sh: synced; helper $(helpers)"
  ;;
down)
  [ -e "$backup" ] || die "no check is running ($backup is missing)"
  # Carry on when disable fails: the backup is restored below.
  omarchy plugin disable "$id" || echo "live-check.sh: disable failed; restoring the backup instead" >&2
  rm -rf "$plugin_dir"
  rescan
  if ! cmp -s "$backup" "$config"; then
    # This also reverts bar changes made by hand during the check.
    echo "live-check.sh: shell.json still differs; restoring the backup over these changes:" >&2
    diff -u "$backup" "$config" >&2 || :  # diff exits 1 when it shows a change
    cp "$backup" "$config"
    restart_shell
  fi
  wait_helpers 0
  cmp -s "$backup" "$config" || die "shell.json differs from the backup after restoring it; the backup stays at $backup"
  rm "$backup"
  echo "live-check.sh: down; bar restored, sidepulse.service $(systemctl --user is-active sidepulse.service || true)"
  ;;
*)
  die "usage: scripts/live-check.sh up | sync | down"
  ;;
esac
