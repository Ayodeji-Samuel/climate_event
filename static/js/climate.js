/**
 * climate.js — ANSASphere Climate & Environment Monitor
 * Handles the 15-layer environmental analysis window.
 * Depends on: Leaflet (global L), desktop.js toast/apiJSON helpers,
 *             nigeria_admin.js (State/LGA pickers + boundary overlay).
 *
 * Region = Nigeria, one of its 37 states or one of its 774 LGAs.  The
 * boundary overlay dims everything outside Nigeria, outlines the states
 * and, once a state is selected, its LGAs; clicking the map selects a
 * state/LGA.  GEE tiles are clipped server-side to the same GAUL polygon.
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
   STATE
══════════════════════════════════════════════════════════════ */
const _cs = {
  map:           null,
  tileLayer:     null,
  boundaries:    null,   // NigeriaBoundaryLayer
  pickers:       null,   // { regionId(), set(id) }
  selectedLayer: null,
  currentRegion: null,   // region of the last successful analysis
  isLoading:     false,
};

/* ══════════════════════════════════════════════════════════════
   INIT  (called once after desktop is ready)
══════════════════════════════════════════════════════════════ */
function initClimateMonitor() {
  if (_cs.map) {
    // Window re-opened: Leaflet must recalculate its container size
    _cs.map.invalidateSize();
    return;
  }
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
    _updateActiveLayerInfo(_cs.selectedLayer);    // Reset trend state when a different layer is selected
    const canvas = document.getElementById('climate-trend-canvas');
    if (canvas) canvas.dataset.loadedKey = '';
    const trendBtn = document.getElementById('climate-trend-btn');
    if (trendBtn) trendBtn.style.display = 'none';
    toggleTrendPanel(true);  });
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

  addDarkBasemap(_cs.map);

  _cs.boundaries = new NigeriaBoundaryLayer(_cs.map, regionId => {
    _cs.pickers?.set(regionId);
    _onRegionChange(regionId);
  });
  _cs.boundaries.show('nigeria');
}

function _currentRegionId() {
  return _cs.pickers ? _cs.pickers.regionId() : 'nigeria';
}

/** New State/LGA selected: redraw boundaries and drop the old result. */
function _onRegionChange(regionId) {
  _cs.boundaries?.show(regionId);
  _cs.currentRegion = null;
  if (_cs.tileLayer) { _cs.map.removeLayer(_cs.tileLayer); _cs.tileLayer = null; }
  _clearStats();
  _hideAlertBox();
}

/* ══════════════════════════════════════════════════════════════
   CONTROLS  (bound once — guard prevents duplicate listeners)
══════════════════════════════════════════════════════════════ */
let _controlsBound = false;
function _bindControls() {
  if (_controlsBound) return;
  _controlsBound = true;
  const btn = document.getElementById('run-climate-btn');
  if (btn) btn.addEventListener('click', runClimateAnalysis);

  NigeriaAdmin.bindPickers(
    document.getElementById('climate-state-select'),
    document.getElementById('climate-lga-select'),
    _onRegionChange,
  ).then(p => { _cs.pickers = p; })
   .catch(err => _climateToast(`Could not load state/LGA list: ${err.message}`, 'warning'));
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

  const regionId = _currentRegionId();
  const date     = (document.getElementById('climate-date-input') || {}).value || '';
  const region   = NigeriaAdmin.region(regionId);

  _setLoading(true, `Analyzing ${_cs.selectedLayer} · ${region ? region.label : regionId}…`);
  _clearStats();
  _hideAlertBox();

  try {
    const params = new URLSearchParams({ layer_id: _cs.selectedLayer, region_id: regionId });
    if (date) params.set('date', date);

    // apiJSON aborts after 3 min and turns PythonAnywhere's HTML 502/504
    // pages into readable errors, so the spinner never hangs forever.
    const json = await apiJSON(`/api/climate/analyze?${params}`);
    const result = json.result || {};

    if (!result.success) {
      _climateToast(result.message || 'Analysis failed', 'error');
      _showStats({ error: result.message });
      return;
    }

    // Region the trend panel should chart
    _cs.currentRegion = regionId;

    // Add tile layer to map
    if (result.tile_url) {
      _addTileLayer(result.tile_url);
    }

    _showStats(result.stats || {});
    _showLegend(_cs.selectedLayer);
    _checkAlerts(result);
    _showDatasetInfo(result.meta);

    // Reveal trend button now that we have a successful analysis
    const trendBtn = document.getElementById('climate-trend-btn');
    if (trendBtn) trendBtn.style.display = '';

  } catch (err) {
    const msg = err.message || String(err);
    _climateToast(msg, 'error');
    _showStats({ error: msg });
  } finally {
    _setLoading(false);
  }
}

/* ══════════════════════════════════════════════════════════════
   MAP HELPERS
══════════════════════════════════════════════════════════════ */
function _addTileLayer(url) {
  if (!_cs.map) return;

  // Remove previous climate tile
  if (_cs.tileLayer) {
    _cs.map.removeLayer(_cs.tileLayer);
    _cs.tileLayer = null;
  }

  // Tiles go to the tilePane, which is always below the boundary vectors
  // in the overlayPane, so no re-stacking is needed.
  _cs.tileLayer = L.tileLayer(url, { opacity: 0.85, maxZoom: 18 }).addTo(_cs.map);
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
  const row = (label, value, cls = '') =>
    `<div class="cs-row ${cls}"><span class="cs-key">${esc(label)}</span><strong class="cs-val">${value}</strong></div>`;
  const rows = [];
  Object.entries(stats)
    .filter(([k]) => !skipKeys.has(k))
    .forEach(([k, v]) => {
      const label = k.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
      if (v && typeof v === 'object') {
        // e.g. land-cover class counts → share of the area per class
        const entries = Object.entries(v).filter(([, n]) => typeof n === 'number');
        const total = entries.reduce((sum, [, n]) => sum + n, 0) || 1;
        rows.push(row(label, ''));
        entries.sort((a, b) => b[1] - a[1]).forEach(([name, n]) =>
          rows.push(row(name, `${(n / total * 100).toFixed(1)}%`, 'cs-sub')));
        return;
      }
      const value = typeof v === 'boolean'
        ? (v ? '<span class="stat-yes">Yes ⚠</span>' : '<span class="stat-no">No</span>')
        : (typeof v === 'number'
            ? v.toLocaleString(undefined, { maximumFractionDigits: Math.abs(v) >= 100 ? 1 : 3 })
            : esc(v ?? '—'));
      rows.push(row(label, value));
    });

  // Extra info rows
  if (stats.region) rows.unshift(row('Region', esc(stats.region)));
  if (stats.period) rows.push(row('Period', esc(stats.period), 'cs-period'));
  if (stats.year)   rows.push(row('Year', esc(stats.year), 'cs-period'));
  if (stats.error)  rows.push(row('Error', esc(stats.error)));

  grid.innerHTML = rows.join('');
}

function _clearStats() {
  const grid = document.getElementById('climate-stats-grid');
  if (grid) grid.innerHTML = '';
  const info = document.getElementById('climate-dataset-info');
  if (info) info.textContent = '';
  // Hide trend btn and close chart panel on every new analysis start
  const trendBtn = document.getElementById('climate-trend-btn');
  if (trendBtn) trendBtn.style.display = 'none';
  toggleTrendPanel(true);
  const canvas = document.getElementById('climate-trend-canvas');
  if (canvas) canvas.dataset.loadedKey = '';
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
    showToast('Climate Monitor', msg, type);   // showToast(title, message, type)
  } else {
    console.warn('[ClimateMonitor]', msg);
  }
}

/* ══════════════════════════════════════════════════════════════
   DESKTOP INTEGRATION
   desktop.js calls window.ANSA.onClimateOpen() whenever the
   Climate Monitor window is shown (first open AND re-opens).
   initClimateMonitor handles both cases:
     - first open  → full init (map, controls, layer list)
     - re-open     → invalidateSize() so Leaflet redraws tiles
══════════════════════════════════════════════════════════════ */
document.addEventListener('DOMContentLoaded', () => {
  window.ANSA = window.ANSA || {};
  window.ANSA.onClimateOpen = initClimateMonitor;
});

/* ══════════════════════════════════════════════════════════════
   TREND CHART PANEL
   Uses Chart.js (loaded via CDN in index.html).
   Shows a 12-month time series for the selected layer plus 2
   correlated parameters, with Pearson r badges and an insight.
══════════════════════════════════════════════════════════════ */

let _trendChart = null;   // Chart.js instance — destroyed/re-created on each load

/**
 * Toggle the slide-up chart panel.
 * Pass `true` to force-close regardless of current state.
 */
function toggleTrendPanel(forceClose) {
  const panel = document.getElementById('climate-chart-panel');
  const btn   = document.getElementById('climate-trend-btn');
  if (!panel) return;

  const isOpen = panel.classList.contains('open');
  if (forceClose === true || (forceClose !== false && isOpen)) {
    panel.classList.remove('open');
    if (btn) btn.textContent = '📈 Show Trend Analysis';
    return;
  }

  panel.classList.add('open');
  if (btn) btn.textContent = '📉 Hide Trend Analysis';

  // Wait for the CSS height transition (~350 ms) before rendering the chart,
  // otherwise the canvas has 0 height and Chart.js draws nothing.
  setTimeout(() => _ensureTrendLoaded(), 380);
}

/** Load trend data only if not already loaded for this layer+region combo. */
function _ensureTrendLoaded() {
  const regionId = _cs.currentRegion || _currentRegionId();
  if (!_cs.selectedLayer || !regionId) return;
  const canvas = document.getElementById('climate-trend-canvas');
  if (!canvas) return;
  const key = `${_cs.selectedLayer}:${regionId}`;
  if (canvas.dataset.loadedKey === key) return;   // already rendered

  const date = (document.getElementById('climate-date-input') || {}).value || '';
  _loadTrend(_cs.selectedLayer, regionId, date);
}

/** Fetch `/api/climate/trend` and render the chart. */
async function _loadTrend(layerId, regionId, date) {
  const loading = document.getElementById('ccp-loading');
  if (loading) loading.style.display = 'flex';

  try {
    const params = new URLSearchParams({ layer_id: layerId, region_id: regionId });
    if (date) params.set('date', date);

    const json = await apiJSON(`/api/climate/trend?${params}`);
    const data = json.result || {};

    if (!data.success) {
      _renderTrendError(data.message || 'Trend data not available for this layer.');
      return;
    }
    _renderTrendChart(data);

    // Mark as loaded so we skip re-fetch on re-open
    const canvas = document.getElementById('climate-trend-canvas');
    if (canvas) canvas.dataset.loadedKey = `${layerId}:${regionId}`;

  } catch (err) {
    _renderTrendError(`Could not load trend: ${err.message}`);
  } finally {
    if (loading) loading.style.display = 'none';
  }
}

/** Show an error message inside the chart panel. */
function _renderTrendError(msg) {
  const insightEl = document.getElementById('ccp-insight');
  if (insightEl) insightEl.textContent = msg;
  const corrEl = document.getElementById('ccp-correlations');
  if (corrEl) corrEl.innerHTML = '';
  const tabTitle = document.getElementById('ccp-tab-title');
  if (tabTitle) tabTitle.textContent = '📈 Trend Analysis';
}

/** Render the Chart.js multi-line chart from the API result object. */
function _renderTrendChart(data) {
  if (typeof Chart === 'undefined') {
    _renderTrendError('Chart.js library not loaded — check your network connection.');
    return;
  }

  const canvas = document.getElementById('climate-trend-canvas');
  if (!canvas) return;

  // Destroy previous instance to avoid memory leaks
  if (_trendChart) { _trendChart.destroy(); _trendChart = null; }

  const primary    = data.primary    || {};
  const correlates = data.correlates || [];
  const labels     = (primary.series || []).map(p => p.month);

  const PALETTE = { primary: '#00d4b8', c0: '#00a8ff', c1: '#ffbe3d' };

  const datasets = [
    {
      label:            `${primary.label} (${primary.unit})`,
      data:             (primary.series || []).map(p => p.value),
      borderColor:      PALETTE.primary,
      backgroundColor:  'rgba(0,212,184,.09)',
      borderWidth:      2,
      pointRadius:      3,
      pointHoverRadius: 5,
      tension:          0.35,
      fill:             true,
      yAxisID:          'y',
      spanGaps:         true,
    },
  ];

  if (correlates[0]) {
    datasets.push({
      label:           `${correlates[0].label} (${correlates[0].unit})`,
      data:            (correlates[0].series || []).map(p => p.value),
      borderColor:     PALETTE.c0,
      backgroundColor: 'transparent',
      borderWidth:     1.5,
      borderDash:      [6, 3],
      pointRadius:     2,
      tension:         0.35,
      fill:            false,
      yAxisID:         'y1',
      spanGaps:        true,
    });
  }
  if (correlates[1]) {
    datasets.push({
      label:           `${correlates[1].label} (${correlates[1].unit})`,
      data:            (correlates[1].series || []).map(p => p.value),
      borderColor:     PALETTE.c1,
      backgroundColor: 'transparent',
      borderWidth:     1.5,
      borderDash:      [3, 3],
      pointRadius:     2,
      tension:         0.35,
      fill:            false,
      yAxisID:         'y1',
      spanGaps:        true,
    });
  }

  _trendChart = new Chart(canvas, {
    type: 'line',
    data: { labels, datasets },
    options: {
      responsive:           true,
      maintainAspectRatio:  false,
      interaction:          { mode: 'index', intersect: false },
      animation:            { duration: 400 },
      plugins: {
        legend: {
          position: 'top',
          labels: {
            color:    '#a8c4e0',
            boxWidth: 14,
            font:     { size: 10 },
            padding:  10,
          },
        },
        tooltip: {
          backgroundColor: 'rgba(7,21,37,.96)',
          titleColor:      '#e8f2ff',
          bodyColor:       '#a8c4e0',
          borderColor:     'rgba(0,168,255,.2)',
          borderWidth:     1,
          padding:         8,
        },
      },
      scales: {
        x: {
          grid:  { color: 'rgba(255,255,255,.04)' },
          ticks: { color: '#5a7a9a', font: { size: 10 }, maxTicksLimit: 12 },
        },
        y: {
          position: 'left',
          grid:     { color: 'rgba(255,255,255,.05)' },
          ticks:    { color: PALETTE.primary, font: { size: 10 } },
          title: {
            display: true,
            text:    primary.unit || '',
            color:   PALETTE.primary,
            font:    { size: 10 },
          },
        },
        y1: {
          position: 'right',
          grid:     { drawOnChartArea: false },
          ticks:    { color: PALETTE.c0, font: { size: 10 } },
          title: {
            display: correlates.length > 0,
            text:    correlates[0] ? correlates[0].unit : '',
            color:   PALETTE.c0,
            font:    { size: 10 },
          },
        },
      },
    },
  });

  // ── Tab title ────────────────────────────────────────────────
  const tabTitle = document.getElementById('ccp-tab-title');
  if (tabTitle) {
    tabTitle.textContent =
      `📈 ${primary.label} — ${data.months || 12}-Month Trend · ${data.region_label || ''}`;
  }

  // ── Correlation badges ───────────────────────────────────────
  const corrEl = document.getElementById('ccp-correlations');
  if (corrEl) {
    if (correlates.length === 0) {
      corrEl.innerHTML = '<div style="font-size:10px;color:var(--text-2)">No correlation data.</div>';
    } else {
      corrEl.innerHTML = correlates.map((c, i) => {
        const r   = c.correlation;
        const rFmt = (r !== null && r !== undefined) ? r.toFixed(2) : null;
        const cls  = !rFmt ? 'na' : (r >= 0 ? 'pos' : 'neg');
        const rTxt = rFmt ? `r=${rFmt}` : 'r=N/A';
        const col  = i === 0 ? PALETTE.c0 : PALETTE.c1;
        return `<div class="corr-badge" title="${c.description || ''}">
          <div class="corr-dot" style="background:${col}"></div>
          <span class="corr-label">${c.label}</span>
          <span class="corr-r ${cls}">${rTxt}</span>
        </div>`;
      }).join('');
    }
  }

  // ── Insight text ─────────────────────────────────────────────
  const insightEl = document.getElementById('ccp-insight');
  if (insightEl) insightEl.textContent = data.insight || '';
}

