"""
api/routes.py
All REST endpoints for ANSASphere.
Module instances are retrieved from Flask's current_app.
"""

import json
import logging
import time
from datetime import datetime
from flask import (
    Blueprint, current_app, jsonify, request, Response, stream_with_context
)

logger = logging.getLogger(__name__)

api_bp = Blueprint("api", __name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _gee():
    return current_app.gee


def _detector():
    return current_app.flood_detector


def _climate():
    return current_app.climate_layers


def _agent():
    return current_app.ai_agent


def _alerts():
    return current_app.alert_system


def _ok(data: dict, status: int = 200):
    return jsonify({"status": "ok", **data}), status


def _err(message: str, status: int = 400):
    return jsonify({"status": "error", "message": message}), status


# ---------------------------------------------------------------------------
# Health / System
# ---------------------------------------------------------------------------

@api_bp.get("/health")
def health():
    gee_status = _gee().status
    agent_status = _agent().monitor_status
    alert_stats = _alerts().stats()
    return _ok({
        "service": "ANSASphere",
        "version": "1.0.0",
        "timestamp": datetime.utcnow().isoformat(),
        "gee": gee_status,
        "agent": agent_status,
        "alerts": alert_stats,
    })


@api_bp.get("/gee/status")
def gee_status():
    return _ok({"gee": _gee().status})


@api_bp.post("/gee/reconnect")
def gee_reconnect():
    _gee().reconnect()
    return _ok({"gee": _gee().status})


# ---------------------------------------------------------------------------
# Regions
# ---------------------------------------------------------------------------

@api_bp.get("/flood/regions")
def flood_regions():
    return _ok({"regions": _detector().list_regions()})


# ---------------------------------------------------------------------------
# Flood analysis
# ---------------------------------------------------------------------------

@api_bp.get("/flood/analyze")
def flood_analyze():
    """
    Query params:
      region_id  – one of the known region IDs  (default: nigeria)
      date       – ISO date YYYY-MM-DD           (default: today)
      bbox       – west,south,east,north         (optional, overrides region bbox)
    """
    region_id = request.args.get("region_id", "nigeria")
    date = request.args.get("date", datetime.utcnow().strftime("%Y-%m-%d"))

    bbox = None
    raw_bbox = request.args.get("bbox")
    if raw_bbox:
        try:
            bbox = [float(v) for v in raw_bbox.split(",")]
            if len(bbox) != 4:
                return _err("bbox must be 4 comma-separated floats: west,south,east,north")
        except ValueError:
            return _err("Invalid bbox format")

    result = _detector().analyze(region_id=region_id, event_date=date, bbox=bbox)
    return _ok({"result": result})


@api_bp.get("/flood/water-layer")
def flood_water_layer():
    """Return JRC permanent water tile URL for a region."""
    region_id = request.args.get("region_id", "nigeria")
    result = _detector().get_permanent_water_tiles(region_id)
    return _ok(result)


# ---------------------------------------------------------------------------
# Alerts
# ---------------------------------------------------------------------------

@api_bp.get("/alerts")
def list_alerts():
    active_only = request.args.get("active", "true").lower() == "true"
    if active_only:
        alerts = _alerts().get_active()
    else:
        limit = min(int(request.args.get("limit", 100)), 500)
        alerts = _alerts().get_all(limit=limit)
    return _ok({"alerts": alerts, "count": len(alerts)})


@api_bp.post("/alerts")
def create_alert():
    data = request.get_json(force=True, silent=True) or {}
    region = data.get("region", "").strip()
    level = int(data.get("level", 1))
    message = data.get("message", "").strip()

    if not region:
        return _err("'region' is required")
    if not message:
        return _err("'message' is required")
    if not 0 <= level <= 4:
        return _err("'level' must be 0–4")

    alert = _alerts().create(region=region, level=level, message=message)
    return _ok({"alert": alert}, status=201)


@api_bp.patch("/alerts/<int:alert_id>/resolve")
def resolve_alert(alert_id: int):
    success = _alerts().resolve(alert_id)
    if not success:
        return _err("Alert not found", 404)
    return _ok({"resolved": alert_id})


@api_bp.delete("/alerts/<int:alert_id>")
def delete_alert(alert_id: int):
    _alerts().delete(alert_id)
    return _ok({"deleted": alert_id})


@api_bp.get("/alerts/stats")
def alert_stats():
    return _ok(_alerts().stats())


# ---------------------------------------------------------------------------
# AI Agent
# ---------------------------------------------------------------------------

@api_bp.post("/ai/chat")
def ai_chat():
    data = request.get_json(force=True, silent=True) or {}
    message = (data.get("message") or "").strip()
    session_id = data.get("session_id", "default")

    if not message:
        return _err("'message' is required")

    response = _agent().chat(message, session_id=session_id)
    return _ok({"response": response})


@api_bp.get("/ai/status")
def ai_status():
    return _ok({"agent": _agent().monitor_status})


@api_bp.post("/ai/monitor/start")
def start_monitor():
    data = request.get_json(force=True, silent=True) or {}
    regions = data.get("regions")
    interval = int(data.get("interval", 3600))
    _agent().start_monitor(regions=regions, interval=interval)
    return _ok({"message": "Monitor started", "status": _agent().monitor_status})


@api_bp.post("/ai/monitor/stop")
def stop_monitor():
    _agent().stop_monitor()
    return _ok({"message": "Monitor stopped"})


# ---------------------------------------------------------------------------
# Server-Sent Events — real-time push to frontend
# ---------------------------------------------------------------------------

@api_bp.get("/events/stream")
def event_stream():
    """
    SSE endpoint.  Pushes system status every 30 s and alert changes.
    Clients connect once; no polling needed.
    """
    def generate():
        last_alert_count = -1
        tick = 0
        while True:
            try:
                # Every 30 s: system heartbeat
                gee_ok = _gee().is_connected
                active_alerts = _alerts().get_active()
                alert_count = len(active_alerts)
                high_alerts = [a for a in active_alerts if a.get("level", 0) >= 3]

                payload = {
                    "type": "heartbeat",
                    "tick": tick,
                    "timestamp": datetime.utcnow().isoformat(),
                    "gee_connected": gee_ok,
                    "active_alerts": alert_count,
                    "high_alerts": len(high_alerts),
                }
                yield f"data: {json.dumps(payload)}\n\n"

                # Push alert diff if count changed
                if alert_count != last_alert_count:
                    alert_payload = {
                        "type": "alerts_update",
                        "alerts": active_alerts[:10],  # top 10
                        "count": alert_count,
                    }
                    yield f"data: {json.dumps(alert_payload)}\n\n"
                    last_alert_count = alert_count

                tick += 1
                time.sleep(30)

            except GeneratorExit:
                break
            except Exception as exc:
                logger.warning("SSE error: %s", exc)
                break

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",   # important for Nginx/PythonAnywhere
        },
    )


# ---------------------------------------------------------------------------
# Climate Layers  (15 environmental layers)
# ---------------------------------------------------------------------------

@api_bp.get("/climate/layers")
def climate_layers():
    """Return the full catalogue of available environmental layers."""
    return _ok({"layers": _climate().list_layers()})


@api_bp.get("/climate/analyze")
def climate_analyze():
    """
    Query params:
      layer_id   – one of the 15 layer IDs  (required)
      region_id  – known region ID          (default: nigeria)
      date       – ISO date YYYY-MM-DD      (default: today)
      bbox       – west,south,east,north    (optional, overrides region bbox)
      no_cache   – '1' to bypass cache      (default: use cache)
    """
    layer_id  = request.args.get("layer_id", "").strip()
    region_id = request.args.get("region_id", "nigeria").strip()
    date      = request.args.get("date", datetime.utcnow().strftime("%Y-%m-%d"))
    no_cache  = request.args.get("no_cache", "0") == "1"

    if not layer_id:
        return _err("'layer_id' is required. Use GET /api/climate/layers to list options.")

    bbox = None
    raw_bbox = request.args.get("bbox")
    if raw_bbox:
        try:
            bbox = [float(v) for v in raw_bbox.split(",")]
            if len(bbox) != 4:
                return _err("bbox must be 4 floats: west,south,east,north")
        except ValueError:
            return _err("Invalid bbox format")

    if not _gee().is_connected:
        return _ok({
            "result": {
                "success": False,
                "message": "GEE not connected",
                "layer_id": layer_id,
                "region_id": region_id,
                "date": date,
                "tile_url": None,
                "stats": {},
            }
        })

    result = _climate().analyze(
        layer_id=layer_id,
        region_id=region_id,
        date=date,
        bbox=bbox,
        use_cache=not no_cache,
    )
    return _ok({"result": result})


@api_bp.get("/climate/multi")
def climate_multi():
    """
    Run several layers at once for a single region.
    Query params:
      layers    – comma-separated layer IDs (e.g. vegetation,fires,temperature)
      region_id – known region ID
      date      – ISO date YYYY-MM-DD
    Returns a dict keyed by layer_id.
    """
    raw_layers = request.args.get("layers", "")
    region_id  = request.args.get("region_id", "nigeria").strip()
    date       = request.args.get("date", datetime.utcnow().strftime("%Y-%m-%d"))

    layer_ids = [l.strip() for l in raw_layers.split(",") if l.strip()]
    if not layer_ids:
        return _err("'layers' must be a comma-separated list of layer IDs.")
    if len(layer_ids) > 6:
        return _err("Maximum 6 layers per multi-request to avoid GEE quota issues.")

    if not _gee().is_connected:
        return _ok({
            "results": {lid: {"success": False, "message": "GEE not connected"}
                        for lid in layer_ids}
        })

    results = {}
    for lid in layer_ids:
        results[lid] = _climate().analyze(
            layer_id=lid, region_id=region_id, date=date
        )
    return _ok({"results": results, "region_id": region_id, "date": date})


@api_bp.get("/climate/cache/stats")
def climate_cache_stats():
    """Return cache memory usage in bytes and entry counts."""
    cache = _gee().cache
    return _ok({
        "compressed_bytes": cache.size,
        "compressed_kb": round(cache.size / 1024, 2),
    })
