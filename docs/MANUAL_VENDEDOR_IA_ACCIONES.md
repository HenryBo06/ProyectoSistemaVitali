# Manual de vendedor — SmartOrder AI: sugerencias de venta y siguiente acción

**Versión:** piloto de demostración (noviembre de 2026).
**Destinatarios:** vendedores con cartera asignada.
**Fuera de alcance:** creación de usuarios, configuración ETL, análisis interno
administrativo, aceptación de producción y datos confidenciales de Vitali.

## Qué hará por ti

SmartOrder te ayuda a **elegir qué producto revisar primero para un cliente**
y propone una **siguiente acción comercial**. En la vista de ventas aparecen:

1. **Producto para revisar primero.** Sale de las compras observadas de TU
   cliente y de un orden de recurrencia histórica, con selección adaptativa
   únicamente cuando supera la evaluación cronológica correspondiente.
2. **Acción sugerida.** Por ejemplo, contactar al cliente para comprobar
   interés, preguntar qué cantidad necesita o confirmar la presentación si
   existe incompatibilidad entre fuentes.
3. **Por qué se recomienda.** La justificación distingue entre recurrencia
   histórica y cambios de prioridad que sí fueron auditados. NO es una
   probabilidad garantizada de compra.
4. **Qué comprobar antes de ofrecer o confirmar.** Disponibilidad, tarifa,
   presentación en kg/lb/unidad, cantidad expresada por el cliente y fecha de
   entrega; lo que no esté validado sigue siendo una decisión manual.

El sistema **no contacta clientes**, no promete existencias, no emite una
cotización oficial ni confirma ventas por sí mismo. No hay una IA conversacional
que redacte mensajes o envíe comunicaciones automáticamente. El cálculo del
producto y las reglas de acción son métodos estadísticos y reglas de negocio
explicables; no se deben presentar como una predicción infalible.

## Paso a paso: consultar una oportunidad

1. Entra con tu cuenta personal de **vendedor**. Solo debes ver tus clientes
   asignados. No compartas usuario ni contraseña.
2. Abre **IA · Sugerencias de venta** (Django) o **IA comercial: qué ofrecer
   y qué hacer** (Streamlit). Selecciona el cliente y la **fecha requerida de
   entrega**. La fecha es lo que solicitaste al sistema evaluar, no una entrega
   confirmada.
3. Selecciona **Venta**. La reposición del cliente es un recorrido distinto:
   solo se calcula si existen conteos, objetivos y movimientos compatibles.
4. Lee el bloque **Sugerencia de venta para este cliente**. Anota el nombre
   del producto destacado y la acción propuesta. Lee los requisitos de
   **antes de confirmar**, especialmente cuando aparezcan alertas.
5. Abre **Ver explicación** o **Ver explicación por producto** si necesitas
   saber si la prioridad proviene de recurrencia histórica o de un método
   alternativo validado. Puedes revisar otros productos y su acción sugerida.
6. Contacta al cliente por el canal habitual **fuera de SmartOrder**, sin
   afirmar que la IA conoce lo que va a comprar. Comprueba interés, cantidad,
   unidad, precio autorizado y fecha. La llamada o visita la realiza la persona.
7. Si hay interés confirmado, selecciona **Preparar venta** o **Preparar venta
   de este producto**. Verifica cuidadosamente cliente, producto, cantidad,
   presentación, tarifa autorizada, descuento y condiciones antes de enviar.
8. Al confirmar, la venta pasa a la gestión operativa correspondiente.
   **Confirmar una venta no autoriza fabricar ni garantiza despacho.** La
   revisión y autorización operativas siguen el flujo establecido.

## Cómo interpretar los mensajes

| Mensaje o situación | Significado para el vendedor | Acción correcta |
| --- | --- | --- |
| Prioridad histórica | Este producto aparece con más recurrencia observada; no implica una compra futura segura | Consultar al cliente si necesita el producto |
| Prioridad evaluada/auditada | Una alternativa mejoró frente al método histórico en ventanas pasadas del mismo cliente | Ofrecer como una oportunidad, no como pedido confirmado |
| Sin estimación / cantidad manual | La evidencia de unidades, fecha o cobertura no permite dar cantidad automática | Preguntar cantidad al cliente y registrar motivo cuando corresponda |
| Presentaciones incompatibles | No se deben convertir automáticamente kg, lb y unidad | Confirmar con administración y el cliente antes de cotizar |
| Disponibilidad por confirmar | La aplicación no certifica existencias recientes | Consultar inventario; no prometer despacho |
| Tarifa pendiente | No hay precio autorizado para esa combinación comercial | Pedir revisión administrativa antes de cotizar |
| Fecha fuera del horizonte evaluado | La inferencia de siete días no justifica proyectar una cantidad de entrega más distante | Cantidad manual, fecha verificada con el cliente |
| Reposición del cliente sin conteo | Faltan existencias actuales o el objetivo | Registrar conteo y solicitar confirmación del objetivo; no inventar déficit |

## Prueba guiada de diez minutos con datos simulados

Este ejercicio **solo utiliza el laboratorio LAB**, no datos reales:

1. Inicia sesión como vendedor del laboratorio.
2. Elige uno de sus clientes ficticios y una fecha de entrega válida.
3. Abre Venta y lee en voz alta **producto recomendado, razón y siguiente
   acción**; observa que las cantidades aparecen bloqueadas o manuales cuando
   la fuente no está validada.
4. Simula que el cliente responde: «Sí me interesa, pero confirma el precio
   y el inventario». Explica que SmartOrder pide verificar esas condiciones.
5. Abre Preparar venta. Escribe cantidades ficticias acordadas; **no pulses
   confirmar si solo estás presentando la sugerencia**.
6. Repite con otro producto y compara la explicación. El sistema puede
   recomendar revisar un producto diferente si cambian los datos o la fecha;
   no prometas ese cambio sin evidencia.
7. Cierra explicando que la administración gestiona datos, usuarios, pedidos
   y controles, **no recibe el asistente de acciones comerciales del vendedor**.

**Criterio de éxito del manual:** un vendedor nuevo puede responder sin ayuda:
«¿Qué producto debería ofrecer primero?», «¿Por qué lo muestra?» y
«¿Qué debo confirmar antes de cerrar la venta?».

## Si no aparece ninguna sugerencia

- **No ves clientes:** solicita al administrador asignar tu cartera.
- **No hay productos:** puede faltar el catálogo vigente o compras
  registradas del cliente; pide revisión de fuente.
- **No aparece una cantidad:** no es necesariamente un error. El motor evita
  un número automático cuando faltan garantías de datos.
- **La presentación no coincide:** no hagas una conversión manual sin
  autorización comercial. Usa la unidad acordada al preparar el pedido.
- **No conoces el precio o la disponibilidad:** consulta a administración
  antes de comprometer una entrega.
- **Ves datos que parecen reales en un piloto:** detén la demostración y
  consulta al responsable; las pruebas públicas usan únicamente datos LAB.

## Responsabilidad y límites

En el entorno de demostración, el historial es ficticio o una fuente demo sin
validación empresarial. Los porcentajes del laboratorio son verificaciones
**técnicas**, no resultados comerciales de Vitali ni promesas de incremento
de ventas. La decisión comercial final es del vendedor y del cliente;
administración conserva el control sobre precios, fuentes, usuarios y
autorizaciones operativas.

La versión piloto en VM debe verificarse con cuentas de prueba, navegadores
reales, HTTPS y respaldo restaurable antes de invitar a otros usuarios.
