"""
modules/visit_tracker.py
Counts site visits per country, aggregated by day.

Privacy: only (day, country, count) is stored — never IP addresses.  The
visitor's IP is resolved to an ISO 3166-1 alpha-2 country code in-process
from a local DB-IP Lite database (CC BY 4.0), so it never leaves the server.
The database is downloaded by scripts/update_geoip.py; without it visits are
still counted, under the unknown country "ZZ".

"Days" follow a fixed UTC offset (default UTC+1, Nigeria/WAT, no DST) so
"today" matches the local calendar rather than UTC.
"""

import logging
import os
import sqlite3
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

UNKNOWN_COUNTRY = "ZZ"
PERIODS = {
    # period → (label, days incl. today; None = all time)
    "day":   ("Today", 1),
    "week":  ("Last 7 days", 7),
    "month": ("Last 30 days", 30),
    "all":   ("All time", None),
}
GEOIP_RECHECK_SECONDS = 600   # pick up a newly downloaded/updated database


class VisitTracker:
    def __init__(self, db_path: str, geoip_path: str, utc_offset_hours: float = 1.0):
        self.db_path = str(db_path)
        self.geoip_path = str(geoip_path)
        self.tz = timezone(timedelta(hours=utc_offset_hours))
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)

        self._reader = None
        self._reader_mtime = None
        self._next_geoip_check = 0.0
        self._geoip_lock = threading.Lock()

        with self._conn() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS visit_counts (
                    day     TEXT    NOT NULL,   -- YYYY-MM-DD, local time
                    country TEXT    NOT NULL,   -- ISO alpha-2, 'ZZ' = unknown
                    visits  INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (day, country)
                )
                """
            )

    @contextmanager
    def _conn(self):
        conn = sqlite3.connect(self.db_path, timeout=10)
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # GeoIP
    # ------------------------------------------------------------------

    def _geoip_reader(self):
        """Open (or re-open after an update) the country database.  Checked
        at most every GEOIP_RECHECK_SECONDS so running scripts/update_geoip.py
        takes effect without reloading the web app."""
        now = time.monotonic()
        if now < self._next_geoip_check:
            return self._reader
        with self._geoip_lock:
            if now < self._next_geoip_check:
                return self._reader
            self._next_geoip_check = now + GEOIP_RECHECK_SECONDS
            try:
                mtime = os.path.getmtime(self.geoip_path)
            except OSError:
                if self._reader is None:
                    logger.warning("GeoIP database not found at %s — run scripts/update_geoip.py",
                                   self.geoip_path)
                return self._reader
            if mtime != self._reader_mtime:
                try:
                    import maxminddb
                    old, self._reader = self._reader, maxminddb.open_database(self.geoip_path)
                    self._reader_mtime = mtime
                    if old is not None:
                        old.close()
                    logger.info("GeoIP database loaded (%s)", self.geoip_path)
                except Exception as exc:
                    logger.error("Could not open GeoIP database %s: %s", self.geoip_path, exc)
            return self._reader

    def country_for(self, ip: Optional[str]) -> str:
        reader = self._geoip_reader()
        if not ip or reader is None:
            return UNKNOWN_COUNTRY
        try:
            record = reader.get(ip.strip())
        except ValueError:   # not an IP address
            return UNKNOWN_COUNTRY
        code = (record or {}).get("country_code")
        if not code:   # private / reserved ranges (e.g. localhost)
            return UNKNOWN_COUNTRY
        return str(code).upper()

    @property
    def geoip_status(self) -> Dict:
        reader = self._geoip_reader()
        if reader is None:
            return {"available": False, "source": "DB-IP Lite"}
        built = datetime.fromtimestamp(reader.metadata().build_epoch, tz=timezone.utc)
        return {"available": True, "source": "DB-IP Lite", "build_date": built.strftime("%Y-%m-%d")}

    # ------------------------------------------------------------------
    # Recording
    # ------------------------------------------------------------------

    def today(self) -> datetime:
        return datetime.now(self.tz)

    def record(self, ip: Optional[str]) -> str:
        """Count one visit for *ip*'s country today; returns the country code."""
        country = self.country_for(ip)
        day = self.today().strftime("%Y-%m-%d")
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO visit_counts (day, country, visits) VALUES (?, ?, 1) "
                "ON CONFLICT(day, country) DO UPDATE SET visits = visits + 1",
                (day, country),
            )
        return country

    # ------------------------------------------------------------------
    # Statistics
    # ------------------------------------------------------------------

    def stats(self, period: str) -> Dict:
        """Visits per country for *period* ("day" | "week" | "month" | "all"),
        plus a zero-filled time series (per day; per month for "all")."""
        label, days = PERIODS[period]
        today = self.today().date()
        start = (today - timedelta(days=days - 1)) if days else None
        where, args = ("WHERE day >= ?", (start.isoformat(),)) if start else ("", ())
        first = None   # earliest recorded day, for "all"

        with self._conn() as conn:
            by_country = conn.execute(
                f"SELECT country, SUM(visits) FROM visit_counts {where} "
                "GROUP BY country ORDER BY SUM(visits) DESC, country", args
            ).fetchall()
            if start:
                rows = conn.execute(
                    f"SELECT day, SUM(visits) FROM visit_counts {where} GROUP BY day", args
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT substr(day, 1, 7), SUM(visits) FROM visit_counts GROUP BY 1"
                ).fetchall()
                first = conn.execute("SELECT MIN(day) FROM visit_counts").fetchone()[0]

        total = sum(n for _, n in by_country)
        countries = [
            {"code": code, "visits": n, "share": round(n / total * 100, 1) if total else 0}
            for code, n in by_country
        ]

        counts = dict(rows)
        if start:
            keys = [(start + timedelta(days=i)).isoformat() for i in range(days)]
        else:
            keys = _month_keys(first[:7] if first else today.strftime("%Y-%m"),
                               today.strftime("%Y-%m"))
        series = [{"key": k, "visits": counts.get(k, 0)} for k in keys]

        return {
            "period": period,
            "label": label,
            "start": start.isoformat() if start else first,
            "end": today.isoformat(),
            "timezone": _tz_label(self.tz),
            "total": total,
            "countries": countries,
            "series": series,
        }


def _month_keys(first: str, last: str) -> List[str]:
    """Inclusive list of "YYYY-MM" keys from *first* to *last*."""
    y, m = map(int, first.split("-"))
    ly, lm = map(int, last.split("-"))
    keys = []
    while (y, m) <= (ly, lm):
        keys.append(f"{y:04d}-{m:02d}")
        m += 1
        if m > 12:
            y, m = y + 1, 1
    return keys


def _tz_label(tz: timezone) -> str:
    offset = tz.utcoffset(None)
    minutes = int(offset.total_seconds() // 60)
    sign = "+" if minutes >= 0 else "-"
    return f"UTC{sign}{abs(minutes) // 60:02d}:{abs(minutes) % 60:02d}"
