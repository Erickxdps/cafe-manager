// Agregar / quitar filas de presentaciones en el formulario de producto
(function () {
  const box = document.getElementById('variantes');
  const tpl = document.getElementById('plantilla-variante');
  document.getElementById('agregar-variante').addEventListener('click', function () {
    box.append(tpl.content.cloneNode(true));
    box.lastElementChild.querySelector('input[name="var_name"]').focus();
  });
  box.addEventListener('click', function (e) {
    if (e.target.classList.contains('quitar')) e.target.closest('.var-fila').remove();
  });
})();
