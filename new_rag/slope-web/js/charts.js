/* ══════════════════════════════════════════════════════════════
   手写 SVG 图表
   ─ 为什么不用 ECharts / Chart.js：部署环境**没有外网**（CDN 不可达），
     引任何外部库都会白屏。这里全部手写，离线可用、无构建链。
   ─ 关键点：viewBox 跟着元素实际像素走，否则文字会被拉伸变形。
   ══════════════════════════════════════════════════════════════ */

const SVG_NS = 'http://www.w3.org/2000/svg';

const Charts = {

  /** 让 viewBox 匹配元素的真实像素尺寸 —— 保证文字不被拉伸 */
  _fit(svg) {
    const r = svg.getBoundingClientRect();
    const w = Math.max(300, Math.round(r.width || 300));
    const h = Math.max(140, Math.round(r.height || 160));
    svg.setAttribute('viewBox', `0 0 ${w} ${h}`);
    return { w, h };
  },

  _el(tag, attrs = {}) {
    const e = document.createElementNS(SVG_NS, tag);
    for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v);
    return e;
  },

  _clear(svg) { while (svg.firstChild) svg.removeChild(svg.firstChild); },

  /**
   * 折线图。
   * @param points [{ts, value}]  时间升序
   * @param opts   {color, unit, label}
   */
  line(svg, points, opts = {}) {
    const { w, h } = this._fit(svg);
    this._clear(svg);

    const padL = 58, padR = 16, padT = 16, padB = 30;
    const iw = w - padL - padR;
    const ih = h - padT - padB;
    const color = opts.color || '#2dd4bf';

    if (!points || points.length < 2) {
      svg.appendChild(this._el('text', {
        x: w / 2, y: h / 2, fill: '#5d6b7a', 'font-size': 13,
        'text-anchor': 'middle',
      })).textContent = '暂无足够数据';
      return;
    }

    const xs = points.map((p) => new Date(p.ts).getTime());
    const ys = points.map((p) => Number(p.value) || 0);
    const x0 = Math.min(...xs), x1 = Math.max(...xs);
    let y0 = Math.min(...ys), y1 = Math.max(...ys);
    // 上下各留 8% 余量，否则线会贴着边框
    const span = (y1 - y0) || 1;
    y0 -= span * 0.08; y1 += span * 0.08;

    const px = (t) => padL + ((t - x0) / ((x1 - x0) || 1)) * iw;
    const py = (v) => padT + ih - ((v - y0) / ((y1 - y0) || 1)) * ih;

    /* 网格 + Y 轴刻度（5 条） */
    for (let i = 0; i <= 4; i++) {
      const v = y0 + ((y1 - y0) * i) / 4;
      const y = py(v);
      svg.appendChild(this._el('line', {
        x1: padL, y1: y, x2: w - padR, y2: y,
        stroke: '#2b3542', 'stroke-width': 1,
        'stroke-dasharray': i === 0 ? '' : '3 4',
      }));
      const t = this._el('text', {
        x: padL - 8, y: y + 4, fill: '#5d6b7a', 'font-size': 10.5,
        'text-anchor': 'end',
      });
      t.textContent = Math.abs(v) >= 1000 ? v.toFixed(0) : v.toFixed(2);
      svg.appendChild(t);
    }

    /* 面积 + 折线 */
    const dLine = points.map((p, i) =>
      `${i ? 'L' : 'M'}${px(xs[i]).toFixed(1)},${py(ys[i]).toFixed(1)}`).join(' ');
    svg.appendChild(this._el('path', {
      d: `${dLine} L${px(x1).toFixed(1)},${padT + ih} L${px(x0).toFixed(1)},${padT + ih} Z`,
      fill: color, opacity: 0.12,
    }));
    svg.appendChild(this._el('path', {
      d: dLine, fill: 'none', stroke: color, 'stroke-width': 2,
      'stroke-linejoin': 'round', 'stroke-linecap': 'round',
    }));

    /* X 轴时间刻度（首/中/末，只显示 时:分） */
    const fmt = (t) => {
      const d = new Date(t);
      return `${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')} `
           + `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
    };
    [0, Math.floor((points.length - 1) / 2), points.length - 1].forEach((i, k) => {
      const t = this._el('text', {
        x: px(xs[i]), y: h - 9, fill: '#5d6b7a', 'font-size': 10.5,
        'text-anchor': k === 0 ? 'start' : (k === 2 ? 'end' : 'middle'),
      });
      t.textContent = fmt(xs[i]);
      svg.appendChild(t);
    });

    /* 最新值标注 */
    const lx = px(xs[xs.length - 1]), ly = py(ys[ys.length - 1]);
    svg.appendChild(this._el('circle', { cx: lx, cy: ly, r: 4, fill: color }));
    svg.appendChild(this._el('circle', {
      cx: lx, cy: ly, r: 8, fill: 'none', stroke: color, opacity: 0.4,
    }));
    const last = this._el('text', {
      x: Math.min(lx + 10, w - padR - 4), y: Math.max(ly - 9, 12),
      fill: color, 'font-size': 11.5, 'font-weight': 600,
      'text-anchor': lx > w - 90 ? 'end' : 'start',
    });
    last.textContent = `${ys[ys.length - 1].toFixed(2)}${opts.unit || ''}`;
    svg.appendChild(last);
  },

  /**
   * 点位分布示意：按经纬度做线性投影。
   * 圈的大小 = 该点位当前告警数，颜色 = 最高告警等级。
   */
  siteMap(svg, sites, alertCount, alertLevel) {
    const { w, h } = this._fit(svg);
    this._clear(svg);

    const pts = (sites || []).filter((s) => Number.isFinite(Number(s.lat)) && Number(s.lat) !== 0);
    if (!pts.length) {
      const t = this._el('text', {
        x: w / 2, y: h / 2, fill: '#5d6b7a', 'font-size': 13, 'text-anchor': 'middle',
      });
      t.textContent = '暂无带坐标的点位';
      svg.appendChild(t);
      return;
    }

    const pad = 46;
    const lats = pts.map((s) => Number(s.lat));
    const lngs = pts.map((s) => Number(s.lng));
    const la0 = Math.min(...lats), la1 = Math.max(...lats);
    const ln0 = Math.min(...lngs), ln1 = Math.max(...lngs);
    const dLa = (la1 - la0) || 0.001, dLn = (ln1 - ln0) || 0.001;

    // 纬度向上，所以 y 要翻转
    const X = (lng) => pad + ((lng - ln0) / dLn) * (w - pad * 2);
    const Y = (lat) => h - pad - ((lat - la0) / dLa) * (h - pad * 2);

    const LEVEL_COLOR = {
      blue: '#3b82f6', yellow: '#eab308', orange: '#f97316', red: '#ef4444',
    };

    /* 底纹网格 */
    for (let i = 1; i < 5; i++) {
      const x = pad + ((w - pad * 2) * i) / 5;
      const y = pad + ((h - pad * 2) * i) / 5;
      svg.appendChild(this._el('line', { x1: x, y1: pad, x2: x, y2: h - pad,
        stroke: '#212a34', 'stroke-width': 1 }));
      svg.appendChild(this._el('line', { x1: pad, y1: y, x2: w - pad, y2: y,
        stroke: '#212a34', 'stroke-width': 1 }));
    }

    pts.forEach((s) => {
      const n = alertCount[s.code] || 0;
      const lv = alertLevel[s.code];
      const col = LEVEL_COLOR[lv] || (n ? '#f97316' : '#22c55e');
      const x = X(Number(s.lng)), y = Y(Number(s.lat));
      const r = 9 + Math.min(n, 6) * 2.2;

      // 有告警时加一圈呼吸光晕，一眼能从图里挑出来
      if (n) {
        svg.appendChild(this._el('circle', {
          cx: x, cy: y, r: r + 7, fill: col, opacity: 0.13,
        }));
      }
      svg.appendChild(this._el('circle', {
        cx: x, cy: y, r, fill: col, opacity: 0.22, stroke: col, 'stroke-width': 1.6,
      }));
      svg.appendChild(this._el('circle', { cx: x, cy: y, r: 3, fill: col }));

      const code = this._el('text', {
        x, y: y - r - 7, fill: '#e6edf3', 'font-size': 11.5,
        'font-weight': 600, 'text-anchor': 'middle',
      });
      code.textContent = s.code;
      svg.appendChild(code);

      if (n) {
        const badge = this._el('text', {
          x, y: y + r + 14, fill: col, 'font-size': 10.5, 'text-anchor': 'middle',
        });
        badge.textContent = `${n} 条告警`;
        svg.appendChild(badge);
      }
    });
  },

  /** 等级分布条：给告警面板做概要 */
  levelBar(host, counts) {
    const order = [['red', '红色'], ['orange', '橙色'], ['yellow', '黄色'], ['blue', '蓝色']];
    host.innerHTML = order.map(([k, label]) => `
      <div class="sum-item">
        <div class="sum-num lv-${k}" style="background:none;border:none;padding:0;color:var(--lv-${k})">
          ${counts[k] || 0}
        </div>
        <div class="sum-label">${label}</div>
      </div>`).join('') + `
      <div class="sum-item">
        <div class="sum-num">${counts.total || 0}</div>
        <div class="sum-label">合计</div>
      </div>
      <div class="sum-item">
        <div class="sum-num" style="color:var(--lv-red)">${counts.unack || 0}</div>
        <div class="sum-label">未确认</div>
      </div>`;
  },
};
