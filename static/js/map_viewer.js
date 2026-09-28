/**
 * map_viewer.js — Leaflet map + GEE tile management
 * Initialises a Leaflet map in #flood-map using CartoDB DarkMatter.
 * Region = Nigeria, a state or an LGA, chosen with the State/LGA pickers
 * or by clicking the map (see nigeria_admin.js).  Flood analysis is
 * triggered by the "Analyze" button; results are visualised as GEE tile
 * overlays clipped to the selected boundary.
 */

'use strict';

let _map      = null;
let _floodLayer   = null;
let _sarLayer     = null;
let _waterLayer   = null;
let _analysisData = null;
let _boundaries   = null;   // NigeriaBoundaryLayer
let _pickers      = null;   // { regionId(), set(id) }

/* ══════════════════════════════════════════════════════════════
   INIT
══════════════════════════════════════════════════════════════ */
function initMap() {
  if (_map) return;

  _map = L.map('flood-map', {
    center: [9.0820, 8.6753],   // Nigeria centre
    zoom: 6,
    zoomControl: true,
    attributionControl: true,
  });

  // Register helpers so desktop.js can reach the flood map
  const expose = () => {
    window.ANSA.invalidateMap    = () => _map?.invalidateSize();
    window.ANSA.selectFloodRegion = selectRegion;
    window.ANSA.floodRegion      = () => NigeriaAdmin.region(currentRegionId());
  };
  if (window.ANSA) expose();
  else window.addEventListener('load', () => window.ANSA && expose());

  // ── Dark base layer + labels ─────────────────────────────────
  addDarkBasemap(_map);

  // Satellite layer (alternate, not added by default)
  window._satLayer = L.tileLayer(
    'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
    { attribution: 'ESRI', maxZoom: 19 }
  );

  // ── Nigeria boundaries + State/LGA pickers ───────────────────
  _boundaries = new NigeriaBoundaryLayer(_map, selectRegion);
  _boundaries.show('nigeria');
  NigeriaAdmin.bindPickers(
    document.getElementById('flood-state-select'),
    document.getElementById('flood-lga-select'),
    onRegionChange,
  ).then(p => { _pickers = p; })
   .catch(err => {
     console.warn('Region list failed to load:', err);
     window.ANSA?.showToast('Boundaries', 'Could not load state/LGA list', 'warning');
   });

  bindMapControls();
}

function currentRegionId() {
  return _pickers ? _pickers.regionId() : 'nigeria';
}

/** Programmatic selection (map click, region chip, alert link). */
function selectRegion(regionId) {
  _pickers?.set(regionId);
  onRegionChange(regionId);
}

function onRegionChange(regionId) {
  _boundaries?.show(regionId);
  clearResult();
  if (document.getElementById('layer-water')?.checked) loadWaterLayer();
}

/* ══════════════════════════════════════════════════════════════
   CONTROLS
══════════════════════════════════════════════════════════════ */
function bindMapControls() {
  // Run analysis button
  document.getElementById('run-flood-btn')?.addEventListener('click', runAnalysis);

  // Layer toggles
  document.getElementById('layer-flood')?.addEventListener('change', e => {
    if (_floodLayer) {
      e.target.checked ? _floodLayer.addTo(_map) : _map.removeLayer(_floodLayer);
    }
  });
  document.getElementById('layer-sar')?.addEventListener('change', e => {
    if (_sarLayer) {
      e.target.checked ? _sarLayer.addTo(_map) : _map.removeLayer(_sarLayer);
    }
  });
  document.getElementById('layer-water')?.addEventListener('change', e => {
    if (e.target.checked) {
      loadWaterLayer();
    } else if (_waterLayer) {
      _map.removeLayer(_waterLayer);
    }
  });
}

/* ══════════════════════════════════════════════════════════════
   FLOOD ANALYSIS
══════════════════════════════════════════════════════════════ */
async function runAnalysis() {
  const regionId = currentRegionId();
  const date     = document.getElementById('flood-date-input')?.value ||
                   new Date().toISOString().split('T')[0];

  setLoadingState(true);
  _boundaries?.fit(regionId);

  try {
    const params = new URLSearchParams({ region_id: regionId, date });
    const data = await apiJSON(`/api/flood/analyze?${params}`);

    _analysisData = data.result;
    displayResult(_analysisData);

    // Show create-alert button if risk >= advisory
    const level = _analysisData?.risk?.level || 0;
    const alertBtn = document.getElementById('flood-alert-btn');
    if (alertBtn) alertBtn.style.display = level >= 2 ? '' : 'none';

    // Auto-notification if high risk
    if (level >= 3) {
      window.ANSA?.addNotification(
        `${_analysisData.risk.label} — ${_analysisData.region_label}`,
        `${_analysisData.stats.flooded_area_km2.toLocaleString()} km² flooded`,
        'warning'
      );
    }
  } catch (err) {
    console.error('Analysis error:', err);
    window.ANSA?.showToast('Analysis Error', err.message || String(err), 'error');
    displayNoData();
  } finally {
    setLoadingState(false);
  }
}

/* ══════════════════════════════════════════════════════════════
   DISPLAY RESULT
══════════════════════════════════════════════════════════════ */
function removeResultLayers() {
  if (_floodLayer) { _map.removeLayer(_floodLayer); _floodLayer = null; }
  if (_sarLayer)   { _map.removeLayer(_sarLayer);   _sarLayer   = null; }
}

/** Reset map overlays + sidebar when the region changes. */
function clearResult() {
  removeResultLayers();
  _analysisData = null;
  ['stat-flooded', 'stat-total', 'stat-pct', 'stat-images', 'stat-updated']
    .forEach(id => updateStat(id, '—'));
  const badge = document.getElementById('risk-badge');
  if (badge) { badge.textContent = '—'; badge.style.color = ''; }
  const detail = document.getElementById('risk-detail');
  if (detail) detail.textContent = 'Run analysis to view';
  const alertBtn = document.getElementById('flood-alert-btn');
  if (alertBtn) alertBtn.style.display = 'none';
}

function displayResult(result) {
  // ── Remove old layers ────────────────────────────────────────
  removeResultLayers();

  // ── Add new GEE tile layers ──────────────────────────────────
  if (result.flood_tile_url) {
    _floodLayer = L.tileLayer(result.flood_tile_url, {
      opacity: 0.85,
      maxZoom: 19,
      attribution: 'Google Earth Engine | Sentinel-1',
    });
    if (document.getElementById('layer-flood')?.checked) {
      _floodLayer.addTo(_map);
    }
  }
  if (result.sar_tile_url) {
    _sarLayer = L.tileLayer(result.sar_tile_url, {
      opacity: 0.6,
      maxZoom: 19,
      attribution: 'Google Earth Engine | Sentinel-1 SAR',
    });
    if (document.getElementById('layer-sar')?.checked) {
      _sarLayer.addTo(_map);
    }
  }

  // ── Stats panel ─────────────────────────────────────────────
  const s = result.stats || {};
  const r = result.risk  || {};

  updateStat('stat-flooded', fmt(s.flooded_area_km2, ' km²'));
  updateStat('stat-total',   fmt(s.total_area_km2,   ' km²'));
  updateStat('stat-pct',     fmt(s.flood_fraction_pct, '%'));
  updateStat('stat-images',  `${s.before_images ?? '—'}/${s.after_images ?? '—'}`);
  updateStat('stat-updated', result.analysis_time
    ? new Date(result.analysis_time).toLocaleTimeString() : '—');

  // ── Risk badge ───────────────────────────────────────────────
  const badge  = document.getElementById('risk-badge');
  const detail = document.getElementById('risk-detail');
  if (badge) {
    badge.textContent  = r.label || '—';
    badge.style.color  = r.color || 'var(--accent)';
  }
  if (detail) {
    detail.textContent = result.success
      ? `${result.region_label}: ${riskDescription(r.level || 0)}`
      : (result.message || '—');
  }

  // ── Success toast ────────────────────────────────────────────
  if (result.success) {
    window.ANSA?.showToast(
      `${result.region_label} — ${r.label}`,
      `Flooded: ${fmt(s.flooded_area_km2, ' km²')} (${fmt(s.flood_fraction_pct, '%')})`,
      riskToastType(r.level)
    );
  } else {
    window.ANSA?.showToast('No Data', result.message || 'No imagery available', 'warning');
  }
}

function displayNoData() {
  updateStat('stat-flooded', '—');
  updateStat('stat-total',   '—');
  updateStat('stat-pct',     '—');
  updateStat('stat-images',  '—');
  updateStat('stat-updated', '—');
  const badge = document.getElementById('risk-badge');
  if (badge) { badge.textContent = 'Error'; badge.style.color = 'var(--danger)'; }
}

/* ══════════════════════════════════════════════════════════════
   PERMANENT WATER LAYER
══════════════════════════════════════════════════════════════ */
async function loadWaterLayer() {
  const regionId = currentRegionId();
  try {
    const data = await apiJSON(`/api/flood/water-layer?region_id=${encodeURIComponent(regionId)}`);
    if (regionId !== currentRegionId()) return;   // region changed meanwhile
    if (data.tile_url) {
      if (_waterLayer) _map.removeLayer(_waterLayer);
      _waterLayer = L.tileLayer(data.tile_url, {
        opacity: 0.7,
        maxZoom: 19,
        attribution: 'JRC Global Surface Water',
      });
      if (document.getElementById('layer-water')?.checked) {
        _waterLayer.addTo(_map);
      }
    }
  } catch (err) {
    console.warn('Water layer error:', err);
  }
}

/* ══════════════════════════════════════════════════════════════
   HELPERS
══════════════════════════════════════════════════════════════ */
function setLoadingState(loading) {
  const overlay = document.getElementById('map-loading');
  if (overlay) overlay.classList.toggle('active', loading);
  const btn = document.getElementById('run-flood-btn');
  if (btn) btn.disabled = loading;
}

function updateStat(id, val) {
  const el = document.getElementById(id);
  if (el) el.textContent = val;
}

function fmt(val, suffix = '') {
  if (val === undefined || val === null) return '—';
  if (typeof val === 'number') {
    return val.toLocaleString(undefined, { maximumFractionDigits: 1 }) + suffix;
  }
  return val + suffix;
}

function riskDescription(level) {
  return [
    'No significant flooding detected.',
    'Watch: Minor flooding possible in low-lying areas.',
    'Advisory: Moderate flooding likely. Prepare emergency kits.',
    'Warning: Significant flooding underway. Evacuate low-lying areas.',
    'Emergency: Severe flooding. Immediate evacuation required.',
  ][Math.min(level, 4)] || '—';
}

function riskToastType(level) {
  if (level >= 3) return 'error';
  if (level >= 1) return 'warning';
  return 'success';
}

/* ══════════════════════════════════════════════════════════════
   ENTRY — wait for desktop to reveal the window before init
══════════════════════════════════════════════════════════════ */
document.addEventListener('DOMContentLoaded', () => {
  // Map container is inside a window that starts visible — init after boot
  const observer = new MutationObserver(() => {
    const win = document.getElementById('win-flood-monitor');
    if (win && !win.classList.contains('hidden') && !_map) {
      initMap();
      observer.disconnect();
    }
  });
  observer.observe(document.getElementById('win-flood-monitor') || document.body, {
    attributes: true, attributeFilter: ['class'],
  });

  // Also init immediately in case window is already visible
  setTimeout(() => {
    if (!_map) initMap();
  }, 1500);
});
