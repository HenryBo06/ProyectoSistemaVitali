# Matriz de trazabilidad SmartOrder — revisión inicial

Fuente: PLAN_MAESTRO_MEJORAS_SMARTORDER_VITALI_2026-10-30.md (versión 1.0); 36 historias y 54 casos. Estado conservador de la rama de integración. Las evidencias locales con datos ficticios no son UAT ni aceptación de Vitali. Los estados pendientes requieren cotejo de todos los criterios; bloqueado identifica dependencia externa o infraestructura no disponible.

SHA-256 del plan recibido: `c63b1c8f74eec7a9343bdb32c4206039b1ce96773d4c18d480204254e275a0f4`. El original permanece en la carpeta de descargas y no forma parte de esta rama.

## Historias

| ID | Historia | Estado | Observación |
|---|---|---|---|
| HU-001 | Conexión a una base de datos de ventas externa · P0 | Bloqueado | Requiere datos, infraestructura o decisión externa; alcance completo sin verificar. |
| HU-002 | Mapeo de campos · P0 | Bloqueado | Requiere datos, infraestructura o decisión externa; alcance completo sin verificar. |
| HU-003 | Importar Excel completo · P0 | Pendiente | Existe funcionalidad relacionada; falta comprobar todos los criterios y UAT. |
| HU-004 | Calidad y cobertura de datos · P0 | Pendiente | Existe funcionalidad relacionada; falta comprobar todos los criterios y UAT. |
| HU-005 | Sincronización sin duplicados · P0 | Bloqueado | Requiere datos, infraestructura o decisión externa; alcance completo sin verificar. |
| HU-006 | Panel de salud de fuentes · P1 | Pendiente | Existe funcionalidad relacionada; falta comprobar todos los criterios y UAT. |
| HU-007 | Gestión segura de conexiones · P0 | Bloqueado | Requiere datos, infraestructura o decisión externa; alcance completo sin verificar. |
| HU-008 | Recomendación comercial visible · P0 | Pendiente | Existe funcionalidad relacionada; falta comprobar todos los criterios y UAT. |
| HU-009 | Explicación comercial comprensible · P0 | Pendiente | Existe funcionalidad relacionada; falta comprobar todos los criterios y UAT. |
| HU-010 | Cartera privada · P0 | Pendiente | Existe funcionalidad relacionada; falta comprobar todos los criterios y UAT. |
| HU-011 | Prioridades de mi jornada · P1 | Pendiente | Existe funcionalidad relacionada; falta comprobar todos los criterios y UAT. |
| HU-012 | Aceptar sugerencia · P0 | Pendiente | Existe funcionalidad relacionada; falta comprobar todos los criterios y UAT. |
| HU-013 | Ajustar cantidad con motivo · P0 | Pendiente | Existe funcionalidad relacionada; falta comprobar todos los criterios y UAT. |
| HU-014 | Pedido sin duplicidad · P0 | Pendiente | Existe funcionalidad relacionada; falta comprobar todos los criterios y UAT. |
| HU-015 | Precio y descuento correctos · P0 | Pendiente | Existe funcionalidad relacionada; falta comprobar todos los criterios y UAT. |
| HU-016 | Estado de pedido claro · P1 | Pendiente | Existe funcionalidad relacionada; falta comprobar todos los criterios y UAT. |
| HU-017 | Reposición diferenciada · P1 | Pendiente | Existe funcionalidad relacionada; falta comprobar todos los criterios y UAT. |
| HU-018 | Autorización de fabricación por línea · P0 | Pendiente | Existe funcionalidad relacionada; falta comprobar todos los criterios y UAT. |
| HU-019 | Vigencia de datos antes de aprobar · P0 | Pendiente | Existe funcionalidad relacionada; falta comprobar todos los criterios y UAT. |
| HU-020 | Gestión de vendedores y carteras · P1 | Pendiente | Existe funcionalidad relacionada; falta comprobar todos los criterios y UAT. |
| HU-021 | Panel de administración útil · P1 | Pendiente | Existe funcionalidad relacionada; falta comprobar todos los criterios y UAT. |
| HU-022 | Auditoría de cambios · P0 | Pendiente | Existe funcionalidad relacionada; falta comprobar todos los criterios y UAT. |
| HU-023 | Comparar calidad de pronósticos · P0 | Bloqueado | Requiere datos, infraestructura o decisión externa; alcance completo sin verificar. |
| HU-024 | Historial de recomendaciones · P1 | Pendiente | Existe funcionalidad relacionada; falta comprobar todos los criterios y UAT. |
| HU-025 | Importar la VM en servidor limpio · P0 | Bloqueado | Requiere datos, infraestructura o decisión externa; alcance completo sin verificar. |
| HU-026 | Acceder por LAN/VPN HTTPS · P0 | Bloqueado | Requiere datos, infraestructura o decisión externa; alcance completo sin verificar. |
| HU-027 | Persistencia tras reinicio · P0 | Bloqueado | Requiere datos, infraestructura o decisión externa; alcance completo sin verificar. |
| HU-028 | Respaldo y restauración · P0 | Bloqueado | Requiere datos, infraestructura o decisión externa; alcance completo sin verificar. |
| HU-029 | Monitoreo del servicio · P1 | Bloqueado | Requiere datos, infraestructura o decisión externa; alcance completo sin verificar. |
| HU-030 | Actualizar sin perder datos · P0 | Bloqueado | Requiere datos, infraestructura o decisión externa; alcance completo sin verificar. |
| HU-031 | Operación sin internet (si Vitali lo exige) · P1 condicional | Bloqueado | Requiere datos, infraestructura o decisión externa; alcance completo sin verificar. |
| HU-032 | Autenticación segura · P0 | Pendiente | Existe funcionalidad relacionada; falta comprobar todos los criterios y UAT. |
| HU-033 | Accesibilidad de tareas críticas · P1 | Pendiente | Existe funcionalidad relacionada; falta comprobar todos los criterios y UAT. |
| HU-034 | Rendimiento y tiempo de espera · P1 | Bloqueado | Requiere datos, infraestructura o decisión externa; alcance completo sin verificar. |
| HU-035 | Onboarding guiado · P1 | Bloqueado | Requiere datos, infraestructura o decisión externa; alcance completo sin verificar. |
| HU-036 | Diferenciar demo y producción · P0 | Pendiente | Existe funcionalidad relacionada; falta comprobar todos los criterios y UAT. |

## Casos

| ID | Historia | Caso | Resultado esperado | Estado | Evidencia o límite |
|---|---|---|---|---|---|
| CP-001 | HU-001 | Conectar PostgreSQL de prueba read-only | Conexión exitosa sin mostrar secreto | Bloqueado | Requiere infraestructura, fuente, entorno o decisión externa. |
| CP-002 | HU-001 | Contraseña incorrecta | Error controlado y sin cambios | Bloqueado | Requiere infraestructura, fuente, entorno o decisión externa. |
| CP-003 | HU-001 | Host/puerto no permitido | Rechazo y registro sin fuga de datos | Bloqueado | Requiere infraestructura, fuente, entorno o decisión externa. |
| CP-004 | HU-002 | Mapear columnas completas | Vista previa normalizada | Bloqueado | Requiere infraestructura, fuente, entorno o decisión externa. |
| CP-005 | HU-002 | Falta unidad o fecha ambigua | Activación bloqueada | Bloqueado | Requiere infraestructura, fuente, entorno o decisión externa. |
| CP-006 | HU-003 | Importar histórico XLSX válido | Nueva versión, totales conciliados | Pendiente | Pendiente de ejecución y cotejo específico contra el caso completo. |
| CP-007 | HU-003 | Reimportar archivo o exceder límite | Sin duplicado ni corrupción | Pendiente | Pendiente de ejecución y cotejo específico contra el caso completo. |
| CP-008 | HU-004 | Hueco histórico no verificado | Cobertura desconocida, manual | Pendiente | Pendiente de ejecución y cotejo específico contra el caso completo. |
| CP-009 | HU-004 | Unidad incompatible | Sin conversión automática | Pendiente | Pendiente de ejecución y cotejo específico contra el caso completo. |
| CP-010 | HU-005 | Sincronizar lote dos veces | Mismo total lógico | Bloqueado | Requiere infraestructura, fuente, entorno o decisión externa. |
| CP-011 | HU-005 | Actualizar/cancelar registro | Conciliación sin venta duplicada | Bloqueado | Requiere infraestructura, fuente, entorno o decisión externa. |
| CP-012 | HU-006 | Simular fallo de BD externa | Estado de error, trazabilidad | Bloqueado | Requiere infraestructura, fuente, entorno o decisión externa. |
| CP-013 | HU-006 | Usar última versión válida al fallar | Sin afirmar frescura falsa | Bloqueado | Requiere infraestructura, fuente, entorno o decisión externa. |
| CP-014 | HU-007 | Vendedor consulta conector | Acceso denegado en backend | Bloqueado | Requiere infraestructura, fuente, entorno o decisión externa. |
| CP-015 | HU-007 | Rotar secreto | Nueva credencial efectiva, logs limpios | Bloqueado | Requiere infraestructura, fuente, entorno o decisión externa. |
| CP-016 | HU-008 | Ventas aptas sin dato de entrega | Pronóstico comercial visible | Pendiente | Presentación probada con tarjeta simulada; falta ejecutar caso completo con serie apta. |
| CP-017 | HU-008 | Stock no validado | No promete entrega ni fabricación | Pendiente | El motor bloquea entregas para fechas de venta; falta escenario de stock no validado completo. |
| CP-018 | HU-009 | Revisar motivo de predicción y bloqueo | Explicación comprensible y veraz | Pendiente | Pendiente de ejecución y cotejo específico contra el caso completo. |
| CP-019 | HU-010 | Acceder a cliente no asignado | 403 / rechazo sin fuga | Probado | test_seller_cannot_open_unassigned_client_dashboard_or_recommendations |
| CP-020 | HU-010 | Ver cliente asignado | Información autorizada | Pendiente | Pendiente de ejecución y cotejo específico contra el caso completo. |
| CP-021 | HU-011 | Filtrar y ordenar oportunidades | Selección correcta y estado vacío útil | Pendiente | Pendiente de ejecución y cotejo específico contra el caso completo. |
| CP-022 | HU-012 | Usar sugerencia válida | Copia cantidad en borrador correcto | Pendiente | Pendiente de ejecución y cotejo específico contra el caso completo. |
| CP-023 | HU-012 | Cambiar fuente antes de confirmar | Revalidación/alerta | Pendiente | Pendiente de ejecución y cotejo específico contra el caso completo. |
| CP-024 | HU-013 | Cambiar cantidad con motivo | Auditoría valor antes/después | Pendiente | Pendiente de ejecución y cotejo específico contra el caso completo. |
| CP-025 | HU-013 | Cantidad inválida o motivo vacío | Error visible, formulario conservado | Pendiente | Pendiente de ejecución y cotejo específico contra el caso completo. |
| CP-026 | HU-014 | Doble clic al confirmar | Pedido único | Pendiente | Reenvío secuencial idempotente probado; falta doble clic/concurrencia en navegador. |
| CP-027 | HU-014 | Reenviar token con contenido distinto | Rechazo sin sobreescritura | Probado | test_retries_reuse_order_but_changed_content_is_rejected; alcance local |
| CP-028 | HU-015 | Tarifa, unidad y descuento de excepción | Total correcto y aprobación adecuada | Pendiente | Pendiente de ejecución y cotejo específico contra el caso completo. |
| CP-029 | HU-016 | Venta confirmada aún no fabricada | Estado de venta, no producción | Pendiente | Pendiente de ejecución y cotejo específico contra el caso completo. |
| CP-030 | HU-016 | ERP desconectado | Estado anterior fechado / pendiente | Pendiente | Pendiente de ejecución y cotejo específico contra el caso completo. |
| CP-031 | HU-017 | Reposición con conteo atrasado | Modo revisión manual | Pendiente | Pendiente de ejecución y cotejo específico contra el caso completo. |
| CP-032 | HU-018 | Admin autoriza una línea | Autorización independiente y auditada | Pendiente | Autorización posterior observada en Django; falta cotejar evidencia completa de auditoría. |
| CP-033 | HU-018 | Vendedor intenta autorizar | Acceso denegado | Probado | test_seller_cannot_upload_manage_users_review_or_export |
| CP-034 | HU-019 | Actualizar inventario entre pedido y revisión | Revalidación exigida | Pendiente | Pendiente de ejecución y cotejo específico contra el caso completo. |
| CP-035 | HU-020 | Asignar y revocar cliente | Permisos efectivos en lista y URL | Pendiente | Pendiente de ejecución y cotejo específico contra el caso completo. |
| CP-036 | HU-021 | Abrir dashboard con datos faltantes | KPIs coherentes, sin falsos ceros | Pendiente | Pendiente de ejecución y cotejo específico contra el caso completo. |
| CP-037 | HU-022 | Revisar auditoría y probar acceso indebido | Log íntegro y acceso por rol | Pendiente | Pendiente de ejecución y cotejo específico contra el caso completo. |
| CP-038 | HU-023 | Backtesting real con corte temporal | Métricas/base/coverage reproducibles | Bloqueado | Requiere infraestructura, fuente, entorno o decisión externa. |
| CP-039 | HU-023 | Evaluar periodo con cero real | WAPE no definido, sin división errónea | Probado | test_score_reports_signed_bias_and_marks_zero_actual_denominator_unavailable; alcance local |
| CP-040 | HU-024 | Consultar recomendación pasada | Modelo/fecha/datos originales intactos | Pendiente | Pendiente de ejecución y cotejo específico contra el caso completo. |
| CP-041 | HU-025 | Importar VM a hipervisor elegido | Arranque y web accesibles | Bloqueado | Requiere infraestructura, fuente, entorno o decisión externa. |
| CP-042 | HU-025 | Instalar desde host limpio | Sin dependencias de portátil del dev | Bloqueado | Requiere infraestructura, fuente, entorno o decisión externa. |
| CP-043 | HU-026 | Entrar por HTTPS y probar firewall | Certificado y controles correctos | Bloqueado | Requiere infraestructura, fuente, entorno o decisión externa. |
| CP-044 | HU-027 | Reiniciar VM con ventas y pedidos | Persistencia y autoarranque | Bloqueado | Requiere infraestructura, fuente, entorno o decisión externa. |
| CP-045 | HU-028 | Restaurar backup en segunda VM | Registros/archivos recuperados | Bloqueado | Requiere infraestructura, fuente, entorno o decisión externa. |
| CP-046 | HU-028 | Validar respaldo corrupto | Error y alerta; no falso éxito | Bloqueado | Requiere infraestructura, fuente, entorno o decisión externa. |
| CP-047 | HU-029 | Detener worker o llenar volumen controlado | Alerta y diagnóstico | Bloqueado | Requiere infraestructura, fuente, entorno o decisión externa. |
| CP-048 | HU-030 | Actualizar/revertir en clon | Integridad y plan documentado | Bloqueado | Requiere infraestructura, fuente, entorno o decisión externa. |
| CP-049 | HU-031 | Desconectar salida a internet (si aplica) | Operación offline verificada | Bloqueado | Requiere infraestructura, fuente, entorno o decisión externa. |
| CP-050 | HU-032 | Login erróneo / logout / sesión revocada | Protección y denegación apropiadas | Pendiente | Pendiente de ejecución y cotejo específico contra el caso completo. |
| CP-051 | HU-033 | Completar pedido con teclado a zoom 200 % | Sin bloqueos de accesibilidad | Pendiente | Pendiente de ejecución y cotejo específico contra el caso completo. |
| CP-052 | HU-034 | 5–10 sesiones de carga de prueba **tentativas** | Latencia medible vs objetivo acordado | Bloqueado | Requiere infraestructura, fuente, entorno o decisión externa. |
| CP-053 | HU-035 | Configurar VM desde cero | Alta de admin, fuente y primera inferencia | Bloqueado | Requiere infraestructura, fuente, entorno o decisión externa. |
| CP-054 | HU-036 | Alternar fuente demo/real | Etiquetas honestas y procedencia | Pendiente | Pendiente de ejecución y cotejo específico contra el caso completo. |

## Límites

- El caso de fechas comerciales distintas de entrega usa tarjetas simuladas; COR-03 sigue pendiente de definición empresarial.
- La suite automatizada no sustituye navegación real, restauración en VM, datos reales ni aceptación firmada.
- Esta matriz se conserva como revisión inicial y debe actualizarse con evidencias identificables por caso.
