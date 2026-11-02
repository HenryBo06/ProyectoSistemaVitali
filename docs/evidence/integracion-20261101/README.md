# Evidencia local — 1 de noviembre de 2026

- Rama: `codex/integracion-vitali-20261101`; base: `56450b11a7951022bd47c3a4d0ccad2819291d7d`. Las capturas corresponden a los cambios de integración de esta rama.
- Entorno: Django y Streamlit en localhost, SQLite temporal independiente, productos y cuentas ficticios. Sin datos de Vitali ni conexión Odoo o Telegram.
- Django: vendedor accedió, consultó su cartera, ingresó manualmente 3 kg de Alitas, revisó USD 33.75 y confirmó la venta. Producción quedó pendiente; un administrador autorizó 3 kg después. El resumen con precio vacío mostró «Tarifa pendiente» y «Total pendiente de tarifa».
- Django: al solicitar `Cliente B`, ajeno a la cartera sintética, el servidor respondió HTTP 403.
- Streamlit: vendedor seleccionó Alitas, ingresó 2 kg y revisó USD 22.50; la venta quedó confirmada y producción pendiente. Un administrador autorizó posteriormente los 2 kg desde Streamlit.
- Capturas: recomendaciones Django en escritorio y 390×844, productos Streamlit en 1440×900 y 390×844, y detalle administrativo Django. Las tarjetas de este escenario quedaron en decisión manual: no se afirma que se haya observado un pronóstico apto en navegador.
- La fecha objetivo distinta de entrega se prueba con una tarjeta simulada en `test_seller_sees_commercial_forecast_separate_from_delivery_quantity`; COR-03 sigue pendiente de definición comercial y no demuestra que el motor soporte la selección independiente en producción.
- La suite `manage.py test operations tests --noinput` terminó con 147 pruebas correctas. `manage.py check` y `makemigrations --check --dry-run` no informaron problemas ni migraciones nuevas.
- El DOCX del documento maestro abrió y su contenedor ZIP pasó la comprobación de integridad. El PDF correspondiente produjo `Broken xref table` en lectura estricta; un extractor logró recuperar texto, pero el PDF requiere revisión o regeneración sin sobrescribir el original.
- Se clonó y arrancó `SmartOrder-Codex-Prueba-20261101` desde `SmartOrder-PoC-20261017`, que permaneció apagada. Ubuntu 24.04.5 presentó inicio de sesión y Guest Additions informó cero usuarios conectados. Los servicios existentes respondieron HTTP 302 (Django) y 200 (Streamlit), pero corresponden a la imagen previa, no a esta revisión. Ver `vm-copia-inicio-sesion.png`.
- No se ejecutaron Docker, PostgreSQL de despliegue, TLS, reinicio ni restauración en una VM con esta revisión; el acceso administrativo aún está pendiente. Tampoco se realizó UAT de Vitali.
- El aviso de SQLite temporal de Streamlit se mostró también en este arranque local; requiere revisar su contexto. La prioridad de interfaz de esta iteración es Django.
