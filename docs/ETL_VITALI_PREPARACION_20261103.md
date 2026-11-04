# ETL de ventas flexible — SmartOrder Vitali

Implementado: extracción e inspección sin mutaciones de CSV UTF-8 y XLSX, selección explícita de formato de fecha y separador decimal, mapeo configurable de cabeceras a un contrato canónico, controles de calidad, detección de columnas nuevas, presencia de referencias repetidas y estadísticas de cobertura. Sólo administración puede ver muestras y encabezados. Limitado a 20 MB y 150 mil filas, con vista previa de cinco registros y como máximo 25 problemas detallados.

**No se activa una fuente por inspeccionarla.** La etapa Load ya utiliza el importador canónico `import_sales` y conserva los modelos existentes. Sigue pendiente decidir las claves de línea y la semántica de fechas para cualquier carga incremental; sin identificador único fiable no se deben ejecutar upserts ni combinar snapshots automáticamente. Se conservan los controles de verificación humana.

El gestor de conexiones PostgreSQL existente se mantiene separado: conexión autorizada no implica autorización para extraer tablas arbitrarias ni modificar la fuente. Conectar el extractor SQL requiere consultas predefinidas, permisos SELECT y aprobación del origen; no se ejecutan consultas SQL proporcionadas en la UI.

Futuras etapas: perfiles persistentes y versionados por fuente, staging transaccional, conciliación, auditoría de ejecución, cargas idempotentes con llave de negocio, cuarentena de filas fallidas, rollback mediante versionado y observabilidad de deriva.

## Perfiles inmutables y auditoría
Se pueden registrar perfiles de mapeo por alias y versión, con fecha/decimal/hoja. Cada inspección deja únicamente metadata (huellas SHA-256, volumen, fallos y actor); nunca contenido del CSV ni valores de clientes. Cuando un mismo perfil ve un esquema de columnas distinto al anterior, bloquea la preparación hasta que administración revise y genere otra versión del mapeo. El módulo de vista previa sigue sin activar fuentes ni modificar datos de inferencia.


## Integración con el importador heredado (carga aprobada)

La etapa de carga de snapshots completos pasa ahora por \`/administracion/etl/\`:
1. Administración registra un perfil inmutable, con las columnas obligatorias y la unidad comercial.
2. Inspecciona CSV/XLSX; el servidor guarda solo huellas y métricas del archivo, no los datos.
3. En una segunda operación, administración selecciona la inspección exitosa y vuelve a subir el mismo archivo. Se exige coincidencia SHA-256 y revalidación de estructura y filas.
4. Se confirma explícitamente origen real/simulado, significado de fecha y que el archivo reemplaza por completo el histórico activo. No se permite importar silenciosamente un delta.
5. El ETL convierte a XLSX canónico en memoria y delega persistencia a \`import_sales\`: conserva modelos, cobertura, versionado y contratos de inferencia existentes. El historial previo no se borra.
6. \`EtlLoadRun\` impide repetir el mismo archivo fuente y enlaza inspección, versión de perfil, dataset, autor y fecha; \`AccessEvent\` guarda metadatos del cambio.

La carga clásica XLSX sigue disponible temporalmente para no romper integraciones y pruebas previas. La retirada definitiva requiere actualizar el recorrido de aceptación y migrar todos los consumidores; hasta entonces es una ruta heredada que debe usarse solo por compatibilidad.

Los datos reales todavía necesitan las decisiones comerciales COR-03 y de cobertura. Esta etapa **no** implementa mezcla incremental de snapshots, extracción SQL ni conexión con Odoo real, ni acredita aceptación empresarial. Cuando el origen carece de unidades, administración puede elegir presentaciones provisionales kg/unidad/lb; estas cargas permanecen **sin verificar** para proteger las sugerencias automáticas.


## Compatibilidad con el Excel de demostración 2025

El archivo de demostración proporcionado fuera del repositorio contiene una hoja \`Ventas2025\` de 10 columnas y 1.294 registros entre el 1 de enero y el 31 de diciembre de 2025. Contiene 10 clientes y 8 productos, pero no incluye \`Unidad\`, \`Referencia_pedido\` ni \`Referencia_linea\`. **El archivo original no se publica ni se modifica en GitHub.** Esta descripción no prueba que el origen sea empresarial real.

Para cargarlo por el nuevo ETL, administración debe crear un perfil de mapeo, inspeccionar el archivo, confirmar manualmente la unidad comercial de cada producto (no inferirla por el nombre) y volver a subir el mismo archivo. La aprobación queda asociada a la huella del archivo y su perfil. Cuando la procedencia sea simulada, la carga permanece marcada como simulada y no queda verificada como fuente real.

El inspector expone la proporción de combinaciones cliente-producto-unidad-fecha observadas. Los huecos del histórico de demostración no equivalen necesariamente a ventas cero: ninguna cobertura comercial pasa a COMPLETA sin revisión humana. La ausencia de referencias de línea sigue impidiendo concatenar cargas incrementales de forma segura. El dato de 2025 es histórico, no una garantía de pronóstico preciso en fechas futuras ni prueba de la fecha comercial COR-03.


## Presentaciones provisionales para la exposición

Mientras Vitali no proporcione su catálogo oficial, la interfaz de asignación de unidades faltantes ofrece exclusivamente **kilogramos (\`kg\`)**, **unidades (\`unidad\`)** y **libras (\`lb\`)**, sin seleccionar automáticamente ninguna presentación por producto. Las opciones son hipótesis de trabajo aportadas por el equipo, **no una confirmación empresarial**.

- El ETL rechaza asignaciones manuales distintas de esas tres opciones (incluidas cajas o docenas).
- No convierte libras a kilogramos ni agrega ventas de unidades incompatibles. La cantidad original se conserva con la unidad seleccionada.
- Cuando alguna fila requiere una unidad asignada provisionalmente, el histórico nuevo permanece **sin verificar**, aun si el archivo fue declarado de procedencia real. Los controles de cobertura e inferencia automática se mantienen.
- Un archivo que ya trae \`Unidad\` no se reescribe ni convierte silenciosamente. Las unidades explícitas ajenas al catálogo provisional requieren revisión comercial antes de estandarizarse; este cambio no modifica históricos previos.
- El registro de auditoría documenta \`unit_assumption_status=provisional\` y \`units_pending_company_confirmation=true\` sin guardar datos comerciales del archivo.


## Aceptación de venta contextual y unificación de interfaces

La prioridad histórica de productos utiliza **días distintos con venta por cliente-producto** en el histórico activo; ordena por mayor recurrencia y USD solo para empates. No compara cantidades entre kg, unidad y lb. Vendedores ven posición y frecuencia, no cifras internas de facturación del cliente; si los datos están desactualizados o no verificados, ninguna prioridad histórica se convierte en cantidad predictiva. Django y Streamlit consumen el mismo \`recommendation_cards\` y preservan el mismo orden.

Streamlit dispone ahora de la **misma ruta de aprobación ETL** que Django (no otro cargador): ejecuta las vistas Django con el puente autenticado, exige perfil versionado, huella SHA-256, inspección válida, segunda carga idéntica y las tres confirmaciones. Si el archivo carece de \`Unidad\`, ofrece exclusivamente kg/unidad/lb por producto y mantiene el histórico sin verificar. Entre reruns solo almacena el resumen de inspección ligado al administrador; no persiste en memoria de sesión los bytes del origen. La ruta clásica XLSX permanece como compatibilidad temporal, expresamente identificada como tal.

### Evaluación retrospectiva diagnóstica del Excel proporcionado

En el Excel 2025 aportado por el usuario (no versionado en GitHub) hay 1.294 filas, 10 clientes y 8 productos. Se observan 80 pares cliente-producto y 365 fechas distintas a nivel global; los pares solo cuentan con 7–26 transacciones registradas. En una evaluación de cada serie ordenada cronológicamente (empatando fechas iguales por el orden original del Excel), con el 75 % inicial de eventos como historial y el tramo restante como prueba secuencial, la mediana de las últimas cuatro cantidades observadas obtuvo MAE 220,68 en 354 transacciones de prueba, contra MAE 267,86 del último evento. La comparación de MAE utiliza las cantidades numéricas como ejercicio diagnóstico y no equivale a una métrica de demanda física válida entre productos con unidades no confirmadas. Esto **solo evalúa cantidad por transacción condicionada a que existe una transacción**, no el día ni la demanda futura, y las cantidades carecen de unidad comercial validada. No es una validación del pronóstico de 7 días del motor productivo ni autoriza pedidos automáticos. Los días no observados no se imputan como cero en esta prueba.

Para aceptación MVP, validar el recorrido de cliente-producto, consulta, cantidad manual, revisión administrativa y exportación. Las acciones reales sobre inventario/producción y pronóstico operativo requieren datos recientes, fechas, cobertura y unidades confirmadas por Vitali.
