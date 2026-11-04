# Auditoría retrospectiva y benchmark público — SmartOrder AI

**Fecha:** 2026-11-03. **Rama:** `main`. **VM:** fuera de alcance. **Estado de datos:** demostración, no evidencia comercial verificada. Se mantiene el bloqueo de cantidades automáticas.

## 1. Histórico proporcionado (no agregado al repositorio)

Fuente: `Demo_Ventas_Avicola_2025_IA_Pedidos_v2.xlsx` original, hoja `Ventas2025`. Se inspeccionó localmente; no se publica el XLSX ni registros de clientes en GitHub.

- 1.294 filas entre 2025-01-01 y 2025-12-31; 10 clientes, 8 productos, 80 combinaciones cliente-producto.
- 365 fechas distintas globalmente; 1.270 combinaciones cliente-producto-fecha, de 29.200 potenciales: **4,35 %** observadas. No se imputan ventas cero cuando la cobertura es desconocida.
- No hay nulos, registros duplicados exactos ni discrepancias entre `cantidad × precio` y monto. Hay 24 filas adicionales en fechas compartidas por el mismo cliente-producto; no son necesariamente duplicados comerciales.
- Faltan la unidad de venta (kg, libra, unidad), referencia de pedido y línea, y certificación del significado de `Fecha`. Sus registros de 2025 no verifican pronósticos para noviembre de 2026.
- Precios extremos por producto: los intervalos observados abarcan aproximadamente USD 1,80–17,99 en varios artículos. Sin presentación comercial y tarifa vigente, no se comparan como precios unitarios equivalentes.

## 2. Producto recomendado vs. compra posterior (sin fuga temporal)

**Diseño:** cortes fijos el primer día de abril a diciembre de 2025, horizonte de 30 días; 10 clientes por corte, 90 escenarios activos. Los candidatos solo incluyen productos vistos antes del corte. La recomendación histórica prioriza más *días distintos con venta* del cliente-producto y USD solo para desempatar. Las referencias utilizan (a) popularidad histórica de todos los clientes, (b) últimos 30 días de ese cliente y (c) selección uniforme entre candidatos históricos, calculada como probabilidad esperada exacta. Métrica `Hit@1`: ¿el producto seleccionado aparece al menos una vez en las compras del horizonte? Condicionada a que el cliente compró; no mide demanda, oportunidad de contacto, volumen o margen.

| Método | Hit@1 en 30 días |
| --- | ---: |
| Prioridad histórica cliente-producto | **72,22 %** (65/90) |
| Popularidad general | 75,56 % (68/90) |
| Últimos 30 días por cliente | 75,56 % (68/90) |
| Selección aleatoria (probabilidad esperada) | 74,27 % |

**Sensibilidad a horizonte:** a 7 días, hubo 83 escenarios con alguna compra futura: la prioridad histórica acertó **34,94 %**, la popularidad general **38,55 %** y el aleatorio esperado **30,21 %**. A 14 días, con 90 escenarios, las cifras correspondientes fueron **51,11 %**, **54,44 %** y **46,47 %**.

El horizonte de 30 días tiene efecto techo: se compran varios de los 8 productos. No se observa superioridad consistente frente a referencias simples. Estas cifras NO son precisión del XGBoost ni del pronóstico operativo de siete días.

## 3. Diagnóstico de cantidades condicionado a ventas observadas

Separando cada serie cliente-producto por fecha y conservando 75 % inicial como entrenamiento, la **mediana de las últimas cuatro transacciones** logra MAE numérico de **220,68**, frente a **267,86** con la última cantidad, en 354 transacciones de prueba. Estas son cifras **sin unidad confirmada** y solo existen al producirse una transacción. No validan un objetivo de demanda diaria ni un pedido automático.

## 4. Referencia independiente: transacciones reales públicas

Se agregó `scripts/validar_publico_uci.py` y el workflow `SmartOrder - Backtest publico UCI` (ejecución manual o al modificar el script). Fuente UCI: [Online Retail](https://archive.ics.uci.edu/dataset/352/online-retail), Daqing Chen, 2015, DOI https://doi.org/10.24432/C5BW33, **CC BY 4.0**. Son compras UK de regalos de 2010–2011, **NO datos de pollo ni de Vitali**.

El benchmark descarga la fuente pública en el runner efímero, filtra cancelaciones, cantidades/precios no positivos y ventas sin cliente; evalúa cortes mensuales fijos, horizontes de 7 y 30 días, frecuencia cliente-producto, referencias sencillas y una comparación independiente de cantidad por factura. Produce un JSON agregado con SHA-256, exclusiones y métricas; no carga información externa a la base de datos, no convierte unidades ni activa recomendaciones comerciales automáticas.

Comando reproducible:

```bash
python -m pip install 'openpyxl>=3.1,<4'
python scripts/validar_publico_uci.py --download-uci --output results/uci_benchmark.json
```

**Interpretación obligatoria:** si el método de frecuencia gana en UCI, acredita únicamente comportamiento de una heurística genérica en otro sector. No es validación del motor XGBoost Vitali ni habilita ventas, pedidos o cantidades automáticas en producción.

Para contexto sectorial, consultar [USDA ERS, Meat Price Spreads](https://ers.usda.gov/data-products/meat-price-spreads) y [USDA AMS, Chicken Market News](https://www.ams.usda.gov/market-news/chicken-market-news-reports). Sus precios estadounidenses agregados no se usan como precios o cantidades de clientes salvadoreños.

## 5. Puertas para la siguiente fase

Conservar `verified=false` para cargas con presentaciones provisionales. Pedir a Vitali catálogo por SKU y unidad, significado de fechas, cobertura de días sin movimiento, referencias de pedido/línea, historial reciente y evidencia comercial de stock/tarifas. Solo entonces efectuar backtest completo del mismo motor desplegable, por fecha objetivo, cliente, producto y unidad, con comparaciones a referencias ingenuas y ventanas temporales sin fuga. La VM continúa fuera de alcance.
