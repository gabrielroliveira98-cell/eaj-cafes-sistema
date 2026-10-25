/* Experiência cinematográfica controlada pela rolagem.
 *
 * - A seção .cine é alta; o palco interno fica preso com position: sticky.
 * - O progresso da rolagem (0 a 1) vira o índice do quadro. Com a sequência completa do vídeo
 *   (dezenas de quadros), cada posição mostra um quadro real, como um vídeo. Com poucos quadros,
 *   o canvas funde dois quadros vizinhos para suavizar.
 * - Versão das imagens escolhida pela tela: "pt" (recorte vertical nativo) no celular em pé,
 *   "md" em telas menores ou conexões econômicas, "lg" (resolução nativa) nas demais.
 * - Carregamento progressivo: primeiro uma amostra espalhada pela sequência, depois os intervalos,
 *   com vários downloads em paralelo. Enquanto um quadro não chega, mostra o mais próximo.
 * - Com prefers-reduced-motion, a seção vira uma sequência estática de imagens e textos.
 */
(() => {
  const sec = document.querySelector('.cine');
  if (!sec) return;

  const reduce = matchMedia('(prefers-reduced-motion: reduce)');
  if (reduce.matches) { sec.classList.add('static'); return; }

  const stage = sec.querySelector('.cine-stage');
  const canvas = sec.querySelector('.cine-canvas');
  const ctx = canvas.getContext('2d', { alpha: false });
  const chaps = [...sec.querySelectorAll('.chap')];
  const rail = [...sec.querySelectorAll('.cine-rail span')];
  const loader = document.getElementById('cine-loader');
  const head = document.getElementById('site-head');
  const base = sec.dataset.static;
  const lists = {
    lg: (sec.dataset.framesLg || '').split('|').filter(Boolean),
    md: (sec.dataset.framesMd || '').split('|').filter(Boolean),
    pt: (sec.dataset.framesPt || '').split('|').filter(Boolean),
  };
  const N = lists.lg.length;
  if (!N) return;
  const BLEND = N < 60;                 // poucos quadros: funde vizinhos; sequência de vídeo: quadro exato
  const PARALLEL = 6;

  // ---------- escolha da versão ----------
  const conn = navigator.connection || {};
  const slow = conn.saveData || /(^|-)2g|3g/.test(conn.effectiveType || '');
  function pickVariant() {
    const w = stage.clientWidth, h = stage.clientHeight, dpr = Math.min(devicePixelRatio || 1, 2);
    if (lists.pt.length === N && w / h < 0.8) return 'pt';
    if (lists.md.length === N && (slow || w * dpr <= 1100)) return 'md';
    return 'lg';
  }

  // ---------- carregamento ----------
  let variant = null, frames = [], queued = [], loaded = 0, inflight = 0, generation = 0;
  function priorityOrder() {
    const out = [], seen = new Set();
    const push = i => { if (i >= 0 && i < N && !seen.has(i)) { seen.add(i); out.push(i); } };
    push(0); push(N - 1);
    for (let step = 2 ** Math.ceil(Math.log2(N)); step >= 1; step /= 2)
      for (let i = 0; i < N; i += step) push(i);
    return out;
  }
  function startVariant(v) {
    variant = v; generation++;
    frames = new Array(N).fill(null);
    loaded = 0; inflight = 0;
    queued = priorityOrder();
    pump();
  }
  function pump() {
    while (inflight < PARALLEL && queued.length) load(queued.shift());
  }
  function prioritize(center) {          // traz para a frente os quadros perto da posição atual
    const near = [];
    for (let d = 0; d <= 8; d++) [center + d, center - d].forEach(i => {
      if (i >= 0 && i < N && !frames[i] && queued.includes(i)) near.push(i);
    });
    if (near.length) { queued = [...new Set([...near, ...queued])]; pump(); }
  }
  function load(i) {
    const gen = generation, img = new Image();
    img.decoding = 'async';
    inflight++;
    const done = ok => {
      if (gen !== generation) return;          // troca de versão no meio do caminho
      inflight--;
      if (ok) frames[i] = img;
      loaded++;
      updateLoader();
      if (Math.abs(i - Math.round(current * (N - 1))) <= 2) draw(true);
      if (i === 0) sec.classList.add('ready');
      pump();
    };
    // onload é o sinal confiável; decode() só antecipa a decodificação e tem tempo limite
    img.onload = () => Promise.race([img.decode ? img.decode().catch(() => {}) : 0, new Promise(r => setTimeout(r, 300))])
                              .then(() => done(true));
    img.onerror = () => done(false);
    img.src = base + lists[variant][i];
  }
  let loaderTimer = setTimeout(() => { if (loaded < N) loader.hidden = false; }, 900);
  function updateLoader() {
    if (loaded >= N) { clearTimeout(loaderTimer); loader.hidden = true; return; }
    loader.lastElementChild.textContent = `Carregando ${Math.round(loaded * 100 / N)}%`;
  }
  function nearest(i) {
    if (frames[i]) return frames[i];
    for (let d = 1; d < N; d++) {
      if (frames[i - d]) return frames[i - d];
      if (frames[i + d]) return frames[i + d];
    }
    return null;
  }

  // ---------- desenho ----------
  let cw = 0, ch = 0;
  function resize() {
    const dpr = Math.min(devicePixelRatio || 1, 2);
    cw = Math.round(stage.clientWidth * dpr);
    ch = Math.round(stage.clientHeight * dpr);
    canvas.width = cw; canvas.height = ch;
    ctx.imageSmoothingEnabled = true;
    ctx.imageSmoothingQuality = 'high';
    const v = pickVariant();
    if (v !== variant) startVariant(v);
    draw(true);
  }
  function cover(img, alpha) {
    const iw = img.naturalWidth, ih = img.naturalHeight;
    const s = Math.max(cw / iw, ch / ih);                // preenche sem distorcer
    const w = iw * s, h = ih * s;
    const fx = variant === 'pt' ? 0.5 : (cw < ch ? 0.56 : 0.5);
    ctx.globalAlpha = alpha;
    ctx.drawImage(img, (cw - w) * fx, (ch - h) / 2, w, h);
  }

  let target = 0, current = 0, lastKey = '';
  function progress() {
    const r = sec.getBoundingClientRect();
    const span = r.height - innerHeight;
    return span > 0 ? Math.min(1, Math.max(0, -r.top / span)) : 0;
  }
  function draw(force) {
    if (!cw) return;
    const f = current * (N - 1);
    let a, b = null, t = 0;
    if (BLEND) {
      const i = Math.floor(f);
      t = f - i;
      a = nearest(i);
      if (t > 0.01) b = nearest(Math.min(N - 1, i + 1));
    } else {
      a = nearest(Math.round(f));
    }
    if (!a) return;
    const key = a.src + '|' + (b ? b.src + t.toFixed(2) : '');
    if (!force && key === lastKey) return;               // mesmo quadro: não redesenha
    lastKey = key;
    ctx.globalAlpha = 1;
    ctx.fillStyle = '#0E0B09';
    ctx.fillRect(0, 0, cw, ch);
    cover(a, 1);
    if (b && b !== a) cover(b, t);
    ctx.globalAlpha = 1;
  }

  // ---------- textos sincronizados ----------
  let active = -1;
  function syncCopy(p) {
    let idx = 0;
    chaps.forEach((c, k) => { if (p >= parseFloat(c.dataset.from)) idx = k; });
    if (idx !== active) {
      chaps.forEach((c, k) => { c.classList.toggle('on', k === idx); c.classList.toggle('past', k < idx); });
      active = idx;
    }
    rail.forEach((r, k) => {
      const from = parseFloat(chaps[k].dataset.from), to = Math.min(1, parseFloat(chaps[k].dataset.to));
      r.style.setProperty('--f', Math.min(1, Math.max(0, (p - from) / (to - from))).toFixed(3));
    });
    stage.style.setProperty('--end', Math.max(0, (p - 0.93) / 0.07).toFixed(3));   // escurece até a loja
    sec.classList.toggle('scrolled', p > 0.015);
  }

  // ---------- laço ----------
  let raf = 0, lastT = 0;
  function tick(t) {
    const dt = lastT ? Math.min(64, t - lastT) : 16.7;
    lastT = t;
    current += (target - current) * (1 - Math.pow(1 - 0.22, dt / 16.7));   // suavização independente do fps
    if (Math.abs(target - current) < 0.0004) current = target;
    draw(false);
    if (current !== target) raf = requestAnimationFrame(tick);
    else { raf = 0; lastT = 0; }
  }
  function onScroll() {
    target = progress();
    syncCopy(target);
    prioritize(Math.round(target * (N - 1)));
    if (head) head.classList.toggle('solid', sec.getBoundingClientRect().bottom <= 80);
    if (!raf) raf = requestAnimationFrame(tick);
  }

  let visible = true;
  new IntersectionObserver(es => { visible = es[0].isIntersecting; if (visible) onScroll(); }).observe(sec);
  addEventListener('scroll', () => { if (visible) onScroll(); else if (head) head.classList.add('solid'); }, { passive: true });
  let rt;
  addEventListener('resize', () => { clearTimeout(rt); rt = setTimeout(() => { resize(); onScroll(); }, 120); });
  reduce.addEventListener('change', e => { if (e.matches) { sec.classList.add('static'); cancelAnimationFrame(raf); } });

  resize();
  target = current = progress();
  syncCopy(target);
  draw(true);
  if (head) head.classList.toggle('solid', sec.getBoundingClientRect().bottom <= 80);
})();
