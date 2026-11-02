# Evidencia del rediseño compacto

Las imágenes son capturas de las aplicaciones Django y Streamlit ejecutándose localmente con datos ficticios. La rama es `codex/smartorder-densidad-20261101`, preparada desde `d835af5`. Las capturas `*-antes-*` registran la versión de PR #10; las capturas sin `antes` son esta rama. En ambas se usó el mismo cliente de prueba y productos equivalentes. Los formularios de pedido muestran dos líneas seleccionadas cuando el nombre indica `pedido-seleccionado` o `lineas-pedido`.

## Cambios observables

- Django conserva las plantillas y la identidad de Isaac. El dashboard compacto prioriza cartera, oportunidad y próxima acción.
- Productos y recomendaciones se presentan como hechos comerciales breves. La explicación se despliega solo cuando hay una sugerencia o una estimación útil; se retiró la frase genérica repetida.
- La cantidad del pedido se introduce manualmente. La oportunidad comercial se etiqueta separada de la cantidad solicitada y no llena el campo.
- El formulario agrupa condiciones y excepciones en secciones opcionales, mantiene motivo de ajuste, tarifa y descuento autorizado y oculta la configuración mientras se revisa el pedido.
- Streamlit conserva su implementación Python y el mismo contrato de negocio. El catálogo móvil usa filas breves, las explicaciones quedan agrupadas y la entrada de cantidades se mantiene manual.
- Vendedor no recibe cantidades históricas individuales, fuente del archivo, métricas del modelo ni detalles de auditoría. La confirmación de venta deja producción pendiente; administración autoriza cada línea por separado.

## Medición de capturas Django

Las capturas Django son de página completa. En escritorio se usó un viewport de 1440 × 900; en móvil, 390 × 844. La anchura renderizada móvil de 375 px corresponde al ancho de contenido del sitio. Las alturas siguientes son las alturas de página capturadas, no el alto del viewport.

| Pantalla y estado | Antes (px) | Después (px) | Cambio de altura |
| --- | ---: | ---: | ---: |
| Dashboard, móvil | 375 × 1734 | 375 × 933 | −46.2 % |
| Recomendaciones, móvil | 375 × 1994 | 375 × 1180 | −40.8 % |
| Pedido sin selección, móvil | 375 × 2589 | 375 × 1220 | −52.9 % |
| Pedido con dos líneas, escritorio | 1425 × 2314 | 1425 × 992 | −57.1 % |
| Pedido con dos líneas, móvil | 375 × 3495 | 375 × 1599 | −54.3 % |

En todos los casos se fijó la ventana del navegador a 1440 × 900. La página anterior tenía barra vertical y su imagen de documento mide 1425 px de ancho; al caber las pantallas nuevas en alto, la imagen ocupa los 1440 px. En las recomendaciones, ambos estados caben dentro de la captura de escritorio 1440 × 900. La cifra es una medición visual de altura de documento, no una comparación de velocidad.

## Django antes / después

| Pantalla | Antes | Después |
| --- | --- | --- |
| Dashboard escritorio / móvil | `django-antes-dashboard-escritorio.png` · `django-antes-dashboard-movil.png` | `django-dashboard-escritorio.png` · `django-dashboard-movil.png` |
| Productos y recomendaciones escritorio / móvil | `django-antes-recomendaciones-escritorio.png` · `django-antes-recomendaciones-movil.png` | `django-recomendaciones-escritorio.png` · `django-recomendaciones-movil.png` |
| Pedido sin seleccionar productos | `django-antes-crear-pedido-escritorio.png` · `django-antes-crear-pedido-movil.png` | `django-crear-pedido-escritorio.png` · `django-crear-pedido-movil.png` |
| Pedido con dos líneas seleccionadas | `django-antes-pedido-seleccionado-escritorio.png` · `django-antes-pedido-seleccionado-movil.png` | `django-pedido-seleccionado-escritorio.png` · `django-pedido-seleccionado-movil.png` |
| Revisión y confirmación de venta | — | `django-revision-venta-escritorio.png` · `django-revision-venta-movil.png` · `django-confirmacion-venta-escritorio.png` · `django-confirmacion-venta-movil.png` |
| Autorización administrativa de producción | — | `django-admin-produccion-pendiente-escritorio.png` · `django-admin-autorizacion-linea-escritorio.png` · `django-admin-autorizado-escritorio.png` · `django-admin-autorizado-movil.png` |

## Streamlit antes / después

Todas las imágenes Streamlit corresponden a viewports de 1440 × 900 y 390 × 844. El menú lateral móvil abierto ocupa 300 px dentro del viewport de 390 px; la página no presenta desbordamiento horizontal.

| Pantalla | Antes | Después |
| --- | --- | --- |
| Dashboard | `streamlit-antes-dashboard-escritorio.png` · `streamlit-antes-dashboard-movil.png` | `streamlit-dashboard-escritorio.png` · `streamlit-dashboard-movil.png` |
| Recomendaciones | `streamlit-antes-recomendaciones-escritorio.png` · `streamlit-antes-recomendaciones-movil.png` | `streamlit-recomendaciones-escritorio.png` · `streamlit-recomendaciones-movil.png` |
| Menú móvil abierto | `streamlit-antes-menu-movil.png` | `streamlit-menu-movil.png` |
| Pedido, dos líneas y revisión | `streamlit-antes-crear-pedido-escritorio.png` · `streamlit-antes-crear-pedido-movil.png` · `streamlit-antes-revision-escritorio.png` · `streamlit-antes-revision-movil.png` | `streamlit-lineas-pedido-escritorio.png` · `streamlit-lineas-pedido-movil.png` · `streamlit-revision-escritorio.png` · `streamlit-revision-movil.png` |
| Venta confirmada | — | `streamlit-venta-confirmada-escritorio.png` · `streamlit-venta-confirmada-movil.png` |
| Administración de producción | — | `streamlit-admin-produccion-pendiente-escritorio.png` · `streamlit-admin-autorizacion-lineas-escritorio.png` · `streamlit-admin-produccion-autorizada-escritorio.png` · `streamlit-admin-produccion-autorizada-movil.png` |

## Recorridos y límites

- Se preparó y confirmó una venta local en Django y otra en Streamlit con cantidades ficticias tecleadas manualmente. El estado posterior a confirmar fue `pending` para producción.
- Administración abrió el flujo de autorización por separado y aprobó las líneas. No se activó ni probó producción real ni sincronización Odoo.
- El menú lateral y el flujo responsive de Streamlit se observaron en navegador. Django y Streamlit son interfaces separadas; no se copió React ni lógica comercial paralela.
- Los datos de navegador se guardaron únicamente en la base ficticia de `.local-web/density-audit/`; esa base no forma parte de la evidencia versionada ni del commit.
- La VM queda pendiente: no hay una sesión administrativa disponible y no se proporcionaron credenciales de acceso.

## Verificaciones

- CI de PR #10: el job histórico registró 147 pruebas aprobadas y falló únicamente en `git diff --check HEAD^ HEAD` porque el PDF se trató como texto. `.gitattributes` asigna el atributo `binary` a PDF y DOCX; los documentos originales no se modificaron.
- La suite local de esta rama descubrió y aprobó 123 pruebas (`manage.py test --verbosity 1`). El número difiere de los 147 reportados por CI en PR #10.
- También pasaron `manage.py check`, `compileall` de `streamlit_app.py`, `node --check` de `order.js`, `git diff --check` y `git check-attr` para los documentos PDF/DOCX.
