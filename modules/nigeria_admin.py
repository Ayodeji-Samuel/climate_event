"""
modules/nigeria_admin.py
Nigeria administrative hierarchy: country → 37 states (incl. FCT) → 774 LGAs.

The hierarchy comes from static/geo/nigeria/index.json (built by
scripts/build_nigeria_boundaries.py), so listing and resolving regions needs
no Earth Engine call.  Analysis geometries are filtered server-side from the
same FAO GAUL 2025 dataset the static map files were built from, so the
area Earth Engine clips to is exactly the outline drawn on the map.

Region ids:
  "nigeria"            whole country
  "<state>"            e.g. "lagos", "akwa-ibom", "fct"
  "<state>/<lga>"      e.g. "lagos/surulere"  (LGA names repeat across states)
"""

import json
import re
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Dict, List, Optional

INDEX_PATH = Path(__file__).resolve().parent.parent / "static" / "geo" / "nigeria" / "index.json"

GAUL_LEVEL1 = "FAO/GAUL_SIMPLIFIED_500m/2025/level1"
GAUL_LEVEL2 = "FAO/GAUL_SIMPLIFIED_500m/2025/level2"

# Extra spellings people use for states
_STATE_ALIASES = {"abuja": "fct", "fct": "fct", "federal capital territory": "fct",
                  "nassarawa": "nasarawa", "akwa lbom": "akwa-ibom"}


@dataclass(frozen=True)
class Region:
    id: str
    name: str                       # "Surulere"
    label: str                      # "Surulere LGA, Lagos" / "Lagos State" / "Nigeria"
    level: str                      # "country" | "state" | "lga"
    bbox: tuple                     # (west, south, east, north)
    code: Optional[int] = None      # GAUL1_CODE (state) or GAUL2_CODE (LGA)
    state_id: Optional[str] = None  # parent state for LGAs

    def to_dict(self) -> Dict:
        d = asdict(self)
        d["bbox"] = list(self.bbox)
        return d


def _norm(text: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9]+", " ", text.lower()).split())


class NigeriaAdmin:
    def __init__(self, index_path: Path = INDEX_PATH):
        data = json.loads(Path(index_path).read_text(encoding="utf-8"))
        self.country = Region(id="nigeria", name="Nigeria", label="Nigeria",
                              level="country", bbox=tuple(data["country"]["bbox"]))
        self._regions: Dict[str, Region] = {"nigeria": self.country}
        self._states: List[Region] = []
        self._lgas: Dict[str, List[Region]] = {}

        for s in data["states"]:
            state_label = s["name"] if s["id"] == "fct" else f"{s['name']} State"
            state = Region(id=s["id"], name=s["name"], label=state_label, level="state",
                           bbox=tuple(s["bbox"]), code=s["code"])
            self._states.append(state)
            self._regions[state.id] = state
            lgas = [Region(id=l["id"], name=l["name"], label=f"{l['name']} LGA, {s['name']}",
                           level="lga", bbox=tuple(l["bbox"]), code=l["code"], state_id=s["id"])
                    for l in s["lgas"]]
            self._lgas[state.id] = lgas
            for lga in lgas:
                self._regions[lga.id] = lga

        # Normalised name → regions, for free-text resolution (AI agent)
        self._state_names = {_norm(s.name): s for s in self._states}
        for alias, sid in _STATE_ALIASES.items():
            self._state_names[alias] = self._regions[sid]
        self._lga_names: Dict[str, List[Region]] = {}
        for lgas in self._lgas.values():
            for lga in lgas:
                self._lga_names.setdefault(_norm(lga.name), []).append(lga)

    # ------------------------------------------------------------------
    # Lookup
    # ------------------------------------------------------------------

    def get(self, region_id: Optional[str]) -> Optional[Region]:
        if not region_id:
            return None
        return self._regions.get(region_id.strip().lower())

    def states(self) -> List[Region]:
        return list(self._states)

    def lgas(self, state_id: str) -> List[Region]:
        return list(self._lgas.get(state_id, []))

    def resolve(self, text: Optional[str]) -> Optional[Region]:
        """
        Find the region a free-text phrase refers to, e.g. "Surulere, Lagos",
        "flood risk in Kogi", "lagos/ikeja".  Prefers the most specific match:
        an LGA (disambiguated by any state also mentioned) over a state over
        the whole country.  Returns None when nothing matches.
        """
        if not text:
            return None
        direct = self.get(text)
        if direct:
            return direct
        padded = f" {_norm(text)} "

        states = [s for name, s in self._state_names.items() if f" {name} " in padded]
        state_ids = {s.id for s in states}

        lga_hits: List[Region] = []
        for name, regions in self._lga_names.items():
            if f" {name} " not in padded:
                continue
            for lga in regions:
                # "Gombe", "Bauchi", "Ekiti"… are also LGA names; a bare state
                # name means the state unless the user says "LGA".
                if name in self._state_names and " lga " not in padded:
                    continue
                lga_hits.append(lga)

        if lga_hits:
            in_state = [l for l in lga_hits if l.state_id in state_ids]
            candidates = in_state or lga_hits
            # Longest name wins ("Lagos Island" over "Lagos"), then first state alphabetically
            return max(candidates, key=lambda l: len(l.name))
        if states:
            return max(states, key=lambda s: len(s.name))
        if " nigeria " in padded:
            return self.country
        return None

    # ------------------------------------------------------------------
    # Earth Engine geometry
    # ------------------------------------------------------------------

    def geometry(self, ee, region: Region):
        """Server-side ee.Geometry for *region* (lazy — no round-trip)."""
        if region.level == "lga":
            fc = ee.FeatureCollection(GAUL_LEVEL2).filter(ee.Filter.eq("GAUL2_CODE", region.code))
        elif region.level == "state":
            fc = ee.FeatureCollection(GAUL_LEVEL1).filter(ee.Filter.eq("GAUL1_CODE", region.code))
        else:
            fc = ee.FeatureCollection(GAUL_LEVEL1).filter(ee.Filter.eq("ISO3_CODE", "NGA"))
        return fc.geometry()


ADMIN = NigeriaAdmin()
