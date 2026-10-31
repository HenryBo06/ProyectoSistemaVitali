# Evidencias reales del frontend local

Capturas obtenidas del navegador sobre la rama auditada `origin/main` (`35323af1fbb64495ce7d8e84ee83923d16fceb4a`) con Django ejecutando contra una base SQLite local sintética separada. No muestran usuarios/datos de producción. Las cuentas de captura y DB no se incluyen.

| Archivo | Pantalla | Qué demuestra |
| --- | --- | --- |
| `../evidencias/presentacion-ia/01-acceso.jpg` | Inicio de sesión | Identidad visual Vitali y formulario de acceso. |
| `../evidencias/presentacion-ia/02-dashboard-administrador.jpg` | Centro de operación | Vista administrativa y aviso de que ventas no autorizan fabricación. Los indicadores vacíos reflejan que no se cargaron ventas confirmadas para esta fecha. |
| `../evidencias/presentacion-ia/04-panel-vendedor.jpg` | Mi trabajo | Cartera con un cliente LAB asignado y ruta comercial del vendedor. |
| `../evidencias/presentacion-ia/03-recomendacion-sintetica.jpg` | Productos y sugerencias | Cliente/producto LAB, disponibilidad simulada y cantidad sugerida con la explicación correspondiente. Método de captura: mediana de días equivalentes. |
| `../evidencias/presentacion-ia/05-formulario-venta.jpg` | Nueva venta | Flujo para revisar cliente, entrega y línea de producto. Captura anterior a confirmar la venta. |

El valor de 10 kg y la métrica de error cero aparecen únicamente en el material explicativo como resultado de una serie artificial constante. No es una medición de desempeño real. Las capturas prueban que las pantallas se mostraron y el flujo llegó al formulario; no prueban generalización del modelo ni la autorización de producción.

**Reproducción:** ejecuta `python scripts/preparar_smartorder_lab.py` desde una instalación sin datos bajo `.local-web-lab/`; consulta credenciales localmente en el archivo ignorado `credentials.json`. Nunca agregues ese archivo a Git.
