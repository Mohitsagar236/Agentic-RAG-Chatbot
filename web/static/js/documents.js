import { api } from './api.js';
import { setKnowledgeReady } from './chat.js';
import { escapeHtml } from './markdown.js';
import { setAppInert, showToast, trapFocus } from './ui.js';

const ALLOWED_EXTENSIONS = new Set(['.pdf', '.txt', '.csv', '.md']);

let selectedFiles = [];
let documents = [];
let uploadPanelOpen = false;
let ingesting = false;
let modalController = null;
let modalReturnFocus = null;

function fileIcon(name) {
  const extension = String(name || '').split('.').pop().toLowerCase();
  return { pdf: '📄', txt: '📝', csv: '📊', md: '📋' }[extension] || '📄';
}

function basename(source) {
  return String(source || '').split(/[\\/]/).pop();
}

function renderDocuments(items) {
  documents = items;
  const section = document.getElementById('docs-section');
  const list = document.getElementById('docs-list');
  document.getElementById('doc-count').textContent = String(items.length);
  section.style.display = items.length ? '' : 'none';
  list.innerHTML = items.map((documentInfo) => {
    const name = documentInfo.name || basename(documentInfo.source) || 'Document';
    const type = String(documentInfo.type || name.split('.').pop() || 'unknown').toUpperCase();
    const chunks = documentInfo.chunks ?? '?';
    return `
      <div class="doc-item">
        <span class="doc-item-icon" aria-hidden="true">${fileIcon(name)}</span>
        <div class="doc-item-info">
          <span class="doc-item-name" title="${escapeHtml(name)}">${escapeHtml(name)}</span>
          <span class="doc-item-meta">${escapeHtml(chunks)} chunk${chunks === 1 ? '' : 's'} · ${escapeHtml(type)}</span>
        </div>
        <div class="doc-item-btns">
          <button type="button" class="doc-btn view-btn" data-source="${escapeHtml(documentInfo.source)}"
                  aria-label="View ${escapeHtml(name)}">
            <svg aria-hidden="true" width="12" height="12" viewBox="0 0 24 24" fill="none"
                 stroke="currentColor" stroke-width="2">
              <path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/>
              <circle cx="12" cy="12" r="3"/>
            </svg>
          </button>
          <button type="button" class="doc-btn del-btn" data-source="${escapeHtml(documentInfo.source)}"
                  data-name="${escapeHtml(name)}" aria-label="Delete ${escapeHtml(name)}">
            <svg aria-hidden="true" width="11" height="11" viewBox="0 0 24 24" fill="none"
                 stroke="currentColor" stroke-width="2">
              <polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14H6L5 6"/>
              <path d="M10 11v6M14 11v6"/>
            </svg>
          </button>
        </div>
      </div>`;
  }).join('');
}

async function loadDocuments(statusSources = []) {
  try {
    const data = await api.getDocuments();
    const items = Array.isArray(data.documents) ? data.documents : [];
    renderDocuments(items);
  } catch (error) {
    const fallback = [...new Set(statusSources)].map((source) => {
      const name = basename(source);
      return {
        name,
        source,
        type: name.split('.').pop().toLowerCase(),
        chunks: '?',
      };
    });
    renderDocuments(fallback);
    throw error;
  }
}

export async function loadStatus({ announceErrors = true } = {}) {
  try {
    const data = await api.getStatus();
    const chunkCount = Number(data.chunk_count) || 0;
    document.getElementById('chunk-count').textContent = String(chunkCount);
    document.getElementById('model-name-sidebar').textContent = data.model || 'Unavailable';
    document.getElementById('model-chip-name').textContent = data.model || 'RAG Assistant';
    setKnowledgeReady(chunkCount > 0);

    try {
      await loadDocuments(Array.isArray(data.sources) ? data.sources : []);
    } catch (documentError) {
      if (announceErrors) showToast(documentError.message, 'error');
    }
    return data;
  } catch (error) {
    setKnowledgeReady(false);
    document.getElementById('model-name-sidebar').textContent = 'Unavailable';
    if (announceErrors) showToast(`Status unavailable: ${error.message}`, 'error');
    return null;
  }
}

export function toggleUploadPanel(open) {
  const panel = document.getElementById('upload-panel');
  uploadPanelOpen = open ?? !uploadPanelOpen;
  panel.style.display = uploadPanelOpen ? '' : 'none';
  panel.setAttribute('aria-hidden', String(!uploadPanelOpen));
  const attachButton = document.getElementById('attach-btn');
  attachButton.classList.toggle('active', uploadPanelOpen);
  attachButton.setAttribute('aria-expanded', String(uploadPanelOpen));
  if (uploadPanelOpen) document.getElementById('drop-zone').focus();
}

function addFiles(files) {
  for (const file of files) {
    const dotIndex = file.name.lastIndexOf('.');
    const extension = dotIndex >= 0 ? file.name.slice(dotIndex).toLowerCase() : '';
    if (!ALLOWED_EXTENSIONS.has(extension)) {
      showToast(`Unsupported file: ${file.name}`, 'error');
      continue;
    }
    const duplicate = selectedFiles.some((candidate) => (
      candidate.name === file.name
      && candidate.size === file.size
      && candidate.lastModified === file.lastModified
    ));
    if (!duplicate) selectedFiles.push(file);
  }
  renderFileList();
}

function renderFileList() {
  const list = document.getElementById('file-list');
  list.innerHTML = selectedFiles.map((file, index) => `
    <div class="file-item">
      <span aria-hidden="true">${fileIcon(file.name)}</span>
      <span class="file-name" title="${escapeHtml(file.name)}">${escapeHtml(file.name)}</span>
      <button type="button" class="remove-file-btn" data-index="${index}"
              aria-label="Remove ${escapeHtml(file.name)}">&times;</button>
    </div>`).join('');
  document.getElementById('ingest-btn').disabled = ingesting || selectedFiles.length === 0;
}

async function ingestSelectedFiles() {
  if (ingesting || selectedFiles.length === 0) return;
  const button = document.getElementById('ingest-btn');
  const status = document.getElementById('ingest-status');
  const filesToUpload = [...selectedFiles];
  ingesting = true;
  button.textContent = 'Ingesting…';
  status.textContent = `Uploading ${filesToUpload.length} file${filesToUpload.length === 1 ? '' : 's'}…`;
  status.className = 'ingest-status';
  renderFileList();

  try {
    const data = await api.ingestDocuments(filesToUpload);
    selectedFiles = selectedFiles.filter((file) => !filesToUpload.includes(file));
    status.textContent = data.message || 'Documents ingested.';
    status.className = 'ingest-status success';
    await loadStatus({ announceErrors: false });
    showToast(status.textContent, 'success');
    window.setTimeout(() => toggleUploadPanel(false), 1_200);
  } catch (error) {
    status.textContent = error.message;
    status.className = 'ingest-status error';
    showToast(error.message, 'error');
  } finally {
    ingesting = false;
    button.textContent = 'Ingest Documents';
    renderFileList();
  }
}

async function deleteDocument(source, name) {
  if (!window.confirm(`Delete "${name}" from the knowledge base?`)) return;
  try {
    const data = await api.deleteDocument(source);
    showToast(data.message || `${name} deleted.`, 'success');
    await loadStatus({ announceErrors: false });
  } catch (error) {
    showToast(error.message, 'error');
  }
}

function resolveDocumentSource(source) {
  const exact = documents.find((item) => item.source === source);
  if (exact) return exact.source;
  const byName = documents.find((item) => (
    item.name === source || basename(item.source) === basename(source)
  ));
  return byName?.source || source;
}

export async function openDocumentModal(source) {
  if (!source) return;
  modalController?.abort();
  const requestController = new AbortController();
  modalController = requestController;
  modalReturnFocus = document.activeElement;

  const resolvedSource = resolveDocumentSource(source);
  const overlay = document.getElementById('doc-overlay');
  const body = document.getElementById('doc-modal-body');
  const name = document.getElementById('doc-modal-name');
  const info = document.getElementById('doc-modal-info');
  const icon = document.getElementById('doc-modal-icon');
  const badge = document.getElementById('doc-type-badge');
  const footer = document.getElementById('doc-modal-foot');
  const truncated = document.getElementById('doc-trunc');

  body.classList.remove('message-error');
  body.innerHTML = '<div class="doc-loading"><div class="dots" aria-hidden="true"><span></span><span></span><span></span></div> Loading…</div>';
  footer.style.display = 'none';
  name.textContent = basename(resolvedSource) || 'Document';
  info.textContent = '';
  badge.textContent = '…';
  icon.textContent = '📄';
  overlay.classList.add('open');
  overlay.setAttribute('aria-hidden', 'false');
  setAppInert(true);
  document.getElementById('doc-modal-close').focus();

  try {
    const data = await api.getDocumentContent(resolvedSource, {
      signal: requestController.signal,
    });
    name.textContent = data.name || basename(resolvedSource);
    badge.textContent = String(data.type || 'unknown').toUpperCase();
    icon.textContent = fileIcon(data.name);
    info.textContent = `${data.chunks ?? 0} chunks · ${Number(data.total_chars || 0).toLocaleString()} characters`;

    const content = document.createElement('pre');
    content.className = 'doc-content';
    content.textContent = data.content || '';
    body.replaceChildren(content);
    footer.style.display = 'flex';
    truncated.textContent = data.truncated
      ? `Showing first 15,000 of ${Number(data.total_chars || 0).toLocaleString()} characters`
      : '';
  } catch (error) {
    if (requestController.signal.aborted) return;
    body.textContent = `Failed to load content: ${error.message}`;
    body.classList.add('message-error');
  }
}

export function closeDocumentModal() {
  const overlay = document.getElementById('doc-overlay');
  if (!overlay.classList.contains('open')) return;
  modalController?.abort();
  modalController = null;
  overlay.classList.remove('open');
  overlay.setAttribute('aria-hidden', 'true');
  setAppInert(false);
  if (modalReturnFocus?.isConnected) modalReturnFocus.focus();
  modalReturnFocus = null;
}

function handleModalKeydown(event) {
  const overlay = document.getElementById('doc-overlay');
  if (!overlay.classList.contains('open')) return;
  if (event.key === 'Escape') {
    event.preventDefault();
    closeDocumentModal();
    return;
  }
  trapFocus(event, overlay.querySelector('.doc-modal'));
}

export function setupDocuments() {
  const input = document.getElementById('file-input');
  const dropZone = document.getElementById('drop-zone');

  dropZone.addEventListener('click', () => input.click());
  dropZone.addEventListener('dragover', (event) => {
    event.preventDefault();
    dropZone.classList.add('drag-over');
  });
  dropZone.addEventListener('dragleave', () => dropZone.classList.remove('drag-over'));
  dropZone.addEventListener('drop', (event) => {
    event.preventDefault();
    dropZone.classList.remove('drag-over');
    addFiles([...event.dataTransfer.files]);
  });
  input.addEventListener('change', () => {
    addFiles([...input.files]);
    input.value = '';
  });

  document.getElementById('file-list').addEventListener('click', (event) => {
    const removeButton = event.target.closest('.remove-file-btn');
    if (!removeButton) return;
    selectedFiles.splice(Number(removeButton.dataset.index), 1);
    renderFileList();
  });
  document.getElementById('ingest-btn').addEventListener('click', ingestSelectedFiles);
  document.getElementById('cancel-upload-btn').addEventListener('click', () => toggleUploadPanel(false));
  document.getElementById('attach-btn').addEventListener('click', () => toggleUploadPanel());
  document.getElementById('upload-trigger-btn').addEventListener('click', () => {
    window.dispatchEvent(new CustomEvent('rag:close-sidebar'));
    toggleUploadPanel(true);
  });
  document.getElementById('refresh-docs-btn').addEventListener('click', () => loadStatus());

  document.getElementById('docs-list').addEventListener('click', (event) => {
    const viewButton = event.target.closest('.view-btn');
    const deleteButton = event.target.closest('.del-btn');
    if (viewButton) openDocumentModal(viewButton.dataset.source);
    if (deleteButton) deleteDocument(deleteButton.dataset.source, deleteButton.dataset.name);
  });

  document.getElementById('doc-modal-close').addEventListener('click', closeDocumentModal);
  document.getElementById('doc-modal-close-foot').addEventListener('click', closeDocumentModal);
  document.getElementById('doc-overlay').addEventListener('click', (event) => {
    if (event.target.id === 'doc-overlay') closeDocumentModal();
  });
  document.addEventListener('keydown', handleModalKeydown);
  window.addEventListener('rag:open-document', (event) => openDocumentModal(event.detail?.source));
}
