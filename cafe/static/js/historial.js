// Historial: habilita "Comparar" solo con 2 a 7 jornadas marcadas.
(function () {
  const form = document.getElementById('comparar');
  if (!form) return;
  const btn = document.getElementById('btn-comparar'), info = document.getElementById('cuenta-comparar');
  const max = 7;
  function update() {
    const n = form.querySelectorAll('input[name="j"]:checked').length;
    btn.disabled = n < 2 || n > max;
    info.textContent = n === 0 ? 'Marca de 2 a ' + max + ' jornadas.'
      : n > max ? 'Máximo ' + max + ' jornadas.' : n + (n === 1 ? ' jornada marcada.' : ' jornadas marcadas.');
  }
  form.addEventListener('change', update);
  update();
})();
