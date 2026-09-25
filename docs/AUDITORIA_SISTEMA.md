# Auditoría del sistema y de sus fuentes

**Fecha de revisión:** 25 de septiembre de 2026. **Alcance:** cinco PDF locales, el libro de ventas recibido, código y dependencias del repositorio, base de datos y flujo de interfaz. El Excel y los PDF no se publican en Git; los hallazgos de datos se refieren al archivo denominado `Demo_Ventas_Avicola_2025_IA_Pedidos_v2.xlsx`, no a una operación comprobada de Vitali.

## 1. Definición funcional consolidada

El sistema ayuda al vendedor a decidir **qué producto solicitar, cuánto y para qué fecha**, usando ventas observadas, un pronóstico y datos operativos ingresados por él. El vendedor envía una solicitud interna. Administración revisa, puede ajustar con motivo, descarga un archivo de producción y confirma en la aplicación cuándo lo compartió. El sistema no controla una planta ni un ERP.

Esta definición toma el objetivo común de los PDF y la decisión posterior del responsable del proyecto. Algunos documentos llaman al desarrollo piloto/prototipo; ese término no describe el alcance acordado. La guía actualizada exige sala–SKU, pero la fuente disponible solo permite cliente–producto. La integración ERP aparece como inmediata en `ProyectoIA_UEES_Vitali_PedidosSugeridos.pdf` pp. 9–10 y como pendiente de confirmar/fase posterior en `Guia_Trabajo_IA_Pedidos_Sugeridos_Avicola_Actualizado.pdf` pp. 2–4. Se adopta la exportación revisada por administración hasta contar con especificación y acceso reales.

| Fuente local revisada | Aporte verificable |
| --- | --- |
| `Definicion_Problema_Solucion_Alcances_Vitali_IA.pdf`, pp. 1–2 | Problema de pedidos basados en ventas, inventario y revisión humana. |
| `Sistema Inteligente de Pedidos Sugeridos.pdf`, pp. 2–4 | Fórmula y flujo de sugerencia para el vendedor. |
| `Transformacion_Tecnologica_Vitali_SmartOrder_AI.pdf`, pp. 1–4 | Objetivo comercial y KPIs de merma/agotados, hoy no medibles con el Excel. |
| `ProyectoIA_UEES_Vitali_PedidosSugeridos.pdf`, pp. 8–10 | Flujo del vendedor e integración ERP propuesta. |
| `Guia_Trabajo_IA_Pedidos_Sugeridos_Avicola_Actualizado.pdf`, pp. 1–6 | Nivel sala–SKU esperado, XGBoost y visualización con semáforos. |

## 2. Inventario y calidad del Excel

Hoja `Ventas2025`, rango `A1:J1295`: **1,294 registros**, del 01/01/2025 al 31/12/2025; **10 clientes, 8 productos, 80 pares cliente–producto y 365 fechas**. Las diez columnas son `Fecha`, `Cliente`, `Zona_Geografica`, `Canal_Distribucion`, `Canal_Venta`, `Producto`, `Categoria`, `Cantidad_kg_unid`, `Precio_Unitario_USD` y `Monto_Venta_USD`. El total calculado de monto es **USD 5,110,481.96**. No se encontraron campos vacíos ni diferencias entre monto y cantidad × precio. La columna de monto contiene fórmulas con resultado guardado. Hay 24 claves repetidas de fecha–cliente–producto, compatibles con ventas separadas del mismo día; no se encontraron filas completamente idénticas.

El libro contiene seis categorías y tres canales de venta (`Retail`, `Horeca`, `Mayorista`). La suma de `Cantidad_kg_unid` **no es un KPI global**: mezcla kg, unidades y potencialmente docenas. El tablero compara productos por USD y muestra cantidades solo dentro del producto. Tampoco se puede inferir frecuencia de compra real por cliente si varias líneas representan una misma operación, pues no hay identificador de transacción.

**Campos que faltan:** identificador de sala/sucursal, SKU, unidad oficial por producto, inventario disponible, pedidos pendientes, inventario objetivo, pedido mínimo, múltiplo de empaque, fecha requerida, plazo de entrega, promociones, vida útil, mermas, agotados y capacidad/estado de producción. Los campos operativos indispensables se recogen en pantalla sin valores inventados. SKU y unidad oficial quedan pendientes de catálogo; la exportación actual usa nombre de producto y unidad confirmada por el vendedor.

El archivo cubre un año. No permite evaluar estacionalidad entre años ni demostrar que una recomendación de septiembre de 2026 fue precisa. Tampoco sirve por sí solo para medir reducción de mermas, agotados o sobreproducción, aunque esos KPI figuren en los PDF.

## 3. Inventario de la aplicación

| Componente | Función actual | Control o límite |
| --- | --- | --- |
| `smartorder/data.py` | Lee XLSX, valida esquema, importes y rangos; crea series diarias. | Carga máxima 20 MB/150,000 filas; 250,000 celdas diarias para evitar expansión desmedida. |
| `smartorder/forecast.py` | Entrena XGBoost, compara promedio de 28 días por producto y prepara pronóstico de siete días. | Ocho ventanas internas de validación; un año no valida salto interanual. |
| `smartorder/orders.py` | Calcula cantidad con inventario/reglas y genera XLSX de pedidos revisados. | Requiere unidad ingresada; protege texto exportado frente a fórmulas de Excel. |
| `smartorder/storage.py` | SQLite para cuentas, asignaciones, fuente activa, inventario ingresado, solicitudes y revisiones. | Contraseñas derivadas con PBKDF2; roles, bloqueo por intentos, invalidación de sesión al cambiar contraseña. |
| `app.py` | Panel de administración, cartera y recomendación del vendedor, revisión y descarga a producción. | Acceso inicial protegido por código en despliegue de red; modo local explícito y restringido a loopback. |
| `tests/test_system.py` | Comprobaciones de datos, modelo, permisos, pedidos y exportación. | No sustituyen validación con procesos reales ni prueba de persistencia en Cloud. |

La base SQLite tiene tablas `users`, `assignments`, `datasets`, `settings`, `operational_inputs`, `decisions` y `reviews`. Al activar otro Excel, las solicitudes sin entregar del anterior quedan marcadas como **Fuente sustituida** y se requiere una nueva solicitud; las ya exportadas conservan su trazabilidad. Se bloquean solicitudes con horizontes de siete días superpuestos para el mismo cliente y producto, incluso si el Excel cambió después de una exportación. Las cantidades de varios pedidos solo se agregan cuando coinciden **producto, unidad y fecha requerida**.

## 4. Hallazgos y estado

| Prioridad | Hallazgo | Estado al cierre |
| --- | --- | --- |
| Alta | El vendedor podía quedar sin productos al no tener clientes asignados o al reemplazarse el Excel. | Interfaz aclara la causa; administración asigna clientes al crear la cuenta y ve cuentas sin clientes válidos. |
| Alta | Faltaba el paso de administración entre propuesta y producción. | Solicitud pendiente, revisión con motivo, XLSX y confirmación manual de entrega implementados. |
| Alta | El panel principal mostraba WAPE/MAE técnicos; el error global sumaba productos de medidas diferentes. | Resumen comercial con filtros y gráficos; diagnóstico técnico por producto en sección secundaria. |
| Alta | El primer visitante de una instancia nueva podía crear el administrador. | Instancia de red exige `SMARTORDER_SETUP_CODE` privado de al menos 20 caracteres; modo local explícito y servidor en `127.0.0.1` o `::1`. |
| Alta | Un cambio de Excel podía dejar pedidos anteriores disponibles para revisar/exportar. | Se advierte en interfaz y se bloquean solicitudes ajenas al archivo activo en las operaciones de almacenamiento. |
| Alta | Dos fechas de análisis cercanas podían producir pedidos duplicados para los mismos días. | Se bloquean horizontes superpuestos; un pedido nuevo comienza después del horizonte anterior o requiere rechazar la solicitud pendiente. |
| Media | Un Excel con fechas o demasiados pares extremos podía expandir la serie diaria y agotar memoria. | Límite previo a la expansión; se rechaza con mensaje. |
| Media | El cambio de contraseña no cerraba sesiones anteriores. | Versión de sesión invalidada al cambiar contraseña o estado de cuenta. |
| Alta, abierta | SQLite y cargas en disco local no tienen persistencia garantizada en Streamlit Community Cloud. | Para uso compartido se requiere base y almacenamiento persistentes externos, con migración de datos y copias de seguridad. |
| Alta, abierta | No existe identificador de sucursal ni SKU/unidad oficial. | Obtener catálogo y ventas/inventario por sala antes de afirmar recomendaciones sala–SKU o integración automática. |
| Media, abierta | No hay métricas reales de merma, agotados, cumplimiento de entrega o capacidad de planta. | Definir fuentes de esos eventos y KPIs cuando existan datos operativos. |
| Media, abierta | No hay validación prospectiva interanual ni mejora probada frente al promedio. | Recibir varios años, medir por producto y horizonte; no atribuir ahorros al modelo. |

## 5. Interpretación y próximos datos necesarios

El número visible como “pronóstico” estima cantidad para siete días en la medida original del Excel. El pedido sugerido agrega inventario objetivo y resta inventario disponible y pedidos pendientes; después aplica mínimo y múltiplo ingresados. Una recomendación para 2026 usa datos de 2025 como referencia si faltan ventas recientes, por lo que requiere revisión humana. **“Exportado” significa que administración confirmó manualmente haber descargado y compartido el XLSX**; la aplicación no recibe acuse de producción ni confirma fabricación.

Para evolucionar a operación por sucursal se necesitan, como mínimo, ventas con `sucursal_id`/`sku`, catálogo con unidad y conversiones, inventario y pedidos abiertos por sucursal, calendario/tiempo de entrega y una regla de quién solicita, aprueba y recibe. Para medir el objetivo de evitar sobreproducción también hacen falta producción efectuada, venta final, inventario final y merma con fecha y producto. La selección de almacenamiento y privacidad para Cloud depende del entorno de Vitali; no debe resolverse simulando persistencia en el disco temporal.

## 6. Verificación realizada

Se ejecutó la suite `python -m unittest discover -s tests -v` en el entorno del proyecto, pruebas del flujo de interfaz con `streamlit.testing.v1.AppTest`, revisión de dependencias con `pip check` y comprobación de formato con `git diff --check`. Las pruebas abarcan acceso por rol, asignaciones, cálculo, rechazo de Excel inválido, límites de memoria, revisión administrativa y exportación segura. La comprobación local no valida un despliegue externo ni garantiza que un XLSX haya sido realmente entregado a producción.
