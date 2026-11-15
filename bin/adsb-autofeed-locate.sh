#!/bin/bash
# adsb-pi roams across subnets -- no fixed IP to hardcode. Resolves its
# current address from AdGuard's own DHCP lease list (root-readable only;
# this runs as root via systemd, no sudo needed) and re-points INTERCEPT's
# ADS-B tracking at it. If the Pi isn't leased on this LAN right now (off
# roaming elsewhere), exits clean -- the .timer unit retries in 5 min.

set -euo pipefail

LEASES=/opt/adguard/work/data/leases.json
IP="$(python3 -c "
import json
try:
    with open('$LEASES') as f:
        d = json.load(f)
except OSError:
    raise SystemExit(0)
for l in d.get('leases', []):
    if l.get('hostname') == 'adsb-pi':
        print(l.get('ip', ''))
        break
" 2>/dev/null)"

if [ -z "$IP" ]; then
    echo "adsb-autofeed-locate: adsb-pi not leased on this LAN right now, skipping"
    exit 0
fi

SBS_HOST="$IP" exec /opt/intercept/bin/adsb-autofeed.sh
