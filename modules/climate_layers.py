"""
modules/climate_layers.py
Multi-layer environmental analysis using Google Earth Engine.

Supported layers (15):
  vegetation       – NDVI from MODIS/Sentinel-2
  land_cover       – ESA WorldCover / MODIS IGBP classification
  heatwaves        – MODIS LST anomaly vs a 3-year baseline
  fires            – VIIRS 375 m active fires (NASA LANCE) / MODIS FIRMS fallback
  temperature      – MODIS Land Surface Temperature (LST)
  soil_moisture    – SMAP L4 9 km surface soil moisture
  deformation      – Sentinel-1 InSAR coherence proxy (mean coherence drop)
  forest_structure – Hansen Global Forest Change canopy cover + loss
  elevation        – SRTM DEM + slope/aspect
  rainfall         – CHIRPS daily precipitation accumulation
  snow             – MODIS daily snow cover fraction
  crop_stress      – Sentinel-2 NDWI / LAI crop health composite
  pollution        – Sentinel-5P NO2 + aerosol optical depth
  sea_level        – GRACE/GRACE-FO mascon water storage anomaly (coastal proxy)
  glacier          – GLIMS glacier outlines + Landsat snowline elevation

Cache: all results compressed with zlib (lossless) in the shared GEECache.
Stats: floats rounded to 4 significant digits before caching to reduce size.
Tile URLs: short-lived (~1 h) EE map tile URLs — not compressed (strings).
Regions: Nigeria, its 37 states and 774 LGAs (modules/nigeria_admin.py).
"""

import json
import logging
import zlib
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from modules.gee_engine import GEEEngine
from modules.nigeria_admin import ADMIN

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Compression helpers (lossless zlib level-6, ~60-70 % reduction on JSON)
# ---------------------------------------------------------------------------

def _compress(obj: Any) -> bytes:
    """Serialise *obj* to JSON and compress with zlib level-6."""
    raw = json.dumps(obj, separators=(",", ":")).encode("utf-8")
    return zlib.compress(raw, level=6)


def _decompress(data: bytes) -> Any:
    """Decompress zlib bytes and deserialise JSON."""
    return json.loads(zlib.decompress(data).decode("utf-8"))


def _round4(v):
    """Round a float to 4 significant digits (reduces JSON size ~30 %)."""
    if isinstance(v, float):
        return float(f"{v:.4g}")
    return v


def _compact_stats(d: Dict) -> Dict:
    """Recursively round all float values in a stats dict."""
    out = {}
    for k, v in d.items():
        if isinstance(v, dict):
            out[k] = _compact_stats(v)
        elif isinstance(v, list):
            out[k] = [_round4(x) for x in v]
        else:
            out[k] = _round4(v)
    return out


# ---------------------------------------------------------------------------
# Layer metadata catalogue
# ---------------------------------------------------------------------------

LAYER_CATALOGUE: Dict[str, Dict] = {
    "vegetation": {
        "label": "Vegetation (NDVI)",
        "description": "Normalised Difference Vegetation Index from MODIS 500 m 16-day composite.",
        "unit": "NDVI (−1 to 1)",
        "icon": "🌿",
        "category": "land",
        "dataset": "MODIS/061/MOD13A1",
    },
    "land_cover": {
        "label": "Land Cover",
        "description": "ESA WorldCover 10 m annual land-use/land-cover classification.",
        "unit": "Class ID (0–100)",
        "icon": "🗺️",
        "category": "land",
        "dataset": "ESA/WorldCover/v200",
    },
    "heatwaves": {
        "label": "Heatwaves",
        "description": "Land surface temperature over the last 20 days vs a 3-year MODIS baseline. Values > 3 °C indicate heat stress.",
        "unit": "°C anomaly",
        "icon": "🌡️",
        "category": "climate",
        "dataset": "MODIS/061/MOD11A1",
    },
    "fires": {
        "label": "Active Fires",
        "description": "NOAA-20 VIIRS 375 m active fire detections and fire radiative power (NASA LANCE); MODIS 1 km FIRMS for dates before Oct 2023.",
        "unit": "MW (fire radiative power)",
        "icon": "🔥",
        "category": "hazard",
        "dataset": "NASA/LANCE/NOAA20_VIIRS/C2 + FIRMS",
    },
    "temperature": {
        "label": "Land Surface Temperature",
        "description": "MODIS Terra LST_Day_1km daily composite in Celsius.",
        "unit": "°C",
        "icon": "🌡️",
        "category": "climate",
        "dataset": "MODIS/061/MOD11A1",
    },
    "soil_moisture": {
        "label": "Soil Moisture",
        "description": "SMAP L4 9 km, 3-hourly surface soil moisture (0–5 cm), 10-day mean.",
        "unit": "m³/m³",
        "icon": "💧",
        "category": "land",
        "dataset": "NASA/SMAP/SPL4SMGP/008",
    },
    "deformation": {
        "label": "Ground Deformation",
        "description": "Sentinel-1 InSAR coherence proxy: low coherence indicates surface change / subsidence.",
        "unit": "Coherence (0–1)",
        "icon": "📐",
        "category": "hazard",
        "dataset": "COPERNICUS/S1_GRD (coherence proxy)",
    },
    "forest_structure": {
        "label": "Forest Structure",
        "description": "Hansen Global Forest Change: tree canopy cover (%) and annual loss year.",
        "unit": "% canopy cover",
        "icon": "🌲",
        "category": "land",
        "dataset": "UMD/hansen/global_forest_change_2023_v1_11",
    },
    "elevation": {
        "label": "Elevation & Terrain",
        "description": "SRTM 30 m DEM with derived slope and aspect.",
        "unit": "metres",
        "icon": "⛰️",
        "category": "terrain",
        "dataset": "USGS/SRTMGL1_003",
    },
    "rainfall": {
        "label": "Rainfall",
        "description": "CHIRPS 0.05° daily precipitation — 5-day accumulation.",
        "unit": "mm",
        "icon": "🌧️",
        "category": "climate",
        "dataset": "UCSB-CHG/CHIRPS/DAILY",
    },
    "snow": {
        "label": "Snow Cover",
        "description": "MODIS Terra daily snow cover fraction (NDSI-based).",
        "unit": "% area covered",
        "icon": "❄️",
        "category": "climate",
        "dataset": "MODIS/061/MOD10A1",
    },
    "crop_stress": {
        "label": "Crop Stress",
        "description": "Sentinel-2 NDWI & EVI composite indicating crop water stress.",
        "unit": "Index (−1 to 1)",
        "icon": "🌾",
        "category": "land",
        "dataset": "COPERNICUS/S2_SR_HARMONIZED",
    },
    "pollution": {
        "label": "Air Pollution (NO₂)",
        "description": "Sentinel-5P TROPOMI tropospheric NO₂ column density.",
        "unit": "mol/m² (×10⁻⁵)",
        "icon": "💨",
        "category": "atmosphere",
        "dataset": "COPERNICUS/S5P/NRTI/L3_NO2",
    },
    "sea_level": {
        "label": "Sea Level / Water Storage",
        "description": "GRACE/GRACE-FO JPL mascon terrestrial water storage anomaly (latest month available — releases lag by months).",
        "unit": "cm equivalent water height",
        "icon": "🌊",
        "category": "ocean",
        "dataset": "NASA/GRACE/MASS_GRIDS_V04/MASCON_CRI",
    },
    "glacier": {
        "label": "Glacier / Ice",
        "description": "Landsat-derived NDSI snowline elevation and GLIMS glacier outlines.",
        "unit": "% snow/ice cover",
        "icon": "🧊",
        "category": "cryosphere",
        "dataset": "LANDSAT/LC08/C02/T1_L2 + MODIS snow",
    },
}


# ---------------------------------------------------------------------------
# Main analyser class
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Time-series helpers (module-level, pure Python)
# ---------------------------------------------------------------------------

def _pearson(series_a: List[dict], series_b: List[dict]) -> Optional[float]:
    """Pearson r from two {month, value} series.  Returns None if < 3 valid pairs."""
    a_map = {p["month"]: p["value"] for p in series_a}
    b_map = {p["month"]: p["value"] for p in series_b}
    pairs = [
        (a_map[m], b_map[m])
        for m in sorted(set(a_map) & set(b_map))
        if a_map.get(m) is not None and b_map.get(m) is not None
    ]
    if len(pairs) < 3:
        return None
    xs = [p[0] for p in pairs]
    ys = [p[1] for p in pairs]
    n  = len(xs)
    mx, my = sum(xs) / n, sum(ys) / n
    num = sum((xs[i] - mx) * (ys[i] - my) for i in range(n))
    sx  = sum((xs[i] - mx) ** 2 for i in range(n)) ** 0.5
    sy  = sum((ys[i] - my) ** 2 for i in range(n)) ** 0.5
    if sx * sy == 0:
        return None
    return round(num / (sx * sy), 3)


def _linear_pct_change(vals: List) -> Optional[float]:
    """% change from first to last non-None value in the series."""
    clean = [v for v in vals if v is not None]
    if len(clean) < 2 or clean[0] == 0:
        return None
    return round((clean[-1] - clean[0]) / abs(clean[0]) * 100, 1)



class ClimateLayerAnalyzer:
    """
    Analyses all 15 environmental layers for any region using GEE.
    Results are serialised as JSON, compressed with zlib, and stored
    in the shared GEEEngine TTL cache to minimise memory and re-computation.
    Cache TTL: 30 min for dynamic layers (fires, temperature), 6 h for static.
    """

    # TTL in seconds per layer category
    _TTL: Dict[str, int] = {
        "fires":        1800,   # 30 min
        "temperature":  1800,
        "heatwaves":    3600,   # 1 h
        "rainfall":     3600,
        "snow":         3600,
        "soil_moisture": 3600,
        "pollution":    3600,
        "vegetation":   21600,  # 6 h
        "land_cover":   86400,  # 24 h
        "deformation":  21600,
        "forest_structure": 86400,
        "elevation":    86400,
        "crop_stress":  21600,
        "sea_level":    86400,
        "glacier":      86400,
    }

    # ------------------------------------------------------------------
    # Monthly time-series configuration (for /api/climate/trend)
    # col, band, scale, mul?, add?, reducer (mean|sum|count_threshold), unit, label
    # None = static layer with no meaningful monthly series
    # ------------------------------------------------------------------
    _SERIES_CFG: Dict[str, Optional[Dict]] = {
        "vegetation":    dict(col="MODIS/061/MOD13A1", band="NDVI", scale=500,
                              mul=0.0001, reducer="mean",
                              unit="NDVI", label="Vegetation (NDVI)"),
        "temperature":   dict(col="MODIS/061/MOD11A1", band="LST_Day_1km", scale=1000,
                              mul=0.02, add=-273.15, reducer="mean",
                              unit="°C", label="Land Surface Temp."),
        "heatwaves":     dict(col="MODIS/061/MOD11A1", band="LST_Day_1km", scale=1000,
                              mul=0.02, add=-273.15, reducer="mean",
                              unit="°C", label="Surface Temp (LST)"),
        "rainfall":      dict(col="UCSB-CHG/CHIRPS/DAILY", band="precipitation", scale=5000,
                              reducer="sum", unit="mm", label="Monthly Rainfall"),
        "soil_moisture": dict(col="NASA/SMAP/SPL4SMGP/008", band="sm_surface",
                              scale=11000, reducer="mean", sample_hour=1,  # 01:30 UTC granule
                              unit="m³/m³", label="Soil Moisture"),
        "snow":          dict(col="MODIS/061/MOD10A1", band="NDSI_Snow_Cover", scale=500,
                              reducer="mean", unit="% cover", label="Snow Cover"),
        "pollution":     dict(col="COPERNICUS/S5P/NRTI/L3_NO2",
                              band="tropospheric_NO2_column_number_density",
                              scale=1000, mul=1e5, reducer="mean",
                              unit="×10⁻⁵ mol/m²", label="NO₂ Column"),
        "sea_level":     dict(col="NASA/GRACE/MASS_GRIDS_V04/MASCON_CRI",
                              band="lwe_thickness", scale=50000, reducer="mean",
                              lagged=True,   # releases trail by months
                              unit="cm EWH", label="Water Storage"),
        # MODIS FIRMS (2000 →) rather than VIIRS LANCE (Oct 2023 →) so a
        # 24-month trend always has a full record.
        "fires":         dict(col="FIRMS", band="T21",
                              scale=1000, threshold=330, reducer="count_threshold",
                              unit="fire pixels", label="Fire Detections"),
        "glacier":       dict(col="MODIS/061/MOD10A1", band="NDSI_Snow_Cover", scale=500,
                              reducer="mean", unit="% snow/ice", label="Snow/Ice Cover"),
        "crop_stress":   dict(col="MODIS/061/MOD13A1", band="NDVI", scale=500,
                              mul=0.0001, reducer="mean",
                              unit="NDVI", label="Vegetation (NDVI)"),
        "forest_structure": dict(col="UCSB-CHG/CHIRPS/DAILY", band="precipitation",
                                 scale=5000, reducer="sum", unit="mm", label="Rainfall"),
        "deformation":   dict(col="UCSB-CHG/CHIRPS/DAILY", band="precipitation",
                              scale=5000, reducer="sum", unit="mm", label="Rainfall"),
        "elevation":     None,   # static DEM — no monthly series
        "land_cover":    None,   # static classification — no monthly series
    }

    # Layer IDs to use as correlated parameters (up to 2 per layer)
    _LAYER_CORRELATES: Dict[str, List[str]] = {
        "vegetation":       ["rainfall",     "temperature"],
        "fires":            ["temperature",  "vegetation"],
        "heatwaves":        ["temperature",  "soil_moisture"],
        "temperature":      ["rainfall",     "vegetation"],
        "rainfall":         ["soil_moisture","vegetation"],
        "soil_moisture":    ["rainfall",     "vegetation"],
        "deformation":      ["rainfall",     "soil_moisture"],
        "forest_structure": ["rainfall",     "vegetation"],
        "elevation":        [],
        "crop_stress":      ["rainfall",     "soil_moisture"],
        "pollution":        ["temperature",  "vegetation"],
        "sea_level":        ["rainfall",     "soil_moisture"],
        "glacier":          ["temperature",  "snow"],
        "snow":             ["temperature",  "rainfall"],
        "land_cover":       [],
    }

    # Human-readable description of each (layer, correlate) relationship
    _CORRELATION_DESC: Dict[Tuple[str, str], str] = {
        ("fires",     "temperature"):    "Higher temperatures dry vegetation and accelerate fire spread.",
        ("fires",     "vegetation"):     "Dense vegetation reduces fire intensity; degraded land burns readily.",
        ("vegetation","rainfall"):       "Rainfall directly drives vegetation growth via soil moisture.",
        ("vegetation","temperature"):    "Extreme heat suppresses plant growth; optimal temperatures promote it.",
        ("heatwaves", "temperature"):    "Heatwave intensity tracks land surface temperature anomalies.",
        ("heatwaves", "soil_moisture"):  "Dry soils amplify heatwaves by reducing evaporative cooling.",
        ("temperature","rainfall"):      "Rainfall cools surfaces through evapotranspiration.",
        ("temperature","vegetation"):    "Vegetated land stays cooler than bare ground.",
        ("rainfall",  "soil_moisture"):  "Soil moisture tracks cumulative rainfall with a short lag.",
        ("rainfall",  "vegetation"):     "Higher rainfall sustains denser, more productive vegetation.",
        ("soil_moisture","rainfall"):    "Monthly rainfall is the primary driver of surface soil moisture.",
        ("soil_moisture","vegetation"):  "Moist soils sustain higher vegetation density and root growth.",
        ("pollution", "temperature"):    "Heat intensifies photochemical smog and traps pollutants near surface.",
        ("pollution", "vegetation"):     "Dense vegetation absorbs NO₂; urban areas show elevated levels.",
        ("crop_stress","rainfall"):      "Insufficient rainfall is the leading cause of crop water deficit.",
        ("crop_stress","soil_moisture"): "Low soil moisture directly causes crop stress and yield reduction.",
        ("sea_level", "rainfall"):       "Heavy precipitation increases terrestrial water storage anomalies.",
        ("sea_level", "soil_moisture"):  "High soil moisture contributes to positive GRACE water storage.",
        ("glacier",   "temperature"):    "Rising temperatures accelerate glacier retreat and snowmelt.",
        ("glacier",   "snow"):           "Seasonal snow and glacier extent are closely coupled in alpine zones.",
        ("snow",      "temperature"):    "Snow accumulation is suppressed when temperatures stay above 0 °C.",
        ("snow",      "rainfall"):       "Warm rain events at altitude accelerate snowpack melt.",
        ("forest_structure","rainfall"): "Rainfall supports forest regeneration and canopy density.",
        ("deformation","rainfall"):      "Saturated soils from heavy rain increase ground deformation risk.",
        ("deformation","soil_moisture"): "High soil moisture reduces shear strength, raising deformation risk.",
    }


    def __init__(self, gee_engine: GEEEngine):
        self._gee = gee_engine

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def list_layers(self) -> List[Dict]:
        """Return the full layer catalogue (no GEE needed)."""
        return [
            {"id": lid, **{k: v for k, v in meta.items() if k != "dataset"}}
            for lid, meta in LAYER_CATALOGUE.items()
        ]

    def analyze(
        self,
        layer_id: str,
        region_id: str,
        date: Optional[str] = None,
        bbox: Optional[List[float]] = None,
        use_cache: bool = True,
    ) -> Dict[str, Any]:
        """
        Run analysis for *layer_id* over *region_id* on *date*.
        Returns a dict:  { layer_id, region_id, date, tile_url, stats, meta, success }
        Stats dict is already decompressed for the caller.
        """
        if layer_id not in LAYER_CATALOGUE:
            return {"success": False, "message": f"Unknown layer: {layer_id}",
                    "layer_id": layer_id}
        if not bbox and ADMIN.get(region_id) is None:
            return {"success": False, "message": f"Unknown region: {region_id}",
                    "layer_id": layer_id, "region_id": region_id}

        date = date or datetime.utcnow().strftime("%Y-%m-%d")
        try:
            datetime.strptime(date, "%Y-%m-%d")
        except ValueError:
            return {"success": False, "layer_id": layer_id, "region_id": region_id,
                    "message": f"Invalid date '{date}' (expected YYYY-MM-DD)"}
        bbox_key = ",".join(f"{v:.4f}" for v in bbox) if bbox else ""
        cache_key = f"climate:{layer_id}:{region_id}:{bbox_key}:{date}"

        if use_cache:
            cached_bytes = self._gee.cache.get(cache_key)
            if cached_bytes is not None:
                try:
                    return _decompress(cached_bytes)
                except Exception:
                    pass   # stale / corrupt — re-compute

        result = self._dispatch(layer_id, region_id, date, bbox)
        result["layer_id"] = layer_id
        result["region_id"] = region_id
        result["date"] = date
        result["meta"] = {k: v for k, v in LAYER_CATALOGUE[layer_id].items()
                          if k != "dataset"}

        # Compact stats before compression
        if "stats" in result and isinstance(result["stats"], dict):
            result["stats"] = _compact_stats(result["stats"])

        # Only cache successes — caching a transient EE timeout would pin the
        # failure for up to 24 h.
        if result.get("success"):
            ttl = self._TTL.get(layer_id, 3600)
            self._gee.cache.set(cache_key, _compress(result), ttl=ttl)
        return result

    # ------------------------------------------------------------------
    # Dispatcher
    # ------------------------------------------------------------------

    def _dispatch(self, layer_id: str, region_id: str,
                  date: str, bbox: Optional[List[float]]) -> Dict:
        fn = getattr(self, f"_analyze_{layer_id}", None)
        if fn is None:
            return {"success": False, "message": f"No analyser for layer: {layer_id}"}
        try:
            return fn(region_id, date, bbox)
        except Exception as exc:
            logger.exception("Climate layer '%s' analysis error: %s", layer_id, exc)
            return {"success": False, "message": str(exc),
                    "tile_url": None, "stats": {}}

    # ------------------------------------------------------------------
    # Geometry helper
    # ------------------------------------------------------------------

    def _get_aoi(self, region_id: str, bbox: Optional[List[float]]):
        """Return (ee_geometry, region_label, level).

        Uses the exact country / state / LGA polygon from FAO GAUL 2025
        (500 m simplified) so .clip() and .reduceRegion() follow the true
        boundary — the same outline the map draws.
        """
        ee = self._gee._ee
        if bbox and len(bbox) == 4:
            return ee.Geometry.BBox(*bbox), "Custom area", "custom"
        region = ADMIN.get(region_id)
        if region is None:
            raise ValueError(f"Unknown region '{region_id}'.")
        return ADMIN.geometry(ee, region), region.label, region.level

    @staticmethod
    def _s(level: str, country: int, state: int, lga: int) -> int:
        """reduceRegion scale (m) sized to the analysis unit: coarse enough for
        a whole-country run to finish, fine enough that a small LGA still
        covers many pixels."""
        return {"country": country, "lga": lga}.get(level, state)

    def _tile(self, image, vis: Dict) -> Optional[str]:
        try:
            return self._gee.image_to_tile_url(image, vis)
        except Exception as exc:
            logger.warning("Tile URL generation failed: %s", exc)
            return None

    # ------------------------------------------------------------------
    # 1. Vegetation — MODIS NDVI
    # ------------------------------------------------------------------

    def _analyze_vegetation(self, region_id: str, date: str,
                             bbox: Optional[List[float]]) -> Dict:
        ee = self._gee._ee
        aoi, label, level = self._get_aoi(region_id, bbox)
        end = datetime.strptime(date, "%Y-%m-%d")
        # MODIS MOD13A1 16-day composites can lag 3-4 weeks in GEE ingestion;
        # 90-day window guarantees we always find the latest available composite.
        start = (end - timedelta(days=90)).strftime("%Y-%m-%d")

        # Limit to the 3 most-recent composites; median is cloud-robust.
        # Scale 1000 m is adequate for national NDVI statistics (4× fewer pixels).
        img = (ee.ImageCollection("MODIS/061/MOD13A1")
               .filterBounds(aoi)
               .filterDate(start, date)
               .select("NDVI")
               .sort("system:time_start", False)
               .limit(3)
               .median()
               .multiply(0.0001)
               .clip(aoi))

        stats = img.reduceRegion(
            reducer=ee.Reducer.mean().combine(ee.Reducer.stdDev(), "", True)
                               .combine(ee.Reducer.min(), "", True)
                               .combine(ee.Reducer.max(), "", True),
            geometry=aoi, scale=self._s(level, 1000, 500, 250), maxPixels=1e8, bestEffort=True,
        ).getInfo()

        if not stats or stats.get("NDVI_mean") is None:
            return {"success": False,
                    "message": "No MODIS NDVI imagery available for this region/period.",
                    "tile_url": None, "stats": {}}

        tile = self._tile(img, {
            "min": -0.2, "max": 1.0,
            "palette": ["#d73027", "#f46d43", "#fdae61", "#fee08b",
                        "#d9ef8b", "#a6d96a", "#66bd63", "#1a9850"],
        })
        return {
            "success": True,
            "tile_url": tile,
            "stats": {
                "ndvi_mean": stats.get("NDVI_mean"),
                "ndvi_std":  stats.get("NDVI_stdDev"),
                "ndvi_min":  stats.get("NDVI_min"),
                "ndvi_max":  stats.get("NDVI_max"),
                "region":    label,
                "period":    f"{start} → {date}",
            },
        }

    # ------------------------------------------------------------------
    # 2. Land Cover — ESA WorldCover
    # ------------------------------------------------------------------

    def _analyze_land_cover(self, region_id: str, date: str,
                             bbox: Optional[List[float]]) -> Dict:
        ee = self._gee._ee
        aoi, label, level = self._get_aoi(region_id, bbox)
        img = ee.ImageCollection("ESA/WorldCover/v200").first().clip(aoi)

        # Class histogram (pixel counts per class)
        hist = img.reduceRegion(
            reducer=ee.Reducer.frequencyHistogram(),
            geometry=aoi, scale=self._s(level, 300, 100, 10),
            maxPixels=1e10, bestEffort=True, tileScale=4,
        ).getInfo()
        class_hist = hist.get("Map", {})

        # WorldCover class names
        class_names = {
            "10": "Tree cover", "20": "Shrubland", "30": "Grassland",
            "40": "Cropland", "50": "Built-up", "60": "Bare/sparse veg",
            "70": "Snow/ice", "80": "Permanent water", "90": "Herbaceous wetland",
            "95": "Mangroves", "100": "Moss/lichen",
        }
        named = {class_names.get(str(k), f"Class {k}"): v
                 for k, v in class_hist.items()}

        tile = self._tile(img, {"min": 10, "max": 100,
                                "palette": ["006400","FFBB22","FFFF4C","F096FF",
                                            "FA0000","B4B4B4","F0F0F0","0064C8",
                                            "0096A0","00CF75","FAE6A0"]})
        return {
            "success": True,
            "tile_url": tile,
            "stats": {"class_counts": named, "region": label, "year": "2021"},
        }

    # ------------------------------------------------------------------
    # 3. Heatwaves — MODIS LST anomaly
    # ------------------------------------------------------------------

    def _analyze_heatwaves(self, region_id: str, date: str,
                            bbox: Optional[List[float]]) -> Dict:
        ee = self._gee._ee
        aoi, label, level = self._get_aoi(region_id, bbox)
        end = datetime.strptime(date, "%Y-%m-%d")
        start      = (end - timedelta(days=20)).strftime("%Y-%m-%d")
        # 3-year baseline is statistically robust and substantially faster
        # than 5 years (fewer images for GEE to process server-side).
        base_start = (end - timedelta(days=365 * 3)).strftime("%Y-%m-%d")

        # Apply scale+offset on the mean image, not per-image (much faster).
        def _lst_mean(d0, d1):
            return (ee.ImageCollection("MODIS/061/MOD11A1")
                      .filterBounds(aoi).filterDate(d0, d1)
                      .select("LST_Day_1km")
                      .mean()
                      .multiply(0.02).subtract(273.15))

        recent   = _lst_mean(start, date).clip(aoi)
        baseline = _lst_mean(base_start, start)
        anomaly  = recent.subtract(baseline)

        stats = anomaly.reduceRegion(
            reducer=ee.Reducer.mean().combine(ee.Reducer.max(), "", True),
            geometry=aoi, scale=self._s(level, 1000, 1000, 250), maxPixels=1e8, bestEffort=True,
        ).getInfo()
        mean_anom = stats.get("LST_Day_1km_mean", 0) or 0
        max_anom  = stats.get("LST_Day_1km_max", 0)  or 0
        heatwave  = bool(mean_anom > 3.0)

        tile = self._tile(anomaly, {
            "min": -5, "max": 10,
            "palette": ["313695","4575b4","74add1","abd9e9","e0f3f8",
                        "ffffbf","fee090","fdae61","f46d43","d73027","a50026"],
        })
        return {
            "success": True,
            "tile_url": tile,
            "stats": {
                "anomaly_mean_c": mean_anom,
                "anomaly_max_c":  max_anom,
                "heatwave_detected": heatwave,
                "region": label,
                "period": f"{start} → {date}",
            },
        }

    # ------------------------------------------------------------------
    # 4. Fires — FIRMS VIIRS
    # ------------------------------------------------------------------

    def _analyze_fires(self, region_id: str, date: str,
                       bbox: Optional[List[float]]) -> Dict:
        ee = self._gee._ee
        aoi, label, level = self._get_aoi(region_id, bbox)
        end = datetime.strptime(date, "%Y-%m-%d")
        start = (end - timedelta(days=7)).strftime("%Y-%m-%d")

        # NOAA-20 VIIRS (375 m, Oct 2023 →) catches the small agricultural and
        # bush fires typical of Nigeria; MODIS FIRMS (1 km, 2000 →) covers
        # older dates.  Both collections hold only fire pixels (rest masked).
        viirs = (ee.ImageCollection("NASA/LANCE/NOAA20_VIIRS/C2")
                   .filterBounds(aoi).filterDate(start, date))
        modis = (ee.ImageCollection("FIRMS")
                   .filterBounds(aoi).filterDate(start, date))
        n_viirs, n_modis = ee.List([viirs.size(), modis.size()]).getInfo()

        if not n_viirs and not n_modis:
            return {
                "success": True,
                "tile_url": None,
                "stats": {
                    "active_fires": 0,
                    "estimated_fire_area_km2": 0.0,
                    "region": label,
                    "period": f"{start} → {date}",
                    "note": "No fire detections in this period/region.",
                },
            }

        if n_viirs:
            source, scale, band = "NOAA-20 VIIRS 375 m", 375, "frp"
            intensity = viirs.select(band).max()                 # MW
            vis = {"min": 0, "max": 50,
                   "palette": ["yellow", "orange", "red", "darkred"]}
        else:
            source, scale, band = "MODIS 1 km (FIRMS)", 1000, "T21"
            intensity = modis.select(band).max()                 # K
            vis = {"min": 300, "max": 400,
                   "palette": ["yellow", "orange", "red", "darkred"]}
        intensity = intensity.clip(aoi)
        fire_px = intensity.mask().rename("fire").selfMask()      # 1 where detected

        region_kw = dict(geometry=aoi, scale=scale, maxPixels=1e10,
                         bestEffort=True, tileScale=4)
        stats_raw = ee.Dictionary({
            "pixels": fire_px.reduceRegion(reducer=ee.Reducer.count(), **region_kw).get("fire"),
            "area_m2": fire_px.multiply(ee.Image.pixelArea())
                              .reduceRegion(reducer=ee.Reducer.sum(), **region_kw).get("fire"),
            "intensity": intensity.reduceRegion(
                reducer=ee.Reducer.max().combine(ee.Reducer.mean(), "", True), **region_kw),
        }).getInfo()
        inten = stats_raw.get("intensity") or {}

        stats = {
            "active_fires": int(stats_raw.get("pixels") or 0),     # fire pixels detected
            "estimated_fire_area_km2": round((stats_raw.get("area_m2") or 0) / 1e6, 2),
            "source": source,
            "region": label,
            "period": f"{start} → {date}",
        }
        if n_viirs:
            stats["max_frp_mw"] = inten.get("frp_max")
            stats["mean_frp_mw"] = inten.get("frp_mean")
        else:
            stats["max_brightness_k"] = inten.get("T21_max")
            stats["mean_brightness_k"] = inten.get("T21_mean")

        return {
            "success": True,
            "tile_url": self._tile(intensity, vis),
            "stats": stats,
        }

    # ------------------------------------------------------------------
    # 5. Temperature — MODIS LST
    # ------------------------------------------------------------------

    def _analyze_temperature(self, region_id: str, date: str,
                              bbox: Optional[List[float]]) -> Dict:
        ee = self._gee._ee
        aoi, label, level = self._get_aoi(region_id, bbox)
        end = datetime.strptime(date, "%Y-%m-%d")

        # 20-day window handles the 7-14 day MODIS ingestion lag.  LST is
        # clear-sky only, and in the rainy season a small LGA (e.g. in Lagos)
        # can be cloud-covered for weeks — widen to 60 days before giving up.
        for window in (20, 60):
            start = (end - timedelta(days=window)).strftime("%Y-%m-%d")
            lst = (ee.ImageCollection("MODIS/061/MOD11A1")
                     .filterBounds(aoi).filterDate(start, date)
                     .select("LST_Day_1km")
                     .mean()
                     .multiply(0.02).subtract(273.15)
                     .clip(aoi))

            stats = lst.reduceRegion(
                reducer=ee.Reducer.mean().combine(ee.Reducer.min(), "", True)
                                   .combine(ee.Reducer.max(), "", True),
                geometry=aoi, scale=self._s(level, 1000, 1000, 250), maxPixels=1e8, bestEffort=True,
            ).getInfo()
            if stats and stats.get("LST_Day_1km_mean") is not None:
                break
        else:
            return {"success": False,
                    "message": "No cloud-free MODIS LST imagery in the last 60 days.",
                    "tile_url": None, "stats": {}}

        tile = self._tile(lst, {
            "min": -10, "max": 50,
            "palette": ["040274","040281","0502a3","0502b8","0602ff",
                        "235cb1","307ef3","269db1","30c8e2","32d3ef",
                        "3ae237","b5e22e","d6e21f","fff705","ffd611",
                        "ffb613","ff8b13","ff6e08","ff500d","ff0000",
                        "de0101","c21301"],
        })
        return {
            "success": True,
            "tile_url": tile,
            "stats": {
                "lst_mean_c": stats.get("LST_Day_1km_mean"),
                "lst_min_c":  stats.get("LST_Day_1km_min"),
                "lst_max_c":  stats.get("LST_Day_1km_max"),
                "region": label,
                "period": f"{start} → {date}",
            },
        }

    # ------------------------------------------------------------------
    # 6. Soil Moisture — SMAP
    # ------------------------------------------------------------------

    def _analyze_soil_moisture(self, region_id: str, date: str,
                                bbox: Optional[List[float]]) -> Dict:
        ee = self._gee._ee
        aoi, label, level = self._get_aoi(region_id, bbox)
        end = datetime.strptime(date, "%Y-%m-%d")
        start = (end - timedelta(days=10)).strftime("%Y-%m-%d")

        # SMAP L4 v008, 3-hourly 9 km.  (NASA_USDA/HSL/SMAP10KM_soil_moisture,
        # used previously, stopped on 2022-08-02 and returned only nulls.)
        col = (ee.ImageCollection("NASA/SMAP/SPL4SMGP/008")
                 .filterBounds(aoi).filterDate(start, date)
                 .select("sm_surface"))   # surface (0–5 cm) soil moisture
        img = col.mean().clip(aoi)

        stats = img.reduceRegion(
            reducer=ee.Reducer.mean().combine(ee.Reducer.min(), "", True)
                               .combine(ee.Reducer.max(), "", True),
            geometry=aoi, scale=self._s(level, 11000, 5000, 1000), maxPixels=1e8, bestEffort=True,
        ).getInfo()

        if not stats or stats.get("sm_surface_mean") is None:
            return {"success": False,
                    "message": "No SMAP soil moisture data available for this period.",
                    "tile_url": None, "stats": {}}

        tile = self._tile(img, {
            "min": 0.0, "max": 0.5,
            "palette": ["d29642","eecfa8","fffde4","dcf3ff","a9dbff","51b2d4","0a5994"],
        })
        return {
            "success": True,
            "tile_url": tile,
            "stats": {
                "ssm_mean_m3m3": stats.get("sm_surface_mean"),
                "ssm_min_m3m3":  stats.get("sm_surface_min"),
                "ssm_max_m3m3":  stats.get("sm_surface_max"),
                "region": label,
                "period": f"{start} → {date}",
            },
        }

    # ------------------------------------------------------------------
    # 7. Deformation — Sentinel-1 coherence proxy
    # ------------------------------------------------------------------

    def _analyze_deformation(self, region_id: str, date: str,
                              bbox: Optional[List[float]]) -> Dict:
        ee = self._gee._ee
        aoi, label, level = self._get_aoi(region_id, bbox)
        end = datetime.strptime(date, "%Y-%m-%d")
        start = (end - timedelta(days=30)).strftime("%Y-%m-%d")
        ref_start = (end - timedelta(days=90)).strftime("%Y-%m-%d")
        ref_end   = (end - timedelta(days=60)).strftime("%Y-%m-%d")

        def s1_mean(d0, d1):
            return (ee.ImageCollection("COPERNICUS/S1_GRD")
                      .filter(ee.Filter.eq("instrumentMode", "IW"))
                      .filter(ee.Filter.listContains("transmitterReceiverPolarisation", "VV"))
                      .filter(ee.Filter.eq("orbitProperties_pass", "DESCENDING"))
                      .filterBounds(aoi).filterDate(d0, d1)
                      .select("VV").mean())

        recent = s1_mean(start, date).clip(aoi)
        ref    = s1_mean(ref_start, ref_end)
        diff   = recent.subtract(ref).abs()   # large diff → deformation / change

        stats = diff.reduceRegion(
            reducer=ee.Reducer.mean().combine(ee.Reducer.max(), "", True),
            geometry=aoi, scale=self._s(level, 500, 200, 50), maxPixels=1e8, bestEffort=True,
        ).getInfo()
        mean_diff = stats.get("VV_mean", 0) or 0
        deform_flag = bool(abs(mean_diff) > 3.0)

        tile = self._tile(diff, {
            "min": 0, "max": 10,
            "palette": ["white","lightyellow","yellow","orange","red","darkred"],
        })
        return {
            "success": True,
            "tile_url": tile,
            "stats": {
                "backscatter_diff_mean_db": mean_diff,
                "backscatter_diff_max_db":  stats.get("VV_max"),
                "deformation_detected": deform_flag,
                "region": label,
                "period": f"{start} → {date}",
            },
        }

    # ------------------------------------------------------------------
    # 8. Forest Structure — Hansen GFC
    # ------------------------------------------------------------------

    def _analyze_forest_structure(self, region_id: str, date: str,
                                   bbox: Optional[List[float]]) -> Dict:
        ee = self._gee._ee
        aoi, label, level = self._get_aoi(region_id, bbox)
        gfc = ee.Image("UMD/hansen/global_forest_change_2023_v1_11").clip(aoi)
        canopy   = gfc.select("treecover2000")
        loss     = gfc.select("loss")
        loss_yr  = gfc.select("lossyear")

        # Total canopy area & loss area
        canopy_binary = canopy.gt(10)   # > 10 % cover = forest
        # 30 m over all of Nigeria is ~1e9 pixels per sum — too slow for a
        # web request; coarser scales for larger units.
        area_scale = self._s(level, 300, 100, 30)
        forest_km2 = self._gee.compute_area_km2(canopy_binary, aoi, scale=area_scale)
        loss_km2   = self._gee.compute_area_km2(loss, aoi, scale=area_scale)

        stats_cc = canopy.reduceRegion(
            reducer=ee.Reducer.mean(),
            geometry=aoi, scale=self._s(level, 1000, 300, 30), maxPixels=1e8, bestEffort=True,
        ).getInfo()

        tile = self._tile(canopy, {
            "min": 0, "max": 100,
            "palette": ["fffde4","cde6c0","78c679","31a354","006837"],
        })
        return {
            "success": True,
            "tile_url": tile,
            "stats": {
                "canopy_cover_mean_pct": stats_cc.get("treecover2000"),
                "forest_area_km2":       forest_km2,
                "forest_loss_km2":       loss_km2,
                "region": label,
                "year": "2023 baseline",
            },
        }

    # ------------------------------------------------------------------
    # 9. Elevation — SRTM
    # ------------------------------------------------------------------

    def _analyze_elevation(self, region_id: str, date: str,
                            bbox: Optional[List[float]]) -> Dict:
        ee = self._gee._ee
        aoi, label, level = self._get_aoi(region_id, bbox)
        dem = ee.Image("USGS/SRTMGL1_003").clip(aoi)
        terrain = ee.Algorithms.Terrain(dem)
        elevation = terrain.select("elevation")
        slope     = terrain.select("slope")

        stats = elevation.addBands(slope).reduceRegion(
            reducer=ee.Reducer.mean().combine(ee.Reducer.min(), "", True)
                               .combine(ee.Reducer.max(), "", True),
            geometry=aoi, scale=self._s(level, 1000, 300, 30), maxPixels=1e8, bestEffort=True,
        ).getInfo()

        tile = self._tile(elevation, {
            "min": 0, "max": 3000,
            "palette": ["006633","E5FFCC","662A00","D8D8D8","F5F5F5"],
        })
        return {
            "success": True,
            "tile_url": tile,
            "stats": {
                "elevation_mean_m": stats.get("elevation_mean"),
                "elevation_min_m":  stats.get("elevation_min"),
                "elevation_max_m":  stats.get("elevation_max"),
                "slope_mean_deg":   stats.get("slope_mean"),
                "slope_max_deg":    stats.get("slope_max"),
                "region": label,
            },
        }

    # ------------------------------------------------------------------
    # 10. Rainfall — CHIRPS
    # ------------------------------------------------------------------

    def _analyze_rainfall(self, region_id: str, date: str,
                           bbox: Optional[List[float]]) -> Dict:
        ee = self._gee._ee
        aoi, label, level = self._get_aoi(region_id, bbox)
        end = datetime.strptime(date, "%Y-%m-%d")
        # CHIRPS daily lags ~4 weeks in Earth Engine.  Search a 60-day window
        # and take the 5 most-recent available days so we always obtain data
        # regardless of publication delay.
        window_start = (end - timedelta(days=60)).strftime("%Y-%m-%d")

        col = (ee.ImageCollection("UCSB-CHG/CHIRPS/DAILY")
                 .filterBounds(aoi)
                 .filterDate(window_start, date)
                 .select("precipitation")
                 .sort("system:time_start", False)
                 .limit(5))   # 5 most-recent available days

        accum = col.sum().clip(aoi)   # up to 5-day accumulation in mm

        stats = accum.reduceRegion(
            reducer=ee.Reducer.mean().combine(ee.Reducer.max(), "", True),
            geometry=aoi, scale=self._s(level, 5500, 5500, 1000), maxPixels=1e8, bestEffort=True,
        ).getInfo()

        if not stats or stats.get("precipitation_mean") is None:
            return {"success": False,
                    "message": "No CHIRPS rainfall data available for this period.",
                    "tile_url": None, "stats": {}}

        tile = self._tile(accum, {
            "min": 0, "max": 150,
            "palette": ["white","lightblue","steelblue","blue","darkblue","purple"],
        })
        return {
            "success": True,
            "tile_url": tile,
            "stats": {
                "precip_accum_mean_mm": stats.get("precipitation_mean"),
                "precip_accum_max_mm":  stats.get("precipitation_max"),
                "region": label,
                "period": f"{window_start} → {date} (most recent 5 days)",
            },
        }

    # ------------------------------------------------------------------
    # 11. Snow Cover — MODIS
    # ------------------------------------------------------------------

    def _analyze_snow(self, region_id: str, date: str,
                      bbox: Optional[List[float]]) -> Dict:
        ee = self._gee._ee
        aoi, label, level = self._get_aoi(region_id, bbox)
        end = datetime.strptime(date, "%Y-%m-%d")
        start = (end - timedelta(days=8)).strftime("%Y-%m-%d")

        col = (ee.ImageCollection("MODIS/061/MOD10A1")
                 .filterBounds(aoi).filterDate(start, date)
                 .select("NDSI_Snow_Cover"))
        img = col.mean().clip(aoi)   # 0–100 snow cover %

        stats = img.reduceRegion(
            reducer=ee.Reducer.mean().combine(ee.Reducer.max(), "", True),
            geometry=aoi, scale=self._s(level, 1000, 500, 250), maxPixels=1e8, bestEffort=True,
        ).getInfo()
        mean_snow = stats.get("NDSI_Snow_Cover_mean", 0) or 0

        # Area with > 50 % snow cover
        snow_binary = img.gt(50)
        snow_area_km2 = self._gee.compute_area_km2(
            snow_binary, aoi, scale=self._s(level, 1000, 500, 250))

        tile = self._tile(img, {
            "min": 0, "max": 100,
            "palette": ["#1A1A1A","#4169E1","#87CEEB","#E0E0E0","white"],
        })
        return {
            "success": True,
            "tile_url": tile,
            "stats": {
                "snow_cover_mean_pct": mean_snow,
                "snow_cover_max_pct":  stats.get("NDSI_Snow_Cover_max"),
                "snow_area_km2":       snow_area_km2,
                "region": label,
                "period": f"{start} → {date}",
            },
        }

    # ------------------------------------------------------------------
    # 12. Crop Stress — Sentinel-2 NDWI / EVI
    # ------------------------------------------------------------------

    def _analyze_crop_stress(self, region_id: str, date: str,
                              bbox: Optional[List[float]]) -> Dict:
        ee = self._gee._ee
        aoi, label, level = self._get_aoi(region_id, bbox)
        end = datetime.strptime(date, "%Y-%m-%d")
        start = (end - timedelta(days=20)).strftime("%Y-%m-%d")

        col = (ee.ImageCollection("COPERNICUS/S2_SR_HARMONIZED")
                 .filterBounds(aoi).filterDate(start, date)
                 .filter(ee.Filter.lt("CLOUDY_PIXEL_PERCENTAGE", 30))
                 .select(["B3","B4","B8","B11"]))

        if col.size().getInfo() == 0:
            return {"success": False, "message": "No Sentinel-2 imagery available for period.",
                    "tile_url": None, "stats": {}}

        img = col.median().clip(aoi)
        ndwi = img.normalizedDifference(["B3","B8"]).rename("NDWI")
        evi  = img.expression(
            "2.5 * (NIR - RED) / (NIR + 6*RED - 7.5*BLUE + 1)",
            {"NIR": img.select("B8").divide(10000),
             "RED": img.select("B4").divide(10000),
             "BLUE": img.select("B3").divide(10000)},
        ).rename("EVI")

        stress = ndwi.multiply(-1).add(evi.multiply(-0.5)).rename("stress")
        stats = ndwi.addBands(evi).reduceRegion(
            reducer=ee.Reducer.mean(),
            geometry=aoi, scale=self._s(level, 1000, 300, 30), maxPixels=1e8, bestEffort=True,
        ).getInfo()

        tile = self._tile(evi, {
            "min": 0, "max": 1,
            "palette": ["#8B4513","#F4A460","#FFFF00","#9ACD32","#228B22"],
        })
        return {
            "success": True,
            "tile_url": tile,
            "stats": {
                "ndwi_mean":  stats.get("NDWI"),
                "evi_mean":   stats.get("EVI"),
                "crop_water_stress": bool((stats.get("NDWI") or 0) < -0.1),
                "region": label,
                "period": f"{start} → {date}",
            },
        }

    # ------------------------------------------------------------------
    # 13. Pollution — Sentinel-5P NO₂
    # ------------------------------------------------------------------

    def _analyze_pollution(self, region_id: str, date: str,
                            bbox: Optional[List[float]]) -> Dict:
        ee = self._gee._ee
        aoi, label, level = self._get_aoi(region_id, bbox)
        end = datetime.strptime(date, "%Y-%m-%d")
        start = (end - timedelta(days=14)).strftime("%Y-%m-%d")

        img = (ee.ImageCollection("COPERNICUS/S5P/NRTI/L3_NO2")
                 .filterBounds(aoi).filterDate(start, date)
                 .select("tropospheric_NO2_column_number_density")
                 .mean()
                 .multiply(1e5)   # convert to ×10⁻⁵ mol/m²
                 .clip(aoi))

        stats = img.reduceRegion(
            reducer=ee.Reducer.mean().combine(ee.Reducer.max(), "", True),
            geometry=aoi, scale=1000, maxPixels=1e8, bestEffort=True,
        ).getInfo()

        if not stats or stats.get("tropospheric_NO2_column_number_density_mean") is None:
            return {"success": False, "message": "No Sentinel-5P NO\u2082 imagery available for this period.",
                    "tile_url": None, "stats": {}}
        mean_no2 = stats.get("tropospheric_NO2_column_number_density_mean", 0) or 0
        high_poll = bool(mean_no2 > 5.0)   # >5×10⁻⁵ mol/m² = elevated NO₂

        tile = self._tile(img, {
            "min": 0, "max": 15,
            "palette": ["black","blue","purple","red","orange","yellow","white"],
        })
        return {
            "success": True,
            "tile_url": tile,
            "stats": {
                "no2_mean_1e5_mol_m2": mean_no2,
                "no2_max_1e5_mol_m2":  stats.get("tropospheric_NO2_column_number_density_max"),
                "high_pollution": high_poll,
                "region": label,
                "period": f"{start} → {date} (14-day mean)",
            },
        }

    # ------------------------------------------------------------------
    # 14. Sea Level / Water Storage — GRACE-FO
    # ------------------------------------------------------------------

    def _analyze_sea_level(self, region_id: str, date: str,
                           bbox: Optional[List[float]]) -> Dict:
        ee = self._gee._ee
        aoi, label, level = self._get_aoi(region_id, bbox)
        # GRACE/GRACE-FO monthly mascons are released months late (the LAND
        # grids used previously ended in 2017), so use the latest month
        # available on or before the requested date and report which it is.
        col = (ee.ImageCollection("NASA/GRACE/MASS_GRIDS_V04/MASCON_CRI")
                 .filterDate("2002-01-01", date)
                 .select("lwe_thickness"))
        latest = ee.Image(col.sort("system:time_start", False).first())
        img = latest.clip(aoi)   # cm equivalent water height

        info = ee.Dictionary({
            "n": col.size(),
            "month": ee.Algorithms.If(
                col.size().gt(0),
                ee.Date(latest.get("system:time_start")).format("YYYY-MM"), None),
            "stats": ee.Algorithms.If(col.size().gt(0), img.reduceRegion(
                reducer=ee.Reducer.mean().combine(ee.Reducer.min(), "", True)
                                   .combine(ee.Reducer.max(), "", True),
                # Mascons are ~55 km; finer scales keep small LGAs from
                # falling between pixel centres.
                geometry=aoi, scale=self._s(level, 25000, 10000, 2000),
                maxPixels=1e8, bestEffort=True,
            ), None),
        }).getInfo()
        if not info.get("n") or not info.get("stats"):
            return {"success": False,
                    "message": "No GRACE water-storage data available on or before this date.",
                    "tile_url": None, "stats": {}}
        stats = info["stats"]
        start = f"{info['month']} (latest GRACE month)"
        mean_lwe = stats.get("lwe_thickness_mean", 0) or 0

        tile = self._tile(img, {
            "min": -20, "max": 20,
            "palette": ["#8B0000","#FF4500","#FF8C00","#FFD700",
                        "#ADFF2F","#00CED1","#1E90FF","#00008B"],
        })
        return {
            "success": True,
            "tile_url": tile,
            "stats": {
                "lwe_mean_cm":   mean_lwe,
                "lwe_min_cm":    stats.get("lwe_thickness_min"),
                "lwe_max_cm":    stats.get("lwe_thickness_max"),
                "water_anomaly_positive": bool(mean_lwe > 2.0),
                "region": label,
                "period": start,
            },
        }

    # ------------------------------------------------------------------
    # 15. Glacier — Landsat NDSI + MODIS snow
    # ------------------------------------------------------------------

    def _analyze_glacier(self, region_id: str, date: str,
                          bbox: Optional[List[float]]) -> Dict:
        ee = self._gee._ee
        aoi, label, level = self._get_aoi(region_id, bbox)
        end = datetime.strptime(date, "%Y-%m-%d")
        start = (end - timedelta(days=30)).strftime("%Y-%m-%d")

        # Landsat 8/9 NDSI (snow/ice index)
        col = (ee.ImageCollection("LANDSAT/LC09/C02/T1_L2")
                 .filterBounds(aoi).filterDate(start, date)
                 .filter(ee.Filter.lt("CLOUD_COVER", 20))
                 .select(["SR_B3","SR_B6"]))   # Green, SWIR

        if col.size().getInfo() == 0:
            # Fallback: MODIS snow
            return self._analyze_snow(region_id, date, bbox)

        img = col.median().multiply(0.0000275).add(-0.2).clip(aoi)
        ndsi = img.normalizedDifference(["SR_B3","SR_B6"]).rename("NDSI")
        glacier = ndsi.gt(0.4)   # NDSI > 0.4 → snow/ice
        glacier_km2 = self._gee.compute_area_km2(glacier, aoi, scale=self._s(level, 300, 100, 30))

        stats = ndsi.reduceRegion(
            reducer=ee.Reducer.mean().combine(ee.Reducer.max(), "", True),
            geometry=aoi, scale=self._s(level, 1000, 300, 100), maxPixels=1e8, bestEffort=True,
        ).getInfo()

        tile = self._tile(ndsi, {
            "min": -0.5, "max": 1.0,
            "palette": ["#8B4513","#F5DEB3","#87CEEB","#B0C4DE","white"],
        })
        return {
            "success": True,
            "tile_url": tile,
            "stats": {
                "ndsi_mean":         stats.get("NDSI_mean"),
                "ndsi_max":          stats.get("NDSI_max"),
                "glacier_ice_km2":   glacier_km2,
                "region": label,
                "period": f"{start} → {date}",
            },
        }

    # ==================================================================
    # TIME SERIES / TREND ANALYSIS  (public)
    # ==================================================================

    def get_trend(
        self,
        layer_id: str,
        region_id: str,
        date: Optional[str] = None,
        months: int = 12,
    ) -> Dict[str, Any]:
        """
        Compute a monthly time series for *layer_id* over the past *months*
        months, plus 2 correlated layer series and Pearson r coefficients.

        Returns::

            {
              success, layer_id, region_id, region_label, months,
              primary:    {label, unit, series: [{month, value}]},
              correlates: [{id, label, unit, series, correlation, description}],
              insight:    str,
            }

        Results are cached for 24 h (historical data changes slowly).
        """
        date   = date or datetime.utcnow().strftime("%Y-%m-%d")
        months = max(3, min(24, int(months)))
        cache_key = f"trend:{layer_id}:{region_id}:{date[:7]}:{months}"

        cached_bytes = self._gee.cache.get(cache_key)
        if cached_bytes is not None:
            try:
                return _decompress(cached_bytes)
            except Exception:
                pass

        if layer_id not in LAYER_CATALOGUE:
            return {"success": False, "message": f"Unknown layer: {layer_id}"}

        cfg = self._SERIES_CFG.get(layer_id)
        if cfg is None:
            return {
                "success": False,
                "message": (
                    f"Time series is not available for the static layer '{layer_id}'."
                ),
            }

        try:
            ee = self._gee._ee
            aoi, label, level = self._get_aoi(region_id, None)
            correlate_ids = [c for c in self._LAYER_CORRELATES.get(layer_id, [])[:2]
                             if self._SERIES_CFG.get(c)]

            # Datasets published months late (GRACE) would give an all-null
            # window ending today; end the window — for every series, so the
            # correlations line up — at the latest month actually available.
            end_date = date
            if cfg.get("lagged"):
                last = (ee.ImageCollection(cfg["col"]).filterDate("2002-01-01", date)
                          .aggregate_max("system:time_start").getInfo())
                if last:
                    end_date = datetime.utcfromtimestamp(last / 1000).strftime("%Y-%m-%d")

            # Primary + correlates in ONE getInfo: Earth Engine evaluates the
            # series in parallel server-side, so no Python threads are needed
            # (PythonAnywhere web apps don't support them).
            series = {"_primary": self._monthly_series(cfg, aoi, end_date, months, level)}
            for c_id in correlate_ids:
                series[c_id] = self._monthly_series(
                    self._SERIES_CFG[c_id], aoi, end_date, months, level)
            info = ee.Dictionary(series).getInfo()

            primary_series = self._parse_series(info["_primary"], cfg)
            corr_results = {c: (c, self._parse_series(info[c], self._SERIES_CFG[c]))
                            for c in correlate_ids}

            primary = {
                "label":  cfg.get("label", layer_id),
                "unit":   cfg.get("unit", ""),
                "series": primary_series,
            }

            correlates = []
            for c_id in correlate_ids:
                _, c_series = corr_results.get(c_id, (c_id, []))
                c_cfg = self._SERIES_CFG.get(c_id) or {}
                r     = _pearson(primary_series, c_series)
                correlates.append({
                    "id":          c_id,
                    "label":       c_cfg.get("label", LAYER_CATALOGUE.get(c_id, {}).get("label", c_id)),
                    "unit":        c_cfg.get("unit", ""),
                    "series":      c_series,
                    "correlation": r,
                    "description": self._CORRELATION_DESC.get((layer_id, c_id), ""),
                })

            insight = self._trend_insight(layer_id, primary_series, correlates)

            result = {
                "success":      True,
                "layer_id":     layer_id,
                "region_id":    region_id,
                "region_label": label,
                "months":       months,
                "primary":      primary,
                "correlates":   correlates,
                "insight":      insight,
            }
            self._gee.cache.set(cache_key, _compress(result), ttl=86400)
            return result

        except Exception as exc:
            logger.exception("get_trend error for %s/%s: %s", layer_id, region_id, exc)
            return {"success": False, "message": str(exc)}

    # ------------------------------------------------------------------
    # Monthly series computation helpers
    # ------------------------------------------------------------------

    def _monthly_series(
        self, cfg: Dict, aoi, end_date: str, months: int, level: str
    ):
        """
        Server-side ee.List of N monthly {idx, month, value} features (lazy —
        evaluated by the caller's single getInfo).  Uses ee.List.sequence +
        map() to avoid N round-trips to the GEE API.  A plain List rather
        than a FeatureCollection: nested inside an ee.Dictionary, getInfo()
        returns only a FeatureCollection's schema, not its features.
        """
        ee = self._gee._ee

        # Starting month
        end = datetime.strptime(end_date, "%Y-%m-%d")
        y, mo = end.year, end.month
        mo -= (months - 1)
        while mo <= 0:
            mo += 12
            y  -= 1
        start_ee = ee.Date(f"{y:04d}-{mo:02d}-01")

        # Coarse datasets (GRACE ~55 km, SMAP 9 km) need a finer sampling
        # scale for a small state or LGA to contain any pixel centre.
        scale = cfg.get("scale", 5000)
        if level != "country":
            scale = min(scale, 5000 if level != "lga" else 1000)

        if cfg.get("reducer") == "count_threshold":
            return self._fire_monthly_series(cfg, aoi, start_ee, months, scale)

        col_id  = cfg["col"]
        band    = cfg["band"]
        mul     = cfg.get("mul")
        add_val = cfg.get("add")
        use_sum = cfg.get("reducer") == "sum"
        sample_hour = cfg.get("sample_hour")

        # A fully-masked placeholder so reduceRegion returns null for empty months
        _empty_img = ee.Image.constant(0).rename(band).updateMask(ee.Image.constant(0))

        def make_feature(m):
            m   = ee.Number(m)
            ms  = start_ee.advance(m, "month")
            me  = ms.advance(1, "month")
            col = (
                ee.ImageCollection(col_id)
                  .filterBounds(aoi)
                  .filterDate(ms, me)
                  .select(band)
            )
            if sample_hour is not None:
                # Sub-daily products: one image per day is plenty for a
                # monthly mean and keeps EE under its memory limit.
                col = col.filter(ee.Filter.calendarRange(sample_hour, sample_hour, "hour"))
            # Guard: empty collection → 0-band image which breaks multiply/add
            reduced = col.sum() if use_sum else col.mean()
            img = ee.Image(ee.Algorithms.If(col.size().gt(0), reduced, _empty_img))
            if mul is not None:
                img = img.multiply(mul)
            if add_val is not None:
                img = img.add(add_val)
            val = img.reduceRegion(
                reducer    = ee.Reducer.mean(),
                geometry   = aoi,
                scale      = scale,
                maxPixels  = 1e9,
                bestEffort = True,
            ).get(band)
            return ee.Feature(None, {"idx": m, "month": ms.format("YYYY-MM"), "value": val})

        return ee.List.sequence(0, months - 1).map(make_feature)

    @staticmethod
    def _parse_series(features: List[Dict], cfg: Dict) -> List[Dict]:
        """Turn a getInfo'd list of monthly features into [{month, value}]."""
        decimals = 1 if cfg.get("reducer") == "count_threshold" else 4
        feats = sorted(features, key=lambda f: f["properties"].get("idx", 0))
        series = []
        for f in feats:
            p = f["properties"]
            v = p.get("value")
            series.append({
                "month": p.get("month", ""),
                "value": round(float(v), decimals) if v is not None else None,
            })
        return series

    def _fire_monthly_series(self, cfg: Dict, aoi, start_ee, months: int, scale: int):
        """Count FIRMS fire pixels (T21 > threshold) per month (lazy ee.List)."""
        ee        = self._gee._ee
        col_id    = cfg["col"]
        band      = cfg["band"]
        threshold = cfg.get("threshold", 330)

        _empty_fire = ee.Image.constant(0).rename(band).updateMask(ee.Image.constant(0))

        def make_feature(m):
            m   = ee.Number(m)
            ms  = start_ee.advance(m, "month")
            me  = ms.advance(1, "month")
            col = (
                ee.ImageCollection(col_id)
                  .filterBounds(aoi)
                  .filterDate(ms, me)
                  .select(band)
            )
            fire_sum = ee.Image(ee.Algorithms.If(
                col.size().gt(0),
                col.map(lambda img: img.gt(threshold).rename(band)).sum(),
                _empty_fire,
            ))
            val = fire_sum.reduceRegion(
                reducer    = ee.Reducer.sum(),
                geometry   = aoi,
                scale      = scale,
                maxPixels  = 1e10,
                bestEffort = True,
            ).get(band)
            return ee.Feature(None, {"idx": m, "month": ms.format("YYYY-MM"), "value": val})

        return ee.List.sequence(0, months - 1).map(make_feature)

    def _trend_insight(
        self, layer_id: str, series: List[Dict], correlates: List[Dict]
    ) -> str:
        """Generate a concise data-driven insight from a trend series."""
        vals  = [p["value"] for p in series if p["value"] is not None]
        label = LAYER_CATALOGUE.get(layer_id, {}).get("label", layer_id)

        if len(vals) < 3:
            return f"Insufficient data to determine a trend for {label}."

        pct = _linear_pct_change(vals)
        if pct is None:
            trend_str = f"{label} data is available for this period"
        elif abs(pct) < 5:
            trend_str = f"{label} remained relatively stable over the past {len(vals)} months"
        elif pct > 0:
            trend_str = f"{label} increased by {abs(pct):.1f}% over the past {len(vals)} months"
        else:
            trend_str = f"{label} decreased by {abs(pct):.1f}% over the past {len(vals)} months"

        strong = sorted(
            [c for c in correlates if c.get("correlation") is not None],
            key=lambda x: abs(x.get("correlation") or 0),
            reverse=True,
        )
        parts = [trend_str]
        if strong and abs(strong[0].get("correlation") or 0) > 0.4:
            c         = strong[0]
            direction = "positively" if (c["correlation"] or 0) > 0 else "negatively"
            parts.append(
                f"It is {direction} correlated with {c['label']} (r={c['correlation']:.2f})"
            )
            if c.get("description"):
                parts.append(c["description"])

        return ". ".join(p.rstrip(".") for p in parts) + "."

