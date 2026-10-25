#!/usr/bin/env bash
# Isolated test for deploy.sh's require_build_stamp().
# Extracts just that function (deploy.sh dispatches a real deploy on source).
# Run: bash scripts/test_deploy_build_stamp.sh
set -u
DIR="$(cd "$(dirname "$0")" && pwd)"
die() { echo "die: $*" >&2; return 1; }
eval "$(sed -n '/^require_build_stamp() {/,/^}/p' "$DIR/deploy.sh")"

t="$(mktemp -d)"; trap 'rm -rf "$t"' EXIT
fail=0
expect() { # name want(0|1) content|MISSING
  local f="$t/stamp"; rm -f "$f"
  [ "$3" = MISSING ] || printf '%s\n' "$3" > "$f"
  ( require_build_stamp "$f" ) >/dev/null 2>&1; local rc=$?
  if [ "$rc" -ne "$2" ]; then echo "FAIL: $1 (rc=$rc want=$2)"; fail=1; else echo "ok: $1"; fi
}
expect "missing file refused" 1 MISSING
expect "unknown refused" 1 unknown
expect "empty refused" 1 ""
expect "short sha refused" 1 12573f6
expect "41 hex refused" 1 12573f6aa439dd1b8ae1e6bb4bd5a09499afbf18a
expect "full sha accepted" 0 12573f6aa439dd1b8ae1e6bb4bd5a09499afbf18
exit "$fail"
