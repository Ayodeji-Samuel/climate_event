import os
from pathlib import Path


class Config:
    BASE_DIR = Path(__file__).parent

    # Security
    SECRET_KEY = os.environ.get("SECRET_KEY", "ansasphere-change-me-in-production")

    # Google Earth Engine
    GEE_CREDENTIALS_PATH = os.environ.get(
        "GEE_CREDENTIALS_PATH",
        str(BASE_DIR / "ee-my-makinde-2b6858cddb01.json"),
    )
    GEE_PROJECT = os.environ.get("GEE_PROJECT", "ee-my-makinde")

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

    # Background scheduler intervals (seconds)
    MONITOR_INTERVAL = 3600    # re-run flood checks every hour
    ALERT_INTERVAL = 900       # check & push alerts every 15 min

    DEBUG = os.environ.get("DEBUG", "false").lower() == "true"


class DevelopmentConfig(Config):
    DEBUG = True


class ProductionConfig(Config):
    DEBUG = False


config_map = {
    "development": DevelopmentConfig,
    "production": ProductionConfig,
    "default": DevelopmentConfig,
}
