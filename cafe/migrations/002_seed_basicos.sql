-- Solo lo que viene de la especificación (editable después). No hay productos ni precios del menú real.
INSERT INTO settings(key, value) VALUES ('default_fund_cents','10000'), ('default_reserve_cents','10000');

INSERT INTO categories(name, sort_order) VALUES
 ('Cafés calientes',1),('Tés',2),('Jugos',3),('Panadería francesa',4),('Pastelería',5),
 ('Chocolates',6),('Galletas',7),('Promociones',8),('Otros',9);

INSERT INTO checklists(kind, name) VALUES ('open','Apertura'), ('close','Cierre');
INSERT INTO checklist_items(checklist_id, position, text)
SELECT 1, column1, column2 FROM (VALUES
 (1,'Iniciar jornada'),(2,'Realizar limpieza'),(3,'Revisar lista de productos esperados'),
 (4,'Recibir productos'),(5,'Contar unidades'),(6,'Registrar entradas'),(7,'Revisar existencias'),
 (8,'Comprobar fondo inicial'),(9,'Organizar el área de trabajo'));
INSERT INTO checklist_items(checklist_id, position, text)
SELECT 2, column1, column2 FROM (VALUES
 (1,'Revisar pedidos'),(2,'Comprobar pagos registrados'),(3,'Revisar gastos'),
 (4,'Contar existencias restantes'),(5,'Comparar inventario esperado y contado'),
 (6,'Consultar efectivo esperado'),(7,'Registrar efectivo contado (opcional)'),
 (8,'Reservar el fondo para la siguiente jornada'),(9,'Generar informe de cierre'));

-- Combo de Bs 17: bebida + galleta. Las opciones de cada slot se configuran con el catálogo real.
INSERT INTO promotions(name, description, price_cents) VALUES ('Combo Bs 17','Un café o té + una galleta',1700);
INSERT INTO promotion_slots(promotion_id, name, position) VALUES (1,'Bebida',1),(1,'Galleta',2);
