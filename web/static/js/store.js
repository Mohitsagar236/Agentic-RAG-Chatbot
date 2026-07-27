const LEGACY_CHAT_KEY = 'rag_chat_v1';
const CHATS_KEY = 'rag_chats_v1';
const ACTIVE_CHAT_KEY = 'rag_active_chat_v1';

let chats = [];
let activeChatId = '';

function normalizeMessage(message) {
  if (!message || typeof message.text !== 'string') return null;
  return {
    role: message.role === 'user' ? 'user' : 'bot',
    text: message.text,
    sources: Array.isArray(message.sources) ? message.sources : [],
  };
}

function normalizeChat(chat) {
  if (!chat || typeof chat.id !== 'string' || !chat.id) return null;
  const messages = Array.isArray(chat.messages)
    ? chat.messages.map(normalizeMessage).filter(Boolean)
    : [];
  return {
    id: chat.id,
    title: typeof chat.title === 'string' && chat.title.trim() ? chat.title : deriveTitle(messages),
    messages,
    createdAt: Number.isFinite(chat.createdAt) ? chat.createdAt : Date.now(),
  };
}

function persist() {
  try {
    localStorage.setItem(CHATS_KEY, JSON.stringify(chats));
    localStorage.setItem(ACTIVE_CHAT_KEY, activeChatId);
  } catch {
    // The app remains usable when storage is unavailable or full.
  }
}

function createId() {
  if (globalThis.crypto?.randomUUID) return `chat_${crypto.randomUUID()}`;
  return `chat_${Date.now()}_${Math.random().toString(36).slice(2, 10)}`;
}

function formatTitle(text) {
  const clean = String(text || '').trim().replace(/\s+/g, ' ');
  return clean.length <= 40 ? clean : `${clean.slice(0, 37)}...`;
}

function deriveTitle(messages) {
  const firstUser = messages.find((message) => message.role === 'user');
  return formatTitle(firstUser?.text) || 'New chat';
}

export function createChat(seedMessages = []) {
  const messages = seedMessages.map(normalizeMessage).filter(Boolean);
  return {
    id: createId(),
    title: deriveTitle(messages),
    messages,
    createdAt: Date.now(),
  };
}

export function initializeStore() {
  try {
    const saved = JSON.parse(localStorage.getItem(CHATS_KEY) || '[]');
    if (Array.isArray(saved)) chats = saved.map(normalizeChat).filter(Boolean);
  } catch {
    chats = [];
  }

  if (chats.length === 0) {
    try {
      const legacyMessages = JSON.parse(localStorage.getItem(LEGACY_CHAT_KEY) || '[]');
      if (Array.isArray(legacyMessages) && legacyMessages.length > 0) {
        chats = [createChat(legacyMessages)];
        localStorage.removeItem(LEGACY_CHAT_KEY);
      }
    } catch {
      // Ignore corrupted legacy data.
    }
  }

  if (chats.length === 0) chats = [createChat()];
  const storedActive = localStorage.getItem(ACTIVE_CHAT_KEY);
  activeChatId = chats.some((chat) => chat.id === storedActive) ? storedActive : chats[0].id;
  persist();
}

export function getChats() {
  return chats;
}

export function getChat(chatId) {
  return chats.find((chat) => chat.id === chatId) || null;
}

export function getActiveChatId() {
  return activeChatId;
}

export function getActiveChat() {
  return getChat(activeChatId);
}

export function setActiveChat(chatId) {
  if (!getChat(chatId)) return false;
  activeChatId = chatId;
  persist();
  return true;
}

export function createAndActivateChat() {
  const chat = createChat();
  chats.unshift(chat);
  activeChatId = chat.id;
  persist();
  return chat;
}

export function appendMessage(chatId, message) {
  const chat = getChat(chatId);
  const normalized = normalizeMessage(message);
  if (!chat || !normalized) return null;
  chat.messages.push(normalized);
  if (chat.title === 'New chat' && normalized.role === 'user') {
    chat.title = formatTitle(normalized.text) || 'New chat';
  }
  persist();
  return normalized;
}

export function deleteChat(chatId) {
  const index = chats.findIndex((chat) => chat.id === chatId);
  if (index < 0) return null;
  const [removed] = chats.splice(index, 1);

  if (chats.length === 0) chats = [createChat()];
  if (activeChatId === chatId) {
    activeChatId = (chats[index] || chats[index - 1] || chats[0]).id;
  }
  persist();
  return removed;
}
