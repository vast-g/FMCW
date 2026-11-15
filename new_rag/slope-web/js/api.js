/* ══════════════════════════════════════════════════════════════
   API 客户端
   ─ 统一处理三件事：JWT 携带、响应信封解包、错误提示
   ─ 两个后端：Java 业务域（/api）与 Python Agent（/agent）
     经 nginx 反代到同一来源，前端不需要知道端口，也不存在跨域
   ══════════════════════════════════════════════════════════════ */

const TOKEN_KEY = 'slope.token';
const USER_KEY  = 'slope.user';

const API = {
  /* ── token 管理 ── */
  get token() { return localStorage.getItem(TOKEN_KEY) || ''; },
  set token(v) { v ? localStorage.setItem(TOKEN_KEY, v) : localStorage.removeItem(TOKEN_KEY); },
  get user() { return localStorage.getItem(USER_KEY) || ''; },
  set user(v) { v ? localStorage.setItem(USER_KEY, v) : localStorage.removeItem(USER_KEY); },
  get loggedIn() { return !!this.token; },

  /**
   * 统一请求。
   * Java 侧返回 {code, message, data}，code===0 才算成功 ——
   * 不能只看 HTTP 状态码，否则 401 会被当成"有数据"。
   */
  async req(base, path, { method = 'GET', body, auth = true, raw = false } = {}) {
    const headers = { 'Content-Type': 'application/json' };
    if (auth && this.token) headers['Authorization'] = 'Bearer ' + this.token;

    let resp;
    try {
      resp = await fetch(base + path, {
        method,
        headers,
        body: body === undefined ? undefined : JSON.stringify(body),
      });
    } catch (e) {
      throw new Error('无法连接服务（检查后端是否已启动）');
    }

    if (raw) return resp;

    let payload = null;
    const text = await resp.text();
    if (text) {
      try { payload = JSON.parse(text); } catch { payload = { message: text.slice(0, 200) }; }
    }

    if (!resp.ok) {
      const msg = (payload && payload.message) || `HTTP ${resp.status}`;
      const err = new Error(msg);
      err.status = resp.status;
      // 401 统一处理：清掉本地 token，避免前端拿着废 token 反复请求
      if (resp.status === 401) { this.token = ''; this.user = ''; }
      throw err;
    }
    // Java 的信封：code!==0 视为失败
    if (payload && typeof payload.code === 'number' && payload.code !== 0) {
      throw new Error(payload.message || `业务错误 ${payload.code}`);
    }
    return payload ? payload.data : null;
  },

  /* ── Java 业务域 ── */
  java(path, opts) { return this.req('/api', path, opts); },

  /* ── Python Agent ── */
  agent(path, opts) { return this.req('/agent', path, opts); },

  /* ── 具体接口 ── */
  async login(username, password) {
    // 登录本身不需要 token
    const d = await this.req('/api', '/auth/login', {
      method: 'POST', body: { username, password }, auth: false,
    });
    this.token = d.token;
    this.user = d.username || username;
    return d;
  },

  logout() { this.token = ''; this.user = ''; },

  sites(params = {}) {
    const q = new URLSearchParams(
      Object.entries(params).filter(([, v]) => v !== '' && v != null));
    return this.java('/sites' + (q.toString() ? '?' + q : ''));
  },

  devices(params = {}) {
    const q = new URLSearchParams(
      Object.entries(params).filter(([, v]) => v !== '' && v != null));
    return this.java('/devices' + (q.toString() ? '?' + q : ''));
  },

  alertRules() { return this.java('/alert-rules'); },

  updateAlertRule(id, payload) {
    return this.java('/alert-rules/' + id, { method: 'PUT', body: payload });
  },

  alertEvents(params = {}) {
    const q = new URLSearchParams(
      Object.entries(params).filter(([, v]) => v !== '' && v != null));
    return this.java('/alert-events' + (q.toString() ? '?' + q : ''));
  },

  acknowledge(id) {
    return this.java(`/alert-events/${id}/acknowledge`, { method: 'POST' });
  },

  readings(params = {}) {
    const q = new URLSearchParams(
      Object.entries(params).filter(([, v]) => v !== '' && v != null));
    return this.java('/readings' + (q.toString() ? '?' + q : ''));
  },

  /**
   * 问 Agent。
   * Python 侧返回 {code, answer}，**没有 data 信封**，
   * 所以这里单独解包，不能走 java() 那套。
   */
  async chat(query, sessionId) {
    const r = await fetch('/agent/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ query, session_id: sessionId }),
    });
    const text = await r.text();
    if (!r.ok) throw new Error(`Agent 错误 HTTP ${r.status}: ${text.slice(0, 160)}`);
    let j = null;
    try { j = JSON.parse(text); } catch { throw new Error('Agent 返回非 JSON: ' + text.slice(0, 160)); }
    return j.answer ?? j.data?.answer ?? JSON.stringify(j).slice(0, 400);
  },

  /* ── 健康检查（两个后端各探一次） ── */
  async health() {
    const out = { java: false, agent: false };
    try {
      const d = await this.req('/api', '/health', { auth: false });
      out.java = !!(d && (d.status === 'UP' || d.database === 'up'));
    } catch { /* 保持 false */ }
    try {
      const d = await this.req('/agent', '/health', { auth: false });
      out.agent = !!d;
    } catch { /* 保持 false */ }
    return out;
  },
};
