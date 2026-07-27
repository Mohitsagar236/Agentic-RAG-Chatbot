import { api } from './api.js';
import {
  appendMessage,
  createAndActivateChat,
  deleteChat as deleteStoredChat,
  getActiveChat,
  getActiveChatId,
  getChat,
  getChats,
  setActiveChat,
} from './store.js';
import { escapeHtml, renderMarkdown } from './markdown.js';
import { copyText, showToast } from './ui.js';

const pendingRequests = new Map();
let knowledgeReady = false;

function sourceDetails(source) {
  if (source && typeof source === 'object') {
    const value = source.source || source.name || '';
    return {
      name: source.name || String(value).split(/[\\/]/).pop() || 'Document',
      source: value,
    };
  }
  const value = String(source || '');
  return {
    name: value.split(/[\\/]/).pop() || 'Document',
    source: value,
  };
}

function welcomeMarkup() {
  const suggestions = [
    ['🔎', 'What is RAG and why does it reduce hallucinations?'],
    ['🧠', 'What are the three types of machine learning?'],
    ['⚡', 'Explain the transformer attention mechanism'],
    ['📋', 'Summarize the key points from my documents'],
  ];
  return `
    <div class="welcome-logo">
      <div class="welcome-glow"></div>
      <span class="welcome-initial">R</span>
    </div>
    <h1 class="welcome-title">How can I help you?</h1>
    <p class="welcome-sub">Ask me anything about your uploaded documents.</p>
    <div class="suggestion-grid">
      ${suggestions.map(([icon, text]) => `
        <button type="button" class="suggestion-card" data-suggestion="${escapeHtml(text)}">
          <span class="suggestion-icon">${icon}</span><span>${escapeHtml(text)}</span>
        </button>`).join('')}
    </div>`;
}

function ensureWelcome() {
  let welcome = document.getElementById('welcome');
  if (welcome) return welcome;
  welcome = document.createElement('div');
  welcome.id = 'welcome';
  welcome.className = 'welcome';
  welcome.innerHTML = welcomeMarkup();
  const chatArea = document.getElementById('chat-area');
  chatArea.insertBefore(welcome, document.getElementById('thread'));
  return welcome;
}

function removeWelcome() {
  document.getElementById('welcome')?.remove();
}

export function renderChatList() {
  const list = document.getElementById('chat-list');
  list.innerHTML = getChats().map((chat) => {
    const count = chat.messages.length;
    const active = chat.id === getActiveChatId();
    return `
      <div class="chat-item${active ? ' active' : ''}" data-id="${escapeHtml(chat.id)}">
        <button type="button" class="chat-item-main chat-select-btn"
                aria-current="${active ? 'true' : 'false'}" data-id="${escapeHtml(chat.id)}">
          <span class="chat-item-title">${escapeHtml(chat.title || 'New chat')}</span>
          <span class="chat-item-meta">${count} message${count === 1 ? '' : 's'}</span>
        </button>
        <button type="button" class="chat-del-btn" data-id="${escapeHtml(chat.id)}"
                aria-label="Delete ${escapeHtml(chat.title || 'New chat')}">
          <svg aria-hidden="true" width="12" height="12" viewBox="0 0 24 24" fill="none"
               stroke="currentColor" stroke-width="2">
            <polyline points="3 6 5 6 21 6"/>
            <path d="M19 6l-1 14H6L5 6"/>
            <path d="M10 11v6M14 11v6"/>
          </svg>
        </button>
      </div>`;
  }).join('');
}

function appendUserMessageDom(text) {
  const element = document.createElement('div');
  element.className = 'msg msg-user';
  const bubble = document.createElement('div');
  bubble.className = 'bubble';
  bubble.textContent = text;
  element.appendChild(bubble);
  document.getElementById('thread').appendChild(element);
  scrollBottom();
  return element;
}

function createBotMessageDom() {
  const element = document.createElement('div');
  element.className = 'msg msg-bot';
  element.innerHTML = `
    <div class="bot-avatar" aria-hidden="true">R</div>
    <div class="bot-body">
      <div class="bot-text">
        <span class="sr-only">Generating response</span>
        <div class="dots" aria-hidden="true"><span></span><span></span><span></span></div>
      </div>
      <div class="bot-actions">
        <button type="button" class="act-btn copy-btn" aria-label="Copy response">
          <svg aria-hidden="true" width="13" height="13" viewBox="0 0 24 24" fill="none"
               stroke="currentColor" stroke-width="2">
            <rect x="9" y="9" width="13" height="13" rx="2"/>
            <path d="M5 15H4a2 2 0 01-2-2V4a2 2 0 012-2h9a2 2 0 012 2v1"/>
          </svg>
          Copy
        </button>
        <button type="button" class="act-btn speak-btn" aria-label="Read response aloud">
          <svg aria-hidden="true" width="13" height="13" viewBox="0 0 24 24" fill="none"
               stroke="currentColor" stroke-width="2">
            <polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5"/>
            <path d="M15.5 8.5a5 5 0 010 7"/>
            <path d="M19 5a10 10 0 010 14"/>
          </svg>
          Listen
        </button>
      </div>
    </div>`;
  document.getElementById('thread').appendChild(element);
  scrollBottom();
  return {
    botEl: element,
    textEl: element.querySelector('.bot-text'),
  };
}

function addSources(botElement, sources) {
  if (!Array.isArray(sources) || sources.length === 0) return;
  const current = botElement.querySelector('.msg-sources');
  current?.remove();

  const container = document.createElement('div');
  container.className = 'msg-sources';
  for (const source of sources) {
    const details = sourceDetails(source);
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'source-chip';
    button.dataset.source = details.source;
    button.setAttribute('aria-label', `Open source ${details.name}`);
    button.textContent = `📄 ${details.name}`;
    container.appendChild(button);
  }
  botElement.querySelector('.bot-body')
    .insertBefore(container, botElement.querySelector('.bot-actions'));
}

function populateBotMessage(botElement, textElement, message) {
  textElement.innerHTML = renderMarkdown(message.text);
  textElement.dataset.raw = message.text;
  addSources(botElement, message.sources);
}

export function renderActiveChat() {
  const chat = getActiveChat();
  const thread = document.getElementById('thread');
  thread.innerHTML = '';

  if (!chat || chat.messages.length === 0) {
    ensureWelcome();
  } else {
    removeWelcome();
    for (const message of chat.messages) {
      if (message.role === 'user') {
        appendUserMessageDom(message.text);
      } else {
        const elements = createBotMessageDom();
        populateBotMessage(elements.botEl, elements.textEl, message);
      }
    }
  }

  const pending = chat && pendingRequests.get(chat.id);
  if (pending) {
    removeWelcome();
    const elements = createBotMessageDom();
    pending.botEl = elements.botEl;
    pending.textEl = elements.textEl;
  }
  syncComposerState();
}

function scrollBottom() {
  const chatArea = document.getElementById('chat-area');
  chatArea.scrollTop = chatArea.scrollHeight;
}

function syncComposerState() {
  const input = document.getElementById('question-input');
  const sendButton = document.getElementById('send-btn');
  const pending = pendingRequests.has(getActiveChatId());
  document.getElementById('thread').setAttribute('aria-busy', String(pending));

  input.disabled = !knowledgeReady;
  input.placeholder = knowledgeReady
    ? 'Message RAG Assistant…'
    : 'Upload and ingest documents first…';

  sendButton.classList.toggle('cancel-request', pending);
  sendButton.setAttribute('aria-label', pending ? 'Stop response' : 'Send message');
  sendButton.title = pending ? 'Stop response' : 'Send message';
  sendButton.disabled = pending ? false : (!knowledgeReady || input.value.trim() === '');
}

export function setKnowledgeReady(enabled) {
  knowledgeReady = Boolean(enabled);
  syncComposerState();
}

export function setComposerValue(value, { focus = true } = {}) {
  const input = document.getElementById('question-input');
  input.value = String(value || '');
  resizeComposer();
  syncComposerState();
  if (focus && !input.disabled) input.focus();
}

function resizeComposer() {
  const input = document.getElementById('question-input');
  input.style.height = 'auto';
  input.style.height = `${Math.min(input.scrollHeight, 180)}px`;
}

function removePendingDom(pending) {
  if (pending?.botEl?.isConnected) pending.botEl.remove();
}

export function cancelChatRequest(chatId = getActiveChatId(), notify = true) {
  const pending = pendingRequests.get(chatId);
  if (!pending) return false;
  pending.controller.abort();
  removePendingDom(pending);
  pendingRequests.delete(chatId);
  if (chatId === getActiveChatId()) syncComposerState();
  if (notify) showToast('Response stopped.', 'info');
  return true;
}

export function cancelAllChatRequests() {
  for (const chatId of [...pendingRequests.keys()]) cancelChatRequest(chatId, false);
}

export async function sendQuestion(questionValue) {
  const question = String(questionValue ?? document.getElementById('question-input').value).trim();
  const chatId = getActiveChatId();
  if (!question || !chatId) return { ok: false, reason: 'empty' };
  if (!knowledgeReady) {
    showToast('Upload and ingest documents before asking a question.', 'error');
    return { ok: false, reason: 'no_documents' };
  }
  if (pendingRequests.has(chatId)) {
    showToast('Stop the current response before sending another message.', 'info');
    return { ok: false, reason: 'pending' };
  }

  removeWelcome();
  appendMessage(chatId, { role: 'user', text: question });
  if (chatId === getActiveChatId()) appendUserMessageDom(question);
  renderChatList();

  const input = document.getElementById('question-input');
  if (questionValue === undefined) {
    input.value = '';
    resizeComposer();
  }

  const elements = chatId === getActiveChatId()
    ? createBotMessageDom()
    : { botEl: null, textEl: null };
  const controller = new AbortController();
  const pending = { controller, ...elements };
  pendingRequests.set(chatId, pending);
  syncComposerState();

  try {
    const data = await api.chat(question, chatId, { signal: controller.signal });
    if (typeof data.answer !== 'string') throw new Error('The server response did not include an answer.');
    const message = {
      role: 'bot',
      text: data.answer,
      sources: Array.isArray(data.sources) ? data.sources : [],
    };
    appendMessage(chatId, message);
    renderChatList();

    if (chatId === getActiveChatId()) {
      if (!pending.botEl?.isConnected) {
        const replacement = createBotMessageDom();
        pending.botEl = replacement.botEl;
        pending.textEl = replacement.textEl;
      }
      populateBotMessage(pending.botEl, pending.textEl, message);
      scrollBottom();
    }
    return { ok: true, answer: data.answer, sources: message.sources, chatId };
  } catch (error) {
    if (controller.signal.aborted || error?.name === 'AbortError') {
      removePendingDom(pending);
      return { ok: false, reason: 'aborted', chatId };
    }

    const message = error?.message || 'Request failed.';
    if (pending.textEl?.isConnected) {
      pending.textEl.textContent = `Error: ${message}`;
      pending.textEl.classList.add('message-error');
      pending.textEl.setAttribute('role', 'alert');
    }
    showToast(`${getChat(chatId)?.title || 'Chat'}: ${message}`, 'error');
    return { ok: false, reason: 'error', error, chatId };
  } finally {
    const current = pendingRequests.get(chatId);
    if (current?.controller === controller) pendingRequests.delete(chatId);
    if (chatId === getActiveChatId()) syncComposerState();
  }
}

export function startNewChat() {
  createAndActivateChat();
  renderChatList();
  renderActiveChat();
  setComposerValue('', { focus: knowledgeReady });
}

function selectChat(chatId) {
  if (chatId === getActiveChatId() || !setActiveChat(chatId)) return;
  renderChatList();
  renderActiveChat();
  window.dispatchEvent(new CustomEvent('rag:chat-selected'));
}

function confirmDeleteChat(chatId) {
  const chat = getChat(chatId);
  if (!chat) return;
  if (!window.confirm(`Delete "${chat.title || 'New chat'}"? This cannot be undone.`)) return;
  cancelChatRequest(chatId, false);
  deleteStoredChat(chatId);
  renderChatList();
  renderActiveChat();
}

function handleThreadClick(event) {
  const copyButton = event.target.closest('.copy-btn');
  if (copyButton) {
    const textElement = copyButton.closest('.bot-body')?.querySelector('.bot-text');
    copyText(textElement?.dataset.raw || textElement?.textContent || '');
    return;
  }

  const codeButton = event.target.closest('.copy-code');
  if (codeButton) {
    const code = codeButton.closest('.code-block')?.querySelector('pre')?.textContent || '';
    copyText(code).then((copied) => {
      if (!copied) return;
      codeButton.textContent = 'Copied!';
      window.setTimeout(() => { codeButton.textContent = 'Copy'; }, 2_000);
    });
    return;
  }

  const speakButton = event.target.closest('.speak-btn');
  if (speakButton) {
    const textElement = speakButton.closest('.bot-body')?.querySelector('.bot-text');
    window.dispatchEvent(new CustomEvent('rag:speak', {
      detail: { text: textElement?.dataset.raw || textElement?.textContent || '' },
    }));
    return;
  }

  const sourceButton = event.target.closest('.source-chip');
  if (sourceButton) {
    window.dispatchEvent(new CustomEvent('rag:open-document', {
      detail: { source: sourceButton.dataset.source },
    }));
  }
}

export function setupChat() {
  renderChatList();
  renderActiveChat();

  const input = document.getElementById('question-input');
  input.addEventListener('input', () => {
    resizeComposer();
    syncComposerState();
  });
  input.addEventListener('keydown', (event) => {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault();
      if (!pendingRequests.has(getActiveChatId())) sendQuestion();
    }
  });

  document.getElementById('send-btn').addEventListener('click', () => {
    if (!cancelChatRequest(getActiveChatId())) sendQuestion();
  });

  document.getElementById('chat-list').addEventListener('click', (event) => {
    const deleteButton = event.target.closest('.chat-del-btn');
    if (deleteButton) {
      confirmDeleteChat(deleteButton.dataset.id);
      return;
    }
    const selectButton = event.target.closest('.chat-select-btn');
    if (selectButton) selectChat(selectButton.dataset.id);
  });

  document.getElementById('chat-area').addEventListener('click', (event) => {
    const suggestion = event.target.closest('[data-suggestion]');
    if (!suggestion) return;
    setComposerValue(suggestion.dataset.suggestion, { focus: false });
    sendQuestion();
  });
  document.getElementById('thread').addEventListener('click', handleThreadClick);
}
