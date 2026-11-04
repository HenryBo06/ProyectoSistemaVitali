# Guía de demostración en Windows

## Preparación segura

La demo que respalda las capturas utiliza `scripts/preparar_smartorder_lab.py` y crea su base bajo `.local-web-lab/`. El directorio está excluido de Git. No copies allí la base operativa ni uses contraseñas de cuentas reales. El script crea cuentas aleatorias y un Excel rotulado como simulado.

1. En la carpeta del repositorio, ejecuta `iniciar_smartorder.cmd` para el modo local normal o usa un entorno virtual aislado para presentar la app.
2. Para crear la base sintética desde cero, ejecuta `python scripts/preparar_smartorder_lab.py`. Solo utiliza una carpeta LAB vacía. Si el marcador y la fuente ya existen, el script preserva los datos.
3. Para cargar el Excel artificial de entregas y mostrar una recomendación de respaldo, ejecuta `python scripts/preparar_demo_presentacion.py`. Solo trabaja en `.local-web-lab`; es idempotente y conserva la fuente si ya existe.
4. Las credenciales se guardan en `.local-web-lab/credentials.json`, ignorado por Git. Léelas localmente; no las incluyas en diapositivas ni las envíes por chat.
5. Para esta captura, se usó una cuenta local de captura y un dataset de entregas enteramente sintético. Esa cuenta no está en el código ni en el repositorio.
6. Inicia el servidor Django en `http://127.0.0.1:8090/` o el puerto que indique el iniciador.

## Recorrido de 5 minutos

1. **Acceso:** inicia sesión y explica que Django separa las vistas según el rol.
2. **Panel vendedor:** muestra cartera asignada y el paso “Elegir cliente y revisar productos”. Aclara que el vendedor no gestiona cartera ajena.
3. **Productos y sugerencias:** abre el cliente `LAB` que aparece en la demo. Lee que el conjunto es simulado y comprueba la cantidad y la explicación.
4. **Distingue el método:** en la captura el método es “Mediana de días equivalentes”, un respaldo estadístico; no digas que esa cifra la produjo XGBoost.
5. **Preparar venta:** pulsa “Crear venta” o “Preparar una venta”. Confirma cliente/fecha/producto y revisa la cantidad editable. Termina antes de confirmar para no escribir un registro.
6. **Responsabilidad:** explica que una venta confirmada es una solicitud registrada. Administración conserva revisión y autorización de producción.
7. **Cierre:** muestra el panel de administración para explicar indicadores y permisos, sin atribuir cifras sintéticas a resultados de empresa.

## Errores frecuentes

- Si aparece “Decisión manual”, la aplicación está protegiendo la decisión por falta de evidencia compatible. No se debe forzar una recomendación.
- Si no aparece un cliente, administración debe asignar una cuenta vendedora a un cliente sintético.
- Si la cuenta de demo no accede, consulta el archivo de credenciales privado del LAB o vuelve a preparar solo un LAB vacío; no restablezcas cuentas existentes.
- SQLite local y archivos de carga no son persistencia de producción.

## Qué se verificó en esta entrega

Se abrió el frontend Django de `origin/main` en navegador, se inició sesión con roles de laboratorio, se mostró una cartera sintética limitada, se consultó la recomendación y se abrió el formulario de venta. No se envió ni confirmó la venta, no se autorizó producción y no se usaron datos reales.
