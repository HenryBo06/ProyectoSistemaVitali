# Evidencia de correcciones del PR #11

Fecha: 2026-11-02. Capturas reales de Django y Streamlit en navegador, con cuentas y registros ficticios cargados en una base SQLite temporal. El escenario de forecast/sugerencia de Django se inyectó solo en el proceso de captura; no se cambió el código de producción para generar esos valores.

## Cantidades y unidad comercial

- [Venta: forecast ausente, sugerencia 5 kg y cantidad solicitada vacía](django-venta-falta-pronostico-movil.png)
- [Reposición: sugerencia 5 kg, sin forecast ni oportunidad comercial](django-reposicion-sugerida-movil.png)
- [Venta: oportunidad 18 kg y sugerencia de entrega 5 kg separadas](django-venta-ambos-valores-movil.png)
- [Recomendaciones de venta: ambas cantidades diferenciadas](django-recomendaciones-venta-ambos-movil.png)
- [Recomendaciones de reposición: sugerencia 5 kg](django-recomendaciones-reposicion-movil.png)
- [Streamlit móvil: próximas entregas con cliente, fecha y estado visibles](streamlit-dashboard-proximas-entregas-movil.png)

## Revisión comercial y producción independiente

- [Formulario con desplazamiento real en móvil](django-venta-falta-pronostico-movil.png)
- [Formulario seleccionado en escritorio, 1440 × 900](django-venta-scroll-escritorio.png)
- [Revisión de venta en escritorio, 1440 × 900](django-venta-revision-escritorio.png)
- [Revisión de venta en móvil, 390 × 844](django-venta-revision-movil.png)
- [Venta confirmada por vendedor; producción sigue pendiente](django-venta-confirmada-produccion-pendiente-movil.png)
- [Autorización administrativa pendiente, escritorio](django-admin-autorizacion-pendiente-escritorio.png)
- [Producción autorizada por administración, escritorio](django-admin-produccion-autorizada-escritorio.png)

## Medidas de desplazamiento

- Django a 390 × 844: cabecera sticky en `top=0`, alto 64 px. Después de `PageDown` real la cabecera quedó sobre el contenido, el producto comenzó aproximadamente en `y=67` y el campo de cantidad en `y=400`; no se tapó ningún control. En revisión, la cabecera quedó en `y=0–64` y el título visible comenzó en `y=202`; la vista cabía en el viewport.
- Django a 1440 × 900: cabecera sticky en `top=0`, alto 72 px. Durante desplazamiento real, el formulario administrativo quedó entre `y=167–346`; el encabezado no lo cubrió. En el formulario de venta la altura excedente fue de 23 px y no ocultó controles.
- Streamlit a 390 × 844: el resumen de próxima entrega ocupó una fila adaptable; la fecha y el estado completos quedaron en un bloque de 323 px de ancho. El dashboard no renderizó ningún `stDataFrame`; la regla CSS de ocultamiento permanece limitada al contenedor del catálogo de escritorio.

La superposición que parecía verse en capturas de página completa no se reprodujo al desplazarse en el navegador: el sticky permanece en la parte superior y los controles revisados quedan por debajo. No se modificó la cabecera.

## Privacidad

La prueba de regresión inserta valores centinela de fecha y cantidad histórica individual en la tarjeta simulada y verifica que no aparezcan en las dos pantallas del vendedor. Las capturas contienen solo el conjunto ficticio `Cliente Demo` / `audit-*`.

## Validación

La línea base de PR #11 tenía 147 pruebas. Esta corrección añade siete pruebas de regresión y no elimina métodos ni archivos de prueba. El comando exacto de CI, `python manage.py test operations tests --noinput`, descubrió y aprobó **154 pruebas en 232.997 s** en la verificación final.

También pasaron `python manage.py check`, `python -m compileall -q streamlit_app.py operations vitali_web`, `node --check operations/static/operations/order.js`, `git diff --check` y `git check-attr` para los documentos PDF/DOCX.

La prueba de Streamlit verifica cliente, fecha y estado de una entrega próxima. En el dashboard móvil la tabla del catálogo no oculta la información operativa. La unidad comercial se toma de la tarifa autorizada; cuando falta, se pide confirmación y no se infiere ni convierte desde inventario. El scroll real no reprodujo superposición con la cabecera sticky, así que no se cambió.

La venta sintética quedó confirmada por vendedor y producción se autorizó después por separado mediante administración.

## GitHub Actions

El PR #11 permanece en borrador contra `codex/integracion-vitali-20261101`. El workflow se ejecuta automáticamente con PR dirigido a `main` o mediante `workflow_dispatch`; este PR no dispara CI automáticamente. La ejecución manual debe registrarse desde una sesión GitHub autorizada después de publicar este commit. No se cambiará el destino del PR para simular una ejecución.
