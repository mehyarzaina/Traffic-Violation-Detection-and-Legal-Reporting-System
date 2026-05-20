// ── Upload Preview ─────────────────────────────────────────────────────
const fileInput   = document.getElementById('file-input');
const previewWrap = document.getElementById('preview-wrap');
const previewImg  = document.getElementById('preview-img');
const fileNameEl  = document.getElementById('file-name');
const analyzeBtn  = document.getElementById('analyze-btn');
const uploadForm  = document.getElementById('upload-form');
const spinner     = document.getElementById('spinner');
const spinnerMsg  = document.getElementById('spinner-msg');

if (fileInput) {
  fileInput.addEventListener('change', function () {
    const file = this.files[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = e => {
      previewImg.src = e.target.result;
      previewWrap.style.display = 'block';
      fileNameEl.textContent = file.name;
      analyzeBtn.disabled = false;
    };
    reader.readAsDataURL(file);
  });
}

if (uploadForm) {
  uploadForm.addEventListener('submit', function () {
    spinner.classList.add('active');
    const msgs = [
      'Running violation models...',
      'Extracting license plate...',
      'Retrieving law articles...',
      'Generating legal report...',
      'Saving to database...'
    ];
    let i = 0;
    setInterval(() => { spinnerMsg.textContent = msgs[i++ % msgs.length]; }, 1800);
  });
}

// ── Chat helper ─────────────────────────────────────────────────────────
function addBubble(container, text, role) {
  const div = document.createElement('div');
  div.className = `chat-bubble ${role}`;
  div.textContent = text;
  container.appendChild(div);
  container.scrollTop = container.scrollHeight;
  return div;
}

async function sendChat(inputEl, messagesEl, sendBtn) {
  const question = inputEl.value.trim();
  if (!question) return;
  inputEl.value = '';
  sendBtn.disabled = true;
  addBubble(messagesEl, question, 'user');
  const loading = addBubble(messagesEl, 'Thinking...', 'bot loading');

  const context  = inputEl.dataset.context  || '';
  const plate    = inputEl.dataset.plate    || '';
  const location = inputEl.dataset.location || '';
  const fullQ    = context
    ? `[Context: violation=${context}, plate=${plate}, location=${location}] ${question}`
    : question;

  try {
    const res  = await fetch('/chatbot', { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({ question: fullQ }) });
    const data = await res.json();
    loading.className = 'chat-bubble bot';
    loading.textContent = data.answer || 'Sorry, no answer available.';
  } catch {
    loading.className = 'chat-bubble bot';
    loading.textContent = 'Connection error. Please try again.';
  }
  sendBtn.disabled = false;
  inputEl.focus();
}

// ── Inline chat (detect page) ────────────────────────────────────────────
const inlineChatInput = document.getElementById('chat-input');
const inlineChatSend  = document.getElementById('chat-send');
const inlineChatMsgs  = document.getElementById('chat-messages');

if (inlineChatSend && inlineChatInput) {
  inlineChatSend.addEventListener('click', () => sendChat(inlineChatInput, inlineChatMsgs, inlineChatSend));
  inlineChatInput.addEventListener('keydown', e => { if (e.key === 'Enter') sendChat(inlineChatInput, inlineChatMsgs, inlineChatSend); });
}

// ── Floating chatbot FAB ─────────────────────────────────────────────────
const fab       = document.getElementById('chat-fab');
const chatPanel = document.getElementById('chat-panel');
const closeBtn  = document.getElementById('chat-panel-close');
const fabInput  = document.getElementById('fab-chat-input');
const fabSend   = document.getElementById('fab-chat-send');
const fabMsgs   = document.getElementById('fab-chat-messages');

if (fab && chatPanel) {
  fab.addEventListener('click', () => chatPanel.classList.toggle('open'));
  if (closeBtn) closeBtn.addEventListener('click', () => chatPanel.classList.remove('open'));
  if (fabSend)  fabSend.addEventListener('click',  () => sendChat(fabInput, fabMsgs, fabSend));
  if (fabInput) fabInput.addEventListener('keydown', e => { if (e.key === 'Enter') sendChat(fabInput, fabMsgs, fabSend); });
}