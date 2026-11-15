#!/bin/bash
# Points INTERCEPT's ADS-B tracking at an existing readsb (Ultrafeeder) SBS
# TCP port instead of letting it spawn its own dump1090 process. Defaults to
# the local Ultrafeeder readsb (127.0.0.1:31003); set SBS_HOST/SBS_PORT to
# point a remote INTERCEPT instance (e.g. the main host, which has no dongle
# of its own) at the Pi's feed over LAN instead — same read-only SBS tap
# used by fr24feed/piaware/adsbexchange/SkyWatch, just one more passive
# consumer. dump1090-fa retired 2026-08-17 — see pi-provision/CLAUDE.md.
#
# INTERCEPT's /adsb/start state (adsb_using_service) is a plain in-memory
# global — it does not survive an intercept.service restart. This unit
# re-arms it every time intercept.service (re)starts, the same way
# skywatch-adsb-autofeed.service re-arms SkyWatch's module state.
#
# On hosts where INTERCEPT has a real login wall (main host), set
# INTERCEPT_USER/INTERCEPT_PASS and this logs in once and reuses the session
# cookie for the /adsb/start call. On hosts with INTERCEPT_DISABLE_AUTH=true
# (the Pi), leave them unset — no login step is attempted.

set -euo pipefail

SBS_HOST="${SBS_HOST:-127.0.0.1}"
SBS_PORT="${SBS_PORT:-31003}"
INTERCEPT_BASE="${INTERCEPT_BASE:-http://127.0.0.1:5050}"
INTERCEPT_USER="${INTERCEPT_USER:-}"
INTERCEPT_PASS="${INTERCEPT_PASS:-}"
MAX_ATTEMPTS=30
SLEEP_SECS=2
COOKIE_JAR="$(mktemp)"
trap 'rm -f "$COOKIE_JAR"' EXIT

login_if_needed() {
    [ -n "$INTERCEPT_USER" ] || return 0
    # INTERCEPT's login form is CSRF-protected (Flask-WTF) -- fetch the login
    # page first and pull the token out, same as thor-vigil's own Intercept
    # adapter does (app/adapters/intercept_adapter.py's _CSRF_RE).
    local login_page csrf_token
    login_page="$(curl -fsS -m 5 -c "$COOKIE_JAR" "${INTERCEPT_BASE}/login")" || return 1
    csrf_token="$(printf '%s' "$login_page" | sed -n 's/.*name="csrf_token"[^>]*value="\([^"]*\)".*/\1/p' | head -1)"
    [ -n "$csrf_token" ] || { echo "no csrf_token found on login page" >/tmp/intercept-adsb-autofeed.last; return 1; }
    curl -fsS -m 5 -c "$COOKIE_JAR" -b "$COOKIE_JAR" -X POST "${INTERCEPT_BASE}/login" \
        --data-urlencode "username=${INTERCEPT_USER}" \
        --data-urlencode "password=${INTERCEPT_PASS}" \
        --data-urlencode "csrf_token=${csrf_token}" \
        >/tmp/intercept-adsb-autofeed.last 2>&1
}

start_tracking() {
    # 200 = started now, 409 = already tracking (e.g. re-run of this script
    # while intercept.service never restarted) -- both are success here.
    local code
    code="$(curl -sS -m 5 -b "$COOKIE_JAR" -o /tmp/intercept-adsb-autofeed.last -w '%{http_code}' \
        -X POST "${INTERCEPT_BASE}/adsb/start" \
        -H "Content-Type: application/json" \
        -d "{\"remote_sbs_host\":\"${SBS_HOST}\",\"remote_sbs_port\":${SBS_PORT}}")"
    [ "$code" = "200" ] || [ "$code" = "409" ]
}

for attempt in $(seq 1 "$MAX_ATTEMPTS"); do
    if login_if_needed && start_tracking; then
        echo "intercept-adsb-autofeed: tracking started via SBS tap on attempt $attempt"
        exit 0
    fi
    sleep "$SLEEP_SECS"
done

echo "intercept-adsb-autofeed: failed after $MAX_ATTEMPTS attempts, giving up" >&2
cat /tmp/intercept-adsb-autofeed.last >&2 2>/dev/null || true
exit 1
