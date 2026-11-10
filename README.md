# Café Personal

Asistente local para organizar la jornada en la cafetería. Flask + SQLite, sin Internet.
Registros personales y orientativos; no sustituyen el sistema oficial del establecimiento.

## Instalación (una vez, con Internet)
    python -m venv venv
    venv\Scripts\activate
    pip install -r requirements.txt

## Iniciar
Doble clic en el acceso directo **Café Personal** del Escritorio (o en `iniciar_servidor.bat`). La ventana queda
minimizada y el navegador se abre solo en http://localhost:31847 cuando el servidor ya responde.
Si ya estaba encendida, solo abre otra pestaña. **Apagar:** cerrar la ventana "Cafe Personal" de la barra de tareas.
Cualquier problema al iniciar aparece en un mensaje en pantalla.

Para ver los mensajes al depurar, desde una consola en la carpeta del proyecto: `iniciar_servidor.bat min`.
Otro puerto: `set CAFE_PORT=numero` antes de iniciar.

## Crear el acceso directo (una vez)
Doble clic en `crear_acceso_directo.bat`. Crea en el Escritorio el acceso con el ícono `cafe\static\img\cafe.ico`.
Hay que repetirlo si se mueve la carpeta del proyecto.

## Entrar desde el celular u otro equipo de la misma red
**La aplicación no tiene contraseña:** quien esté conectado a la misma red puede ver y cambiar todo, incluidas las
copias de seguridad. Úsala solo en una red de confianza, no en una Wi-Fi compartida con clientes.

1. Permitir el puerto, una sola vez, en una consola abierta como administrador:

       netsh advfirewall firewall add rule name="Cafe Personal" dir=in action=allow protocol=TCP localport=31847 profile=private remoteip=localsubnet

   La regla vale para redes marcadas como *privadas*: en Windows, Configuración → Red e Internet → Propiedades de la
   Wi-Fi → Perfil de red: Privado.
2. Ver la IP con `ipconfig`: usar la **Dirección IPv4** del adaptador Wi-Fi o Ethernet real, el que tiene *Puerta de
   enlace predeterminada*. No usar las de adaptadores virtuales (VirtualBox, WSL, Docker, VPN).
3. En el celular, conectado a la misma red: `http://IP:31847`, por ejemplo `http://192.168.0.8:31847`.
   No usar nombres `.local`: en Android no son fiables.
4. Conviene reservar esa IP en el router (reserva DHCP) para que no cambie.

## Instalar esta actualización sobre un sistema en uso
1. Apagar la aplicación y crear una copia en ⚙ Configuración si se quiere un respaldo extra.
2. Reemplazar los archivos del programa con los nuevos, **sin borrar** `venv\`, `ubicacion_datos.txt` ni la carpeta
   de datos (la indicada en `datos=`; sin ese archivo, `data\`).
3. Doble clic en `crear_acceso_directo.bat`.
4. Ejecutar el comando del firewall de arriba si se va a entrar desde otros equipos.
5. Actualizar favoritos: la dirección antigua con el puerto 5000 deja de funcionar.

Al iniciar, la base se actualiza sola si hace falta y guarda una copia antes.

## Pruebas
    python -m unittest discover -s tests -v

## Datos de prueba (opcional)
    python cargar_prueba.py            (productos marcados "(Prueba)")
    python cargar_prueba.py --quitar   (los elimina)

## Datos
Todo vive en la carpeta indicada en `ubicacion_datos.txt` (`datos=`): cafe.db, uploads/, backups/, logs/. Sin ese archivo se usa `data/`.
La línea `copias=` indica un segundo lugar donde se guarda cada copia `.zip` (por ejemplo OneDrive).
No conviene que la carpeta de datos esté dentro de OneDrive: la sincronización puede bloquear la base mientras se usa.
La base se crea y actualiza sola al iniciar; antes de actualizar una base con datos se guarda una copia en `backups/`.

## Copias de seguridad
- **Crear:** ⚙ Configuración → "Crear copia ahora". Se guarda un `.zip` (base de datos + fotos) en `data\backups`.
- **Automáticas:** al cerrar cada jornada se crea una copia (se conservan las últimas 30). Antes de actualizar la
  aplicación o de restaurar también se guarda una copia del estado anterior.
- **Llevarla a otro lugar:** en la lista de copias, "Descargar", y guárdala en un USB u otra carpeta.
- **Restaurar:** ⚙ Configuración → "Restaurar" junto a la copia, o "Restaurar desde un archivo" (.zip completo o .db
  solo base). La copia se comprueba antes de usarla; si algo falla, los datos vuelven solos a como estaban.
- **A mano (con la aplicación detenida):** copiar o reemplazar toda la carpeta `data`.

## Informes
Historial → jornadas con totales, comparación de 2 a 7 jornadas e informes por rango de fechas,
descargables en Excel y PDF (también desde el informe de cierre de cada jornada).
