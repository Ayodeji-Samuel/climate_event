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
    FLOOD_WARN_THRESHOLD = 0.05    # 5 %  → Watch
    FLOOD_ADV_THRESHOLD = 0.15     # 15 % → Advisory
    FLOOD_HIGH_THRESHOLD = 0.30    # 30 % → Warning
    FLOOD_EMERG_THRESHOLD = 0.50   # 50 % → Emergency

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
