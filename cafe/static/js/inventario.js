// Diferencia en vivo en recepción (contado - esperado) y conteo (contado - sistema)
document.querySelectorAll('tr[data-diff]').forEach(function (row) {
  const cnt = row.querySelector('.cnt'), esp = row.querySelector('.esp'), cell = row.querySelector('.dif');
  function update() {
    const counted = cnt.value === '' ? null : Number(cnt.value);
    const base = esp ? (esp.value === '' ? null : Number(esp.value)) : Number(row.dataset.base);
    cell.className = 'dif';
    if (counted === null || base === null) { cell.textContent = ''; return; }
    const d = counted - base;
    cell.textContent = d === 0 ? 'Coincide' : (d > 0 ? '+' : '−') + Math.abs(d);
    if (d !== 0) cell.classList.add(d > 0 ? 'pos' : 'neg');
  }
  cnt.addEventListener('input', update);
  if (esp) esp.addEventListener('input', update);
});
