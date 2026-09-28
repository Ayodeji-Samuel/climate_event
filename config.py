import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent


def _project_path(value: str) -> str:
    """Resolve a relative path against the project folder — PythonAnywhere's
    WSGI working directory is not necessarily the project directory."""
    path = Path(value)
    return str(path if path.is_absolute() else BASE_DIR / path)


def _env_bool(name: str, default: bool) -> bool:
    return os.environ.get(name, str(default)).strip().lower() in ("1", "true", "yes", "on")


class Config:
    BASE_DIR = BASE_DIR

    # Security
    SECRET_KEY = os.environ.get("SECRET_KEY", "ansasphere-change-me-in-production")

    # Google Earth Engine
    GEE_CREDENTIALS_PATH = _project_path(
        os.environ.get("GEE_CREDENTIALS_PATH", "ee-my-makinde-2b6858cddb01.json")
    )
    GEE_PROJECT = os.environ.get("GEE_PROJECT", "ee-my-makinde")
    # Per-request EE timeout (ms).  PythonAnywhere kills web requests after
    # 300 s, so individual EE calls must give up well before that.
    GEE_DEADLINE_MS = int(os.environ.get("GEE_DEADLINE_MS", "90000"))

    # AI — OpenRouter
    OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")
    OPENROUTER_MODEL = os.environ.get("OPENROUTER_MODEL", "openai/gpt-4o-mini")

    # Cache TTL in seconds
    CACHE_TTL_SHORT = 300       # 5 min  — flood map tiles
    CACHE_TTL_MEDIUM = 1800     # 30 min — flood stats
    CACHE_TTL_LONG = 86400      # 24 hr  — historical data

    # Alert thresholds (fraction of region flooded)
    # Calibrated for country-scale analysis: even large countries have realistic
    # floods in the 0.1–2 % range.  Original 5–50 % values required country-wide
    # catastrophes (e.g. 70 000 km² for Nigeria Watch) and would never trigger.
    FLOOD_WARN_THRESHOLD = 0.001   # 0.1 % → Watch
    FLOOD_ADV_THRESHOLD = 0.003    # 0.3 % → Advisory
    FLOOD_HIGH_THRESHOLD = 0.008   # 0.8 % → Warning
    FLOOD_EMERG_THRESHOLD = 0.015  # 1.5 % → Emergency
    # The fractions above were tuned for whole countries.  In a 20–50 km² LGA
    # a few pixels of SAR noise (0.1 km²) already exceed 0.1 %, so no risk
    # level is assigned until at least this much new water is detected.
    FLOOD_MIN_AREA_KM2 = float(os.environ.get("FLOOD_MIN_AREA_KM2", "1.0"))

    # Flood monitor.  Regions are ids from modules/nigeria_admin.py
    # ("nigeria", "<state>", "<state>/<lga>").  Default: states along the
    # Niger–Benue system hit hardest in the 2012 and 2022 floods.
    MONITOR_INTERVAL = int(os.environ.get("MONITOR_INTERVAL", "3600"))
    MONITOR_REGIONS = [
        r.strip().lower()
        for r in os.environ.get(
            "MONITOR_REGIONS", "kogi,benue,anambra,delta,bayelsa,adamawa"
        ).split(",")
        if r.strip()
    ]
    # Background monitor thread inside the web process.  PythonAnywhere web
    # apps don't support threads — there, run run_monitor.py as a scheduled
    # task instead (see README section in run_monitor.py).
    MONITOR_THREADS_ALLOWED = True
    MONITOR_AUTOSTART = _env_bool("MONITOR_AUTOSTART", False)

    # Visitor statistics (modules/visit_tracker.py).  The GeoIP database is
    # downloaded by scripts/update_geoip.py.
    VISITS_DB_PATH = _project_path(os.environ.get("VISITS_DB_PATH", "data/visits.db"))
    GEOIP_DB_PATH = _project_path(
        os.environ.get("GEOIP_DB_PATH", "data/geoip/dbip-country.mmdb")
    )
    # Day boundaries for daily/weekly/monthly counts — Nigeria (WAT) is UTC+1, no DST
    VISITS_UTC_OFFSET_HOURS = float(os.environ.get("VISITS_UTC_OFFSET_HOURS", "1"))
    # A browser counts as a new visit after this long without loading the site
    VISIT_SESSION_MINUTES = int(os.environ.get("VISIT_SESSION_MINUTES", "30"))

    DEBUG = os.environ.get("DEBUG", "false").lower() == "true"


class DevelopmentConfig(Config):
    DEBUG = True


class ProductionConfig(Config):
    DEBUG = False
    MONITOR_THREADS_ALLOWED = False


config_map = {
    "development": DevelopmentConfig,
    "production": ProductionConfig,
    "default": DevelopmentConfig,
}
