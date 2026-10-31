# Explicación del recomendador de SmartOrder AI

## Qué intenta estimar

SmartOrder calcula una cantidad de ventas observadas para una serie cliente-producto, usando la semántica de fecha confirmada. La cantidad orienta al vendedor. No crea una venta automáticamente ni autoriza fabricar.

## Entradas que el código usa

- fecha y cantidades de ventas históricas;
- cliente, producto y unidad;
- corte temporal disponible y plazo;
- para XGBoost: mes, día de semana y plazo, más ventas acumuladas en ventanas de 7, 28 y 56 días.

La cobertura del histórico, la procedencia, frescura y compatibilidad de fechas controlan si el sistema confía en una cantidad. La existencia puede mostrarse aparte desde el lote de inventario cargado. No encontré variables meteorológicas integradas.

## Algoritmos observables

`METHODS` enumera promedio reciente de 7 días, promedio de 28 y 56 días, mismo período del año anterior y XGBoost. Para una venta diaria, `_sale_forecast` toma la mediana de hasta ocho días equivalentes de semana; este fue el método visualizado en la captura de demo.

XGBoost usa `XGBRegressor` con objetivo cuadrático, `tree_method="hist"`, 80 estimadores, profundidad 3, aprendizaje 0.05, un hilo y semilla fija 73. Se entrena en orígenes históricos anteriores al corte. Para reducir el dominio de productos voluminosos, el código escala cada objetivo por una mediana de su producto; después vuelve a escala la predicción y limita el resultado a cero.

## Evaluación y controles

El backtest separa ventanas cronológicas para selección y auditoría. La configuración comienza con 16 ventanas, 8 para escoger método y 8 posteriores para auditar. El método elegido se contrasta con la media de 28 días. También se calculan MAE, WAPE y sesgo firmado en el reporte semanal. Para venta diaria, el objeto de evaluación de la aplicación reporta ventanas, cobertura, MAE y sobreestimación frente a referencia simple.

Una cantidad automatizada requiere, entre otros controles, fecha compatible, fuente verificada y reciente, serie/unidad confirmada, cobertura necesaria y comparación aprobada. Si no pasa esas condiciones, la interfaz informa revisión manual. El código no debe describirse como “siempre usa XGBoost”.

## Evidencia de demostración

La base separada `.local-web-lab` contiene datos inventados y rotulados. La captura de `03-recomendacion-sintetica.jpg` muestra una cantidad de 10 kg recomendada por **“Mediana de días equivalentes”** sobre un patrón sintético constante. La evaluación sintética de ocho ventanas registró MAE 0 frente a MAE 0 de la referencia. Ese empate solo verifica que el flujo permitió la sugerencia en esta muestra artificial; no mide precisión comercial, generalización ni ahorro.

## Límites

- No se entregó un histórico autorizado de Vitali para entrenar ni validar.
- El patrón sintético se diseñó para probar el recorrido y no simula adecuadamente la variabilidad comercial real.
- No hay integración meteorológica, identificación de una versión persistida del modelo ni reporte de una métrica real en pantalla/captura de esta demo.
- Las predicciones de ventas no equivalen necesariamente a demanda no atendida, consumo del cliente ni producción requerida.
- El inventario cargado es un snapshot. La disponibilidad tiene que confirmarse por el flujo operativo.

## Trazabilidad de código

- `smartorder/recommendations.py`: constantes de métodos, datos y motor, `_sale_forecast`, `for_sale`, `_features`, `_xgboost`, `_candidates`, `backtest` y `_weekly_forecast`.
- `smartorder/data.py`: carga/normalización del XLSX y series con cobertura.
- `operations/services.py`: importación y función `recommendation_cards`.
- `operations/views.py`: vista Django `recommendations`.
- `operations/templates/operations/recommendations.html`: producto, cantidad, explicación y acción del vendedor.
- `tests/test_recommendations.py`: pruebas de fechas, datos frescos, cobertura, línea base y limitación a ventas/entregas.
