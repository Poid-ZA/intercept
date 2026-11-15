"""GPS routes for gpsd daemon support."""

from __future__ import annotations

import queue

from flask import Blueprint, Response, jsonify, request

from utils.gps import (
    VALID_SOURCE_PREFERENCES,
    GPSPosition,
    GPSSkyData,
    add_device_to_gpsd,
    add_external_position_callback,
    detect_gps_devices,
    get_current_position,
    get_external_position,
    get_gps_reader,
    gpsd_devices,
    get_source_preference,
    is_gpsd_running,
    set_source_preference,
    start_gpsd,
    start_gpsd_daemon,
    stop_gps,
    stop_gpsd_daemon,
)
from utils.logging import get_logger
from utils.sse import sse_stream_fanout

logger = get_logger("intercept.gps")

gps_bp = Blueprint("gps", __name__, url_prefix="/gps")

# Queue for SSE position updates
_gps_queue: queue.Queue = queue.Queue(maxsize=100)


def _position_callback(position: GPSPosition) -> None:
    """Callback to queue position updates for SSE stream."""
    try:
        _gps_queue.put_nowait({"type": "position", **position.to_dict()})
    except queue.Full:
        # Discard oldest if queue is full
        try:
            _gps_queue.get_nowait()
            _gps_queue.put_nowait({"type": "position", **position.to_dict()})
        except queue.Empty:
            pass


def _position_source(position: GPSPosition | None) -> str | None:
    """Classify a GPSPosition by where it came from, for the frontend."""
    if position is None:
        return None
    return "meshtastic" if position.device and position.device.startswith("meshtastic:") else "gpsd"


# Stream external (e.g. Meshtastic) position fixes to the SSE queue too, so
# the map updates live even when gpsd is not connected.
add_external_position_callback(_position_callback)


def _sky_callback(sky: GPSSkyData) -> None:
    """Callback to queue sky data updates for SSE stream."""
    try:
        _gps_queue.put_nowait({"type": "sky", **sky.to_dict()})
    except queue.Full:
        try:
            _gps_queue.get_nowait()
            _gps_queue.put_nowait({"type": "sky", **sky.to_dict()})
        except queue.Empty:
            pass


def _meshtastic_connected_response():
    external = get_external_position()
    if not external:
        return None
    return jsonify(
        {
            "status": "connected",
            "source": "meshtastic",
            "has_fix": True,
            "position": external.to_dict(),
            "sky": None,
        }
    )


@gps_bp.route("/source", methods=["GET", "POST"])
def gps_source():
    """Get or set the preferred GPS source: auto (default), gpsd, or meshtastic."""
    if request.method == "POST":
        data = request.get_json(silent=True) or {}
        source = data.get("source")
        if source not in VALID_SOURCE_PREFERENCES:
            return jsonify({"status": "error", "message": f"Invalid source: {source}"}), 400
        set_source_preference(source)

    return jsonify({"status": "ok", "source": get_source_preference()})


@gps_bp.route("/auto-connect", methods=["POST"])
def auto_connect_gps():
    """
    Automatically connect to the preferred GPS source.

    Called on page load to seamlessly enable GPS. With source preference
    "meshtastic", only a Meshtastic node's onboard GPS fix is used. With
    "gpsd", only gpsd is used (no Meshtastic fallback). With the default
    "auto", gpsd is preferred and Meshtastic is used as a fallback.
    """
    pref = get_source_preference()

    if pref == "meshtastic":
        response = _meshtastic_connected_response()
        if response:
            return response
        return jsonify({"status": "unavailable", "message": "No Meshtastic GPS fix available"})

    # Check if already running
    reader = get_gps_reader()
    if reader and reader.is_running:
        # Ensure stream callbacks are attached for this process.
        reader.add_callback(_position_callback)
        reader.add_sky_callback(_sky_callback)
        position = reader.position
        sky = reader.sky
        return jsonify(
            {
                "status": "connected",
                "source": "gpsd",
                "has_fix": position is not None,
                "position": position.to_dict() if position else None,
                "sky": sky.to_dict() if sky else None,
            }
        )

    host = "localhost"
    port = 2947

    # If gpsd isn't running, try to detect a device and start it
    if not is_gpsd_running(host, port):
        devices = detect_gps_devices()
        if not devices:
            if pref == "auto":
                response = _meshtastic_connected_response()
                if response:
                    return response
            return jsonify({"status": "unavailable", "message": "No GPS device detected"})

        # Try to start gpsd with the first detected device
        device_path = devices[0]["path"]
        success, msg = start_gpsd_daemon(device_path, host, port)
        if not success:
            if pref == "auto":
                response = _meshtastic_connected_response()
                if response:
                    return response
            return jsonify(
                {
                    "status": "unavailable",
                    "message": msg,
                    "devices": devices,
                }
            )
        logger.info(f"Auto-started gpsd on {device_path}")
    elif gpsd_devices(host, port) == []:
        # A gpsd with no receiver: systemd's gpsd.socket starts one at boot,
        # before anything tells it where the GPS is. Connecting to it would
        # wait for ever, so give it the receiver we can see, or say why not.
        devices = detect_gps_devices()
        if not devices:
            return jsonify({"status": "unavailable", "message": "gpsd is running but no GPS receiver is attached"})
        device_path = devices[0]["path"]
        added, msg = add_device_to_gpsd(device_path, host, port)
        print(f"[GPS] gpsd had no receiver; {msg}", flush=True)
        if not added:
            hint = (
                f"gpsd is running with no GPS receiver, and {msg}. Run: sudo gpsdctl add {device_path} "
                f'(or set DEVICES="{device_path}" in /etc/default/gpsd and restart gpsd)'
            )
            logger.warning(hint)
            return jsonify({"status": "unavailable", "message": hint, "devices": devices})

    # Clear the queue
    while not _gps_queue.empty():
        try:
            _gps_queue.get_nowait()
        except queue.Empty:
            break

    # Start the gpsd client
    success = start_gpsd(host, port, callback=_position_callback, sky_callback=_sky_callback)

    if success:
        return jsonify(
            {
                "status": "connected",
                "source": "gpsd",
                "has_fix": False,
                "position": None,
                "sky": None,
            }
        )

    if pref == "auto":
        response = _meshtastic_connected_response()
        if response:
            return response
    return jsonify({"status": "unavailable", "message": "Failed to connect to gpsd"})


@gps_bp.route("/devices")
def list_gps_devices():
    """List detected GPS serial devices."""
    devices = detect_gps_devices()
    return jsonify(
        {
            "devices": devices,
            "gpsd_running": is_gpsd_running(),
        }
    )


@gps_bp.route("/stop", methods=["POST"])
def stop_gps_reader():
    """Stop GPS client and gpsd daemon if we started it."""
    reader = get_gps_reader()
    if reader:
        reader.remove_callback(_position_callback)
        reader.remove_sky_callback(_sky_callback)

    stop_gps()
    stop_gpsd_daemon()

    return jsonify({"status": "stopped"})


@gps_bp.route("/status")
def get_gps_status():
    """Get current GPS client status, honoring the source preference."""
    pref = get_source_preference()
    reader = get_gps_reader()

    if pref != "meshtastic" and reader and reader.is_running:
        position = reader.position
        sky = reader.sky
        return jsonify(
            {
                "running": True,
                "source": "gpsd",
                "device": reader.device_path,
                "position": position.to_dict() if position else None,
                "sky": sky.to_dict() if sky else None,
                "last_update": reader.last_update.isoformat() if reader.last_update else None,
                "error": reader.error,
                "message": "Waiting for GPS fix - ensure GPS has clear view of sky" if not position else None,
            }
        )

    if pref != "gpsd":
        external = get_external_position()
        if external:
            return jsonify(
                {
                    "running": True,
                    "source": "meshtastic",
                    "device": external.device,
                    "position": external.to_dict(),
                    "sky": None,
                    "last_update": external.timestamp.isoformat() if external.timestamp else None,
                    "error": None,
                    "message": None,
                }
            )

    return jsonify(
        {
            "running": False,
            "source": None,
            "device": None,
            "position": None,
            "sky": None,
            "error": None,
            "message": "GPS client not started",
        }
    )


@gps_bp.route("/position")
def get_position():
    """Get current GPS position."""
    position = get_current_position()

    if position:
        return jsonify({"status": "ok", "source": _position_source(position), "position": position.to_dict()})
    else:
        reader = get_gps_reader()
        if not reader or not reader.is_running:
            return jsonify({"status": "error", "message": "GPS client not running"}), 400
        else:
            return jsonify({"status": "waiting", "message": "Waiting for GPS fix - ensure GPS has clear view of sky"})


@gps_bp.route("/satellites")
def get_satellites():
    """Get current satellite sky view data."""
    reader = get_gps_reader()

    if not reader or not reader.is_running:
        return jsonify({"status": "waiting", "running": False, "message": "GPS client not running"})

    sky = reader.sky
    if sky:
        return jsonify({"status": "ok", "sky": sky.to_dict()})
    else:
        return jsonify({"status": "waiting", "message": "Waiting for satellite data"})


@gps_bp.route("/stream")
def stream_gps():
    """SSE stream of GPS position and sky updates."""
    response = Response(
        sse_stream_fanout(
            source_queue=_gps_queue,
            channel_key="gps",
            timeout=1.0,
            keepalive_interval=30.0,
        ),
        mimetype="text/event-stream",
    )
    response.headers["Cache-Control"] = "no-cache"
    response.headers["X-Accel-Buffering"] = "no"
    response.headers["Connection"] = "keep-alive"
    return response
