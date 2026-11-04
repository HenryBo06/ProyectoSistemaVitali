# Guion de exposición (8–10 minutos)

## 0:00–0:45 | Apertura

“Presento SmartOrder AI, una herramienta para apoyar al área comercial de una empresa avícola con pedidos sugeridos por cliente y producto. El propósito es ayudar a revisar cantidades con base en el historial disponible. La sugerencia no confirma la venta y no aprueba producción.”

## 0:45–1:45 | Problema y objetivo

“Cada cliente puede tener una frecuencia y un volumen distintos. Una revisión manual del historial puede ser lenta. El proyecto busca resumir antecedentes y ofrecer una cantidad para que el vendedor la evalúe. No contamos aquí con cifras verificadas para afirmar pérdidas, ahorros ni mejora de ventas.”

## 1:45–2:45 | Herramientas

“XGBoost es el modelo de aprendizaje automático que existe en el motor para regresión. Python, pandas y NumPy preparan los datos. Django aplica usuarios, roles, procesos y páginas. SQLite conserva datos del laboratorio local. ChatGPT y Codex apoyaron el análisis y la elaboración de esta entrega, pero no generan la cantidad mostrada.”

“Hay métodos de comparación estadística como promedios y mediana. La aplicación elige una cantidad automática solo cuando los controles de evidencia lo permiten.”

## 2:45–4:00 | Entradas y arquitectura

“El archivo Excel aporta fecha, cliente, producto, cantidad y unidad. El significado de las fechas debe confirmarse. Django llama al servicio de carga y al motor de recomendaciones; los datos locales se consultan por el ORM.”

“No usamos clima: el repositorio no contiene una integración de datos meteorológicos asociados al cliente.”

## 4:00–5:15 | Modelo

“XGBoost recibe características de calendario y cantidades recientes. El entrenamiento usa ventanas anteriores al corte para evitar mirar información futura. Se compara por series temporales con una referencia simple. Si los datos son antiguos, incompletos o incompatibles, se pide revisión manual.”

“Existe el candidato XGBoost en código, pero la captura de hoy usó la mediana de días equivalentes. No atribuyo su valor al modelo de árboles.”

## 5:15–7:15 | Demostración

“Inicio como vendedor. Solo veo el cliente asignado. Al entrar a productos y sugerencias, la tarjeta identifica que el producto y sus cantidades pertenecen a LAB simulado. La sugerencia de 10 kg viene de una serie artificial constante; sirve para comprobar el recorrido, no para validar resultados reales.”

“Ahora abro ‘Crear venta’. El formulario presenta cliente, fecha y producto. Puedo ajustar y revisar antes de confirmar. No confirmo esta operación en la exposición. El vendedor confirma ventas; administración autoriza por separado producción.”

## 7:15–8:30 | Verificación y límites

“La suite Django reportó 119 pruebas aprobadas en esta rama y el chequeo del proyecto no señaló problemas. Eso acredita pruebas automatizadas de comportamiento, no calidad predictiva con datos reales.”

“Antes de un piloto real necesitamos un histórico autorizado, fecha y unidad consistentes, cobertura, devoluciones y una evaluación temporal documentada. También conviene registrar la versión del modelo.”

## 8:30–9:00 | Cierre

“SmartOrder ya ofrece un flujo de sugerencias y un motor con alternativas estadísticas y XGBoost, protegido por condiciones de calidad de datos y revisión humana. El siguiente paso es validar el motor con ventas recientes autorizadas. La presentación separa lo implementado, lo probado con datos sintéticos y el trabajo futuro.”
