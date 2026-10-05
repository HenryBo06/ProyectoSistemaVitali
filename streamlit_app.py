"""Vitali's native Streamlit interface, sharing Django accounts and workflow."""

import os
from datetime import date, timedelta

import streamlit as st

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "vitali_web.settings")
# Only named server settings may come from Streamlit secrets.
try:
    for name in ("SMARTORDER_SECRET_KEY", "SMARTORDER_SETUP_CODE", "SMARTORDER_DB", "SMARTORDER_WEB_HOME",
                 "SMARTORDER_TELEGRAM_BOT_TOKEN", "SMARTORDER_TELEGRAM_CHAT_ID"):
        if name in st.secrets:
            os.environ.setdefault(name, str(st.secrets[name]))
except st.errors.StreamlitSecretNotFoundError:
    pass

import django
django.setup()

from django.contrib.auth import get_user_model
from django.conf import settings
from django.core.exceptions import PermissionDenied
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.db import close_old_connections
from django.http import Http404
from django.utils import timezone

from operations.models import OrderLine
from operations.streamlit_bridge import call_screen, request_for, screen_context, sign_in, sign_out


st.set_page_config(page_title="SmartOrder AI · Vitali", page_icon="V", layout="wide")


@st.cache_resource
def prepare_database():
    # ponytail: migrate once per process; large deployments use the deployment migration step.
    call_command("migrate", interactive=False, verbosity=0)


prepare_database()
close_old_connections()
st.html("""<style>
    :root {--v-spectrum:linear-gradient(90deg,#5ba744 0% 16.66%,#f6cd28 16.66% 33.33%,#ee9233 33.33% 50%,#dd3a42 50% 66.66%,#42b7d3 66.66% 83.33%,#1479a5 83.33% 100%)}
    .stApp {color:#20364a;font-family:"Segoe UI",Arial,sans-serif}
    [data-testid="stSidebar"] {background:#f4f7fa;border-right:1px solid #dce5ec}
    [data-testid="stSidebar"] h1 {font-size:1.6rem!important}
    h1 {font-family:"Aptos Display","Segoe UI",sans-serif;letter-spacing:-.035em;line-height:1.15!important;font-weight:750!important}
    h2,h3 {letter-spacing:-.02em}
    [data-testid="stMetric"] {padding:1rem 1.2rem;background:#f4f7fa;border:1px solid #dce5ec;border-radius:10px;box-shadow:none}
    [data-testid="stMetricLabel"] {color:#506577}
    [data-testid="stMetricValue"] {font-variant-numeric:tabular-nums}
    [data-testid="stMainBlockContainer"] {max-width:1320px;padding-top:2.4rem;padding-bottom:3rem}
    [data-testid="stForm"] {border-color:#dce5ec;border-radius:12px;padding:1.4rem;background:white}
    [data-testid="stForm"]:has(input[aria-label="Usuario"]) {max-width:540px;margin-top:1.5rem}
    [data-testid="stVerticalBlockBorderWrapper"] {border-color:#dce5ec!important;border-radius:12px!important}
    [data-testid="stRadio"] label {padding:.4rem .25rem}
    [data-testid="stSidebar"] [data-testid="stRadio"] label {padding:.7rem .65rem;border-radius:8px;width:100%;margin:0!important}
    [data-testid="stSidebar"] [data-testid="stRadio"] label:has(input:checked) {background:white;box-shadow:inset 3px 0 #12688b;color:#105675}
    button:focus-visible,input:focus-visible {outline:3px solid #1479a5!important;outline-offset:3px}
    .vitali-identity {margin:0 0 1.6rem;border-bottom:1px solid #dce5ec;padding-bottom:1rem}
    .vitali-identity::before {content:"";display:block;height:5px;background:var(--v-spectrum);border-radius:3px;margin-bottom:1.1rem}
    .vitali-identity strong {font-size:1.3rem;letter-spacing:.18em;color:#12688b;font-weight:750}
    .vitali-identity span {display:block;font-size:.68rem;letter-spacing:.24em;color:#506577;margin-top:.15rem}
    @media(max-width:640px) {[data-testid="stMainBlockContainer"] {padding:4rem 1rem}}
    @media(prefers-reduced-motion:reduce) {* {animation:none!important;transition:none!important}}
</style>""")


def context(name, params=None, **route):
    return screen_context(name, st.session_state.get("session_key"), params, **route)


def action(name, data, files=None, **route):
    response, notices, key = call_screen(
        name, st.session_state.get("session_key"), data=data, files=files,
        local_setup=(os.environ.get("SMARTORDER_ALLOW_LOCAL_SETUP") == "1"
                     and st.get_option("server.address") in {"127.0.0.1", "localhost", "::1"}),
        home_url=os.environ.get("SMARTORDER_STREAMLIT_URL", "http://127.0.0.1:8501/"), **route,
    )
    st.session_state.session_key = key
    if response.status_code >= 400:
        st.error(response.content.decode("utf-8"))
        return None
    if notices:
        for level, message in notices:
            getattr(st, level if level in {"error", "warning", "success", "info"} else "info")(message)
        if any(level == "error" for level, _ in notices):
            return None
        # ponytail: preserve feedback across this UI's rerun for the same account and role.
        st.session_state.action_notices = (user.pk, user.is_staff, notices)
    return response


def source(c):
    if not user.is_staff:
        return
    status = c.get("source_status")
    if status == "reference":
        st.warning("Fuente sin verificar: las cifras son referencia y la cantidad se decide manualmente.")
    elif status == "stale":
        st.warning("El histórico no tiene ventas recientes. Revise las fechas antes de decidir un pedido.")
    if c.get("dataset"):
        dataset = c["dataset"]
        st.caption(f"Última venta: {dataset.last_date:%d/%m/%Y}")
        with st.expander("Fuente y cobertura"):
            st.write(dataset.filename)
            st.write(f"{dataset.first_date:%d/%m/%Y} — {dataset.last_date:%d/%m/%Y}")
            st.caption("Cada carga reemplaza el histórico activo; conserva las fuentes anteriores.")


def table(rows):
    if rows:
        st.dataframe(rows, hide_index=True, width="stretch")
    else:
        st.info("Aún no hay registros para mostrar.")


def dashboard():
    if not user.is_staff:
        st.title("Mi trabajo")
        st.caption("Disponibilidad, sugerencias y seguimiento de tu cartera.")
        c = context("dashboard")
        left, right = st.columns(2)
        left.metric("Clientes asignados", len(c["clients"]))
        right.metric("Pedidos de mi cartera", c["order_count"])
        st.subheader("Tu siguiente venta")
        with st.container(border=True):
            st.write("**Consulta productos y prepara la entrega**")
            st.caption("Elige un cliente de tu cartera, revisa la disponibilidad y confirma las condiciones de la venta.")
            st.button("Consultar productos", type="primary", on_click=st.session_state.update,
                      kwargs={"workspace": "Productos y pedidos"})
        st.subheader("Mi cartera")
        for client in c["clients"]:
            with st.container(border=True):
                name_col, action_col = st.columns([3, 1])
                name_col.write(f"**{client}**")
                name_col.caption("Cliente asignado a tu cartera")
                action_col.button("Ver productos", key=f"client:{client}", on_click=st.session_state.update,
                                  kwargs={"workspace": "Productos y pedidos", "product_client": client})
        if not c["clients"]:
            st.info("Solicita a administración que te asigne clientes para empezar.")
        return
    st.title("Resumen de ventas")
    st.caption("Ventas observadas y trabajo pendiente de tu cartera." if not user.is_staff
               else "Ventas observadas y pedidos pendientes de revisión.")
    c = context("dashboard")
    if not c.get("dataset"):
        st.info("Administración debe cargar el Excel de ventas para empezar.")
        return
    st.info(f"{c['kpis']['pending_orders_display']} pedidos pendientes · Consulta Pedidos y producción para continuar.")
    source(c)
    with st.container(border=True):
        filters = st.columns([2, 1, 1])
        client = filters[0].selectbox("Cliente", ["Todos", *c["clients"]])
        start = filters[1].date_input("Desde", value=c["period_start"])
        end = filters[2].date_input("Hasta", value=c["period_end"])
    if start > end:
        st.error("La fecha inicial debe ser anterior a la final.")
        return
    c = context("dashboard", {"client": "" if client == "Todos" else client,
                              "start_date": start, "end_date": end})
    cols = st.columns([2, 1, 1])
    cols[0].metric("Ventas del período · USD", c["kpis"]["sales_usd_display"])
    cols[1].metric("Productos con ventas", c["kpis"]["products_display"])
    cols[2].metric("Clientes de cartera" if not user.is_staff else "Clientes con ventas",
                   c["kpis"]["clients_display"])
    if not c["has_sales"]:
        st.info("Sin ventas para estos filtros. Selecciona otro cliente o período.")
        return
    left, right = st.columns([3, 2])
    with left:
        st.subheader("Ventas por mes")
        chart = c["chart_monthly"]
        st.vega_lite_chart({"Mes": chart["labels"], "Ventas USD": chart["values"]}, {
            "mark": {"type": "bar", "color": "#12688b"},
            "encoding": {"x": {"field": "Mes", "type": "nominal", "sort": chart["labels"]},
                         "y": {"field": "Ventas USD", "type": "quantitative"},
                         "tooltip": [{"field": "Mes"}, {"field": "Ventas USD", "format": ",.2f"}]},
        }, width="stretch")
    with right:
        st.subheader("Canales de venta")
        chart = c["chart_channels"]
        st.vega_lite_chart({"Canal": chart["labels"], "Ventas USD": chart["values"]}, {
            "mark": {"type": "arc", "innerRadius": 65},
            "encoding": {"theta": {"field": "Ventas USD", "type": "quantitative"},
                         "color": {"field": "Canal", "type": "nominal", "scale": {
                             "range": ["#1479a5", "#5ba744", "#ee9233", "#42b7d3", "#dd3a42", "#f6cd28"]}},
                         "tooltip": [{"field": "Canal"}, {"field": "Ventas USD", "format": ",.2f"}]},
        }, width="stretch")
    st.subheader("Productos con mayores ventas")
    chart = c["chart_products"]
    st.vega_lite_chart({"Producto": chart["labels"], "Ventas USD": chart["values"]}, {
        "mark": {"type": "bar", "color": "#12688b", "cornerRadiusEnd": 4},
        "encoding": {"x": {"field": "Ventas USD", "type": "quantitative"},
                     "y": {"field": "Producto", "type": "nominal", "sort": "-x"},
                     "tooltip": [{"field": "Producto"}, {"field": "Ventas USD", "format": ",.2f"}]},
    }, width="stretch")
    table([{"Producto": row["label"], "Ventas USD": row["value_display"],
            "Días con venta": row["days_with_sale"]} for row in c["top_products"]])


def products():
    st.title("Productos y pedidos")
    st.caption("Consulta disponibilidad y sugerencias; confirma ventas sin aprobación administrativa.")
    c = context("recommendations")
    source(c)
    if not c.get("clients"):
        st.info("Necesitas ventas cargadas y clientes asignados para consultar productos.")
        return
    left, right = st.columns([2, 1])
    client = left.selectbox("Cliente", c["clients"], key="product_client")
    delivery = right.date_input("Fecha requerida de entrega", value=c["required_date"],
                                min_value=c["min_date"], max_value=c["max_date"])
    c = context("recommendations", {"client": client, "required_date": delivery})
    st.caption(f"Entrega comprometida: {delivery:%d/%m/%Y}")
    st.subheader("Disponibilidad por producto")
    cards = c["recommendations"]
    for card in cards:
        with st.container(border=True):
            st.subheader(card["product"])
            first, second = st.columns(2)
            first.metric("Cantidad sugerida" if card["suggested"] is not None else "Cantidad para solicitar",
                         f"{card['suggested']:,.3f} {card['unit']}" if card["suggested"] is not None else "Decisión manual")
            second.metric("Existencia disponible", "Sin inventario" if card["available"] is None
                          else f"{card['available']:,.3f} {card['unit']}")
            if card["stock_date"]:
                st.caption(f"Corte de inventario: {card['stock_date']:%d/%m/%Y}")
            if card["inventory_warning"]:
                st.warning(card["inventory_warning"])
            st.caption(card["explanation"])
            if user.is_staff:
                with st.expander("Ventas históricas y explicación"):
                    st.write(card["explanation"])
                    st.write(f"Ventas históricas de este cliente: {card['sales_usd_display']} · {card['days_with_sale']} días con venta")
                    if card["reference_qty"] is not None:
                        st.write(f"Período de referencia: {card['reference_period']}")
                        st.write(f"Cantidad observada: {card['reference_qty']} · " +
                                 (card["unit"] if card["unit_confirmed"] else "Medida original del Excel"))
                    else:
                        st.caption("No hay un período equivalente del año anterior para este producto.")
                    if card["available"] is not None:
                        st.write(f"Entradas antes de entrega: {card['incoming']} · Compromisos previos: {card['commitments_before']} · Compromisos de la semana: {card['commitments']}")
    if user.is_staff:
        st.info("El vendedor confirma la venta. Administración autoriza producción en Pedidos y producción.")
        return
    st.subheader("Confirmar venta")
    st.caption("Selecciona productos, revisa cada cantidad e indica la unidad. Las cantidades manuales y ajustes requieren un motivo.")
    selected = st.multiselect("Productos a solicitar", [card["product"] for card in cards])
    by_product = {card["product"]: card for card in cards}
    with st.form("create_order"):
        payload = {"client": client, "required_date": delivery, "product": [],
                   "quantity": [], "unit": [], "reason": [], "unit_price": [], "discount_percent": []}
        for product in selected:
            card = by_product[product]
            st.write(f"**{product}**")
            quantity_col, unit_col = st.columns(2)
            qty = quantity_col.text_input("Cantidad solicitada", value=str(card["suggested"] or ""), key=f"qty:{client}:{delivery}:{product}")
            unit = unit_col.text_input("Unidad", value=card["unit"], disabled=card["unit_confirmed"], key=f"unit:{client}:{product}")
            price_col, discount_col = st.columns(2)
            price = price_col.text_input("Precio unitario USD", value="0", key=f"price:{product}")
            discount = discount_col.text_input("Descuento %", value="0", key=f"discount:{product}")
            reason = st.text_input("Motivo de cantidad manual o ajuste", key=f"reason:{product}")
            st.divider()
            payload["unit_price"].append(price)
            payload["discount_percent"].append(discount)
            for field, value in (("product", product), ("quantity", qty), ("unit", unit), ("reason", reason)):
                payload[field].append(value)
        with st.expander("Añadir otro producto"):
            new_product = st.text_input("Producto nuevo")
            new_qty = st.text_input("Cantidad del producto nuevo")
            new_unit = st.text_input("Unidad del producto nuevo")
            new_reason = st.text_input("Motivo del producto nuevo")
            new_price = st.text_input("Precio del producto nuevo USD", value="0")
            new_discount = st.text_input("Descuento del producto nuevo %", value="0")
        if new_product or new_qty:
            payload["unit_price"].append(new_price)
            payload["discount_percent"].append(new_discount)
            for field, value in (("product", new_product), ("quantity", new_qty), ("unit", new_unit), ("reason", new_reason)):
                payload[field].append(value)
        st.subheader("Condiciones de la venta")
        payload["payment_terms"] = st.text_input("Condiciones comerciales", max_chars=255)
        payload["note"] = st.text_area("Observaciones del pedido")
        if st.form_submit_button("Confirmar venta", type="primary"):
            action("order_create", payload)


def uploads():
    st.title("Datos en Excel")
    st.caption("Carga fuentes completas. Revisa su origen y la correspondencia de unidades antes de usarlas para decidir.")
    sales, stock = st.tabs(["Ventas", "Inventario"])
    with sales:
        c = context("sales_upload")
        if c["preview"]:
            p = c["preview"]
            st.info(f"Fuente activa: {p['filename']} · {p['first_date']:%d/%m/%Y} — {p['last_date']:%d/%m/%Y}")
            st.caption(f"{p['row_count']} filas · {p['clients']} clientes · {p['products']} productos")
            st.caption("Origen confirmado por administración" if p["verified"] else "Referencia, pendiente de confirmar")
            for note in p["notes"]:
                st.caption(note)
        with st.form("sales_import"):
            upload = st.file_uploader("Excel de ventas completo", type="xlsx")
            verified = st.checkbox("El archivo contiene ventas reales verificadas")
            origin = st.checkbox("Confirmé el origen del archivo")
            full = st.checkbox("Este archivo contiene el histórico completo y reemplaza la carga anterior")
            if st.form_submit_button("Cargar ventas", type="primary"):
                files = {"sales_file": SimpleUploadedFile(upload.name, upload.getvalue())} if upload else {}
                if action("sales_upload", {"verified": verified, "confirmed_origin": origin,
                                            "confirmed_full_snapshot": full}, files) is not None:
                    st.rerun()
        table([{"Archivo": d.filename, "Desde": d.first_date, "Hasta": d.last_date,
                "Filas": d.row_count, "Verificado": d.verified,
                "Activado por": d.uploaded_by.username, "Activo": d.active} for d in c["datasets"]])
    with stock:
        c = context("inventory_upload")
        if c["batch"]:
            batch, p = c["batch"], c["preview"]
            st.info(f"Inventario activo: {batch.filename} · {p['items']} existencias · {p['flows']} movimientos fechados")
            st.caption(f"Cortes del {p['oldest']:%d/%m/%Y} al {p['newest']:%d/%m/%Y}")
            st.caption("Equivalencia de unidad confirmada" if batch.unit_match_confirmed else "Equivalencia de unidad por confirmar")
            if batch.notes:
                st.info(batch.notes)
        response, _, _ = call_screen("inventory_template", st.session_state.session_key)
        st.download_button("Descargar plantilla de inventario", response.content, "plantilla_inventario_vitali.xlsx")
        with st.form("stock_import"):
            upload = st.file_uploader("Excel de inventario oficial", type="xlsx")
            match = st.checkbox("Confirmé que la unidad de inventario coincide con la de las ventas")
            if st.form_submit_button("Cargar inventario", type="primary"):
                files = {"inventory_file": SimpleUploadedFile(upload.name, upload.getvalue())} if upload else {}
                if action("inventory_upload", {"unit_match_confirmed": match}, files) is not None:
                    st.rerun()
        table([{"Cliente": i.client, "Producto": i.product, "Existencia": str(i.available),
                "Unidad": i.unit, "Fecha de corte": i.observed_on} for i in c["inventory_items"]])
        st.subheader("Actualizaciones anteriores")
        table([{"Archivo": batch.filename, "Fecha de carga": timezone.localtime(batch.created_at).strftime("%d/%m/%Y %H:%M"),
                "Productos": batch.items.count(), "Activado por": batch.uploaded_by.username,
                "Activo": batch.active} for batch in c["inventory_imports"]])


def sale_changes(detail):
    if not detail["can_edit_sale"] and not detail["can_cancel_sale"]:
        return
    order, lines = detail["order"], detail["lines"]
    with st.expander("Editar o cancelar antes de iniciar ejecución"):
        for item in lines:
            line = item if isinstance(item, dict) else {key: getattr(item, key) for key in ("id", "product", "requested_qty", "unit_price", "discount_percent", "status")}
            if not detail["can_edit_sale"]:
                continue
            with st.form(f"sale_edit:{line['id']}"):
                st.write(line["product"])
                qty = st.text_input("Cantidad", value=str(line["requested_qty"]))
                price = st.text_input("Precio USD", value=str(line["unit_price"]))
                discount = st.text_input("Descuento %", value=str(line["discount_percent"]))
                reason = st.text_input("Motivo del cambio", max_chars=2000)
                if st.form_submit_button("Guardar cambio"):
                    if action("sale_change", {"action": "edit", "line_id": line["id"], "quantity": qty, "unit_price": price, "discount_percent": discount, "change_reason": reason}, order_id=order.pk) is not None:
                        st.rerun()
        if detail["can_cancel_sale"]:
            with st.form(f"sale_cancel:{order.pk}"):
                reason = st.text_input("Motivo de cancelación", max_chars=2000)
                if st.form_submit_button("Cancelar venta"):
                    if action("sale_change", {"action": "cancel", "change_reason": reason}, order_id=order.pk) is not None:
                        st.rerun()


def odoo_order_panel(detail):
    operation = detail.get("odoo")
    if not operation:
        return
    st.subheader("Seguimiento de producción y entrega")
    st.caption("Cantidades y avance de la operación registrada en Odoo.")
    if not operation["enabled"]:
        st.info("La conexión está deshabilitada. Se muestran los últimos resultados registrados." if operation.get("synced") else
                "El seguimiento operativo está pendiente de configurar.")
        if not operation.get("synced"):
            return
    st.write(f"Estado: {operation['status']}")
    if operation.get("sale_name"):
        st.write(f"Venta: {operation['sale_name']}")
    updated = operation.get("last_synced")
    st.caption(f"Última actualización: {timezone.localtime(updated):%d/%m/%Y %H:%M}" if updated else "Última actualización: Pendiente")
    if operation.get("stale") and operation["enabled"]:
        st.warning("El avance está pendiente de actualizar. Se conservan los últimos resultados registrados; revisa su fecha.")
    if operation.get("error"):
        st.warning(operation["error"] if user.is_staff else
                   "El avance operativo está pendiente de actualizar. Administración revisará la conexión.")
    st.caption("Cantidades por producto y unidad; no se suman unidades distintas.")
    table([{"Producto": item["product"], "Unidad": item["unit"],
            **{label: str(item[key]) if item[key] is not None else placeholder
               for key, label, placeholder in (("ordered", "Vendido", "Por confirmar"),
                   ("approved", "Autorizado", "Pendiente"), ("reserved", "Reservado", "Por confirmar"),
                   ("produced", "Producido", "Por confirmar"), ("delivered", "Entregado", "Por confirmar"),
                   ("returned", "Devuelto", "Por confirmar"), ("scrapped", "Merma", "Por confirmar"))},
            "Avance": item["stage"]} for item in operation.get("lines", [])])
    for item in operation.get("lines", []):
        delivered_at = item.get("last_delivery_at")
        st.caption(f"{item['product']}: entrega neta {item.get('net_delivered', 'Por confirmar')} {item['unit']} · "
                   f"Pendiente {item.get('outstanding', 'Por confirmar')} {item['unit']} · "
                   + (f"Última entrega real: {timezone.localtime(delivered_at):%d/%m/%Y %H:%M}" if delivered_at else "Última entrega real: Pendiente"))
    if not user.is_staff:
        return
    st.caption("La fabricación usa las cantidades autorizadas en SmartOrder. Sincronizar una venta no autoriza su producción.")
    for action_name, label in (("sync", "Sincronizar venta con Odoo"), ("refresh", "Actualizar avance de Odoo")):
        if operation.get("can_" + action_name) and st.button(label, key=f"odoo:{detail['order'].pk}:{action_name}"):
            if action("order_odoo", {"action": action_name}, order_id=detail["order"].pk) is not None:
                st.rerun()
    for action_name, label, confirmation in (
        ("authorize", "Enviar cantidades autorizadas a fabricar", "Revisé las cantidades aprobadas y autorizo su envío a fabricación."),
        ("cancel", "Cancelar operación en Odoo", "Revisé la venta y sus operaciones antes de cancelar en Odoo."),
    ):
        if operation.get("can_" + action_name):
            with st.form(f"odoo:{detail['order'].pk}:{action_name}"):
                confirmed = st.checkbox(confirmation)
                if st.form_submit_button(label):
                    if not confirmed:
                        st.error("Confirma que revisaste la operación antes de continuar.")
                    elif action("order_odoo", {"action": action_name, "confirm_action": "1"}, order_id=detail["order"].pk) is not None:
                        st.rerun()
    finance = operation.get("admin_finance")
    if finance:
        st.subheader("Facturación y saldo")
        st.write(f"Saldo pendiente: {finance['balance'] if finance['balance'] is not None else 'Por confirmar'} {finance['currency']}")
        table([{"Documento": invoice["name"], "Tipo": invoice.get("type_label", "Por confirmar"),
                "Estado": invoice.get("state_label", invoice["state"]),
                "Estado de pago": invoice.get("payment_state_label", "Por confirmar"),
                "Total": invoice["total"] if invoice["total"] is not None else "Por confirmar",
                "Pendiente": invoice["residual"] if invoice["residual"] is not None else "Por confirmar",
                "Moneda": invoice["currency"]} for invoice in finance.get("invoices", [])])


def orders():
    st.title("Pedidos y producción")
    c = context("orders")
    st.caption("Ventas confirmadas y autorización de producción por producto; hasta 100 pedidos recientes.")
    st.caption("Los indicadores cuentan productos; cada producto tiene su propia decisión.")
    for col, (label, key) in zip(st.columns(3), [("Pendientes de producción", "pending"), ("Producción autorizada", "approved"), ("Productos enviados", "exported")]):
        col.metric(label, c["order_counts"][key])
    if not c["orders"]:
        st.info("Aún no hay pedidos. El vendedor puede crear uno en Productos y pedidos.")
        return
    table([{"Pedido": o.pk, "Cliente": o.client, "Vendedor": o.seller.username, "Entrega": o.required_date,
            "Estado": o.status_display, "Productos": o.line_count} for o in c["orders"]])
    order_id = st.selectbox("Pedido para consultar", [o.pk for o in c["orders"]])
    detail = context("order_detail", order_id=order_id)
    order = detail["order"]
    st.subheader(f"Pedido {order.pk} · {order.client}")
    st.caption(f"Entrega {order.required_date:%d/%m/%Y} · Vendedor {order.seller.username} · Creado {timezone.localtime(order.created_at):%d/%m/%Y %H:%M}")
    if order.note:
        st.write(order.note)
    sale_changes(detail)
    odoo_order_panel(detail)
    if not user.is_staff:
        st.success("Venta confirmada" if order.sale_confirmed else "Solicitud anterior al nuevo flujo")
        if order.payment_terms:
            st.write(f"Condiciones comerciales: {order.payment_terms}")
        for line in detail["lines"]:
            with st.container(border=True):
                st.subheader(line["product"])
                st.write(f"Vendido: {line['requested_qty']} {line['unit']}")
                st.write(f"Precio: USD {line['unit_price']} · Descuento: {line['discount_percent']} %")
                st.write(f"Producción: {line['get_status_display']}")
                if line["approved_qty"] is not None:
                    st.write(f"Cantidad autorizada para producción: {line['approved_qty']} {line['unit']}")
                if line["review_note"]:
                    st.write(f"Respuesta de administración: {line['review_note']}")
                future = line["future_quantity"]
                st.write("Disponibilidad por confirmar; entrega futura pendiente de planificación." if future is None else
                         f"Compromiso futuro: {future} {line['unit']} según inventario al confirmar." if future > 0 else
                         "Existencia suficiente según inventario al confirmar.")
                st.caption("La existencia observada no constituye una reserva ni confirma entrega.")
                if line["suggested_qty"] is not None:
                    st.write(f"Sugerencia: {line['suggested_qty']} {line['unit']}")
                if line["reason"]:
                    st.write(line["reason"])
        return
    st.success("Venta confirmada sin aprobación comercial" if order.sale_confirmed else "Solicitud anterior al nuevo flujo")
    st.write(f"Estado: {order.get_status_display()}")
    st.write(f"Condiciones comerciales: {order.payment_terms or 'Sin especificar'}")
    st.caption(f"Ventana de demanda: {order.required_date:%d/%m/%Y} — {detail['order_window_end']:%d/%m/%Y}")
    st.caption(f"Ventas usadas: {order.dataset.filename} · Inventario al crear: {order.inventory_batch.filename if order.inventory_batch else 'Sin inventario'}")
    if not order.dataset.verified:
        st.warning("Las cantidades se propusieron manualmente con ventas históricas de referencia. Revisa la explicación de cada producto.")
    if not detail["sources_current"]:
        st.warning("Las fuentes cambiaron. Administración debe revisar ventas e inventario vigentes y revalidar este pedido.")
        st.caption(f"Ventas actuales: {detail['current_sales'].filename if detail['current_sales'] else 'Sin archivo'} · Inventario actual: {detail['current_inventory'].filename if detail['current_inventory'] else 'Sin archivo'}")
        if user.is_staff and order.status != "exported":
            checked = st.checkbox("Revisé ventas e inventario vigentes para este pedido")
            if st.button("Revalidar fuentes", disabled=not checked):
                if action("order_revalidate", {"confirm_revalidation": "1"}, order_id=order_id) is not None:
                    st.rerun()
    for line in detail["lines"]:
        with st.container(border=True):
            st.write(f"**{line.product} · {line.get_status_display()}**")
            st.write(f"Solicitado: {line.requested_qty} {line.unit} · Aprobado: {line.approved_qty if line.approved_qty is not None else 'Pendiente'}")
            st.write(f"Precio unitario: USD {line.unit_price} · Descuento: {line.discount_percent} %")
            st.write(f"Compromiso futuro según inventario al confirmar: {line.future_quantity if line.future_quantity is not None else 'Por confirmar'} {line.unit}")
            st.caption("La existencia observada no constituye una reserva ni confirma entrega.")
            st.write(f"Base: {line.method or 'Manual'}")
            if line.reference_qty is not None:
                st.write(f"Cantidad en período comparable: {line.reference_qty} {line.unit}")
            if line.suggested_qty is not None:
                st.write(f"Sugerencia: {line.suggested_qty} {line.unit}")
            st.caption(f"Motivo del vendedor: {line.reason or 'Cantidad sugerida aceptada'}")
            if line.review_note:
                st.write(f"Revisión: {line.review_note}")
            if user.is_staff and line.status == OrderLine.Status.PENDING and detail["sources_current"]:
                with st.form(f"review:{line.pk}"):
                    qty = st.text_input("Cantidad aprobada", value=str(line.requested_qty))
                    note = st.text_input("Motivo del ajuste o rechazo")
                    approve = st.form_submit_button("Autorizar producción", type="primary")
                    reject = st.form_submit_button("No autorizar producción")
                    if approve or reject:
                        if action("order_review", {"line_id": line.pk, "decision": "approve" if approve else "reject",
                                                   "approved_quantity": qty, "review_note": note}, order_id=order_id) is not None:
                            st.rerun()
    if user.is_staff:
        st.subheader("Preparar archivo para producción")
        approved = {line.pk: line for line in c["approved_lines"]}
        if not approved:
            st.info("No hay productos aprobados con fuentes vigentes para exportar.")
            return
        selected = st.multiselect("Productos aprobados", list(approved), format_func=lambda pk:
                                  f"Pedido {approved[pk].order_id} · {approved[pk].order.client} · {approved[pk].product}")
        if st.button("Preparar XLSX", disabled=not selected):
            response = action("export_batch", {"line_ids": selected, "action": "download"})
            if response is not None:
                st.session_state.export_file = (sorted(selected), response.content)
        export = st.session_state.get("export_file")
        if export and export[0] == sorted(selected) and selected:
            downloaded = st.download_button("Descargar XLSX para producción", export[1], "pedidos_produccion_vitali.xlsx")
            if downloaded:
                st.session_state.downloaded_ids = export[0]
            confirm = st.checkbox("Ya compartí este archivo con producción")
            if st.button("Registrar envío a producción", disabled=not confirm or st.session_state.get("downloaded_ids") != sorted(selected)):
                if action("export_batch", {"line_ids": selected, "action": "confirm", "shared_confirm": True}) is not None:
                    st.session_state.pop("export_file", None)
                    st.session_state.pop("downloaded_ids", None)
                    st.rerun()


def odoo_settings_screen():
    st.title("Conexión y catálogos de Odoo")
    st.caption("Relaciona clientes, productos y unidades antes de transferir una venta.")
    c = context("odoo_settings")
    st.subheader("Estado de la conexión")
    st.write(f"Servicio: {c['url'] or 'Pendiente de configurar'}")
    st.write(f"Base: {c['database'] or 'Pendiente de configurar'}")
    st.write("Conexión habilitada: Sí" if c["enabled"] else "Conexión habilitada: No")
    st.write("Acceso configurado: Sí" if c["key_configured"] else "Acceso configurado: Pendiente")
    st.info(c["connection_status"])
    st.caption("Las credenciales se mantienen en la configuración privada del equipo.")
    if st.button("Comprobar conexión"):
        if action("odoo_settings", {"action": "check"}) is not None:
            st.rerun()
    st.subheader("Relacionar un producto")
    with st.form("odoo_product_mapping"):
        product = st.text_input("Producto SmartOrder", help="Productos disponibles: " + ", ".join(c["product_options"]))
        sku = st.text_input("Código de producto en Odoo")
        source_unit = st.text_input("Unidad de la fuente", help="Unidad exacta de SmartOrder; deja vacío solo si la fuente carece de unidad.")
        unit = st.text_input("Unidad de Odoo", help="Identificador externo; por ejemplo uom.product_uom_kgm para kilogramos.")
        if st.form_submit_button("Guardar relación de producto", type="primary"):
            if action("odoo_settings", {"action": "product", "product": product,
                                       "sku": sku, "unit": unit, "source_unit": source_unit}) is not None:
                st.rerun()
    st.subheader("Relacionar un cliente")
    with st.form("odoo_customer_mapping"):
        client = st.text_input("Cliente SmartOrder", help="Clientes disponibles: " + ", ".join(c["client_options"]))
        code = st.text_input("Código de cliente en Odoo")
        if st.form_submit_button("Guardar relación de cliente", type="primary"):
            if action("odoo_settings", {"action": "customer", "client": client, "code": code}) is not None:
                st.rerun()
    st.subheader("Productos relacionados")
    table([{"Producto SmartOrder": row["product"], "Código Odoo": row["sku"],
            "Unidad de fuente": row["source_unit"] or "Sin unidad en fuente",
            "Unidad Odoo": row["unit"]} for row in c["products"]])
    st.subheader("Clientes relacionados")
    table([{"Cliente SmartOrder": row["client"], "Código Odoo": row["code"]} for row in c["customers"]])


def operational_reports_screen():
    st.title("Resultados operativos")
    st.caption("Cumplimiento y cantidades reales registradas por venta y producto.")
    c = context("operational_reports", st.session_state.get("ops_report_filters"))
    if c.get("notice"):
        st.info(c["notice"])
    with st.form("ops_report_period"):
        left, right = st.columns(2)
        start = left.date_input("Entrega desde", value=c["from_date"])
        end = right.date_input("Entrega hasta", value=c["to_date"])
        if st.form_submit_button("Aplicar período"):
            if start and end and start > end:
                st.error("La fecha inicial debe ser anterior o igual a la final.")
            else:
                st.session_state.ops_report_filters = {key: value for key, value in
                    (("from_date", start), ("to_date", end)) if value is not None}
                st.rerun()
    st.subheader("Cumplimiento por unidad")
    st.caption("Cada unidad se compara por separado; no se suman kilogramos con unidades o cajas.")
    table([{"Unidad": row["unit"], **{label: str(row[key]) if row[key] is not None else "Por confirmar"
            for key, label in (("ordered", "Vendido"), ("delivered", "Entregado"),
                              ("pending", "Pendiente"), ("scrapped", "Merma"),
                              ("fulfillment_percent", "Cumplimiento %"))}} for row in c["totals"]])
    st.subheader("Detalle por venta y producto")
    table([{"Venta": row["order_id"], "Cliente": row["client"], "Producto": row["product"],
            "Unidad": row["unit"], **{label: str(row[key]) if row[key] is not None else "Por confirmar"
                for key, label in (("ordered", "Vendido"), ("reserved", "Reservado"), ("produced", "Producido"),
                                   ("delivered", "Salida al cliente"), ("returned", "Devuelto"), ("net_delivered", "Entrega neta"), ("scrapped", "Merma"),
                                   ("pending", "Pendiente"))}, "Avance": row["stage"],
            "Entrega comprometida": row["required_date"].strftime("%d/%m/%Y"),
            "A tiempo": "Por confirmar" if row["on_time"] is None else "Sí" if row["on_time"] else "No",
            "Margen simulado": row["margin"] if row["margin"] is not None else "Por confirmar",
            "Actualizado": timezone.localtime(row["last_synced"]).strftime("%d/%m/%Y %H:%M") if row["last_synced"] else "Pendiente"} for row in c["rows"]])
    st.subheader("Sugerencia y ejecución de fabricación")
    st.caption("Comparación de reposición/fabricación por producto y unidad. La diferencia es producido menos sugerido; no mide error de demanda ni beneficio económico.")
    table([{"Venta": row["order_id"], "Producto": row["product"], "Unidad": row["unit"],
            **{label: str(row.get(key)) if row.get(key) is not None else "Por confirmar"
               for key, label in (("suggested", "Sugerido"), ("approved", "Autorizado"),
                                  ("produced", "Producido"), ("production_difference", "Diferencia de producción"))},
            "Método": row.get("method") or "Manual / sin sugerencia",
            "Venta creada": timezone.localtime(row["created_at"]).strftime("%d/%m/%Y %H:%M") if row.get("created_at") else "Por confirmar",
            "Fuente conservada de ventas": row.get("source", "Por confirmar"),
            "Período de fuente": f"{row['source_from']:%d/%m/%Y} a {row['source_to']:%d/%m/%Y}" if row.get("source_from") and row.get("source_to") else "Por confirmar",
            "Fuente conservada de inventario": row.get("inventory_source") or "Por confirmar"} for row in c["rows"]])
    st.subheader("Diferencias del inventario importado")
    st.caption("Lectura aprobada menos lectura anterior del mismo cliente, producto y archivo. Incluye antecedentes anteriores al período para evitar contar dos veces una corrección. No representa conteos físicos ni pérdidas de Odoo.")
    table([{"Unidad": row["unit"], "Correcciones": row["count"],
            "Diferencia acumulada": str(row["difference"]) if row["difference"] is not None else "Por confirmar"}
           for row in c.get("correction_totals", [])])
    table([{"Cliente": row["client"], "Producto": row["product"], "Unidad": row["unit"],
            **{label: str(row[key]) if row[key] is not None else "Por confirmar"
               for key, label in (("previous", "Lectura anterior"), ("observed", "Lectura aprobada"), ("difference", "Diferencia"))},
            "Fecha observada": row["observed_on"].strftime("%d/%m/%Y"),
            "Aprobada": timezone.localtime(row["approved_at"]).strftime("%d/%m/%Y %H:%M"),
            "Administrador": row["approved_by"], "Motivo": row["reason"], "Fuente": row["source"]}
           for row in c.get("corrections", [])])


def accounts():
    st.title("Usuarios y cartera")
    c = context("users")
    table([{"Usuario": u["username"], "Rol": u["role"], "Activo": u["is_active"],
            "Clientes": ", ".join(u["assigned_clients"])} for u in c["users"]])
    with st.expander("Crear cuenta"):
        with st.form("new_user", clear_on_submit=True):
            name = st.text_input("Nombre de usuario")
            password = st.text_input("Contraseña inicial", type="password")
            role = st.selectbox("Rol", ["vendedor", "admin"])
            clients = st.multiselect("Clientes asignados (solo vendedor)", c["clients"])
            if st.form_submit_button("Crear cuenta", type="primary"):
                if action("users", {"action": "create", "username": name, "password": password,
                                    "role": role, "clients": clients}) is not None:
                    st.rerun()
    if not c["accounts"]:
        return
    account = st.selectbox("Cuenta para administrar", c["accounts"], format_func=lambda u: u.username)
    if not account.is_staff:
        with st.form("assignment"):
            clients = st.multiselect("Cartera", c["clients"], default=c["assignments"].get(account.pk, []))
            if st.form_submit_button("Guardar cartera"):
                if action("users", {"action": "assign", "user_id": account.pk, "clients": clients}) is not None:
                    st.rerun()
    with st.form("change_role"):
        role = st.selectbox("Nuevo rol", ["admin", "vendedor"], index=0 if account.is_staff else 1)
        if st.form_submit_button("Guardar rol y cerrar sesiones"):
            if action("users", {"action": "role", "user_id": account.pk, "role": role}) is not None:
                st.rerun()
    table([{"Fecha": timezone.localtime(event.created_at).strftime("%d/%m/%Y %H:%M"), "Usuario": event.actor.username, "Acción": event.action, "Registro": event.target} for event in c["access_events"]])
    with st.form("reset", clear_on_submit=True):
        password = st.text_input("Nueva contraseña", type="password")
        if st.form_submit_button("Restablecer contraseña"):
            action("users", {"action": "reset_password", "user_id": account.pk, "password": password})
            st.session_state.pop("action_notices", None)
    if st.button("Desactivar cuenta" if account.is_active else "Activar cuenta"):
        if action("users", {"action": "toggle", "user_id": account.pk}) is not None:
            st.rerun()


def corrections():
    st.title("Correcciones de inventario")
    c = context("correction_requests")
    st.caption("La solicitud del vendedor requiere revisión administrativa; el Excel original se conserva.")
    if not user.is_staff and c["clients"] and c["products"]:
        with st.form("correction"):
            client = st.selectbox("Cliente", c["clients"])
            product = st.selectbox("Producto", c["products"])
            stock = st.text_input("Existencia propuesta")
            reason = st.text_area("Motivo de la corrección")
            if st.form_submit_button("Solicitar corrección", type="primary"):
                if action("correction_requests", {"client": client, "product": product,
                                                   "claimed_stock": stock, "reason": reason}) is not None:
                    st.rerun()
    for row in c["corrections"]:
        with st.container(border=True):
            st.write(f"**{row.client} · {row.product} · {row.get_status_display()}**")
            st.write(f"Propuesta: {row.proposed_available} {row.unit} · {row.observed_on:%d/%m/%Y}")
            st.write(row.reason)
            if row.review_note:
                st.caption(row.review_note)
            if user.is_staff and row.status == "pending":
                with st.form(f"correction_review:{row.pk}"):
                    note = st.text_input("Motivo de revisión")
                    approve = st.form_submit_button("Aprobar corrección")
                    reject = st.form_submit_button("Rechazar corrección")
                    if approve or reject:
                        if action("correction_requests", {"correction_id": row.pk, "action": "approve" if approve else "reject", "review_note": note}) is not None:
                            st.rerun()
    if not c["corrections"]:
        st.info("Aún no hay correcciones solicitadas.")


user = request_for(st.session_state.get("session_key")).user
notice_user, notice_admin, saved_notices = st.session_state.pop("action_notices", (None, None, []))
if user.is_authenticated and (notice_user, notice_admin) == (user.pk, user.is_staff):
    for level, message in saved_notices:
        getattr(st, level if level in {"warning", "success", "info"} else "info")(message)
if not user.is_authenticated:
    st.html('<div class="vitali-identity"><strong>VITALI</strong><span>ALIMENTOS</span></div>')
    st.title("SmartOrder AI")
    st.caption("Vitali Alimentos · Ventas, pedidos y producción")
    initial = not get_user_model().objects.exists()
    allowed = os.environ.get("SMARTORDER_ALLOW_LOCAL_SETUP") == "1" and st.get_option("server.address") in {"127.0.0.1", "localhost", "::1"}
    remote_setup = len(settings.SMARTORDER_SETUP_CODE) >= 32
    if initial and not allowed and not remote_setup:
        st.info("Para crear la cuenta inicial en Streamlit Cloud, configura SMARTORDER_SETUP_CODE (mínimo 32 caracteres) en App settings → Secrets. En el equipo local, ejecuta iniciar_streamlit.cmd.")
        st.stop()
    with st.form("login", clear_on_submit=True):
        st.subheader("Crear administrador inicial" if initial else "Entrar a tu cuenta")
        setup_code = st.text_input("Código de instalación", type="password") if initial and not allowed else ""
        username = st.text_input("Usuario")
        password = st.text_input("Contraseña", type="password", help="Al menos 12 caracteres para una cuenta nueva.")
        if st.form_submit_button("Crear administrador" if initial else "Entrar", type="primary"):
            if initial:
                response = action("setup", {"username": username, "password": password,
                                              "setup_code": setup_code})
                if response is not None:
                    st.rerun()
            else:
                key = sign_in(username, password, st.session_state.get("session_key"))
                if key:
                    st.session_state.session_key = key
                    st.rerun()
                st.error("Usuario o contraseña incorrectos, o cuenta desactivada.")
    st.stop()

with st.sidebar:
    st.html('<div class="vitali-identity"><strong>VITALI</strong><span>ALIMENTOS</span></div>')
    st.title("SmartOrder AI")
    st.write(f"**{user.username}**")
    st.caption("Administración" if user.is_staff else "Vendedor · cartera asignada")
    screens = {("Resumen de ventas" if user.is_staff else "Mi trabajo"): dashboard, "Productos y pedidos": products,
               "Pedidos y producción": orders, "Correcciones de inventario": corrections}
    if user.is_staff:
        screens.update({"Datos en Excel": uploads, "Usuarios y cartera": accounts,
                        "Conexión y catálogos de Odoo": odoo_settings_screen,
                        "Resultados operativos": operational_reports_screen})
    page = st.radio("Espacios de trabajo", list(screens), key="workspace")
    if st.button("Cerrar sesión"):
        sign_out(st.session_state.session_key)
        st.session_state.clear()
        st.rerun()
    st.caption("El vendedor confirma ventas. Administración autoriza producción.")

st.html('<div class="vitali-identity"><strong>VITALI</strong><span>ALIMENTOS · SMARTORDER</span></div>')
try:
    screens[page]()
except (PermissionDenied, Http404) as exc:
    st.error(str(exc) or "No tienes permiso para consultar este registro.")
finally:
    close_old_connections()
