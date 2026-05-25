"""
app.py – ANSASphere Flask application factory.
"""

import logging
import os
from dotenv import load_dotenv
load_dotenv()  # must run before config.py reads os.environ
from flask import Flask, render_template
from flask_cors import CORS

from config import config_map
from modules.gee_engine import GEEEngine
from modules.flood_detector import FloodDetector
from modules.climate_layers import ClimateLayerAnalyzer
from modules.ai_agent import AIAgent
from modules.alert_system import AlertSystem
from api.routes import api_bp

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


def create_app(env: str = "default") -> Flask:
    app = Flask(__name__)
    cfg_class = config_map.get(env, config_map["default"])
    app.config.from_object(cfg_class)

    # ── CORS (all origins in dev; lock down in prod) ──────────────────
    CORS(app, resources={r"/api/*": {"origins": "*"}})

    # ── Earth Engine ──────────────────────────────────────────────────
    gee = GEEEngine(
        credentials_path=app.config["GEE_CREDENTIALS_PATH"],
        project=app.config["GEE_PROJECT"],
    )
    app.gee = gee

    # ── Domain modules ────────────────────────────────────────────────
    flood_cfg = {k: app.config[k] for k in app.config if k.startswith("FLOOD_")}
    detector = FloodDetector(gee, config=flood_cfg)
    app.flood_detector = detector

    alert_system = AlertSystem()
    app.alert_system = alert_system

    climate = ClimateLayerAnalyzer(gee)
    app.climate_layers = climate

    agent = AIAgent(
        flood_detector=detector,
        alert_system=alert_system,
        climate_layers=climate,
        openrouter_api_key=app.config.get("OPENROUTER_API_KEY", ""),
        openrouter_model=app.config.get("OPENROUTER_MODEL", "openai/gpt-4o-mini"),
    )
    app.ai_agent = agent

    # ── Blueprints ────────────────────────────────────────────────────
    app.register_blueprint(api_bp, url_prefix="/api")

    # ── Main route ───────────────────────────────────────────────────
    @app.route("/")
    def index():
        return render_template("index.html")

    # ── Background monitor (non-blocking) ────────────────────────────
    monitor_interval = app.config.get("MONITOR_INTERVAL", 3600)
    agent.start_monitor(interval=monitor_interval)
    logger.info("ANSASphere started. GEE connected: %s", gee.is_connected)

    return app


# ── Entry point ──────────────────────────────────────────────────────────
env = os.environ.get("FLASK_ENV", "development")
app = create_app(env)

if __name__ == "__main__":
    app.run(debug=app.config["DEBUG"], host="0.0.0.0", port=5000)
