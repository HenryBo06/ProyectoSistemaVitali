# ETL de ventas flexible — SmartOrder Vitali

Implementado: extracción e inspección sin mutaciones de CSV UTF-8 y XLSX, selección explícita de formato de fecha y separador decimal, mapeo configurable de cabeceras a un contrato canónico, controles de calidad, detección de columnas nuevas, presencia de referencias repetidas y estadísticas de cobertura. Sólo administración puede ver muestras y encabezados. Limitado a 20 MB y 150 mil filas, con vista previa de cinco registros y como máximo 25 problemas detallados.

**No se activa una fuente por inspeccionarla.** La integración de la etapa Load con las tablas de ventas existentes debe esperar a que se confirme el esquema real del CSV y la granularidad/identificadores de Odoo. Sin identificador único fiable no se deben ejecutar upserts incrementales ni combinar snapshots de forma automática. Se conservarán los contratos, pruebas y verificaciones manuales.

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

Los datos reales todavía necesitan las decisiones comerciales COR-03 y de cobertura. Esta etapa **no** implementa mezcla incremental de snapshots, extracción SQL ni conexión con Odoo real, ni acredita aceptación empresarial. La unidad comercial debe estar presente en todas las filas antes de activar el histórico mediante ETL.
