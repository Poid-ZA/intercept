// Shared observer location helper for map-based modules.
// Default: shared location enabled unless explicitly disabled via config.
// A live GPS fix (gpsd or a Meshtastic node's onboard GPS, resolved server-side
// by /system/location) takes priority over a stored or configured location, so
// every map follows the receiver when it moves.
window.ObserverLocation = (function() {
    const SHARED_KEY = 'observerLocation';
    const AIS_KEY = 'ais_observerLocation';
    const LEGACY_LAT_KEY = 'observerLat';
    const LEGACY_LON_KEY = 'observerLon';

    function isSharedEnabled() {
        return window.INTERCEPT_SHARED_OBSERVER_LOCATION !== false;
    }

    function normalize(lat, lon) {
        const latNum = parseFloat(lat);
        const lonNum = parseFloat(lon);
        if (Number.isNaN(latNum) || Number.isNaN(lonNum)) return null;
        if (latNum < -90 || latNum > 90 || lonNum < -180 || lonNum > 180) return null;
        return { lat: latNum, lon: lonNum };
    }

    const DEFAULT_LOCATION = normalize(window.INTERCEPT_DEFAULT_LAT, window.INTERCEPT_DEFAULT_LON)
        || { lat: 51.5074, lon: -0.1278 };

    function parseLocation(raw) {
        if (!raw) return null;
        try {
            const parsed = JSON.parse(raw);
            if (parsed && parsed.lat !== undefined && parsed.lon !== undefined) {
                return normalize(parsed.lat, parsed.lon);
            }
        } catch (e) {}
        return null;
    }

    function readKey(key) {
        return parseLocation(localStorage.getItem(key));
    }

    function readLegacyLatLon() {
        const lat = localStorage.getItem(LEGACY_LAT_KEY);
        const lon = localStorage.getItem(LEGACY_LON_KEY);
        if (lat === null || lon === null) return null;
        return normalize(lat, lon);
    }

    function hasStoredLocation() {
        return !!(readKey(SHARED_KEY) || readKey(AIS_KEY) || readLegacyLatLon());
    }

    // ---- live GPS (gpsd / Meshtastic), resolved by /system/location ----
    const LIVE_POLL_MS = 20000;
    let livePosition = null;   // { lat, lon } or null
    let liveSource = null;     // 'meshtastic' | 'gpsd' | null
    let livePollStarted = false;

    function getLive() {
        return livePosition ? { ...livePosition } : null;
    }

    function getLiveSource() {
        return liveSource;
    }

    function applyLive(data) {
        const normalized = data && data.source === 'gps' ? normalize(data.lat, data.lon) : null;
        const changed = !!normalized !== !!livePosition
            || (normalized && livePosition
                && (normalized.lat !== livePosition.lat || normalized.lon !== livePosition.lon));
        livePosition = normalized;
        liveSource = normalized ? (data.gps_source || 'gpsd') : null;
        if (changed) {
            window.dispatchEvent(new CustomEvent('observer-location-changed', {
                detail: normalized
                    ? { lat: normalized.lat, lon: normalized.lon, source: liveSource }
                    : getShared()
            }));
        }
    }

    function refreshLive() {
        return fetch('/system/location')
            .then(r => (r.ok ? r.json() : null))
            .then(data => { if (data) applyLive(data); })
            .catch(() => {});
    }

    function startLivePolling() {
        if (livePollStarted) return;
        livePollStarted = true;
        refreshLive();
        setInterval(refreshLive, LIVE_POLL_MS);
    }

    function getShared() {
        const live = getLive();
        if (live) return live;

        const current = readKey(SHARED_KEY);
        if (current) return current;

        const legacy = readKey(AIS_KEY) || readLegacyLatLon();
        if (legacy) {
            setShared(legacy);
            return legacy;
        }
        return { ...DEFAULT_LOCATION };
    }

    function setShared(location, options = {}) {
        const normalized = location ? normalize(location.lat, location.lon) : null;
        if (!normalized) return;
        localStorage.setItem(SHARED_KEY, JSON.stringify(normalized));
        if (options.updateLegacy !== false) {
            localStorage.setItem(LEGACY_LAT_KEY, normalized.lat.toString());
            localStorage.setItem(LEGACY_LON_KEY, normalized.lon.toString());
        }
    }

    function getForModule(moduleKey, options = {}) {
        const live = getLive();
        if (live) return live;

        if (isSharedEnabled()) {
            return getShared();
        }
        if (moduleKey) {
            const moduleLocation = readKey(moduleKey);
            if (moduleLocation) return moduleLocation;
        }
        if (options.fallbackToLatLon) {
            const legacy = readLegacyLatLon();
            if (legacy) return legacy;
        }
        return { ...DEFAULT_LOCATION };
    }

    function setForModule(moduleKey, location, options = {}) {
        const normalized = location ? normalize(location.lat, location.lon) : null;
        if (!normalized) return;
        if (isSharedEnabled()) {
            setShared(normalized, options);
            return;
        }
        if (moduleKey) {
            localStorage.setItem(moduleKey, JSON.stringify(normalized));
        } else if (options.fallbackToLatLon) {
            localStorage.setItem(LEGACY_LAT_KEY, normalized.lat.toString());
            localStorage.setItem(LEGACY_LON_KEY, normalized.lon.toString());
        }
    }

    startLivePolling();

    return {
        isSharedEnabled,
        hasStoredLocation,
        getShared,
        setShared,
        getForModule,
        setForModule,
        getLive,
        getLiveSource,
        refreshLive,
        normalize,
        DEFAULT_LOCATION: { ...DEFAULT_LOCATION }
    };
})();
