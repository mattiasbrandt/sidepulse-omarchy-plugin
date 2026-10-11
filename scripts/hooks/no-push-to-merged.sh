#!/usr/bin/env bash
# Claude Code PreToolUse hook (.claude/settings.json): refuse a `git push` from
# a branch whose PR is already merged. `main` takes squash merges, so commits
# pushed there afterwards reach no PR and no CI; they belong on a new branch
# from main. Twice during #10 the user merged a PR while review fixes for it
# were still being pushed.
#
# Reads the hook's JSON on stdin. Prints a deny decision when the current
# branch has a merged PR; otherwise prints nothing and the push goes ahead.
# When the check itself cannot run (no gh, no network), it warns and allows.
set -uo pipefail

input=$(cat)
command=$(jq -r '.tool_input.command // ""' <<<"$input")
cwd=$(jq -r '.cwd // "."' <<<"$input")

case $command in
*"git push"*) ;;
*) exit 0 ;;
esac

warn() {
  echo "no-push-to-merged: $*; allowing the push" >&2
  exit 0
}

branch=$(git -C "$cwd" branch --show-current 2>/dev/null) || warn "not a git checkout"
[ -n "$branch" ] || warn "no current branch"
[ "$branch" != main ] || exit 0

merged=$(cd "$cwd" && gh pr list --head "$branch" --state merged --json number --jq '.[0].number // empty' 2>&1) ||
  warn "gh pr list failed: $merged"
[ -n "$merged" ] || exit 0

# A newer PR reusing the branch name is still a valid target.
open=$(cd "$cwd" && gh pr list --head "$branch" --state open --json number --jq '.[0].number // empty' 2>&1) ||
  warn "gh pr list failed: $open"
[ -z "$open" ] || exit 0

reason="PR #$merged from branch $branch is already merged; pushing here reaches no PR. Branch from main and cherry-pick the commits instead."
jq -n --arg reason "$reason" '{
  hookSpecificOutput: {
    hookEventName: "PreToolUse",
    permissionDecision: "deny",
    permissionDecisionReason: $reason
  }
}'
