"""
modules/alert_system.py
SQLite-backed alert CRUD.  Thread-safe; no external dependencies.
"""

import sqlite3
import threading
import logging
from datetime import datetime
from typing import List, Dict, Optional
from pathlib import Path

logger = logging.getLogger(__name__)

RISK_LABELS = {0: "None", 1: "Watch", 2: "Advisory", 3: "Warning", 4: "Emergency"}
RISK_COLORS = {
    0: "#4a9eff",
    1: "#00d4b8",
    2: "#ffbe3d",
    3: "#ff8c00",
    4: "#ff4466",
}

DB_PATH = Path(__file__).parent.parent / "data" / "alerts.db"


class AlertSystem:
    def __init__(self, db_path: Optional[str] = None):
        self.db_path = str(db_path or DB_PATH)
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._init_db()

    # ------------------------------------------------------------------
    # Schema
    # ------------------------------------------------------------------

    def _init_db(self):
        with self._get_conn() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS alerts (
                    id        INTEGER PRIMARY KEY AUTOINCREMENT,
                    region    TEXT    NOT NULL,
                    level     INTEGER NOT NULL DEFAULT 0,
                    label     TEXT    NOT NULL,
                    color     TEXT    NOT NULL,
                    message   TEXT    NOT NULL,
                    source    TEXT    DEFAULT 'manual',
                    active    INTEGER NOT NULL DEFAULT 1,
                    created   TEXT    NOT NULL,
                    resolved  TEXT
                )
                """
            )

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    def create(
        self,
        region: str,
        level: int,
        message: str,
        source: str = "manual",
    ) -> Dict:
        label = RISK_LABELS.get(level, "Unknown")
        color = RISK_COLORS.get(level, "#64748b")
        created = datetime.utcnow().isoformat()
        with self._lock:
            with self._get_conn() as conn:
                # Avoid duplicate active alerts for same region+level
                existing = conn.execute(
                    "SELECT id FROM alerts WHERE region=? AND level=? AND active=1",
                    (region, level),
                ).fetchone()
                if existing:
                    return self._row_to_dict(
                        conn.execute(
                            "SELECT * FROM alerts WHERE id=?", (existing["id"],)
                        ).fetchone()
                    )
                cursor = conn.execute(
                    """
                    INSERT INTO alerts (region, level, label, color, message, source, created)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (region, level, label, color, message, source, created),
                )
                alert_id = cursor.lastrowid
        logger.info("Alert created: id=%d region=%s level=%d", alert_id, region, level)
        return self.get_by_id(alert_id)

    def get_by_id(self, alert_id: int) -> Optional[Dict]:
        with self._get_conn() as conn:
            row = conn.execute("SELECT * FROM alerts WHERE id=?", (alert_id,)).fetchone()
        return self._row_to_dict(row) if row else None

    def get_active(self) -> List[Dict]:
        with self._get_conn() as conn:
            rows = conn.execute(
                "SELECT * FROM alerts WHERE active=1 ORDER BY level DESC, created DESC"
            ).fetchall()
        return [self._row_to_dict(r) for r in rows]

    def get_all(self, limit: int = 100) -> List[Dict]:
        with self._get_conn() as conn:
            rows = conn.execute(
                "SELECT * FROM alerts ORDER BY created DESC LIMIT ?", (limit,)
            ).fetchall()
        return [self._row_to_dict(r) for r in rows]

    def resolve(self, alert_id: int) -> bool:
        with self._lock:
            with self._get_conn() as conn:
                conn.execute(
                    "UPDATE alerts SET active=0, resolved=? WHERE id=?",
                    (datetime.utcnow().isoformat(), alert_id),
                )
        return True

    def resolve_all_for_region(self, region: str) -> int:
        with self._lock:
            with self._get_conn() as conn:
                cursor = conn.execute(
                    "UPDATE alerts SET active=0, resolved=? WHERE region=? AND active=1",
                    (datetime.utcnow().isoformat(), region),
                )
        return cursor.rowcount

    def delete(self, alert_id: int) -> bool:
        with self._lock:
            with self._get_conn() as conn:
                conn.execute("DELETE FROM alerts WHERE id=?", (alert_id,))
        return True

    def stats(self) -> Dict:
        with self._get_conn() as conn:
            total = conn.execute("SELECT COUNT(*) FROM alerts").fetchone()[0]
            active = conn.execute(
                "SELECT COUNT(*) FROM alerts WHERE active=1"
            ).fetchone()[0]
            by_level = conn.execute(
                "SELECT level, COUNT(*) as cnt FROM alerts WHERE active=1 GROUP BY level"
            ).fetchall()
        return {
            "total": total,
            "active": active,
            "by_level": {row["level"]: row["cnt"] for row in by_level},
        }

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _row_to_dict(row) -> Dict:
        if not row:
            return {}
        return dict(row)
