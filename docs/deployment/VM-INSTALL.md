# Despliegue piloto con Docker Compose

Este paquete inicia Django/Gunicorn, PostgreSQL y Caddy para una VM Linux con Docker Compose. Es una base de despliegue, no una imagen VM certificada ni una aceptación de producción. No contiene datos empresariales, cuentas precargadas ni secretos.

## Requisitos

- VM Ubuntu Server LTS mantenida por TI, con Docker Engine y el plugin Docker Compose instalados desde sus fuentes oficiales.
- DNS para el nombre elegido, acceso de usuarios a la VM y reglas de firewall administradas por TI.
- Un nombre de host aprobado. Caddy genera un certificado de su CA interna; cada dispositivo de usuario debe confiar en esa CA por el mecanismo aprobado por TI.
- Una copia de seguridad fuera de la VM y un responsable de probar su restauración antes de cargar datos reales.

No abrir PostgreSQL ni el puerto 8000 al exterior. La política de red de la VM debe restringir los puertos publicados 80/443 a las subredes autorizadas; SSH se administra por separado. La aplicación tiene salida de red para conectores/notificaciones: TI debe limitarla en el firewall del host a los destinos y puertos aprobados; no se ha habilitado aún una lista de destinos del conector de Vitali.

## Primera instalación

1. Obtenga el código de una revisión aprobada y verifique su procedencia.
2. Cree `.env` a partir de `.env.example` y restrinja su lectura al usuario administrador del despliegue.
3. Genere valores aleatorios distintos para `SMARTORDER_SECRET_KEY`, `SMARTORDER_SETUP_CODE` y `POSTGRES_PASSWORD`; no los guarde en Git ni en tickets. Use para la contraseña PostgreSQL caracteres URL seguros (`A-Z`, `a-z`, `0-9`, `_`, `-`).
4. Configure `SMARTORDER_DOMAIN` con el nombre aprobado y sus entradas DNS/firewall/TLS. Para evitar falsos inicios, no use `localhost` como dominio del piloto.
5. Valide la configuración e inicie servicios:

   ```sh
   docker compose --env-file .env config
   docker compose --env-file .env up -d --build
   docker compose --env-file .env ps
   ```

6. Revise `docker compose --env-file .env logs --tail=100 web proxy db`. Complete el alta de administrador con el código privado y entréguelo por un canal separado.
7. Antes de importar ventas, comprobar acceso por HTTPS, permisos de usuario, persistencia tras reinicio, backup y restauración en un destino de prueba.

`tls internal` hace que Caddy emita certificados con su CA local. La raíz de esa CA es de confianza sensible: TI debe obtenerla de forma autenticada desde el volumen de Caddy, verificarla y distribuirla por su mecanismo corporativo. No desactive las advertencias TLS ni distribuya la clave privada de CA. Si la empresa dispone de certificados de una CA corporativa o pública, sustituya este modo por el procedimiento certificado de TI.

## Operación y límites actuales

- Datos Django y PostgreSQL persisten en volúmenes Docker; estos volúmenes **no** son un backup independiente.
- Las migraciones Django se ejecutan al iniciar el contenedor web. Despliegue una sola instancia web y conserve un backup verificable antes de actualizar.
- El proxy usa TLS interno. No configura por sí solo DNS, firewall, VPN, monitoreo, backup remoto ni renovación de una CA corporativa.
- HSTS está habilitado por un año en Compose; usa el dominio solo tras confirmar HTTPS y distribución confiable de la CA a todos los clientes.
- No está incluido el conector de ventas de Vitali. El tipo de BD, esquema autorizado, unidades, permisos de solo lectura y significado de columnas deben confirmarse y probarse en una fuente no productiva.
- El sistema usa PostgreSQL como su base interna; la conexión de origen es una integración distinta y no cambia `DATABASES['default']`.
- El archivo `.env`, los volúmenes, el certificado raíz de Caddy y cualquier backup necesitan políticas de acceso, rotación y retención propias de TI.
- El proceso no configura proveedor de correo, restauración automatizada, worker de inferencia, alta disponibilidad ni formato de exportación de VM.

## Respaldo y prueba de restauración

PostgreSQL y `/data` (incluidos los archivos cargados en `/data/uploads`) son persistencias independientes. El procedimiento siguiente detiene el contenedor web durante la captura para evitar escrituras concurrentes, y genera una copia lógica de PostgreSQL más un archivo comprimido del volumen de datos. No guarda respaldos dentro del repositorio ni en la VM como única copia. El archivo de `/data` puede contener datos sensibles; almacénelo cifrado y con acceso/retención aprobados por TI.

En la VM, use un destino externo aprobado y accesible para el usuario del despliegue. La contraseña PostgreSQL de `.env` debe seguir la restricción URL-safe descrita arriba:

```sh
set -eu
umask 077
backup_dir=/mnt/backup-aprobado/smartorder
mkdir -p "$backup_dir"
stamp="$(date -u +%Y%m%dT%H%M%SZ)-$$"
db_backup="$backup_dir/postgres-$stamp.dump"
data_backup="$backup_dir/data-$stamp.tar.gz"

docker compose --env-file .env stop web
trap 'docker compose --env-file .env start web' EXIT
docker compose --env-file .env exec -T db \
  sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc --no-owner --no-acl' \
  > "$db_backup"
docker compose --env-file .env run --rm -T --no-deps --entrypoint python web \
  -c 'import sys,tarfile; archive=tarfile.open(fileobj=sys.stdout.buffer, mode="w|gz"); archive.add("/data", arcname="."); archive.close()' \
  > "$data_backup"
docker compose --env-file .env exec -T db pg_restore --list < "$db_backup" > /dev/null
sha256sum "$db_backup" "$data_backup" > "$db_backup.sha256"
sha256sum -c "$db_backup.sha256"
docker compose --env-file .env start web
trap - EXIT
```

Si un comando falla, el `trap` intenta iniciar web nuevamente; verifique el estado del servicio antes de continuar. Copie los tres archivos al almacenamiento de retención fuera de la VM mediante el canal aprobado por TI y valide el checksum allí.

Para probar la restauración sin sustituir la base activa ni el volumen de archivos, cree recursos temporales con nombres únicos. Use los mismos archivos y checksums del respaldo:

```sh
set -eu
db_backup=/mnt/backup-aprobado/smartorder/postgres-<stamp>.dump
data_backup=/mnt/backup-aprobado/smartorder/data-<stamp>.tar.gz
restore_db="restore_check_$(date -u +%Y%m%dT%H%M%SZ)_$$"
restore_volume="smartorder_restore_$(date -u +%Y%m%dT%H%M%SZ)_$$"

docker compose --env-file .env exec -T db \
  sh -c 'createdb -U "$POSTGRES_USER" "$1"' sh "$restore_db"
docker compose --env-file .env exec -T db \
  sh -c 'pg_restore -U "$POSTGRES_USER" -d "$1" --no-owner --no-acl --exit-on-error' \
  sh "$restore_db" < "$db_backup"
docker compose --env-file .env exec -T db \
  sh -c 'psql -U "$POSTGRES_USER" -d "$1" -v ON_ERROR_STOP=1 -c "SELECT COUNT(*) FROM django_migrations;"' \
  sh "$restore_db"

docker volume create "$restore_volume"
# El archivo contiene el contenido de /data en la raiz, sin una carpeta data adicional.
cat "$data_backup" | docker compose --env-file .env run --rm -T --no-deps \
  --user 0:0 -v "$restore_volume:/restore" --entrypoint python web \
  -c 'import sys,tarfile; archive=tarfile.open(fileobj=sys.stdin.buffer, mode="r|gz"); archive.extractall("/restore", filter="data")'
docker compose --env-file .env run --rm -T --no-deps \
  -v "$restore_volume:/restore:ro" --entrypoint python web \
  -c 'from pathlib import Path; root=Path("/restore"); assert root.is_dir(); print("Restored entries:", sum(1 for _ in root.rglob("*"))); print("Top-level entries:", [item.name for item in root.iterdir()])'
```

Confirme con TI los checksums, la salida de `pg_restore`, el conteo de migraciones y que el archivo restaurado incluye los uploads esperados. Solo después de validar, elimine exclusivamente los recursos temporales creados en esta prueba, usando exactamente los valores de `restore_db` y `restore_volume`:

```sh
docker compose --env-file .env exec -T db \
  sh -c 'dropdb -U "$POSTGRES_USER" "$1"' sh "$restore_db"
docker volume rm "$restore_volume"
```

No apunte `restore_db` a la base activa ni ejecute `dropdb` o `docker volume rm` sobre recursos ajenos a esta prueba. Si alguna verificación falla, conserve los recursos temporales para investigar y no los limpie automáticamente. Este procedimiento no se ha ejecutado: requiere Docker, almacenamiento externo, ventana de mantenimiento y revisión de TI.

## Verificación antes del piloto

Ejecute en una copia de la configuración aprobada:

```sh
docker compose --env-file .env exec web python manage.py check --deploy
docker compose --env-file .env exec web python manage.py test operations tests --noinput
```

Los avisos de `check --deploy` deben resolverse o justificarse explícitamente. No habilite `includeSubDomains` o `preload` sin una decisión de TI que cubra todos los hosts afectados. Las pruebas técnicas no sustituyen una prueba de restauración, seguridad de red, UAT ni aceptación empresarial.
