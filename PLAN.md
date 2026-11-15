# PLAN — Wireless-page slow-load fix, generalized (2026-08-13)

## Context

User reported "all pages load slow now" after the APRS/Meshtastic slow-load
fix landed. Root cause of that earlier bug: `meshtastic.js`/`meshcore.js`
eagerly fetched an external tile CDN (with a blocking 5s `Promise.race`
against `Settings.init()`) on every map init, instead of using the
zero-network `MapUtils.init()` canvas-grid-then-upgrade pattern. Fixed
2026-08-12 for those two.

Audit (this session, 2026-08-13) found **the identical bug pattern in three
more mode JS modules**, plus a milder variant in a fourth, all under
`static/js/modes/`:

| File | Pattern | Severity |
|---|---|---|
| `sstv.js` (ISS SSTV) | full bug: eager CDN fetch + blocking 5s `Promise.race(Settings.init(), timeout)` | same as old meshtastic |
| `weather-satellite.js` | same full bug pattern | same, and this file is 92KB (2nd largest mode JS after meshtastic) |
| `websdr.js` | same full bug pattern | same, plus loads `globe.gl` from `cdn.jsdelivr.net` |
| `drone.js` | milder: eager CDN fetch only if `Settings.createTileLayer` not yet ready, no blocking timeout | lower but still non-zero-network |

Also found a **feature-parity gap**: MeshCore never got a dedicated
`/meshcore/dashboard` page, even though Meshtastic (its sibling mesh mode,
same map weight) did on 2026-08-12. That migration note in
`/opt/intercept/CLAUDE.md` literally names "map-heavy modes" as the
criterion — MeshCore qualifies and was missed.

Also found: `weather-satellite.js`/`.html` (92KB JS + 24.5KB template) and
`websdr.js` (38KB JS, plus a 3D globe library + iframe SDR widget) are heavy
enough, by the same size/weight logic that justified ADS-B/AIS/Satellite/
APRS/Meshtastic getting dedicated pages, to be dashboard candidates too.

**Not in scope tonight** (reviewed, deliberately deferred): `bt_locate.js`
(70KB but already uses `MapUtils.init()`, no CDN bug — lower urgency),
`waterfall.js`/`subghz.js` (large but not map-pages, different perf class,
not what "similar to the Meshtastic pages" was pointing at).

## Board

### To Do
(nothing left)

### In Progress
(nothing — all tasks complete)

### Done
- [x] T0 — Audit complete: identified the 4 files with the CDN bug and the
      MeshCore parity gap (this document).
- [x] T1 — `sstv.js` — swapped eager CDN fetch for `MapUtils.init()`.
      Also extended `MapUtils.init()` itself to spread through arbitrary
      extra Leaflet options (`...options` in `map-utils.js`) so per-mode
      options like `worldCopyJump`/`maxBounds` survive the swap — additive,
      backward-compatible change, doesn't alter any existing caller's
      defaults.
- [x] T2 — `weather-satellite.js` — same swap.
- [x] T3 — `websdr.js` — same swap (preserved `maxBounds`/`maxBoundsViscosity`
      via the `MapUtils.init()` passthrough from T1).
- [x] T4 — `drone.js` — same swap (was the milder variant, no blocking
      timeout, but still not zero-network).
      Verified all 4: `node --check` clean, no `cartocdn`/`fastly` string
      remaining, `intercept.service` restarted clean, `/`, and all 4
      `static/js/modes/*.js` files 200 via curl.
- [x] T5 — WebSDR dashboard: `templates/websdr_dashboard.html` (new) +
      `routes/websdr.py` (`/websdr/dashboard` route). Reuses
      `static/js/modes/websdr.js` unmodified. Subagent verified: `node
      --check` on websdr.js clean, `ast.parse` on routes/websdr.py clean,
      `render_template()` smoke test passed both `embedded=True/False`.
      Independently re-verified (orchestrating session): same three checks,
      all pass; template is 9242 chars, references websdr.js.
- [x] T8 — MeshCore dashboard: `templates/meshcore_dashboard.html` (new) +
      `routes/meshcore.py` (`/meshcore/dashboard` route). Trivial reuse case
      — just includes the existing `partials/modes/meshcore.html` partial
      plus unmodified `static/js/modes/meshcore.js`. Subagent + independently
      re-verified (orchestrating session): `node --check` clean, `ast.parse`
      clean, template renders (3462 chars — wrapper only, partial included
      at render time so full size confirmed via subagent's render-test:
      11295/11144 chars for embedded=False/True).
- [x] T7 — SSTV/ISS dashboard: `templates/sstv_dashboard.html` (new) +
      `routes/sstv.py` (`/sstv/dashboard` route). Reuses
      `static/js/modes/sstv.js` unmodified; confirmed no cross-contamination
      with the separate HF-SSTV (`sstv_general`) mode. Subagent + independently
      re-verified (orchestrating session): `node --check` clean, `ast.parse`
      clean, template is 14382 chars and references `js/modes/sstv.js`.
- [x] T6 — Weather Satellite dashboard: `templates/weather_sat_dashboard.html`
      (new) + `routes/weather_sat.py` (`/weather-sat/dashboard` route).
      Reuses `static/js/modes/weather-satellite.js` unmodified. Subagent +
      independently re-verified (orchestrating session): `node --check`
      clean, `ast.parse` clean, template is 21113 chars (orchestrator's
      static-file byte count; subagent's live `render_template()` call,
      which also resolves the included sidebar partial, reported 44856
      chars) and references `weather-satellite.js`.
- [x] Nav wiring — added `/websdr/dashboard`, `/weather-sat/dashboard`,
      `/sstv/dashboard`, `/meshcore/dashboard` href params to all 8
      `mode_item`/`mobile_item` calls (desktop + mobile × 4 modes) in
      `templates/partials/nav.html`. Verified each URL appears exactly
      twice.
- [x] Main-server restart + full smoke test — `intercept.service` restarted
      clean (no tracebacks in `journalctl`). `curl` against `/`,
      `/websdr/dashboard`, `/weather-sat/dashboard`, `/sstv/dashboard`,
      `/meshcore/dashboard` all return 302 (auth redirect), none 500.
      Main-server portion of this goal is now fully complete.
- [x] T9a — Deployed T1-T8 to the Pi (adsb-pi, 192.168.200.28) via the same
      surgical-SSH-Python-edit process used for the earlier APRS/Meshtastic
      deploy: verified every anchor was byte-identical to the pre-edit main
      server content before patching (Pi was still on the old buggy code for
      all 4 CDN-fetch files, confirmed via `grep cartocdn` before touching
      anything), patched `map-utils.js` + `sstv.js` + `weather-satellite.js`
      + `websdr.js` + `drone.js`, appended/inserted the 4 new `/dashboard`
      routes into `routes/{websdr,weather_sat,sstv,meshcore}.py`, scp'd the
      4 new dashboard templates (byte-for-byte size match confirmed against
      the main-server originals), wired all 8 nav.html entries. Verified:
      `ast.parse` clean on all 4 route files, `intercept.service` restarted
      clean (no tracebacks), all 5 dashboard URLs (`/`, `/websdr/dashboard`,
      `/weather-sat/dashboard`, `/sstv/dashboard`, `/meshcore/dashboard`)
      return 302 not 500, no `cartocdn` string left in any of the 4 JS
      files, `top` shows healthy CPU (load 0.43, no busy-spin regression —
      confirms this deploy didn't reintroduce or interact badly with the
      separately-fixed ADS-B-history CPU bug from earlier tonight).
- [x] T9b — Extended the `wireless-dashboards` pi-provision overlay
      (`/opt/pi-provision/assets/wireless-dashboards/apply.py`) to cover all
      of T1-T8, not just the earlier APRS/Meshtastic work: added anchor
      patches for `map-utils.js`'s options-passthrough,
      `sstv.js`/`weather-satellite.js`/`websdr.js`/`drone.js`'s CDN fixes,
      new `_append()` helper + patches for the 4 new routes
      (`websdr.py`/`weather_sat.py`/`sstv.py`/`meshcore.py`, 3 of which
      append at file-end rather than anchor-replace), the 4 new dashboard
      templates copied into the overlay's asset dir, and all 8 new
      nav.html entries (4 desktop + 4 mobile). Verified idempotent twice:
      once against the main server's fully-patched tree (`already present`,
      zero diff) and once against the Pi's fully-patched tree (copied the
      whole overlay dir to `/tmp` on the Pi via scp and ran it there —
      also `already present`, zero diff). A future Pi reflash will now
      pick up the entire night's work automatically.

## Post-deploy bug: MeshCore dashboard layout broken (2026-08-13)

User reported after deploy: Meshtastic dashboard fixed (was a Pi file-sync
issue — `map-utils.js`/`meshtastic.js` had drifted from main server due to
an earlier ad-hoc SSH edit predating the `apply.py` overlay; re-synced,
byte-identical now), but **MeshCore dashboard "looks like shit"**.

Diagnosed root cause: `templates/meshcore_dashboard.html` only loads
`css/modes/meshcore.css`, but the included `partials/modes/meshcore.html`
partial uses several classes not defined there:
- `.mesh-visuals-container` (the dashboard's own outer wrapper class) only
  exists in `meshtastic.css`, a sibling mode's stylesheet — without it the
  whole layout has no flex/sizing rules and collapses. This is the primary
  cause.
- `.signal-details-modal` (Add Contact / Traceroute modals) only in
  `css/core/components.css`, not loaded.
- `.preset-btn`/`.run-btn` (modal buttons) only in `css/index.css` (SPA-only,
  deliberately not loaded by dashboards).
- `.mock-input` has no CSS definition anywhere in the codebase — pre-existing
  in the main SPA too, not a dashboard regression, left alone.

- [x] T10 — Fixed `meshcore_dashboard.html`: added `core/components.css`
      link, inlined `.mesh-visuals-container` (verbatim from `meshtastic.css`)
      and `.preset-btn`/`.run-btn` (verbatim from `index.css` — subagent
      caught that `.run-btn` actually has 2 more properties than the spec
      snippet in its dispatch prompt and used the real full rule instead of
      the truncated one, correctly). Independently re-verified (orchestrating
      session): all 4 additions present in the file, main server / Pi /
      pi-provision overlay copy are all byte-identical (sha256 match, not
      just trusted the subagent's report), both services active, both
      `/meshcore/dashboard` return 302 not 500. `.mock-input` (undefined
      CSS anywhere in the codebase, pre-existing in the main SPA too) was
      correctly left alone as out-of-scope, not a dashboard regression.

## Outcome

All 9 tasks (T1-T9b) complete. Both the main server and the Pi have: the
CDN-eager-fetch fix in 6 JS modules (2 from earlier tonight + 4 from this
round), 6 total dedicated dashboard pages for map-heavy modes (ADS-B/AIS/
Satellite pre-existing, APRS/Meshtastic from earlier tonight, WebSDR/Weather
Satellite/SSTV/MeshCore new this round), and the pi-provision overlay keeps
all of it reflash-safe. The originally-reported "all pages load slow now"
complaint should be resolved — every mode with a map now either has a
zero-network `MapUtils.init()` fallback or, for the heaviest ones, its own
dedicated page that doesn't load alongside the other ~20 SPA modes.

## Verification (per task)
- `node --check <file>.js` where node is available (main server has it; the
  Pi doesn't — `ast.parse`/`py_compile` covers the .py side there instead).
- `grep`/text-search the patched JS for absence of `cartocdn`/`fastly`.
- New dashboard routes: `render_template()` test or a live `curl` against
  `/<mode>/dashboard` expecting 302 (auth redirect) not 500.
- Restart `intercept.service`, confirm clean `journalctl` startup, no new
  tracebacks.
- Each dashboard subagent works on ONE mode only — no shared-file edits
  across subagents (nav.html is the one exception, touched once at the end
  by the orchestrating session, not by subagents, to avoid merge clashes).
