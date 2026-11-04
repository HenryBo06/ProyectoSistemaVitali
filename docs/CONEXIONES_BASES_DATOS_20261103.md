# Conexiones de bases de datos en SmartOrder AI (Vitali)

Desde el panel de Administración -> **Conexiones de bases de datos** se pueden registrar, editar, habilitar/deshabilitar y probar fuentes PostgreSQL. Streamlit ofrece registro y controles de test/habilitación con el mismo backend y permisos. Los vendedores no pueden consultar ese módulo.

## Seguridad
- **No** se guardan ni muestran contraseñas en la base de SmartOrder.
- Un administrador configura servidor, puerto, base, usuario con permiso SELECT y el nombre de una variable privada `SMARTORDER_SOURCE_<IDENTIFICADOR>_PASSWORD`.
- La contraseña se provisiona fuera de Git/DB; el host debe existir en la lista privada `SMARTORDER_SOURCE_DB_ALLOWED_HOSTS`, separada por comas. Por defecto solo loopback.
- Para hosts remotos se requiere TLS. Prueba de conexión = `BEGIN READ ONLY; SELECT 1; ROLLBACK` (timeout de conexión 5s, timeout de ejecución 3s), sin SQL definido por el navegador.
- Se registra actor y eventos administrativos de cambios y pruebas, sin credenciales.
- **El usuario del motor de origen debe contar con permisos SELECT exclusivamente**: el modo de transacción es una defensa adicional, no un sustituto de los permisos de la base.
- Cambiar los campos invalida la prueba anterior y deshabilita la conexión hasta aprobar una nueva prueba.

## Límites intencionales
Este módulo **todavía no extrae ni transforma ventas de PostgreSQL**: eso depende del diccionario de datos del ingeniero, de identificadores estables y de acordar fecha/cliente/producto/unidad. La ruta a inferencia no se activa al guardar un origen. Odoo conserva su integración independiente y no se convierte en conexión SQL directa.

Hay un primer adaptador real PostgreSQL porque psycopg ya forma parte de las dependencias. Otros motores se implementarán cuando se conozca el origen auténtico (SQL Server, MySQL, etc.), sin simular compatibilidad. La instalación de credenciales, accesos de red y VM queda en manos de infraestructura/Codex; los permisos y las pantallas son responsabilidad de la aplicación.

## Pruebas
La suite cubre registro, restricción de acceso por roles, bloqueo de activación no probada, TLS, allowlist, uso de secretos, consulta de solo lectura y registro administrativo. La prueba no depende de una base corporativa real y no acredita conexión a un servidor de Vitali.
