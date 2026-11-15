#!/usr/bin/env bash
# INTERCEPT tool dependencies — PI (200.17) COMPLETION run: rtlamr, dump1090, radiosonde
# Uses TMPDIR=/var/tmp (ext4) — /tmp is 200M tmpfs on this Pi.
set -u
export TMPDIR=/var/tmp
log() { echo "[$(date +%H:%M:%S)] $*"; }
cd /var/tmp || exit 1

# --- AIS-catcher (binary never landed before the wedge; reinstall) ---
log "== AIS-catcher =="
if ! command -v AIS-catcher >/dev/null && ! command -v aiscatcher >/dev/null; then
  tmp=$(mktemp -d)
  git clone --depth 1 -q https://github.com/jvde-github/AIS-catcher.git "$tmp/AIS-catcher" 2>/dev/null \
    && cd "$tmp/AIS-catcher" && mkdir -p build && cd build \
    && cmake .. >/dev/null 2>&1 && make -j2 >/dev/null 2>&1 \
    && sudo install -m 0755 AIS-catcher /usr/local/bin/AIS-catcher && log "AIS-catcher OK" || log "AIS-catcher FAILED"
  cd /var/tmp && rm -rf "$tmp"
else log "AIS-catcher present"; fi

# --- rtlamr (Go) ---
log "== rtlamr =="
if ! command -v rtlamr >/dev/null; then
  GO=go
  if go version 2>/dev/null | grep -qE "go1\.(1[0-9]|2[0-1])\."; then
    log "system Go too old, fetching Go 1.23 tarball"
    if [ ! -x /usr/local/go/bin/go ]; then
      curl -sL -o /var/tmp/go.tgz https://go.dev/dl/go1.23.4.linux-arm64.tar.gz \
        && sudo tar -C /usr/local -xzf /var/tmp/go.tgz && rm -f /var/tmp/go.tgz
    fi
    GO=/usr/local/go/bin/go
  fi
  export GOPATH="${GOPATH:-$HOME/go}" PATH="$HOME/go/bin:$PATH"
  mkdir -p "$GOPATH/bin"
  if $GO install github.com/bemasher/rtlamr@latest 2>/dev/null && [ -f "$GOPATH/bin/rtlamr" ]; then
    sudo ln -sf "$GOPATH/bin/rtlamr" /usr/local/bin/rtlamr && log "rtlamr OK"
  else log "rtlamr FAILED"; fi
else log "rtlamr present"; fi

# --- readsb -> /usr/local/bin/dump1090 ---
log "== dump1090 (readsb) =="
if ! command -v dump1090 >/dev/null; then
  tmp=$(mktemp -d)
  git clone --depth 1 -q https://github.com/wiedehopf/readsb.git "$tmp/dump1090" 2>/dev/null \
    && cd "$tmp/dump1090" \
    && make -j2 RTLSDR=yes >"$tmp/readsb.log" 2>&1 \
    && sudo install -m 0755 readsb /usr/local/bin/dump1090 && log "dump1090 OK" || { log "dump1090 FAILED"; tail -3 "$tmp/readsb.log" 2>/dev/null; }
  cd /var/tmp && rm -rf "$tmp"
else log "dump1090 present"; fi

# --- radiosonde_auto_rx ---
log "== radiosonde_auto_rx =="
if [ ! -f /opt/radiosonde_auto_rx/auto_rx/auto_rx.py ]; then
  tmp=$(mktemp -d)
  if git clone --depth 1 -q https://github.com/projecthorus/radiosonde_auto_rx.git "$tmp/ras" 2>/dev/null; then
    cd "$tmp/ras/auto_rx" || exit 1
    /opt/intercept/venv/bin/pip install -q --no-cache-dir -r requirements.txt semver 2>/dev/null \
      && bash build.sh >/dev/null 2>&1 \
      && sudo mkdir -p /opt/radiosonde_auto_rx/auto_rx \
      && sudo cp -r . /opt/radiosonde_auto_rx/auto_rx/ \
      && sudo chmod +x /opt/radiosonde_auto_rx/auto_rx/auto_rx.py \
      && log "radiosonde_auto_rx OK" || log "radiosonde_auto_rx FAILED"
  fi
  cd /var/tmp && rm -rf "$tmp"
else log "radiosonde_auto_rx present"; fi

log "== PI COMPLETION DONE =="
