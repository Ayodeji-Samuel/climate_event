"""
api/routes.py
All REST endpoints for ANSASphere.
Module instances are retrieved from Flask's current_app.

Regions are Nigeria, its 37 states and 774 LGAs (modules/nigeria_admin.py):
  region_id = "nigeria" | "<state>" | "<state>/<lga>"   e.g. "lagos/surulere"
Boundary GeoJSON is served as static files from /static/geo/nigeria/.
"""

import logging
import re
from datetime import datetime
from flask import Blueprint, current_app, jsonify, request

from modules.nigeria_admin import ADMIN
from modules.visit_tracker import PERIODS

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


def _visits():
    return current_app.visit_tracker


def _ok(data: dict, status: int = 200):
    return jsonify({"status": "ok", **data}), status


def _err(message: str, status: int = 400):
    return jsonify({"status": "error", "message": message}), status


def _int_arg(value, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _region_arg():
    """(region_id, error_response) for the ?region_id= query param."""
    region_id = (request.args.get("region_id") or "nigeria").strip().lower()
    if ADMIN.get(region_id) is None:
        return region_id, _err(
            f"Unknown region '{region_id}'. Use 'nigeria', a state id (e.g. 'lagos') "
            "or 'state/lga' (e.g. 'lagos/surulere'); see GET /api/regions.")
    return region_id, None


def _bbox_arg():
    """(bbox list | None, error_response)."""
    raw_bbox = request.args.get("bbox")
    if not raw_bbox:
        return None, None
    try:
        bbox = [float(v) for v in raw_bbox.split(",")]
    except ValueError:
        return None, _err("Invalid bbox format")
    if len(bbox) != 4:
        return None, _err("bbox must be 4 comma-separated floats: west,south,east,north")
    return bbox, None


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
        "version": "1.1.0",
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
# Regions  (Nigeria → states → LGAs)
# ---------------------------------------------------------------------------

@api_bp.get("/regions")
def regions():
    """Country + the 37 states, each with its LGA count."""
    return _ok({
        "country": ADMIN.country.to_dict(),
        "states": [{**s.to_dict(), "lga_count": len(ADMIN.lgas(s.id))}
                   for s in ADMIN.states()],
    })


@api_bp.get("/regions/<state_id>/lgas")
def region_lgas(state_id: str):
    state = ADMIN.get(state_id)
    if state is None or state.level != "state":
        return _err(f"Unknown state '{state_id}'", 404)
    return _ok({"state": state.to_dict(),
                "lgas": [l.to_dict() for l in ADMIN.lgas(state.id)]})


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
      region_id  – nigeria | <state> | <state>/<lga>   (default: nigeria)
      date       – ISO date YYYY-MM-DD                  (default: today)
      bbox       – west,south,east,north                (optional, overrides region)
    """
    region_id, error = _region_arg()
    if error:
        return error
    bbox, error = _bbox_arg()
    if error:
        return error
    date = request.args.get("date") or datetime.utcnow().strftime("%Y-%m-%d")

    use_cache = request.args.get("no_cache", "0") != "1"
    result = _detector().analyze(region_id=region_id, event_date=date, bbox=bbox, use_cache=use_cache)
    return _ok({"result": result})


@api_bp.get("/flood/water-layer")
def flood_water_layer():
    """Return JRC permanent water tile URL for a region."""
    region_id, error = _region_arg()
    if error:
        return error
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
        limit = max(1, min(_int_arg(request.args.get("limit"), 100), 500))
        alerts = _alerts().get_all(limit=limit)
    return _ok({"alerts": alerts, "count": len(alerts)})


@api_bp.post("/alerts")
def create_alert():
    data = request.get_json(force=True, silent=True) or {}
    region = str(data.get("region", "")).strip()
    level = _int_arg(data.get("level", 1), -1)
    message = str(data.get("message", "")).strip()

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
    if not _alerts().delete(alert_id):
        return _err("Alert not found", 404)
    return _ok({"deleted": alert_id})


@api_bp.get("/alerts/stats")
def alert_stats():
    return _ok(_alerts().stats())


# ---------------------------------------------------------------------------
# Visitor statistics  (visits per country)
# ---------------------------------------------------------------------------

_VISIT_COOKIE = "ansa_visit"
_BOT_UA = re.compile(
    r"bot|crawl|spider|slurp|headless|lighthouse|preview|curl|wget|python-requests", re.I)


def _client_ip() -> str:
    # PythonAnywhere's load balancer puts the real client address in
    # X-Real-IP (X-Forwarded-For can be forged by the client).  Locally
    # there is no proxy, so fall back to the socket address.
    return (request.headers.get("X-Real-IP") or request.remote_addr or "").strip()


@api_bp.post("/visits")
def record_visit():
    """
    Sent by the page once it has loaded (crawlers that don't run JavaScript
    never call it).  A browser is counted once per VISIT_SESSION_MINUTES of
    activity — the cookie only marks "already counted" and holds no
    identifier.  Only the visitor's country is stored, never the IP.
    """
    country = None
    if (request.cookies.get(_VISIT_COOKIE) is None
            and not _BOT_UA.search(request.user_agent.string or "")):
        country = _visits().record(_client_ip())
    resp, status = _ok({"counted": country is not None, "country": country})
    # Refreshed on every page load, so the session lasts while the visitor is active
    resp.set_cookie(_VISIT_COOKIE, "1",
                    max_age=current_app.config["VISIT_SESSION_MINUTES"] * 60,
                    httponly=True, samesite="Lax")
    return resp, status


@api_bp.get("/visits/stats")
def visit_stats():
    """
    Query params:
      period – day | week | month | all   (default: week)
               today, last 7 days, last 30 days, all time — in local time
    """
    period = (request.args.get("period") or "week").strip().lower()
    if period not in PERIODS:
        return _err(f"'period' must be one of: {', '.join(PERIODS)}")
    return _ok({**_visits().stats(period),
                "geoip": _visits().geoip_status,
                "session_minutes": current_app.config["VISIT_SESSION_MINUTES"]})


# ---------------------------------------------------------------------------
# AI Agent
# ---------------------------------------------------------------------------

@api_bp.post("/ai/chat")
def ai_chat():
    data = request.get_json(force=True, silent=True) or {}
    message = (data.get("message") or "").strip()
    session_id = str(data.get("session_id") or "default")[:100]

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
    interval = max(60, _int_arg(data.get("interval"), current_app.config["MONITOR_INTERVAL"]))
    if not _agent().start_monitor(regions=regions, interval=interval):
        return _err(
            "Background threads are disabled on this server (PythonAnywhere doesn't "
            "support them in web apps). The monitor runs as a scheduled task: "
            "run_monitor.py.", 409)
    return _ok({"message": "Monitor started", "status": _agent().monitor_status})


@api_bp.post("/ai/monitor/stop")
def stop_monitor():
    _agent().stop_monitor()
    return _ok({"message": "Monitor stopped"})


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
      layer_id   – one of the 15 layer IDs                (required)
      region_id  – nigeria | <state> | <state>/<lga>      (default: nigeria)
      date       – ISO date YYYY-MM-DD                    (default: today)
      bbox       – west,south,east,north                  (optional, overrides region)
      no_cache   – '1' to bypass cache                    (default: use cache)
    """
    layer_id  = request.args.get("layer_id", "").strip()
    date      = request.args.get("date") or datetime.utcnow().strftime("%Y-%m-%d")
    no_cache  = request.args.get("no_cache", "0") == "1"

    if not layer_id:
        return _err("'layer_id' is required. Use GET /api/climate/layers to list options.")
    region_id, error = _region_arg()
    if error:
        return error
    bbox, error = _bbox_arg()
    if error:
        return error

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
      region_id – nigeria | <state> | <state>/<lga>
      date      – ISO date YYYY-MM-DD
    Returns a dict keyed by layer_id.
    """
    raw_layers = request.args.get("layers", "")
    date       = request.args.get("date") or datetime.utcnow().strftime("%Y-%m-%d")
    region_id, error = _region_arg()
    if error:
        return error

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


@api_bp.get("/climate/trend")
def climate_trend():
    """
    Compute a 12-month time series + 2 correlated layer series for a layer.

    Query params:
      layer_id   – one of the 15 layer IDs              (required)
      region_id  – nigeria | <state> | <state>/<lga>    (default: nigeria)
      date       – ISO date YYYY-MM-DD                  (default: today)
      months     – number of months                     (default: 12, range: 3–24)

    Returns:
      { result: { success, primary: {label, unit, series},
                  correlates: [{id, label, unit, series, correlation, description}],
                  insight: str } }
    """
    layer_id  = request.args.get("layer_id", "").strip()
    date      = request.args.get("date") or datetime.utcnow().strftime("%Y-%m-%d")
    months    = max(3, min(24, _int_arg(request.args.get("months"), 12)))

    if not layer_id:
        return _err("'layer_id' is required.")
    region_id, error = _region_arg()
    if error:
        return error

    if not _gee().is_connected:
        return _ok({"result": {"success": False, "message": "GEE not connected"}})

    result = _climate().get_trend(
        layer_id=layer_id,
        region_id=region_id,
        date=date,
        months=months,
    )
    return _ok({"result": result})
