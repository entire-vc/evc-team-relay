#!/usr/bin/env bash
#
# Isolated test for deploy.sh's resolve_smoke_url() and resolve_expected_billing()
# (#08e44245).
#
# deploy.sh cannot be sourced directly to test this — its bottom dispatch
# (`case "$COMPONENT" in ...) deploy_control_plane ;; ...`) fires
# unconditionally on source and would attempt a real deploy (docker build,
# migrate, restart). Instead this extracts JUST these two function bodies
# with sed and sources them — both touch no network/docker, only a file
# read, so this is safe to run anywhere.
#
# Run: bash scripts/test_deploy_smoke_url.sh
# Exits non-zero (with the failing case named) on any assertion failure —
# this IS the red/green control for #08e44245's acceptance criteria, not a
# live two-host deploy (safer, and directly exercises the exact logic that
# was wrong: URL derivation + fail-closed on an undeterminable origin, and
# — after the first fix bounced (@ralph, DO-NOT-SHIP) — per-host billing
# expectation instead of a hardcoded True).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEPLOY_SH="$SCRIPT_DIR/deploy.sh"

# Extract resolve_smoke_url() and resolve_expected_billing() (each from its
# `<name>() {` line through its matching top-level `}`) and source only those.
fn_src="$(sed -n '/^resolve_smoke_url() {/,/^}/p' "$DEPLOY_SH")"
if [ -z "$fn_src" ]; then
  echo "FAIL: could not extract resolve_smoke_url() from $DEPLOY_SH — has it been renamed?" >&2
  exit 1
fi
eval "$fn_src"

billing_fn_src="$(sed -n '/^resolve_expected_billing() {/,/^}/p' "$DEPLOY_SH")"
if [ -z "$billing_fn_src" ]; then
  echo "FAIL: could not extract resolve_expected_billing() from $DEPLOY_SH — has it been renamed?" >&2
  exit 1
fi
eval "$billing_fn_src"

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

# --- RED CONTROL (@ralph bounce, defect 1): tr-relay-vm's ACTUAL live shape
# — CONTROL_PLANE_PUBLIC_URL set, no CORS/ORIGIN key at all (grep'd live
# 2026-09-04, 72 keys, zero CORS/ORIGIN matches) — is exactly the fixture the
# CORS_ALLOWED_ORIGINS-only version of resolve_smoke_url() failed closed on,
# which would have hard-broken EVC's deploy job on every run. -----------
mkdir -p "$tmpdir/evc-live-shape"
cat > "$tmpdir/evc-live-shape/.env" <<'EOF'
CONTROL_PLANE_PUBLIC_URL=https://cp.tr.entire.vc
DOMAIN_BASE=entire.vc
WEB_PUBLISH_DOMAIN=publish.entire.vc
RELAY_PUBLIC_URL=wss://relay.entire.vc
BILLING_ENABLED=true
EOF
unset SMOKE_URL
url="$(resolve_smoke_url "$tmpdir/evc-live-shape")"
assert_eq "EVC's real .env shape (no CORS key) still resolves via CONTROL_PLANE_PUBLIC_URL" "https://cp.tr.entire.vc/server/info" "$url"

# --- Positive control: tr-ru-vm's own .env -> its own domain, not EVC's --
mkdir -p "$tmpdir/ru"
cat > "$tmpdir/ru/.env" <<'EOF'
CONTROL_PLANE_PUBLIC_URL=https://cp.teamrelay.ru
BILLING_ENABLED=false
EOF
url="$(resolve_smoke_url "$tmpdir/ru")"
assert_eq "RU host resolves its own domain, not EVC's" "https://cp.teamrelay.ru/server/info" "$url"
case "$url" in
  *cp.tr.entire.vc*) echo "FAIL: RU host resolved to the EVC domain — this is the original bug" >&2; fail=1 ;;
esac

# --- Fallback: CONTROL_PLANE_PUBLIC_URL absent -> CORS_ALLOWED_ORIGINS used
mkdir -p "$tmpdir/cors-fallback"
cat > "$tmpdir/cors-fallback/.env" <<'EOF'
CORS_ALLOWED_ORIGINS=https://cp.teamrelay.ru,https://staging.teamrelay.ru
EOF
url="$(resolve_smoke_url "$tmpdir/cors-fallback")"
assert_eq "no CONTROL_PLANE_PUBLIC_URL: falls back to CORS_ALLOWED_ORIGINS, first origin wins" "https://cp.teamrelay.ru/server/info" "$url"

# --- CONTROL_PLANE_PUBLIC_URL wins over CORS_ALLOWED_ORIGINS when both set
mkdir -p "$tmpdir/both"
cat > "$tmpdir/both/.env" <<'EOF'
CONTROL_PLANE_PUBLIC_URL=https://cp.teamrelay.ru
CORS_ALLOWED_ORIGINS=https://some-other-origin.example
EOF
url="$(resolve_smoke_url "$tmpdir/both")"
assert_eq "CONTROL_PLANE_PUBLIC_URL takes priority over CORS_ALLOWED_ORIGINS" "https://cp.teamrelay.ru/server/info" "$url"

# --- RED CONTROL: no .env at all -> fail closed, not a guessed domain ----
mkdir -p "$tmpdir/missing"
if resolve_smoke_url "$tmpdir/missing" >"$tmpdir/out" 2>/dev/null; then
  echo "FAIL: resolve_smoke_url succeeded with no .env present — must fail closed. Got: $(cat "$tmpdir/out")" >&2
  fail=1
else
  echo "ok: missing .env -> fail closed (exit non-zero, no URL printed)"
fi

# --- RED CONTROL: neither key present at all (only unrelated keys) -------
mkdir -p "$tmpdir/neither"
cat > "$tmpdir/neither/.env" <<'EOF'
DOMAIN_BASE=example.com
EOF
if resolve_smoke_url "$tmpdir/neither" >"$tmpdir/out3" 2>/dev/null; then
  echo "FAIL: resolve_smoke_url succeeded with neither key present — must fail closed. Got: $(cat "$tmpdir/out3")" >&2
  fail=1
else
  echo "ok: neither CONTROL_PLANE_PUBLIC_URL nor CORS_ALLOWED_ORIGINS present -> fail closed"
fi

# --- RED CONTROL: CORS_ALLOWED_ORIGINS='*' (wildcard, not a real origin),
#     no CONTROL_PLANE_PUBLIC_URL to fall back TO ------------------------
mkdir -p "$tmpdir/wildcard"
cat > "$tmpdir/wildcard/.env" <<'EOF'
CORS_ALLOWED_ORIGINS=*
EOF
if resolve_smoke_url "$tmpdir/wildcard" >"$tmpdir/out2" 2>/dev/null; then
  echo "FAIL: resolve_smoke_url succeeded on a '*' wildcard origin — must fail closed. Got: $(cat "$tmpdir/out2")" >&2
  fail=1
else
  echo "ok: wildcard CORS_ALLOWED_ORIGINS, no fallback -> fail closed"
fi

# --- SMOKE_URL override always wins, even with a valid .env present ------
export SMOKE_URL="https://manual-override.example/server/info"
url="$(resolve_smoke_url "$tmpdir/evc-live-shape")"
assert_eq "SMOKE_URL override wins over .env" "https://manual-override.example/server/info" "$url"
unset SMOKE_URL

# --- Historical regression: the OLD hardcoded default must be gone -------
if grep -q 'SMOKE_URL:-https://cp.tr.entire.vc/server/info' "$DEPLOY_SH"; then
  echo "FAIL: the old hardcoded default is still present in deploy.sh" >&2
  fail=1
else
  echo "ok: old hardcoded default is gone"
fi

# ============================================================================
# resolve_expected_billing() — #08e44245 defect 2 (@ralph bounce)
# ============================================================================

# --- RED CONTROL reproducing @ralph's defect 2: a hardcoded True would have
# expected billing_enabled=true on RU, where it's deliberately false --------
expected="$(resolve_expected_billing "$tmpdir/ru")"
assert_eq "RU host (BILLING_ENABLED=false) expects False, not the old hardcoded True" "False" "$expected"
if [ "$expected" = "True" ]; then
  echo "FAIL: this is the exact @ralph bounce — a hardcoded True would auto-rollback healthy RU" >&2
  fail=1
fi

# --- Positive control: EVC host (BILLING_ENABLED=true) expects True --------
expected="$(resolve_expected_billing "$tmpdir/evc-live-shape")"
assert_eq "EVC host (BILLING_ENABLED=true) expects True" "True" "$expected"

# --- Absent key -> defaults to False, matching Settings.billing_enabled ----
expected="$(resolve_expected_billing "$tmpdir/neither")"
assert_eq "missing BILLING_ENABLED -> defaults to False (matches app's own default)" "False" "$expected"

# --- Missing .env entirely -> defaults to False, does not error ------------
expected="$(resolve_expected_billing "$tmpdir/missing")"
assert_eq "missing .env entirely -> defaults to False, no error" "False" "$expected"

# --- Case-insensitive truthy values normalize to 'True' ---------------------
mkdir -p "$tmpdir/billing-case"
cat > "$tmpdir/billing-case/.env" <<'EOF'
BILLING_ENABLED=TRUE
EOF
expected="$(resolve_expected_billing "$tmpdir/billing-case")"
assert_eq "BILLING_ENABLED=TRUE (uppercase) normalizes to True" "True" "$expected"

if [ "$fail" -ne 0 ]; then
  echo "--- one or more checks FAILED ---" >&2
  exit 1
fi
echo "--- all checks passed ---"
