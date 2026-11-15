# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

INTERCEPT is a web-based Signal Intelligence (SIGINT) platform providing a unified Flask interface for software-defined radio (SDR) tools. It supports pager decoding, 433MHz sensors, ADS-B aircraft tracking, ACARS messaging, WiFi/Bluetooth scanning, satellite tracking, ISS SSTV decoding, AIS vessel tracking, weather satellite imagery (NOAA APT & Meteor LRPT), and Meshtastic mesh networking.

## Common Commands

### Docker (Primary)
```bash
# Build and run (basic profile)
docker compose --profile basic up -d

# Build and run with ADS-B history (Postgres)
docker compose --profile history up -d

# Rebuild after code changes
docker compose --profile basic up -d --build

# Multi-arch build (amd64 + arm64 for RPi)
./build-multiarch.sh
```

### Local Setup (Alternative)
```bash
# First-time setup (interactive wizard with install profiles)
./setup.sh

# Or headless full install
./setup.sh --non-interactive

# Or install specific profiles
./setup.sh --profile=core,weather

# Run with production server (gunicorn + gevent, handles concurrent SSE/WebSocket)
sudo ./start.sh

# Or for quick local dev (Flask dev server)
sudo -E venv/bin/python intercept.py

# Other setup utilities
./setup.sh --health-check      # Verify installation
./setup.sh --postgres-setup    # Set up ADS-B history database
./setup.sh --menu              # Force interactive menu
```

### Testing
```bash
# Run all tests under a memory ceiling (preferred — see note below)
./run-tests.sh

# Run all tests
pytest

# Run specific test file
pytest tests/test_bluetooth.py

# Run with coverage
pytest --cov=routes --cov=utils

# Run a specific test
pytest tests/test_bluetooth.py::test_function_name -v

# Browser smoke test: every mode and dashboard, fails on JS errors
# (needs: pip install playwright && python -m playwright install chromium)
pytest tests/smoke -q
```

**SSE fanout mock-queue leak (fixed 2026-08-14)**: `routes/bluetooth_v2.py`'s
stream endpoint passes `scanner._event_queue` to `utils/sse.py`'s fanout, whose
distributor thread loops on `src.get(timeout=...)`. `test_bluetooth_api.py`'s
`mock_scanner` left that attribute a bare `MagicMock`, so `.get()` returned
instantly instead of blocking and never raised `queue.Empty` — the daemon
thread spun at full CPU, and `unittest.mock` appended a `_Call` to the mock's
call history on every iteration (~110 MB/s, confirmed via `tracemalloc`:
437k blocks at `utils/sse.py:61`). `_fanout_channels` is module-global and
`_run_fanout` was `while True`, so the thread outlived its test and kept
growing for the whole session — the suite reached ~28 GB and tripped the
**global** OOM killer, taking unrelated host processes down with it. Growth was
wall-clock-driven, not test-driven, which is why it looked like whichever test
happened to idle longest (the `time.sleep(duration + 1)` in
`get_tscm_bluetooth_snapshot`) was the leaker. The fixture now uses a real
`queue.Queue` (the part that matters most). The `stop`-event teardown and
autouse `conftest.py` reset from the original fix were dropped when this branch
took upstream's `utils/sse.py` and `tests/conftest.py` during the 2.33.89 merge
(upstream has no teardown). Peak for that file was 2510 MB → 319 MB with the
full fix; re-check peak memory if the suite grows again. Prefer `./run-tests.sh`
(cgroup `MemoryMax=8G`, `MemorySwapMax=0`) so a future runaway dies in its own
cgroup instead of OOMing the host. Do **not** wrap runs in
`git stash push … && pytest && git stash pop` — if the run is killed the pop
never fires and the edits are left orphaned in a stash.

### Linting and Formatting
```bash
# Lint with ruff
ruff check .

# Auto-fix linting issues
ruff check --fix .

# Format with black
black .

# Type checking
mypy .
```

## Architecture

### Entry Points
- `setup.sh` - Menu-driven installer with profile system (wizard, health check, PostgreSQL setup, env configurator, update, uninstall). Sources `.env` on startup via `start.sh`.
- `start.sh` - Production startup script (gunicorn + gevent auto-detection, CLI flags, HTTPS, `.env` sourcing, fallback to Flask dev server)
- `intercept.py` - Direct Flask dev server entry point (quick local development)
- `app.py` - Flask application initialization, global state management, process lifecycle, SSE streaming infrastructure, conditional gevent monkey-patch

### Route Blueprints (routes/)
Each signal type has its own Flask blueprint:
- `pager.py` - POCSAG/FLEX decoding via rtl_fm + multimon-ng
- `sensor.py` - 433MHz IoT sensors via rtl_433
- `adsb.py` - Aircraft tracking via dump1090 (SBS protocol on port 30003)
- `acars.py` - Aircraft datalink messages via acarsdec
- `wifi.py`, `wifi_v2.py` - WiFi scanning (legacy and unified APIs)
- `bluetooth.py`, `bluetooth_v2.py` - Bluetooth scanning (legacy and unified APIs)
- `satellite.py` - Pass prediction using TLE data
- `sstv.py` - ISS SSTV image decoding (pure-Python decoder in `utils/sstv/`)
- `weather_sat.py` - NOAA APT & Meteor LRPT via SatDump
- `ais.py` - AIS vessel tracking and VHF DSC distress monitoring
- `aprs.py` - Amateur packet radio via direwolf
- `rtlamr.py` - Utility meter reading
- `meshtastic.py` - Meshtastic LoRa mesh networking
- `meshcore.py` - Meshcore LoRa mesh networking

### Core Utilities (utils/)

**SDR Abstraction Layer** (`utils/sdr/`):
- `SDRFactory` with factory pattern for multiple SDR types (RTL-SDR, LimeSDR, HackRF, Airspy, SDRPlay)
- Each type has a `CommandBuilder` for generating CLI commands

**Bluetooth Module** (`utils/bluetooth/`):
- Multi-backend: DBus/BlueZ primary, fallback for systems without BlueZ
- `aggregator.py` - Merges observations across time
- `tracker_signatures.py` - 47K+ known tracker fingerprints (AirTag, Tile, SmartTag)
- `heuristics.py` - Behavioral analysis for device classification

**TSCM (Counter-Surveillance)** (`utils/tscm/`):
- `baseline.py` - Snapshot "normal" RF environment
- `detector.py` - Compare current scan to baseline, flag anomalies
- `device_identity.py` - Track devices despite MAC randomization
- `correlation.py` - Cross-reference Bluetooth and WiFi observations

**WiFi Utilities** (`utils/wifi/`):
- Platform-agnostic scanner with parsers for airodump-ng, nmcli, iw, iwlist, airport (macOS)
- `channel_analyzer.py` - Frequency band analysis

**Weather Satellite** (`utils/weather_sat.py`):
- Singleton `WeatherSatDecoder` using SatDump CLI for NOAA APT and Meteor LRPT
- Subprocess management with stdout parsing, image watcher via rglob
- Pass prediction using skyfield TLE data

**SSTV Decoder** (`utils/sstv/`):
- Pure-Python pipeline shared by ISS SSTV and HF SSTV (`sstv_general.py`): rtl_fm audio -> FIR bandpass -> VIS detection -> instantaneous-frequency image decoding (numpy/scipy + Pillow, no external decoder)
- Doppler tracking for ISS passes; singleton pattern, image gallery with timestamped filenames

### Key Patterns

**Server-Sent Events (SSE)**: All real-time features stream via SSE endpoints (`/stream_pager`, `/stream_sensor`, etc.). Pattern uses `queue.Queue` with timeout and keepalive messages. Under gunicorn + gevent, each SSE connection is a lightweight greenlet instead of an OS thread.

**Process Management**: External decoders run as subprocesses with output threads feeding queues. Use `safe_terminate()` for cleanup. Global locks prevent race conditions.

**Data Stores**: `DataStore` class with TTL-based automatic cleanup (WiFi: 10min, Bluetooth: 5min, Aircraft: 5min).

**Input Validation**: Centralized in `utils/validation.py` - always validate frequencies, gains, device indices before spawning processes.

### External Tool Integrations

| Tool | Purpose | Integration |
|------|---------|-------------|
| rtl_fm | FM demodulation | Subprocess, pipes to multimon-ng |
| multimon-ng | Pager decoding | Reads from rtl_fm stdout |
| rtl_433 | 433MHz sensors | JSON output parsing |
| dump1090 | ADS-B decoding | SBS protocol socket (port 30003) |
| acarsdec | ACARS messages | Output parsing |
| airmon-ng/airodump-ng | WiFi scanning | Monitor mode, CSV parsing |
| bluetoothctl/hcitool | Bluetooth | Fallback when DBus unavailable |
| SatDump | Weather satellites | CLI live mode, NOAA APT + Meteor LRPT |
| AIS-catcher | AIS vessel tracking | JSON output parsing |
| direwolf | APRS | TNC modem for packet radio |

### Observer location (where "here" comes from)
One chain answers "where is the receiver", used by every map and every
observer-dependent calculation. In priority order:

1. **Live GPS fix** — gpsd, or a Meshtastic node's onboard GPS. Meshtastic
   positions reach it two ways: `POSITION_APP` packets from the local node
   (`utils/meshtastic.py:_handle_*`), and the device's own nodeDB position read
   on every `get_node_info()` call (`_publish_local_position()`) — the local
   node usually never sends itself a `POSITION_APP` packet, so the nodeDB path
   is what actually fires for a directly-connected node. Both call
   `utils.gps.set_external_position()`. `utils/gps.py:get_current_position()`
   arbitrates between gpsd and external per the `auto|gpsd|meshtastic`
   preference (`/gps/source`).
2. **Configured location** — `INTERCEPT_DEFAULT_LAT/LON`, written by
   `/settings/observer-location`.
3. **Constant** — London, `utils/constants.py`.

`routes/system.py:_get_observer_location()` implements it and serves
`/system/location` (`source: gps|config|default`, plus `gps_source:
meshtastic|gpsd` when live). Fixes older than 15 minutes are treated as stale
and skipped, so an unplugged node doesn't pin every map to a ghost position.

Frontend: every consumer must both *resolve* through `ObserverLocation` and
*re-follow* the fix. Reading it once at page/mode init is not enough on a
mobile station: `observer-location.js` fires `observer-location-changed`
whenever the live fix moves, and the ADS-B / AIS / satellite dashboards, the
SPA (distance calcs + satellite inputs + ground-track reticle), the APRS
dashboard, SSTV and radiosonde all listen for it and re-centre/recompute
(2026-08-14). SSTV and the APRS dashboard previously bypassed the chain
entirely — SSTV read `localStorage.observerLat/Lon`, the APRS dashboard read
`window.INTERCEPT_DEFAULT_LAT/LON` — both now go through
`ObserverLocation.getShared()` (their `resolveObserver()` helpers).
`static/js/core/app.js` has a stale copy of the same observer bootstrap but is
not included by any template — dead file, left in place.

`static/js/core/observer-location.js` polls `/system/location` every
20 s and a live fix **overrides** stored/manual coordinates —
`ObserverLocation.getShared()`/`getForModule()` return it, and every consumer
(ADS-B/AIS/satellite dashboards, weather-sat, SSTV, radiosonde, BT-Locate, the
SPA) follows automatically. It fires the existing `observer-location-changed`
event when the fix moves. Server-side defaults resolve through the same chain
(`routes/satellite.py:_default_observer()`,
`routes/radiosonde.py:_fallback_station_location()`) rather than hardcoding
London or 0/0. **Add new observer-dependent code through these helpers, never a
literal coordinate.**

On `adsb-pi` the same chain also drives dump1090-fa: `gps-location-sync.timer`
rewrites `RECEIVER_LAT/LON` (and the ADS-B Exchange feeder's
`LATITUDE`/`LONGITUDE`) when the fix moves >250 m so SkyAware/tar1090 and the
upstream feed all track the mobile station — see
`/opt/pi-provision/assets/gps-location-sync/`. The Pi's own homepage
(`http://<pi>/`) reads `/system/location` through a lighttpd proxy to :5050
rather than baking coordinates in at install time.

### Frontend Structure
- **UI direction (decided 2026-06-12)**: map-heavy modes get dedicated dashboard
  pages; the SPA in `index.html` keeps text/scan modes. The migration is
  complete — ADS-B, AIS, satellite, APRS, Meshtastic and Meshcore each have a
  dashboard (`/adsb/dashboard`, `/ais/dashboard`, `/satellite/dashboard`,
  `/aprs/dashboard`, `/meshtastic/dashboard`, `/meshcore/dashboard`) served by a
  route on the mode's blueprint. Their nav buttons and welcome-catalog cards
  link to the dashboard, and each path is in `DASHBOARD_NAV_PATHS`
  (`static/js/modes/core-mode-switch.js`) so the SPA tears scans down on
  navigation. Do not re-add these modes to the SPA registry.
- **Dashboards**: `templates/<mode>_dashboard.html` — standalone pages using
  bundled (offline) assets. APRS/Meshtastic/Meshcore share page chrome via
  `static/css/dashboard-shell.css` (linked before each dashboard's own
  `static/css/<mode>_dashboard.css`); ADS-B/AIS/satellite predate it and carry
  their own chrome. Mesh dashboards reuse the existing self-contained
  `static/js/modes/{meshtastic,meshcore}.js` modules unchanged.
- **ADS-B history writer CPU-spin fix (2026-08-13)**: `AdsbHistoryWriter._run()`
  and `AdsbSnapshotWriter._run()` (`utils/adsb_history.py`) computed
  `timeout = max(0.0, FLUSH_INTERVAL - (time.time() - last_flush))`, but
  `last_flush` only advances on a successful non-empty flush — once the queue
  sat empty longer than `FLUSH_INTERVAL`, `timeout` locked at `0` forever and
  `queue.get(timeout=0)` became a non-blocking busy-spin (confirmed via
  `py-spy dump` pegging a CPU core at 100%, parked in gevent's
  `Timeout.start()`). Fixed by blocking with no timeout when `batch` is empty
  (nothing time-based to flush anyway) rather than computing a timeout that
  degenerates to 0. Same bug/fix applied to the Pi's `/opt/intercept` copy —
  see `/opt/pi-provision/CLAUDE.md`'s 2026-08-13 entry, which also has the
  provisioning-overlay fix so a Pi reflash doesn't reintroduce it.
- **Templates**: `templates/index.html` (main SPA), `templates/partials/modes/*.html` (sidebar panels), `templates/partials/nav.html` (global nav)
- **JS Modules**: `static/js/modes/*.js` - IIFE pattern per mode (e.g., `WeatherSat`, `SSTV`, `Meshtastic`)
- **CSS**: `static/css/modes/*.css` - scoped styles per mode, CSS variables for theming (`--bg-card`, `--accent-cyan`, `--font-mono`)
- **Mode Integration**: Each mode is declared once in `static/js/mode-registry.js`
  (label, group, elementId, module, init/destroy hooks, visuals flag). The
  catalog, sidebar toggles, destroy map, visuals list, and init dispatch in
  `templates/index.html` are all derived from it. A new mode additionally needs:
  its partial in `templates/partials/modes/`, entries in the CSS/JS lazy-load
  asset maps in `index.html`, and its include in the partials block.
  `tests/test_mode_registry.py` enforces registry/asset consistency.

### Docker
- `Dockerfile` - Single-stage build with all SDR tools compiled from source (dump1090, AIS-catcher, SatDump, etc.). CMD runs `start.sh` (gunicorn + gevent)
- `docker-compose.yml` - Two profiles: `basic` (standalone) and `history` (with Postgres for ADS-B)
- `build-multiarch.sh` - Multi-arch build script for amd64 + arm64 (RPi5)
- Data persisted via `./data:/app/data` volume mount

### Configuration
- `config.py` - Environment variable support with `INTERCEPT_` prefix (e.g., `INTERCEPT_PORT`, `INTERCEPT_WEATHER_SAT_GAIN`)
- Database: SQLite in `instance/` directory for settings, baselines, history

## Testing Notes

Tests use pytest with extensive mocking of external tools. Key fixtures in `tests/conftest.py`. Mock subprocess calls when testing decoder integration.

### Think Before Coding

Don't assume. Don't hide confusion. Surface tradeoffs.

Before implementing:

State your assumptions explicitly. If uncertain, ask.
If multiple interpretations exist, present them - don't pick silently.
If a simpler approach exists, say so. Push back when warranted.
If something is unclear, stop. Name what's confusing. Ask.
2. Simplicity First

Minimum code that solves the problem. Nothing speculative.

No features beyond what was asked.
No abstractions for single-use code.
No "flexibility" or "configurability" that wasn't requested.
No error handling for impossible scenarios.
If you write 200 lines and it could be 50, rewrite it.
Ask yourself: "Would a senior engineer say this is overcomplicated?" If yes, simplify.

### Surgical Changes

Touch only what you must. Clean up only your own mess.

When editing existing code:

Don't "improve" adjacent code, comments, or formatting.
Don't refactor things that aren't broken.
Match existing style, even if you'd do it differently.
If you notice unrelated dead code, mention it - don't delete it.
When your changes create orphans:

Remove imports/variables/functions that YOUR changes made unused.
Don't remove pre-existing dead code unless asked.
The test: Every changed line should trace directly to the user's request.

### Goal-Driven Execution

Define success criteria. Loop until verified.

Transform tasks into verifiable goals:

"Add validation" → "Write tests for invalid inputs, then make them pass"
"Fix the bug" → "Write a test that reproduces it, then make it pass"
"Refactor X" → "Ensure tests pass before and after"
For multi-step tasks, state a brief plan:

1. [Step] → verify: [check]
2. [Step] → verify: [check]
3. [Step] → verify: [check]

