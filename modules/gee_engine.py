"""
modules/gee_engine.py
Handles Google Earth Engine authentication, connection management,
image collection queries, and map tile URL generation.
Results are cached to avoid re-running identical EE computations.
"""

import json
import logging
import time
import threading
import zlib
from functools import lru_cache
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)


class GEECache:
    """
    Thread-safe TTL cache for EE results.
    Values are compressed with zlib level-6 (lossless) before storage,
    reducing in-process memory by ~60-70 % for typical JSON payloads.
    Raw bytes are returned to callers; callers that store pre-compressed
    bytes (e.g. ClimateLayerAnalyzer) can pass them directly.
    """

    def __init__(self):
        self._store: Dict[str, Dict] = {}   # key → {data: bytes, expires: float}
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Internal (de)compression
    # ------------------------------------------------------------------

    @staticmethod
    def _pack(value: Any) -> bytes:
        """Serialise *value* to JSON and compress.  Pre-compressed bytes pass through."""
        if isinstance(value, bytes):
            return value   # already compressed by caller
        raw = json.dumps(value, separators=(",", ":")).encode("utf-8")
        return zlib.compress(raw, level=6)

    @staticmethod
    def _unpack(data: bytes) -> Any:
        """Decompress zlib bytes and deserialise JSON.  Returns raw bytes if not JSON."""
        try:
            return json.loads(zlib.decompress(data).decode("utf-8"))
        except Exception:
            return data   # caller receives raw bytes

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get(self, key: str) -> Optional[Any]:
        """
        Return the cached value for *key*, or None if missing / expired.
        If the stored value was pre-compressed bytes (from ClimateLayerAnalyzer),
        the raw bytes are returned so the caller can decompress them itself.
        """
        with self._lock:
            entry = self._store.get(key)
            if entry and time.time() < entry["expires"]:
                return entry["data"]   # raw bytes – caller decides how to use
            if entry:
                del self._store[key]
            return None

    def get_obj(self, key: str) -> Optional[Any]:
        """Like get() but always decompresses and returns a Python object."""
        raw = self.get(key)
        if raw is None:
            return None
        return self._unpack(raw)

    def set(self, key: str, value: Any, ttl: int = 300):
        """
        Store *value* under *key* with *ttl* seconds expiry.
        *value* may be a Python object (will be JSON-compressed) or
        pre-compressed bytes (stored as-is).
        """
        data = self._pack(value)
        with self._lock:
            self._store[key] = {"data": data, "expires": time.time() + ttl}

    def invalidate(self, key: str):
        with self._lock:
            self._store.pop(key, None)

    def clear(self):
        with self._lock:
            self._store.clear()

    @property
    def size(self) -> int:
        """Return total compressed bytes currently held in cache."""
        with self._lock:
            return sum(len(e["data"]) for e in self._store.values())


class GEEEngine:
    """
    Thin wrapper around the Earth Engine Python API.
    Handles auth, keeps connection health, exposes helper methods.
    """

    def __init__(self, credentials_path: str, project: str, deadline_ms: int = 0):
        self.credentials_path = credentials_path
        self.project = project
        # Per-request EE timeout.  PythonAnywhere kills any web request that
        # runs past 300 s; a deadline makes a slow computation fail with a
        # clean JSON error instead of a killed worker and a bare 502.
        self.deadline_ms = deadline_ms
        self.cache = GEECache()
        self._connected = False
        self._ee = None          # the `ee` module – lazy import
        self._init_error: Optional[str] = None
        self._connect()

    # ------------------------------------------------------------------
    # Connection
    # ------------------------------------------------------------------

    def _connect(self):
        """Authenticate with GEE using service-account credentials."""
        try:
            import ee
            from google.oauth2 import service_account

            scopes = ["https://www.googleapis.com/auth/earthengine"]
            creds = service_account.Credentials.from_service_account_file(
                self.credentials_path, scopes=scopes
            )
            ee.Initialize(credentials=creds, project=self.project)
            if self.deadline_ms:
                ee.data.setDeadline(self.deadline_ms)
            self._ee = ee
            self._connected = True
            self._init_error = None
            logger.info("GEE authenticated successfully (project=%s)", self.project)
        except Exception as exc:
            self._connected = False
            self._init_error = str(exc)
            logger.error("GEE authentication failed: %s", exc)

    def reconnect(self):
        self._connect()

    @property
    def is_connected(self) -> bool:
        return self._connected

    @property
    def status(self) -> Dict:
        return {
            "connected": self._connected,
            "project": self.project,
            "error": self._init_error,
        }

    # ------------------------------------------------------------------
    # Sentinel-1 helpers
    # ------------------------------------------------------------------

    def get_s1_collection(self, aoi, start_date: str, end_date: str,
                          polarization: str = "VV",
                          pass_direction: str = "DESCENDING"):
        """Return a filtered Sentinel-1 GRD ImageCollection.

        pass_direction can be "DESCENDING", "ASCENDING", or "BOTH".
        "BOTH" merges imagery from both orbits for maximum coverage.
        """
        ee = self._ee
        col = (
            ee.ImageCollection("COPERNICUS/S1_GRD")
            .filter(ee.Filter.eq("instrumentMode", "IW"))
            .filter(ee.Filter.listContains("transmitterReceiverPolarisation", polarization))
            .filterBounds(aoi)
            .filterDate(start_date, end_date)
            .select(polarization)
        )
        if pass_direction != "BOTH":
            col = col.filter(ee.Filter.eq("orbitProperties_pass", pass_direction))
        return col

    def get_s2_collection(self, aoi, start_date: str, end_date: str,
                          cloud_pct: int = 20):
        """Return a cloud-filtered Sentinel-2 SR ImageCollection."""
        ee = self._ee
        return (
            ee.ImageCollection("COPERNICUS/S2_SR_HARMONIZED")
            .filterBounds(aoi)
            .filterDate(start_date, end_date)
            .filter(ee.Filter.lt("CLOUDY_PIXEL_PERCENTAGE", cloud_pct))
        )

    # ------------------------------------------------------------------
    # Region helpers
    # ------------------------------------------------------------------

    def bbox_geometry(self, west: float, south: float,
                      east: float, north: float):
        """Create a rectangle geometry from bounding box coords."""
        ee = self._ee
        return ee.Geometry.BBox(west, south, east, north)

    # ------------------------------------------------------------------
    # Map tile helpers
    # ------------------------------------------------------------------

    def image_to_tile_url(self, image, vis_params: Dict) -> str:
        """Return a short-lived tile URL for the given EE image."""
        map_id = image.getMapId(vis_params)
        return map_id["tile_fetcher"].url_format

    # ------------------------------------------------------------------
    # JRC / permanent water
    # ------------------------------------------------------------------

    def get_permanent_water_mask(self, seasonality_threshold: int = 10):
        """Pixels that are water for > threshold months/yr → permanent."""
        ee = self._ee
        jrc = ee.Image("JRC/GSW1_4/GlobalSurfaceWater").select("seasonality")
        return jrc.gte(seasonality_threshold)

    # ------------------------------------------------------------------
    # DEM / terrain
    # ------------------------------------------------------------------

    def get_slope(self):
        """Return slope image in degrees from HydroSHEDS DEM."""
        ee = self._ee
        dem = ee.Image("WWF/HydroSHEDS/03VFDEM")
        terrain = ee.Algorithms.Terrain(dem)
        return terrain.select("slope")

    # ------------------------------------------------------------------
    # Area computation
    # ------------------------------------------------------------------

    def compute_area_km2(self, binary_image, geometry, scale: int = 100) -> float:
        """Compute the area (km²) of pixels equal to 1 in *binary_image*."""
        ee = self._ee
        try:
            area_m2 = (
                binary_image
                .multiply(ee.Image.pixelArea())
                .reduceRegion(
                    reducer=ee.Reducer.sum(),
                    geometry=geometry,
                    scale=scale,
                    maxPixels=1e11,
                    bestEffort=True,
                )
                .getInfo()
            )
            # The key name depends on the band name
            val = list(area_m2.values())[0] if area_m2 else 0
            return round((val or 0) / 1e6, 2)   # m² → km²
        except Exception as exc:
            logger.warning("area computation failed: %s", exc)
            return 0.0

    def compute_region_area_km2(self, geometry) -> float:
        """Total area of a geometry in km²."""
        try:
            # maxError=1 m forced exact geodesic maths on every vertex of a
            # country-sized polygon; 100 m is still < 0.01 % error.
            area = geometry.area(maxError=100).getInfo()
            return round(area / 1e6, 2)
        except Exception as exc:
            logger.warning("region area failed: %s", exc)
            return 0.0
