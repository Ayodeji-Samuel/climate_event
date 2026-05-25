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

Results are cached in GEEEngine.cache to avoid redundant EE calls.
"""

import logging
from datetime import datetime, timedelta
from typing import Dict, Any, Optional, Tuple

from modules.gee_engine import GEEEngine

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Known regions: (country, [west, south, east, north])
# ---------------------------------------------------------------------------
KNOWN_REGIONS: Dict[str, Dict] = {
    "nigeria": {
        "label": "Nigeria",
        "country": "Nigeria",
        "bbox": [2.676932, 4.240594, 14.680073, 13.885645],
    },
    "ghana": {
        "label": "Ghana",
        "country": "Ghana",
        "bbox": [-3.260786, 4.737842, 1.187968, 11.173482],
    },
    "kenya": {
        "label": "Kenya",
        "country": "Kenya",
        "bbox": [33.908859, -4.720446, 41.899578, 4.622203],
    },
    "ethiopia": {
        "label": "Ethiopia",
        "country": "Ethiopia",
        "bbox": [32.997734, 3.403202, 47.978478, 14.894254],
    },
    "mozambique": {
        "label": "Mozambique",
        "country": "Mozambique",
        "bbox": [30.216253, -26.861025, 40.835802, -10.471883],
    },
    "bangladesh": {
        "label": "Bangladesh",
        "country": "Bangladesh",
        "bbox": [88.008940, 20.670883, 92.673546, 26.631412],
    },
    "india": {
        "label": "India",
        "country": "India",
        "bbox": [68.162386, 6.747139, 97.395555, 35.504475],
    },
    "pakistan": {
        "label": "Pakistan",
        "country": "Pakistan",
        "bbox": [60.872971, 23.694695, 77.840194, 37.097012],
    },
    "myanmar": {
        "label": "Myanmar",
        "country": "Myanmar",
        "bbox": [92.189225, 9.784569, 101.170506, 28.534878],
    },
    "thailand": {
        "label": "Thailand",
        "country": "Thailand",
        "bbox": [97.343398, 5.612851, 105.636812, 20.465045],
    },
    "indonesia": {
        "label": "Indonesia",
        "country": "Indonesia",
        "bbox": [95.010776, -10.359987, 141.019965, 5.479821],
    },
    "tanzania": {
        "label": "Tanzania",
        "country": "Tanzania",
        "bbox": [29.340000, -11.745696, 40.443222, -0.990736],
    },
    "custom": {
        "label": "Custom BBox",
        "country": None,
        "bbox": None,       # populated at query time
    },
}

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
WATER_VIS = {
    "min": 0,
    "max": 1,
    "palette": ["#0050a0"],   # Dark blue for permanent water
}


class FloodDetector:
    def __init__(self, gee: GEEEngine, config=None):
        self.gee = gee
        self.cfg = config or {}
        # Thresholds
        self._warn = float(self.cfg.get("FLOOD_WARN_THRESHOLD", 0.05))
        self._adv = float(self.cfg.get("FLOOD_ADV_THRESHOLD", 0.15))
        self._high = float(self.cfg.get("FLOOD_HIGH_THRESHOLD", 0.30))
        self._emerg = float(self.cfg.get("FLOOD_EMERG_THRESHOLD", 0.50))

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def list_regions(self):
        return [
            {"id": k, "label": v["label"]}
            for k, v in KNOWN_REGIONS.items()
            if k != "custom"
        ]

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

        if event_date is None:
            event_date = datetime.utcnow().strftime("%Y-%m-%d")

        cache_key = f"flood:{region_id}:{event_date}"
        if use_cache:
            cached = self.gee.cache.get_obj(cache_key)
            if cached:
                logger.debug("Cache hit for %s", cache_key)
                return cached

        try:
            result = self._run_detection(region_id, event_date, bbox)
            self.gee.cache.set(cache_key, result, ttl=1800)
            return result
        except Exception as exc:
            logger.exception("Flood detection failed for %s: %s", region_id, exc)
            return self._error_result(str(exc), region_id)

    def get_permanent_water_tiles(self, region_id: str = "nigeria") -> Dict:
        """Return tile URL for the JRC permanent water layer."""
        if not self.gee.is_connected:
            return {"error": "GEE not connected"}
        try:
            ee = self.gee._ee
            region_info = KNOWN_REGIONS.get(region_id, KNOWN_REGIONS["nigeria"])
            bbox = region_info["bbox"]
            aoi = self.gee.bbox_geometry(*bbox)

            jrc = ee.Image("JRC/GSW1_4/GlobalSurfaceWater").select("occurrence")
            jrc_clipped = jrc.updateMask(jrc.gt(50)).clip(aoi)
            tile_url = self.gee.image_to_tile_url(jrc_clipped, {
                "min": 50, "max": 100, "palette": ["#c9eeff", "#0050a0"]
            })
            return {"tile_url": tile_url, "layer": "permanent_water"}
        except Exception as exc:
            return {"error": str(exc)}

    # ------------------------------------------------------------------
    # Core detection algorithm
    # ------------------------------------------------------------------

    def _run_detection(
        self, region_id: str, event_date: str, custom_bbox: Optional[list]
    ) -> Dict[str, Any]:
        ee = self.gee._ee

        # --- Region geometry -------------------------------------------
        region_info = KNOWN_REGIONS.get(region_id)
        if not region_info:
            raise ValueError(f"Unknown region: {region_id}")

        bbox = custom_bbox or region_info.get("bbox")
        if not bbox:
            raise ValueError("No bounding box for this region")
        aoi = self.gee.bbox_geometry(*bbox)

        # --- Date windows -----------------------------------------------
        event_dt = datetime.strptime(event_date, "%Y-%m-%d")
        after_start = (event_dt - timedelta(days=7)).strftime("%Y-%m-%d")
        after_end = (event_dt + timedelta(days=7)).strftime("%Y-%m-%d")
        # Same season, previous year
        before_start = (event_dt - timedelta(days=365 + 30)).strftime("%Y-%m-%d")
        before_end = (event_dt - timedelta(days=365 - 30)).strftime("%Y-%m-%d")

        # --- Sentinel-1 collections ------------------------------------
        before_col = self.gee.get_s1_collection(aoi, before_start, before_end)
        after_col = self.gee.get_s1_collection(aoi, after_start, after_end)

        before_size = before_col.size().getInfo()
        after_size = after_col.size().getInfo()

        logger.info(
            "S1 images: before=%d (%s→%s), after=%d (%s→%s)",
            before_size, before_start, before_end,
            after_size, after_start, after_end,
        )

        if before_size == 0 or after_size == 0:
            # Widen search window
            before_start = (event_dt - timedelta(days=365 + 60)).strftime("%Y-%m-%d")
            before_end = (event_dt - timedelta(days=365 - 60)).strftime("%Y-%m-%d")
            after_start = (event_dt - timedelta(days=15)).strftime("%Y-%m-%d")
            after_end = (event_dt + timedelta(days=15)).strftime("%Y-%m-%d")
            before_col = self.gee.get_s1_collection(aoi, before_start, before_end)
            after_col = self.gee.get_s1_collection(aoi, after_start, after_end)
            before_size = before_col.size().getInfo()
            after_size = after_col.size().getInfo()

        if before_size == 0 or after_size == 0:
            return self._no_data_result(region_id, event_date, bbox)

        # --- Composites + speckle filter --------------------------------
        SMOOTH_M = 50   # focal mean radius in metres
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

        # --- Statistics --------------------------------------------------
        flooded_area_km2 = self.gee.compute_area_km2(flooded, aoi, scale=100)
        total_area_km2 = self.gee.compute_region_area_km2(aoi)
        flood_fraction = (flooded_area_km2 / total_area_km2) if total_area_km2 else 0

        risk_level, risk_label, risk_color = self._score_risk(flood_fraction)

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
            "region_label": region_info["label"],
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

    def _no_data_result(self, region_id, event_date, bbox):
        return {
            "success": False,
            "region_id": region_id,
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
        return {
            "success": False,
            "region_id": region_id,
            "message": message,
            "flood_tile_url": None,
            "sar_tile_url": None,
            "stats": {},
            "risk": {"level": 0, "label": "Unknown", "color": "#64748b"},
            "analysis_time": datetime.utcnow().isoformat(),
        }
