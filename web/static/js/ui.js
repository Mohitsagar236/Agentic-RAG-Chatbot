let toastTimer = 0;
let inertDepth = 0;
const priorInertState = new Map();

export function showToast(message, type = 'success') {
  const toast = document.getElementById('toast');
  window.clearTimeout(toastTimer);
  toast.textContent = String(message || '');
  toast.className = `toast show ${type}`;
  toastTimer = window.setTimeout(() => {
    toast.className = 'toast';
  }, 3_600);
}

export async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text);
    showToast('Copied!', 'success');
    return true;
  } catch {
    showToast('Copy failed. Select the text and copy it manually.', 'error');
    return false;
  }
}

export function trapFocus(event, container) {
  if (event.key !== 'Tab') return;
  const selector = [
    'button:not([disabled])',
    '[href]',
    'input:not([disabled])',
    'select:not([disabled])',
    'textarea:not([disabled])',
    '[tabindex]:not([tabindex="-1"])',
  ].join(',');
  const focusable = [...container.querySelectorAll(selector)]
    .filter((element) => !element.hidden && element.offsetParent !== null);
  if (focusable.length === 0) return;

  const first = focusable[0];
  const last = focusable.at(-1);
  if (event.shiftKey && document.activeElement === first) {
    event.preventDefault();
    last.focus();
  } else if (!event.shiftKey && document.activeElement === last) {
    event.preventDefault();
    first.focus();
  }
}

export function setAppInert(inert) {
  const elements = ['main', 'sidebar']
    .map((id) => document.getElementById(id))
    .filter(Boolean);

  if (inert) {
    if (inertDepth === 0) {
      priorInertState.clear();
      elements.forEach((element) => priorInertState.set(element, element.inert));
    }
    inertDepth += 1;
    elements.forEach((element) => { element.inert = true; });
    return;
  }

  inertDepth = Math.max(0, inertDepth - 1);
  if (inertDepth === 0) {
    elements.forEach((element) => {
      element.inert = priorInertState.get(element) || false;
    });
    priorInertState.clear();
  }
}
