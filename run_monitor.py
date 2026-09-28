"""
run_monitor.py – run one flood-monitor cycle and exit.

PythonAnywhere web apps can't run background threads, so the automatic
flood monitor runs as a scheduled task instead.  In the Tasks tab add an
hourly task:

  /home/<username>/climate_event/.venv/bin/python /home/<username>/climate_event/run_monitor.py

Regions come from MONITOR_REGIONS in .env (default: Kogi, Benue, Anambra,
Delta, Bayelsa, Adamawa).  Alerts land in data/alerts.db, which the web
app's Alert Center reads, and the run time shows as "Last run" in Settings.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("FLASK_ENV", "production")

from app import app  # noqa: E402


def main() -> int:
    if not app.gee.is_connected:
        print(f"GEE not connected: {app.gee.status.get('error')}")
        return 1
    failures = 0
    for row in app.ai_agent.run_monitor_cycle():
        if row.get("success"):
            print(f"{row['region_id']:24s} risk level {row['risk']}")
        else:
            failures += 1
            print(f"{row['region_id']:24s} FAILED: {row.get('message')}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
