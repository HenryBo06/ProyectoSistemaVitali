# Herramientas de IA y desarrollo

## Herramientas de IA

### XGBoost

El repositorio implementa `xgboost.XGBRegressor` en `smartorder/recommendations.py`. Este es el modelo de aprendizaje automático que puede participar en el pronóstico semanal. Recibe características de calendario, plazo y ventas agregadas de 7, 28 y 56 días. El código normaliza cantidades por producto y limita predicciones bajo cero. El método solo se agrega a la comparación cuando dispone de historial suficiente. La presencia del modelo en código no demuestra que haya sido el método elegido en cada pantalla.

### ChatGPT y OpenAI Codex

En esta tarea se usaron como asistentes para inspeccionar el repositorio, explicar el sistema y preparar el material académico. Son herramientas de apoyo al desarrollo. No forman parte de la predicción que corre en SmartOrder ni reciben los registros de la demostración local.

## Software de soporte

| Herramienta | Función en el proyecto | Papel |
| --- | --- | --- |
| Python | Lenguaje del backend y motor de recomendaciones | Implementación |
| pandas | Agrupar y preparar series temporales/tablas | Biblioteca de datos |
| NumPy | Características numéricas, arreglos y operaciones vectorizadas | Biblioteca numérica |
| XGBoost | Regresión de árboles para demanda/ventas agregadas | Modelo ML |
| Django | Login, roles, vistas, formularios, ORM y servicios | Framework web |
| SQLite | Almacenamiento local para la aplicación/lab | Base de datos |
| HTML/CSS y Bootstrap local | Vistas responsivas de Django | Interfaz |
| Git y GitHub | Historial, ramas y revisión del código | Control de versiones |

`scikit-learn` está declarado en dependencias, pero no encontré una llamada a sus estimadores o métricas en el código Python del motor. Por precisión, no lo presento como algoritmo utilizado.

## Qué no se debe confundir

- Un método de promedio o mediana es una referencia estadística, no un modelo de machine learning.
- XGBoost es ML, pero solo se utiliza cuando los controles de datos y comparación lo habilitan.
- ChatGPT/Codex asistieron a la preparación de código/documentación en esta actividad; no predicen cantidades de compra.
- Django, pandas, NumPy, SQLite y Bootstrap hacen posible el sistema, pero no son por sí solos herramientas de IA.
- Las métricas de la captura salen de una demostración con datos sintéticos y no deben presentarse como resultados de Vitali.
