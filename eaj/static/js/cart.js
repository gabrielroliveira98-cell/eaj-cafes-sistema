/* Carrinho da loja.
 * O navegador guarda apenas { product_id, quantity }. Preços, subtotais, total e disponibilidade
 * vêm sempre do servidor (/api/carrinho), que é quem valida o pedido.
 */
(() => {
  const KEY = 'eaj_cart_v1';
  const csrf = document.querySelector('meta[name="csrf-token"]')?.content || '';
  const brl = c => (c < 0 ? '−' : '') + 'R$ ' + (Math.abs(c) / 100).toLocaleString('pt-BR', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  const esc = s => String(s).replace(/[&<>"']/g, ch => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[ch]));

  function read() {
    try { const v = JSON.parse(localStorage.getItem(KEY) || '[]'); return Array.isArray(v) ? v : []; }
    catch { return []; }
  }
  function write(items) {
    try { localStorage.setItem(KEY, JSON.stringify(items)); } catch { /* armazenamento indisponível */ }
    window.dispatchEvent(new CustomEvent('cart:change'));
  }
  function setQty(pid, qty) {
    const items = read().filter(i => i.product_id !== pid);
    if (qty > 0) items.push({ product_id: pid, quantity: qty });
    write(items);
  }
  function add(pid, qty) {
    const items = read();
    const it = items.find(i => i.product_id === pid);
    if (it) it.quantity += qty; else items.push({ product_id: pid, quantity: qty });
    write(items);
  }

  let lastQuote = null, pending = null;
  async function quote() {
    const items = read();
    if (!items.length) return (lastQuote = { lines: [], subtotal_cents: 0, problems: [] });
    if (pending) pending.abort();
    pending = new AbortController();
    const r = await fetch('/api/carrinho', {
      method: 'POST', signal: pending.signal,
      headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf },
      body: JSON.stringify({ items })
    });
    if (!r.ok) throw new Error('Não foi possível atualizar o carrinho.');
    const q = await r.json();
    // o servidor pode ter ajustado quantidades (estoque) ou removido itens inativos
    const fixed = q.lines.map(l => ({ product_id: l.product_id, quantity: l.quantity }));
    if (JSON.stringify(fixed) !== JSON.stringify(items)) {
      try { localStorage.setItem(KEY, JSON.stringify(fixed)); } catch {}
    }
    return (lastQuote = q);
  }

  // ---------- contador ----------
  const countEl = document.getElementById('cart-count');
  function paintCount(bump) {
    const n = read().reduce((a, i) => a + i.quantity, 0);
    if (!countEl) return;
    countEl.textContent = n;
    countEl.setAttribute('aria-label', `${n} ${n === 1 ? 'item' : 'itens'} no carrinho`);
    if (bump) { countEl.classList.remove('bump'); void countEl.offsetWidth; countEl.classList.add('bump'); }
  }

  // ---------- gaveta ----------
  const drawer = document.getElementById('cart'), shade = document.getElementById('cart-shade');
  const openBtn = document.getElementById('cart-open'), closeBtn = document.getElementById('cart-close');
  const body = document.getElementById('cart-body'), foot = document.getElementById('cart-foot');
  let lastFocus = null;

  function lineHTML(l, compact) {
    return `<div class="line" data-pid="${l.product_id}">
      <img src="/static/${esc(l.image)}" alt="" width="64" height="64" loading="lazy">
      <div><h3>${esc(l.name)}</h3><p class="muted">${brl(l.unit_price_cents)} cada</p>
        ${compact ? `<p class="muted">Quantidade: ${l.quantity}</p>` : `<div class="qty qty-sm" role="group" aria-label="Quantidade de ${esc(l.name)}">
          <button type="button" data-d="-1" aria-label="Diminuir">−</button>
          <input type="number" min="1" max="${Math.min(l.available, 50)}" value="${l.quantity}" aria-label="Quantidade">
          <button type="button" data-d="1" aria-label="Aumentar">+</button></div>`}
      </div>
      <div class="line-end"><b>${brl(l.line_total_cents)}</b>
        ${compact ? '' : `<button class="link small" type="button" data-remove aria-label="Remover ${esc(l.name)} do carrinho">Remover</button>`}</div>
    </div>`;
  }

  async function renderDrawer() {
    if (!body) return;
    body.innerHTML = '<p class="cart-empty"><span class="spin" aria-hidden="true"></span> Atualizando…</p>';
    try {
      const q = await quote();
      if (!q.lines.length) {
        body.innerHTML = `<div class="cart-empty"><p>Seu carrinho está vazio.</p><p><a class="btn btn-sm" href="/#loja">Ver os cafés</a></p></div>` +
          (q.problems.length ? `<p class="cart-note">${q.problems.map(esc).join(' ')}</p>` : '');
        foot.hidden = true;
      } else {
        body.innerHTML = (q.problems.length ? `<p class="cart-note">${[...new Set(q.problems)].map(esc).join(' ')}</p>` : '') +
          q.lines.map(l => lineHTML(l, false)).join('');
        document.getElementById('cart-subtotal').textContent = brl(q.subtotal_cents);
        foot.hidden = false;
      }
    } catch (e) {
      if (e.name === 'AbortError') return;
      body.innerHTML = `<p class="cart-note">${esc(e.message)} Verifique sua conexão e tente de novo.</p>`;
    }
    paintCount(false);
  }

  function open() {
    if (!drawer) return;
    lastFocus = document.activeElement;
    shade.hidden = false;
    drawer.classList.add('open');
    drawer.setAttribute('aria-hidden', 'false');
    openBtn?.setAttribute('aria-expanded', 'true');
    document.body.style.overflow = 'hidden';
    renderDrawer();
    setTimeout(() => closeBtn?.focus(), 50);
  }
  function close() {
    if (!drawer || !drawer.classList.contains('open')) return;
    drawer.classList.remove('open');
    drawer.setAttribute('aria-hidden', 'true');
    openBtn?.setAttribute('aria-expanded', 'false');
    shade.hidden = true;
    document.body.style.overflow = '';
    lastFocus?.focus?.();
  }
  openBtn?.addEventListener('click', open);
  closeBtn?.addEventListener('click', close);
  shade?.addEventListener('click', close);
  document.addEventListener('keydown', e => {
    if (e.key === 'Escape') close();
    if (e.key === 'Tab' && drawer?.classList.contains('open')) {        // mantém o foco dentro da gaveta
      const f = [...drawer.querySelectorAll('button, a[href], input')].filter(x => !x.closest('[hidden]'));
      if (!f.length) return;
      if (e.shiftKey && document.activeElement === f[0]) { e.preventDefault(); f[f.length - 1].focus(); }
      else if (!e.shiftKey && document.activeElement === f[f.length - 1]) { e.preventDefault(); f[0].focus(); }
    }
  });

  let qtyTimer;
  body?.addEventListener('click', e => {
    const line = e.target.closest('.line'); if (!line) return;
    const pid = +line.dataset.pid, input = line.querySelector('input');
    if (e.target.closest('[data-remove]')) { setQty(pid, 0); renderDrawer(); return; }
    const d = e.target.closest('[data-d]');
    if (d && input) {
      const v = Math.max(0, Math.min(+input.max || 50, (+input.value || 1) + +d.dataset.d));
      input.value = v;
      clearTimeout(qtyTimer);
      qtyTimer = setTimeout(() => { setQty(pid, v); renderDrawer(); }, 250);
    }
  });
  body?.addEventListener('change', e => {
    const line = e.target.closest('.line'); if (!line || e.target.tagName !== 'INPUT') return;
    const v = Math.max(0, Math.min(+e.target.max || 50, parseInt(e.target.value, 10) || 0));
    setQty(+line.dataset.pid, v); renderDrawer();
  });

  // ---------- botões "adicionar" ----------
  function toast(msg) {
    const t = document.createElement('p');
    t.className = 'toast'; t.setAttribute('role', 'status'); t.textContent = msg;
    document.body.appendChild(t);
    setTimeout(() => t.remove(), 2600);
  }
  document.querySelectorAll('.qty:not(.qty-sm)').forEach(q => {
    const input = q.querySelector('input');
    q.addEventListener('click', e => {
      const b = e.target.closest('[data-step]'); if (!b) return;
      input.value = Math.max(1, Math.min(+input.max || 50, (+input.value || 1) + +b.dataset.step));
    });
    input.addEventListener('change', () => { input.value = Math.max(1, Math.min(+input.max || 50, parseInt(input.value, 10) || 1)); });
  });
  document.querySelectorAll('[data-product] .add-cart').forEach(btn => btn.addEventListener('click', () => {
    const card = btn.closest('[data-product]');
    const p = JSON.parse(card.dataset.product);
    const q = parseInt(card.querySelector('.qty input')?.value, 10) || 1;
    const inCart = read().find(i => i.product_id === p.id)?.quantity || 0;
    if (inCart + q > p.available) {
      toast(`Só há ${p.available} unidade(s) disponíveis de ${p.name}.`);
      return;
    }
    add(p.id, q);
    paintCount(true);
    toast(`${q}× ${p.name} adicionado ao carrinho.`);
  }));

  window.addEventListener('storage', e => { if (e.key === KEY) paintCount(false); });
  window.EAJCart = { read, write, quote, brl, esc, lineHTML, clear: () => write([]) };
  paintCount(false);

  // avisos somem sozinhos
  document.querySelectorAll('.flash').forEach(f => setTimeout(() => { f.classList.add('out'); setTimeout(() => f.remove(), 450); }, 6000));
})();
