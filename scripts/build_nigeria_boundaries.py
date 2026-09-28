"""
scripts/build_nigeria_boundaries.py
One-off generator for the static Nigeria boundary files in static/geo/nigeria/.

Source: FAO/GAUL_SIMPLIFIED_500m/2025 (level1/2) — the same dataset the
backend filters server-side for analysis (modules/nigeria_admin.py), so what
the map draws is exactly what Earth Engine clips to.  GAUL 2025 has the
official 37 states (incl. FCT) and 774 LGAs with unique codes.

Outputs
  index.json            hierarchy used by the UI pickers and modules/nigeria_admin.py
  country.geojson       Nigeria outline (dissolved from the states)
  states.geojson        37 states incl. FCT (GAUL level1)
  lgas/<state>.geojson  LGAs per state (GAUL level2), loaded on demand by the UI

Run from the project root (needs GEE credentials in .env):
  python scripts/build_nigeria_boundaries.py
"""

import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

import ee  # noqa: E402
from google.oauth2 import service_account  # noqa: E402

OUT = ROOT / "static" / "geo" / "nigeria"
DATASET = "FAO/GAUL_SIMPLIFIED_500m/2025/level{}"
PRECISION = 4   # ~11 m — far finer than the 500 m source simplification

# GAUL 2025 spellings → official names (the GAUL value is still used for lookups)
STATE_DISPLAY = {"Akwa Lbom": "Akwa Ibom", "Nassarawa": "Nasarawa"}
STATE_SLUG = {"Federal Capital Territory": "fct"}


def slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def _clean_ring(ring):
    out = []
    for x, y in ring:
        pt = [round(x, PRECISION), round(y, PRECISION)]
        if not out or pt != out[-1]:
            out.append(pt)
    if out and out[0] != out[-1]:
        out.append(out[0])
    return out if len(out) >= 4 else None


def _polygons(geom):
    """Yield polygon coordinate arrays from any GeoJSON geometry, dropping points/lines."""
    t = geom.get("type")
    if t == "Polygon":
        yield geom["coordinates"]
    elif t == "MultiPolygon":
        yield from geom["coordinates"]
    elif t == "GeometryCollection":
        for g in geom.get("geometries", []):
            yield from _polygons(g)


def normalise(geom):
    """Round coordinates and collapse to Polygon / MultiPolygon only."""
    polys = []
    for poly in _polygons(geom):
        rings = [r for r in (_clean_ring(r) for r in poly) if r]
        if rings:
            polys.append(rings)
    if not polys:
        return None
    if len(polys) == 1:
        return {"type": "Polygon", "coordinates": polys[0]}
    return {"type": "MultiPolygon", "coordinates": polys}


def bbox(geom):
    xs, ys = [], []
    for poly in _polygons(geom):
        for ring in poly:
            for x, y in ring:
                xs.append(x)
                ys.append(y)
    return [round(min(xs), 4), round(min(ys), 4), round(max(xs), 4), round(max(ys), 4)]


def nigeria(level):
    return (ee.FeatureCollection(DATASET.format(level))
              .filter(ee.Filter.eq("ISO3_CODE", "NGA")))


def fetch(level, props):
    return nigeria(level).select(props).getInfo()["features"]


def write(path: Path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, separators=(",", ":"), ensure_ascii=False), encoding="utf-8")
    print(f"  {path.relative_to(ROOT)}  {path.stat().st_size / 1024:.0f} KB")


def main():
    creds = service_account.Credentials.from_service_account_file(
        os.environ.get("GEE_CREDENTIALS_PATH", str(ROOT / "ee-my-makinde-2b6858cddb01.json")),
        scopes=["https://www.googleapis.com/auth/earthengine"],
    )
    ee.Initialize(credentials=creds, project=os.environ.get("GEE_PROJECT", "ee-my-makinde"))

    print("Fetching states and LGAs from Earth Engine...")
    adm1 = fetch(1, ["GAUL1_CODE", "GAUL1_NAME"])
    adm2 = fetch(2, ["GAUL1_CODE", "GAUL2_CODE", "GAUL2_NAME"])

    # ── Country (GAUL 2025 has no level0; dissolve the states) ─────────
    country_geom = normalise(nigeria(1).union(100).first().geometry().getInfo())
    write(OUT / "country.geojson", {"type": "FeatureCollection", "features": [{
        "type": "Feature", "properties": {"id": "nigeria", "name": "Nigeria"},
        "geometry": country_geom}]})

    # ── States ────────────────────────────────────────────────────────
    states = {}
    state_features = []
    for f in adm1:
        gaul = f["properties"]["GAUL1_NAME"]
        name = STATE_DISPLAY.get(gaul, gaul)
        sid = STATE_SLUG.get(gaul, slugify(name))
        geom = normalise(f["geometry"])
        states[f["properties"]["GAUL1_CODE"]] = {
            "id": sid, "name": name, "code": f["properties"]["GAUL1_CODE"],
            "bbox": bbox(geom), "lgas": [],
        }
        state_features.append({"type": "Feature",
                               "properties": {"id": sid, "name": name}, "geometry": geom})
    write(OUT / "states.geojson", {"type": "FeatureCollection", "features": state_features})

    state_features.sort(key=lambda f: f["properties"]["name"])

    # ── LGAs, grouped per state.  LGA names repeat across states
    #    (Surulere, Obi, Bassa…), so ids are "<state>/<lga>" ─────────────
    lga_features = {code: [] for code in states}
    for f in sorted(adm2, key=lambda f: f["properties"]["GAUL2_NAME"]):
        p = f["properties"]
        state = states[p["GAUL1_CODE"]]
        lid = f"{state['id']}/{slugify(p['GAUL2_NAME'])}"
        if any(l["id"] == lid for l in state["lgas"]):
            raise SystemExit(f"Duplicate LGA id {lid} — adjust slug rules")
        geom = normalise(f["geometry"])
        if geom is None:
            print(f"  ! skipping {lid}: no polygon geometry")
            continue
        state["lgas"].append({"id": lid, "name": p["GAUL2_NAME"],
                              "code": p["GAUL2_CODE"], "bbox": bbox(geom)})
        lga_features[p["GAUL1_CODE"]].append({
            "type": "Feature",
            "properties": {"id": lid, "name": p["GAUL2_NAME"], "state": state["name"]},
            "geometry": geom,
        })
    for code, feats in lga_features.items():
        write(OUT / "lgas" / f"{states[code]['id']}.geojson",
              {"type": "FeatureCollection", "features": feats})

    # ── Index ─────────────────────────────────────────────────────────
    index = {
        "source": "FAO/GAUL_SIMPLIFIED_500m/2025",
        "country": {"id": "nigeria", "name": "Nigeria", "bbox": bbox(country_geom)},
        "states": sorted(states.values(), key=lambda s: s["name"]),
    }
    write(OUT / "index.json", index)
    n_lgas = sum(len(s["lgas"]) for s in index["states"])
    print(f"Done: {len(index['states'])} states, {n_lgas} LGAs")


if __name__ == "__main__":
    main()
