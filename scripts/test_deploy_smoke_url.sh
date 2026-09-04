#!/usr/bin/env bash
#
# Isolated test for deploy.sh's resolve_smoke_url() (#08e44245).
#
# deploy.sh cannot be sourced directly to test this — its bottom dispatch
# (`case "$COMPONENT" in ...) deploy_control_plane ;; ...`) fires
# unconditionally on source and would attempt a real deploy (docker build,
# migrate, restart). Instead this extracts JUST the resolve_smoke_url()
# function body with sed and sources that — the function touches no
# network/docker, only a file read, so this is safe to run anywhere.
#
# Run: bash scripts/test_deploy_smoke_url.sh
# Exits non-zero (with the failing case named) on any assertion failure —
# this IS the red/green control for #08e44245's acceptance criteria, not a
# live two-host deploy (safer, and directly exercises the exact logic that
# was wrong: URL derivation + fail-closed on an undeterminable origin).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEPLOY_SH="$SCRIPT_DIR/deploy.sh"

# Extract resolve_smoke_url() (from its `resolve_smoke_url() {` line through
# its matching top-level `}`) and source only that.
fn_src="$(sed -n '/^resolve_smoke_url() {/,/^}/p' "$DEPLOY_SH")"
if [ -z "$fn_src" ]; then
  echo "FAIL: could not extract resolve_smoke_url() from $DEPLOY_SH — has it been renamed?" >&2
  exit 1
fi
eval "$fn_src"

tmpdir="$(mktemp -d)"
trap 'rm -rf "$tmpdir"' EXIT

fail=0
assert_eq() {
  local desc="$1" expected="$2" actual="$3"
  if [ "$expected" != "$actual" ]; then
    echo "FAIL: $desc — expected [$expected], got [$actual]" >&2
    fail=1
  else
    echo "ok: $desc"
  fi
}

# --- Positive control: tr-ru-vm's own .env -> its own domain -------------
mkdir -p "$tmpdir/ru"
cat > "$tmpdir/ru/.env" <<'EOF'
CORS_ALLOWED_ORIGINS=https://cp.teamrelay.ru
EOF
unset SMOKE_URL
url="$(resolve_smoke_url "$tmpdir/ru")"
assert_eq "RU host resolves its own domain, not EVC's" "https://cp.teamrelay.ru/server/info" "$url"
case "$url" in
  *cp.tr.entire.vc*) echo "FAIL: RU host resolved to the EVC domain — this is the exact bug" >&2; fail=1 ;;
esac

# --- Positive control: tr-relay-vm's own .env -> unchanged behavior ------
mkdir -p "$tmpdir/evc"
cat > "$tmpdir/evc/.env" <<'EOF'
CORS_ALLOWED_ORIGINS=https://cp.tr.entire.vc
EOF
url="$(resolve_smoke_url "$tmpdir/evc")"
assert_eq "EVC host resolves its own domain, behavior unchanged" "https://cp.tr.entire.vc/server/info" "$url"

# --- Comma-separated CORS_ALLOWED_ORIGINS -> first origin wins -----------
mkdir -p "$tmpdir/multi"
cat > "$tmpdir/multi/.env" <<'EOF'
CORS_ALLOWED_ORIGINS=https://cp.teamrelay.ru,https://staging.teamrelay.ru
EOF
url="$(resolve_smoke_url "$tmpdir/multi")"
assert_eq "comma-separated origins: first one wins" "https://cp.teamrelay.ru/server/info" "$url"

# --- RED CONTROL: no .env at all -> fail closed, not a guessed domain ----
mkdir -p "$tmpdir/missing"
if resolve_smoke_url "$tmpdir/missing" >"$tmpdir/out" 2>/dev/null; then
  echo "FAIL: resolve_smoke_url succeeded with no .env present — must fail closed. Got: $(cat "$tmpdir/out")" >&2
  fail=1
else
  echo "ok: missing .env -> fail closed (exit non-zero, no URL printed)"
fi

# --- RED CONTROL: CORS_ALLOWED_ORIGINS='*' (wildcard, not a real origin) -
mkdir -p "$tmpdir/wildcard"
cat > "$tmpdir/wildcard/.env" <<'EOF'
CORS_ALLOWED_ORIGINS=*
EOF
if resolve_smoke_url "$tmpdir/wildcard" >"$tmpdir/out2" 2>/dev/null; then
  echo "FAIL: resolve_smoke_url succeeded on a '*' wildcard origin — must fail closed. Got: $(cat "$tmpdir/out2")" >&2
  fail=1
else
  echo "ok: wildcard CORS_ALLOWED_ORIGINS -> fail closed"
fi

# --- SMOKE_URL override always wins, even with a valid .env present ------
export SMOKE_URL="https://manual-override.example/server/info"
url="$(resolve_smoke_url "$tmpdir/evc")"
assert_eq "SMOKE_URL override wins over .env" "https://manual-override.example/server/info" "$url"
unset SMOKE_URL

# --- Historical regression: the OLD hardcoded default must be gone -------
if grep -q 'SMOKE_URL:-https://cp.tr.entire.vc/server/info' "$DEPLOY_SH"; then
  echo "FAIL: the old hardcoded default is still present in deploy.sh" >&2
  fail=1
else
  echo "ok: old hardcoded default is gone"
fi

if [ "$fail" -ne 0 ]; then
  echo "--- one or more checks FAILED ---" >&2
  exit 1
fi
echo "--- all checks passed ---"
