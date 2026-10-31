# Auditoría de IA de SmartOrder AI

**Estado auditado:** `origin/main` en `35323af1fbb64495ce7d8e84ee83923d16fceb4a` (30-10-2026). **Alcance:** contraste de la solución con los dos puntos de la actividad docente: presentar herramientas de IA y mostrar código o pantallas con explicación.

## Hallazgo ejecutivo

SmartOrder ya integra un recomendador temporal en Python en `smartorder/recommendations.py`. Contiene métodos estadísticos y un candidato de aprendizaje automático con `XGBRegressor`. El sistema condiciona una recomendación automática a la evidencia disponible y ofrece decisión manual cuando la fuente, cobertura, unidad o desempeño comparativo no bastan. La pantalla y el flujo de venta están integrados en Django. El vendedor revisa y confirma la venta; esa acción no autoriza producción.

La demostración de esta entrega se ejecutó con un SQLite separado y datos inventados para laboratorio, rotulados como simulados. No se usó un historial de negocio Vitali. La tarjeta capturada muestra el método **“Mediana de días equivalentes”** con 10 kg en datos sintéticos constantes. Esto comprueba un flujo de recomendación de la aplicación, pero **no es evidencia de precisión comercial ni una salida del modelo XGBoost**.

## Requisitos de la actividad

| Requisito del docente | Evidencia preparada | Estado |
| --- | --- | --- |
| Listar herramientas de IA a utilizar | `PRESENTACION_IA_HERRAMIENTAS.md` y diapositiva 4 | Cumplido, distinguiendo modelo IA, asistentes y software de soporte |
| Código fuente o capturas y explicación | diapositivas 5–13, capturas reales y `EXPLICACION_MODELO_IA.md` | Cumplido para la demo local sintética |

## Componentes existentes y verificados

- **Importación de Excel:** `operations/services.py::import_sales` delega lectura y validación a `smartorder/data.py`; conserva semántica de fecha y procedencia. Un archivo de pedidos no se trata como registro de entregas sin confirmación.
- **Modelo de datos:** Django administra conjuntos de ventas, líneas, cobertura, pedidos, usuarios, asignación de clientes y operación.
- **Motor de sugerencias:** `RecommendationEngine` organiza series por cliente/producto/unidad; incluye últimos 7 días, promedios de 28 y 56 días, mismo período del año previo, mediana de días equivalentes y candidato XGBoost.
- **ML que existe:** regresión con árboles XGBoost para estimar volumen semanal. Usa mes, día de semana, plazo y agregados de 7, 28 y 56 días. Normaliza por escala de producto y trunca predicciones a cero.
- **Evaluación temporal:** selecciona métodos usando ventanas cronológicas y separa ventanas de auditoría. Compara con promedio de 28 días y aplica criterios de elegibilidad. Para venta de un día, el camino mostrado en la captura evalúa una mediana de días equivalentes contra la media de 28 días.
- **Regla de salida:** el motor pide fuente verificada y reciente, semántica temporal compatible, cobertura completa, unidad y ventaja frente a referencia simple. Sin evidencia suficiente, muestra decisión manual.
- **Frontend local:** Django usa plantillas, Bootstrap local y `operations/static/operations/css/local-frontend.css`. La plantilla de recomendaciones muestra cantidad, existencia, explicación y enlace para preparar una venta.
- **Roles:** acceso a vistas administrativas se restringe. La cartera del vendedor se filtra por asignación. Las pruebas del frontend/roles están en `operations/test_local_frontend.py` y otras suites de `operations/`.
- **Interfaz Streamlit:** `app.py` importa `streamlit_app.py`; ambas interfaces reutilizan funciones de Django. Esta entrega académica prioriza y captura Django local.

## Herramientas reales

- **XGBoost:** dependencia declarada y `XGBRegressor` utilizado dentro del motor. Es el modelo de aprendizaje automático del proyecto.
- **Python, pandas y NumPy:** implementación y arreglos/tablas de series temporales.
- **Django y SQLite:** aplicación local y persistencia de la demo.
- **HTML/CSS y Bootstrap empaquetado local:** interfaz del navegador.
- **ChatGPT/Codex:** asistencia usada en la revisión, preparación de esta documentación y presentación en esta tarea. No son parte del modelo que genera cantidades.
- **Git/GitHub:** repositorio, ramas y revisión de cambios.
- **scikit-learn:** figura en `requirements.txt`, pero no localicé uso directo en el código Python auditado. No se presenta como biblioteca usada por el recomendador.

## Lo que no está implementado o no se puede afirmar

- No encontré un flujo que una observaciones meteorológicas fiables con fecha y cliente/ubicación; **clima no es entrada actual**.
- No se observó un registro persistente de artefacto del modelo, versionado reproducible o fecha/ID de modelo en la sugerencia.
- No hay base para afirmar que XGBoost gana en un histórico real de Vitali. La tarjeta de demo se resolvió por método estadístico y datos sintéticos.
- No reportamos reducción de merma, agotados, costos, errores ni mejora de ventas.
- Los gráficos e indicadores administrativos no demuestran por sí solos validación productiva.
- La base de demo creada para capturas es SQLite local descartable; no se importaron datos comerciales ni se publicaron sus cuentas.

## Arquitectura real resumida

Navegador Django → vistas/control de rol en `operations/views.py` → `operations/services.py` → lectura de XLSX/series en `smartorder/data.py` y `smartorder/recommendations.py` → Django ORM sobre SQLite → pantalla revisable por vendedor. Las sugerencias se usan como referencia para preparar el flujo de venta ya existente. Confirmar venta y autorizar producción son decisiones diferentes.

## Verificación de esta entrega

- Rama creada desde `origin/main`: `codex/academic-ai-delivery`.
- Dependencias ya disponibles en entorno virtual aislado: Django 5.2.18, pandas 2.3.3, NumPy 2.5.4, XGBoost 3.4.1, scikit-learn 1.9.1, Streamlit 1.66.1.
- `manage.py check`: sin problemas.
- `manage.py test`: **119 pruebas, OK**.
- Demo manual en Django contra `.local-web-lab` independiente: acceso admin, navegación rol vendedor, cartera asignada, tarjeta de recomendación sintética y formulario de venta precargado. No se confirmó la venta.
- Instalador de datos sintéticos: `scripts/preparar_smartorder_lab.py`. La base y credenciales quedan ignoradas localmente.

## Recomendación de trabajo futuro

Primero solicitar y sanear un histórico autorizado con significado de fecha y unidad confirmados; documentar devoluciones/cancelaciones y cobertura. Después medir el XGBoost contra la referencia cronológica por cliente/producto y guardar versión/fecha del modelo. Solo con esa evidencia conviene agregar variables externas, incluida meteorología. Revisar entonces controles de permisos y persistencia del entorno de despliegue.
