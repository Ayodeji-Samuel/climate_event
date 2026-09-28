/**
 * ai_agent.js — ANSA AI Assistant chat interface
 * Handles message submission, response rendering (Markdown-lite),
 * tool-call visualisation, and quick-action buttons.
 */

'use strict';

let _sessionId = 'session-' + Date.now();
let _thinking  = false;

/* ══════════════════════════════════════════════════════════════
   INIT
══════════════════════════════════════════════════════════════ */
document.addEventListener('DOMContentLoaded', () => {
  bindChat();
  loadAIStatus();
});

function bindChat() {
  const sendBtn   = document.getElementById('chat-send-btn');
  const textarea  = document.getElementById('chat-input');

  sendBtn?.addEventListener('click', sendMessage);

  textarea?.addEventListener('keydown', e => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      sendMessage();
    }
  });

  // Quick action buttons
  document.querySelectorAll('.quick-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      const msg = btn.dataset.msg;
      if (msg) submitMessage(msg);
    });
  });
}

/* ══════════════════════════════════════════════════════════════
   SEND
══════════════════════════════════════════════════════════════ */
function sendMessage() {
  const textarea = document.getElementById('chat-input');
  const text     = textarea?.value?.trim();
  if (!text || _thinking) return;
  textarea.value = '';
  submitMessage(text);
}

async function submitMessage(text) {
  if (_thinking) return;
  _thinking = true;

  appendUserMessage(text);
  const thinkEl = appendThinkingIndicator();

  try {
    const res  = await fetch('/api/ai/chat', {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify({ message: text, session_id: _sessionId }),
    });
    const data = await res.json();
    thinkEl.remove();

    if (data.status === 'ok') {
      const resp = data.response;
      appendAssistantMessage(
        resp.response,
        resp.tool_calls || [],
        resp.engine
      );
    } else {
      appendAssistantMessage(
        'Sorry, I encountered an error. Please try again.',
        [], 'error'
      );
    }
  } catch (err) {
    thinkEl.remove();
    appendAssistantMessage(
      `Connection error: ${err.message}. Is the Flask server running?`,
      [], 'error'
    );
  } finally {
    _thinking = false;
  }
}

/* ══════════════════════════════════════════════════════════════
   RENDER MESSAGES
══════════════════════════════════════════════════════════════ */
function appendUserMessage(text) {
  const el = document.createElement('div');
  el.className = 'chat-msg user';
  el.innerHTML = `
    <div class="msg-avatar">U</div>
    <div class="msg-bubble"><p>${esc(text)}</p></div>
  `;
  appendToChat(el);
}

function appendAssistantMessage(text, toolCalls, engine) {
  const el = document.createElement('div');
  el.className = 'chat-msg assistant';

  // Tool call traces
  let tracesHtml = '';
  if (toolCalls && toolCalls.length > 0) {
    tracesHtml = toolCalls.map(tc => {
      const toolName = tc.tool || 'tool';
      const summary  = summariseTool(toolName, tc.result);
      return `<div class="tool-trace">
        <span class="tool-trace-name">⚙ ${esc(toolName)}</span> — ${esc(summary)}
      </div>`;
    }).join('');
  }

  const rendered = renderMarkdown(text || '');

  el.innerHTML = `
    <div class="msg-avatar">A</div>
    <div class="msg-bubble">
      ${tracesHtml}
      ${rendered}
    </div>
  `;
  appendToChat(el);

  // Update engine badge
  if (engine && engine !== 'error') {
    const badge = document.getElementById('ai-engine-badge');
    if (badge) badge.textContent = engine;
  }
}

function appendThinkingIndicator() {
  const el = document.createElement('div');
  el.className = 'chat-msg assistant';
  el.id = 'thinking-indicator';
  el.innerHTML = `
    <div class="msg-avatar">A</div>
    <div class="msg-bubble">
      <div class="thinking-dots">
        <span></span><span></span><span></span>
      </div>
    </div>
  `;
  appendToChat(el);
  return el;
}

function appendToChat(el) {
  const container = document.getElementById('chat-messages');
  if (!container) return;
  container.appendChild(el);
  // Smooth scroll to bottom
  container.scrollTo({ top: container.scrollHeight, behavior: 'smooth' });
}

/* ══════════════════════════════════════════════════════════════
   TOOL SUMMARY
══════════════════════════════════════════════════════════════ */
function summariseTool(name, result) {
  if (!result) return 'No result';
  if (result.error) return `Error: ${result.error}`;

  switch (name) {
    case 'analyze_flood_risk': {
      const risk = result.risk?.label || '—';
      const area = result.stats?.flooded_area_km2;
      return `${result.region_label || result.region_id || ''} — Risk: ${risk}${area !== undefined ? `, ${area.toLocaleString()} km² flooded` : ''}`;
    }
    case 'list_active_alerts': {
      const count = (result.alerts || []).length;
      return `${count} active alert${count !== 1 ? 's' : ''}`;
    }
    case 'explain_risk_level':
      return `Level ${result.level} — ${result.label}`;
    case 'recommend_action':
      return `${(result.actions || []).length} recommendations for Level ${result.risk_level}`;
    case 'list_lgas':
      return `${(result.lgas || []).length} LGAs in ${result.state || ''}`;
    case 'analyze_climate_layer':
      return result.success
        ? `${result.meta?.label || result.layer_id} — ${result.stats?.region || result.region_id}`
        : `Unavailable: ${result.message || 'no data'}`;
    default:
      return JSON.stringify(result).substring(0, 80);
  }
}

/* ══════════════════════════════════════════════════════════════
   LIGHTWEIGHT MARKDOWN RENDERER
   Supports: **bold**, *italic*, `code`, ## headers, • bullets
══════════════════════════════════════════════════════════════ */
function renderMarkdown(text) {
  if (!text) return '';
  let html = esc(text);

  // Headers
  html = html.replace(/^### (.+)$/gm, '<h4 style="color:var(--text-0);margin:6px 0 3px">$1</h4>');
  html = html.replace(/^## (.+)$/gm,  '<h3 style="color:var(--text-0);margin:8px 0 4px">$1</h3>');
  html = html.replace(/^# (.+)$/gm,   '<h2 style="color:var(--accent);margin:10px 0 6px">$1</h2>');

  // Bold + italic
  html = html.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
  html = html.replace(/\*(.+?)\*/g,     '<em>$1</em>');

  // Inline code
  html = html.replace(/`([^`]+)`/g, '<code>$1</code>');

  // Bullet points (• or - or *)
  html = html.replace(/^[•\-\*] (.+)$/gm,
    '<div style="display:flex;gap:8px;margin:2px 0"><span style="color:var(--accent);flex-shrink:0">•</span><span>$1</span></div>');

  // Numbered list
  html = html.replace(/^(\d+)\. (.+)$/gm,
    '<div style="display:flex;gap:8px;margin:2px 0"><span style="color:var(--teal);flex-shrink:0;font-family:var(--mono);font-size:11px">$1.</span><span>$2</span></div>');

  // Paragraphs (double newline)
  html = html
    .split(/\n\n+/)
    .map(para => para.trim() ? `<p>${para.replace(/\n/g, '<br>')}</p>` : '')
    .join('');

  return html;
}

/* ══════════════════════════════════════════════════════════════
   AI STATUS
══════════════════════════════════════════════════════════════ */
async function loadAIStatus() {
  try {
    const res  = await fetch('/api/ai/status');
    const data = await res.json();
    const engine = data.agent?.engine || 'rule-based';
    const badge  = document.getElementById('ai-engine-badge');
    if (badge) badge.textContent = engine;

    // Teal for the OpenRouter LLM, muted for rule-based
    const dot = document.getElementById('ai-dot');
    if (dot) dot.style.background =
      engine.startsWith('openrouter') ? 'var(--teal)' : 'var(--accent)';
  } catch {}
}

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
