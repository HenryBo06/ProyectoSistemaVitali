# SmartOrder AI — Sistema inteligente de pedidos sugeridos para Vitali Alimentos

SmartOrder AI ayuda a decidir cuánto producto pedir por **cliente y producto**. Un administrador carga registros de ventas desde Excel y revisa calidad, tendencias y precisión del pronóstico. Cada vendedor ve únicamente sus clientes asignados, los productos más comprados, una estimación para los próximos siete días y el cálculo del pedido con el inventario que él mismo registra. El vendedor conserva la aprobación final.

## Qué hace la aplicación

| Administrador | Vendedor |
| --- | --- |
| Activa un Excel de ventas; ve cobertura, ingresos, productos y evolución mensual. | Ve sus clientes y los productos más comprados en el mismo mes del año anterior, ordenados por monto vendido. |
| Compara XGBoost con un promedio de las cuatro semanas anteriores mediante WAPE y MAE. | Ve demanda estimada para siete días, ventas comparables y la explicación del método aplicado. |
| Crea cuentas, asigna clientes, revisa decisiones y exporta pedidos aprobados. | Registra inventario y reglas, revisa o cambia el pedido, explica el ajuste y exporta sus decisiones. |

El cálculo es `MAX(0, pronóstico + inventario objetivo − inventario disponible − pedidos pendientes)`, seguido del pedido mínimo y el múltiplo de empaque ingresados. No se genera una cantidad de pedido hasta que el vendedor complete esos datos y confirme la unidad del producto.

## Ejecutar en Windows

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m streamlit run app.py --server.address 127.0.0.1
```

En el primer inicio, cree la cuenta administradora; no hay contraseñas predeterminadas. La aplicación carga automáticamente `Demo_Ventas_Avicola_2025_IA_Pedidos_v2.xlsx` si está en esta carpeta. En una copia del repositorio público, el administrador debe cargarlo desde **Datos y modelo**. Las cargas y la base SQLite se guardan bajo `.local/`, excluido de Git. La aplicación está pensada para ejecución local; sus cuentas no sustituyen la autenticación corporativa para un despliegue en red.

Para comprobar la lógica:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

## Contrato del Excel

Se aceptan hojas con estas columnas: `Fecha`, `Cliente`, `Zona_Geografica`, `Canal_Distribucion`, `Canal_Venta`, `Producto`, `Categoria`, `Cantidad_kg_unid`, `Precio_Unitario_USD` y `Monto_Venta_USD`. Se pueden incluir varios años en la misma hoja o en varias hojas con el mismo encabezado. La fecha debe ser una fecha de Excel o texto `AAAA-MM-DD`; cantidad y precio deben ser numéricos y no negativos. El monto se verifica contra cantidad × precio; si la fórmula de Excel no tiene valor guardado, se calcula y se informa.

Cada carga activada **sustituye el histórico anterior**. Esto evita sumar dos veces transacciones superpuestas: el archivo no contiene identificadores de transacción para conciliarlas con seguridad. Los registros del mismo día, cliente y producto se suman para analizar demanda. Como se confirmó que el histórico entregado está completo, un día sin venta en un par cliente–producto se trata como cero.

El archivo recibido tiene 1,294 registros entre el 1 de enero y el 31 de diciembre de 2025, 10 clientes y 8 productos. `Cantidad_kg_unid` mezcla medidas; la interfaz nunca suma cantidades entre productos distintos. El vendedor debe especificar la unidad correcta antes de calcular un pedido. El archivo no tiene sucursal, inventario, pedidos pendientes, promociones, vida útil ni tiempo de entrega. Por eso la vista actual es por cliente–producto y los datos operativos se ingresan en pantalla.

## Cómo se obtiene la recomendación

1. Se construye una serie diaria por cliente y producto. XGBoost estima la venta acumulada de los próximos siete días con fecha, cliente, producto, ventas de los 7 y 28 días previos y, cuando existe, el mismo período del año anterior. Las variables solo contienen información anterior al período pronosticado.
2. Las últimas ocho ventanas no superpuestas del histórico se reservan para comparar XGBoost y el promedio de 28 días. Se muestran WAPE y MAE de ambos; una cifra menor representa menos error. Un resultado medido dentro de 2025 **no prueba** precisión entre años.
3. Si hay 28 días recientes y XGBoost obtuvo menor WAPE, se aplica XGBoost; de lo contrario, el promedio reciente. Si faltan ventas recientes pero existe el mismo mes del año anterior completo, se usa su promedio semanal observado y se muestra la estimación XGBoost aparte. Cada fila identifica el método aplicado y las ventas comparables.
4. El vendedor introduce inventario disponible, pedidos pendientes, inventario objetivo, mínimo y múltiplo de empaque. La aplicación muestra la fórmula y registra cualquier ajuste aprobado con usuario, fecha, fuente y valores utilizados.

El histórico incluido termina en 2025. Una recomendación calculada en 2026 mostrará que faltan ventas recientes y usará una referencia histórica si existe; el vendedor debe reconocer esa advertencia antes de aprobar. El semáforo indica si los datos están listos para revisión, no una probabilidad calibrada. La aplicación no atribuye a XGBoost una mejora que la evaluación no demuestre ni presenta estimaciones como ventas reales, reducción de mermas o resultados de Vitali.

## Preguntas que el sistema deja respondidas

- **¿Por qué no aparece una sucursal?** El Excel actual solo identifica cliente y producto. Para recomendar por sala se necesitan ventas e inventario con un identificador real de sala.
- **¿De dónde salió la cantidad sugerida?** La pantalla muestra el método de pronóstico, el período comparable, cada valor de la fórmula y el redondeo comercial.
- **¿Qué pasa si XGBoost no mejora?** Se muestran ambas métricas. El sistema aplica la referencia disponible indicada en cada recomendación.
- **¿Quién tomó la decisión?** Se guardan usuario, fecha, cantidad sugerida, cantidad aprobada y motivo del ajuste. El archivo exportado contiene la última decisión de cada vendedor para cada cliente, producto y fecha.
- **¿Se conecta con ERP o planta?** Todavía no. El Excel es la fuente de ventas y la exportación Excel es la salida de pedidos aprobados.

## Estructura

`smartorder/data.py` valida la fuente; `smartorder/forecast.py` entrena y evalúa; `smartorder/orders.py` calcula y exporta pedidos; `smartorder/storage.py` gestiona cuentas, asignaciones y trazabilidad; `app.py` presenta las vistas según permisos. Los PDF, imágenes y Excel originales permanecen locales y no se publican en este repositorio.
