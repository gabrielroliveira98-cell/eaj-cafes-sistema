/* Gráficos SVG da EAJ (sem dependências).
 * O SVG é desenhado na largura real do contêiner, então os textos mantêm 12px em qualquer tela.
 * Todos os gráficos têm tooltip ao passar o mouse / tocar, e estado vazio quando não há dados.
 */
window.EAJCharts = (() => {
  const NS = 'http://www.w3.org/2000/svg';
  const esc = s => String(s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const tip = document.createElement('div');
  tip.className = 'chart-tip'; tip.hidden = true; tip.setAttribute('role', 'tooltip');
  document.body.appendChild(tip);

  function showTip(html, ev) {
    tip.innerHTML = html; tip.hidden = false;
    const w = tip.offsetWidth, h = tip.offsetHeight;
    let x = ev.clientX + 14, y = ev.clientY + 14;
    if (x + w > innerWidth - 8) x = ev.clientX - w - 14;
    if (y + h > innerHeight - 8) y = ev.clientY - h - 14;
    tip.style.left = Math.max(8, x) + 'px'; tip.style.top = Math.max(8, y) + 'px';
  }
  const hideTip = () => { tip.hidden = true; };
  addEventListener('scroll', hideTip, { passive: true });

  function niceMax(v) {
    if (v <= 0) return 1;
    const p = 10 ** Math.floor(Math.log10(v)), n = v / p;
    return (n <= 1 ? 1 : n <= 2 ? 2 : n <= 2.5 ? 2.5 : n <= 5 ? 5 : 10) * p;
  }
  function el(tag, attrs, parent) {
    const e = document.createElementNS(NS, tag);
    for (const k in attrs) e.setAttribute(k, attrs[k]);
    if (parent) parent.appendChild(e);
    return e;
  }
  function empty(host, msg) {
    host.innerHTML = `<div class="empty-chart">${esc(msg || 'Sem dados no período selecionado.')}</div>`;
  }
  function legend(host, series) {
    if (series.length < 2) return;
    const d = document.createElement('div');
    d.className = 'chart-legend';
    d.innerHTML = series.map(s => `<span><i style="background:${s.color}"></i>${esc(s.name)}</span>`).join('');
    host.appendChild(d);
  }
  function tipRows(title, series, i, fmt) {
    return `<b>${esc(title)}</b>` + series.map(s =>
      `<div class="row"><span><i style="background:${s.color}"></i>${esc(s.name)}</span><span>${fmt(s.values[i])}</span></div>`).join('');
  }
  // barra com ponta arredondada e base reta na linha do zero
  function barPath(x, w, y0, y1, r) {
    const h = Math.abs(y1 - y0); r = Math.min(r, w / 2, h);
    const d = y1 < y0 ? 1 : -1;
    return `M${x},${y0}V${y1 + d * r}Q${x},${y1} ${x + r},${y1}H${x + w - r}Q${x + w},${y1} ${x + w},${y1 + d * r}V${y0}Z`;
  }
  function axis(svg, w, h, m, min, max, fmtAxis) {
    const n = 4, g = el('g', {}, svg);
    for (let k = 0; k <= n; k++) {
      const v = min + (max - min) * k / n, y = m.t + (h - m.t - m.b) * (1 - k / n);
      el('line', { x1: m.l, x2: w - m.r, y1: y, y2: y, stroke: v === 0 ? 'rgba(241,232,217,.22)' : 'rgba(241,232,217,.06)' }, g);
      const t = el('text', { x: m.l - 8, y: y + 4, 'text-anchor': 'end', 'font-size': 11, fill: '#9C8C7A' }, g);
      t.textContent = fmtAxis(v);
    }
  }
  function xLabels(svg, labels, xs, h, m, innerW) {
    const step = Math.max(1, Math.ceil(labels.length * 52 / innerW));
    labels.forEach((l, i) => {
      if (i % step && i !== labels.length - 1) return;
      const t = el('text', { x: xs(i), y: h - m.b + 18, 'text-anchor': 'middle', 'font-size': 11, fill: '#9C8C7A' }, svg);
      t.textContent = l;
    });
  }

  /* barras agrupadas: { labels, series:[{name,color,values}], fmt, fmtAxis, height } */
  function bars(host, o) {
    const all = o.series.flatMap(s => s.values);
    if (!all.some(v => v)) return empty(host, o.empty);
    host.innerHTML = '';
    const w = Math.max(280, host.clientWidth), h = o.height || 260, m = { t: 12, r: 8, b: 30, l: o.left || 74 };
    const max = niceMax(Math.max(0, ...all)), min = Math.min(0, ...all) < 0 ? -niceMax(-Math.min(...all)) : 0;
    const svg = el('svg', { viewBox: `0 0 ${w} ${h}`, role: 'img', 'aria-label': o.label || '' }, null);
    host.appendChild(svg);
    axis(svg, w, h, m, min, max, o.fmtAxis || o.fmt);
    const innerW = w - m.l - m.r, band = innerW / o.labels.length, n = o.series.length;
    const y = v => m.t + (h - m.t - m.b) * (max - v) / (max - min), y0 = y(0);
    const gw = Math.min(band * 0.72, 18 * n + 6 * (n - 1) + 8), bw = Math.max(2, (gw - 2 * (n - 1)) / n);
    const hl = el('rect', { y: m.t, height: h - m.t - m.b, width: band, fill: 'rgba(241,232,217,.04)', opacity: 0 }, svg);
    o.labels.forEach((lab, i) => {
      const gx = m.l + i * band + (band - gw) / 2;
      o.series.forEach((s, k) => {
        const v = s.values[i] || 0;
        if (!v) return;
        el('path', { d: barPath(gx + k * (bw + 2), bw, y0, y(v), 3), fill: s.color }, svg);
      });
      const hit = el('rect', { x: m.l + i * band, y: m.t, width: band, height: h - m.t - m.b, fill: 'transparent' }, svg);
      const html = tipRows(lab, o.series, i, o.fmt);
      hit.addEventListener('pointermove', ev => { hl.setAttribute('x', m.l + i * band); hl.setAttribute('opacity', 1); showTip(html, ev); });
      hit.addEventListener('pointerleave', () => { hl.setAttribute('opacity', 0); hideTip(); });
    });
    xLabels(svg, o.labels, i => m.l + i * band + band / 2, h, m, innerW);
    legend(host, o.series);
  }

  /* linhas: { labels, series, fmt, fmtAxis, height } */
  function line(host, o) {
    const all = o.series.flatMap(s => s.values);
    if (!all.length || (!all.some(v => v) && !o.allowZero)) return empty(host, o.empty);
    host.innerHTML = '';
    const w = Math.max(280, host.clientWidth), h = o.height || 260, m = { t: 12, r: 12, b: 30, l: o.left || 74 };
    const max = niceMax(Math.max(1, ...all)), min = 0;
    const svg = el('svg', { viewBox: `0 0 ${w} ${h}`, role: 'img', 'aria-label': o.label || '' }, null);
    host.appendChild(svg);
    axis(svg, w, h, m, min, max, o.fmtAxis || o.fmt);
    const innerW = w - m.l - m.r, n = o.labels.length;
    const x = i => m.l + (n === 1 ? innerW / 2 : innerW * i / (n - 1));
    const y = v => m.t + (h - m.t - m.b) * (max - v) / (max - min);
    o.series.forEach(s => {
      el('polyline', { points: s.values.map((v, i) => `${x(i)},${y(v)}`).join(' '), fill: 'none', stroke: s.color,
                       'stroke-width': 2, 'stroke-linejoin': 'round', 'stroke-linecap': 'round' }, svg);
      if (n <= 31) s.values.forEach((v, i) => el('circle', { cx: x(i), cy: y(v), r: 2.5, fill: s.color }, svg));
    });
    const cross = el('line', { y1: m.t, y2: h - m.b, stroke: 'rgba(241,232,217,.25)', 'stroke-dasharray': '3 3', opacity: 0 }, svg);
    const dots = o.series.map(s => el('circle', { r: 5, fill: s.color, stroke: '#1B130F', 'stroke-width': 2, opacity: 0 }, svg));
    const hit = el('rect', { x: m.l, y: m.t, width: innerW, height: h - m.t - m.b, fill: 'transparent' }, svg);
    hit.addEventListener('pointermove', ev => {
      const r = svg.getBoundingClientRect(), px = (ev.clientX - r.left) * (w / r.width);
      const i = Math.max(0, Math.min(n - 1, Math.round((px - m.l) / (innerW / Math.max(1, n - 1)))));
      cross.setAttribute('x1', x(i)); cross.setAttribute('x2', x(i)); cross.setAttribute('opacity', 1);
      o.series.forEach((s, k) => { dots[k].setAttribute('cx', x(i)); dots[k].setAttribute('cy', y(s.values[i])); dots[k].setAttribute('opacity', 1); });
      showTip(tipRows(o.labels[i], o.series, i, o.fmt), ev);
    });
    hit.addEventListener('pointerleave', () => { cross.setAttribute('opacity', 0); dots.forEach(d => d.setAttribute('opacity', 0)); hideTip(); });
    xLabels(svg, o.labels, x, h, m, innerW);
    legend(host, o.series);
  }

  /* barras horizontais em HTML (legíveis no celular): { items:[{label,value,color,sub}], fmt } */
  function hbars(host, o) {
    if (!o.items.some(i => i.value)) return empty(host, o.empty);
    const max = Math.max(...o.items.map(i => Math.abs(i.value))) || 1;
    host.innerHTML = '<div class="hb">' + o.items.map(i => `
      <div class="hb-row" title="${esc(i.label)}: ${esc(o.fmt(i.value))}">
        <span class="hb-lbl">${esc(i.label)}${i.sub ? `<small>${esc(i.sub)}</small>` : ''}</span>
        <span class="hb-track"><i style="width:${Math.max(i.value ? 1.5 : 0, Math.abs(i.value) / max * 100)}%;background:${i.color || '#C79A55'}"></i></span>
        <span class="hb-val">${esc(o.fmt(i.value))}</span>
      </div>`).join('') + '</div>';
  }

  return { bars, line, hbars, empty, hideTip };
})();
