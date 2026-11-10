// Configuración de promociones: elegir secciones completas (y productos sueltos), agregar o quitar elecciones.
(function () {
  const box = document.getElementById('slots');
  const tpl = document.getElementById('plantilla-slot');
  if (!box || !tpl) return;
  let next = 1000;

  function summary(fs) {
    const cats = [...fs.querySelectorAll('.cat-toggle:checked')];
    const inCats = cats.reduce((n, c) => n + Number(c.dataset.n || 0), 0);
    const loose = fs.querySelectorAll('.sueltos input:checked:not(:disabled)').length;
    const total = inCats + loose;
    const p = fs.querySelector('.resumen-eleccion');
    if (!total) {
      p.textContent = 'Todavía no hay nada para elegir: activa al menos una sección o agrega productos sueltos.';
      p.classList.add('falta');
    } else {
      const parts = [];
      if (cats.length) parts.push(cats.length + (cats.length === 1 ? ' sección' : ' secciones'));
      if (loose) parts.push(loose + (loose === 1 ? ' producto suelto' : ' productos sueltos'));
      p.textContent = 'Se podrá elegir entre ' + total + (total === 1 ? ' producto' : ' productos') + ' (' + parts.join(' y ') + ').';
      p.classList.remove('falta');
    }
  }

  function syncSection(toggle) {      // una sección completa: sus productos sueltos sobran
    const fs = toggle.closest('.eleccion');
    const group = fs.querySelector('.sueltos-grupo[data-cat="' + toggle.value + '"]');
    if (group) {
      group.classList.toggle('incluida', toggle.checked);
      group.querySelectorAll('input').forEach(c => { c.disabled = toggle.checked; if (toggle.checked) c.checked = false; });
    }
  }

  function refreshRemoveButtons() {
    const all = box.querySelectorAll('.eleccion');
    all.forEach(fs => { fs.querySelector('.quitar-eleccion').hidden = all.length < 2; });
  }

  box.addEventListener('change', function (e) {
    const fs = e.target.closest('.eleccion');
    if (!fs) return;
    if (e.target.classList.contains('cat-toggle')) syncSection(e.target);
    summary(fs);
  });
  box.addEventListener('click', function (e) {
    if (!e.target.classList.contains('quitar-eleccion')) return;
    const fs = e.target.closest('.eleccion');
    const name = fs.querySelector('input[name$="_name"]').value.trim() || 'esta elección';
    if (confirm('¿Quitar «' + name + '» del combo? Se aplica al guardar.')) { fs.remove(); refreshRemoveButtons(); }
  });
  document.getElementById('agregar-slot').addEventListener('click', function () {
    const wrap = document.createElement('div');
    wrap.innerHTML = tpl.innerHTML.replaceAll('__I__', String(next++));
    const fs = wrap.firstElementChild;
    box.append(fs);
    summary(fs); refreshRemoveButtons();
    fs.querySelector('input[name$="_name"]').focus();
  });

  box.querySelectorAll('.eleccion').forEach(fs => {
    fs.querySelectorAll('.cat-toggle:checked').forEach(t => fs.querySelector('.sueltos-grupo[data-cat="' + t.value + '"]')?.classList.add('incluida'));
    summary(fs);
  });
  refreshRemoveButtons();
})();
