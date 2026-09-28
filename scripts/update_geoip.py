"""
scripts/update_geoip.py
Download (or refresh) the IP → country database used for visitor statistics.

Database: DB-IP "IP to Country Lite" (CC BY 4.0, https://db-ip.com), fetched
from the jsDelivr mirror of the @ip-location-db/dbip-country-mmdb package —
cdn.jsdelivr.net is on PythonAnywhere's free-account allowlist, while
download.db-ip.com is not.  Upstream updates monthly.

Run once after deploying, then monthly (PythonAnywhere Tasks tab):
  /home/<username>/climate_event/.venv/bin/python /home/<username>/climate_event/scripts/update_geoip.py
The web app picks up the new file within 10 minutes; no reload needed.
"""

import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

from config import Config  # noqa: E402

URL = "https://cdn.jsdelivr.net/npm/@ip-location-db/dbip-country-mmdb/dbip-country.mmdb"


def main() -> int:
    import maxminddb

    dest = Path(Config.GEOIP_DB_PATH)
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".download")

    print(f"Downloading {URL}")
    with requests.get(URL, stream=True, timeout=120) as resp:
        resp.raise_for_status()
        with open(tmp, "wb") as fh:
            for chunk in resp.iter_content(chunk_size=1 << 16):
                fh.write(chunk)

    # Validate before replacing the live file
    try:
        with maxminddb.open_database(str(tmp)) as reader:
            sample = (reader.get("8.8.8.8") or {}).get("country_code")
            build = reader.metadata().build_epoch
        if sample != "US":
            raise ValueError(f"unexpected lookup result for 8.8.8.8: {sample!r}")
    except Exception as exc:
        tmp.unlink(missing_ok=True)
        print(f"Downloaded file is not a valid country database: {exc}")
        return 1

    os.replace(tmp, dest)
    built =datetime.fromtimestamp(build, tz=timezone.utc).strftime("%Y-%m-%d")
    print(f"Saved {dest} ({dest.stat().st_size / 1e6:.1f} MB, built {built})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
