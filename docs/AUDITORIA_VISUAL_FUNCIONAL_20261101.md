# Auditoría visual y funcional preliminar — 1 de noviembre de 2026

## Alcance y evidencia

Se revisó la integración local de Django y el proceso independiente de Streamlit con una base aislada y datos ficticios (Cliente A, Alitas y Chorizo). Las capturas reales están en [evidence/auditoria-20261101](evidence/auditoria-20261101/); cada nombre identifica interfaz, pantalla y tamaño (escritorio 1440 × 900, móvil 390 × 844). No son maquetas. El flujo capturado no equivale a aceptación comercial ni a verificación de las 36 historias y 54 casos; véase [matriz inicial del plan](MATRIZ_PLAN_MAESTRO_20261101.md).

| Pantalla / recorrido | Django | Streamlit | Resultado y límites |
|---|---|---|---|
| Inicio de sesión | Capturado | Capturado | Credenciales de prueba, no cuentas reales. |
| Panel vendedor | Capturado | Capturado | Acceso con vendedor ficticio. |
| Productos y recomendaciones | Capturado | Capturado | Con datos sintéticos insuficientes para generar una recomendación automática; no demuestra precisión del pronóstico. |
| Creación, revisión y confirmación | Capturado | Capturado | Cantidades introducidas manualmente. En Django: 3 kg, USD 12,50 y 10 %; total mostrado USD 33,75. |
| Panel administrativo | Capturado | Capturado | Acceso con administrador ficticio. |
| Revisión y autorización | Capturado | Capturado | La confirmación de venta y la autorización administrativa se realizaron por separado. |

La funcionalidad capturada terminó el recorrido de prueba; esto no declara completa ninguna pantalla ni verifica todos sus controles, estados vacíos, errores, dispositivos o accesibilidad. El menú de Streamlit queda abierto y tapa gran parte de algunas capturas móviles: esas imágenes evidencian la navegación, pero no permiten evaluar bien todo el contenido. En una captura móvil Django la barra fija se superpone a parte del formulario: debe evaluarse durante la auditoría. Las tarjetas extensas y los textos repetidos hacen largo el formulario móvil. La interfaz Streamlit mantiene componentes y navegación propios, con una presentación diferente a Django; hace falta revisar coherencia y densidad visual. No se identificó un control roto en el recorrido ejercitado; los controles y escenarios no ejercitados permanecen sin verificar.

## Diseño y requisitos

La integración conserva el frontend Django de Isaac incorporado desde el PR #7: plantillas, navegación y estilos constituyen la base del resultado, con correcciones posteriores. No se reemplazó por React. Frente a esa base, las capturas permiten revisar jerarquía visual, longitud de tarjetas y navegación móvil; no establecen por sí solas una calificación profesional o aprobación de diseño.

La [propuesta Lovable](https://lovable.dev/projects/b2e04f04-580e-4a6c-883e-27d2c61518f9) es un prototipo de referencia: pueden considerarse su jerarquía más limpia, navegación y organización de información para adaptar con plantillas Django o componentes Streamlit. Sus datos son ilustrativos, incluidas cifras de capacidad y datos comerciales; no acreditan funcionalidades ni autorizan mostrar históricos individuales al vendedor. No se ha copiado su código React. La comparación con el plan maestro sigue siendo parcial: roles, venta separada de autorización, privacidad y entrada manual se contrastaron en los recorridos indicados; los demás criterios requieren revisión individual y aceptación por usuarios.

## Permisos, pronóstico y límites

El vendedor de prueba ve sus clientes asignados, productos, cantidades y condiciones comerciales del flujo; la revisión del backend y pruebas comprueban que las tarjetas de vendedor excluyen fecha y cantidad de ventas históricas individuales. Se verificó además un rechazo 403 al intentar acceder a un cliente ajeno. El administrador ve el panel de revisión y puede autorizar producción por separado. Esto no demuestra una auditoría exhaustiva de todas las rutas o posibles filtraciones.

Las pruebas automatizadas cubren el caso en que un pronóstico comercial existe pero no se convierte en cantidad sugerida ni se rellena automáticamente en el formulario de Django y la interfaz Streamlit. El recorrido visual empleó entrada manual y datos sin pronóstico elegible, por lo que no prueba visualmente todos los casos de pronóstico ni permite afirmar que nunca pueda ocurrir en cualquier ruta futura. La discrepancia entre fecha objetivo comercial y fecha de entrega sigue pendiente de definición y validación integral (COR-03).

Los datos, inventario y órdenes usados para las capturas son ficticios; los ejemplos de Odoo son simulados y no prueban integración real ni fabricación. No se probó el despliegue, restauración, reinicio, TLS, PostgreSQL ni aceptación en la VM Ubuntu: no hay sesión administrativa disponible. No se debe interpretar esta evidencia como disponibilidad de producción.

## Pruebas y documentación

La suite de la integración ejecutada antes de esta documentación registró 147 pruebas satisfactorias; el registro está en [suite.log](evidence/integracion-20261101/suite.log). La evidencia de navegador cubre los recorridos anteriores y no sustituye una evaluación completa de los controles. El documento histórico DOCX se pudo abrir; el PDF histórico presenta una tabla de referencias dañada y no se ha regenerado. Se requiere auditoría externa antes de decidir cambios visuales importantes.
