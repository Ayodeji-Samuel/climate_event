"""
modules/flood_detector.py
Implements Sentinel-1 SAR-based flood detection using standard
UNOSAT/ESA methodology:
  1. Collect pre-event + post-event S1/VV composites
  2. Apply Lee speckle filter (focal_mean)
  3. Compute dB difference (pre – post)
  4. Threshold at +3 dB (backscatter drop signals flooding)
  5. Mask permanent water (JRC) and steep slopes (>5°)
  6. Compute flooded area & risk level
  7. Return tile URL + metadata dict

Regions are Nigeria, any of its 37 states, or any of its 774 LGAs
(see modules/nigeria_admin.py); analysis is clipped to the exact boundary.
Results are cached in GEEEngine.cache to avoid redundant EE calls.
"""

import logging
from datetime import datetime, timedelta
from typing import Dict, Any, Optional, Tuple

from modules.gee_engine import GEEEngine
from modules.nigeria_admin import ADMIN, Region

logger = logging.getLogger(__name__)

# Risk band labels keyed by (level 0–4)
RISK_LABELS = {0: "None", 1: "Watch", 2: "Advisory", 3: "Warning", 4: "Emergency"}
RISK_COLORS = {
    0: "#4a9eff",
    1: "#00d4b8",
    2: "#ffbe3d",
    3: "#ff8c00",
    4: "#ff4466",
}

# Visualisation palettes
FLOOD_VIS = {
    "min": 0,
    "max": 1,
    "palette": ["#00a8ff"],   # Electric blue for flooded pixels
}

# reduceRegion scale (m) for the flooded-area sum.  Sentinel-1 is 10 m;
# larger units use coarser scales to stay well inside the EE time limit.
AREA_SCALE = {"lga": 20, "state": 50, "country": 100, "custom": 50}


class FloodDetector:
    def __init__(self, gee: GEEEngine, config=None):
        self.gee = gee
        self.cfg = config or {}
        # Thresholds
        self._warn = float(self.cfg.get("FLOOD_WARN_THRESHOLD", 0.05))
        self._adv = float(self.cfg.get("FLOOD_ADV_THRESHOLD", 0.15))
        self._high = float(self.cfg.get("FLOOD_HIGH_THRESHOLD", 0.30))
        self._emerg = float(self.cfg.get("FLOOD_EMERG_THRESHOLD", 0.50))
        self._min_area_km2 = float(self.cfg.get("FLOOD_MIN_AREA_KM2", 0))

    @property
    def thresholds(self) -> Dict[int, float]:
        """Minimum flooded fraction for each risk level (1–4)."""
        return {1: self._warn, 2: self._adv, 3: self._high, 4: self._emerg}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def list_regions(self):
        return [{"id": r.id, "label": r.label, "level": r.level}
                for r in [ADMIN.country] + ADMIN.states()]

    def analyze(
        self,
        region_id: str = "nigeria",
        event_date: Optional[str] = None,
        bbox: Optional[list] = None,
        use_cache: bool = True,
    ) -> Dict[str, Any]:
        """
        Run flood detection for *region_id* around *event_date*.
        Returns a dict with tile_url, stats, risk level, etc.
        """
        if not self.gee.is_connected:
            return self._error_result("GEE not connected", region_id)

        region = ADMIN.get(region_id)
        if region is None and not bbox:
            return self._error_result(f"Unknown region: {region_id}", region_id)

        if event_date is None:
            event_date = datetime.utcnow().strftime("%Y-%m-%d")
        try:
            datetime.strptime(event_date, "%Y-%m-%d")
        except ValueError:
            return self._error_result(f"Invalid date '{event_date}' (expected YYYY-MM-DD)", region_id)

        bbox_key = ",".join(f"{v:.4f}" for v in bbox) if bbox else ""
        cache_key = f"flood:{region_id}:{bbox_key}:{event_date}"
        if use_cache:
            cached = self.gee.cache.get_obj(cache_key)
            if cached:
                logger.debug("Cache hit for %s", cache_key)
                return cached

        try:
            result = self._run_detection(region, region_id, event_date, bbox)
            if result.get("success"):
                self.gee.cache.set(cache_key, result, ttl=1800)
            return result
        except Exception as exc:
            logger.exception("Flood detection failed for %s: %s", region_id, exc)
            return self._error_result(str(exc), region_id)

    def get_permanent_water_tiles(self, region_id: str = "nigeria") -> Dict:
        """Return tile URL for the JRC permanent water layer."""
        if not self.gee.is_connected:
            return {"error": "GEE not connected"}
        region = ADMIN.get(region_id) or ADMIN.country
        try:
            ee = self.gee._ee
            aoi = ADMIN.geometry(ee, region)
            jrc = ee.Image("JRC/GSW1_4/GlobalSurfaceWater").select("occurrence")
            jrc_clipped = jrc.updateMask(jrc.gt(50)).clip(aoi)
            tile_url = self.gee.image_to_tile_url(jrc_clipped, {
                "min": 50, "max": 100, "palette": ["#c9eeff", "#0050a0"]
            })
            return {"tile_url": tile_url, "layer": "permanent_water", "region_id": region.id}
        except Exception as exc:
            return {"error": str(exc)}

    # ------------------------------------------------------------------
    # Core detection algorithm
    # ------------------------------------------------------------------

    def _run_detection(
        self, region: Optional[Region], region_id: str, event_date: str,
        custom_bbox: Optional[list]
    ) -> Dict[str, Any]:
        ee = self.gee._ee

        # --- Region geometry -------------------------------------------
        if custom_bbox:
            aoi = self.gee.bbox_geometry(*custom_bbox)
            level, label, bbox = "custom", "Custom area", list(custom_bbox)
        else:
            aoi = ADMIN.geometry(ee, region)
            level, label, bbox = region.level, region.label, list(region.bbox)

        # --- Date windows -----------------------------------------------
        # Candidate (before, after, orbit) windows, tried in order.  All
        # collection sizes are fetched in ONE round trip instead of up to six.
        event_dt = datetime.strptime(event_date, "%Y-%m-%d")

        def d(days):
            return (event_dt + timedelta(days=days)).strftime("%Y-%m-%d")

        # Same season, previous year → post-event window around the date
        strategies = [
            ("DESCENDING", (d(-395), d(-335)), (d(-7), d(7))),
            ("ASCENDING",  (d(-395), d(-335)), (d(-7), d(7))),
            ("BOTH",       (d(-425), d(-305)), (d(-15), d(15))),   # widened
        ]
        cols = []
        for orbit, before_win, after_win in strategies:
            cols.append((
                self.gee.get_s1_collection(aoi, *before_win, pass_direction=orbit),
                self.gee.get_s1_collection(aoi, *after_win, pass_direction=orbit),
            ))
        sizes = ee.List([ee.List([b.size(), a.size()]) for b, a in cols]).getInfo()

        chosen = next((i for i, (nb, na) in enumerate(sizes) if nb > 0 and na > 0), None)
        logger.info("S1 image counts %s for %s on %s → strategy %s",
                    sizes, region_id, event_date, chosen)
        if chosen is None:
            return self._no_data_result(region_id, label, event_date, bbox)
        before_col, after_col = cols[chosen]
        before_size, after_size = sizes[chosen]

        # --- Composites + speckle filter --------------------------------
        # 100 m focal-mean (≈10 Sentinel-1 pixels) gives a better SNR
        # than the original 50 m while preserving flood boundaries.
        SMOOTH_M = 100   # focal mean radius in metres
        before = before_col.mean().clip(aoi)
        after = after_col.mean().clip(aoi)
        before_sm = before.focal_mean(SMOOTH_M, "circle", "meters")
        after_sm = after.focal_mean(SMOOTH_M, "circle", "meters")

        # S1 GRD values are dB (sigma0).  Drop > 3 dB → flooding.
        diff = before_sm.subtract(after_sm)
        flooded_raw = diff.gt(3)

        # --- Remove permanent water + steep terrain ---------------------
        perm_water = self.gee.get_permanent_water_mask(10)
        slope = self.gee.get_slope()
        flooded = (
            flooded_raw
            .updateMask(flooded_raw)
            .where(perm_water, 0)
            .updateMask(slope.lt(5))
            .rename("flood")
        )

        # --- Statistics (flooded + total area in one round trip) ---------
        stats = ee.Dictionary({
            "flooded_m2": flooded.multiply(ee.Image.pixelArea()).reduceRegion(
                reducer=ee.Reducer.sum(), geometry=aoi, scale=AREA_SCALE[level],
                maxPixels=1e11, bestEffort=True, tileScale=4,
            ).get("flood"),
            "total_m2": aoi.area(maxError=100),
        }).getInfo()
        flooded_area_km2 = round((stats.get("flooded_m2") or 0) / 1e6, 2)
        total_area_km2 = round((stats.get("total_m2") or 0) / 1e6, 2)
        flood_fraction = (flooded_area_km2 / total_area_km2) if total_area_km2 else 0

        # Below the minimum absolute area the "flood" is indistinguishable
        # from speckle noise, however large a share of a tiny LGA it is.
        risk_level, risk_label, risk_color = self._score_risk(
            flood_fraction if flooded_area_km2 >= self._min_area_km2 else 0)

        # --- Visualisation tile URL -------------------------------------
        flooded_vis = flooded.selfMask()
        tile_url = self.gee.image_to_tile_url(flooded_vis, FLOOD_VIS)

        # After-image for reference
        after_vis_url = self.gee.image_to_tile_url(
            after_sm.clip(aoi),
            {"min": -25, "max": 0, "palette": ["#000022", "#0066cc", "#ffffff"]},
        )

        return {
            "success": True,
            "region_id": region_id,
            "region_label": label,
            "region_level": level,
            "event_date": event_date,
            "bbox": bbox,
            "flood_tile_url": tile_url,
            "sar_tile_url": after_vis_url,
            "stats": {
                "flooded_area_km2": flooded_area_km2,
                "total_area_km2": total_area_km2,
                "flood_fraction_pct": round(flood_fraction * 100, 2),
                "before_images": before_size,
                "after_images": after_size,
            },
            "risk": {
                "level": risk_level,
                "label": risk_label,
                "color": risk_color,
            },
            "analysis_time": datetime.utcnow().isoformat(),
        }

    # ------------------------------------------------------------------
    # Risk scoring
    # ------------------------------------------------------------------

    def _score_risk(self, fraction: float) -> Tuple[int, str, str]:
        if fraction >= self._emerg:
            lvl = 4
        elif fraction >= self._high:
            lvl = 3
        elif fraction >= self._adv:
            lvl = 2
        elif fraction >= self._warn:
            lvl = 1
        else:
            lvl = 0
        return lvl, RISK_LABELS[lvl], RISK_COLORS[lvl]

    # ------------------------------------------------------------------
    # Fallback results
    # ------------------------------------------------------------------

    def _no_data_result(self, region_id, region_label, event_date, bbox):
        return {
            "success": False,
            "region_id": region_id,
            "region_label": region_label,
            "event_date": event_date,
            "bbox": bbox,
            "message": "No Sentinel-1 imagery available for this date/region.",
            "flood_tile_url": None,
            "sar_tile_url": None,
            "stats": {
                "flooded_area_km2": 0,
                "total_area_km2": 0,
                "flood_fraction_pct": 0,
            },
            "risk": {"level": 0, "label": "None", "color": RISK_COLORS[0]},
            "analysis_time": datetime.utcnow().isoformat(),
        }

    def _error_result(self, message, region_id):
        region = ADMIN.get(region_id)
        return {
            "success": False,
            "region_id": region_id,
            "region_label": region.label if region else region_id,
            "message": message,
            "flood_tile_url": None,
            "sar_tile_url": None,
            "stats": {},
            "risk": {"level": 0, "label": "Unknown", "color": "#64748b"},
            "analysis_time": datetime.utcnow().isoformat(),
        }
