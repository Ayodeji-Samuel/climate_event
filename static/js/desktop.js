/**
 * desktop.js — ANSASphere OS Shell
 * Manages: window lifecycle, drag/resize, taskbar, app launcher,
 *           notification panel, toast system, SSE heartbeat, clock.
 */

'use strict';

/* ══════════════════════════════════════════════════════════════
   CONSTANTS
══════════════════════════════════════════════════════════════ */
const APP_META = {
  'flood-monitor':    { title: 'Flood Monitor',         winId: 'win-flood-monitor'    },
  'ai-assistant':     { title: 'AI Assistant',          winId: 'win-ai-assistant'     },
  'alert-center':     { title: 'Alert Center',          winId: 'win-alert-center'     },
  'data-explorer':    { title: 'Data Explorer',         winId: 'win-data-explorer'    },
  'climate-monitor':  { title: 'Climate Monitor',       winId: 'win-climate-monitor'  },
  'settings':         { title: 'Settings',              winId: 'win-settings'         },
};

/* ══════════════════════════════════════════════════════════════
   BOOT SEQUENCE
══════════════════════════════════════════════════════════════ */
const BOOT_STEPS = [
  [10,  'Loading Earth Engine SDK…'],
  [30,  'Authenticating with Google Earth Engine…'],
  [55,  'Initialising AI agent…'],
  [75,  'Loading flood detection models…'],
  [90,  'Preparing interface…'],
  [100, 'ANSASphere ready.'],
];

function runBoot() {
  const bar     = document.getElementById('boot-bar');
  const status  = document.getElementById('boot-status');
  const screen  = document.getElementById('boot-screen');
  const desktop = document.getElementById('desktop');
  let idx = 0;

  function step() {
    if (idx >= BOOT_STEPS.length) {
      setTimeout(() => {
        screen.classList.add('fade-out');
        desktop.classList.remove('hidden');
        onDesktopReady();
      }, 600);
      return;
    }
    const [pct, msg] = BOOT_STEPS[idx++];
    bar.style.width = pct + '%';
    status.textContent = msg;
    setTimeout(step, 350 + Math.random() * 250);
  }
  step();
}

/* ══════════════════════════════════════════════════════════════
   DESKTOP READY
══════════════════════════════════════════════════════════════ */
function onDesktopReady() {
  startClock();
  bindDesktopIcons();
  bindAppLauncher();
  bindNotifPanel();
  initTaskbar();
  bindWindowEvents();
  connectSSE();
  loadInitialData();
  // Show flood monitor by default
  setTimeout(() => openApp('flood-monitor'), 400);
}

/* ══════════════════════════════════════════════════════════════
   CLOCK
══════════════════════════════════════════════════════════════ */
function startClock() {
  const el = document.getElementById('topbar-clock');
  function tick() {
    const now = new Date();
    const h = String(now.getHours()).padStart(2,'0');
    const m = String(now.getMinutes()).padStart(2,'0');
    el.textContent = `${h}:${m}`;
  }
  tick();
  setInterval(tick, 10000);
}

/* ══════════════════════════════════════════════════════════════
   WINDOW MANAGER
══════════════════════════════════════════════════════════════ */
let _zTop = 100;
const _winState = {};   // { winId: { minimized, maximized, origStyle } }

function getWin(appId) {
  const meta = APP_META[appId];
  return meta ? document.getElementById(meta.winId) : null;
}

function openApp(appId) {
  const win = getWin(appId);
  if (!win) return;

  win.classList.remove('hidden');
  win.style.display = '';

  const state = _winState[appId] || {};
  if (state.minimized) {
    state.minimized = false;
    _winState[appId] = state;
  }
  focusWindow(win, appId);
  updateTaskbar();

  // Leaflet requires invalidateSize() after its container becomes visible
  if (appId === 'flood-monitor') {
    setTimeout(() => window.ANSA?.invalidateMap?.(), 120);
  }
}

function closeApp(appId) {
  const win = getWin(appId);
  if (!win) return;
  win.classList.add('hidden');
  if (_winState[appId]) _winState[appId].minimized = false;
  updateTaskbar();
}

function minimizeApp(appId) {
  const win = getWin(appId);
  if (!win) return;
  win.classList.add('hidden');
  const state = _winState[appId] || {};
  state.minimized = true;
  _winState[appId] = state;
  updateTaskbar();
}

function toggleMaximize(appId) {
  const win = getWin(appId);
  if (!win) return;
  const state = _winState[appId] || {};
  if (state.maximized) {
    // Restore
    const orig = state.origStyle || {};
    win.style.left   = orig.left   || '';
    win.style.top    = orig.top    || '';
    win.style.width  = orig.width  || '';
    win.style.height = orig.height || '';
    win.classList.remove('maximized');
    state.maximized = false;
  } else {
    // Save + maximize
    state.origStyle = {
      left:   win.style.left,
      top:    win.style.top,
      width:  win.style.width,
      height: win.style.height,
    };
    win.classList.add('maximized');
    win.style.left = '0'; win.style.top = '0';
    win.style.width = '100%'; win.style.height = '100%';
    state.maximized = true;
  }
  _winState[appId] = state;
}

function focusWindow(win, appId) {
  _zTop++;
  win.style.zIndex = _zTop;
  document.querySelectorAll('.os-window').forEach(w => w.classList.remove('focused'));
  win.classList.add('focused');
  // Highlight taskbar btn
  document.querySelectorAll('.taskbar-btn').forEach(b => {
    b.classList.toggle('active', b.dataset.app === appId);
  });
}

/* ── Window control buttons ─────────────────────────────────── */
function bindWindowEvents() {
  // Delegate window control button clicks
  document.addEventListener('click', e => {
    const btn = e.target.closest('.wc-btn');
    if (!btn) return;
    const win = btn.closest('.os-window');
    if (!win) return;
    const appId = win.dataset.app;
    const action = btn.dataset.action;
    if (action === 'close')    closeApp(appId);
    if (action === 'minimize') minimizeApp(appId);
    if (action === 'maximize') toggleMaximize(appId);
  });

  // Clicking on a window brings it to focus
  document.addEventListener('mousedown', e => {
    const win = e.target.closest('.os-window');
    if (win) focusWindow(win, win.dataset.app);
  });

  // Enable drag + resize for all windows
  document.querySelectorAll('.os-window').forEach(enableDrag);
  document.querySelectorAll('.os-window').forEach(enableResize);
}

/* ── Drag ────────────────────────────────────────────────────── */
function enableDrag(win) {
  const titlebar = win.querySelector('[data-drag-target]');
  if (!titlebar) return;

  let ox = 0, oy = 0, startX = 0, startY = 0;

  titlebar.addEventListener('mousedown', e => {
    if (e.target.closest('.wc-btn, .win-toolbar, select, input, button')) return;
    const state = _winState[win.dataset.app] || {};
    if (state.maximized) return;

    startX = e.clientX; startY = e.clientY;
    ox = win.offsetLeft; oy = win.offsetTop;

    function onMove(ev) {
      win.style.left = (ox + ev.clientX - startX) + 'px';
      win.style.top  = (oy + ev.clientY - startY) + 'px';
    }
    function onUp() {
      document.removeEventListener('mousemove', onMove);
      document.removeEventListener('mouseup', onUp);
    }
    document.addEventListener('mousemove', onMove);
    document.addEventListener('mouseup',   onUp);
    e.preventDefault();
  });
}

/* ── Resize (bottom-right corner) ───────────────────────────── */
function enableResize(win) {
  win.addEventListener('mousedown', e => {
    const rect = win.getBoundingClientRect();
    const inCorner =
      e.clientX > rect.right  - 14 &&
      e.clientY > rect.bottom - 14;
    if (!inCorner) return;

    const state = _winState[win.dataset.app] || {};
    if (state.maximized) return;

    const startW = win.offsetWidth;
    const startH = win.offsetHeight;
    const startX = e.clientX;
    const startY = e.clientY;

    function onMove(ev) {
      const newW = Math.max(320, startW + ev.clientX - startX);
      const newH = Math.max(240, startH + ev.clientY - startY);
      win.style.width  = newW + 'px';
      win.style.height = newH + 'px';
    }
    function onUp() {
      document.removeEventListener('mousemove', onMove);
      document.removeEventListener('mouseup', onUp);
    }
    document.addEventListener('mousemove', onMove);
    document.addEventListener('mouseup',   onUp);
    e.preventDefault();
  });
}

/* ══════════════════════════════════════════════════════════════
   TASKBAR
══════════════════════════════════════════════════════════════ */
function initTaskbar() {
  updateTaskbar();
}

function updateTaskbar() {
  const container = document.getElementById('taskbar-items');
  container.innerHTML = '';

  for (const [appId, meta] of Object.entries(APP_META)) {
    const win = getWin(appId);
    if (!win) continue;
    const isOpen = !win.classList.contains('hidden');
    const state = _winState[appId] || {};
    if (!isOpen && !state.minimized) continue;

    const btn = document.createElement('button');
    btn.className = 'taskbar-btn' + (isOpen ? ' active' : ' minimized');
    btn.dataset.app = appId;
    btn.textContent = meta.title;
    btn.addEventListener('click', () => {
      if (state.minimized) {
        openApp(appId);
      } else {
        minimizeApp(appId);
      }
    });
    container.appendChild(btn);
  }
}

/* ══════════════════════════════════════════════════════════════
   DESKTOP ICONS
══════════════════════════════════════════════════════════════ */
function bindDesktopIcons() {
  document.querySelectorAll('.desk-icon').forEach(icon => {
    icon.addEventListener('dblclick', () => openApp(icon.dataset.app));
    icon.addEventListener('click',    () => openApp(icon.dataset.app));
  });
}

/* ══════════════════════════════════════════════════════════════
   APP LAUNCHER
══════════════════════════════════════════════════════════════ */
function bindAppLauncher() {
  const btn      = document.getElementById('app-launcher-btn');
  const launcher = document.getElementById('app-launcher');
  const search   = document.getElementById('launcher-search');

  btn.addEventListener('click', e => {
    e.stopPropagation();
    const isOpen = launcher.style.display !== 'none';
    launcher.style.display = isOpen ? 'none' : 'block';
    if (!isOpen) search.focus();
  });

  // Launcher items
  launcher.querySelectorAll('.launcher-item').forEach(item => {
    item.addEventListener('click', () => {
      openApp(item.dataset.app);
      launcher.style.display = 'none';
    });
  });

  // Search filter
  search.addEventListener('input', () => {
    const q = search.value.toLowerCase();
    launcher.querySelectorAll('.launcher-item').forEach(item => {
      item.style.display =
        item.textContent.toLowerCase().includes(q) ? '' : 'none';
    });
  });

  // Close on outside click
  document.addEventListener('click', e => {
    if (!launcher.contains(e.target) && e.target !== btn) {
      launcher.style.display = 'none';
    }
  });
}

/* ══════════════════════════════════════════════════════════════
   NOTIFICATION PANEL
══════════════════════════════════════════════════════════════ */
const _notifications = [];

function bindNotifPanel() {
  const btn   = document.getElementById('notif-btn');
  const panel = document.getElementById('notif-panel');

  btn.addEventListener('click', e => {
    e.stopPropagation();
    const isOpen = panel.style.display !== 'none';
    panel.style.display = isOpen ? 'none' : 'block';
    if (!isOpen) renderNotifPanel();
  });

  document.getElementById('clear-notifs-btn').addEventListener('click', () => {
    _notifications.length = 0;
    renderNotifPanel();
    updateNotifBadge();
  });

  document.addEventListener('click', e => {
    if (!panel.contains(e.target) && e.target !== btn) {
      panel.style.display = 'none';
    }
  });
}

function addNotification(title, body, type = 'info') {
  _notifications.unshift({
    title, body, type,
    time: new Date().toLocaleTimeString(),
  });
  if (_notifications.length > 50) _notifications.pop();
  updateNotifBadge();
  showToast(title, body, type);
}

function updateNotifBadge() {
  const badge = document.getElementById('notif-badge');
  const count = _notifications.length;
  badge.textContent = count > 99 ? '99+' : count;
  badge.style.display = count > 0 ? 'flex' : 'none';
}

function renderNotifPanel() {
  const list = document.getElementById('notif-list');
  if (_notifications.length === 0) {
    list.innerHTML = '<div class="notif-empty">No notifications</div>';
    return;
  }
  list.innerHTML = _notifications.map(n => `
    <div class="notif-item">
      <div class="notif-item-title">${esc(n.title)}</div>
      <div class="notif-item-body">${esc(n.body)}</div>
      <div class="notif-item-time">${esc(n.time)}</div>
    </div>
  `).join('');
}

/* ══════════════════════════════════════════════════════════════
   TOAST
══════════════════════════════════════════════════════════════ */
function showToast(title, message, type = 'info') {
  const container = document.getElementById('toast-container');
  const toast = document.createElement('div');
  toast.className = `toast ${type}`;
  toast.innerHTML = `
    <div class="toast-title">${esc(title)}</div>
    <div>${esc(message)}</div>
  `;
  container.appendChild(toast);
  setTimeout(() => {
    toast.classList.add('out');
    setTimeout(() => toast.remove(), 350);
  }, 4000);
}

/* ══════════════════════════════════════════════════════════════
   SSE — SERVER-SENT EVENTS
══════════════════════════════════════════════════════════════ */
let _sseSource = null;

function connectSSE() {
  if (_sseSource) _sseSource.close();
  _sseSource = new EventSource('/api/events/stream');

  _sseSource.onmessage = e => {
    try {
      const data = JSON.parse(e.data);
      handleSSEEvent(data);
    } catch {}
  };

  _sseSource.onerror = () => {
    updateGEEStatusUI(false);
    setTimeout(connectSSE, 15000);   // reconnect after 15 s
  };
}

function handleSSEEvent(data) {
  if (data.type === 'heartbeat') {
    updateGEEStatusUI(data.gee_connected);
    // Update notification badge from active alerts
    const badge = document.getElementById('notif-badge');
    if (data.active_alerts > 0) {
      badge.textContent = data.active_alerts;
      badge.style.display = 'flex';
    }
    // Update taskbar GEE indicator
    const dot = document.querySelector('#sys-gee .dot-sm');
    if (dot) dot.classList.toggle('online', data.gee_connected);
  }

  if (data.type === 'alerts_update' && data.count > 0) {
    addNotification(
      `${data.count} Active Alert${data.count !== 1 ? 's' : ''}`,
      `${data.alerts[0]?.region || ''}: ${data.alerts[0]?.label || ''}`,
      'warning'
    );
    // Refresh alert center if open
    if (!document.getElementById('win-alert-center').classList.contains('hidden')) {
      loadAlerts();
    }
  }
}

/* ══════════════════════════════════════════════════════════════
   SYSTEM STATUS
══════════════════════════════════════════════════════════════ */
function updateGEEStatusUI(connected) {
  const dot   = document.getElementById('gee-dot');
  const label = document.getElementById('gee-label');
  if (dot) {
    dot.classList.toggle('online',  connected);
    dot.classList.toggle('offline', !connected);
  }
  if (label) label.textContent = connected ? 'GEE ✓' : 'GEE ✗';
}

/* ══════════════════════════════════════════════════════════════
   INITIAL DATA LOAD
══════════════════════════════════════════════════════════════ */
async function loadInitialData() {
  try {
    const [healthRes, regionsRes] = await Promise.all([
      fetch('/api/health'),
      fetch('/api/flood/regions'),
    ]);
    const health  = await healthRes.json();
    const regions = await regionsRes.json();

    // GEE status
    updateGEEStatusUI(health.gee?.connected);

    // AI engine badge
    const aiEngine = health.agent?.engine || 'rule-based';
    const badge = document.getElementById('ai-engine-badge');
    if (badge) badge.textContent = aiEngine;

    // Populate region dropdown
    const sel = document.getElementById('flood-region-select');
    if (sel && regions.regions) {
      sel.innerHTML = regions.regions
        .map(r => `<option value="${r.id}">${r.label}</option>`)
        .join('');
    }

    // Settings panel
    updateSettings(health);
    // Data explorer overview
    updateDataOverview(health);
    // Regions in data explorer
    populateRegionsGrid(regions.regions || []);
    // Load alert history
    loadAlertHistory();
    // Load active alerts for alert center
    loadAlerts();

    // Set today's date in date picker
    const dateInput = document.getElementById('flood-date-input');
    if (dateInput) {
      dateInput.value = new Date().toISOString().split('T')[0];
    }

  } catch (err) {
    console.warn('Initial data load error:', err);
    showToast('Connection Warning', 'Could not reach API — is Flask running?', 'warning');
  }
}

/* ══════════════════════════════════════════════════════════════
   SETTINGS PANEL
══════════════════════════════════════════════════════════════ */
function updateSettings(health) {
  setText('s-gee-project', health.gee?.project || '—');
  setText('s-gee-status', health.gee?.connected ? '✓ Connected' : '✗ Disconnected');
  setText('s-ai-engine', health.agent?.engine || '—');
  setText('s-monitor-status', health.agent?.running ? 'Running' : 'Stopped');
  const lr = health.agent?.last_run;
  setText('s-last-run', lr ? new Date(lr).toLocaleTimeString() : 'Never');

  document.getElementById('s-gee-reconnect')?.addEventListener('click', async () => {
    await fetch('/api/gee/reconnect', { method: 'POST' });
    showToast('GEE', 'Reconnect requested', 'info');
    setTimeout(loadInitialData, 2000);
  });
  document.getElementById('s-start-monitor')?.addEventListener('click', async () => {
    await fetch('/api/ai/monitor/start', { method: 'POST' });
    showToast('AI Monitor', 'Background monitor started', 'success');
  });
  document.getElementById('s-stop-monitor')?.addEventListener('click', async () => {
    await fetch('/api/ai/monitor/stop', { method: 'POST' });
    showToast('AI Monitor', 'Background monitor stopped', 'warning');
  });
}

/* ══════════════════════════════════════════════════════════════
   DATA EXPLORER
══════════════════════════════════════════════════════════════ */
function updateDataOverview(health) {
  setText('de-gee-status', health.gee?.connected ? 'Online' : 'Offline');
  setText('de-alerts',     health.alerts?.active ?? '—');
  setText('de-ai-engine',  health.agent?.engine  || '—');
  setText('de-monitor',    health.agent?.running  ? 'Active' : 'Stopped');
}

function populateRegionsGrid(regions) {
  const grid = document.getElementById('regions-grid');
  if (!grid) return;
  grid.innerHTML = regions.map(r =>
    `<div class="region-chip" data-region="${r.id}">${r.label}</div>`
  ).join('');
  grid.querySelectorAll('.region-chip').forEach(chip => {
    chip.addEventListener('click', () => {
      const sel = document.getElementById('flood-region-select');
      if (sel) sel.value = chip.dataset.region;
      openApp('flood-monitor');
    });
  });
}

function loadAlertHistory() {
  fetch('/api/alerts?active=false&limit=50')
    .then(r => r.json())
    .then(data => {
      const tbody = document.getElementById('history-body');
      if (!tbody) return;
      const alerts = data.alerts || [];
      if (alerts.length === 0) {
        tbody.innerHTML = '<tr><td colspan="5" class="table-empty">No alerts recorded</td></tr>';
        return;
      }
      tbody.innerHTML = alerts.map(a => `
        <tr>
          <td>${esc(a.region || '')}</td>
          <td><span style="color:${a.color || '#999'}">${esc(a.label || '')}</span></td>
          <td>${esc((a.message || '').substring(0, 60))}${(a.message || '').length > 60 ? '…' : ''}</td>
          <td style="font-family:var(--mono);font-size:10px">${esc(fmtDate(a.created))}</td>
          <td>${a.active ? '<span style="color:var(--danger)">Active</span>' : '<span style="color:var(--success)">Resolved</span>'}</td>
        </tr>
      `).join('');
    })
    .catch(() => {});
}

// Tab switching
document.addEventListener('click', e => {
  const tab = e.target.closest('.data-tab');
  if (!tab) return;
  const tabId = tab.dataset.tab;
  document.querySelectorAll('.data-tab').forEach(t => t.classList.remove('active'));
  document.querySelectorAll('.data-panel').forEach(p => p.classList.remove('active'));
  tab.classList.add('active');
  document.getElementById(`tab-${tabId}`)?.classList.add('active');

  if (tabId === 'history') loadAlertHistory();
  if (tabId === 'regions') {
    fetch('/api/flood/regions')
      .then(r => r.json())
      .then(d => populateRegionsGrid(d.regions || []));
  }
});

/* ══════════════════════════════════════════════════════════════
   ALERT CENTER
══════════════════════════════════════════════════════════════ */
async function loadAlerts() {
  try {
    const res  = await fetch('/api/alerts?active=true');
    const data = await res.json();
    const alerts = data.alerts || [];
    renderAlerts(alerts);
    updateAlertStats(alerts);
  } catch (err) {
    console.warn('loadAlerts error:', err);
  }
}

function renderAlerts(alerts) {
  const list    = document.getElementById('alert-list');
  const noMsg   = document.getElementById('no-alerts-msg');
  if (!list) return;

  if (alerts.length === 0) {
    list.innerHTML = '';
    if (noMsg) { noMsg.style.display = ''; list.appendChild(noMsg); }
    return;
  }
  if (noMsg) noMsg.style.display = 'none';

  list.innerHTML = alerts.map(a => `
    <div class="alert-card" style="border-left-color:${a.color || '#aaa'}">
      <div class="alert-card-left">
        <div class="alert-card-region">${esc(a.region || '')}</div>
        <div class="alert-card-message">${esc(a.message || '')}</div>
        <div class="alert-card-meta">Source: ${esc(a.source || 'manual')} · ${esc(fmtDate(a.created))}</div>
      </div>
      <div class="alert-card-right">
        <span class="alert-level-badge"
              style="background:${hexAlpha(a.color,'.15')};color:${a.color};border:1px solid ${hexAlpha(a.color,'.3')}">
          ${esc(a.label || '')}
        </span>
        <button class="resolve-btn" data-id="${a.id}">Resolve</button>
      </div>
    </div>
  `).join('');

  list.querySelectorAll('.resolve-btn').forEach(btn => {
    btn.addEventListener('click', async () => {
      await fetch(`/api/alerts/${btn.dataset.id}/resolve`, { method: 'PATCH' });
      loadAlerts();
      showToast('Alert', 'Alert resolved', 'success');
    });
  });
}

function updateAlertStats(alerts) {
  const counts = {1:0, 2:0, 3:0, 4:0};
  alerts.forEach(a => { if (counts[a.level] !== undefined) counts[a.level]++; });
  setText('stat-total-alerts', alerts.length);
  setText('stat-watch',      counts[1]);
  setText('stat-advisory',   counts[2]);
  setText('stat-warning',    counts[3]);
  setText('stat-emergency',  counts[4]);
}

// Refresh button
document.getElementById('refresh-alerts-btn')?.addEventListener('click', loadAlerts);

// New alert button → modal
document.getElementById('new-alert-btn')?.addEventListener('click', () => {
  document.getElementById('alert-modal').style.display = 'flex';
});
['alert-modal-close','alert-modal-cancel'].forEach(id => {
  document.getElementById(id)?.addEventListener('click', () => {
    document.getElementById('alert-modal').style.display = 'none';
  });
});
document.getElementById('alert-modal-submit')?.addEventListener('click', async () => {
  const region  = document.getElementById('modal-region').value.trim();
  const level   = parseInt(document.getElementById('modal-level').value);
  const message = document.getElementById('modal-message').value.trim();
  if (!region || !message) { showToast('Error', 'Region and message are required', 'error'); return; }
  const res = await fetch('/api/alerts', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ region, level, message }),
  });
  const data = await res.json();
  if (data.status === 'ok') {
    document.getElementById('alert-modal').style.display = 'none';
    loadAlerts();
    showToast('Alert Created', `Level ${level} alert for ${region}`, 'success');
  } else {
    showToast('Error', data.message || 'Failed to create alert', 'error');
  }
});

// Quick create alert from flood monitor
document.getElementById('flood-alert-btn')?.addEventListener('click', () => {
  const region = document.getElementById('flood-region-select')?.value || '';
  const meta   = APP_META['flood-monitor'];
  document.getElementById('modal-region').value  = region;
  document.getElementById('modal-message').value = `Flood detected in ${region} — automated from satellite analysis.`;
  document.getElementById('alert-modal').style.display = 'flex';
});

/* ══════════════════════════════════════════════════════════════
   UTILITIES
══════════════════════════════════════════════════════════════ */
function esc(str) {
  return String(str || '')
    .replace(/&/g,'&amp;')
    .replace(/</g,'&lt;')
    .replace(/>/g,'&gt;')
    .replace(/"/g,'&quot;');
}

function setText(id, val) {
  const el = document.getElementById(id);
  if (el) el.textContent = val;
}

function fmtDate(iso) {
  if (!iso) return '—';
  try { return new Date(iso).toLocaleString([], {dateStyle:'short',timeStyle:'short'}); }
  catch { return iso; }
}

function hexAlpha(hex, alpha) {
  if (!hex || !hex.startsWith('#')) return `rgba(160,160,160,${alpha})`;
  const r = parseInt(hex.slice(1,3),16);
  const g = parseInt(hex.slice(3,5),16);
  const b = parseInt(hex.slice(5,7),16);
  return `rgba(${r},${g},${b},${alpha})`;
}

/* ══════════════════════════════════════════════════════════════
   ENTRY POINT
══════════════════════════════════════════════════════════════ */
document.addEventListener('DOMContentLoaded', runBoot);

// Expose helpers to other scripts
window.ANSA = { openApp, showToast, addNotification, loadAlerts, esc };
