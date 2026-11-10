// Editor de pedidos: carrito en el navegador; el servidor valida y es la autoridad de totales y existencias.
(function () {
  const boot = JSON.parse(document.getElementById('boot').textContent);
  const { catalog, promotions, recipes, methods } = boot;
  let order = boot.order;
  let lines = order.items.map(i => ({ ...i }));
  let dirty = false, busy = false, printed = null;   // printed: línea recién agregada
  const $ = id => document.getElementById(id);

  const norm = t => t.normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLowerCase();
  const money = c => 'Bs ' + String(Math.floor(c / 100)).replace(/\B(?=(\d{3})+(?!\d))/g, '.') + (c % 100 ? ',' + String(c % 100).padStart(2, '0') : '');
  function parseBs(text) {                       // solo para la vista previa; el servidor recalcula
    const t = text.trim().toLowerCase().replace(/bs|\s/g, '');
    const s = t.includes(',') && t.includes('.') ? t.replace(/\./g, '').replace(',', '.')
      : /^\d{1,3}(\.\d{3})+$/.test(t) ? t.replace(/\./g, '') : t.replace(',', '.');
    if (!/^\d+(\.\d{1,2})?$/.test(s)) return t === '' ? 0 : null;
    const [w, f = ''] = s.split('.'); return Number(w) * 100 + Number(f.padEnd(2, '0'));
  }
  function h(tag, props, ...kids) {
    const e = document.createElement(tag);
    for (const [k, v] of Object.entries(props || {})) {
      if (k === 'class') e.className = v;
      else if (k.startsWith('on')) e.addEventListener(k.slice(2), v);
      else if (v !== false && v != null) e.setAttribute(k, v);
    }
    e.append(...kids.flat().filter(x => x != null && x !== false));
    return e;
  }
  function showError(msg) { const m = $('mensaje'); m.textContent = msg; m.hidden = !msg; if (msg) m.scrollIntoView({ block: 'nearest' }); }
  function touch() { dirty = true; render(); }
  const readOnly = () => order.status === 'cancelled' || (order.id && !order.workday_open);

  // ---------- selector ----------
  const COMBOS = 'Combos';
  const cats = ['', ...(promotions.length ? [COMBOS] : []), ...new Set(catalog.map(c => c.category))];
  let cat = '';
  function renderCats() {
    $('cats').replaceChildren(...cats.map(c => h('button', { type: 'button', class: 'tab' + (cat === c ? ' activa' : ''), 'aria-pressed': String(cat === c),
      onclick: () => { cat = c; renderCats(); renderPicker(); } }, c || 'Todas')));
  }
  $('filtro').addEventListener('input', renderPicker);

  function chip(av, stock) {
    const txt = { agotado: 'Agotado', bajo: 'Quedan ' + stock, ok: stock + ' disp.', sin_control: '' }[av];
    return txt ? h('span', { class: 'chip ' + (av === 'sin_control' ? 'neutro' : av) }, txt) : null;
  }
  function renderPicker() {
    const q = norm($('filtro').value), box = $('lista-productos');
    box.replaceChildren();
    if (!cat || cat === COMBOS) promotions.filter(p => !q || norm(p.name).includes(q)).forEach(p => box.append(comboCard(p)));
    if (cat !== COMBOS) catalog.filter(c => (!cat || c.category === cat) && (!q || norm(c.name).includes(q))).forEach(c => {
      const out = c.availability === 'agotado';
      box.append(h('div', { class: 'prod' + (out ? ' off' : '') + (c.recipe_id ? ' tiene-receta' : '') },
        h('button', { type: 'button', class: 'prod-add', disabled: out || readOnly(), onclick: () => addVariant(c) },
          c.photo ? h('img', { class: 'prod-foto', src: '/media/' + c.photo, alt: '' }) : null,
          h('span', { class: 'prod-nombre' }, c.name),
          h('span', { class: 'prod-pie' }, h('span', { class: 'prod-precio' }, money(c.price_cents)), chip(c.availability, c.stock))),
        c.recipe_id ? h('button', { type: 'button', class: 'prod-receta', 'aria-label': 'Ver receta de ' + c.name, onclick: () => showRecipe(c.recipe_id) }, 'Receta') : null));
    });
    if (!box.children.length) box.append(h('p', { class: 'vacio' }, q ? 'Ningún producto coincide con la búsqueda.' : 'No hay productos en esta categoría.'));
  }
  function comboCard(p) {
    const ready = p.slots.length && p.slots.every(s => s.options.length);
    const label = s => s.name + (s.quantity > 1 ? ' ×' + s.quantity : '');
    return h('div', { class: 'prod combo' },
      h('button', { type: 'button', class: 'prod-add', disabled: !ready || readOnly(), onclick: () => openCombo(p) },
        h('span', { class: 'prod-nombre' }, p.name, h('small', { class: 'suave' }, ready ? 'Elegir ' + p.slots.map(label).join(' + ').toLowerCase() : 'Sin opciones configuradas')),
        h('span', { class: 'prod-pie' }, h('span', { class: 'prod-precio' }, money(p.price_cents)))),
      ready ? null : h('a', { class: 'btn sec peq', href: '/promociones/' + p.id }, 'Configurar'));
  }

  // ---------- armar un combo: una sección por elección, con pestañas por categoría ----------
  function openCombo(p) {
    const dlg = $('combo-dialog');
    const pick = {};          // slot.id -> [variant_id, ...] (tantas como slot.quantity)
    const tab = {};           // slot.id -> categoría visible
    const find = {};          // slot.id -> texto del buscador (solo si hay muchas opciones)
    let focusSlot = null;
    p.slots.forEach(s => {
      pick[s.id] = [];
      const avail = s.options.filter(o => o.availability !== 'agotado');
      if (s.quantity === 1 && avail.length === 1 && s.options.length === 1) pick[s.id] = [avail[0].variant_id];
      tab[s.id] = s.options[0] ? s.options[0].category : '';
    });
    const optName = (s, vid) => (s.options.find(o => o.variant_id === vid) || {}).name;
    const complete = () => p.slots.every(s => pick[s.id].length === s.quantity);

    function choose(s, o) {
      const list = pick[s.id];
      const at = list.indexOf(o.variant_id);
      if (s.quantity === 1) pick[s.id] = at >= 0 ? [] : [o.variant_id];
      else if (list.length < s.quantity) list.push(o.variant_id);
      else { list.shift(); list.push(o.variant_id); }
      draw();
      if (s.quantity === 1 && pick[s.id].length) {          // pasa sola a la siguiente elección pendiente
        const nextSlot = p.slots.find(x => pick[x.id].length < x.quantity);
        if (nextSlot) setTimeout(() => dlg.querySelector('[data-slot="' + nextSlot.id + '"]')?.scrollIntoView({ block: 'nearest', behavior: 'smooth' }), 0);
      }
    }
    function slotSection(s) {
      const cats = [...new Set(s.options.map(o => o.category))];
      const chosen = pick[s.id];
      const head = h('div', { class: 'paso-cabeza' },
        h('h3', null, s.name + (s.quantity > 1 ? ' ×' + s.quantity : '')),
        chosen.length === s.quantity
          ? h('span', { class: 'chip ok' }, chosen.map(v => optName(s, v)).join(', '))
          : h('span', { class: 'chip bajo' }, s.quantity > 1 ? 'Elige ' + (s.quantity - chosen.length) + ' más' : 'Elige uno'));
      const tabs = cats.length > 1 ? h('div', { class: 'secciones-tabs', role: 'tablist', 'aria-label': 'Secciones de ' + s.name },
        cats.map(c => h('button', { type: 'button', role: 'tab', class: 'tab' + (tab[s.id] === c ? ' activa' : ''),
          'aria-selected': String(tab[s.id] === c), onclick: () => { tab[s.id] = c; draw(); } },
          c, h('small', null, String(s.options.filter(o => o.category === c).length))))) : null;
      const many = s.options.length > 12, q = norm(find[s.id] || '');
      const search = many ? h('input', { type: 'search', class: 'buscar-opcion', placeholder: 'Buscar en ' + s.name.toLowerCase() + '…',
        'aria-label': 'Buscar en ' + s.name, value: find[s.id] || '', 'data-find': s.id }) : null;
      if (search) search.addEventListener('input', () => { find[s.id] = search.value; focusSlot = s.id; draw(); });
      const visible = s.options.filter(o => q ? norm(o.name).includes(q) : (cats.length < 2 || o.category === tab[s.id]));
      const grid = h('div', { class: 'opciones-combo' }, visible.length ? visible.map(o => {
        const n = chosen.filter(v => v === o.variant_id).length, out = o.availability === 'agotado';
        const pic = o.photo ? h('img', { class: 'mini', src: '/media/' + o.photo, alt: '' })
                            : h('span', { class: 'mini sin-foto' }, (o.name.match(/[\p{L}\p{N}]/u) || ['·'])[0].toUpperCase());
        return h('button', { type: 'button', class: 'opcion' + (n ? ' elegida' : ''), disabled: out, 'aria-pressed': String(n > 0),
                             onclick: () => choose(s, o) },
          pic, h('span', { class: 'grow' }, h('strong', null, o.name),
            h('small', null, out ? 'Agotado' : o.availability === 'bajo' ? 'Quedan ' + o.stock : o.availability === 'ok' ? o.stock + ' disp.' : '')),
          n > 1 ? h('span', { class: 'chip neutro' }, '×' + n) : null);
      }) : h('p', { class: 'suave' }, 'Nada coincide con «' + find[s.id] + '».'));
      return h('section', { class: 'paso' + (chosen.length === s.quantity ? ' listo' : ''), 'data-slot': s.id },
               head, search, q ? null : tabs, grid);
    }
    function draw() {
      $('combo-titulo').textContent = p.name;
      $('combo-precio').textContent = money(p.price_cents) + ' en total, se cobra una sola vez';
      $('combo-pasos').replaceChildren(...p.slots.map(slotSection));
      const names = p.slots.flatMap(s => pick[s.id].map(v => optName(s, v)));
      $('combo-resumen').textContent = complete() ? names.join(' + ') : 'Falta elegir: ' +
        p.slots.filter(s => pick[s.id].length < s.quantity).map(s => s.name.toLowerCase()).join(' y ');
      $('combo-agregar').disabled = !complete();
      if (focusSlot !== null) {                 // el buscador se redibuja: devolverle el foco y el cursor
        const input = dlg.querySelector('[data-find="' + focusSlot + '"]');
        if (input) { input.focus(); input.setSelectionRange(input.value.length, input.value.length); }
        focusSlot = null;
      }
    }
    $('combo-agregar').onclick = () => {
      if (!complete()) return;
      lines.push({ promotion_id: p.id, name: p.name, quantity: 1, unit_price_cents: p.price_cents,
                   selections: Object.fromEntries(p.slots.map(s => [String(s.id), pick[s.id].slice()])),
                   components_text: p.slots.flatMap(s => pick[s.id].map(v => optName(s, v))).join(' + ') });
      printed = lines.length - 1; dlg.close(); showError(''); touch();
    };
    $('combo-cerrar').onclick = () => dlg.close();
    draw(); dlg.showModal();
  }
  function showRecipe(id) {
    $('receta-titulo').textContent = recipes[id].name;
    $('receta-pasos').replaceChildren(...recipes[id].steps.map(s => h('li', null, s)));
    $('receta-dialog').showModal();
  }
  function addVariant(c) {
    const line = lines.find(l => l.variant_id === c.variant_id);
    if (line) line.quantity = Math.min(999, line.quantity + 1);
    else { lines.push({ variant_id: c.variant_id, name: c.name, quantity: 1, unit_price_cents: c.price_cents }); printed = lines.length - 1; }
    showError(''); touch();
  }

  // ---------- carrito ----------
  function render() {
    const ro = readOnly();
    $('lineas').replaceChildren(...lines.map((l, i) => h('li', { class: 'linea' + (i === printed ? ' nueva' : '') },
      h('div', { class: 'grow' }, h('strong', null, l.name), l.components_text ? h('small', { class: 'suave' }, l.components_text) : null,
        h('small', { class: 'suave' }, money(l.unit_price_cents) + (l.promotion_id ? '' : ' c/u'))),
      l.promotion_id ? null : h('span', { class: 'cant' },
        h('button', { type: 'button', 'aria-label': 'Menos', disabled: ro, onclick: () => { l.quantity = Math.max(1, l.quantity - 1); touch(); } }, '−'),
        h('output', null, String(l.quantity)),
        h('button', { type: 'button', 'aria-label': 'Más', disabled: ro, onclick: () => { l.quantity = Math.min(999, l.quantity + 1); touch(); } }, '+')),
      h('strong', null, money(l.quantity * l.unit_price_cents)),
      h('button', { type: 'button', class: 'btn sec peq', disabled: ro, 'aria-label': 'Quitar ' + l.name, onclick: () => { lines.splice(i, 1); touch(); } }, '×'))));
    if (printed !== null) { const ul = $('lineas'), li = ul.children[printed]; if (li) ul.scrollTop = li.offsetTop - ul.offsetTop; printed = null; }   // solo dentro de la comanda
    if (!lines.length) $('lineas').append(h('li', { class: 'vacio' }, 'Toca un producto para agregarlo.'));
    const sub = lines.reduce((s, l) => s + l.quantity * l.unit_price_cents, 0);
    const disc = parseBs($('descuento').value);
    $('subtotal').textContent = money(sub);
    $('desc-val').textContent = disc === null ? 'no válido' : '− ' + money(disc);
    $('total').textContent = disc === null || disc > sub ? '—' : money(sub - disc);
    $('descuento').disabled = $('notas').disabled = ro;
    $('titulo').textContent = order.id ? 'Comanda #' + order.id : 'Comanda nueva';
    renderStatus(); renderButtons(ro);
  }
  function renderStatus() {
    const e = $('estado'); e.replaceChildren();
    if (order.status === 'paid') e.append(h('span', { class: 'chip ok' }, 'Pagado · ' + methods[order.method]));
    else if (order.status === 'pending') e.append(h('span', { class: 'chip bajo' }, 'Pendiente de pago'));
    else if (order.status === 'cancelled') e.append(h('span', { class: 'chip agotado' }, 'Cancelado'));
    if (order.id && !order.workday_open && order.status !== 'cancelled') e.append(h('p', { class: 'suave' }, 'La jornada está cerrada: el pedido es de solo lectura.'));
  }
  function btn(label, fn, cls = '') { return h('button', { type: 'button', class: 'btn ' + cls, disabled: busy, onclick: fn }, label); }
  function renderButtons(ro) {
    const b = $('botones'); b.replaceChildren();
    if (ro) return;
    const st = order.status;
    if (st === 'new' || st === 'pending') {
      b.append(h('div', { class: 'cobro' }, btn('Efectivo', () => run(() => pay('cash')), 'efectivo'), btn('QR', () => run(() => pay('qr')))),
               btn('Guardar sin cobrar', () => run(save), 'sec'));
      if (st === 'pending') b.append(btn('Cancelar pedido', () => cancel(), 'peligro'));
    } else if (st === 'paid') {
      b.append(btn('Guardar cambios', () => run(save), dirty ? '' : 'sec'),
               btn('Cambiar a ' + (order.method === 'cash' ? 'QR' : 'efectivo'), () => run(changeMethod), 'sec'),
               btn('Cancelar pedido', () => cancel(), 'peligro'));
    }
  }

  // ---------- servidor ----------
  async function api(method, url, body) {
    const r = await fetch(url, { method, headers: { 'Content-Type': 'application/json' }, body: body ? JSON.stringify(body) : undefined });
    const data = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(data.error || 'Ocurrió un error inesperado.');
    return data;
  }
  async function run(fn) {                       // evita doble envío (doble clic)
    if (busy) return;
    busy = true; showError(''); render();
    try { await fn(); } catch (e) { showError(e.message); } finally { busy = false; render(); }
  }
  const payload = () => ({
    lines: lines.map(l => ({ item_id: l.item_id, variant_id: l.variant_id, promotion_id: l.promotion_id, quantity: l.quantity, selections: l.selections })),
    discount: $('descuento').value, notes: $('notas').value });
  function fillExtras(o) {
    $('descuento').value = o.discount_text || ''; $('notas').value = o.notes || '';
    if (o.discount_text || o.notes) $('extras').open = true;
  }
  function adopt(o) {
    order = o; lines = o.items.map(i => ({ ...i })); dirty = false;
    if (window.refreshCaja) window.refreshCaja();
    fillExtras(o);
    if (location.pathname !== '/pedidos/' + o.id) history.replaceState(null, '', '/pedidos/' + o.id);
  }
  async function save() {
    adopt(order.id ? await api('PUT', '/api/pedidos/' + order.id, payload()) : await api('POST', '/api/pedidos', payload()));
  }
  async function pay(method) {
    if (!order.id || dirty) await save();
    adopt(await api('POST', '/api/pedidos/' + order.id + '/pagar', { method }));
  }
  async function changeMethod() {
    if (dirty) await save();
    adopt(await api('POST', '/api/pedidos/' + order.id + '/metodo', { method: order.method === 'cash' ? 'qr' : 'cash' }));
  }
  function cancel() {
    const paid = order.status === 'paid';
    if (!confirm(paid ? '¿Cancelar este pedido pagado? Se anulará el cobro y se devolverán las existencias.' : '¿Cancelar este pedido?')) return;
    run(async () => adopt(await api('POST', '/api/pedidos/' + order.id + '/cancelar', {})));
  }

  $('descuento').addEventListener('input', touch);
  $('notas').addEventListener('input', () => { dirty = true; });
  window.addEventListener('beforeunload', e => { if (dirty && !readOnly()) { e.preventDefault(); e.returnValue = ''; } });
  fillExtras(order);
  renderCats(); renderPicker(); render(); dirty = false; render();
})();
