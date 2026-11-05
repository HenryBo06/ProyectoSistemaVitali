# SmartOrder AI — Tarifas y datos sintéticos en la VM local

## Preparación automática sin rellenar formularios

Solo con el XLSX **Demo_Ventas_Avicola_2025_IA_Pedidos_v2.xlsx** activo como laboratorio, el administrador puede entrar en **Clientes y tarifas** (`/tarifas/`), marcar la confirmación explícita y pulsar **Cargar datos sintéticos faltantes**.

La operación crea, para los pares cliente/producto existentes en el Excel:
- tarifas ficticias USD, redondeadas a dos decimales usando la mediana de los precios del XLSX ficticio;
- objetivo de reposición simulado de 20 unidades de presentación;
- conteo simulado de 6 unidades de presentación en la ubicación marcada **LAB - conteo simulado (no operativo)**.

Es **idempotente**: una segunda ejecución no duplica estos datos. **Nunca sobrescribe tarifas, objetivos o conteos existentes**, no crea pedidos ni usuarios, no asigna clientes a vendedores y no verifica la fuente de ventas ni habilita cantidades automáticas. Los campos de inventario real seguirán sin verificar.

### Presentaciones solo para la simulación

- kg: Alitas, Muslo y Encuentro, Nuggets, Pechuga, Pollo Entero Fresco.
- lb: Chorizo de Pollo, Jamón de Pavo.
- unid: Huevos Docena (una unidad de presentación de la demostración; **no** se interpreta como huevo individual).

Esta asignación es **una convención del laboratorio**, no una deducción de los registros históricos. No se convierten ni mezclan cantidades kg/lb/unid.

El vendedor ve un aviso de datos simulados cuando se carga el laboratorio. Para asignar clientes reales a un vendedor, la operación separada sigue en **Usuarios y clientes**.

## Dólares y quetzales

En **Clientes y tarifas**:
1. Seleccionar cliente, producto y presentación.
2. Elegir **USD** o **GTQ**.
3. Introducir un precio positivo con **hasta dos decimales** en la moneda seleccionada. No se acepta 0.0006.
4. Si se elige GTQ, proporcionar el tipo de cambio **GTQ por 1 USD** (hasta seis decimales) y su fecha pasada o presente. El sistema no hace consultas externas.
5. Guardar. Se conserva el precio cotizado, el tipo de cambio, su fecha y el **equivalente USD** de cuatro decimales interno. El importe mostrado en USD tiene **dos decimales**.

**Ejemplo fechado, no tasa permanente:** el Banco de Guatemala publicó **Q 7.64082 por USD** para el 4 de noviembre de 2026. Verificar la tasa correspondiente a la fecha de la operación antes de utilizar el control: https://www.banguat.gob.gt/tipo_cambio/

Los pedidos, reportes contables y la integración Odoo **permanecen en USD**. El importe GTQ es **referencia comercial**, NO cobro/contabilidad/facturación en quetzales. Para liquidar en GTQ de verdad aún se necesitaría persistir moneda por documento y por línea, manejar impuestos/conciliación y confirmar soporte en Odoo. No confundir esta fase con ese alcance.

Los precios existentes de pedidos se conservan. La precisión de cuatro decimales en el modelo no se elimina porque es necesaria para conversión y compatibilidad contable; la visualización para personas usa centavos.

## Instalación en VM existente (no borra datos)

Desde XFCE:
```bash
cd ~/ProyectoSistemaVitali
git status --short
git fetch origin main
git merge --ff-only origin/main
sudo docker compose --env-file .env up -d --build web
sudo docker compose --env-file .env ps
sudo docker compose --env-file .env exec web python manage.py showmigrations operations
```

**Antes:** hacer backup de los datos PostgreSQL y conservar `.env` fuera del repositorio. No continuar con `git merge` si hay cambios locales no guardados, y no usar `down -v`, `volume prune` ni recrear la base. La imagen web ejecuta migraciones automáticamente al arrancar; comprobar la migración `0018_pricetariff_currency`.

Tras actualizar: iniciar sesión como admin, entrar a `/tarifas/`, usar el botón LAB, revisar la tabla y comprobar vendedor `/recomendaciones/` con un cliente asignado.

## Recuperación

Si la migración falla, revisar `sudo docker compose --env-file .env logs --tail=100 web`. No borrar volúmenes. Guardar versión Git, esquema PostgreSQL y fallo concreto. Un rollback de código requiere analizar compatibilidad de migraciones.

## Alcance

**Esta función es para la exposición académica/VM local.** No es una aprobación comercial de precios ni validación de inventario o predicción de demanda real.


## Seeder de cartera para vendedores existentes

Para que el vendedor no vea el selector vacío, el administrador puede ir a `/usuarios/`, localizar la cuenta vendedora activa (por ejemplo, `vendedor`), marcar la confirmación **LAB** y pulsar **Preparar cartera LAB de vendedor**. El sistema asigna los clientes *sin propietario* del histórico ficticio activo.

Alternativamente, en la VM con Docker (sustituye los nombres por los reales):

```bash
cd ~/ProyectoSistemaVitali
sudo docker compose --env-file .env exec -T web python manage.py seed_demo_seller --username vendedor --actor admin --dry-run
sudo docker compose --env-file .env exec -T web python manage.py seed_demo_seller --username vendedor --actor admin
```

Este proceso **no crea usuarios**, no modifica contraseñas, no reemplaza históricos, no altera tarifas ni inventa nuevas ventas. Los clientes ya asignados a otro vendedor se respetan. Repetir el comando no duplica la cartera y los clientes siguen asociados tras reiniciar la VM. Si luego se importa un Excel ficticio con un conjunto distinto de clientes, vuelve a ejecutar el seeder; nunca se apropian clientes nuevos automáticamente.

El seeder **da acceso al historial para la inferencia**; no modifica el cálculo de recomendaciones. Se necesitan registros de ventas para ese cliente y las unidades no verificadas continúan bajo revisión. Para probar: entrar como vendedor, abrir `/recomendaciones/`, seleccionar un cliente, confirmar producto prioritario, explicación y siguiente acción comercial.
