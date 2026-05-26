/**
 * map_viewer.js — Leaflet map + GEE tile management
 * Initialises a Leaflet map in #flood-map using CartoDB DarkMatter.
 * Flood analysis is triggered by the "Analyze" button; results are
 * visualised as GEE tile overlays.
 */

'use strict';

let _map      = null;
let _floodLayer   = null;
let _sarLayer     = null;
let _waterLayer   = null;
let _analysisData = null;

/* ══════════════════════════════════════════════════════════════
   INIT
══════════════════════════════════════════════════════════════ */
function initMap() {
  if (_map) return;

  _map = L.map('flood-map', {
    center: [9.0820, 8.6753],   // Nigeria centre
    zoom: 5,
    zoomControl: true,
    attributionControl: true,
  });

  // Register invalidateMap so desktop.js can call it when the window is revealed
  if (window.ANSA) window.ANSA.invalidateMap = () => _map?.invalidateSize();
  else window.addEventListener('load', () => {
    if (window.ANSA) window.ANSA.invalidateMap = () => _map?.invalidateSize();
  });

  // ── Dark base layer ──────────────────────────────────────────
  L.tileLayer(
    'https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png',
    {
      attribution:
        '&copy; <a href="https://carto.com/">CARTO</a> &copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
      subdomains: 'abcd',
      maxZoom: 19,
    }
  ).addTo(_map);

  // Satellite layer (alternate, not added by default)
  window._satLayer = L.tileLayer(
    'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
    { attribution: 'ESRI', maxZoom: 19 }
  );

  bindMapControls();
}

/* ══════════════════════════════════════════════════════════════
   CONTROLS
══════════════════════════════════════════════════════════════ */
function bindMapControls() {
  // Run analysis button
  document.getElementById('run-flood-btn')?.addEventListener('click', runAnalysis);

  // Region select → re-centre map
  document.getElementById('flood-region-select')?.addEventListener('change', e => {
    flyToRegion(e.target.value);
  });

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
   REGION BOUNDING BOXES (must match flood_detector.py)
══════════════════════════════════════════════════════════════ */
const REGION_BOUNDS = {
  nigeria:     [[4.24,   2.68],  [13.89,  14.68]],
  ghana:       [[4.74,  -3.26],  [11.17,   1.19]],
  kenya:       [[-4.72, 33.91], [ 4.62,  41.90]],
  ethiopia:    [[3.40,  32.99],  [14.89,  47.98]],
  mozambique:  [[-26.86, 30.22], [-10.47, 40.84]],
  tanzania:    [[-11.75, 29.34], [ -0.99, 40.44]],
  bangladesh:  [[20.67, 88.01],  [26.63,  92.67]],
  india:       [[6.75,  68.16],  [35.50,  97.40]],
  pakistan:    [[23.69, 60.87],  [37.10,  77.84]],
  myanmar:     [[9.78,  92.19],  [28.53, 101.17]],
  thailand:    [[5.61,  97.34],  [20.47, 105.64]],
  indonesia:   [[-10.36, 95.01], [ 5.48, 141.02]],
};

function flyToRegion(regionId) {
  if (!_map) return;
  const bounds = REGION_BOUNDS[regionId];
  if (bounds) {
    _map.fitBounds(bounds, { padding: [20, 20], animate: true, duration: 0.8 });
  }
}

/* ══════════════════════════════════════════════════════════════
   FLOOD ANALYSIS
══════════════════════════════════════════════════════════════ */
async function runAnalysis() {
  const regionId = document.getElementById('flood-region-select')?.value;
  const date     = document.getElementById('flood-date-input')?.value ||
                   new Date().toISOString().split('T')[0];

  if (!regionId) {
    window.ANSA?.showToast('Error', 'Please select a region', 'error');
    return;
  }

  setLoadingState(true);
  flyToRegion(regionId);

  try {
    const url = `/api/flood/analyze?region_id=${encodeURIComponent(regionId)}&date=${date}`;
    const res  = await fetch(url);
    const data = await res.json();

    if (data.status !== 'ok') {
      throw new Error(data.message || 'API error');
    }

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
function displayResult(result) {
  // ── Remove old layers ────────────────────────────────────────
  if (_floodLayer) { _map.removeLayer(_floodLayer); _floodLayer = null; }
  if (_sarLayer)   { _map.removeLayer(_sarLayer);   _sarLayer   = null; }

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
    detail.textContent = riskDescription(r.level || 0);
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
  const regionId = document.getElementById('flood-region-select')?.value || 'nigeria';
  try {
    const res  = await fetch(`/api/flood/water-layer?region_id=${encodeURIComponent(regionId)}`);
    const data = await res.json();
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
    'Emergency: Catastrophic flooding. Immediate evacuation required.',
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
