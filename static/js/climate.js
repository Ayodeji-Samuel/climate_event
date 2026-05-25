/**
 * climate.js — ANSASphere Climate & Environment Monitor
 * Handles the 15-layer environmental analysis window.
 * Depends on: Leaflet (global L), desktop.js toast/helpers.
 *
 * Boundary system:
 *   - geoBoundaries API (free, CORS-enabled) provides ADM0 (country) +
 *     ADM1 (states/regions) GeoJSON for each country.
 *   - An inverse-mask polygon (world minus country) is painted over the
 *     basemap to hide everything outside the selected country.
 *   - State boundary lines are drawn inside the country.
 *   - Country outline is drawn with a bright accent stroke.
 *   - GEE climate tiles (.clip(aoi)) naturally clip to the bounding box;
 *     the visual mask hides any data that bleeds past the exact border.
 */

'use strict';

/* ══════════════════════════════════════════════════════════════
   LAYER METADATA  (mirrors LAYER_CATALOGUE in climate_layers.py)
══════════════════════════════════════════════════════════════ */
const CLIMATE_LAYERS = [
  { id: 'vegetation',       label: 'Vegetation (NDVI)',        icon: '🌿', category: 'land',       unit: 'NDVI' },
  { id: 'land_cover',       label: 'Land Cover',               icon: '🗺️', category: 'land',       unit: 'Class' },
  { id: 'heatwaves',        label: 'Heatwaves',                icon: '🌡️', category: 'climate',    unit: '°C anomaly' },
  { id: 'fires',            label: 'Active Fires',             icon: '🔥', category: 'hazard',     unit: 'MW (FRP)' },
  { id: 'temperature',      label: 'Land Surface Temp.',       icon: '🌡️', category: 'climate',    unit: '°C' },
  { id: 'soil_moisture',    label: 'Soil Moisture',            icon: '💧', category: 'land',       unit: 'm³/m³' },
  { id: 'deformation',      label: 'Ground Deformation',       icon: '📐', category: 'hazard',     unit: 'dB diff' },
  { id: 'forest_structure', label: 'Forest Structure',         icon: '🌲', category: 'land',       unit: '% cover' },
  { id: 'elevation',        label: 'Elevation & Terrain',      icon: '⛰️', category: 'terrain',    unit: 'm' },
  { id: 'rainfall',         label: 'Rainfall (CHIRPS)',        icon: '🌧️', category: 'climate',    unit: 'mm (5-day)' },
  { id: 'snow',             label: 'Snow Cover',               icon: '❄️', category: 'climate',    unit: '% area' },
  { id: 'crop_stress',      label: 'Crop Stress',              icon: '🌾', category: 'land',       unit: 'Index' },
  { id: 'pollution',        label: 'Air Pollution (NO₂)',      icon: '💨', category: 'atmosphere', unit: '×10⁻⁵ mol/m²' },
  { id: 'sea_level',        label: 'Sea Level / Water',        icon: '🌊', category: 'ocean',      unit: 'cm anomaly' },
  { id: 'glacier',          label: 'Glacier & Ice',            icon: '🧊', category: 'cryosphere', unit: 'NDSI / km²' },
];

const CATEGORY_ORDER = ['hazard', 'climate', 'land', 'terrain', 'atmosphere', 'ocean', 'cryosphere'];
const CATEGORY_LABELS = {
  hazard: '⚠ Hazards', climate: '🌤 Climate', land: '🌱 Land',
  terrain: '⛰ Terrain', atmosphere: '💨 Atmosphere',
  ocean: '🌊 Ocean', cryosphere: '🧊 Cryosphere',
};

/* Legend colour stops per layer */
const LEGENDS = {
  vegetation:       { stops: ['#d73027','#f46d43','#fdae61','#d9ef8b','#66bd63','#1a9850'], min: '-0.2', max: '1.0' },
  land_cover:       { stops: ['#006400','#FFBB22','#F096FF','#FA0000','#0064C8','#00CF75'], min: 'Trees', max: 'Crops' },
  heatwaves:        { stops: ['#313695','#74add1','#e0f3f8','#ffffbf','#fdae61','#d73027','#a50026'], min: '-5°C', max: '+10°C' },
  fires:            { stops: ['black','purple','red','orange','yellow','white'], min: 'Cool', max: 'Fire' },
  temperature:      { stops: ['#040274','#0602ff','#30c8e2','#3ae237','#ffd611','#ff6e08','#ff0000'], min: '-10°C', max: '50°C' },
  soil_moisture:    { stops: ['#d29642','#eecfa8','#dcf3ff','#51b2d4','#0a5994'], min: 'Dry', max: 'Wet' },
  deformation:      { stops: ['white','lightyellow','yellow','orange','red','darkred'], min: '0 dB', max: '+10 dB' },
  forest_structure: { stops: ['#fffde4','#cde6c0','#78c679','#31a354','#006837'], min: '0%', max: '100%' },
  elevation:        { stops: ['#006633','#E5FFCC','#662A00','#D8D8D8','#F5F5F5'], min: '0 m', max: '3000 m' },
  rainfall:         { stops: ['white','lightblue','steelblue','blue','darkblue','purple'], min: '0 mm', max: '150 mm' },
  snow:             { stops: ['#1A1A1A','#4169E1','#87CEEB','#E0E0E0','white'], min: '0%', max: '100%' },
  crop_stress:      { stops: ['#8B4513','#F4A460','#FFFF00','#9ACD32','#228B22'], min: 'Stressed', max: 'Healthy' },
  pollution:        { stops: ['black','blue','purple','red','orange','yellow','white'], min: '0', max: '15' },
  sea_level:        { stops: ['#8B0000','#FF8C00','#FFD700','#ADFF2F','#1E90FF','#00008B'], min: '-20 cm', max: '+20 cm' },
  glacier:          { stops: ['#8B4513','#F5DEB3','#87CEEB','#B0C4DE','white'], min: 'Bare', max: 'Ice/Snow' },
};

/* ══════════════════════════════════════════════════════════════
   COUNTRY CONFIG  (ISO-3 codes + map centre + zoom)
══════════════════════════════════════════════════════════════ */
const ISO3_MAP = {
  nigeria:    'NGA', ghana:      'GHA', kenya:      'KEN',
  ethiopia:   'ETH', mozambique: 'MOZ', tanzania:   'TZA',
  bangladesh: 'BGD', india:      'IND', pakistan:   'PAK',
  myanmar:    'MMR', thailand:   'THA', indonesia:  'IDN',
};

const REGION_VIEW = {
  nigeria:    { center: [9.0,   8.0],   zoom: 6 },
  ghana:      { center: [7.9,  -1.0],   zoom: 7 },
  kenya:      { center: [0.0,  38.0],   zoom: 6 },
  ethiopia:   { center: [9.0,  40.0],   zoom: 6 },
  mozambique: { center: [-18.0, 35.0],  zoom: 5 },
  tanzania:   { center: [-6.4,  34.9],  zoom: 6 },
  bangladesh: { center: [23.7,  90.4],  zoom: 7 },
  india:      { center: [20.5,  79.0],  zoom: 5 },
  pakistan:   { center: [30.0,  69.0],  zoom: 6 },
  myanmar:    { center: [19.0,  96.5],  zoom: 6 },
  thailand:   { center: [13.0, 101.5],  zoom: 6 },
  indonesia:  { center: [-2.0, 118.0],  zoom: 5 },
};

/* ══════════════════════════════════════════════════════════════
   STATE
══════════════════════════════════════════════════════════════ */
const _cs = {
  map:           null,
  tileLayer:     null,
  maskLayer:     null,   // inverse-mask polygon (world minus country)
  statesLayer:   null,   // ADM1 state/region outlines
  countryLayer:  null,   // ADM0 country outline
  selectedLayer: null,
  currentRegion: null,   // which country boundaries are currently loaded
  isLoading:     false,
  bdLoading:     false,  // true while fetching GeoJSON
};

// In-memory GeoJSON cache: { regionId: { adm0, adm1 } }
const _geoCache = {};

/* ══════════════════════════════════════════════════════════════
   INIT  (called once after desktop is ready)
══════════════════════════════════════════════════════════════ */
function initClimateMonitor() {
  _buildLayerList();
  _initClimateMap();
  _bindControls();

  // Set today's date in date picker
  const today = new Date().toISOString().slice(0, 10);
  const dp = document.getElementById('climate-date-input');
  if (dp) dp.value = today;
}

/* ══════════════════════════════════════════════════════════════
   LAYER LIST  (left sidebar)
══════════════════════════════════════════════════════════════ */
function _buildLayerList() {
  const container = document.getElementById('climate-layer-list');
  if (!container) return;

  const grouped = {};
  CLIMATE_LAYERS.forEach(l => {
    if (!grouped[l.category]) grouped[l.category] = [];
    grouped[l.category].push(l);
  });

  let html = '';
  CATEGORY_ORDER.forEach(cat => {
    if (!grouped[cat]) return;
    html += `<div class="climate-cat-label">${CATEGORY_LABELS[cat]}</div>`;
    grouped[cat].forEach(l => {
      html += `
        <div class="climate-layer-item" data-layer="${l.id}" title="${l.unit}">
          <span class="cli-icon">${l.icon}</span>
          <span class="cli-label">${l.label}</span>
          <span class="cli-unit">${l.unit}</span>
        </div>`;
    });
  });
  container.innerHTML = html;

  container.addEventListener('click', e => {
    const item = e.target.closest('.climate-layer-item');
    if (!item) return;
    container.querySelectorAll('.climate-layer-item').forEach(el => el.classList.remove('active'));
    item.classList.add('active');
    _cs.selectedLayer = item.dataset.layer;
    _updateActiveLayerInfo(_cs.selectedLayer);
  });
}

function _updateActiveLayerInfo(layerId) {
  const meta = CLIMATE_LAYERS.find(l => l.id === layerId);
  if (!meta) return;
  const lbl = document.getElementById('climate-active-layer-label');
  const desc = document.getElementById('climate-layer-desc');
  if (lbl)  lbl.textContent = `${meta.icon}  ${meta.label}`;
  if (desc) desc.textContent = `Unit: ${meta.unit}  ·  Click Analyze to run GEE query.`;
  _showLegend(layerId);
}

/* ══════════════════════════════════════════════════════════════
   LEAFLET MAP
══════════════════════════════════════════════════════════════ */
function _initClimateMap() {
  if (_cs.map) return;
  const el = document.getElementById('climate-map');
  if (!el) return;

  _cs.map = L.map('climate-map', {
    center: [9, 8],
    zoom: 5,
    zoomControl: true,
    attributionControl: false,
  });

  L.tileLayer('https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png', {
    subdomains: 'abcd', maxZoom: 19,
  }).addTo(_cs.map);
}

/* ══════════════════════════════════════════════════════════════
   CONTROLS
══════════════════════════════════════════════════════════════ */
function _bindControls() {
  const btn = document.getElementById('run-climate-btn');
  if (btn) btn.addEventListener('click', runClimateAnalysis);

  const regionSel = document.getElementById('climate-region-select');
  if (regionSel) {
    regionSel.addEventListener('change', () => {
      _loadCountryBoundary(regionSel.value);
    });
    // Load default country boundary after a short delay to allow map to render
    setTimeout(() => _loadCountryBoundary(regionSel.value), 200);
  }
}

/* ══════════════════════════════════════════════════════════════
   COUNTRY BOUNDARY SYSTEM
   Uses geoBoundaries API (free, CORS-enabled):
     ADM0 = country outline
     ADM1 = states / regions / provinces
══════════════════════════════════════════════════════════════ */

/**
 * Fetch and display ADM0 (country outline) + ADM1 (state lines) +
 * inverse mask (everything outside the country is darkened).
 */
async function _loadCountryBoundary(regionId) {
  if (!_cs.map || _cs.bdLoading) return;
  _cs.bdLoading = true;

  // Pan immediately to give instant feedback
  const rv = REGION_VIEW[regionId];
  if (rv) _cs.map.setView(rv.center, rv.zoom);

  // Remove old layers
  _clearBoundaries();

  const iso3 = ISO3_MAP[regionId];
  if (!iso3) { _cs.bdLoading = false; return; }

  try {
    // Use cache if available
    if (!_geoCache[regionId]) {
      const BASE = 'https://www.geoboundaries.org/api/current/gbOpen';
      // Fetch metadata for ADM0 and ADM1 in parallel
      const [m0, m1] = await Promise.all([
        fetch(`${BASE}/${iso3}/ADM0/`).then(r => r.json()),
        fetch(`${BASE}/${iso3}/ADM1/`).then(r => r.json()),
      ]);

      // Prefer simplified GeoJSON for performance
      const url0 = m0.simplifiedGeojsonURL || m0.gjDownloadURL;
      const url1 = m1.simplifiedGeojsonURL || m1.gjDownloadURL;

      const [adm0, adm1] = await Promise.all([
        fetch(url0).then(r => r.json()),
        fetch(url1).then(r => r.json()),
      ]);

      _geoCache[regionId] = { adm0, adm1 };
    }

    _applyBoundaries(regionId, _geoCache[regionId]);

  } catch (err) {
    console.warn('[ClimateMonitor] Boundary load failed for', regionId, err);
    // Non-fatal: map still works, just without boundary overlay
  } finally {
    _cs.bdLoading = false;
  }
}

/** Remove all boundary layers from the map. */
function _clearBoundaries() {
  if (_cs.maskLayer)    { _cs.map.removeLayer(_cs.maskLayer);    _cs.maskLayer    = null; }
  if (_cs.statesLayer)  { _cs.map.removeLayer(_cs.statesLayer);  _cs.statesLayer  = null; }
  if (_cs.countryLayer) { _cs.map.removeLayer(_cs.countryLayer); _cs.countryLayer = null; }
}

/**
 * Add mask + state + country layers to the map.
 * Layer order within Leaflet's overlayPane (SVG) matters:
 *   mask (bottom) → states → country outline (top)
 * Tile layers always sit BELOW the overlayPane, so climate tiles are
 * naturally under the mask (which has a transparent hole over the country).
 */
function _applyBoundaries(regionId, { adm0, adm1 }) {
  if (!_cs.map) return;
  _cs.currentRegion = regionId;

  // 1 ── Inverse mask ─────────────────────────────────────────
  //   A polygon that covers the entire world, with the country
  //   polygon cut out as a hole. fillRule:'evenodd' makes the hole
  //   transparent so the map and climate tile show through there.
  const maskFeature = _buildInverseMask(adm0);
  if (maskFeature) {
    _cs.maskLayer = L.geoJSON(maskFeature, {
      style: {
        fillColor:   '#000000',
        fillOpacity: 0.68,
        fillRule:    'evenodd',
        color:       'transparent',
        weight:      0,
      },
      interactive: false,
    }).addTo(_cs.map);
  }

  // 2 ── State / region internal boundaries (ADM1) ────────────
  _cs.statesLayer = L.geoJSON(adm1, {
    style: {
      color:      '#26c6da',
      weight:     0.9,
      opacity:    0.55,
      fill:       false,
      dashArray:  '5 3',
    },
    interactive: false,
  }).addTo(_cs.map);

  // 3 ── Country outer boundary (ADM0) ─── bright, solid ──────
  _cs.countryLayer = L.geoJSON(adm0, {
    style: {
      color:   '#00e5ff',
      weight:  2.5,
      opacity: 0.95,
      fill:    false,
    },
    interactive: false,
  }).addTo(_cs.map);

  // Fit map to exact country extent
  try {
    const bounds = L.geoJSON(adm0).getBounds();
    if (bounds.isValid()) {
      _cs.map.fitBounds(bounds, { padding: [28, 28], maxZoom: 8 });
    }
  } catch (_) {}
}

/**
 * Build a GeoJSON Feature whose geometry is the entire world minus
 * the country polygon.  Using fillRule:'evenodd' in Leaflet makes
 * the country interior transparent (shows through as a "window").
 *
 * Works for both Polygon and MultiPolygon country shapes.
 */
function _buildInverseMask(adm0GeoJSON) {
  // World bounding ring (GeoJSON = [lon, lat])
  const worldRing = [
    [-180, -90], [180, -90], [180, 90], [-180, 90], [-180, -90],
  ];

  try {
    const features = adm0GeoJSON.features || [adm0GeoJSON];
    const geom = features[0].geometry;

    let holes = [];
    if (geom.type === 'Polygon') {
      // Take only the outer ring (index 0) — ignore any existing holes
      holes = [geom.coordinates[0]];
    } else if (geom.type === 'MultiPolygon') {
      // Take the outer ring of each sub-polygon (ignores interior holes)
      holes = geom.coordinates.map(part => part[0]);
    }

    if (!holes.length) return null;

    return {
      type: 'Feature',
      properties: {},
      geometry: {
        type: 'Polygon',
        // First element = world outer ring; remaining = country holes
        coordinates: [worldRing, ...holes],
      },
    };
  } catch (e) {
    console.warn('[ClimateMonitor] _buildInverseMask error:', e);
    return null;
  }
}

/* ══════════════════════════════════════════════════════════════
   ANALYSIS
══════════════════════════════════════════════════════════════ */
async function runClimateAnalysis() {
  if (_cs.isLoading) return;
  if (!_cs.selectedLayer) {
    _climateToast('Select a layer first', 'warn');
    return;
  }

  const regionId = (document.getElementById('climate-region-select') || {}).value || 'nigeria';
  const date     = (document.getElementById('climate-date-input')    || {}).value || '';

  _setLoading(true, `Analyzing ${_cs.selectedLayer}…`);
  _clearStats();
  _hideAlertBox();

  try {
    const params = new URLSearchParams({ layer_id: _cs.selectedLayer, region_id: regionId });
    if (date) params.set('date', date);

    const res = await fetch(`/api/climate/analyze?${params}`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const json = await res.json();
    const result = json.result || {};

    if (!result.success) {
      _climateToast(result.message || 'Analysis failed', 'error');
      _showStats({ error: result.message });
      return;
    }

    // Add tile layer to map
    if (result.tile_url) {
      _addTileLayer(result.tile_url, regionId);
    }

    _showStats(result.stats || {});
    _showLegend(_cs.selectedLayer);
    _checkAlerts(result);
    _showDatasetInfo(result.meta);

  } catch (err) {
    _climateToast(`Error: ${err.message}`, 'error');
    _showStats({ error: err.message });
  } finally {
    _setLoading(false);
  }
}

/* ══════════════════════════════════════════════════════════════
   MAP HELPERS
══════════════════════════════════════════════════════════════ */
function _addTileLayer(url, regionId) {
  if (!_cs.map) return;

  // Remove previous climate tile
  if (_cs.tileLayer) {
    _cs.map.removeLayer(_cs.tileLayer);
    _cs.tileLayer = null;
  }

  // Add new climate tile (goes to tilePane, always below overlayPane GeoJSON)
  _cs.tileLayer = L.tileLayer(url, { opacity: 0.85, maxZoom: 18 }).addTo(_cs.map);

  // Load/refresh boundary for the region if not already loaded
  if (_cs.currentRegion !== regionId) {
    _loadCountryBoundary(regionId);
  } else {
    // Bring boundary layers above the newly-added tile layer
    // (Within the overlayPane they are already above all tile layers,
    // but re-adding ensures correct SVG stacking order inside the pane)
    _bringBoundariesToFront();
  }
}

/**
 * Ensure mask → states → country are stacked above everything else
 * in the SVG overlayPane after the climate tile is added.
 */
function _bringBoundariesToFront() {
  // Leaflet's GeoJSON layers are already in the overlayPane (above tiles),
  // but within the SVG we re-insert them to maintain correct draw order.
  if (_cs.maskLayer)    _cs.maskLayer.bringToFront();
  if (_cs.statesLayer)  _cs.statesLayer.bringToFront();
  if (_cs.countryLayer) _cs.countryLayer.bringToFront();
}

/* ══════════════════════════════════════════════════════════════
   STATS PANEL
══════════════════════════════════════════════════════════════ */
function _showStats(stats) {
  const grid = document.getElementById('climate-stats-grid');
  if (!grid) return;
  if (!stats || Object.keys(stats).length === 0) {
    grid.innerHTML = '<p class="no-data">No statistics available.</p>';
    return;
  }

  const skipKeys = new Set(['region', 'period', 'year', 'error']);
  const rows = Object.entries(stats)
    .filter(([k]) => !skipKeys.has(k))
    .map(([k, v]) => {
      const label = k.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
      const value = typeof v === 'boolean'
        ? (v ? '<span class="stat-yes">Yes ⚠</span>' : '<span class="stat-no">No</span>')
        : (typeof v === 'number' ? v.toFixed(3) : v);
      return `<div class="cs-row"><span class="cs-key">${label}</span><strong class="cs-val">${value}</strong></div>`;
    });

  // Extra info rows
  if (stats.region) rows.unshift(`<div class="cs-row"><span class="cs-key">Region</span><strong class="cs-val">${stats.region}</strong></div>`);
  if (stats.period) rows.push(`<div class="cs-row cs-period"><span class="cs-key">Period</span><strong class="cs-val">${stats.period}</strong></div>`);
  if (stats.year)   rows.push(`<div class="cs-row cs-period"><span class="cs-key">Year</span><strong class="cs-val">${stats.year}</strong></div>`);

  grid.innerHTML = rows.join('');
}

function _clearStats() {
  const grid = document.getElementById('climate-stats-grid');
  if (grid) grid.innerHTML = '';
  const info = document.getElementById('climate-dataset-info');
  if (info) info.textContent = '';
}

/* ══════════════════════════════════════════════════════════════
   ALERT BOX  (flags anomalies)
══════════════════════════════════════════════════════════════ */
function _checkAlerts(result) {
  const stats = result.stats || {};
  const alerts = [];

  if (stats.heatwave_detected === true)    alerts.push('🌡️ Heatwave detected — anomaly > 3 °C above baseline');
  if (stats.deformation_detected === true) alerts.push('📐 Ground deformation signal detected');
  if (stats.high_pollution === true)       alerts.push('💨 Elevated NO₂ pollution (>5×10⁻⁵ mol/m²)');
  if (stats.water_anomaly_positive === true) alerts.push('🌊 Positive water storage anomaly (potential flood signal)');
  if (stats.crop_water_stress === true)    alerts.push('🌾 Crop water stress detected (NDWI < -0.1)');
  if ((stats.estimated_fire_area_km2 || 0) > 100) alerts.push(`🔥 ${stats.estimated_fire_area_km2?.toFixed(0)} km² active fire area`);

  const box = document.getElementById('climate-alert-box');
  if (!box) return;
  if (alerts.length === 0) {
    box.style.display = 'none';
    box.innerHTML = '';
  } else {
    box.style.display = 'block';
    box.innerHTML = alerts.map(a => `<div class="ca-item">${a}</div>`).join('');
  }
}

function _hideAlertBox() {
  const box = document.getElementById('climate-alert-box');
  if (box) { box.style.display = 'none'; box.innerHTML = ''; }
}

/* ══════════════════════════════════════════════════════════════
   LEGEND
══════════════════════════════════════════════════════════════ */
function _showLegend(layerId) {
  const leg = document.getElementById('climate-legend');
  const title = document.getElementById('climate-legend-title');
  const scale = document.getElementById('climate-legend-scale');
  if (!leg || !scale) return;

  const meta = CLIMATE_LAYERS.find(l => l.id === layerId);
  const lgd  = LEGENDS[layerId];
  if (!lgd) { leg.style.display = 'none'; return; }

  if (title) title.textContent = meta ? `${meta.icon} ${meta.label}` : layerId;
  const gradient = `linear-gradient(to right, ${lgd.stops.join(',')})`;
  scale.innerHTML = `
    <div style="background:${gradient};height:10px;border-radius:4px;margin:4px 0"></div>
    <div style="display:flex;justify-content:space-between;font-size:10px;color:var(--text-muted)">
      <span>${lgd.min}</span><span>${lgd.max}</span>
    </div>`;
  leg.style.display = 'block';
}

/* ══════════════════════════════════════════════════════════════
   DATASET INFO
══════════════════════════════════════════════════════════════ */
function _showDatasetInfo(meta) {
  const el = document.getElementById('climate-dataset-info');
  if (!el || !meta) return;
  el.textContent = meta.description || '';
}

/* ══════════════════════════════════════════════════════════════
   LOADING STATE
══════════════════════════════════════════════════════════════ */
function _setLoading(on, label) {
  _cs.isLoading = on;
  const wrap  = document.getElementById('climate-map-loading');
  const lbl   = document.getElementById('climate-loading-label');
  const btn   = document.getElementById('run-climate-btn');
  if (wrap) wrap.style.display = on ? 'flex' : 'none';
  if (lbl && label) lbl.textContent = label;
  if (btn) btn.disabled = on;
}

/* ══════════════════════════════════════════════════════════════
   TOAST  (falls back to desktop.js showToast if available)
══════════════════════════════════════════════════════════════ */
function _climateToast(msg, type) {
  if (typeof showToast === 'function') {
    showToast(msg, type);
  } else {
    console.warn('[ClimateMonitor]', msg);
  }
}

/* ══════════════════════════════════════════════════════════════
   HOOK into desktop ready
══════════════════════════════════════════════════════════════ */
// Wait for desktop.js to fire, then init
document.addEventListener('DOMContentLoaded', () => {
  // Init map lazily when the window is first opened
  const winEl = document.getElementById('win-climate-monitor');
  if (!winEl) return;

  const observer = new MutationObserver((mutations) => {
    for (const m of mutations) {
      if (m.type === 'attributes' && m.attributeName === 'class') {
        if (!winEl.classList.contains('hidden') && !_cs.map) {
          // Small delay lets the DOM finish showing the window
          setTimeout(initClimateMonitor, 80);
        }
      }
    }
  });
  observer.observe(winEl, { attributes: true });

  // Also init immediately if already open (unlikely at load but safe)
  if (!winEl.classList.contains('hidden')) {
    setTimeout(initClimateMonitor, 80);
  }
});
