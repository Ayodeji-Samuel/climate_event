/**
 * visitors.js — site visit counting + the Data Explorer "Visitors" tab.
 * Depends on: desktop.js (apiJSON, esc, setText), Chart.js.
 *
 * On page load a beacon (POST /api/visits) records the visit.  The server
 * counts each browser once per session (30 min of inactivity by default)
 * and stores only the visitor's country, resolved server-side from a local
 * DB-IP database — never the IP address.
 *
 * The Visitors tab shows visits per country for Daily (today), Weekly
 * (last 7 days), Monthly (last 30 days) and Overall (all time), in the
 * server's local time (Nigeria, UTC+1).
 */

'use strict';

(() => {
  let _period = 'day';
  let _chart  = null;
  let _seq    = 0;          // drops stale responses when periods are clicked quickly

  const _regionNames = (() => {
    try { return new Intl.DisplayNames(['en'], { type: 'region' }); } catch { return null; }
  })();

  function countryName(code) {
    if (code === 'ZZ') return 'Unknown / private network';
    try { return _regionNames?.of(code) || code; } catch { return code; }
  }

  function fmtDay(iso) {
    return new Date(`${iso}T12:00:00Z`).toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' });
  }

  function seriesLabel(key) {
    // "YYYY-MM-DD" (daily bars) or "YYYY-MM" (monthly bars for Overall)
    const d = new Date(key.length === 7 ? `${key}-15T12:00:00Z` : `${key}T12:00:00Z`);
    return key.length === 7
      ? d.toLocaleDateString(undefined, { month: 'short', year: 'numeric' })
      : d.toLocaleDateString(undefined, { day: 'numeric', month: 'short' });
  }

  /* ── Recording ──────────────────────────────────────────────── */
  function recordVisit() {
    fetch('/api/visits', { method: 'POST', keepalive: true, credentials: 'same-origin' })
      .catch(() => {});   // statistics must never disturb the app
  }

  /* ── Loading ────────────────────────────────────────────────── */
  async function load(period = _period) {
    _period = period;
    const seq = ++_seq;
    document.querySelectorAll('#visits-period button').forEach(b => {
      const on = b.dataset.period === period;
      b.classList.toggle('active', on);
      b.setAttribute('aria-pressed', String(on));
    });
    try {
      const data = await apiJSON(`/api/visits/stats?period=${encodeURIComponent(period)}`, { timeout: 20000 });
      if (seq === _seq) render(data);
    } catch (err) {
      if (seq !== _seq) return;
      const body = document.getElementById('visits-body');
      if (body) body.innerHTML = `<tr><td colspan="4" class="table-empty">${esc(err.message)}</td></tr>`;
    }
  }

  /* ── Rendering ──────────────────────────────────────────────── */
  function render(d) {
    const known = d.countries.filter(c => c.code !== 'ZZ');
    const top   = known[0];

    setText('vis-total', d.total.toLocaleString());
    setText('vis-total-sub', d.label);
    setText('vis-countries', known.length.toLocaleString());
    setText('vis-top', top ? countryName(top.code) : '—');
    setText('vis-top-sub', top ? `${top.visits.toLocaleString()} visits · ${top.share}%` : 'No visits yet');
    const topEl = document.getElementById('vis-top');
    if (topEl) topEl.title = top ? countryName(top.code) : '';

    const range = !d.start ? 'No visits recorded yet'
      : d.start === d.end ? fmtDay(d.end)
      : `${fmtDay(d.start)} – ${fmtDay(d.end)}`;
    setText('visits-range', `${range} · ${d.timezone}`);

    const note = document.getElementById('visits-note');
    if (note) {
      note.style.display = d.geoip?.available ? 'none' : 'block';
      note.textContent = 'The country lookup database is not installed, so visits are recorded as ' +
        '"Unknown". Run scripts/update_geoip.py on the server.';
    }

    renderTable(d);
    renderChart(d);

    const foot =document.getElementById('visits-foot');
    if (foot) {
      foot.innerHTML =
        `A visit is one browser, counted again after ${d.session_minutes} minutes of inactivity. ` +
        'Only the country is stored, never the IP address. ' +
        'IP geolocation by <a href="https://db-ip.com" target="_blank" rel="noopener">DB-IP</a>' +
        (d.geoip?.build_date ? ` (database of ${fmtDay(d.geoip.build_date)}).` : '.');
    }
  }

  function renderTable(d) {
    const body = document.getElementById('visits-body');
    if (!body) return;
    if (!d.countries.length) {
      body.innerHTML = `<tr><td colspan="4" class="table-empty">No visits ${d.period === 'all' ? 'recorded yet' : 'in this period'}.</td></tr>`;
      return;
    }
    body.innerHTML = d.countries.map((c, i) => `
      <tr>
        <td class="vis-rank">${i + 1}</td>
        <td><span class="cc-badge">${esc(c.code === 'ZZ' ? '??' : c.code)}</span>${esc(countryName(c.code))}</td>
        <td class="num">${c.visits.toLocaleString()}</td>
        <td>
          <div class="share-cell">
            <div class="share-bar"><span style="width:${Math.max(c.share, 1)}%"></span></div>
            <span class="share-pct">${c.share.toFixed(1)}%</span>
          </div>
        </td>
      </tr>`).join('');
  }

  function renderChart(d) {
    const wrap   = document.getElementById('visits-chart-wrap');
    const canvas = document.getElementById('visits-chart');
    if (!wrap || !canvas) return;

    // A single day has no trend to plot
    const show = d.period !== 'day' && typeof Chart !== 'undefined';
    wrap.style.display = show ? '' : 'none';
    if (_chart) { _chart.destroy(); _chart = null; }
    if (!show) return;

    _chart = new Chart(canvas, {
      type: 'bar',
      data: {
        labels: d.series.map(p => seriesLabel(p.key)),
        datasets: [{
          label: d.period === 'all' ? 'Visits per month' : 'Visits per day',
          data: d.series.map(p => p.visits),
          backgroundColor: 'rgba(0,168,255,.55)',
          hoverBackgroundColor: '#00a8ff',
          borderRadius: 3,
          maxBarThickness: 28,
        }],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        animation: { duration: 300 },
        plugins: {
          legend: { display: false },
          tooltip: {
            backgroundColor: 'rgba(7,21,37,.96)',
            titleColor: '#e8f2ff', bodyColor: '#a8c4e0',
            borderColor: 'rgba(0,168,255,.2)', borderWidth: 1,
          },
        },
        scales: {
          x: { grid: { display: false }, ticks: { color: '#5a7a9a', font: { size: 10 }, maxTicksLimit: 10 } },
          y: {
            beginAtZero: true,
            grid: { color: 'rgba(255,255,255,.05)' },
            ticks: { color: '#5a7a9a', font: { size: 10 }, precision: 0 },
          },
        },
      },
    });
  }

  /* ── Wiring ─────────────────────────────────────────────────── */
  document.addEventListener('DOMContentLoaded', () => {
    recordVisit();

    document.getElementById('visits-period')?.addEventListener('click', e => {
      const btn = e.target.closest('button[data-period]');
      if (btn) load(btn.dataset.period);
    });

    // desktop.js switches the panels; refresh the numbers whenever the tab opens
    document.addEventListener('click', e => {
      if (e.target.closest('.data-tab[data-tab="visitors"]')) load();
    });
  });
})();
