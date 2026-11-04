# Laboratorio sintético avanzado y auditoría de SmartOrder AI

**Fase:** evaluación técnica y demostración reproducible sin fuentes empresariales.
**Alcance excluido:** información confidencial de Vitali, despliegue/VM, Odoo real,
fuentes SQL, credenciales de empresa, cantidades automáticas, producción.

## 1. Propósito

Complementa el laboratorio de sesiones y pedidos de
scripts/preparar_smartorder_lab.py. NO reemplaza su conjunto simple de
datos para pruebas visuales ni toca su SQLite. Permite estudiar cómo se
comporta la priorización comercial al encontrar historial normal, estacional,
intermitente, cambiante o adverso, y comparar referencias en cortes temporales
realmente posteriores al historial de entrada.

**No se entrenan afirmaciones sobre clientes reales.** El generador es
determinista y no consume ni una fila del Excel privado de 2025. Los nombres de
clientes, productos, referencias, precios y cantidades son inventados. El
histórico exportable se rotula de manera explícita como SIMULADO, no importa
automáticamente datos a ninguna base de datos y no confirma cobertura de días
sin ventas.

## 2. Escenarios y presentaciones

El generador permite 3–250 clientes, 3–12 productos y 180–1100 días. Cada
cliente recibe un arquetipo entre ocho escenarios, que se repiten a partir del
noveno cliente:

| Arquetipo | Comportamiento ensayado |
| --- | --- |
| semanal_estable | Ventas con recurrencia semanal y ruido |
| quincenal | Recurrencia aproximadamente quincenal |
| temporada_alta | Frecuencia reforzada en noviembre y diciembre |
| cambio_preferencia | Producto dominante cambia a mitad del historial |
| intermitente | Compras separadas por semanas, sin imponer ceros |
| promocion_puntual | Incremento artificial y temporal de frecuencia/cantidad |
| cierre_sin_cobertura | Intervalo sin registros; **NO** confirma cierre real ni demanda nula |
| tendencia_creciente | Cantidad por transacción ficticia creciente |

Las únicas unidades son **kg, lb y unidad**, asignadas de forma estable por
producto ficticio. Nunca se convierten unas en otras. Se crean identificadores
de línea/orden únicos; distintas compras del mismo producto en un mismo día
son legítimas. La fecha significa **pedido/venta simulada**, nunca entrega
efectiva. Los precios son totalmente ficticios y no siguen tarifas de ningún
mercado o empresa.

## 3. Cuarentena adversarial

A cada prueba se agregan, EXCLUSIVAMENTE en memoria y fuera del conjunto
limpio, ocho registros malformados: unidad desconocida, discrepancia de
unidad/ SKU, cantidad negativa, cantidad extrema, cliente vacío, fecha futura,
referencia de línea duplicada y precio no positivo. El validador los debe
rechazar sin perder filas válidas. Esta cuarentena verifica el **laboratorio**,
no sustituye los controles completos del ETL de Django.

El informe registra el detalle agregado de las ocho causas de rechazo.
No se importa ningún registro adversarial en el XLSX de exposición.

## 4. Evaluación temporal y métricas

En cada prueba, el **65 % inicial del calendario** queda antes del período
de evaluación externa. En el tramo posterior se usan cortes cada 28 días,
evaluando próximas ventanas de **7 y 30 días**. En cada corte el selector
adaptativo recibe solo compras anteriores; los registros posteriores quedan
retenidos para calcular resultados.

Se comparan cuatro referencias: recurrencia histórica del cliente, últimos 30
días, selector adaptativo con su validación interna y probabilidad esperada
de elegir aleatoriamente un producto histórico. Se reportan Hit@1, Hit@3 y
resultados **por tipo de escenario**. Los resultados se condicionan a que
exista al menos una compra observada en el horizonte; no se interpreta un
período sin compras como un cero de demanda. Los clientes con menos de tres
productos previos son excluidos y contabilizados. Los resultados no garantizan
que el método adaptativo siempre gane: una pérdida frente al histórico es
un hallazgo que debe permanecer visible.

El selector puede incorporar nuevas observaciones a medida que avanzan los
cortes (walk-forward), pero nunca utiliza la venta del período objetivo.
Se prueba además la invariancia ante la adición de ventas futuras.

La comparación de tamaño de transacción enfrenta la mediana de las últimas
cuatro compras con el valor de la última compra, bajo orden cronológico,
**por unidad de presentación**. Se reportan MAE y denominadores de kg, lb
y unidad separadamente; no es pronóstico de demanda diaria, de reposición
ni de producción física.

El informe también separa **ocupación de celdas observadas** de
**cobertura empresarial certificada** (siempre desconocida en la simulación).

## 5. Ejecución reproducible

Con un Python 3.12 y dependencias del repositorio:

    python scripts/auditar_smartorder_sintetico.py --seed 20261103 --clients 16 --products 8 --days 630 --export-xlsx --export-csv

Para volumen mayor:

    python scripts/auditar_smartorder_sintetico.py --seed 2112026 --clients 72 --days 560 --evaluation-client-limit 16

Los archivos se generan en .local-synthetic-audit/ (ignorado por Git).
El primer comando produce un JSON de métricas, CSV y XLSX de prueba. El
segundo produce métricas de rendimiento y escalabilidad con 72 clientes,
evaluando únicamente 16 clientes ordenados alfabéticamente, sin fingir
cobertura de los demás.

El XLSX se puede inspeccionar y usar como **fuente simulada** dentro del ETL
de SmartOrder si se confirma el origen simulado y sus presentaciones.
No debe importarse como fuente empresarial real. El exportador rechaza
archivos que excedan la grilla máxima de 250 000 celdas del importador
canónico vigente: las simulaciones grandes son pruebas de carga separadas.

Para comprobar individualmente:

    python -m unittest discover -s tests -p test_synthetic_lab.py -v
    python manage.py test operations tests --noinput

El workflow SmartOrder - Laboratorio sintético avanzado ejecuta las
invariantes, carga XLSX con el lector real, verifica que las cantidades
sigan bloqueadas si la fuente no está verificada y publica solo artefactos
ficticios (retención 14 días). GitHub Actions incluye además las suites
habituales de SQLite, PostgreSQL 17 y navegador en commits de main.

## 6. Criterios de aceptación de esta fase

La fase es técnicamente válida si se cumplen TODOS los siguientes puntos:

- Misma semilla/parámetros = idénticas filas y SHA-256, sin acceso a red ni fuentes externas.
- Ninguna fila de prueba tiene información empresarial; las unidades no se mezclan.
- Ocho adversarios rechazados sin cambiar el histórico limpio.
- Inyección de ventas posteriores no cambia una decisión ya tomada.
- Históricos insuficientes o no verificados no generan cantidades automáticas.
- Reportes incluyen cortes, denominadores y comparación contra métodos triviales.
- La salida XLSX del caso normal pasa por lector canónico.
- Las interfaces y las suites actuales de la aplicación continúan pasando.
- No se despliega, crea usuario real, modifica Odoo ni activa predicciones reales.

No se declara la aceptación comercial ni una precisión de XGBoost para
Vitali: únicamente **funcionalidad técnica en datos sintéticos**.
Los tiempos de ejecución se reportan como observaciones sobre runners
efímeros, no como requisitos estrictos ni equivalencia con una VM.

## 7. Límite y siguiente fase independiente

El cierre de esta fase habilita abordar otra solución sin depender de
credenciales, datos personales ni conexiones a sistemas de Vitali. La
activación empresarial real continúa siendo una fase distinta, sujeta a
autorización comercial explícita y datos auditables, sin subir información
confidencial a GitHub ni utilizarla en pruebas públicas.
