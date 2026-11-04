# Priorización adaptativa auditada de productos — SmartOrder

**Fecha:** 2026-11-03. **Alcance:** solo orden de revisión comercial, no cantidad de pedido ni producción. Se implementó directamente en main, sin tocar VM.

## Problema y decisión

El historial avícola de demostración tiene 1.294 registros de 2025 y no contiene unidad certificada por SKU ni cobertura comprobada por día. El backtest temporal previo mostró que ordenar solo por días históricos de venta NO siempre supera métodos triviales. En cambio, el benchmark público UCI muestra que la frecuencia por cliente puede funcionar mejor que alternativas, pero depende del conjunto de datos. Por ello no se fija un ganador universal.

El motor `smartorder/product_priority.py` mantiene la **frecuencia histórica** como referencia y compara dos candidatos:
- **Últimos 30 días:** prioriza productos con más días de compra recientes; desempata por histórico.
- **Recurrencia ponderada:** días históricos con una semivida temporal de 28 días; desempata por frecuencia histórica.

Cada serie es de UN cliente. No se suman ni comparan kg/lb/unidad, facturación ni cantidades; se usan días distintos de compra de cada producto. El modelo de **cantidad** en `smartorder/recommendations.py` ya tiene otro backtest que puede elegir métodos estadísticos o XGBoost. No se debe equiparar esa selección a la clasificación de productos.

## Procedimiento anti-fuga y límites

Se generan hasta 52 cortes semanales desde al menos 56 días después del primer registro, usando únicamente compras anteriores a cada corte. Una ventana de evaluación mide si el primer producto priorizado se observó comprado en los 7 días siguientes. Se descartan ventanas sin compras observadas: **Hit@1 condicional a compra observada**, no demanda futura general.

Los cortes más antiguos (60 %) eligen el método candidato por mayor Hit@1; los posteriores (40 %) son auditoría **fuera de selección**. Solo se cambia del histórico cuando existen al menos 8 ventanas de selección y 6 de auditoría activas y la alternativa gana al histórico por al menos 2 aciertos y 5 puntos porcentuales en cada partición. Si no cumple, vuelve al orden histórico sin afirmar mejora.

Se conservan las prioridades estacionales previas del mismo período del año anterior. **Si existe un período estacional comparable, no se lo reemplaza por una alternativa que nunca fue contrastada contra esa estacionalidad.** El nuevo selector puede seguir proporcionando métricas diagnósticas, pero el orden sigue siendo la referencia de temporada. Este control es especialmente relevante con un solo año de datos.

Si no hay casos suficientes o el histórico supera el límite de 5.000 observaciones día-producto por cliente, el comportamiento también es histórico. El límite es deliberado para no consumir recursos desproporcionados en la interfaz gratuita; ampliar requerirá optimizar la evaluación por lotes.

## Integración

`operations/services.py::recommendation_cards` consume el selector exclusivamente para `mode="sale"`; conserva `historical_rank` original para compatibilidad y expone `priority_rank`, `priority_method_label`, `priority_audit_cases`, `priority_audit_hits` y `priority_baseline_hits` al catálogo Django y Streamlit. Ambos muestran la distinción entre orden auditado y referencia histórica.

Ninguna recomendación de cantidad cambia automáticamente; las cargas sin unidades, fechas/cobertura comercial confirmada o procedencia aprobada siguen en modo manual. El selector no utiliza ventas futuras después de `today`; no consulta datos de otros clientes para priorizar uno. Las ventas operativas posteriores al snapshot no se incorporan a este ranking hasta tener un historial de día-producto conciliado.

## Verificación

`tests/test_product_priority.py` prueba deriva persistente de preferencias, corte temporal sin fuga, deduplicación del mismo día, pocos datos y protección de estacionalidad no comparada. SmartOrder CI ejecuta estas pruebas con el resto del sistema en SQLite y PostgreSQL 17. Las cifras de precisión presentadas en otras auditorías NO son medidas del nuevo selector integrado hasta ejecutar un backtest del método completo sobre datos comerciales aprobados.

Fuentes de las dos pruebas diagnósticas precedentes: `docs/AUDITORIA_XLSX_Y_BENCHMARK_PUBLICO_20261103.md` y https://archive.ics.uci.edu/dataset/352/online-retail .
