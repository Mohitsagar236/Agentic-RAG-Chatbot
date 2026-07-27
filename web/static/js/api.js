const DEFAULT_TIMEOUT_MS = 45_000;

export class ApiError extends Error {
  constructor(message, { status = 0, code = 'request_failed', details = null } = {}) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.code = code;
    this.details = details;
  }
}

function extractError(payload, fallback) {
  if (typeof payload?.error === 'string') {
    return { message: payload.error, code: 'request_failed' };
  }
  if (payload?.error && typeof payload.error === 'object') {
    return {
      message: payload.error.message || fallback,
      code: payload.error.code || 'request_failed',
    };
  }
  if (typeof payload?.message === 'string') {
    return { message: payload.message, code: 'request_failed' };
  }
  return { message: fallback, code: 'request_failed' };
}

async function decodeResponse(response) {
  const text = await response.text();
  if (!text) return {};

  try {
    return JSON.parse(text);
  } catch {
    if (!response.ok) {
      throw new ApiError(
        `Request failed (${response.status}). The server returned a non-JSON response.`,
        { status: response.status, code: 'invalid_response' },
      );
    }
    throw new ApiError('The server returned an invalid response.', {
      status: response.status,
      code: 'invalid_response',
    });
  }
}

async function request(path, options = {}) {
  const {
    signal: externalSignal,
    timeoutMs = DEFAULT_TIMEOUT_MS,
    headers,
    ...fetchOptions
  } = options;

  const controller = new AbortController();
  let timedOut = false;
  const onExternalAbort = () => controller.abort(externalSignal?.reason);
  externalSignal?.addEventListener('abort', onExternalAbort, { once: true });
  if (externalSignal?.aborted) onExternalAbort();

  const timeoutId = window.setTimeout(() => {
    timedOut = true;
    controller.abort();
  }, timeoutMs);

  try {
    const response = await fetch(path, {
      ...fetchOptions,
      signal: controller.signal,
      headers: {
        Accept: 'application/json',
        'X-RAG-Client': 'web',
        ...headers,
      },
    });
    const payload = await decodeResponse(response);
    if (!response.ok) {
      const fallback = `Request failed (${response.status}).`;
      const error = extractError(payload, fallback);
      throw new ApiError(error.message, {
        status: response.status,
        code: error.code,
        details: payload,
      });
    }
    return payload;
  } catch (error) {
    if (timedOut) {
      throw new ApiError('The request timed out. Please try again.', {
        code: 'timeout',
      });
    }
    if (error?.name === 'AbortError' || externalSignal?.aborted) throw error;
    if (error instanceof ApiError) throw error;
    throw new ApiError('Unable to reach the server. Check your connection and try again.', {
      code: 'network_error',
      details: error,
    });
  } finally {
    window.clearTimeout(timeoutId);
    externalSignal?.removeEventListener('abort', onExternalAbort);
  }
}

function jsonOptions(method, body, options = {}) {
  return {
    ...options,
    method,
    headers: {
      'Content-Type': 'application/json',
      ...options.headers,
    },
    body: JSON.stringify(body),
  };
}

export const api = {
  getStatus(options) {
    return request('/api/status', options);
  },

  getDocuments(options) {
    return request('/api/documents', options);
  },

  getDocumentContent(source, options) {
    return request(`/api/documents/content?source=${encodeURIComponent(source)}`, options);
  },

  deleteDocument(source, options) {
    return request(`/api/documents?source=${encodeURIComponent(source)}`, {
      ...options,
      method: 'DELETE',
    });
  },

  ingestDocuments(files, options = {}) {
    const body = new FormData();
    files.forEach((file) => body.append('files', file));
    return request('/api/ingest', {
      ...options,
      method: 'POST',
      body,
      timeoutMs: options.timeoutMs ?? 120_000,
    });
  },

  chat(question, conversationId, options) {
    return request('/api/chat', jsonOptions('POST', {
      question,
      conversation_id: conversationId,
    }, options));
  },

  clearMemory(conversationId, options) {
    return request('/api/clear-memory', jsonOptions('POST', {
      conversation_id: conversationId,
    }, options));
  },

  resetKnowledgeBase(options) {
    return request('/api/reset', { ...options, method: 'POST' });
  },
};
