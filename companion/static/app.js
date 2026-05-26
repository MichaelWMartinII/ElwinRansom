'use strict';

// ── DOM refs ─────────────────────────────────────────────────
const authScreen    = document.getElementById('auth-screen');
const authForm      = document.getElementById('auth-form');
const passwordInput = document.getElementById('password-input');
const authError     = document.getElementById('auth-error');
const app           = document.getElementById('app');
const messages      = document.getElementById('messages');
const messageInput  = document.getElementById('message-input');
const sendBtn       = document.getElementById('send-btn');
const micBtn        = document.getElementById('mic-btn');
const attachBtn     = document.getElementById('attach-btn');
const fileInput     = document.getElementById('file-input');
const menuBtn       = document.getElementById('menu-btn');
const closeSidebar  = document.getElementById('close-sidebar-btn');
const sidebarNav    = document.getElementById('sidebar-nav');
const overlay       = document.getElementById('sidebar-overlay');
const statusDot     = document.getElementById('status-dot');
const statusText    = document.getElementById('status-text');
const infoPanel     = document.getElementById('info-panel');
const infoTitle     = document.getElementById('info-title');
const infoContent   = document.getElementById('info-content');
const infoClose     = document.getElementById('info-close');
const ttsBtn        = document.getElementById('tts-btn');
const ttsIconOn     = document.getElementById('tts-icon-on');
const ttsIconOff    = document.getElementById('tts-icon-off');
const sendIcon      = document.getElementById('send-icon');
const stopIcon      = document.getElementById('stop-icon');
const agentBtn      = document.getElementById('agent-btn');

// ── 1. Auth ──────────────────────────────────────────────────
let sessionToken = localStorage.getItem('elwin_token') || '';

async function checkAuth() {
  if (!sessionToken) { showAuth(); return; }
  try {
    const r = await fetch('/api/me', { headers: { 'X-Session-Token': sessionToken } });
    if (r.ok) { showApp(); } else { clearToken(); showAuth(); }
  } catch { showAuth(); }
}

function clearToken() {
  localStorage.removeItem('elwin_token');
  sessionToken = '';
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
  try {
    const r = await fetch('/api/auth', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ password: passwordInput.value }),
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
  navigator.serviceWorker.register('/static/sw.js', { scope: '/' }).catch(() => {});
}

// ── 3. TTS ───────────────────────────────────────────────────
let ttsEnabled = localStorage.getItem('elwin_tts') === 'true';

function updateTtsButton() {
  ttsBtn.classList.toggle('active', ttsEnabled);
  ttsIconOn.style.display  = ttsEnabled ? '' : 'none';
  ttsIconOff.style.display = ttsEnabled ? 'none' : '';
}

updateTtsButton();

ttsBtn.addEventListener('click', () => {
  ttsEnabled = !ttsEnabled;
  localStorage.setItem('elwin_tts', ttsEnabled);
  updateTtsButton();
  if (!ttsEnabled) window.speechSynthesis?.cancel();
});

function speak(text) {
  if (!ttsEnabled || !window.speechSynthesis || !text.trim()) return;
  window.speechSynthesis.cancel();
  const utt = new SpeechSynthesisUtterance(text.trim());
  utt.rate = 1.0;
  utt.pitch = 1.0;
  // Pick a natural English voice if available
  const voices = window.speechSynthesis.getVoices();
  const voice = voices.find(v => v.lang.startsWith('en') && /male|daniel|alex|fred/i.test(v.name))
    || voices.find(v => v.lang === 'en-US')
    || voices.find(v => v.lang.startsWith('en'))
    || null;
  if (voice) utt.voice = voice;
  window.speechSynthesis.speak(utt);
}

// ── 3b. Agent mode toggle ─────────────────────────────────────
let agentMode = localStorage.getItem('elwin_agent') === 'true';

function updateAgentButton() {
  agentBtn.classList.toggle('active', agentMode);
  agentBtn.setAttribute('title', agentMode
    ? 'Agent mode ON — Elwin can run commands and edit files'
    : 'Agent mode OFF — click to enable');
  messageInput.placeholder = agentMode
    ? 'Tell Elwin to do something…'
    : 'Message Elwin…';
}

updateAgentButton();

agentBtn.addEventListener('click', () => {
  agentMode = !agentMode;
  localStorage.setItem('elwin_agent', agentMode);
  updateAgentButton();
  if (agentMode) {
    appendSystemMsg('Agent mode on — Elwin can run shell commands, read and edit files.');
  } else {
    appendSystemMsg('Agent mode off.');
  }
});

// ── 4. SSE streaming helper ───────────────────────────────────
let _currentAbort = null;

async function postAndStream(url, body, { onToken, onEvent, method = 'POST' } = {}) {
  if (_currentAbort) _currentAbort.abort();
  const ctrl = new AbortController();
  _currentAbort = ctrl;

  const headers = { 'X-Session-Token': sessionToken };
  let fetchBody;

  if (body instanceof FormData) {
    fetchBody = body;
  } else if (body !== null && body !== undefined) {
    headers['Content-Type'] = 'application/json';
    fetchBody = JSON.stringify(body);
  }

  let resp;
  try {
    resp = await fetch(url, { method, headers, body: fetchBody, signal: ctrl.signal });
  } catch (e) {
    if (e.name === 'AbortError') return;
    throw e;
  }

  if (!resp.ok) {
    if (resp.status === 401) { clearToken(); showAuth(); }
    throw new Error(`HTTP ${resp.status}`);
  }

  const reader = resp.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';

  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split('\n');
      buffer = lines.pop();
      for (const line of lines) {
        if (!line.startsWith('data: ')) continue;
        try {
          const data = JSON.parse(line.slice(6));
          if (data.type === 'token' && onToken) onToken(data.content);
          else if (onEvent) onEvent(data);
        } catch { /* skip malformed */ }
      }
    }
  } catch (e) {
    if (e.name !== 'AbortError') throw e;
  } finally {
    _currentAbort = null;
  }
}

// ── 5. Message rendering ──────────────────────────────────────
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

  const label = document.createElement('div');
  label.className = 'bot-label';
  label.textContent = 'Elwin';
  div.appendChild(label);

  const bubble = document.createElement('div');
  bubble.className = 'bubble';

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

  const textSpan = document.createElement('span');
  textSpan.className = 'bubble-text';
  bubble.appendChild(textSpan);

  return { div, bubble, textSpan, removeDots };
}

function appendConfirmChip(text, parentBubble) {
  const chip = document.createElement('div');
  chip.className = 'confirm-chip';
  chip.textContent = text;
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

// ── 6. Chat pipeline ──────────────────────────────────────────
let busy = false;

function setBusy(val) {
  busy = val;
  if (val) {
    sendBtn.disabled = false;
    sendBtn.classList.add('stop');
    sendBtn.setAttribute('aria-label', 'Stop');
    sendIcon.style.display = 'none';
    stopIcon.style.display = '';
  } else {
    sendBtn.classList.remove('stop');
    sendBtn.setAttribute('aria-label', 'Send');
    sendIcon.style.display = '';
    stopIcon.style.display = 'none';
    sendBtn.disabled = !messageInput.value.trim();
  }
}

function escHtml(s) {
  return String(s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

function inline(text) {
  let out = escHtml(text);
  out = out.replace(/`([^`\n]+)`/g, (_, c) => `<code>${c}</code>`);
  out = out.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
  out = out.replace(/\*(.+?)\*/g, '<em>$1</em>');
  out = out.replace(/\[([^\]]+)\]\((https?:\/\/[^\)]+)\)/g,
    (_, t, u) => `<a href="${u}" target="_blank" rel="noopener">${t}</a>`);
  return out;
}

function renderMarkdown(raw) {
  const codeBlocks = [];
  let text = raw.replace(/```(\w*)\n?([\s\S]*?)```/g, (_, lang, code) => {
    const idx = codeBlocks.length;
    codeBlocks.push(`<pre><code class="lang-${escHtml(lang || 'text')}">${escHtml(code.trimEnd())}</code></pre>`);
    return `\x00CODE${idx}\x00`;
  });

  const lines = text.split('\n');
  const out = [];
  let listType = null;

  function flushList() {
    if (listType) { out.push(`</${listType}>`); listType = null; }
  }

  for (const line of lines) {
    const codePlaceholder = line.trim().match(/^\x00CODE(\d+)\x00$/);
    if (codePlaceholder) { flushList(); out.push(codeBlocks[+codePlaceholder[1]]); continue; }

    const h = line.match(/^(#{1,3}) (.+)/);
    if (h) { flushList(); const lv = h[1].length; out.push(`<h${lv}>${inline(h[2])}</h${lv}>`); continue; }

    if (/^---+$/.test(line.trim())) { flushList(); out.push('<hr>'); continue; }

    const bq = line.match(/^> (.*)/);
    if (bq) { flushList(); out.push(`<blockquote>${inline(bq[1])}</blockquote>`); continue; }

    const ul = line.match(/^[-*+] (.+)/);
    if (ul) {
      if (listType !== 'ul') { flushList(); out.push('<ul>'); listType = 'ul'; }
      out.push(`<li>${inline(ul[1])}</li>`); continue;
    }

    const ol = line.match(/^\d+\. (.+)/);
    if (ol) {
      if (listType !== 'ol') { flushList(); out.push('<ol>'); listType = 'ol'; }
      out.push(`<li>${inline(ol[1])}</li>`); continue;
    }

    if (!line.trim()) { flushList(); continue; }

    flushList();
    out.push(`<p>${inline(line)}</p>`);
  }

  flushList();
  return out.join('\n');
}

function finalizeResponse(msgDiv, textSpan, rawText) {
  const bubble = textSpan.parentElement;
  textSpan.remove();
  if (!rawText.trim()) {
    bubble.querySelector('.typing-dots')?.remove();
    bubble.innerHTML = '<span style="color:var(--text-dim);font-size:0.85em;font-style:italic;">stopped</span>';
    return;
  }
  bubble.innerHTML = renderMarkdown(rawText);
  bubble.classList.add('rendered');

  const COPY_SVG = `<svg width="13" height="13" viewBox="0 0 14 14" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><rect x="4" y="4" width="8" height="8" rx="1"/><path d="M2 10V2h8"/></svg>`;
  const copyBtn = document.createElement('button');
  copyBtn.className = 'copy-btn';
  copyBtn.innerHTML = `${COPY_SVG} Copy`;
  let copyTimer = null;
  copyBtn.addEventListener('click', () => {
    navigator.clipboard.writeText(rawText).then(() => {
      clearTimeout(copyTimer);
      copyBtn.classList.add('copied');
      copyBtn.textContent = 'Copied';
      copyTimer = setTimeout(() => { copyBtn.classList.remove('copied'); copyBtn.innerHTML = `${COPY_SVG} Copy`; }, 1800);
    });
  });
  msgDiv.appendChild(copyBtn);
}

async function sendMessage(text) {
  if (!text.trim() || busy) return;
  setBusy(true);
  messageInput.value = '';
  adjustTextareaHeight();

  appendMessage('user', text);
  const { div, bubble, textSpan, removeDots } = appendBotBubble();
  let streamedText = '';

  setStatus('Thinking…', true);

  try {
    await postAndStream('/api/chat', { text, agent_mode: agentMode }, {
      onToken(tok) {
        removeDots();
        streamedText += tok;
        textSpan.textContent = streamedText;
        scrollToBottom();
      },
      onEvent(data) {
        handleStreamEvent(data, bubble, textSpan, () => { streamedText = ''; textSpan.textContent = ''; });
      },
    });
    speak(streamedText);
    finalizeResponse(div, textSpan, streamedText);
  } catch (e) {
    removeDots();
    textSpan.textContent = `Error: ${e.message}`;
  } finally {
    setBusy(false);
    setStatus('');
    refreshPresence().catch(() => {});
  }
}

function appendToolChip(name, content, bubble) {
  const chip = document.createElement('div');
  chip.className = 'tool-chip';
  const label = document.createElement('span');
  label.className = 'tool-chip-name';
  label.textContent = name;
  chip.appendChild(label);
  if (content) {
    const body = document.createElement('span');
    body.className = 'tool-chip-content';
    body.textContent = content;
    chip.appendChild(body);
  }
  if (bubble) bubble.appendChild(chip);
  scrollToBottom();
}

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
    case 'tool_call': {
      const summary = data.params?.command
        ? `$ ${data.params.command}`.slice(0, 80)
        : data.params?.path || Object.values(data.params || {}).join(' ').slice(0, 60) || '';
      appendToolChip(`⚙ ${data.name}`, summary, bubble);
      setStatus(`Running ${data.name}…`, true);
      break;
    }
    case 'tool_result':
      setStatus('', false);
      break;
    case 'reminder_set':
    case 'event_set':
    case 'todo_added':
    case 'note_saved':
      appendConfirmChip(data.content, bubble);
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

// ── 7. Voice recording (tap to start, tap to stop) ────────────
let mediaRecorder = null;
let audioChunks = [];
let isRecording = false;

async function startRecording() {
  if (busy) return;
  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    audioChunks = [];
    mediaRecorder = new MediaRecorder(stream);
    mediaRecorder.ondataavailable = (e) => { if (e.data.size > 0) audioChunks.push(e.data); };
    mediaRecorder.onstop = sendVoice;
    mediaRecorder.start();
    isRecording = true;
    micBtn.classList.add('recording');
    setStatus('Recording…', true);
  } catch {
    appendSystemMsg('Microphone access denied.');
  }
}

function stopRecording() {
  if (!isRecording || !mediaRecorder) return;
  isRecording = false;
  micBtn.classList.remove('recording');
  setStatus('');
  mediaRecorder.stop();
  mediaRecorder.stream.getTracks().forEach(t => t.stop());
}

micBtn.addEventListener('click', () => {
  if (isRecording) {
    stopRecording();
  } else {
    startRecording();
  }
});

async function sendVoice() {
  if (!audioChunks.length) return;
  setBusy(true);

  const blob = new Blob(audioChunks, { type: 'audio/webm' });
  const formData = new FormData();
  formData.append('audio', blob, 'recording.webm');

  const { div, bubble, textSpan, removeDots } = appendBotBubble();
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
        handleStreamEvent(data, bubble, textSpan, () => { streamedText = ''; textSpan.textContent = ''; });
      },
    });
    speak(streamedText);
    finalizeResponse(div, textSpan, streamedText);
  } catch (e) {
    removeDots();
    textSpan.textContent = `Error: ${e.message}`;
  } finally {
    setBusy(false);
    setStatus('');
    refreshPresence().catch(() => {});
  }
}

// ── 8. File upload (image + documents) ───────────────────────
attachBtn.addEventListener('click', () => fileInput.click());

fileInput.addEventListener('change', async () => {
  const file = fileInput.files[0];
  if (!file) return;
  fileInput.value = '';
  if (busy) return;

  const isDoc = file.type === 'application/pdf'
    || file.type.startsWith('text/')
    || /\.(pdf|txt|csv|md)$/i.test(file.name);

  const caption = messageInput.value.trim();
  messageInput.value = '';
  adjustTextareaHeight();

  setBusy(true);

  if (isDoc) {
    const formData = new FormData();
    formData.append('file', file);
    if (caption) formData.append('caption', caption);

    if (caption) appendMessage('user', caption);
    appendSystemMsg(`Reading ${file.name}…`);
    const { div, bubble, textSpan, removeDots } = appendBotBubble();
    let streamedText = '';
    setStatus('Processing document…', true);

    try {
      await postAndStream('/api/upload/file', formData, {
        onToken(tok) {
          removeDots();
          streamedText += tok;
          textSpan.textContent = streamedText;
          scrollToBottom();
        },
        onEvent(data) {
          handleStreamEvent(data, bubble, textSpan, () => { streamedText = ''; textSpan.textContent = ''; });
        },
      });
      speak(streamedText);
      finalizeResponse(div, textSpan, streamedText);
    } catch (e) {
      removeDots();
      textSpan.textContent = `Error: ${e.message}`;
    } finally {
      setBusy(false);
      setStatus('');
      refreshPresence().catch(() => {});
    }
    return;
  }

  // Image flow
  const formData = new FormData();
  formData.append('image', file);
  if (caption) formData.append('caption', caption);

  if (caption) appendMessage('user', caption);
  appendSystemMsg('Looking at your image…');
  const { div, bubble, textSpan, removeDots } = appendBotBubble();
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
        handleStreamEvent(data, bubble, textSpan, () => { streamedText = ''; textSpan.textContent = ''; });
      },
    });
    speak(streamedText);
    finalizeResponse(div, textSpan, streamedText);
  } catch (e) {
    removeDots();
    textSpan.textContent = `Error: ${e.message}`;
  } finally {
    setBusy(false);
    setStatus('');
    refreshPresence().catch(() => {});
  }
});

// ── 9. Sidebar ────────────────────────────────────────────────
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

  const highTodo = status.todos.find(t => t.priority === 'high') || status.todos[0];
  const nextEvent = status.events[0];

  // Update status dot if everything is quiet
  if (!busy) setStatus('');
}

document.getElementById('btn-new').addEventListener('click', async () => {
  closeSidebarFn();
  try {
    await fetch('/api/new', { method: 'POST', headers: { 'X-Session-Token': sessionToken } });
    appendSystemMsg('New conversation started.');
  } catch (e) {
    appendSystemMsg(`Error: ${e.message}`);
  }
});

document.getElementById('btn-schedule').addEventListener('click', async () => {
  closeSidebarFn();
  try {
    const events = await apiFetch('/api/schedule');
    if (!events.length) { showInfoPanel('Schedule', 'No upcoming events.'); return; }
    const lines = events.map(ev => {
      const start = new Date(ev.start_at).toLocaleString([], { weekday: 'short', month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
      return `${start}  ${ev.title}`;
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
    const pmap = { high: 'High', medium: 'Med', low: 'Low' };
    const lines = todos.map(t => `[${pmap[t.priority] || 'Med'}]  ${t.content}`);
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
    showInfoPanel('Notes', notes.map(n => n.content).join('\n\n'));
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
    const memories = (status.dory?.top_memories || []).map(m => `  ${m.content}`);
    const lines = [
      `Session: ${status.session}`,
      `Memory: ${status.dory?.enabled ? `${status.dory.graph?.nodes || 0} nodes, ${status.dory.graph?.core_nodes || 0} core` : `offline — ${status.dory?.reason || 'unknown'}`}`,
      '',
      'Briefing preview:',
      status.briefing_preview || 'No briefing available.',
    ];
    if (memories.length) lines.push('', 'Top memories:', ...memories);
    showInfoPanel('Status', lines.join('\n'));
  } catch (e) {
    showInfoPanel('Status', `Error: ${e.message}`);
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
      showInfoPanel('Memory Inspector', `Dory unavailable: ${active.reason || 'unknown'}`);
      return;
    }

    const fmt = item => {
      const flags = [item.is_core ? 'core' : '', item.type, `salience ${item.salience}`].filter(Boolean).join(' · ');
      return `${item.content}\n  ${flags}`;
    };

    const lines = [
      `Active memories: ${active.items.length} / ${active.total}`,
      '',
      ...( active.items.length ? active.items.map(fmt) : ['No active memories.']),
    ];

    if (archived.items?.length) {
      lines.push('', 'Archived:', ...archived.items.slice(0, 5).map(fmt));
    }

    showInfoPanel('Memory Inspector', lines.join('\n'));
  } catch (e) {
    showInfoPanel('Memory Inspector', `Error: ${e.message}`);
  }
});

document.getElementById('btn-briefing').addEventListener('click', async () => {
  closeSidebarFn();
  if (busy) return;
  setBusy(true);

  const { div, bubble, textSpan, removeDots } = appendBotBubble();
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
    speak(streamedText);
    finalizeResponse(div, textSpan, streamedText);
  } catch (e) {
    removeDots();
    textSpan.textContent = `Error: ${e.message}`;
  } finally {
    setBusy(false);
    setStatus('');
    refreshPresence().catch(() => {});
  }
});

document.getElementById('btn-conversations').addEventListener('click', async () => {
  closeSidebarFn();
  try {
    const convos = await apiFetch('/api/conversations');
    if (!convos.length) { showInfoPanel('Conversations', 'No conversations yet.'); return; }
    const lines = convos.map(c => {
      const time = new Date(c.last_at).toLocaleDateString([], { month: 'short', day: 'numeric', year: 'numeric' });
      const preview = (c.preview || '(no text)').replace(/\s+/g, ' ').trim().slice(0, 70);
      return `${time}  ${preview}`;
    });
    showInfoPanel('Conversations', lines.join('\n'));
  } catch (e) {
    showInfoPanel('Conversations', `Error: ${e.message}`);
  }
});

// ── 10. Web Push ──────────────────────────────────────────────
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
    appendSystemMsg('Web Push not supported. Use Safari on iOS 16.4+ and install as a home screen app.');
    return;
  }

  try {
    const perm = await Notification.requestPermission();
    if (perm !== 'granted') { appendSystemMsg('Notification permission denied.'); return; }

    const reg = await navigator.serviceWorker.ready;
    const keyResp = await fetch('/api/push/vapid-public-key', { headers: { 'X-Session-Token': sessionToken } });
    const { public_key } = await keyResp.json();
    if (!public_key) {
      appendSystemMsg('VAPID keys not configured. Run: python -m companion.web_push --generate-keys');
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

    appendSystemMsg('Notifications enabled.');
  } catch (e) {
    appendSystemMsg(`Push setup failed: ${e.message}`);
  }
});

// ── 11. UI helpers ────────────────────────────────────────────
function setStatus(text, active) {
  statusText.textContent = text || '';
  statusDot.className = text ? (active ? 'busy' : 'active') : '';
}

function adjustTextareaHeight() {
  messageInput.style.height = 'auto';
  messageInput.style.height = Math.min(messageInput.scrollHeight, 140) + 'px';
}

messageInput.addEventListener('input', () => {
  adjustTextareaHeight();
  sendBtn.disabled = !messageInput.value.trim();
});

messageInput.addEventListener('keydown', (e) => {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault();
    sendMessage(messageInput.value);
  }
});

sendBtn.addEventListener('click', () => {
  if (busy) { if (_currentAbort) _currentAbort.abort(); return; }
  sendMessage(messageInput.value);
});

// Preload voices on page load (required by some browsers)
if (window.speechSynthesis) {
  window.speechSynthesis.getVoices();
  window.speechSynthesis.addEventListener('voiceschanged', () => {});
}

// ── Init ──────────────────────────────────────────────────────
setInterval(() => {
  if (sessionToken) refreshPresence().catch(() => {});
}, 60000);

checkAuth();
