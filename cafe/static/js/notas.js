// Notas: marcar tareas y recordatorios como hechos sin recargar.
(function () {
  const msg = document.getElementById('mensaje');
  document.querySelectorAll('.nota-check').forEach(function (box) {
    box.addEventListener('change', async function () {
      const li = box.closest('.nota');
      box.disabled = true;
      try {
        const r = await fetch('/api/notas/' + box.dataset.id + '/hecha', {
          method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ done: box.checked })
        });
        if (!r.ok) throw new Error((await r.json().catch(() => ({}))).error || 'No se pudo guardar.');
        li.classList.toggle('hecha', box.checked);
        if (msg) msg.hidden = true;
      } catch (e) {
        box.checked = !box.checked;
        if (msg) { msg.textContent = e.message; msg.hidden = false; }
      } finally { box.disabled = false; }
    });
  });
})();
