/* Elwin Ransom — web UI
 *
 * Sections:
 *  1. Auth
 *  2. Service worker registration
 *  3. SSE streaming helper (fetch-based, works with POST)
 *  4. Message rendering
 *  5. Chat pipeline (text)
 *  6. Voice recording (hold-to-record)
 *  7. Image upload
 *  8. Sidebar actions
 *  9. Web Push setup
 * 10. UI helpers (textarea auto-resize, Enter to send)
 */

'use strict';

// ── DOM refs ─────────────────────────────────────────────────
const authScreen   = document.getElementById('auth-screen');
const authForm     = document.getElementById('auth-form');
const passwordInput = document.getElementById('password-input');
const authError    = document.getElementById('auth-error');
const app          = document.getElementById('app');
const messages     = document.getElementById('messages');
const messageInput = document.getElementById('message-input');
const sendBtn      = document.getElementById('send-btn');
const micBtn       = document.getElementById('mic-btn');
const attachBtn    = document.getElementById('attach-btn');
const fileInput    = document.getElementById('file-input');
const menuBtn      = document.getElementById('menu-btn');
const closeSidebar = document.getElementById('close-sidebar-btn');
const sidebarNav   = document.getElementById('sidebar-nav');
const overlay      = document.getElementById('sidebar-overlay');
const statusDot    = document.getElementById('status-dot');
const statusText   = document.getElementById('status-text');
const presenceSession = document.getElementById('presence-session');
const presenceNext = document.getElementById('presence-next');
const presenceFocus = document.getElementById('presence-focus');
const presenceDory = document.getElementById('presence-dory');
const infoPanel    = document.getElementById('info-panel');
const infoTitle    = document.getElementById('info-title');
const infoContent  = document.getElementById('info-content');
const infoClose    = document.getElementById('info-close');

// ── 1. Auth ──────────────────────────────────────────────────
let sessionToken = localStorage.getItem('elwin_token') || '';

async function checkAuth() {
  if (!sessionToken) { showAuth(); return; }
  try {
    const r = await fetch('/api/me', { headers: { 'X-Session-Token': sessionToken } });
    if (r.ok) { showApp(); } else { localStorage.removeItem('elwin_token'); sessionToken = ''; showAuth(); }
  } catch { showAuth(); }
}

function showAuth() {
  authScreen.style.display = 'flex';
  app.classList.remove('visible');
}

function showApp() {
  authScreen.style.display = 'none';
  app.classList.add('visible');
  refreshPresence().catch(() => {});
}

authForm.addEventListener('submit', async (e) => {
  e.preventDefault();
  authError.textContent = '';
  const pw = passwordInput.value;
  try {
    const r = await fetch('/api/auth', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ password: pw }),
    });
    if (r.ok) {
      const data = await r.json();
      sessionToken = data.token;
      localStorage.setItem('elwin_token', sessionToken);
      showApp();
    } else {
      authError.textContent = 'Incorrect password.';
      passwordInput.value = '';
    }
  } catch {
    authError.textContent = 'Connection error.';
  }
});

// ── 2. Service worker ─────────────────────────────────────────
if ('serviceWorker' in navigator) {
  navigator.serviceWorker.register('/static/sw.js', { scope: '/' }).catch(console.warn);
}

// ── 3. SSE streaming helper ───────────────────────────────────
/**
 * postAndStream — send a request and consume the SSE stream.
 * @param {string} url
 * @param {object|FormData|null} body — plain object → JSON; FormData → multipart; null → no body
 * @param {{ onToken, onEvent, method }} opts
 */
async function postAndStream(url, body, { onToken, onEvent, method = 'POST' } = {}) {
  const headers = { 'X-Session-Token': sessionToken };
  let fetchBody;

  if (body instanceof FormData) {
    fetchBody = body;
  } else if (body !== null && body !== undefined) {
    headers['Content-Type'] = 'application/json';
    fetchBody = JSON.stringify(body);
  }

  const resp = await fetch(url, { method, headers, body: fetchBody });
  if (!resp.ok) {
    if (resp.status === 401) { localStorage.removeItem('elwin_token'); sessionToken = ''; showAuth(); }
    throw new Error(`HTTP ${resp.status}`);
  }

  const reader = resp.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split('\n');
    buffer = lines.pop(); // last potentially incomplete line
    for (const line of lines) {
      if (!line.startsWith('data: ')) continue;
      try {
        const data = JSON.parse(line.slice(6));
        if (data.type === 'token' && onToken) {
          onToken(data.content);
        } else if (onEvent) {
          onEvent(data);
        }
      } catch { /* skip malformed */ }
    }
  }
}

// ── 4. Message rendering ──────────────────────────────────────
function appendMessage(role, text) {
  const div = document.createElement('div');
  div.className = `message ${role}`;
  const bubble = document.createElement('div');
  bubble.className = 'bubble';
  bubble.textContent = text;
  div.appendChild(bubble);
  messages.appendChild(div);
  scrollToBottom();
  return bubble;
}

function appendSystemMsg(text) {
  const div = document.createElement('div');
  div.className = 'message system';
  const bubble = document.createElement('div');
  bubble.className = 'bubble';
  bubble.textContent = text;
  div.appendChild(bubble);
  messages.appendChild(div);
  scrollToBottom();
}

function appendBotBubble() {
  const div = document.createElement('div');
  div.className = 'message bot';
  const bubble = document.createElement('div');
  bubble.className = 'bubble';

  // Typing indicator shown until first token arrives
  const dots = document.createElement('div');
  dots.className = 'typing-dots';
  dots.innerHTML = '<span></span><span></span><span></span>';
  bubble.appendChild(dots);

  div.appendChild(bubble);
  messages.appendChild(div);
  scrollToBottom();

  let dotsRemoved = false;
  function removeDots() {
    if (!dotsRemoved) { dots.remove(); dotsRemoved = true; }
  }

  // textSpan holds streamed text
  const textSpan = document.createElement('span');
  textSpan.className = 'bubble-text';
  bubble.appendChild(textSpan);

  return { bubble, textSpan, removeDots };
}

function appendConfirmChip(text, parentBubble) {
  const chip = document.createElement('div');
  chip.className = 'confirm-chip';
  chip.textContent = text;
  // Append to last bot bubble if provided, else as its own message
  if (parentBubble) {
    parentBubble.appendChild(chip);
  } else {
    const last = messages.lastElementChild;
    if (last && last.classList.contains('bot')) {
      last.querySelector('.bubble').appendChild(chip);
    } else {
      appendSystemMsg(text);
    }
  }
  scrollToBottom();
}

function scrollToBottom() {
  messages.scrollTop = messages.scrollHeight;
}

// ── 5. Chat pipeline ──────────────────────────────────────────
let busy = false;

async function sendMessage(text) {
  if (!text.trim() || busy) return;
  busy = true;
  sendBtn.disabled = true;
  messageInput.value = '';
  adjustTextareaHeight();

  appendMessage('user', text);
  const { bubble, textSpan, removeDots } = appendBotBubble();
  let streamedText = '';

  setStatus('Thinking…', true);

  try {
    await postAndStream('/api/chat', { text }, {
      onToken(tok) {
        removeDots();
        streamedText += tok;
        textSpan.textContent = streamedText;
        scrollToBottom();
      },
      onEvent(data) {
        handleStreamEvent(data, bubble, textSpan,
          () => { streamedText = ''; textSpan.textContent = ''; });
      },
    });
  } catch (e) {
    removeDots();
    textSpan.textContent = `Error: ${e.message}`;
  } finally {
    busy = false;
    sendBtn.disabled = false;
    setStatus('');
    refreshPresence().catch(() => {});
  }
}

/** Common event handler for all SSE streams. */
function handleStreamEvent(data, bubble, textSpan, onClear) {
  switch (data.type) {
    case 'clear':
      if (onClear) onClear();
      break;
    case 'status':
      setStatus(data.content, true);
      break;
    case 'transcription':
      appendMessage('user', data.content);
      break;
    case 'reminder_set':
      appendConfirmChip(`✓ ${data.content}`, bubble);
      break;
    case 'event_set':
      appendConfirmChip(`✓ ${data.content}`, bubble);
      break;
    case 'todo_added':
      appendConfirmChip(`✓ ${data.content}`, bubble);
      break;
    case 'note_saved':
      appendConfirmChip(`✓ ${data.content}`, bubble);
      break;
    case 'photo': {
      const img = document.createElement('img');
      img.src = data.src;
      img.alt = data.caption || 'photo';
      if (bubble) bubble.appendChild(img);
      scrollToBottom();
      break;
    }
    case 'done':
      setStatus('');
      break;
    case 'error':
      if (textSpan) textSpan.textContent = `Error: ${data.content}`;
      break;
  }
}

// ── 6. Voice recording ────────────────────────────────────────
let mediaRecorder = null;
let audioChunks = [];
let isRecording = false;

async function startRecording() {
  if (isRecording || busy) return;
  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    audioChunks = [];
    mediaRecorder = new MediaRecorder(stream);
    mediaRecorder.ondataavailable = (e) => { if (e.data.size > 0) audioChunks.push(e.data); };
    mediaRecorder.onstop = sendVoice;
    mediaRecorder.start();
    isRecording = true;
    micBtn.classList.add('recording');
  } catch (e) {
    appendSystemMsg('Microphone access denied.');
  }
}

function stopRecording() {
  if (!isRecording || !mediaRecorder) return;
  isRecording = false;
  micBtn.classList.remove('recording');
  mediaRecorder.stop();
  mediaRecorder.stream.getTracks().forEach((t) => t.stop());
}

async function sendVoice() {
  if (!audioChunks.length) return;
  busy = true;
  sendBtn.disabled = true;

  const blob = new Blob(audioChunks, { type: 'audio/webm' });
  const formData = new FormData();
  formData.append('audio', blob, 'recording.webm');

  const { bubble, textSpan, removeDots } = appendBotBubble();
  let streamedText = '';
  setStatus('Transcribing…', true);

  try {
    await postAndStream('/api/upload/voice', formData, {
      onToken(tok) {
        removeDots();
        streamedText += tok;
        textSpan.textContent = streamedText;
        scrollToBottom();
      },
      onEvent(data) {
        if (data.type === 'transcription') removeDots();
        handleStreamEvent(data, bubble, textSpan,
          () => { streamedText = ''; textSpan.textContent = ''; });
      },
    });
  } catch (e) {
    removeDots();
    textSpan.textContent = `Error: ${e.message}`;
  } finally {
    busy = false;
    sendBtn.disabled = false;
    setStatus('');
    refreshPresence().catch(() => {});
  }
}

// Use pointer events so hold works on touch and mouse
micBtn.addEventListener('pointerdown', (e) => { e.preventDefault(); startRecording(); });
micBtn.addEventListener('pointerup',   stopRecording);
micBtn.addEventListener('pointercancel', stopRecording);

// ── 7. Image upload ───────────────────────────────────────────
attachBtn.addEventListener('click', () => fileInput.click());

fileInput.addEventListener('change', async () => {
  const file = fileInput.files[0];
  if (!file) return;
  fileInput.value = '';
  if (busy) return;
  busy = true;
  sendBtn.disabled = true;

  const caption = messageInput.value.trim();
  messageInput.value = '';
  adjustTextareaHeight();

  const formData = new FormData();
  formData.append('image', file);
  if (caption) formData.append('caption', caption);

  if (caption) appendMessage('user', caption);
  appendSystemMsg('Looking at your image…');
  const { bubble, textSpan, removeDots } = appendBotBubble();
  let streamedText = '';
  setStatus('Processing image…', true);

  try {
    await postAndStream('/api/upload/image', formData, {
      onToken(tok) {
        removeDots();
        streamedText += tok;
        textSpan.textContent = streamedText;
        scrollToBottom();
      },
      onEvent(data) {
        handleStreamEvent(data, bubble, textSpan,
          () => { streamedText = ''; textSpan.textContent = ''; });
      },
    });
  } catch (e) {
    removeDots();
    textSpan.textContent = `Error: ${e.message}`;
  } finally {
    busy = false;
    sendBtn.disabled = false;
    setStatus('');
    refreshPresence().catch(() => {});
  }
});

// ── 8. Sidebar ────────────────────────────────────────────────
function openSidebar() {
  sidebarNav.classList.add('open');
  overlay.classList.add('visible');
}

function closeSidebarFn() {
  sidebarNav.classList.remove('open');
  overlay.classList.remove('visible');
}

menuBtn.addEventListener('click', openSidebar);
closeSidebar.addEventListener('click', closeSidebarFn);
overlay.addEventListener('click', closeSidebarFn);

function showInfoPanel(title, content) {
  infoTitle.textContent = title;
  infoContent.textContent = content;
  infoPanel.classList.add('visible');
}

infoClose.addEventListener('click', () => infoPanel.classList.remove('visible'));

async function apiFetch(path) {
  const r = await fetch(path, { headers: { 'X-Session-Token': sessionToken } });
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return r.json();
}

function truncateLine(text, fallback) {
  const clean = (text || '').replace(/\s+/g, ' ').trim();
  return clean || fallback;
}

async function refreshPresence() {
  const status = await apiFetch('/api/status');
  presenceSession.textContent = truncateLine(status.session, 'unknown');
  presenceNext.textContent = status.events.length
    ? truncateLine(`${new Date(status.events[0].start_at).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })} ${status.events[0].title}`, 'No upcoming events')
    : 'No upcoming events';

  const highTodo = status.todos.find((t) => t.priority === 'high') || status.todos[0];
  const fallbackFocus = truncateLine(
    (status.briefing_preview || '').split('\n').find((line) => line.startsWith('Prep ') || line.startsWith('Take ') || line.startsWith('Handle ')),
    'No active tasks'
  );
  presenceFocus.textContent = highTodo
    ? truncateLine(highTodo.content, 'No active tasks')
    : fallbackFocus;

  if (status.dory && status.dory.enabled) {
    const g = status.dory.graph || {};
    presenceDory.textContent = `${g.nodes || 0} nodes / ${g.core_nodes || 0} core`;
  } else {
    presenceDory.textContent = truncateLine(status.dory?.reason, 'offline');
  }
}

document.getElementById('btn-new').addEventListener('click', async () => {
  closeSidebarFn();
  try {
    await fetch('/api/new', { method: 'POST', headers: { 'X-Session-Token': sessionToken } });
    appendSystemMsg('— new conversation —');
  } catch (e) {
    appendSystemMsg(`Error: ${e.message}`);
  }
});

document.getElementById('btn-schedule').addEventListener('click', async () => {
  closeSidebarFn();
  try {
    const events = await apiFetch('/api/schedule');
    if (!events.length) { showInfoPanel('Schedule', 'No upcoming events.'); return; }
    const lines = events.map((ev) => {
      const start = new Date(ev.start_at).toLocaleString([], { weekday: 'short', month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
      return `• ${start}  ${ev.title}`;
    });
    showInfoPanel('Schedule', lines.join('\n'));
  } catch (e) {
    showInfoPanel('Schedule', `Error: ${e.message}`);
  }
});

document.getElementById('btn-todos').addEventListener('click', async () => {
  closeSidebarFn();
  try {
    const todos = await apiFetch('/api/todos');
    if (!todos.length) { showInfoPanel('Todos', 'No pending todos.'); return; }
    const pmap = { high: 'H', medium: 'M', low: 'L' };
    const lines = todos.map((t) => `[${pmap[t.priority] || 'M'}] ${t.content}`);
    showInfoPanel('Todos', lines.join('\n'));
  } catch (e) {
    showInfoPanel('Todos', `Error: ${e.message}`);
  }
});

document.getElementById('btn-notes').addEventListener('click', async () => {
  closeSidebarFn();
  try {
    const notes = await apiFetch('/api/notes');
    if (!notes.length) { showInfoPanel('Notes', 'No recent notes.'); return; }
    showInfoPanel('Notes', notes.map((n) => n.content).join('\n\n'));
  } catch (e) {
    showInfoPanel('Notes', `Error: ${e.message}`);
  }
});

document.getElementById('btn-usage').addEventListener('click', async () => {
  closeSidebarFn();
  try {
    const u = await apiFetch('/api/usage');
    showInfoPanel('Search Usage', `Used: ${u.used} / ${u.limit}\nRemaining: ${u.remaining}`);
  } catch (e) {
    showInfoPanel('Search Usage', `Error: ${e.message}`);
  }
});

document.getElementById('btn-presence').addEventListener('click', async () => {
  closeSidebarFn();
  try {
    const status = await apiFetch('/api/status');
    const memories = (status.dory?.top_memories || []).map((m) => `• ${m.content}`);
    const lines = [
      `Session: ${status.session}`,
      `Dory: ${status.dory?.enabled ? 'enabled' : `offline (${status.dory?.reason || 'unknown'})`}`,
      '',
      'Briefing preview:',
      status.briefing_preview || 'No briefing available.',
    ];
    if (memories.length) {
      lines.push('', 'Top Dory memories:', ...memories);
    }
    showInfoPanel('Presence', lines.join('\n'));
  } catch (e) {
    showInfoPanel('Presence', `Error: ${e.message}`);
  }
});

document.getElementById('btn-memory').addEventListener('click', async () => {
  closeSidebarFn();
  try {
    const [active, archived] = await Promise.all([
      apiFetch('/api/dory/memories?limit=20&zone=active'),
      apiFetch('/api/dory/memories?limit=10&zone=archived'),
    ]);

    if (!active.enabled) {
      showInfoPanel('Memory Inspector', `Dory unavailable: ${active.reason || 'unknown error'}`);
      return;
    }

    const formatItem = (item) => {
      const flags = [
        item.is_core ? 'core' : '',
        item.type,
        item.zone,
        `salience ${item.salience}`,
      ].filter(Boolean).join(' · ');
      return `• ${item.content}\n  ${flags}`;
    };

    const lines = [
      `Active memories shown: ${active.items.length} / ${active.total}`,
      '',
      'Top active memories:',
      ...(active.items.length ? active.items.map(formatItem) : ['No active memories.']),
    ];

    if (archived.items && archived.items.length) {
      lines.push('', 'Archived memories:', ...archived.items.slice(0, 5).map(formatItem));
    }

    showInfoPanel('Memory Inspector', lines.join('\n'));
  } catch (e) {
    showInfoPanel('Memory Inspector', `Error: ${e.message}`);
  }
});

document.getElementById('btn-briefing').addEventListener('click', async () => {
  closeSidebarFn();
  if (busy) return;
  busy = true;
  sendBtn.disabled = true;

  const { bubble, textSpan, removeDots } = appendBotBubble();
  let streamedText = '';
  setStatus('Assembling briefing…', true);

  try {
    await postAndStream('/api/briefing', null, {
      method: 'GET',
      onToken(tok) {
        removeDots();
        streamedText += tok;
        textSpan.textContent = streamedText;
        scrollToBottom();
      },
      onEvent(data) {
        handleStreamEvent(data, bubble, textSpan, null);
      },
    });
  } catch (e) {
    removeDots();
    textSpan.textContent = `Error: ${e.message}`;
  } finally {
    busy = false;
    sendBtn.disabled = false;
    setStatus('');
    refreshPresence().catch(() => {});
  }
});

// ── 9. Web Push setup ─────────────────────────────────────────
function urlBase64ToUint8Array(b64) {
  const padding = '='.repeat((4 - (b64.length % 4)) % 4);
  const base64 = (b64 + padding).replace(/-/g, '+').replace(/_/g, '/');
  const raw = atob(base64);
  const arr = new Uint8Array(raw.length);
  for (let i = 0; i < raw.length; i++) arr[i] = raw.charCodeAt(i);
  return arr;
}

document.getElementById('btn-push').addEventListener('click', async () => {
  closeSidebarFn();

  if (!('serviceWorker' in navigator) || !('PushManager' in window)) {
    appendSystemMsg('Web Push is not supported in this browser. Use Safari on iOS 16.4+ and install as a home screen app.');
    return;
  }

  try {
    const perm = await Notification.requestPermission();
    if (perm !== 'granted') {
      appendSystemMsg('Notification permission denied.');
      return;
    }

    const reg = await navigator.serviceWorker.ready;
    const keyResp = await fetch('/api/push/vapid-public-key', {
      headers: { 'X-Session-Token': sessionToken },
    });
    const { public_key } = await keyResp.json();
    if (!public_key) {
      appendSystemMsg('VAPID keys not configured on server. Run: python -m companion.web_push --generate-keys');
      return;
    }

    const subscription = await reg.pushManager.subscribe({
      userVisibleOnly: true,
      applicationServerKey: urlBase64ToUint8Array(public_key),
    });

    await fetch('/api/push/subscribe', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-Session-Token': sessionToken },
      body: JSON.stringify(subscription.toJSON()),
    });

    appendSystemMsg('Notifications enabled. You will receive alerts for reminders, events, and briefings.');
  } catch (e) {
    appendSystemMsg(`Push setup failed: ${e.message}`);
  }
});

// ── 10. UI helpers ────────────────────────────────────────────
function setStatus(text, active) {
  statusText.textContent = text || '';
  statusDot.className = text ? (active ? 'busy' : 'active') : '';
}

function adjustTextareaHeight() {
  messageInput.style.height = 'auto';
  messageInput.style.height = Math.min(messageInput.scrollHeight, 120) + 'px';
}

messageInput.addEventListener('input', adjustTextareaHeight);

// Enter to send; Shift+Enter for newline
messageInput.addEventListener('keydown', (e) => {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault();
    sendMessage(messageInput.value);
  }
});

sendBtn.addEventListener('click', () => sendMessage(messageInput.value));

// ── Init ──────────────────────────────────────────────────────
setInterval(() => {
  if (sessionToken) refreshPresence().catch(() => {});
}, 60000);

checkAuth();
