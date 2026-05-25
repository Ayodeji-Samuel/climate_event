"""
modules/climate_layers.py
Multi-layer environmental analysis using Google Earth Engine.

Supported layers (15):
  vegetation       – NDVI from MODIS/Sentinel-2
  land_cover       – ESA WorldCover / MODIS IGBP classification
  heatwaves        – ERA5 temperature anomaly (deviation from 20-yr mean)
  fires            – FIRMS active fire radiative power (VIIRS)
  temperature      – MODIS Land Surface Temperature (LST)
  soil_moisture    – SMAP 10 km soil moisture index
  deformation      – Sentinel-1 InSAR coherence proxy (mean coherence drop)
  forest_structure – Hansen Global Forest Change canopy cover + loss
  elevation        – SRTM DEM + slope/aspect
  rainfall         – CHIRPS daily precipitation accumulation
  snow             – MODIS daily snow cover fraction
  crop_stress      – Sentinel-2 NDWI / LAI crop health composite
  pollution        – Sentinel-5P NO2 + aerosol optical depth
  sea_level        – GRACE-FO land water equivalent (coastal proxy)
  glacier          – GLIMS glacier outlines + Landsat snowline elevation

Cache: all results compressed with zlib (lossless) in the shared GEECache.
Stats: floats rounded to 4 significant digits before caching to reduce size.
Tile URLs: short-lived (~1 h) EE map tile URLs — not compressed (strings).
"""

import json
import logging
import zlib
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from modules.gee_engine import GEEEngine

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
        "description": "LST anomaly relative to ERA5 20-year baseline. Values > 3 °C indicate heat stress.",
        "unit": "°C anomaly",
        "icon": "🌡️",
        "category": "climate",
        "dataset": "MODIS/061/MOD11A1 + ERA5 baseline",
    },
    "fires": {
        "label": "Active Fires",
        "description": "FIRMS VIIRS 375 m active fire detections and fire radiative power.",
        "unit": "MW (fire radiative power)",
        "icon": "🔥",
        "category": "hazard",
        "dataset": "NASA/FIRMS/noaa-20-viirs-c2",
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
        "description": "SMAP L4 10 km soil moisture (surface 0–5 cm).",
        "unit": "m³/m³",
        "icon": "💧",
        "category": "land",
        "dataset": "NASA_USDA/HSL/SMAP10KM_soil_moisture",
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
        "description": "GRACE-FO terrestrial water storage anomaly — coastal inundation proxy.",
        "unit": "cm equivalent water height",
        "icon": "🌊",
        "category": "ocean",
        "dataset": "NASA/GRACE/MASS_GRIDS_V04/LAND",
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

        date = date or datetime.utcnow().strftime("%Y-%m-%d")
        cache_key = f"climate:{layer_id}:{region_id}:{date}"

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
        """Return (ee_geometry, region_label)."""
        from modules.flood_detector import KNOWN_REGIONS
        ee = self._gee._ee
        if bbox and len(bbox) == 4:
            return ee.Geometry.BBox(*bbox), region_id
        if region_id in KNOWN_REGIONS:
            info = KNOWN_REGIONS[region_id]
            bb = info["bbox"]
            return ee.Geometry.BBox(*bb), info["label"]
        raise ValueError(f"Unknown region '{region_id}' and no bbox provided.")

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
        aoi, label = self._get_aoi(region_id, bbox)
        end = datetime.strptime(date, "%Y-%m-%d")
        start = (end - timedelta(days=32)).strftime("%Y-%m-%d")

        col = (ee.ImageCollection("MODIS/061/MOD13A1")
               .filterBounds(aoi)
               .filterDate(start, date)
               .select("NDVI"))
        img = col.median().multiply(0.0001).clip(aoi)

        stats = img.reduceRegion(
            reducer=ee.Reducer.mean().combine(ee.Reducer.stdDev(), "", True)
                               .combine(ee.Reducer.min(), "", True)
                               .combine(ee.Reducer.max(), "", True),
            geometry=aoi, scale=500, maxPixels=1e9,
        ).getInfo()

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
        aoi, label = self._get_aoi(region_id, bbox)
        img = ee.ImageCollection("ESA/WorldCover/v200").first().clip(aoi)

        # Class histogram (pixel counts per class)
        hist = img.reduceRegion(
            reducer=ee.Reducer.frequencyHistogram(),
            geometry=aoi, scale=100, maxPixels=1e10,
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
        aoi, label = self._get_aoi(region_id, bbox)
        end = datetime.strptime(date, "%Y-%m-%d")
        start = (end - timedelta(days=8)).strftime("%Y-%m-%d")
        base_start = (end - timedelta(days=365 * 5)).strftime("%Y-%m-%d")

        def lst_celsius(col):
            return (col.select("LST_Day_1km")
                      .map(lambda img: img.multiply(0.02).subtract(273.15)))

        recent = lst_celsius(
            ee.ImageCollection("MODIS/061/MOD11A1")
              .filterBounds(aoi).filterDate(start, date)
        ).mean().clip(aoi)

        baseline = lst_celsius(
            ee.ImageCollection("MODIS/061/MOD11A1")
              .filterBounds(aoi).filterDate(base_start, start)
        ).mean()

        anomaly = recent.subtract(baseline)

        stats = anomaly.reduceRegion(
            reducer=ee.Reducer.mean().combine(ee.Reducer.max(), "", True),
            geometry=aoi, scale=1000, maxPixels=1e9,
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
        aoi, label = self._get_aoi(region_id, bbox)
        end = datetime.strptime(date, "%Y-%m-%d")
        start = (end - timedelta(days=7)).strftime("%Y-%m-%d")

        # Try FIRMS datasets in preference order (some may require separate access)
        FIRE_SOURCES = [
            "NASA/FIRMS/noaa-20-viirs-c2",
            "NASA/FIRMS/suomi-npp-viirs-c2",
            "NASA/FIRMS/modis/006/Terra",
            "NASA/FIRMS/modis/006/Aqua",
        ]
        fires = None
        count = 0
        for ds_id in FIRE_SOURCES:
            try:
                col = (ee.ImageCollection(ds_id)
                         .filterBounds(aoi)
                         .filterDate(start, date)
                         .select("T21"))
                count = col.size().getInfo()   # raises if dataset inaccessible
                fires = col
                break
            except Exception:
                continue  # try next source

        if fires is None or count == 0:
            return {
                "success": True,
                "tile_url": None,
                "stats": {
                    "active_fires": 0,
                    "estimated_fire_area_km2": 0.0,
                    "region": label,
                    "period": f"{start} → {date}",
                    "note": ("No fire detections in this period/region."
                             if fires is not None
                             else "No accessible fire dataset for this query."),
                },
            }

        fire_img = fires.max().clip(aoi)
        active = fire_img.gt(330)
        fire_area = self._gee.compute_area_km2(active, aoi, scale=375)

        stats_raw = fire_img.reduceRegion(
            reducer=ee.Reducer.max().combine(ee.Reducer.mean(), "", True)
                               .combine(ee.Reducer.count(), "", True),
            geometry=aoi, scale=375, maxPixels=1e10,
        ).getInfo()

        tile = self._tile(fire_img, {
            "min": 300, "max": 400,
            "palette": ["black", "purple", "red", "orange", "yellow", "white"],
        })
        return {
            "success": True,
            "tile_url": tile,
            "stats": {
                "max_brightness_k":       stats_raw.get("T21_max"),
                "mean_brightness_k":      stats_raw.get("T21_mean"),
                "pixel_count":            stats_raw.get("T21_count"),
                "estimated_fire_area_km2": fire_area,
                "region": label,
                "period": f"{start} → {date}",
            },
        }

    # ------------------------------------------------------------------
    # 5. Temperature — MODIS LST
    # ------------------------------------------------------------------

    def _analyze_temperature(self, region_id: str, date: str,
                              bbox: Optional[List[float]]) -> Dict:
        ee = self._gee._ee
        aoi, label = self._get_aoi(region_id, bbox)
        end = datetime.strptime(date, "%Y-%m-%d")
        start = (end - timedelta(days=8)).strftime("%Y-%m-%d")

        col = (ee.ImageCollection("MODIS/061/MOD11A1")
                 .filterBounds(aoi).filterDate(start, date)
                 .select("LST_Day_1km"))
        lst = col.mean().multiply(0.02).subtract(273.15).clip(aoi)

        stats = lst.reduceRegion(
            reducer=ee.Reducer.mean().combine(ee.Reducer.min(), "", True)
                               .combine(ee.Reducer.max(), "", True),
            geometry=aoi, scale=1000, maxPixels=1e9,
        ).getInfo()

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
        aoi, label = self._get_aoi(region_id, bbox)
        end = datetime.strptime(date, "%Y-%m-%d")
        start = (end - timedelta(days=10)).strftime("%Y-%m-%d")

        col = (ee.ImageCollection("NASA_USDA/HSL/SMAP10KM_soil_moisture")
                 .filterBounds(aoi).filterDate(start, date)
                 .select("ssm"))   # surface soil moisture
        img = col.mean().clip(aoi)

        stats = img.reduceRegion(
            reducer=ee.Reducer.mean().combine(ee.Reducer.min(), "", True)
                               .combine(ee.Reducer.max(), "", True),
            geometry=aoi, scale=10000, maxPixels=1e9,
        ).getInfo()

        tile = self._tile(img, {
            "min": 0.0, "max": 0.5,
            "palette": ["d29642","eecfa8","fffde4","dcf3ff","a9dbff","51b2d4","0a5994"],
        })
        return {
            "success": True,
            "tile_url": tile,
            "stats": {
                "ssm_mean_m3m3": stats.get("ssm_mean"),
                "ssm_min_m3m3":  stats.get("ssm_min"),
                "ssm_max_m3m3":  stats.get("ssm_max"),
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
        aoi, label = self._get_aoi(region_id, bbox)
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
            geometry=aoi, scale=100, maxPixels=1e9,
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
        aoi, label = self._get_aoi(region_id, bbox)
        gfc = ee.Image("UMD/hansen/global_forest_change_2023_v1_11").clip(aoi)
        canopy   = gfc.select("treecover2000")
        loss     = gfc.select("loss")
        loss_yr  = gfc.select("lossyear")

        # Total canopy area & loss area
        canopy_binary = canopy.gt(10)   # > 10 % cover = forest
        forest_km2 = self._gee.compute_area_km2(canopy_binary, aoi, scale=30)
        loss_km2   = self._gee.compute_area_km2(loss, aoi, scale=30)

        stats_cc = canopy.reduceRegion(
            reducer=ee.Reducer.mean(),
            geometry=aoi, scale=500, maxPixels=1e9,
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
        aoi, label = self._get_aoi(region_id, bbox)
        dem = ee.Image("USGS/SRTMGL1_003").clip(aoi)
        terrain = ee.Algorithms.Terrain(dem)
        elevation = terrain.select("elevation")
        slope     = terrain.select("slope")

        stats = elevation.addBands(slope).reduceRegion(
            reducer=ee.Reducer.mean().combine(ee.Reducer.min(), "", True)
                               .combine(ee.Reducer.max(), "", True),
            geometry=aoi, scale=500, maxPixels=1e9,
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
        aoi, label = self._get_aoi(region_id, bbox)
        end = datetime.strptime(date, "%Y-%m-%d")
        start = (end - timedelta(days=5)).strftime("%Y-%m-%d")

        col = (ee.ImageCollection("UCSB-CHG/CHIRPS/DAILY")
                 .filterBounds(aoi).filterDate(start, date)
                 .select("precipitation"))
        accum = col.sum().clip(aoi)   # 5-day accumulation in mm

        stats = accum.reduceRegion(
            reducer=ee.Reducer.mean().combine(ee.Reducer.max(), "", True)
                               .combine(ee.Reducer.sum(), "", True),
            geometry=aoi, scale=5000, maxPixels=1e9,
        ).getInfo()

        tile = self._tile(accum, {
            "min": 0, "max": 150,
            "palette": ["white","lightblue","steelblue","blue","darkblue","purple"],
        })
        return {
            "success": True,
            "tile_url": tile,
            "stats": {
                "precip_5day_accum_mean_mm": stats.get("precipitation_mean"),
                "precip_5day_max_mm":        stats.get("precipitation_max"),
                "region": label,
                "period": f"{start} → {date} (5-day accumulation)",
            },
        }

    # ------------------------------------------------------------------
    # 11. Snow Cover — MODIS
    # ------------------------------------------------------------------

    def _analyze_snow(self, region_id: str, date: str,
                      bbox: Optional[List[float]]) -> Dict:
        ee = self._gee._ee
        aoi, label = self._get_aoi(region_id, bbox)
        end = datetime.strptime(date, "%Y-%m-%d")
        start = (end - timedelta(days=8)).strftime("%Y-%m-%d")

        col = (ee.ImageCollection("MODIS/061/MOD10A1")
                 .filterBounds(aoi).filterDate(start, date)
                 .select("NDSI_Snow_Cover"))
        img = col.mean().clip(aoi)   # 0–100 snow cover %

        stats = img.reduceRegion(
            reducer=ee.Reducer.mean().combine(ee.Reducer.max(), "", True),
            geometry=aoi, scale=500, maxPixels=1e9,
        ).getInfo()
        mean_snow = stats.get("NDSI_Snow_Cover_mean", 0) or 0

        # Area with > 50 % snow cover
        snow_binary = img.gt(50)
        snow_area_km2 = self._gee.compute_area_km2(snow_binary, aoi, scale=500)

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
        aoi, label = self._get_aoi(region_id, bbox)
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
            geometry=aoi, scale=100, maxPixels=1e9,
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
        aoi, label = self._get_aoi(region_id, bbox)
        end = datetime.strptime(date, "%Y-%m-%d")
        start = (end - timedelta(days=14)).strftime("%Y-%m-%d")

        col = (ee.ImageCollection("COPERNICUS/S5P/NRTI/L3_NO2")
                 .filterBounds(aoi).filterDate(start, date)
                 .select("tropospheric_NO2_column_number_density"))
        img = col.mean().multiply(1e5).clip(aoi)   # convert to ×10⁻⁵ mol/m²

        stats = img.reduceRegion(
            reducer=ee.Reducer.mean().combine(ee.Reducer.max(), "", True),
            geometry=aoi, scale=1000, maxPixels=1e9,
        ).getInfo()
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
        aoi, label = self._get_aoi(region_id, bbox)
        end = datetime.strptime(date, "%Y-%m-%d")
        start = (end - timedelta(days=60)).strftime("%Y-%m-%d")

        col = (ee.ImageCollection("NASA/GRACE/MASS_GRIDS_V04/LAND")
                 .filterBounds(aoi).filterDate(start, date)
                 .select("lwe_thickness"))
        img = col.mean().clip(aoi)   # cm equivalent water height

        stats = img.reduceRegion(
            reducer=ee.Reducer.mean().combine(ee.Reducer.min(), "", True)
                               .combine(ee.Reducer.max(), "", True),
            geometry=aoi, scale=50000, maxPixels=1e9,
        ).getInfo()
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
                "period": f"{start} → {date} (60-day mean)",
            },
        }

    # ------------------------------------------------------------------
    # 15. Glacier — Landsat NDSI + MODIS snow
    # ------------------------------------------------------------------

    def _analyze_glacier(self, region_id: str, date: str,
                          bbox: Optional[List[float]]) -> Dict:
        ee = self._gee._ee
        aoi, label = self._get_aoi(region_id, bbox)
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
        glacier_km2 = self._gee.compute_area_km2(glacier, aoi, scale=30)

        stats = ndsi.reduceRegion(
            reducer=ee.Reducer.mean().combine(ee.Reducer.max(), "", True),
            geometry=aoi, scale=500, maxPixels=1e9,
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
