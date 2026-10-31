# Auditoría y consolidación SmartOrder/Vitali

**Fecha:** 2026-10-30
**Rama revisada:** `consolidate-smartorder-plan`
**Base actual:** `origin/main` en `35323af1fbb64495ce7d8e84ee83923d16fceb4a`
**Referencia inicial:** `5129401fd8e346f088ba377aff039d44eb03a1d3`

## Alcance y límites

El archivo `PLAN_MAESTRO_MEJORAS_SMARTORDER_VITALI_2026-10-30.md` no está en el árbol de trabajo ni en `origin/main`; tampoco está disponible en la Library de esta sesión. El contexto anterior confirma que se leyó un adjunto de 1,456 líneas, pero no conserva su texto íntegro. Por tanto, esta matriz es una consolidación provisional basada en `docs/AUDITORIA_SISTEMA.md`, los cambios verificables del repositorio y las decisiones conservadas en la conversación; no afirma cubrir cada requisito del plan maestro.

No se ejecutó Docker, no se desplegó una VM, no se conectó a una base de ventas de Vitali y no se hizo UAT. El respaldo/restauración se documentó, pero no se ejecutó. No se declaran resultados ni aceptación del cliente que no estén evidenciados.

## Revisión técnica posterior del paquete

La revisión recibida identificó tres hallazgos de despliegue/interfaz corregibles sin cambiar la definición comercial y una decisión comercial pendiente:

| Hallazgo | Acción local | Estado / límite |
|---|---|---|
| COR-01: restaurar `/data` como UID 10001 en un volumen vacío puede impedir crear archivos. | El comando de extracción del archivo de datos ejecuta solo ese contenedor temporal con `--user 0:0`; el contenedor normal conserva su usuario no root. | Procedimiento corregido; no ejecutado con Docker. Aún se debe verificar propiedad/permisos y lectura por el UID normal en volumen temporal. |
| COR-04: la estructura del tar de `/data` debe coincidir con la ruta de comprobación tras restaurar. | El respaldo archiva `/data` con raíz `.` y la restauración comprueba entradas directamente bajo `/restore`. | Documentación corregida; no se generó/restauró un archivo desde Docker ni se validó propiedad/permisos del volumen. |
| COR-02: `reference_date` usaba `floatformat`, por lo que la fecha histórica se mostraba vacía. | Se restauró `date:'d/m/Y'` y se añadió una aserción Django con fecha no nula y cantidad/unidad. | Reproducido primero con un test fallido; el test pasa tras la corrección. |
| COR-03: no está claro si la oportunidad comercial usa la fecha objetivo de venta/contacto o la fecha solicitada de entrega. | No se modificó el motor ni se reinterpretó la fecha. | Bloqueado por definición del responsable comercial; requiere separar/nombrar ambas fechas y aprobar el horizonte antes de cambiar la lógica. |

La revisión externa también confirmó que no ejecutó la suite completa, Docker, VM, PostgreSQL, restauración real ni UAT. Sus observaciones estructurales no sustituyen esas pruebas.

## Reconciliación de ramas y cambios

`origin/main` ya avanzó **27 commits** desde la referencia inicial. Durante la revisión se incorporaron 11 commits nuevos (PR #7) después de la primera lectura de main en `d5d7b77`; la rama se rebasó sobre el nuevo head `35323af` antes de cerrar esta auditoría. El merge-base actual es `35323af`. Se conservó el rediseño Streamlit integrado en main (PR #4), así como la actualización de la interfaz Django incorporada en PR #7; se reconciliaron únicamente las extensiones para mostrar la oportunidad comercial, los requisitos de producción y la documentación de despliegue. El estado local previo se preservó en el stash `preserve local SmartOrder work before main consolidation`; el archivo generado por Modernization Workflow se mantiene local y no se publica.

Los cambios revisables de esta rama abarcan:

| Archivo(s) | Cambio consolidado |
|---|---|
| `operations/views.py`, `operations/templates/operations/recommendations.html`, `operations/templates/operations/order_form.html` | Proyectan y muestran el pronóstico comercial al vendedor por separado de la cantidad de entrega. No se usa para autocompletar ni autorizar la cantidad. |
| `streamlit_app.py` | Presenta una columna de oportunidad comercial distinta y un mensaje separado para el administrador. |
| `operations/test_planning_inbox.py`, `operations/test_streamlit.py` | Prueban el flujo Django, el formulario y la salida real de la UI Streamlit con el pronóstico separado; el formulario también verifica fecha histórica con cantidad/unidad. |
| `vitali_web/settings.py`, `requirements.txt`, `.gitignore` | Añaden configuración de producción por entorno, HTTPS/cookies/hosts, WhiteNoise y reglas para secretos/estáticos; se conserva el soporte PostgreSQL ya integrado en main. |
| `Dockerfile`, `compose.yaml`, `infra/Caddyfile`, `.dockerignore`, `.env.example` | Base de despliegue Linux con usuario no root, PostgreSQL interno, Django/Gunicorn, Caddy, secretos por entorno y datos persistentes. |
| `docs/BASELINE-2026-10-30.md`, `docs/deployment/VM-INSTALL.md`, `README.md` | Evidencia histórica de línea base, guía operativa y límites del despliegue/backups; el tar archiva el contenido de `/data` en su raíz y solo la extracción temporal usa UID 0. |
| `operations/templates/operations/order_form.html` | La fecha de referencia histórica se renderiza como `dd/mm/yyyy`; se conserva el formato numérico para la cantidad. |
| `docs/CONSOLIDATION_REVIEW_SMARTORDER_VITALI_2026-10-30.md` | Esta matriz, incidencias, registro de pruebas e inventario provisional. |

No se incluye `.github/modernize/canvas-smartorder-vitali-2026-10-30.json`, `.env`, `.local-web/`, la base local SQLite, archivos cargados, estáticos generados ni otros datos locales.

## Matriz de requisitos e historias de usuario

Estados: **Implementado** = hay código/documentación; **Probado** = hay una verificación reproducible indicada; **Pendiente** = trabajo posible aún no terminado; **Bloqueado** = requiere infraestructura o decisión/datos empresariales; **Aceptado por cliente** = evidencia explícita de aceptación de Vitali. “Implementado” no implica “probado” ni “aceptado”.

| Historia / requisito recuperable | Archivos y pruebas de referencia | Estado |
|---|---|---|
| Como vendedor, quiero distinguir la oportunidad de venta prevista de la cantidad que se entrega. | `operations/views.py`, plantillas Django, `streamlit_app.py`; `test_seller_sees_commercial_forecast_separate_from_delivery_quantity`, `test_native_seller_sees_commercial_forecast_separate_from_delivery_quantity`. | Implementado y probado localmente; aceptación de cliente no consta. |
| Como vendedor, quiero que el pronóstico no se copie como cantidad confirmada. | `order_form.html`; prueba Django verifica que no se propague a `value` ni `data-suggested`; prueba Streamlit exige cantidad manual separada. | Implementado y probado localmente; UAT pendiente. |
| Como administración, quiero revisar solicitudes antes de compartir producción. | Flujo existente de solicitudes, revisión y XLSX; pruebas en `operations/test_planning_inbox.py` y `operations/test_streamlit.py`. | Implementado en repo y cubierto por suite; prueba operativa/UAT pendiente. |
| Usar fecha de venta/pedido como semántica de la fecha del Excel. | `SalesDataset.DateSemantics.SALE_ORDER`; evidencia de la decisión en la conversación. | Decisión del usuario del proyecto confirmada; no equivale a aceptación de Vitali. |
| Mostrar fecha de venta histórica junto a su cantidad y unidad. | `order_form.html`; `test_seller_sees_commercial_forecast_separate_from_delivery_quantity` comprueba `07/01/2026`, `3 kg`. | Corregido y probado localmente; no es UAT. |
| Probar el cambio contra una línea base verificable. | `docs/BASELINE-2026-10-30.md`; pruebas de la rama actual. | Línea base histórica y suite actual verificadas localmente; no es UAT. |
| Preservar el rediseño frontend integrado en main. | `origin/main` PR #4 y actualización Django PR #7; diff consolidado limitado a la oportunidad comercial. | Integrado y cubierto por la suite; aceptación de cliente no consta. |
| Proteger cuentas de demo con opt-in y secretos de Streamlit. | Código/documentación integrados en main, PR #6; `operations/test_demo_bootstrap.py` en main. | Integrado en main y con pruebas del repo; secretos reales no se incluyen ni se validan aquí. |
| Persistir base de datos de la instancia de despliegue. | Soporte PostgreSQL en main y CI existente; Compose añade PostgreSQL interno. | Configuración implementada; PostgreSQL Compose/VM no probado en este entorno. |
| Desplegar Django/Gunicorn detrás de TLS con datos persistentes. | `Dockerfile`, `compose.yaml`, `infra/Caddyfile`, `.env.example`, `VM-INSTALL.md`. | Implementado y validado estáticamente; build, arranque, TLS y persistencia real pendientes/bloqueados por Docker/VM. |
| Restringir red y secretos antes de producción. | Compose publica solo 80/443 en proxy; `.env.example`, `.gitignore`, `.dockerignore`, guía de TI. | Reglas de configuración revisadas; firewall/allowlist de salida, gestión real de secretos y verificación en VM bloqueados por TI. |
| Respaldar y restaurar PostgreSQL y archivos subidos. | `docs/deployment/VM-INSTALL.md`, volúmenes `postgres_data` y `smartorder_data`. | Procedimiento escrito; ejecución y prueba de recuperación pendientes por falta de Docker y almacenamiento aprobado. |
| Conectar con ventas reales de Vitali y validar recomendaciones. | Código de integración Odoo existente es otro sistema; no hay conector confirmado para el origen de ventas de Vitali. | Bloqueado: motor, host, esquema, acceso de solo lectura y muestra aprobada no proporcionados. |
| Recomendar por sala–SKU/unidad oficial. | `docs/AUDITORIA_SISTEMA.md`; datos actuales cliente–producto. | Bloqueado por catálogo, sucursales, SKU y conversiones oficiales. |
| Medir error prospectivo, mermas, agotados y cumplimiento real. | Suite/modelos locales y límites descritos en `docs/AUDITORIA_SISTEMA.md`. | Pruebas unitarias/locales existen; backtest con varios años y métricas productivas bloqueados por datos operativos reales. |
| Aceptación de usuario final (UAT) y aprobación del piloto. | No hay actas ni resultados UAT adjuntos/disponibles. | Pendiente; no aceptado por cliente según la evidencia disponible. |

La tabla no sustituye al inventario completo del plan maestro, que sigue sin recuperarse.

## Incidencias y riesgos

| Prioridad | Incidencia | Evidencia / acción |
|---|---|---|
| P0 | Ninguna incidencia P0 confirmada en el alcance disponible. No se puede concluir que no existan P0 en el plan maestro ausente. | Mantener esta limitación hasta recuperar el plan íntegro y ejecutar la evaluación restante. |
| P1 | Despliegue no está certificado: Docker no está instalado en el entorno de revisión. | `Dockerfile`/Compose solo tienen validación estática; se requiere build, arranque, health checks, HTTPS, persistencia y reinicio en VM. |
| P1 | La conexión de ventas de Vitali y los resultados de backtest no son verificables. | Esperar motor, esquema, acceso de solo lectura, semántica de campos y datos aprobados; no reutilizar la DB interna como si fuera el origen. |
| P1 | Recuperación integral no probada. PostgreSQL y `/data` (incluidos XLSX) son volúmenes separados. | Ejecutar backup fuera de VM y restauración en recursos temporales; verificar también los archivos antes de aceptar RPO/RTO. |
| P1 | COR-01: la extracción sobre volumen de restauración recién creado necesita permisos de escritura. | El comando de extracción usa `--user 0:0` únicamente para el contenedor temporal; debe validarse en Docker y luego comprobar lectura/permisos con el usuario no root normal. |
| P1 | COR-04: el formato del respaldo de `/data` debe restaurarse con la misma estructura que se comprueba. | El tar guarda el contenido en raíz y la comprobación lista `/restore`; hay que ejercitar backup y restore reales antes de aceptar la guía. |
| P1 | COR-03: la previsión de ventas y la fecha de entrega consultada pueden representar horizontes distintos. | No cambiar el motor sin decisión comercial sobre fecha objetivo/horizonte; cubrir con un test donde fecha de venta y entrega difieran. |
| P1 | Salida de red de `web` necesita política efectiva de egress en el firewall de la VM. | Compose ofrece conectividad saliente pero no implementa por sí mismo una allowlist. TI debe aplicarla y probarla. |
| Resuelto en esta rama | COR-02: fecha histórica formateada como número en el formulario. | Restaurado el filtro `date:'d/m/Y'`; regresión con fecha no nula pasa. |
| P2 | `manage.py check --deploy` reporta W005 y W021 porque HSTS no incluye subdominios ni preload. | Se evita ampliar HSTS sin política de todos los subdominios/consentimiento TI. El check termina con código 0 y esos 2 avisos. |
| P2 | Las imágenes Docker usan tags de versión (`postgres:17`, `caddy:2`), no digest inmutable. | Para el piloto controlado, registrar y fijar digest tras validación del equipo de plataforma; no se ha hecho por falta de ejecución Docker. |

No se encontraron coincidencias de patrones comunes de credenciales en archivos de proyecto no ignorados. `.env.example` contiene solo valores de reemplazo. La base `.local-web/smartorder.sqlite3` existe en el entorno local, está ignorada y no se publica; no se abrió ni se incluyó en el build context.

## Registro de verificaciones

| Comando / evidencia | Resultado |
|---|---|
| `git fetch origin main`; comparación con `5129401` | `origin/main` actualizado a `35323af1fbb64495ce7d8e84ee83923d16fceb4a`, 27 commits delante; la rama se rebasó sobre ese head y el merge-base actual coincide. Main había avanzado 16 commits al inicio de la revisión y recibió 11 más durante la auditoría. |
| `manage.py test operations.test_planning_inbox.PlanningInboxTests.test_seller_sees_commercial_forecast_separate_from_delivery_quantity operations.test_streamlit.StreamlitBridgeTests.test_native_seller_sees_commercial_forecast_separate_from_delivery_quantity --noinput` | 2 pruebas pasaron en 4.126 s sobre la base más reciente; validan Django y la salida de Streamlit. |
| Mismo test Django antes de la corrección de COR-02 | Falló al no encontrar `Venta registrada el 07/01/2026: 3 kg`; el HTML mostraba `Venta registrada el : 3 kg`, reproduciendo el defecto. |
| Mismos dos tests focalizados después de COR-02 | 2 pruebas pasaron en 3.743 s; el test Django incluye fecha histórica no nula y cantidad con unidad. COR-01 solo tiene ajuste documental; no se ejecutó Docker. |
| `manage.py test operations tests --noinput` | 145 pruebas pasaron en 168.212 s después del rebase; el runner reportó `System check identified no issues`. |
| `manage.py test operations tests --noinput` en `a819125` | 145 pruebas pasaron en 123.151 s; exit code 0 y `System check identified no issues`. Ejecutado localmente, no contra una VM desplegada. |
| `manage.py check` | Correcto: sin problemas. |
| `manage.py makemigrations --check --dry-run` | Correcto: no hay migraciones pendientes de generar. |
| `python -m pip check` | Correcto: dependencias consistentes. |
| `SMARTORDER_DEBUG=0` y valores sintéticos de entorno; `manage.py check --deploy` | Código de salida 0; solo W005 y W021 (HSTS subdominios/preload). No se emplearon secretos reales. |
| `SMARTORDER_DEBUG=0`; `manage.py collectstatic --noinput` con `SMARTORDER_STATIC_ROOT` temporal único | 135 archivos copiados y 135 post-procesados; directorio temporal eliminado después del rebase. |
| Parseo PyYAML de `compose.yaml` y aserciones de topología | Correcto: DB en `backend`, web en `backend` + `outbound`, proxy en `backend`, únicos puertos publicados 80/443, red `backend` interna. Validación estática, no prueba de Docker. |
| Búsqueda de patrones de secretos en archivos publicables; `git check-ignore .env staticfiles .local-web/smartorder.sqlite3` | Sin coincidencias de patrones; `.env`, estáticos generados y SQLite local ignorados. No se publican. |
| `git diff --check` y `git diff --cached --check` | Sin errores. |
| `docker --version`; `docker compose version` | No ejecutable: el comando `docker` no está instalado/disponible. No se hizo build, arranque, prueba PostgreSQL ni restauración real. |

Los chequeos locales reproducibles pasan. Siguen pendientes el CI remoto del PR, ejecución con Docker/Compose en la VM, validación de la revisión actual dentro de esa VM, PostgreSQL real, prueba de restauración y validación empresarial.

## Validación posterior en VM (2026-10-30)

| Comando / evidencia | Resultado |
|---|---|
| `VBoxManage clonevm bb03c78e-9e4d-4376-b28c-de19d33c5d90 --name SmartOrder-Validation-Copy-20261030 --basefolder "C:\Users\barri\VirtualBox VMs" --register --mode machine` | Clonación real completada al 100 %. Se trabajó sobre la copia; la VM base `SmartOrder-PoC-20261017` no se modificó. |
| `VBoxManage startvm SmartOrder-Validation-Copy-20261030 --type headless`; `showvminfo` y `guestproperty enumerate` | La copia inició y quedó en estado `running`. Guest Additions informó Ubuntu 24.04.5 LTS y `10.0.2.15`; no había usuarios conectados. |
| `curl.exe --max-time 12 -sS -L -o NUL -w ... http://127.0.0.1:18000/` y navegador en `http://127.0.0.1:18000/` | HTTP 200 tras una redirección a `/entrar/?next=/`; se mostró la pantalla de inicio de sesión Django. |
| `curl.exe --max-time 12 -sS -L -o NUL -w ... http://127.0.0.1:18501/` | HTTP 200 desde Streamlit, respuesta de 7,260 bytes. |

Estos resultados prueban que la copia de la VM arranca y que los servicios ya presentes en la imagen responden por los puertos reenviados. No prueban que la revisión de esta rama esté desplegada: no se transfirió código ni se autenticó en el sistema invitado. El usuario indicó que no dispone de las credenciales de la VM, así que no se intentó iniciar sesión ni se solicitaron secretos. Docker/Compose dentro de la VM, Django bajo esta revisión, PostgreSQL, Caddy/TLS, persistencia, reinicio y respaldo/restauración siguen sin verificar. Los intentos de clonación anteriores no se eliminaron.

## Siguientes fases ejecutables sin datos de Vitali

1. Ejecutar/verificar las pruebas de PostgreSQL en CI sobre la rama consolidada y revisar cualquier fallo.
2. Ejecutar el build/Compose y la secuencia de backup/restore en una VM de prueba autorizada, sin datos reales; no sustituir la evidencia por parseo YAML.
3. Automatizar en CI el chequeo `check --deploy` para una configuración sintética, resolviendo o justificando W005/W021 sin activar políticas de subdominios a ciegas.
4. Recuperar la versión íntegra del plan maestro y completar una matriz rastreable por identificador de requisito y caso de prueba.
5. Revisar la interfaz y accesibilidad tras la integración sobre `origin/main`, y conservar screenshots de pruebas reproducibles si existe entorno de navegador autorizado.

## Bloqueos que requieren información o aprobación empresarial

- Motor/versión, host, esquema, relaciones y credenciales de solo lectura del origen de ventas de Vitali.
- Definición y autorización de campos, unidades, calendario de pedidos/entregas y política de refresco.
- Catálogo oficial cliente–sucursal–SKU–unidad/conversiones y reglas de inventario, mínimos, múltiplos y plazos.
- Datos históricos multianuales y resultados observados para backtesting; fuentes de merma, agotados y producción.
- Entorno/VM, DNS, certificados, firewall/egress, retención/cifrado de backups y responsables de operación aprobados por TI.
- Usuarios piloto, criterios cuantitativos de aceptación, participantes y acta de UAT de Vitali.
- Plan maestro íntegro y autorización explícita para considerar fases de cliente completadas.
- Acceso administrativo autorizado a la VM de validación para instalar y probar la revisión actual; no se han compartido credenciales.

Ninguno de estos bloqueos se resuelve inventando un esquema, simulando datos productivos o usando valores de ejemplo como credenciales.
