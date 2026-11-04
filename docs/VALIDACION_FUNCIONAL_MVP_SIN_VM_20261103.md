# Validación funcional del MVP SmartOrder — sin instalación de VM

**Alcance:** rama `codex/smartorder-densidad-20261101` del PR #11. Toda la automatización corre en runners efímeros de GitHub Actions; **la VM y su despliegue pertenecen a Codex y quedan fuera de esta fase**. La rama `main` no debe modificarse durante estas validaciones.

## Matriz de aceptación

| Caso | Evidencia / método | Resultado esperado |
|---|---|---|
| Calidad de histórico | Auditoría local de `Demo_Ventas_Avicola_2025_IA_Pedidos_v2.xlsx` | 1.294 filas, 10 clientes, 8 productos, 80 parejas cliente-producto, 365 fechas de 2025; sin nulos, negativos ni diferencias en cantidad × precio |
| Grano de los datos | Auditoría local del mismo Excel | 1.270 combinaciones únicas cliente-producto-fecha, 24 filas adicionales en fechas compartidas; no descartar filas sin identificadores de transacción |
| Cobertura de inferencia | Análisis de 80 series | 4,35 % de celdas diarias observadas: no imputar ausencia de transacción como demanda cero sin aprobar cobertura |
| ETL estructural | `operations.test_etl_preview` | Rechazar diferencias SHA, columnas inválidas, referencias duplicadas y cambios de esquema; no activar nada en una inspección |
| Aprobación ETL | `operations.test_etl_preview` + `scripts/visual_smoke_ci.py` | Crear perfil, inspeccionar y re-adjuntar archivo idéntico, aprobar con autorización, registrar origen, perfil y huella |
| Unidades | Pruebas de opciones provisionales | Solo kg, unidad y lb como selección del operador cuando faltan datos; sin conversión ni afirmaciones de verificación empresarial |
| Catálogo comercial | Pruebas `operations.tests` y navegador | Mostrar productos según frecuencia histórica por cliente; no ordenar cantidades incompatibles entre kg, lb y unidad |
| Inferencia | Pruebas `tests` y motor `smartorder.recommendations` | Respetar cortes cronológicos, cobertura, unidades, procedencia y vigencia; series sin garantías mantienen cantidades manuales |
| Pedidos y administración | `operations.tests`, `operations.test_sales_roles`, `operations.test_commercial_pilot` | Registro de pedido, seguridad de tarifas, revisión administrativa, exportación y separación de permisos |
| Django / Streamlit | Prueba de puente + navegador Chromium | Misma lógica y validaciones; escritorio/móvil; vendedores sin permisos de carga ETL |
| Compatibilidad de base de datos | GitHub Actions, SQLite y PostgreSQL 16 efímero | Suite Django completa en ambas bases, migraciones y persistencia entre procesos |
| Funcionamiento en VM | **Excluido de esta fase** | Instalación posterior por Codex cuando se apruebe el MVP |

## Observaciones del Excel recibido

- El archivo analizado es el original proporcionado por el usuario, examinado **localmente**. **No se subió al repositorio**.
- No existe columna `Unidad` ni identificador de pedido/línea. Las denominaciones de producto tampoco prueban la presentación comercial física.
- Los días con ventas no demuestran una cobertura completa por cliente-producto. De un calendario potencial de 29.200 celdas, solo 1.270 aparecen observadas.
- El último registro es del 31/12/2025. Ese histórico se puede utilizar para una exposición, pero no prueba precisión de predicciones sobre pedidos de noviembre de 2026.
- La evaluación retrospectiva de tamaño de transacción es **diagnóstica y ajena al pronóstico automático de siete días**. Sus errores numéricos agregados no son métricas físicamente interpretables sin unidad confirmada.

## Límites de la aceptación técnica

La suite automatizada y la aplicación en un laboratorio sintético no equivalen a aceptación comercial de Vitali. Para pasar a operación real, es necesario recibir y aprobar: unidad por producto, significado comercial de `Fecha`, evidencia de días con cobertura completa, identificadores de líneas cuando exista actualización incremental, tarifas y stock contrastados, y datos recientes.

No habilitar predicción automática por forzar coberturas, alterar `verified` o inventar `kg` / `lb` / `unidad`. En la demostración, la prioridad histórica se puede usar para elegir qué producto **revisar** y la cantidad de entrega se decide manualmente. **No se requiere ni se ejecuta despliegue de VM en esta fase.**


## Hallazgos al ejecutar toda la suite en PostgreSQL 16

La ampliación del CI detectó un defecto operativo que SQLite no manifestaba: la descarga/confirmación de lotes hacía `SELECT ... FOR UPDATE` incluyendo la unión opcional `OrderLine.reviewer`, lo que PostgreSQL rechaza. La consulta se corrigió para bloquear explícitamente `OrderLine` y su pedido padre, sin bloquear la relación opcional.

Se detectaron además tres pruebas de creación de cuentas demo que asumían SQLite y fallaban correctamente bajo PostgreSQL porque **la aplicación prohíbe sembrar cuentas demo en PostgreSQL**. Las pruebas de creación/validación de contraseñas se ejecutan exclusivamente en SQLite y se añadió una prueba explícita de rechazo en PostgreSQL. No se relajó la protección empresarial para hacer pasar los tests.

El flujo de exportación se valida de nuevo en ambos motores, junto con las restricciones de roles y la interfaz Streamlit.
