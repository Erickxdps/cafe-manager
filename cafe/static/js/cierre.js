// Vista previa del cierre: diferencia contra lo esperado y cuánto va a la bolsa.
(function () {
  const t = document.querySelector('.cierre'), counted = document.getElementById('counted'), reserve = document.getElementById('reserve');
  const out = document.getElementById('resultado'), bolsa = document.getElementById('bolsa');
  if (!t) return;
  const expected = Number(t.dataset.expected);
  const bs = c => {
    const a = Math.abs(c), frac = a % 100;
    return 'Bs ' + String(Math.floor(a / 100)).replace(/\B(?=(\d{3})+(?!\d))/g, '.') + (frac ? ',' + String(frac).padStart(2, '0') : '');
  };
  function parse(v) {
    const s = v.trim().toLowerCase().replace(/bs|\s/g, ''), n = s.includes(',') && s.includes('.') ? s.replace(/\./g, '').replace(',', '.')
      : /^\d{1,3}(\.\d{3})+$/.test(s) ? s.replace(/\./g, '') : s.replace(',', '.');
    if (n === '') return undefined; if (!/^\d+(\.\d{1,2})?$/.test(n)) return null;
    const [w, f = ''] = n.split('.'); return Number(w) * 100 + Number(f.padEnd(2, '0'));
  }
  function update() {
    const c = parse(counted.value), r = parse(reserve.value);
    out.className = bolsa.className = 'resultado';
    if (c === null) out.textContent = 'Revisa el importe contado.';
    else if (c === undefined) out.textContent = 'Sin contar, se usa lo esperado: ' + bs(expected) + '.';
    else {
      const d = c - expected;
      out.textContent = d === 0 ? 'La caja cuadra con lo esperado.' : (d > 0 ? 'Sobran ' : 'Faltan ') + bs(d) + ' respecto a lo esperado.';
      out.classList.add(d === 0 ? 'ok' : 'dif');
    }
    if (c === null || r === null || r === undefined) { bolsa.textContent = 'Revisa la reserva.'; return; }
    const bag = (c === undefined ? expected : c) - r;
    bolsa.textContent = bag >= 0 ? 'A la bolsa: ' + bs(bag) + '.' : 'Falta reponer ' + bs(bag) + ' en caja chica.';
    if (bag < 0) bolsa.classList.add('dif');
  }
  counted.addEventListener('input', update); reserve.addEventListener('input', update); update();
})();
