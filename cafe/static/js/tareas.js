// Marcar tareas de la jornada: se guarda al instante; si falla, se deshace y se avisa.
document.addEventListener('change', async function (e) {
  const box = e.target; if (!box.dataset || !box.dataset.task) return;
  const li = box.closest('li'), msg = document.getElementById('mensaje');
  try {
    const r = await fetch('/api/tareas/' + box.dataset.task, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ done: box.checked }) });
    if (!r.ok) throw new Error((await r.json()).error || 'No se pudo guardar.');
    li.classList.toggle('hecha', box.checked); msg.hidden = true;
    const sect = box.closest('.tareas'), all = sect.querySelectorAll('input[data-task]');
    sect.querySelector('h2 small').textContent = [...all].filter(c => c.checked).length + ' de ' + all.length;
  } catch (err) { box.checked = !box.checked; msg.textContent = err.message; msg.hidden = false; }
});
