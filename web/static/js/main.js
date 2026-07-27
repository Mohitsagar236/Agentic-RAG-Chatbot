import { api } from './api.js';
import {
  cancelAllChatRequests,
  setupChat,
  startNewChat,
} from './chat.js';
import {
  loadStatus,
  setupDocuments,
  toggleUploadPanel,
} from './documents.js';
import { getActiveChatId, initializeStore } from './store.js';
import { showToast } from './ui.js';
import { setupVoice } from './voice.js';

const mobileQuery = window.matchMedia('(max-width: 700px)');
let sidebarExpanded = true;

function setSidebarExpanded(expanded) {
  const sidebar = document.getElementById('sidebar');
  const main = document.getElementById('main');
  const overlay = document.getElementById('sidebar-overlay');
  const toggle = document.getElementById('sidebar-toggle-btn');
  sidebarExpanded = Boolean(expanded);

  if (mobileQuery.matches) {
    sidebar.classList.toggle('open', sidebarExpanded);
    sidebar.classList.toggle('collapsed', !sidebarExpanded);
    overlay.classList.toggle('open', sidebarExpanded);
    overlay.tabIndex = sidebarExpanded ? 0 : -1;
    main.inert = sidebarExpanded;
  } else {
    sidebar.classList.remove('open');
    overlay.classList.remove('open');
    overlay.tabIndex = -1;
    sidebar.classList.toggle('collapsed', !sidebarExpanded);
    main.inert = false;
  }
  sidebar.inert = !sidebarExpanded;
  sidebar.setAttribute('aria-hidden', String(!sidebarExpanded));
  toggle.setAttribute('aria-expanded', String(sidebarExpanded));
}

function setupSidebar() {
  setSidebarExpanded(!mobileQuery.matches);
  document.getElementById('sidebar-toggle-btn').addEventListener('click', () => {
    setSidebarExpanded(!sidebarExpanded);
    if (mobileQuery.matches && sidebarExpanded) {
      document.getElementById('sidebar-close-btn').focus();
    }
  });
  document.getElementById('sidebar-close-btn').addEventListener('click', () => {
    setSidebarExpanded(false);
    document.getElementById('sidebar-toggle-btn').focus();
  });
  document.getElementById('sidebar-overlay').addEventListener('click', () => {
    setSidebarExpanded(false);
    document.getElementById('sidebar-toggle-btn').focus();
  });
  mobileQuery.addEventListener('change', (event) => setSidebarExpanded(!event.matches));
  window.addEventListener('rag:chat-selected', () => {
    if (mobileQuery.matches) setSidebarExpanded(false);
  });
  window.addEventListener('rag:close-sidebar', () => {
    if (mobileQuery.matches) setSidebarExpanded(false);
  });
  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape' && mobileQuery.matches && sidebarExpanded) {
      setSidebarExpanded(false);
      document.getElementById('sidebar-toggle-btn').focus();
    }
  });
}

function setupPrimaryActions() {
  document.getElementById('new-chat-btn').addEventListener('click', () => {
    startNewChat();
    if (mobileQuery.matches) setSidebarExpanded(false);
  });

  document.getElementById('clear-memory-btn').addEventListener('click', async (event) => {
    const button = event.currentTarget;
    const conversationId = getActiveChatId();
    button.disabled = true;
    button.setAttribute('aria-busy', 'true');
    try {
      const data = await api.clearMemory(conversationId);
      showToast(data.message || 'This chat’s server memory was cleared.', 'info');
    } catch (error) {
      showToast(error.message, 'error');
    } finally {
      button.disabled = false;
      button.removeAttribute('aria-busy');
    }
  });

  document.getElementById('reset-btn').addEventListener('click', async (event) => {
    if (!window.confirm('Remove all documents from the knowledge base? This cannot be undone.')) return;
    const button = event.currentTarget;
    button.disabled = true;
    button.setAttribute('aria-busy', 'true');
    try {
      const data = await api.resetKnowledgeBase();
      cancelAllChatRequests();
      toggleUploadPanel(false);
      await loadStatus({ announceErrors: false });
      showToast(data.message || 'Knowledge base cleared.', 'info');
    } catch (error) {
      showToast(error.message, 'error');
    } finally {
      button.disabled = false;
      button.removeAttribute('aria-busy');
    }
  });
}

function bootstrap() {
  initializeStore();
  setupChat();
  setupDocuments();
  setupVoice();
  setupSidebar();
  setupPrimaryActions();
  loadStatus();
  window.addEventListener('beforeunload', cancelAllChatRequests);
}

bootstrap();
