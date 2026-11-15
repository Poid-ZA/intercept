"""Tests for GPS route behavior and gps client callback management."""

from routes import gps as gps_routes
from utils.gps import GPSDClient, GPSPosition


def test_gpsd_client_add_callback_deduplicates():
    """Adding the same position callback twice should only register once."""
    client = GPSDClient()

    def callback(_position):
        return None

    client.add_callback(callback)
    client.add_callback(callback)

    assert client._callbacks.count(callback) == 1


def test_gpsd_client_add_sky_callback_deduplicates():
    """Adding the same sky callback twice should only register once."""
    client = GPSDClient()

    def callback(_sky):
        return None

    client.add_sky_callback(callback)
    client.add_sky_callback(callback)

    assert client._sky_callbacks.count(callback) == 1


def test_auto_connect_attaches_callbacks_when_reader_already_running(client, monkeypatch):
    """Auto-connect should re-attach stream callbacks for an already-running reader."""

    class FakeReader:
        is_running = True
        position = None
        sky = None

        def __init__(self):
            self.position_callbacks = []
            self.sky_callbacks = []

        def add_callback(self, callback):
            self.position_callbacks.append(callback)

        def add_sky_callback(self, callback):
            self.sky_callbacks.append(callback)

    reader = FakeReader()
    monkeypatch.setattr(gps_routes, "get_gps_reader", lambda: reader)

    with client.session_transaction() as sess:
        sess["logged_in"] = True

    response = client.post("/gps/auto-connect")
    payload = response.get_json()

    assert response.status_code == 200
    assert payload["status"] == "connected"
    assert reader.position_callbacks == [gps_routes._position_callback]
    assert reader.sky_callbacks == [gps_routes._sky_callback]


def test_auto_connect_falls_back_to_meshtastic_when_no_gpsd_device(client, monkeypatch):
    """With no gpsd and no serial GPS, auto-connect should use a Meshtastic fix if one exists."""
    monkeypatch.setattr(gps_routes, "get_gps_reader", lambda: None)
    monkeypatch.setattr(gps_routes, "is_gpsd_running", lambda *a, **k: False)
    monkeypatch.setattr(gps_routes, "detect_gps_devices", lambda: [])

    external = GPSPosition(latitude=-33.9, longitude=18.4, fix_quality=3, device="meshtastic:!55890aeb")
    monkeypatch.setattr(gps_routes, "get_external_position", lambda: external)

    with client.session_transaction() as sess:
        sess["logged_in"] = True

    response = client.post("/gps/auto-connect")
    payload = response.get_json()

    assert response.status_code == 200
    assert payload["status"] == "connected"
    assert payload["source"] == "meshtastic"
    assert payload["position"]["device"] == "meshtastic:!55890aeb"


def test_auto_connect_unavailable_when_no_gpsd_and_no_meshtastic_fix(client, monkeypatch):
    """With neither gpsd nor a Meshtastic fix, auto-connect should report unavailable."""
    monkeypatch.setattr(gps_routes, "get_gps_reader", lambda: None)
    monkeypatch.setattr(gps_routes, "is_gpsd_running", lambda *a, **k: False)
    monkeypatch.setattr(gps_routes, "detect_gps_devices", lambda: [])
    monkeypatch.setattr(gps_routes, "get_external_position", lambda: None)

    with client.session_transaction() as sess:
        sess["logged_in"] = True

    response = client.post("/gps/auto-connect")
    payload = response.get_json()

    assert response.status_code == 200
    assert payload["status"] == "unavailable"


def test_status_falls_back_to_meshtastic_when_no_gpsd_reader(client, monkeypatch):
    """Status endpoint should report the Meshtastic fix when no gpsd reader is running."""
    monkeypatch.setattr(gps_routes, "get_gps_reader", lambda: None)

    external = GPSPosition(latitude=-33.9, longitude=18.4, fix_quality=3, device="meshtastic:!55890aeb")
    monkeypatch.setattr(gps_routes, "get_external_position", lambda: external)

    with client.session_transaction() as sess:
        sess["logged_in"] = True

    response = client.get("/gps/status")
    payload = response.get_json()

    assert response.status_code == 200
    assert payload["running"] is True
    assert payload["source"] == "meshtastic"
    assert payload["device"] == "meshtastic:!55890aeb"


def test_position_reports_meshtastic_source(client, monkeypatch):
    """Position endpoint should tag the source when falling back to a Meshtastic fix."""
    external = GPSPosition(latitude=-33.9, longitude=18.4, fix_quality=3, device="meshtastic:!55890aeb")
    monkeypatch.setattr(gps_routes, "get_current_position", lambda: external)

    with client.session_transaction() as sess:
        sess["logged_in"] = True

    response = client.get("/gps/position")
    payload = response.get_json()

    assert response.status_code == 200
    assert payload["status"] == "ok"
    assert payload["source"] == "meshtastic"


def test_get_source_defaults_to_auto(client, monkeypatch):
    monkeypatch.setattr(gps_routes, "get_source_preference", lambda: "auto")

    with client.session_transaction() as sess:
        sess["logged_in"] = True

    response = client.get("/gps/source")
    payload = response.get_json()

    assert response.status_code == 200
    assert payload["source"] == "auto"


def test_post_source_updates_preference(client, monkeypatch):
    calls = []
    monkeypatch.setattr(gps_routes, "set_source_preference", lambda source: calls.append(source))
    monkeypatch.setattr(gps_routes, "get_source_preference", lambda: "meshtastic")

    with client.session_transaction() as sess:
        sess["logged_in"] = True

    response = client.post("/gps/source", json={"source": "meshtastic"})
    payload = response.get_json()

    assert response.status_code == 200
    assert payload["status"] == "ok"
    assert payload["source"] == "meshtastic"
    assert calls == ["meshtastic"]


def test_post_source_rejects_invalid_value(client, monkeypatch):
    with client.session_transaction() as sess:
        sess["logged_in"] = True

    response = client.post("/gps/source", json={"source": "bogus"})

    assert response.status_code == 400


def test_auto_connect_forced_meshtastic_ignores_running_gpsd(client, monkeypatch):
    """With source forced to meshtastic, auto-connect should use the mesh fix even if gpsd is running."""

    class FakeReader:
        is_running = True
        position = GPSPosition(latitude=1.0, longitude=2.0, device="gpsd://localhost:2947")
        sky = None

    monkeypatch.setattr(gps_routes, "get_gps_reader", lambda: FakeReader())
    monkeypatch.setattr(gps_routes, "get_source_preference", lambda: "meshtastic")
    external = GPSPosition(latitude=-33.9, longitude=18.4, fix_quality=3, device="meshtastic:!55890aeb")
    monkeypatch.setattr(gps_routes, "get_external_position", lambda: external)

    with client.session_transaction() as sess:
        sess["logged_in"] = True

    response = client.post("/gps/auto-connect")
    payload = response.get_json()

    assert response.status_code == 200
    assert payload["source"] == "meshtastic"
    assert payload["position"]["device"] == "meshtastic:!55890aeb"


def test_auto_connect_forced_gpsd_does_not_fall_back(client, monkeypatch):
    """With source forced to gpsd, auto-connect should not fall back to a Meshtastic fix."""
    monkeypatch.setattr(gps_routes, "get_gps_reader", lambda: None)
    monkeypatch.setattr(gps_routes, "get_source_preference", lambda: "gpsd")
    monkeypatch.setattr(gps_routes, "is_gpsd_running", lambda *a, **k: False)
    monkeypatch.setattr(gps_routes, "detect_gps_devices", lambda: [])
    monkeypatch.setattr(
        gps_routes,
        "get_external_position",
        lambda: GPSPosition(latitude=-33.9, longitude=18.4, device="meshtastic:!55890aeb"),
    )

    with client.session_transaction() as sess:
        sess["logged_in"] = True

    response = client.post("/gps/auto-connect")
    payload = response.get_json()

    assert response.status_code == 200
    assert payload["status"] == "unavailable"


def test_status_forced_gpsd_ignores_meshtastic_fix(client, monkeypatch):
    """With source forced to gpsd, status should not report a Meshtastic fix even if one exists."""
    monkeypatch.setattr(gps_routes, "get_gps_reader", lambda: None)
    monkeypatch.setattr(gps_routes, "get_source_preference", lambda: "gpsd")
    monkeypatch.setattr(
        gps_routes,
        "get_external_position",
        lambda: GPSPosition(latitude=-33.9, longitude=18.4, device="meshtastic:!55890aeb"),
    )

    with client.session_transaction() as sess:
        sess["logged_in"] = True

    response = client.get("/gps/status")
    payload = response.get_json()

    assert response.status_code == 200
    assert payload["running"] is False


def test_satellites_returns_waiting_when_reader_not_running(client, monkeypatch):
    """Satellite endpoint should return a non-error waiting state when reader is down."""
    monkeypatch.setattr(gps_routes, "get_gps_reader", lambda: None)

    with client.session_transaction() as sess:
        sess["logged_in"] = True

    response = client.get("/gps/satellites")
    payload = response.get_json()

    assert response.status_code == 200
    assert payload["status"] == "waiting"
    assert payload["running"] is False
