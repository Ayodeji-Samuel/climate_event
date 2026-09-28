/**
 * nigeria_admin.js — Nigeria State → LGA pickers and map boundary overlays.
 * Shared by the Flood Monitor (map_viewer.js) and Climate Monitor (climate.js).
 * Depends on: Leaflet (global L), desktop.js esc().
 *
 * Boundaries are static GeoJSON built from FAO GAUL 2025 by
 * scripts/build_nigeria_boundaries.py and served from /static/geo/nigeria/:
 *   index.json            states + LGAs (ids, names, bounding boxes)
 *   country.geojson       national outline (also used for the outside mask)
 *   states.geojson        36 states + FCT
 *   lgas/<state>.geojson  LGAs of one state, loaded on demand
 * The backend clips Earth Engine analyses to the same GAUL polygons.
 *
 * Region ids match the API: "nigeria" | "<state>" | "<state>/<lga>".
 */

'use strict';

const NigeriaAdmin = (() => {
  const BASE = '/static/geo/nigeria';
  const _cache = {};     // url → Promise<json>
  let _regions = null;   // id → { id, name, label, level, bbox, stateId }

  function _json(url) {
    if (!_cache[url]) {
      _cache[url] = fetch(url).then(r => {
        if (!r.ok) throw new Error(`${url}: HTTP ${r.status}`);
        return r.json();
      });
      _cache[url].catch(() => delete _cache[url]);   // allow a retry after a network error
    }
    return _cache[url];
  }

  async function index() {
    const idx = await _json(`${BASE}/index.json`);
    if (!_regions) {
      _regions = {
        nigeria: { id: 'nigeria', name: 'Nigeria', label: 'Nigeria', level: 'country', bbox: idx.country.bbox },
      };
      idx.states.forEach(s => {
        _regions[s.id] = {
          id: s.id, name: s.name, level: 'state', bbox: s.bbox,
          label: s.id === 'fct' ? s.name : `${s.name} State`,
        };
        s.lgas.forEach(l => {
          _regions[l.id] = {
            id: l.id, name: l.name, level: 'lga', bbox: l.bbox, stateId: s.id,
            label: `${l.name} LGA, ${s.name}`,
          };
        });
      });
    }
    return idx;
  }

  /** Region metadata (null until index() has resolved, or for unknown ids). */
  function region(id) {
    return (_regions && _regions[id]) || null;
  }

  /**
   * Wire a State <select> + LGA <select> pair.  onChange(regionId) fires on
   * every user change.  Resolves to { regionId(), set(regionId) }; set() is
   * for programmatic selection (map clicks, region chips) and does not fire
   * onChange.
   */
  async function bindPickers(stateSel, lgaSel, onChange) {
    const idx = await index();
    stateSel.innerHTML =
      '<option value="nigeria">All Nigeria</option>' +
      idx.states.map(s => `<option value="${s.id}">${esc(s.name)}</option>`).join('');

    function fillLgas(stateId) {
      const st = idx.states.find(s => s.id === stateId);
      lgaSel.disabled = !st;
      lgaSel.innerHTML = st
        ? `<option value="">All ${st.lgas.length} LGAs</option>` +
          st.lgas.map(l => `<option value="${l.id}">${esc(l.name)}</option>`).join('')
        : '<option value="">All LGAs</option>';
    }
    const current = () => lgaSel.value || stateSel.value || 'nigeria';

    stateSel.addEventListener('change', () => { fillLgas(stateSel.value); onChange(current()); });
    lgaSel.addEventListener('change', () => onChange(current()));
    fillLgas(null);

    return {
      regionId: current,
      set(regionId) {
        const r = region(regionId) || region('nigeria');
        const stateId = r.level === 'country' ? null : (r.stateId || r.id);
        stateSel.value = stateId || 'nigeria';
        fillLgas(stateId);
        lgaSel.value = r.level === 'lga' ? r.id : '';
      },
    };
  }

  /** World polygon with Nigeria cut out, to dim everything outside the country. */
  function inverseMask(countryGeoJSON) {
    const worldRing = [[-180, -90], [180, -90], [180, 90], [-180, 90], [-180, -90]];
    const geom = countryGeoJSON.features[0].geometry;
    const holes = geom.type === 'Polygon'
      ? [geom.coordinates[0]]
      : geom.coordinates.map(part => part[0]);
    return {
      type: 'Feature', properties: {},
      geometry: { type: 'Polygon', coordinates: [worldRing, ...holes] },
    };
  }

  return {
    index,
    region,
    bindPickers,
    inverseMask,
    country: () => _json(`${BASE}/country.geojson`),
    states:  () => _json(`${BASE}/states.geojson`),
    lgas:    stateId => _json(`${BASE}/lgas/${stateId}.geojson`),
  };
})();


/**
 * Dark basemap + place-name labels for a Leaflet map.
 * (CARTO's dark_all tiles, used previously, now return an
 * "API KEY REQUIRED" watermark for every request.)  Labels sit in their own
 * pane above the Earth Engine tiles and boundaries so towns stay readable.
 */
function addDarkBasemap(map) {
  const attribution = 'Tiles &copy; Esri &mdash; Esri, HERE, Garmin, &copy; OpenStreetMap contributors' +
                      ' | Boundaries: FAO GAUL 2025';
  L.tileLayer(
    'https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Dark_Gray_Base/MapServer/tile/{z}/{y}/{x}',
    { attribution, maxNativeZoom: 16, maxZoom: 19 }
  ).addTo(map);

  map.createPane('labels');
  map.getPane('labels').style.zIndex = 450;          // above overlayPane (400), below markers (600)
  map.getPane('labels').style.pointerEvents = 'none';
  L.tileLayer(
    'https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Dark_Gray_Reference/MapServer/tile/{z}/{y}/{x}',
    { pane: 'labels', maxNativeZoom: 16, maxZoom: 19 }
  ).addTo(map);
}


/**
 * Nigeria boundary overlay for one Leaflet map: outside-country mask,
 * national outline, state lines + labels, and the LGAs of the selected state.
 * Hovering shows names; clicking a state/LGA calls onSelect(regionId).
 * All vector layers live in the overlayPane, above Earth Engine tile layers.
 */
class NigeriaBoundaryLayer {
  constructor(map, onSelect) {
    this.map = map;
    this.onSelect = onSelect || (() => {});
    this._base = null;          // { mask, states, labels, country }
    this._lgaLayer = null;
    this._lgaStateId = null;
    this._selLabel = null;
    this._regionId = 'nigeria';
    this._seq = 0;              // drops stale async results on rapid re-selection
  }

  /** Select a region: draw its boundaries and (optionally) zoom to it. */
  async show(regionId, { fit = true } = {}) {
    const seq = ++this._seq;
    await NigeriaAdmin.index();
    const region = NigeriaAdmin.region(regionId) || NigeriaAdmin.region('nigeria');
    this._regionId = region.id;
    if (fit) this.fit(region.id);   // instant feedback from the index bbox

    const stateId = region.level === 'country' ? null : (region.stateId || region.id);
    try {
      const [country, states, lgas] = await Promise.all([
        NigeriaAdmin.country(),
        NigeriaAdmin.states(),
        stateId ? NigeriaAdmin.lgas(stateId) : null,
      ]);
      if (seq !== this._seq) return;
      this._ensureBase(country, states);
      this._showLgas(stateId, lgas);
      this._restyle();
    } catch (err) {
      console.warn('[NigeriaBoundaryLayer] boundary load failed:', err);
    }
  }

  fit(regionId) {
    const r = NigeriaAdmin.region(regionId);
    if (!r) return;
    const [w, s, e, n] = r.bbox;
    this.map.fitBounds([[s, w], [n, e]], {
      padding: [24, 24],
      maxZoom: r.level === 'lga' ? 12 : r.level === 'state' ? 9 : 7,
    });
  }

  _ensureBase(country, states) {
    if (this._base) return;

    const mask = L.geoJSON(NigeriaAdmin.inverseMask(country), {
      style: { fillColor: '#000', fillOpacity: 0.6, fillRule: 'evenodd', stroke: false },
      interactive: false,
    }).addTo(this.map);

    const statesLayer = L.geoJSON(states, {
      style: () => this._stateStyle(false),
      onEachFeature: (feat, layer) => {
        layer.bindTooltip(esc(feat.properties.name), { sticky: true, className: 'boundary-tip' });
        layer.on('click', () => this.onSelect(feat.properties.id));
      },
    }).addTo(this.map);

    const labels = L.layerGroup(statesLayer.getLayers().map(layer =>
      L.marker(this._labelPoint(layer), {
        icon: L.divIcon({
          className: 'state-label',
          html: `<span>${esc(layer.feature.properties.name)}</span>`,
          iconSize: [1, 1], iconAnchor: [0, 0],
        }),
        interactive: false, keyboard: false,
      })
    )).addTo(this.map);

    const countryLine = L.geoJSON(country, {
      style: { color: '#00e5ff', weight: 2.5, opacity: 0.95, fill: false },
      interactive: false,
    }).addTo(this.map);

    this._base = { mask, states: statesLayer, labels, country: countryLine };
  }

  _showLgas(stateId, lgas) {
    if (stateId === this._lgaStateId) return;
    if (this._lgaLayer) { this.map.removeLayer(this._lgaLayer); this._lgaLayer = null; }
    this._lgaStateId = stateId;
    if (!stateId || !lgas) return;

    this._lgaLayer = L.geoJSON(lgas, {
      style: () => this._lgaStyle(false),
      onEachFeature: (feat, layer) => {
        layer.bindTooltip(`${esc(feat.properties.name)} LGA`, { sticky: true, className: 'boundary-tip' });
        layer.on('click', e => {
          L.DomEvent.stopPropagation(e);
          this.onSelect(feat.properties.id);
        });
      },
    }).addTo(this.map);
    this._base.country.bringToFront();
  }

  /** Highlight the selected state / LGA and label the selected LGA. */
  _restyle() {
    const region = NigeriaAdmin.region(this._regionId);
    const stateId = region.level === 'country' ? null : (region.stateId || region.id);

    this._base.states.eachLayer(l =>
      l.setStyle(this._stateStyle(l.feature.properties.id === stateId)));
    this._base.labels.eachLayer(m => {
      const el = m.getElement();
      if (el) el.classList.toggle('dim', !!stateId);
    });

    if (this._selLabel) { this.map.removeLayer(this._selLabel); this._selLabel = null; }
    if (!this._lgaLayer) return;
    this._lgaLayer.eachLayer(l => {
      const selected = l.feature.properties.id === this._regionId;
      l.setStyle(this._lgaStyle(selected));
      if (selected) {
        l.bringToFront();
        this._selLabel = L.marker(this._labelPoint(l), {
          icon: L.divIcon({
            className: 'state-label lga-label',
            html: `<span>${esc(l.feature.properties.name)}</span>`,
            iconSize: [1, 1], iconAnchor: [0, 0],
          }),
          interactive: false, keyboard: false,
        }).addTo(this.map);
      }
    });
  }

  _stateStyle(selected) {
    return selected
      ? { color: '#00e5ff', weight: 2.2, opacity: 1, dashArray: null, fill: true, fillOpacity: 0 }
      : { color: '#26c6da', weight: 0.9, opacity: 0.55, dashArray: '5 3', fill: true, fillOpacity: 0 };
  }

  _lgaStyle(selected) {
    return selected
      ? { color: '#ffd166', weight: 2.4, opacity: 1, fill: true, fillColor: '#ffd166', fillOpacity: 0.08 }
      : { color: '#ffbe3d', weight: 0.8, opacity: 0.7, fill: true, fillOpacity: 0 };
  }

  _labelPoint(layer) {
    try { return layer.getCenter(); } catch (_) { return layer.getBounds().getCenter(); }
  }
}
