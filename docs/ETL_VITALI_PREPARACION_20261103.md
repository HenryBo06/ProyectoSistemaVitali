# ETL de ventas flexible — SmartOrder Vitali

Implementado: extracción e inspección sin mutaciones de CSV UTF-8 y XLSX, selección explícita de formato de fecha y separador decimal, mapeo configurable de cabeceras a un contrato canónico, controles de calidad, detección de columnas nuevas, presencia de referencias repetidas y estadísticas de cobertura. Sólo administración puede ver muestras y encabezados. Limitado a 20 MB y 150 mil filas, con vista previa de cinco registros y como máximo 25 problemas detallados.

**No se activa una fuente por inspeccionarla.** La integración de la etapa Load con las tablas de ventas existentes debe esperar a que se confirme el esquema real del CSV y la granularidad/identificadores de Odoo. Sin identificador único fiable no se deben ejecutar upserts incrementales ni combinar snapshots de forma automática. Se conservarán los contratos, pruebas y verificaciones manuales.

El gestor de conexiones PostgreSQL existente se mantiene separado: conexión autorizada no implica autorización para extraer tablas arbitrarias ni modificar la fuente. Conectar el extractor SQL requiere consultas predefinidas, permisos SELECT y aprobación del origen; no se ejecutan consultas SQL proporcionadas en la UI.

Futuras etapas: perfiles persistentes y versionados por fuente, staging transaccional, conciliación, auditoría de ejecución, cargas idempotentes con llave de negocio, cuarentena de filas fallidas, rollback mediante versionado y observabilidad de deriva.
