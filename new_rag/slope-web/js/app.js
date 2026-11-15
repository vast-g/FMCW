/* ══════════════════════════════════════════════════════════════
   主逻辑
   ─ 展示层只做三件事：拿数据、渲染、把操作转发给后端
   ─ 特别地：**前端不做任何告警判定**。要不要报警由规则引擎算，
     页面只呈现它的结果。前端自己判等级会导致"页面说的和后端不一致"。
   ══════════════════════════════════════════════════════════════ */

const LEVEL_CN = { blue: '蓝色', yellow: '黄色', orange: '橙色', red: '红色' };
const LEVEL_RANK = { blue: 1, yellow: 2, orange: 3, red: 4 };

const state = {
  sites: [],
  siteById: {},
  alerts: [],
  rules: [],
  siteCode: {},      // site_id -> code
  timer: null,
  chatting: false,
};

/* ────────────── 小工具 ────────────── */
const $ = (id) => document.getElementById(id);

function esc(s) {
  return String(s ?? '').replace(/[&<>"']/g,
    (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

/** 后端可能带纳秒（2026-10-12T13:39:41.123456），JS 的 Date 解析不了，截到毫秒 */
function parseTs(s) {
  if (!s) return null;
  const m = String(s).match(/^(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2}:\d{2})(\.\d{1,3})?/);
  return m ? new Date(`${m[1]}T${m[2]}${m[3] || ''}`) : new Date(s);
}

function fmtTime(s) {
  const d = parseTs(s);
  if (!d || isNaN(d)) return '—';
  const p = (n) => String(n).padStart(2, '0');
  return `${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
}

let toastTimer = null;
function toast(msg, kind = '') {
  const t = $('toast');
  t.textContent = msg;
  t.className = 'toast ' + kind;
  t.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { t.hidden = true; }, 3200);
}

function num(v, digits = 2) {
  const n = Number(v);
  return Number.isFinite(n) ? n.toFixed(digits) : '—';
}

/* ────────────── 健康检查 ────────────── */
async function refreshHealth() {
  const h = await API.health();
  document.querySelectorAll('.dot').forEach((d) => {
    const on = d.dataset.svc === 'java' ? h.java : h.agent;
    d.classList.toggle('on', on);
    d.classList.toggle('off', !on);
  });
}

/* ────────────── 登录 ────────────── */
function renderAuth() {
  const on = API.loggedIn;
  $('in-user').hidden = on;
  $('in-pass').hidden = on;
  $('btn-login').hidden = on;
  $('who').hidden = !on;
  $('btn-logout').hidden = !on;
  $('who').textContent = on ? `已登录：${API.user}` : '';
}

async function doLogin() {
  const u = $('in-user').value.trim(), p = $('in-pass').value;
  if (!u || !p) return toast('请输入用户名和密码', 'err');
  $('btn-login').disabled = true;
  try {
    await API.login(u, p);
    renderAuth();
    toast('登录成功', 'ok');
    await loadAll();
  } catch (e) {
    toast('登录失败：' + e.message, 'err');
  } finally {
    $('btn-login').disabled = false;
  }
}

function doLogout() {
  API.logout();
  renderAuth();
  toast('已退出');
}

/* ────────────── 点位 ────────────── */
async function loadSites() {
  state.sites = (await API.sites()) || [];
  state.siteById = {};
  state.siteCode = {};
  state.sites.forEach((s) => { state.siteById[s.id] = s; state.siteCode[s.id] = s.code; });

  // 点位下拉
  const sel = $('sel-site');
  const keep = sel.value;
  sel.innerHTML = state.sites.map((s) => `<option value="${s.id}">${esc(s.code)} · ${esc(s.name)}</option>`).join('');
  if (keep && state.siteById[keep]) sel.value = keep;

  // 点位卡片：显示该点位最近的告警数
  const alertCount = {};
  state.alerts.forEach((a) => { alertCount[a.site_id] = (alertCount[a.site_id] || 0) + 1; });

  $('sites').innerHTML = state.sites.map((s) => {
    const n = alertCount[s.id] || 0;
    return `<div class="site-item ${s.is_primary ? 'primary' : ''} ${n ? 'risk' : ''}">
      <div class="site-code">${esc(s.code)}${s.is_primary ? ' · 主' : ''}</div>
      <div class="site-name">${esc(s.name)}</div>
      <div class="site-metrics">
        <span>边坡组：<b>${esc(s.slope_group)}</b></span>
        <span>坐标：<b>${num(s.lat, 4)}, ${num(s.lng, 4)}</b></span>
        <span>告警：<b style="color:${n ? 'var(--lv-red)' : 'var(--ok)'}">${n} 条</b></span>
      </div>
    </div>`;
  }).join('') || '<p class="empty">暂无点位</p>';
}

/* ────────────── 告警 ────────────── */
async function loadAlerts() {
  const onlyUnacked = $('f-unacked').checked;
  const page = await API.alertEvents({
    page: 1, size: 50, acknowledged: onlyUnacked ? false : '',
  });
  const list = (page && page.list) || [];
  state.alerts = list;

  // 概要统计（按当前这一页）
  const counts = { total: (page && page.total) || list.length, unack: 0 };
  list.forEach((a) => {
    counts[a.level] = (counts[a.level] || 0) + 1;
    if (!a.acknowledged) counts.unack++;
  });
  Charts.levelBar($('alert-summary'), counts);

  $('tbl-alerts').querySelector('tbody').innerHTML = list.length ? list.map((a) => `
    <tr>
      <td><span class="lv lv-${esc(a.level)}">${LEVEL_CN[a.level] || esc(a.level)}</span></td>
      <td>${esc(state.siteCode[a.site_id] || a.site_id)}</td>
      <td title="${esc(a.rule_code)}">${esc(a.rule_code)}</td>
      <td>${num(a.value, 3)} / ${num(a.threshold, 3)}</td>
      <td>${fmtTime(a.created_at)}</td>
      <td>${a.acknowledged
            ? `<span class="tag ack">已确认 ${esc(a.acknowledged_by || '')}</span>`
            : '<span class="tag unack">未确认</span>'}</td>
      <td>${a.acknowledged ? '' :
            `<button class="btn btn-sm" data-ack="${a.id}">确认</button>`}</td>
    </tr>`).join('')
    : '<tr><td colspan="7" class="empty">没有符合条件的告警</td></tr>';

  renderMap();
}

/** 每个点位的告警数与最高等级 —— 供地图着色 */
function alertStatsBySite() {
  const cnt = {}, lv = {};
  state.alerts.forEach((a) => {
    cnt[a.site_id] = (cnt[a.site_id] || 0) + 1;
    const cur = lv[a.site_id];
    if (!cur || LEVEL_RANK[a.level] > LEVEL_RANK[cur]) lv[a.site_id] = a.level;
  });
  const byCode = {};
  Object.entries(cnt).forEach(([sid, n]) => { byCode[state.siteCode[sid] || sid] = n; });
  const lvByCode = {};
  Object.entries(lv).forEach(([sid, l]) => { lvByCode[state.siteCode[sid] || sid] = l; });
  return { cnt: byCode, lv: lvByCode };
}

function renderMap() {
  const { cnt, lv } = alertStatsBySite();
  Charts.siteMap($('map'), state.sites.map((s) => ({
    code: s.code, lat: s.lat, lng: s.lng,
  })), cnt, lv);
}

async function ackAlert(id) {
  try {
    await API.acknowledge(id);
    toast('已确认（确认人取自登录身份，后端记录）', 'ok');
    await loadAlerts();
    await loadSites();
  } catch (e) {
    toast('确认失败：' + e.message, 'err');
  }
}

/* ────────────── 规则 ────────────── */
async function loadRules() {
  state.rules = (await API.alertRules()) || [];
  $('tbl-rules').querySelector('tbody').innerHTML = state.rules.map((r) => `
    <tr>
      <td title="${esc(r.description || '')}">${esc(r.code)}</td>
      <td>${esc(r.name)}</td>
      <td><input class="th-input" type="number" step="0.01" value="${r.threshold}" data-th="${r.id}"></td>
      <td><span class="lv lv-${esc(r.level)}">${LEVEL_CN[r.level] || esc(r.level)}</span></td>
      <td><input type="checkbox" ${r.enabled ? 'checked' : ''} data-en="${r.id}"></td>
    </tr>`).join('') || '<tr><td colspan="5" class="empty">暂无规则</td></tr>';
}

/** 阈值改动：读当前值 → 提交 → 回读校验 → 失败回滚 */
async function saveThreshold(id, input) {
  const rule = state.rules.find((r) => String(r.id) === String(id));
  if (!rule) return;
  const old = rule.threshold;
  const next = Number(input.value);
  if (!Number.isFinite(next)) { input.value = old; return toast('阈值必须是数字', 'err'); }
  if (Math.abs(next - old) < 1e-9) return;

  try {
    await API.updateAlertRule(id, { ...rule, threshold: next });
    const fresh = (await API.alertRules()).find((r) => String(r.id) === String(id));
    if (!fresh || Math.abs(Number(fresh.threshold) - next) > 1e-9) {
      throw new Error('回读校验不一致');
    }
    rule.threshold = Number(fresh.threshold);
    input.value = rule.threshold;
    toast(`${rule.code} 阈值 ${old} → ${rule.threshold}（规则引擎立即按新值判定）`, 'ok');
  } catch (e) {
    input.value = old;
    toast('保存失败，已回滚：' + e.message, 'err');
  }
}

async function toggleEnabled(id, box) {
  const rule = state.rules.find((r) => String(r.id) === String(id));
  if (!rule) return;
  const want = box.checked;
  try {
    await API.updateAlertRule(id, { ...rule, enabled: want });
    rule.enabled = want;
    toast(`${rule.code} 已${want ? '启用' : '停用'}`, 'ok');
  } catch (e) {
    box.checked = !want;
    toast('切换失败：' + e.message, 'err');
  }
}

/* ────────────── 趋势图 ────────────── */
const METRIC_COLOR = {
  displacement_mm: '#2dd4bf',
  rainfall_mm: '#38bdf8',
  pore_pressure_kpa: '#a78bfa',
  crack_width_mm: '#fb923c',
};
const METRIC_UNIT = {
  displacement_mm: ' mm',
  rainfall_mm: ' mm/h',
  pore_pressure_kpa: ' kPa',
  crack_width_mm: ' mm',
};

async function loadTrend() {
  const sid = $('sel-site').value;
  const metric = $('sel-metric').value;
  if (!sid) return;
  try {
    const rows = (await API.readings({ site_id: sid, metric, hours: 72, limit: 300 })) || [];
    const pts = rows.map((r) => ({ ts: r.ts, value: r.value }));
    Charts.line($('chart'), pts, {
      color: METRIC_COLOR[metric] || '#2dd4bf',
      unit: METRIC_UNIT[metric] || '',
    });
    const site = state.siteById[sid] || {};
    $('chart-legend').innerHTML =
      `<span>点位 <b>${esc(site.code || '')}</b></span>
       <span>指标 <b>${esc(metric)}</b></span>
       <span>数据点 <b>${pts.length}</b></span>
       <span>窗口 <b>近 72 小时</b></span>`;
  } catch (e) {
    Charts.line($('chart'), [], {});
    $('chart-legend').innerHTML = `<span>加载失败：${esc(e.message)}</span>`;
  }
}

/* ────────────── Agent 对话 ────────────── */
function pushMsg(role, text, cls = '') {
  const wrap = document.createElement('div');
  wrap.className = 'msg ' + role + (cls ? ' ' + cls : '');
  const bubble = document.createElement('div');
  bubble.className = 'bubble';
  // 让 **加粗** 生效，其余按纯文本处理（先转义再替换标记，避免 XSS）
  bubble.innerHTML = esc(text)
    .replace(/\*\*([^*]+)\*\*/g, '<b>$1</b>')
    .replace(/`([^`]+)`/g, '<code>$1</code>');
  wrap.appendChild(bubble);
  $('chat-log').appendChild(wrap);
  $('chat-log').scrollTop = $('chat-log').scrollHeight;
  return wrap;
}

async function sendQuestion() {
  if (state.chatting) return;
  const q = $('in-q').value.trim();
  if (!q) return;
  $('in-q').value = '';
  pushMsg('user', q);

  const thinking = pushMsg('bot', 'Agent 正在综合多因素分析…（深度评估约需 1~2 分钟）', 'thinking');
  state.chatting = true;
  $('btn-send').disabled = true;

  try {
    // 用浏览器会话标识，让 Python 侧按会话隔离上下文
    const sid = sessionStorage.getItem('slope.sid')
      || (sessionStorage.setItem('slope.sid', 'web-' + Math.random().toString(36).slice(2, 10)),
          sessionStorage.getItem('slope.sid'));
    const ans = await API.chat(q, sid);
    thinking.remove();
    pushMsg('bot', ans);
  } catch (e) {
    thinking.remove();
    pushMsg('bot', '出错了：' + e.message + '\n（确认 Python Agent 已在 8000 端口启动）');
  } finally {
    state.chatting = false;
    $('btn-send').disabled = false;
  }
}

/* ────────────── 总加载 ────────────── */
async function loadAll() {
  if (!API.loggedIn) return;
  try {
    await loadSites();
    await loadAlerts();
    await loadSites();     // 告警数变了，点位卡片跟着更新
    await loadRules();
    await loadTrend();
  } catch (e) {
    if (e.status === 401) { renderAuth(); toast('登录已过期，请重新登录', 'err'); }
    else toast('加载失败：' + e.message, 'err');
  }
}

/* ────────────── 事件绑定 ────────────── */
function bind() {
  $('btn-login').addEventListener('click', doLogin);
  $('btn-logout').addEventListener('click', doLogout);
  $('in-pass').addEventListener('keydown', (e) => { if (e.key === 'Enter') doLogin(); });

  $('btn-alerts-refresh').addEventListener('click', loadAlerts);
  $('f-unacked').addEventListener('change', loadAlerts);
  $('btn-sites-refresh').addEventListener('click', loadSites);
  $('btn-rules-refresh').addEventListener('click', loadRules);

  $('tbl-alerts').addEventListener('click', (e) => {
    const btn = e.target.closest('[data-ack]');
    if (btn) ackAlert(btn.dataset.ack);
  });

  // 阈值：失焦或回车时提交
  $('tbl-rules').addEventListener('change', (e) => {
    const t = e.target;
    if (t.dataset.th) saveThreshold(t.dataset.th, t);
    if (t.dataset.en) toggleEnabled(t.dataset.en, t);
  });
  $('tbl-rules').addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && e.target.dataset.th) e.target.blur();
  });

  $('sel-site').addEventListener('change', loadTrend);
  $('sel-metric').addEventListener('change', loadTrend);

  $('btn-send').addEventListener('click', sendQuestion);
  $('in-q').addEventListener('keydown', (e) => { if (e.key === 'Enter') sendQuestion(); });

  // 窗口尺寸变化要重画：SVG 的 viewBox 依赖元素真实像素
  let rt = null;
  window.addEventListener('resize', () => {
    clearTimeout(rt);
    rt = setTimeout(() => { renderMap(); loadTrend(); }, 200);
  });
}

/* ────────────── 启动 ────────────── */
(function init() {
  bind();
  renderAuth();
  refreshHealth();
  setInterval(refreshHealth, 15000);   // 两个后端的健康灯定期刷新
  if (API.loggedIn) loadAll();
  else {
    loadTrend().catch(() => {});        // 趋势图需要鉴权，未登录时留空即可
  }
})();
