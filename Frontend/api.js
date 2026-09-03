/* =========================================================
   NIDHI — backend client
   Talks to the FastAPI backend: login, company profile,
   run history, and the streaming /generate analysis.
   ========================================================= */

// Same-origin when served by the backend; falls back to localhost:8000 when
// index.html is opened directly from disk.
const API_BASE = (location.protocol === 'file:' || !location.port)
  ? 'http://localhost:8000'
  : location.origin;

const api = {
  base: API_BASE,

  async login(username, password) {
    const res = await fetch(`${API_BASE}/auth/login`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username, password }),
    });
    const body = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(body.detail || 'Login failed.');
    return body;
  },

  async session() {
    const res = await fetch(`${API_BASE}/auth/session`);
    return res.json();
  },

  async company(companyId) {
    const res = await fetch(`${API_BASE}/company/${companyId}`);
    if (!res.ok) throw new Error(`Company '${companyId}' is not registered.`);
    return res.json();
  },

  async history(limit = 20) {
    const res = await fetch(`${API_BASE}/history?limit=${limit}`);
    return res.ok ? res.json() : [];
  },

  async health() {
    try {
      const res = await fetch(`${API_BASE}/health`);
      return res.ok ? res.json() : null;
    } catch { return null; }
  },

  /**
   * Run the analysis and invoke `onEvent(section, event)` for every SSE frame
   * as it arrives.
   *
   * EventSource cannot be used here: it only issues GET requests and
   * /generate needs a POST body. So we read the response stream directly.
   */
  async generate(payload, onEvent) {
    const res = await fetch(`${API_BASE}/generate`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });

    if (!res.ok) {
      let detail = `Request failed (HTTP ${res.status}).`;
      try {
        const body = await res.json();
        if (Array.isArray(body.detail)) {
          detail = body.detail
            .map(d => `${(d.loc || []).slice(-1)[0]}: ${d.msg}`).join('; ');
        } else if (body.detail) {
          detail = body.detail;
        }
      } catch { /* keep the status-code message */ }
      throw new Error(detail);
    }

    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });

      const frames = buffer.split('\n\n');
      buffer = frames.pop();                 // keep the incomplete tail

      for (const frame of frames) {
        const line = frame.trim();
        if (!line.startsWith('data: ')) continue;
        let event;
        try {
          event = JSON.parse(line.slice(6));
        } catch (err) {
          console.warn('NIDHI: unparseable SSE frame', line);
          continue;
        }
        onEvent(event.section, event);
      }
    }
  },
};
