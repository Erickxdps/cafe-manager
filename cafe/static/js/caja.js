// Efectivo esperado en la pizarra del menú: se vuelve a pedir al servidor tras cada cobro, cancelación o cambio.
(function () {
  // Igual que format_bs en el servidor: "Bs 1.250" o "Bs 1.250,50"
  const money = function (c) {
    const a = Math.abs(c), whole = Math.floor(a / 100), frac = a % 100;
    return (c < 0 ? '-' : '') + 'Bs ' + String(whole).replace(/\B(?=(\d{3})+(?!\d))/g, '.') +
           (frac ? ',' + String(frac).padStart(2, '0') : '');
  };
  window.refreshCaja = async function () {
    const chip = document.getElementById('caja-chip');
    if (!chip) return;
    try {
      const d = await (await fetch('/api/caja')).json();
      if (!d.open) return;
      const value = chip.querySelector('strong');
      const changed = value.textContent !== d.expected_text;
      value.textContent = d.expected_text;
      const cuenta = chip.querySelector('.pizarra-cuenta');
      if (cuenta) {
        cuenta.replaceChildren(money(d.fund) + ' de fondo', document.createElement('br'),
                               '+ ' + money(d.cash) + ' en efectivo', document.createElement('br'),
                               '− ' + money(d.expenses) + ' de gastos');
      }
      chip.classList.toggle('neg', d.expected < 0);
      if (changed) {               // la cifra se "vuelve a escribir" con tiza
        chip.classList.remove('tiza'); void chip.offsetWidth; chip.classList.add('tiza');
      }
    } catch (e) { /* si falla, queda el valor de la carga de la página */ }
  };
})();

document.addEventListener('DOMContentLoaded', function () {
  const botones = document.querySelectorAll('.filtros-cobro button');
  if (!botones.length) return;
  const items = document.querySelectorAll('.cobro-item');
  const vacioFiltro = document.getElementById('cobros-vacio-filtro');

  botones.forEach(function (btn) {
    btn.addEventListener('click', function () {
      botones.forEach(function (b) { b.classList.remove('activo'); b.setAttribute('aria-pressed', 'false'); });
      btn.classList.add('activo'); btn.setAttribute('aria-pressed', 'true');
      const filtro = btn.dataset.filtro;
      let visibles = 0;
      items.forEach(function (it) {
        const show = (filtro === 'todos' || it.dataset.metodo === filtro);
        it.style.display = show ? '' : 'none';
        if (show) visibles++;
      });
      if (vacioFiltro) vacioFiltro.hidden = visibles > 0;
    });
  });
});
