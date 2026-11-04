# SmartOrder AI · Vitali — Auditoría de cierre parcial (03/11/2026)

## Alcance
Revisión de la rama `codex/smartorder-densidad-20261101` / Draft PR #11. Se conservaron los frontends Django y Streamlit y la lógica de negocio compartida. La referencia Lovable es visual y contiene datos ficticios; no se integró React.

**Este documento es un control de cierre parcial, no una aceptación de Vitali ni autorización para fusionar o desplegar.** La matriz inicial contiene 36 HU y 54 casos y no se debe convertir automáticamente en «completada» por tener una suite CI verde.

## 1. Identidad y ergonomía
- Se reutiliza el SVG multicolor que YA estaba versionado en `operations/static/operations/favicon.svg`, tanto en la cabecera, menú e inicio de sesión de Django como en la identidad y el encabezado de Streamlit.
- Colores recuperados del sistema original: turquesa `#0c6f7c`, turquesa oscuro `#085863`, verde `#5ba744`, naranja `#e07c2a`, amarillo `#f6cd28`, rojo `#dd3a42`, cian `#42b7d3` y azul `#1479a5`.
- El SVG es **el activo de marca existente en este repositorio**. No se ha confirmado por separado que sea el logotipo oficial vigente de la compañía.
- Se conservan tarjetas compactas, pestañas horizontales «Venta / Reposición / Conteo», jerarquía tipográfica reducida, campos táctiles y acceso móvil.
- Verificación: pruebas HTML/CSS y navegador real con datos del laboratorio. La accesibilidad integral, el zoom 200 %, contraste, lectores de pantalla y UAT no se declaran completados.

## 2. ¿Funcionan las oportunidades por festividades?
**Sí, para detectar y mostrar ocasiones a revisar**, con límites explícitos:
1. Administración genera candidatos para un año (de ocho años antes a tres años después del actual), o crea un evento local; todos los candidatos nuevos empiezan con `reviewed=False` y `active=False`.
2. Administración revisa la fuente, la fecha, el territorio y activa el evento. Solo los eventos activos y revisados se presentan a vendedores.
3. Para cada cliente asignado, el tablero identifica un producto con ventas históricas en el catálogo activo y muestra una **oportunidad de contacto comercial** si un evento nacional o de la zona comienza dentro de siete días. Es una sugerencia contextual, **no un cálculo probado de aumento de demanda**.
4. Las notificaciones se generan durante la semana anterior (una vez por evento/cliente/producto), con un aviso adicional el mismo día; antes solo se generaban exactamente en D-7 y D-0.
5. La pantalla de venta vincula explícitamente el evento si es elegible para la cartera/zona. Un ID inválido o de otra zona no se sustituye silenciosamente por otro evento nacional.
6. Django y Streamlit comparten estas reglas del servidor. Las pruebas del laboratorio simulan una «Feria LAB (simulada)» y examinan su visibilidad en ambos frontends.

**Calendario de ejemplo para El Salvador:** Año Nuevo; Jueves, Viernes y Sábado Santos; 1.º de mayo; 10 de mayo; 17 de junio; Fiestas Agostinas (el asueto de 3/5 de agosto depende del distrito de San Salvador; el 6 es nacional); Independencia; Difuntos; Navidad. La ventana comercial de Navidad y otras temporadas es una hipótesis operativa para revisión, NO un asueto o patrón de consumo demostrado.

Referencias externas:
- Ministerio de Trabajo, asuetos de agosto 2026: https://www.mtps.gob.sv/2026/07/31/despliegue-nacional-de-inspeccion-laboral-durante-las-vacaciones-agostinas/
- Artículo 190 del Código de Trabajo: https://leyteca.com/sv/codigo-trabajo/articulo-190/
- Decreto del Día del Padre: https://www.asamblea.gob.sv/sites/default/files/documents/decretos/171117_073305431_archivo_documento_legislativo.pdf

## 3. Predicción cuantitativa por temporada: barrera deliberada
El método `RecommendationEngine._event_sale_forecast` requiere:
- Cuatro o más fechas históricas equivalentes para el mismo cliente/producto, con cobertura válida.
- Al menos dos ventanas útiles de validación temporal.
- Comparación con una línea base sin mayor error ni sobreestimación.
- Datos y unidades de venta válidos, fuente verificada, actualidad del corte y semántica de fechas compatible.

La mera aparición de una festividad **no autoriza a sugerir 5, 10 o 100 kg**. La prueba de contexto de temporada puede pasar aunque el pronóstico numérico de temporada siga siendo manual. El calendario histórico es solo preparación, no sustituye ventas de años anteriores.

## 4. Criterios de negocio que no se deben confundir
- Evento/temporada: posible ocasión de contacto, aún por confirmar con el cliente.
- Pronóstico comercial: estimación del día comercial solo con evidencia válida.
- Cantidad recomendada para reponer: concepto distinto, basada en las reglas autorizadas.
- Cantidad pedida: la introduce o confirma expresamente el vendedor.
- Autorización de producción: independiente y exclusiva de administración.
- **COR-03 pendiente:** debe definirse la relación entre fecha de actividad comercial, fecha objetivo del modelo y entrega solicitada. Actualmente algunos enlaces de evento usan la fecha del evento como fecha solicitada del formulario; no se considera aprobada esa equivalencia para producción.

## 5. Matriz de cierre responsable

| Ámbito | Evidencia disponible | Resultado / pendiente |
|---|---|---|
| Django vendedor: cartera, recomendaciones, pedido, confirmación | Tests, laboratorio sintético, capturas PR #11 | Funcional en los escenarios probados; sin UAT real |
| Streamlit vendedor: cartera, catálogo y compra | Bridge compartido, pruebas, capturas en navegador | Recorridos específicos probados; falta aceptación integral |
| Separación vendedor/administración, históricos individuales | Tests por rol y autorización | Controles ejercitados; requiere auditoría global de permisos antes de producción |
| Reposición del cliente, distinta del pronóstico | Tests específicos y navegador | Probado con datos sintéticos |
| Festividades y avisos | Candidatos fuente/revisión, contexto por zona, pruebas del motor y navegador | Oportunidades contextuales funcionales; predicción festiva cuantitativa no demostrada con datos reales |
| Calendarios retrospectivos | 8 años previos disponibles en UI y servidor | Candidatos sin activar; fuentes y datos históricos deben revisarse |
| CI SQLite, JavaScript, checks y PostgreSQL temporal | GitHub Actions sobre PR #11 | Consultar la ejecución del último commit; no asumir éxito de commits anteriores |
| Conexión real Odoo / ERP | Simulaciones y pruebas del puente | **Pendiente** de credenciales y entorno externo |
| VM, Docker/TLS/LAN/VPN, restauración, respaldo y monitoreo | Configuración o material preparatorio | **Pendiente** de ejecutar en VM final y verificar con infraestructura autorizada |
| COR-03, datos de ventas verificados, definición comercial | Dependencia de Vitali | **Pendiente** |
| UAT, accesibilidad integral, rendimiento con carga real | No hay aceptación formal | **Pendiente** |

## 6. Orden de cierre sugerido
1. Revisar el CI y las capturas **del último SHA del PR #11**; corregir cualquier fallo antes de dar por válido el rediseño de marca o temporada.
2. Validar con Vitali COR-03 (objetivo comercial vs. fecha de entrega) y confirmar cuáles temporadas interesan a cada cartera y producto; la zona debe ser explícita para eventos locales.
3. Importar ventas reales históricas con cobertura y unidad verificada; revisar/activar eventos retrospectivos pertinentes, evaluar series elegibles y comparar con el pronóstico normal.
4. Completar revisión de permisos, UAT por roles, accesibilidad y manejo de errores/estados vacíos.
5. Validar VM real: persistencia, reinicio, backups/restauración, HTTPS/LAN, observabilidad y conectividad Odoo. No declarar producción lista antes de esta fase.

**Restricción:** mantener PR #10 y #11 en borrador y `main` intacto, hasta decisión explícita del responsable.
