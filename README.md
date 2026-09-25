# SmartOrder AI — pedidos sugeridos para Vitali Alimentos

Aplicación para analizar ventas importadas de Excel y preparar pedidos internos de productos. El vendedor ve sus clientes asignados, el histórico de cada producto, un pronóstico de siete días y una cantidad sugerida después de ingresar inventario y reglas comerciales. Administración revisa la solicitud y prepara un XLSX para producción.

La versión actual opera por **cliente y producto**. El Excel recibido no contiene sucursal/sala ni código SKU. El archivo se llama `Demo_Ventas_Avicola_2025_IA_Pedidos_v2.xlsx`; sus cifras describen ese archivo, no resultados operativos comprobados de Vitali.

## Probar en este equipo, sin Streamlit Cloud

En Windows, con Python instalado, ejecute una vez:

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Después, haga doble clic en [iniciar_smartorder.cmd](iniciar_smartorder.cmd) o ejecute:

```powershell
$env:SMARTORDER_LOCAL_MODE = '1'
.\.venv\Scripts\python.exe -m streamlit run app.py --server.address 127.0.0.1 --server.port 8501
```

Abra [http://127.0.0.1:8501/](http://127.0.0.1:8501/) en **ese mismo equipo**. Mantenga abierta la ventana de comandos; `Ctrl+C` detiene el servidor. Esta ejecución no depende de Streamlit Cloud, pero sí usa la biblioteca Streamlit instalada localmente. El primer inicio permite crear un administrador sin contraseña predefinida.

El Excel de demostración se carga automáticamente si está en la misma carpeta. En una copia donde falte, administración debe subirlo desde **Datos y modelo**. Los archivos cargados y las cuentas se guardan en `.local/`, que no se publica en Git.

## Recorrido funcional

1. **Administración → Datos y modelo:** cargue/active el Excel y revise su cobertura. Las cargas reemplazan el histórico activo, para evitar sumar transacciones superpuestas sin identificador.
2. **Administración → Usuarios:** cree un vendedor y asígnele al menos un cliente que figure en el Excel activo. Si reemplaza el Excel, compruebe de nuevo las asignaciones.
3. **Vendedor → Mi cartera:** seleccione un cliente y vea sus productos, ventas en USD, cantidad en la medida original del archivo y número de registros. Si existe, la comparación corresponde al mismo mes del año anterior; si falta, aparece el histórico disponible con una advertencia.
4. **Vendedor → Recomendaciones:** seleccione fecha y producto; revise pronóstico, referencia histórica, método y explicación. Ingrese unidad, inventario disponible, pedidos pendientes, inventario objetivo, mínimo, múltiplo y fecha de observación. Indique la fecha requerida de entrega dentro de los siete días pronosticados. Ajuste y motive la cantidad si corresponde. **Enviar pedido a revisión** crea una solicitud interna.
5. **Administración → Producción:** revise la solicitud, apruebe o rechace; si cambia la cantidad, documente el motivo. Prepare y descargue el XLSX. Después de compartirlo por el canal operativo, registre la entrega del lote en la aplicación. La hoja incluye pedidos detallados y un resumen por producto, unidad y fecha requerida.
6. **Historial:** ambos roles ven el estado: pendiente, aprobado, rechazado, sustituido, fuente sustituida o exportado. El vendedor solo ve sus solicitudes; solo administración puede revisar y exportar.

La cantidad sugerida es `MAX(0, pronóstico + inventario objetivo − inventario disponible − pedidos pendientes)`, sujeta al mínimo y al múltiplo indicados por el vendedor. El Excel no trae unidad oficial, SKU, inventario, plazos ni reglas de empaque: no se inventan. Administración debe verificar la unidad antes de aprobar.

## Paneles e indicadores

- **Administración:** filtros de fecha y cliente; ingresos USD, registros, clientes y productos con ventas; evolución mensual; participación por canal de venta; barras de ingresos por producto; pedidos pendientes, aprobados y exportados.
- **Vendedor:** cartera asignada, productos del cliente y ventas comparables; tabla y barras por producto; pronóstico, referencias y pedido sugerido. Las comparaciones entre productos usan USD, porque `Cantidad_kg_unid` mezcla medidas.
- Las métricas técnicas del modelo quedan en **Datos y modelo → Diagnóstico del pronóstico**, por producto, fuera del resumen comercial. WAPE/MAE describen error retrospectivo, no ventas, compras ni ahorro conseguido.

## Excel y pronóstico

La carga exige `Fecha`, `Cliente`, `Zona_Geografica`, `Canal_Distribucion`, `Canal_Venta`, `Producto`, `Categoria`, `Cantidad_kg_unid`, `Precio_Unitario_USD` y `Monto_Venta_USD`. Admite varias hojas con esos encabezados y varios años. Valida fecha, cantidades y montos; una fórmula sin valor guardado se recalcula como cantidad × precio y se informa. Se conservan registros repetidos del mismo día como transacciones y se agregan para construir series diarias. El límite evita expandir más de 250,000 combinaciones diarias cliente–producto.

El archivo recibido contiene 1,294 registros de 2025, 10 clientes, 8 productos y 80 pares cliente–producto. Solo cubre un año. XGBoost se compara con el promedio de cuatro semanas en ocho ventanas de siete días, sin mezclar el error de productos con medidas diferentes. Para fechas de 2026 sin ventas recientes, el pedido usa el **promedio semanal observado del mismo mes de 2025** cuando existe, y XGBoost se muestra como comparación. El campo del año anterior no se puede aprender de un solo año de entrenamiento; tampoco está validada la precisión de un salto completo entre años. Toda recomendación con histórico antiguo requiere revisión comercial e inventario actual.

## Publicación y datos

Para desplegar en Streamlit Community Cloud seleccione el repositorio `HenryBo06/ProyectoSistemaVitali`, rama `main` y entrada `app.py`. En **Advanced settings → Secrets** configure un código privado de al menos 20 caracteres, por ejemplo `SMARTORDER_SETUP_CODE = "valor-largo-generado-por-usted"`, antes del primer acceso. No lo suba a Git. Quien cree el primer administrador deberá ingresar ese código. [Guía oficial de despliegue](https://docs.streamlit.io/deploy/streamlit-community-cloud/deploy-your-app) y [guía de secretos](https://docs.streamlit.io/develop/concepts/connections/secrets-management).

La aplicación guarda cuentas, Excel y pedidos en archivos locales. [Community Cloud no garantiza su persistencia](https://docs.streamlit.io/develop/concepts/connections/connecting-to-data), y una aplicación desplegada desde un repositorio público puede ser pública. Para uso compartido con datos comerciales faltan almacenamiento persistente externo y control de acceso al despliegue. El acceso con cuentas dentro de la app no sustituye esas medidas. El repositorio no contiene el Excel ni los PDF originales.

## Verificación y auditoría

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

La [auditoría del sistema](docs/AUDITORIA_SISTEMA.md) enumera las fuentes revisadas, hallazgos corregidos, límites del Excel y requisitos para pasar de la exportación manual a una operación de producción integrada.
