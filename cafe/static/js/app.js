// Modo claro / oscuro: se guarda en este navegador (es una preferencia de pantalla, no un dato de la cafetería).
(function () {
  const btn = document.getElementById('tema-btn');
  if (!btn) return;
  const root = document.documentElement, txt = btn.querySelector('.tema-txt');
  function show() {
    const dark = root.dataset.tema === 'oscuro';
    btn.setAttribute('aria-pressed', String(dark));
    btn.title = dark ? 'Cambiar a modo claro' : 'Cambiar a modo oscuro';
    if (txt) txt.textContent = dark ? 'Modo claro' : 'Modo oscuro';
  }
  btn.addEventListener('click', function () {
    const dark = root.dataset.tema !== 'oscuro';
    if (dark) root.dataset.tema = 'oscuro'; else delete root.dataset.tema;
    try { localStorage.setItem('cafe-tema', dark ? 'oscuro' : 'claro'); } catch (e) { /* sin almacenamiento: dura hasta recargar */ }
    show();
  });
  show();
})();

// Confirmación antes de acciones destructivas: <form data-confirm="mensaje">
document.addEventListener('submit', function (e) {
  const msg = e.target.dataset && e.target.dataset.confirm;
  if (msg && !window.confirm(msg)) e.preventDefault();
});

// Buscador con autocompletado instantáneo (sin Enter)
(function () {
  const input = document.getElementById('buscador');
  const list = document.getElementById('sugerencias');
  if (!input) return;
  let items = [], selected = -1, seq = 0;

  const money = c => 'Bs ' + (c % 100 ? (c / 100).toFixed(2).replace('.', ',') : c / 100);
  const stateText = r => ({ agotado: 'Agotado', bajo: 'Quedan ' + r.stock, ok: r.stock + ' disp.', sin_control: '' })[r.availability];

  function close() { list.hidden = true; list.replaceChildren(); items = []; selected = -1; }

  function render() {
    list.replaceChildren();
    if (!items.length) {
      const li = document.createElement('li'); li.className = 'vacio-sug'; li.textContent = 'Sin resultados';
      list.append(li); list.hidden = false; return;
    }
    items.forEach((r, i) => {
      const li = document.createElement('li');
      li.setAttribute('aria-selected', i === selected);
      const a = document.createElement('a'); a.className = 'sug'; a.href = '/catalogo/' + r.product_id;
      const pic = r.photo ? Object.assign(document.createElement('img'), { src: '/media/' + r.photo, alt: '' })
                          : Object.assign(document.createElement('span'), { textContent: (r.name.match(/[\p{L}\p{N}]/u) || ['·'])[0].toUpperCase() });
      pic.className = 'mini' + (r.photo ? '' : ' sin-foto');
      const data = document.createElement('span'); data.className = 'datos';
      const strong = document.createElement('strong'); strong.textContent = r.name;
      const small = document.createElement('small'); small.className = 'suave'; small.textContent = r.category;
      data.append(strong, small);
      const price = document.createElement('strong'); price.textContent = money(r.price_cents);
      a.append(pic, data, price);
      if (stateText(r)) {
        const chip = document.createElement('span'); chip.className = 'chip ' + (r.availability === 'sin_control' ? 'neutro' : r.availability);
        chip.textContent = stateText(r); a.append(chip);
      }
      li.append(a);
      if (r.recipe_id) {
        const rl = document.createElement('a'); rl.className = 'receta-link'; rl.href = '/recetario/' + r.recipe_id; rl.textContent = 'Ver receta';
        li.append(rl);
      }
      list.append(li);
    });
    list.hidden = false;
  }

  input.addEventListener('input', async function () {
    const q = input.value.trim();
    if (!q) return close();
    const mine = ++seq;
    try {
      const data = await (await fetch('/api/buscar?q=' + encodeURIComponent(q))).json();
      if (mine !== seq) return;           // llegó una respuesta vieja
      items = data.resultados; selected = -1; render();
    } catch (err) { close(); }
  });
  input.addEventListener('keydown', function (e) {
    if (e.key === 'Escape') { close(); input.blur(); return; }
    if (!items.length) return;
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      e.preventDefault();
      selected = (selected + (e.key === 'ArrowDown' ? 1 : -1) + items.length) % items.length; render();
    } else if (e.key === 'Enter') {
      e.preventDefault(); window.location.href = '/catalogo/' + items[Math.max(selected, 0)].product_id;
    }
  });
  document.addEventListener('click', e => { if (!e.target.closest('.buscar')) close(); });
})();
