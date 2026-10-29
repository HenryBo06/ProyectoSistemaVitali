"""Vitali's native Streamlit interface, sharing Django accounts and workflow."""

import os
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import streamlit as st

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "vitali_web.settings")
# Only named server settings may come from Streamlit secrets.
try:
    for name in ("SMARTORDER_SECRET_KEY", "SMARTORDER_SETUP_CODE", "SMARTORDER_DB", "SMARTORDER_WEB_HOME", "SMARTORDER_DATABASE_URL", "SMARTORDER_MEDIA_ROOT",
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
st.html(f"<style>{(Path(__file__).resolve().parent / 'operations/static/operations/css/streamlit.css').read_text(encoding='utf-8')}</style>")


@st.cache_resource
def prepare_database():
    # ponytail: migrate once per process; large deployments use the deployment migration step.
    if settings.DATABASES["default"]["ENGINE"].endswith("sqlite3"):
        call_command("migrate", interactive=False, verbosity=0)


prepare_database()
close_old_connections()


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
    if not user.is_staff or not settings.SMARTORDER_MAINTENANCE_UI:
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


VEGA_CONFIG = {
    "font": "Segoe UI, Calibri, sans-serif",
    "axis": {"labelFont": "Segoe UI", "titleFont": "Segoe UI", "labelColor": "#4a6470", "titleColor": "#16323c", "gridColor": "#dce6ea"},
    "legend": {"labelFont": "Segoe UI", "titleFont": "Segoe UI", "labelColor": "#4a6470", "titleColor": "#16323c"},
}


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
        st.subheader("Requiere tu atención")
        if c.get("attention_orders"):
            for order in c["attention_orders"]:
                with st.container(border=True):
                    st.write(f"**{order.client} · pedido #{order.pk}**")
                    st.caption(f"Entrega solicitada {order.required_date:%d/%m/%Y} · {order.status_display}")
                    if st.button("Abrir seguimiento", key=f"attention:{order.pk}"):
                        st.session_state.pending_order_target = order.pk
                        st.session_state.pending_workspace = "Pedidos y producción"
                        st.rerun()
        else:
            st.info("No tienes novedades operativas pendientes.")
        st.subheader("Próximas entregas")
        if c.get("upcoming_orders"):
            table([{"Cliente": order.client, "Pedido": order.pk,
                    "Fecha comprometida": order.committed_date,
                    "Estado": order.status_display} for order in c["upcoming_orders"]])
        else:
            st.caption("Aún no hay entregas comprometidas.")
        if c.get("opportunities"):
            st.subheader("Oportunidades para revisar")
            for item in c["opportunities"][:3]:
                with st.container(border=True):
                    st.write(f"**{item['event']} · {item['client']}**")
                    st.caption(f"{item['product']} · entrega sugerida para revisar {item['date']:%d/%m/%Y}")
                    if st.button("Preparar esta venta", key=f"opportunity:{item['event_id']}:{item['client']}:{item['product']}:{item['date']}"):
                        st.session_state.pending_sale_context = {
                            "client": item["client"], "product": item["product"],
                            "required_date": item["date"], "mode": "sale",
                            "event_id": item["event_id"],
                        }
                        st.session_state.pending_workspace = "Productos y pedidos"
                        st.rerun()
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
            "mark": {"type": "bar", "color": "#0c6f7c", "cornerRadiusEnd": 4},
            "encoding": {"x": {"field": "Mes", "type": "nominal", "sort": chart["labels"]},
                         "y": {"field": "Ventas USD", "type": "quantitative"},
                         "tooltip": [{"field": "Mes"}, {"field": "Ventas USD", "format": ",.2f"}]},
            "config": VEGA_CONFIG,
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
            "config": VEGA_CONFIG,
        }, width="stretch")
    st.subheader("Productos con mayores ventas")
    chart = c["chart_products"]
    st.vega_lite_chart({"Producto": chart["labels"], "Ventas USD": chart["values"]}, {
        "mark": {"type": "bar", "color": "#0c6f7c", "cornerRadiusEnd": 4},
        "encoding": {"x": {"field": "Ventas USD", "type": "quantitative"},
                     "y": {"field": "Producto", "type": "nominal", "sort": "-x"},
                     "tooltip": [{"field": "Producto"}, {"field": "Ventas USD", "format": ",.2f"}]},
        "config": VEGA_CONFIG,
    }, width="stretch")
    table([{"Producto": row["label"], "Ventas USD": row["value_display"],
            "Días con venta": row["days_with_sale"]} for row in c["top_products"]])


def products():
    st.title("Productos y pedidos")
    st.caption("Consulta disponibilidad y sugerencias; confirma ventas sin aprobación administrativa.")
    c = context("recommendations")
    source(c)
    if not c.get("clients"):
        st.info("Consulta a administración para habilitar tu cartera y catálogo.")
        return
    pending_sale = st.session_state.pop("pending_sale_context", None)
    if pending_sale and pending_sale.get("client") in c["clients"]:
        st.session_state["product_client"] = pending_sale["client"]
        st.session_state["product_delivery_date"] = pending_sale["required_date"]
        st.session_state["product_mode"] = pending_sale.get("mode", "sale")
        st.session_state["product_event_context"] = (
            pending_sale["client"], pending_sale["required_date"]
        )
        st.session_state["product_event_id"] = pending_sale.get("event_id")
    left, right = st.columns([2, 1])
    client = left.selectbox("Cliente", c["clients"], key="product_client")
    if "product_delivery_date" not in st.session_state:
        st.session_state["product_delivery_date"] = c["required_date"]
    delivery = right.date_input(
        "Fecha requerida de entrega", min_value=c["min_date"], max_value=c["max_date"],
        key="product_delivery_date",
    )
    mode = "vitali" if user.is_staff else st.radio(
        "¿Qué quieres preparar?", ["sale", "client_restock"],
        format_func=lambda option: "Venta" if option == "sale" else "Reposición del cliente",
        horizontal=True, key="product_mode",
    )
    event_context = (client, delivery)
    if mode != "sale" or st.session_state.get("product_event_context") != event_context:
        st.session_state.pop("product_event_id", None)
        st.session_state.pop("product_event_context", None)
    event_id = st.session_state.get("product_event_id")
    event_params = {"event_id": event_id} if event_id else {}
    c = context("recommendations", {
        "client": client, "required_date": delivery, "mode": mode, **event_params,
    })
    st.caption(f"Entrega solicitada: {delivery:%d/%m/%Y} · administración confirmará el compromiso.")
    if not user.is_staff:
        st.info(
            "1. Cliente y entrega · 2. Productos · 3. Revisión · 4. Confirmación"
        )
    st.subheader("Productos disponibles")
    cards = c["recommendations"]
    if not cards:
        if not c.get("catalog_available"):
            st.info("El catálogo de ventas no está disponible. Administración debe cargar datos para consultar productos.")
        elif not c.get("client_in_catalog"):
            st.info("Este cliente no tiene ventas registradas en el catálogo activo. Solicita a administración revisar la asignación.")
        else:
            st.info("No hay productos con ventas registradas para este cliente en la consulta actual.")
        return
    if not user.is_staff:
        warnings = dict.fromkeys(card["inventory_warning"] for card in cards
                                 if card["inventory_warning"])
        for warning in warnings:
            st.warning(warning)
        lines = sorted({card.get("category") or "Sin línea" for card in cards})
        line_filter = st.selectbox("Línea de producto", ["Todas las líneas", *lines],
                                   key=f"product_line:{client}",
                                   disabled=bool(st.session_state.get("sale_review")))
        with st.expander("Consultar disponibilidad y sugerencias"):
            table([{
            "Producto": card["product"],
            "Cantidad sugerida": (f"{card['suggested']:,.3f} {card['unit']}"
                                  if card["suggested"] is not None else "Decisión manual"),
            "Disponibilidad Vitali": ("Por confirmar" if card["available"] is None
                                      else f"{card['available']:,.3f} {card.get('stock_unit') or 'unidad por confirmar'}"),
        } for card in cards if line_filter == "Todas las líneas"
          or (card.get("category") or "Sin línea") == line_filter])
        valid_products = [card["product"] for card in cards]
        previous_selection = list(st.session_state.get("products_selected", []))
        incompatible_products = [item for item in previous_selection if item not in valid_products]
        if "products_selected" in st.session_state:
            st.session_state.products_selected = [item for item in st.session_state.products_selected
                                                  if item in valid_products]
        if incompatible_products:
            st.warning("Se retiraron líneas incompatibles con el catálogo de este cliente: "
                       + ", ".join(incompatible_products))
        if pending_sale and pending_sale.get("product") in valid_products:
            st.session_state.products_selected = [pending_sale["product"]]
        visible_products = [card["product"] for card in cards if line_filter == "Todas las líneas"
                            or (card.get("category") or "Sin línea") == line_filter]
        selected_other_line = [item for item in st.session_state.get("products_selected", [])
                               if item in valid_products and item not in visible_products]
        product_options = list(dict.fromkeys([*visible_products, *selected_other_line]))
        if selected_other_line:
            st.caption("Los productos ya seleccionados de otras líneas se conservan.")
        selected = st.multiselect(
            "Productos a solicitar", product_options,
            placeholder="Selecciona productos para esta venta",
            key="products_selected",
            disabled=bool(st.session_state.get("sale_review")),
        )
        with st.expander("Ver explicación por producto"):
            for card in cards:
                if line_filter != "Todas las líneas" and (card.get("category") or "Sin línea") != line_filter:
                    continue
                st.markdown(f"**{card['product']}**")
                st.write(card["explanation"])
                if card.get("reference_date") and card.get("reference_qty") is not None:
                    st.caption(f"Venta registrada el {card['reference_date']:%d/%m/%Y}: "
                               f"{card['reference_qty']} {card['unit']} para este cliente.")
                if mode == "client_restock":
                    stock = (f"{card['client_stock']} {card['unit']} en {card['client_stock_location']}"
                             if card["client_stock"] is not None else "Conteo de hoy pendiente o vencido")
                    goal = (f"{card['client_stock_target']} {card['unit']}"
                            if card["client_stock_target"] is not None else "por definir")
                    st.caption(f"Existencias del cliente: {stock} · objetivo: {goal}.")
                    if card.get("client_stock_projected") is not None:
                        st.caption(
                            f"Proyección al {delivery:%d/%m/%Y}: {card['client_stock_projected']} {card['unit']} · "
                            f"entradas confirmadas {card['client_stock_incoming']} · "
                            f"salidas confirmadas {card['client_stock_outgoing']}."
                        )
                if settings.SMARTORDER_MAINTENANCE_UI and card["stock_date"]:
                    st.caption(f"Corte de inventario: {card['stock_date']:%d/%m/%Y}")
    else:
        for card in cards:
            with st.container(border=True):
                st.subheader(card["product"])
                first, second = st.columns(2)
                first.metric("Cantidad sugerida" if card["suggested"] is not None else "Cantidad para solicitar",
                             f"{card['suggested']:,.3f} {card['unit']}" if card["suggested"] is not None else "Decisión manual")
                second.metric("Existencia disponible", "Sin inventario" if card["available"] is None
                              else f"{card['available']:,.3f} {card.get('stock_unit') or 'unidad por confirmar'}")
                if settings.SMARTORDER_MAINTENANCE_UI and card["stock_date"]:
                    st.caption(f"Corte de inventario: {card['stock_date']:%d/%m/%Y}")
                if card["inventory_warning"]:
                    st.warning(card["inventory_warning"])
                st.caption(card["explanation"])
                with st.expander("Planificación de cantidades") :
                    st.write(card["explanation"])
                    if card["reference_qty"] is not None:
                        st.write(f"Período de referencia: {card['reference_period']}")
                        st.write(f"Cantidad observada: {card['reference_qty']} · " +
                                 (card["unit"] if card["unit_confirmed"] else "Unidad pendiente de confirmar"))
                    else:
                        st.caption("No hay un período equivalente del año anterior para este producto.")
                    if card["available"] is not None:
                        st.write(f"Entradas antes de entrega: {card['incoming']} · Compromisos previos: {card['commitments_before']} · Compromisos de la semana: {card['commitments']}")
                evaluation = card.get("sale_evaluation")
                if evaluation:
                    with st.expander("Evaluación de sugerencia comercial"):
                        unit_label = card["unit"] or "unidad registrada"
                        mae = f"{evaluation['mae']:.3f} {unit_label}" if evaluation["mae"] is not None else "Sin datos suficientes"
                        baseline = (f"{evaluation['baseline_mae']:.3f} {unit_label}"
                                    if evaluation["baseline_mae"] is not None else "Sin datos suficientes")
                        over = (f"{evaluation['overforecast']:.3f} {unit_label}"
                                if evaluation["overforecast"] is not None else "Sin datos suficientes")
                        baseline_over = (f"{evaluation['baseline_overforecast']:.3f} {unit_label}"
                                         if evaluation["baseline_overforecast"] is not None else "Sin datos suficientes")
                        st.write(f"Ventanas de auditoría: {evaluation['windows']}/{evaluation['targets']} · "
                                 + (f"Cobertura: {evaluation['coverage']:.0%} · "
                                    if evaluation["coverage"] is not None else "Cobertura: sin ventanas · ")
                                 + ("Serie habilitada para sugerencias automáticas." if evaluation["automatic"]
                                    else "Esta serie permanece en revisión manual."))
                        st.write(f"Error absoluto medio: {mae} · referencia simple: {baseline}.")
                        st.write(f"Sobreestimación media: {over} · referencia simple: {baseline_over}.")
                        if evaluation["event"]:
                            st.caption(f"Evaluación separada para {evaluation['event']}.")
                        st.caption("La cobertura corresponde a ventanas cronológicas; la precisión comercial requiere validación con ventas reales recientes.")
    if user.is_staff:
        st.info("El vendedor confirma la venta. Administración autoriza producción en Pedidos y producción.")
        return
    c = context("order_create", {
        "client": client, "required_date": delivery, "mode": mode, **event_params,
    })
    scope = f"{user.pk}:{client}:{delivery}:{mode}:{event_id or ''}"
    cards = c["products"]
    if st.session_state.get("sale_scope") != scope:
        old_scope = st.session_state.get("sale_scope")
        old_client = st.session_state.get("sale_client")
        old_selected = st.session_state.get("products_selected", [])
        old_review = st.session_state.get("sale_review")
        incompatible = []
        draft = {}
        if old_scope:
            for product in old_selected:
                quantity_key = f"qty:{old_scope}:{product}"
                reason_key = f"reason:{old_scope}:{product}"
                unit_key = f"unit:{old_scope}:{product}"
                if quantity_key in st.session_state:
                    draft[product] = {
                        "quantity": st.session_state[quantity_key],
                        "reason": st.session_state.get(reason_key, ""),
                        "unit": st.session_state.get(unit_key, ""),
                    }
            extra_draft = {
                name: st.session_state.get(f"new_{name}:{old_scope}", "")
                for name in ("product", "quantity", "unit", "reason", "price", "discount")
            }
            if old_client != client and any(str(value).strip() for value in extra_draft.values()):
                extra_draft = {name: "" for name in extra_draft}
                incompatible.append("Producto adicional")
            st.session_state.sale_draft_extra = extra_draft
            old_exception_reason = st.session_state.get(f"exception_reason:{old_scope}", "")
            old_exception_enabled = st.session_state.get(f"exception:{old_scope}", False)
            st.session_state.sale_draft_conditions = {
                "payment_terms": st.session_state.get(f"payment_terms:{old_scope}", ""),
                "note": st.session_state.get(f"sale_note:{old_scope}", ""),
                "exception_reason": "",
            }
            if old_client != client and (old_exception_reason or old_exception_enabled):
                incompatible.append("Excepción comercial")
        if old_review:
            st.session_state.sale_draft_conditions = {
                key: old_review.get(key, "")
                for key in ("payment_terms", "note")
            }
            st.session_state.sale_draft_conditions["exception_reason"] = ""
            if old_client != client and old_review.get("exception_reason"):
                incompatible.append("Excepción comercial")
            for product, quantity, unit, reason in zip(
                    old_review.get("product", []), old_review.get("quantity", []),
                    old_review.get("unit", []), old_review.get("reason", [])):
                draft[product] = {"quantity": quantity, "reason": reason, "unit": unit}
        new_cards = {card["product"]: card for card in cards}
        for product, values in draft.items():
            card = new_cards.get(product)
            if card is None:
                continue
            unit = str(values.get("unit", "")).strip()
            expected_unit = str(card.get("order_unit") or card.get("unit") or "").strip()
            if old_client != client and (
                    not unit or not expected_unit or unit.casefold() != expected_unit.casefold()):
                values.update(quantity="", reason="")
                incompatible.append(product)
            if old_client != client:
                values["unit"] = expected_unit
        if incompatible:
            st.session_state.sale_incompatible_lines = incompatible
        else:
            st.session_state.pop("sale_incompatible_lines", None)
        st.session_state.sale_draft = draft
        st.session_state.sale_scope = scope
        st.session_state.sale_client = client
        st.session_state.sale_token = c["submission_token"]
        st.session_state.pop("sale_review", None)
        st.session_state.pop("sale_draft_saved_for", None)
    review_active = bool(st.session_state.get("sale_review"))
    exception_key = f"exception:{scope}"
    if exception_key not in st.session_state:
        st.session_state[exception_key] = bool(
            st.session_state.get("sale_draft_conditions", {}).get("exception_reason")
        )
    st.subheader("2. Productos y condiciones")
    st.caption("Selecciona los productos de esta venta. Completa la cantidad y unidad; las cantidades manuales o ajustadas requieren un motivo.")
    st.caption("Las cantidades y notas se conservan al cambiar contexto; una unidad incompatible requiere volver a indicar la cantidad.")
    if st.session_state.get("sale_incompatible_lines"):
        st.warning("Se limpiaron los datos incompatibles y se regeneraron las condiciones del nuevo contexto: "
                   + ", ".join(st.session_state.sale_incompatible_lines))
    by_product = {card["product"]: card for card in cards}
    for product in selected:
        card = by_product[product]
        quantity_key = f"qty:{scope}:{product}"
        reason_key = f"reason:{scope}:{product}"
        unit_key = f"unit:{scope}:{product}"
        draft_line = st.session_state.get("sale_draft", {}).get(product, {})
        if quantity_key not in st.session_state:
            st.session_state[quantity_key] = draft_line.get("quantity", "")
        if reason_key not in st.session_state:
            st.session_state[reason_key] = draft_line.get("reason", "")
        if unit_key not in st.session_state:
            st.session_state[unit_key] = draft_line.get(
                "unit", (card.get("order_unit") or card["unit"]) if card.get("unit_mismatch")
                else card["unit"] or "",
            )
        if card["suggested"] is not None and card["suggested"] > 0:
            if st.button(f"Usar sugerencia · {card['suggested']:,.3f} {card['unit']}", key=f"use-suggestion:{scope}:{product}", disabled=review_active):
                st.session_state[quantity_key] = str(card["suggested"])
    with st.container():
        exception = st.checkbox("Solicitar excepción comercial", key=exception_key,
                                disabled=review_active)
        payload = {"client": client, "required_date": delivery, "product": [],
                   "quantity": [], "unit": [], "reason": [], "unit_price": [], "discount_percent": [],
                   "submission_token": st.session_state.sale_token, "suggestion_mode": mode,
                   "event_id": event_id or ""}
        for product in selected:
            card = by_product[product]
            st.write(f"**{product}**")
            quantity_col, unit_col = st.columns(2)
            qty = quantity_col.text_input("Cantidad solicitada", key=f"qty:{scope}:{product}", disabled=review_active)
            unit = unit_col.text_input(
                "Unidad", disabled=(card["unit_confirmed"] and not card.get("unit_mismatch"))
                or review_active, key=f"unit:{scope}:{product}",
            )
            price_col, discount_col = st.columns(2)
            price = price_col.text_input("Precio unitario USD", value=str(card["unit_price"] or ""), disabled=not exception or review_active, key=f"price:{scope}:{product}:{exception}")
            discount = discount_col.text_input("Descuento %", value=str(card["discount_percent"]), disabled=not exception or review_active, key=f"discount:{scope}:{product}:{exception}")
            reason = st.text_input("Motivo de cantidad manual o ajuste", key=f"reason:{scope}:{product}", disabled=review_active)
            st.divider()
            payload["unit_price"].append(price)
            payload["discount_percent"].append(discount)
            for field, value in (("product", product), ("quantity", qty), ("unit", unit), ("reason", reason)):
                payload[field].append(value)
        with st.expander("Otro producto (requiere excepción y revisión administrativa)"):
            extra = st.session_state.get("sale_draft_extra", {})
            new_product = st.text_input("Producto nuevo", value=extra.get("product", ""), key=f"new_product:{scope}", disabled=review_active)
            new_qty = st.text_input("Cantidad del producto nuevo", value=extra.get("quantity", ""), key=f"new_quantity:{scope}", disabled=review_active)
            new_unit = st.text_input("Unidad del producto nuevo", value=extra.get("unit", ""), key=f"new_unit:{scope}", disabled=review_active)
            new_reason = st.text_input("Motivo del producto nuevo", value=extra.get("reason", ""), key=f"new_reason:{scope}", disabled=review_active)
            new_price = st.text_input("Precio del producto nuevo USD", value=extra.get("price", "0") or "0", key=f"new_price:{scope}", disabled=review_active)
            new_discount = st.text_input("Descuento del producto nuevo %", value=extra.get("discount", "0") or "0", key=f"new_discount:{scope}", disabled=review_active)
        if exception and (new_product or new_qty):
            payload["unit_price"].append(new_price)
            payload["discount_percent"].append(new_discount)
            for field, value in (("product", new_product), ("quantity", new_qty), ("unit", new_unit), ("reason", new_reason)):
                payload[field].append(value)
        st.subheader("Condiciones de la venta")
        conditions = st.session_state.get("sale_draft_conditions", {})
        payload["payment_terms"] = st.text_input("Condiciones comerciales", max_chars=255,
                                                 value=conditions.get("payment_terms", ""),
                                                 key=f"payment_terms:{scope}", disabled=review_active)
        payload["note"] = st.text_area("Observaciones del pedido", value=conditions.get("note", ""),
                                       key=f"sale_note:{scope}", disabled=review_active)
        payload["exception_reason"] = st.text_area(
            "Motivo de excepción comercial", value=conditions.get("exception_reason", ""),
            disabled=not exception or review_active, key=f"exception_reason:{scope}",
        ) if exception else ""
        submit_action = st.radio(
            "Al continuar", ("Revisar pedido", "Guardar borrador"), horizontal=True,
            key=f"sale_submit_action:{scope}", disabled=review_active,
        )
        submitted = st.button("Continuar", type="primary", disabled=review_active)
        if submitted and submit_action == "Guardar borrador":
            st.session_state.sale_draft = {
                product: {"quantity": quantity, "reason": reason, "unit": unit}
                for product, quantity, reason, unit in zip(
                    payload["product"], payload["quantity"], payload["reason"], payload["unit"],
                )
            }
            st.session_state.sale_draft_conditions = {
                key: payload.get(key, "")
                for key in ("payment_terms", "note", "exception_reason")
            }
            st.session_state.sale_draft_extra = {
                "product": new_product, "quantity": new_qty, "unit": new_unit,
                "reason": new_reason, "price": new_price, "discount": new_discount,
            }
            st.session_state.sale_draft_saved_for = scope
        elif submitted:
            from decimal import Decimal, InvalidOperation
            try:
                if not payload["product"]:
                    raise ValueError
                for product, amount, unit, price, discount, reason in zip(
                        payload["product"], payload["quantity"], payload["unit"],
                        payload["unit_price"], payload["discount_percent"], payload["reason"]):
                    quantity_value, price_value, discount_value = map(
                        Decimal, (str(amount).strip(), str(price).strip(), str(discount).strip() or "0")
                    )
                    if (not unit.strip() or quantity_value <= 0 or price_value <= 0
                            or not all(value.is_finite() for value in (quantity_value, price_value, discount_value))
                            or not 0 <= discount_value < 100):
                        raise ValueError
                    card = by_product.get(product)
                    suggested = card.get("suggested") if card else None
                    if (card is None or suggested is None or quantity_value != suggested) and not reason.strip():
                        raise ValueError
                st.session_state.sale_review = payload
                st.session_state.pop("sale_incompatible_lines", None)
                sale_form_error = ""
            except (InvalidOperation, ValueError, TypeError):
                st.session_state.pop("sale_review", None)
                sale_form_error = "Completa cada cantidad, unidad, tarifa y motivo manual antes de revisar el pedido."
    if submitted and submit_action == "Revisar pedido" and sale_form_error:
        st.error(sale_form_error)
    if st.session_state.get("sale_draft_saved_for") == scope:
        st.success("Borrador guardado. Puedes cambiar el cliente, la fecha o los productos; las líneas compatibles se conservarán.")
    reviewed = st.session_state.get("sale_review")
    if reviewed:
        st.subheader("3. Revisa antes de confirmar")
        from decimal import Decimal, InvalidOperation
        try:
            rows, total = [], Decimal(0)
            for product, amount, unit, price, discount in zip(reviewed["product"], reviewed["quantity"], reviewed["unit"], reviewed["unit_price"], reviewed["discount_percent"]):
                if not str(amount).strip():
                    continue
                qty, rate, off = Decimal(amount), Decimal(price), Decimal(discount)
                if not all(value.is_finite() for value in (qty, rate, off)) or qty <= 0 or rate <= 0 or not 0 <= off < 100:
                    raise ValueError
                subtotal = (qty * rate * (1 - off / 100)).quantize(Decimal("0.01"))
                total += subtotal
                rows.append({"Producto": product, "Cantidad": str(qty), "Unidad": unit, "Precio USD": str(rate), "Descuento %": str(off), "Subtotal USD": str(subtotal)})
            if not rows:
                raise ValueError
            table(rows)
            st.write(f"Total propuesto: USD {total:,.2f}")
            st.write(f"Cliente: {reviewed['client']} · entrega solicitada: {reviewed['required_date']}")
            st.write(f"Condiciones: {reviewed['payment_terms']} · observaciones: {reviewed['note']}")
            st.warning("Bajo pedido. Disponibilidad por validar; producción sin autorizar. La fecha solicitada no garantiza entrega.")
            st.caption("La revisión bloquea los campos. Edita el pedido y revísalo nuevamente antes de confirmar.")
            if st.button("Editar pedido"):
                st.session_state.pop("sale_review", None)
                st.rerun()
            if st.button("Solicitar aprobación de excepción" if reviewed["exception_reason"] else "Confirmar venta", type="primary"):
                result = action("order_create", reviewed)
                if result is not None:
                    st.session_state.pop("sale_review", None)
                    st.session_state.sale_token = context("order_create", {"client": client, "required_date": delivery})["submission_token"]
                    st.session_state.last_sale_url = result.get("Location", "")
                    st.rerun()
        except (InvalidOperation, ValueError, TypeError):
            st.error("Completa productos, cantidades y tarifas válidas antes de confirmar.")
            if st.button("Editar pedido", key=f"edit-invalid-review:{scope}"):
                st.session_state.pop("sale_review", None)
                st.rerun()
    if st.session_state.get("last_sale_url"):
        identifier = st.session_state.last_sale_url.strip("/").split("/")[-1]
        st.success(f"4. Pedido #{identifier} registrado. Consulta su avance en Mis pedidos.")
        if st.button("Ver mi pedido"):
            st.session_state.pending_order_target = int(identifier)
            st.session_state.pending_workspace = "Pedidos y producción"
            st.rerun()


def notification_inbox_screen():
    st.title("Notificaciones")
    st.caption("Respuestas administrativas, cambios de entrega y oportunidades revisadas.")
    c = context("notification_inbox")
    groups = (("Novedades operativas", c.get("operational_notifications", [])),
              ("Oportunidades comerciales", c.get("event_notifications", [])))
    for title, rows in groups:
        st.subheader(title)
        if not rows:
            st.info("No hay notificaciones en esta sección.")
            continue
        for item in rows:
            with st.container(border=True):
                st.write(f"**{item.title}**")
                st.write(item.body)
                st.caption(f"{item.created_at:%d/%m/%Y %H:%M}" + (f" · {item.client}" if item.client else ""))
                open_col, read_col = st.columns(2)
                if item.order_id or item.target_url:
                    if open_col.button("Abrir" if item.order_id else "Preparar venta",
                                       key=f"open-notice:{item.pk}", type="primary"):
                        response = action("notification_inbox", {
                            "notification_id": item.pk, "action": "open",
                        })
                        target = response.get("Location", "") if response is not None else ""
                        parsed = urlsplit(target)
                        if parsed.path == "/pedidos/nuevo/":
                            params = parse_qs(parsed.query)
                            try:
                                st.session_state.pending_sale_context = {
                                    "client": params["client"][0], "product": params.get("product", [""])[0],
                                    "required_date": date.fromisoformat(params["required_date"][0]),
                                    "mode": params.get("mode", ["sale"])[0],
                                }
                                st.session_state.pending_workspace = "Productos y pedidos"
                                st.rerun()
                            except (KeyError, ValueError):
                                st.error("La oportunidad cambió. Abre Productos y pedidos para revisar la cartera.")
                        elif parsed.path.startswith("/pedidos/") and parsed.path.rstrip("/").split("/")[-1].isdigit():
                            st.session_state.pending_order_target = int(parsed.path.rstrip("/").split("/")[-1])
                            st.session_state.pending_workspace = "Pedidos y producción"
                            st.rerun()
                if read_col.button("Marcar no leída" if item.read_at else "Marcar leída",
                                   key=f"read-notice:{item.pk}"):
                    if action("notification_inbox", {
                        "notification_id": item.pk,
                        "action": "unread" if item.read_at else "read",
                    }) is not None:
                        st.rerun()


def client_stock_screen():
    st.title("Conteo de existencias del cliente")
    st.caption("Registra lo que el cliente reporta; el conteo no cambia el inventario de Vitali.")
    c = context("client_stock")
    if not c.get("clients"):
        st.info("Administración debe asignarte clientes para registrar conteos.")
        return
    client = st.selectbox("Cliente", c["clients"], key="stock_client")
    c = context("client_stock", {"client": client})
    if not c.get("products"):
        st.info("Este cliente no tiene productos en el catálogo activo.")
        return
    with st.form("client_stock_count"):
        product = st.selectbox("Producto", c["products"])
        available = st.text_input("Existencia contada")
        unit = st.text_input("Unidad")
        location = st.text_input("Ubicación del cliente")
        observed = st.date_input("Fecha del conteo", value=c["today"], disabled=True)
        if st.form_submit_button("Guardar conteo", type="primary"):
            if action("client_stock", {
                "client": client, "product": product, "available": available,
                "unit": unit, "location": location, "observed_on": observed,
            }) is not None:
                st.rerun()
    table([{
        "Producto": row["product"],
        "Conteo por ubicaciones": row["count_status"],
        "Objetivo": (f"{row['target'].target_quantity} {row['target'].unit}"
                     if row["target"] else "Por definir"),
    } for row in c["product_rows"]])
    st.subheader("Registrar movimiento confirmado")
    st.caption("Las compras a Vitali son entradas al cliente; no representan consumo. Registra aquí solo una entrada o salida que el cliente haya confirmado.")
    with st.form("client_stock_movement"):
        movement_product = st.selectbox("Producto del movimiento", c["products"])
        kind = st.selectbox("Tipo de movimiento", ["incoming", "outgoing"],
                            format_func=lambda value: "Entrada al cliente" if value == "incoming" else "Salida del cliente")
        quantity = st.text_input("Cantidad del movimiento")
        unit = st.text_input("Unidad del movimiento")
        location = st.text_input("Ubicación del movimiento")
        effective_date = st.date_input(
            "Fecha efectiva", value=c["min_movement_date"],
            min_value=c["min_movement_date"], max_value=c["max_movement_date"],
        )
        reference = st.text_input("Referencia del movimiento")
        confirmed = st.checkbox("Confirmo que el cliente reportó este movimiento")
        if st.form_submit_button("Guardar movimiento"):
            if action("client_stock", {
                "action": "movement", "client": client, "product": movement_product,
                "kind": kind, "quantity": quantity, "unit": unit,
                "location": location, "effective_date": effective_date,
                "reference": reference, "confirmed": "1" if confirmed else "0",
            }) is not None:
                st.rerun()
    table([{
        "Fecha": row.effective_date, "Producto": row.product,
        "Movimiento": row.get_kind_display(), "Ubicación": row.location,
        "Cantidad": f"{row.quantity} {row.unit}",
    } for row in c["movements"]])
    st.caption("Los movimientos los confirma el vendedor junto con el cliente y solo se usan después del conteo del día.")
    st.subheader("Reposición")
    for row in c["product_rows"]:
        if st.button(f"Revisar {row['product']}", key=f"client-restock:{client}:{row['product']}"):
            st.session_state.pending_sale_context = {
                "client": client, "product": row["product"],
                "required_date": c["today"], "mode": "client_restock",
            }
            st.session_state.pending_workspace = "Productos y pedidos"
            st.rerun()


def planning_screen():
    st.title("Calendario y reposición")
    st.caption("Eventos revisados por administración y planificación sobre conteos físicos por ubicación.")
    c = context("planning")
    st.subheader("Calendario")
    with st.form("seed-calendar"):
        year = st.number_input("Año de candidatos", min_value=c["today"].year - 1,
                               max_value=c["today"].year + 3, value=c["today"].year)
        if st.form_submit_button("Agregar candidatos de asuetos y temporadas"):
            if action("planning", {"action": "seed_calendar", "year": int(year)}) is not None:
                st.rerun()
    with st.expander("Agregar evento local o comercial"):
        with st.form("create-event"):
            title = st.text_input("Nombre")
            key = st.text_input("Clave (opcional)")
            kind = st.selectbox("Tipo", ["holiday", "commercial", "local"],
                                format_func=lambda value: {"holiday": "Asueto", "commercial": "Temporada comercial", "local": "Celebración local"}[value])
            territory = st.text_input("Territorio", value="Nacional")
            start = st.date_input("Inicio", value=c["today"])
            end = st.date_input("Fin", value=c["today"])
            source_url = st.text_input("Fuente oficial o referencia")
            if st.form_submit_button("Guardar candidato"):
                if action("planning", {"action": "create_event", "title": title, "key": key,
                                        "kind": kind, "territory": territory,
                                        "start_date": start, "end_date": end, "source": source_url}) is not None:
                    st.rerun()
    for event in c["events"]:
        with st.container(border=True):
            st.write(f"**{event.title} · {event.get_kind_display()}**")
            st.caption(f"{event.start_date:%d/%m/%Y}–{event.end_date:%d/%m/%Y} · {event.territory} · "
                       f"{'Revisado' if event.reviewed else 'Pendiente de revisión'}")
            with st.form(f"review-event:{event.pk}"):
                source_url = st.text_input("Fuente", value=event.source, key=f"event-source:{event.pk}")
                reviewed = st.selectbox("Revisión", ["0", "1"], index=1 if event.reviewed else 0,
                                        format_func=lambda value: "Revisado" if value == "1" else "Pendiente")
                active = st.selectbox("Avisos", ["0", "1"], index=1 if event.active else 0,
                                      format_func=lambda value: "Activos" if value == "1" else "Inactivos")
                if st.form_submit_button("Guardar evento"):
                    if action("planning", {"action": "review_event", "event_id": event.pk,
                                            "source": source_url, "reviewed": reviewed,
                                            "active": active}) is not None:
                        st.rerun()

    st.subheader("Objetivos de existencias")
    with st.form("restock-target"):
        scope = st.selectbox("Alcance", ["client", "location"],
                             format_func=lambda value: "Cliente" if value == "client" else "Ubicación Vitali")
        client = st.selectbox("Cliente", c["clients"] or [""], disabled=not c["clients"])
        location_id = st.selectbox("ID de ubicación", c["locations"] or [0], disabled=not c["locations"])
        product = st.selectbox("Producto", c["products"] or [""], disabled=not c["products"])
        unit = st.text_input("Unidad")
        target_quantity = st.text_input("Cantidad objetivo")
        if st.form_submit_button("Guardar objetivo"):
            if action("planning", {"action": "save_target", "scope": scope, "client": client,
                                    "location_id": location_id, "product": product, "unit": unit,
                                    "target_quantity": target_quantity}) is not None:
                st.rerun()

    st.subheader("Conteos físicos de Vitali")
    with st.form("location-stock"):
        location_id = st.selectbox("Ubicación", c["locations"] or [0], disabled=not c["locations"], key="count-location-id")
        location_name = st.text_input("Nombre de ubicación", key="count-location-name")
        product = st.selectbox("Producto", c["products"] or [""], disabled=not c["products"], key="count-product")
        unit = st.text_input("Unidad", key="count-unit")
        available = st.text_input("Existencia física", key="count-available")
        if st.form_submit_button("Guardar conteo de hoy"):
            if action("planning", {"action": "save_location_stock", "location_id": location_id,
                                    "location_name": location_name, "product": product, "unit": unit,
                                    "available": available, "observed_on": c["today"]}) is not None:
                st.rerun()

    st.subheader("Entradas confirmadas")
    with st.form("location-incoming"):
        location_id = st.selectbox("Ubicación receptora", c["locations"] or [0], disabled=not c["locations"], key="incoming-location-id")
        location_name = st.text_input("Nombre de ubicación receptora", key="incoming-location-name")
        product = st.selectbox("Producto previsto", c["products"] or [""], disabled=not c["products"], key="incoming-product")
        unit = st.text_input("Unidad prevista", key="incoming-unit")
        quantity = st.text_input("Cantidad prevista", key="incoming-quantity")
        due_date = st.date_input("Fecha prevista", value=c["today"], key="incoming-date")
        reference = st.text_input("Referencia", key="incoming-reference")
        confirmed = st.checkbox("La entrada está confirmada", key="incoming-confirmed")
        st.caption("Registrar una entrada no autoriza fabricación ni compra.")
        if st.form_submit_button("Registrar entrada"):
            if action("planning", {"action": "save_location_incoming", "location_id": location_id,
                                    "location_name": location_name, "product": product, "unit": unit,
                                    "quantity": quantity, "due_date": due_date, "reference": reference,
                                    "confirmed": "1" if confirmed else "0"}) is not None:
                st.rerun()
    st.subheader("Compromisos importados de Vitali")
    st.caption("Revisa cantidad, unidad, fecha y referencia antes de confirmar. Los duplicados deben enlazarse al pedido original; los ambiguos quedan fuera del cálculo.")
    for flow in c["dated_flows"]:
        with st.container(border=True):
            st.write(f"**{flow.client} · {flow.product}**")
            st.caption(f"{flow.get_kind_display()} · {flow.due_date:%d/%m/%Y} · {flow.quantity} {flow.unit} · "
                       f"Ref. {flow.source_reference or 'pendiente'} · {flow.get_reconciliation_display()} · "
                       f"{'Carga activa' if flow.batch_id else 'Registro administrativo'}")
            flow_clients = sorted(set(c["clients"]) | {flow.client})
            flow_products = sorted(set(c["products"]) | {flow.product})
            with st.form(f"confirm-dated-flow:{flow.pk}"):
                client = st.selectbox("Cliente", flow_clients, index=flow_clients.index(flow.client), key=f"flow-client:{flow.pk}")
                product = st.selectbox("Producto", flow_products, index=flow_products.index(flow.product), key=f"flow-product:{flow.pk}")
                kind = st.selectbox("Tipo de flujo", ["customer_commitment", "incoming"],
                                    index=0 if flow.kind == "customer_commitment" else 1,
                                    format_func=lambda value: "Compromiso de cliente" if value == "customer_commitment" else "Entrada a Vitali",
                                    key=f"flow-kind:{flow.pk}")
                quantity = st.text_input("Cantidad", value=str(flow.quantity), key=f"flow-quantity:{flow.pk}")
                unit = st.text_input("Unidad", value=flow.unit, key=f"flow-unit:{flow.pk}")
                due_date = st.date_input("Fecha", value=flow.due_date, key=f"flow-date:{flow.pk}")
                date_semantics = st.selectbox("Significado de fecha", ["requested", "committed", "expected_incoming"],
                                              index=["requested", "committed", "expected_incoming"].index(flow.date_semantics),
                                              format_func=lambda value: {"requested": "Entrega solicitada", "committed": "Entrega comprometida", "expected_incoming": "Recepción prevista"}[value],
                                              key=f"flow-semantics:{flow.pk}")
                reference = st.text_input("Referencia de origen", value=flow.source_reference, key=f"flow-reference:{flow.pk}")
                reconciliation = st.selectbox("Conciliación", ["pending", "unique", "duplicate", "ambiguous"],
                                              index=["pending", "unique", "duplicate", "ambiguous"].index(flow.reconciliation),
                                              format_func=lambda value: {"pending": "Pendiente", "unique": "Independiente", "duplicate": "Duplicado de pedido", "ambiguous": "Ambiguo"}[value],
                                              key=f"flow-reconciliation:{flow.pk}")
                candidate_lines = flow.candidate_order_lines
                line_options = [None, *candidate_lines]
                related_order_line = st.selectbox("Línea existente si es duplicado", line_options,
                                                  index=next((i + 1 for i, line in enumerate(candidate_lines) if line.pk == flow.related_order_line_id), 0),
                                                  format_func=lambda line: "Sin vínculo" if line is None else f"Pedido #{line.order_id} · {line.requested_qty} {line.unit}",
                                                  key=f"flow-line:{flow.pk}")
                confirmed = st.checkbox("Usar este flujo en planificación", value=flow.confirmed,
                                        key=f"flow-confirmed:{flow.pk}")
                if st.form_submit_button("Guardar y revisar"):
                    if action("planning", {"action": "confirm_dated_flow", "flow_id": flow.pk,
                                            "client": client, "product": product, "kind": kind,
                                            "quantity": quantity, "unit": unit, "due_date": due_date,
                                            "date_semantics": date_semantics, "source_reference": reference,
                                            "reconciliation": reconciliation,
                                            "related_order_line": related_order_line.pk if related_order_line else "",
                                            "confirmed": "1" if confirmed else "0"}) is not None:
                        st.rerun()
    st.subheader("Movimientos de inventario del cliente")
    st.caption("Los movimientos confirmados por el vendedor no afectan la reposición hasta ser conciliados. Los del día del conteo quedan manuales por falta de hora.")
    for movement in c["client_movements"]:
        with st.container(border=True):
            st.write(f"**{movement.client} · {movement.product} · {movement.location}**")
            st.caption(f"{movement.get_kind_display()} · {movement.quantity} {movement.unit} · {movement.effective_date:%d/%m/%Y} · Ref. {movement.reference or 'sin referencia'}")
            with st.form(f"review-client-movement:{movement.pk}"):
                reconciliation = st.selectbox("Conciliación del movimiento", ["pending", "unique", "duplicate", "ambiguous"],
                                              index=["pending", "unique", "duplicate", "ambiguous"].index(movement.reconciliation),
                                              format_func=lambda value: {"pending": "Pendiente", "unique": "Independiente", "duplicate": "Duplicado de pedido", "ambiguous": "Ambiguo"}[value],
                                              key=f"movement-reconciliation:{movement.pk}")
                candidate_lines = movement.candidate_order_lines
                line_options = [None, *candidate_lines]
                related_order_line = st.selectbox("Línea original si es duplicado", line_options,
                                                  index=next((i + 1 for i, line in enumerate(candidate_lines) if line.pk == movement.related_order_line_id), 0),
                                                  format_func=lambda line: "Sin vínculo" if line is None else f"Pedido #{line.order_id} · {line.requested_qty} {line.unit}",
                                                  key=f"movement-line:{movement.pk}")
                if st.form_submit_button("Guardar conciliación"):
                    if action("planning", {"action": "review_client_movement", "movement_id": movement.pk,
                                             "reconciliation": reconciliation,
                                             "related_order_line": related_order_line.pk if related_order_line else ""}) is not None:
                        st.rerun()
    st.subheader("Necesidad para los próximos siete días")
    st.caption("Se suman compromisos únicos y entradas confirmadas; un pronóstico compatible representa la demanda total del período. El objetivo es la reserva al cierre. No se autoriza producción.")
    table([{"Ubicación": row["location"], "Producto": row["product"],
            "Conteo físico": f"{row['current']} {row['unit']}" if row["current"] is not None else "Sin conteo de hoy",
            "Demanda": f"{row['demand']} {row['unit']}",
            "Entradas": f"{row['incoming']} {row['unit']}",
            "Necesidad": f"{row['need']} {row['unit']}" if row["need"] is not None else "Revisión manual",
            "Estado": "Calculada" if row["suggested"] is not None else "Revisión manual",
            "Explicación": row["explanation"]} for row in c["restock_plan"]])


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
            st.caption(f"Procedencia: {p['provenance']} · Fecha: {p['date_semantics']} · "
                       + ("Origen confirmado por administración" if p["verified"] else "Referencia, pendiente de confirmar"))
            for note in p["notes"]:
                st.caption(note)
        with st.form("sales_import"):
            upload = st.file_uploader("Excel de ventas completo", type="xlsx")
            provenance = st.selectbox("Procedencia", ["real", "simulated"],
                                      format_func=lambda value: "Ventas reales" if value == "real" else "Datos simulados")
            date_semantics = st.selectbox("Significado de la fecha", ["sale_order", "delivery"],
                                          format_func=lambda value: "Fecha de venta o pedido" if value == "sale_order" else "Fecha de entrega realizada")
            verified = st.checkbox("El archivo contiene ventas reales verificadas")
            origin = st.checkbox("Confirmé el origen del archivo")
            full = st.checkbox("Este archivo contiene el histórico completo y reemplaza la carga anterior")
            if st.form_submit_button("Cargar ventas", type="primary"):
                files = {"sales_file": SimpleUploadedFile(upload.name, upload.getvalue())} if upload else {}
                if action("sales_upload", {"verified": verified, "confirmed_origin": origin,
                                            "confirmed_full_snapshot": full,
                                            "provenance": provenance, "date_semantics": date_semantics}, files) is not None:
                    st.rerun()
        table([{"Archivo": d.filename, "Desde": d.first_date, "Hasta": d.last_date,
                "Filas": d.row_count, "Procedencia": d.get_provenance_display(),
                "Significado de fecha": d.get_date_semantics_display(), "Verificado": d.verified,
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
                price = st.text_input("Precio USD", value=str(line["unit_price"]), disabled=not user.is_staff)
                discount = st.text_input("Descuento %", value=str(line["discount_percent"]), disabled=not user.is_staff)
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
    st.caption("Cantidades y avance de tu pedido.")
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
        st.warning(operation["error"] if user.is_staff and settings.SMARTORDER_MAINTENANCE_UI else
                   "El seguimiento está pendiente de actualizar. El pedido se conserva.")
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
    st.caption("La producción requiere autorización administrativa independiente de la venta.")
    for action_name, label in (("sync", "Sincronizar venta con Odoo"), ("refresh", "Actualizar avance de Odoo")):
        if settings.SMARTORDER_MAINTENANCE_UI and operation.get("can_" + action_name) and st.button(label, key=f"odoo:{detail['order'].pk}:{action_name}"):
            if action("order_odoo", {"action": action_name}, order_id=detail["order"].pk) is not None:
                st.rerun()
    for action_name, label, confirmation in (
        ("authorize", "Enviar cantidades autorizadas a fabricar", "Revisé las cantidades aprobadas y autorizo su envío a fabricación."),
        ("cancel", "Cancelar operación", "Revisé la venta y sus operaciones antes de cancelar la operación."),
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
    options = [o.pk for o in c["orders"]]
    target = st.session_state.pop("pending_order_target", None)
    if target in options:
        st.session_state["order_to_consult"] = target
    order_id = st.selectbox("Pedido para consultar", options, key="order_to_consult")
    detail = context("order_detail", order_id=order_id)
    commercial_panel(detail)
    order = detail["order"]
    st.subheader(f"Pedido {order.pk} · {order.client}")
    st.caption(f"Entrega {order.required_date:%d/%m/%Y} · Vendedor {order.seller.username} · Creado {timezone.localtime(order.created_at):%d/%m/%Y %H:%M}")
    if order.note:
        st.write(order.note)
    sale_changes(detail)
    odoo_order_panel(detail)
    if not user.is_staff:
        st.success("Venta confirmada" if order.sale_confirmed else "Excepción comercial pendiente")
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
    st.success("Venta confirmada con condiciones registradas" if order.sale_confirmed else "Excepción comercial pendiente")
    st.write(f"Estado: {order.get_status_display()}")
    st.write(f"Condiciones comerciales: {order.payment_terms or 'Sin especificar'}")
    st.caption(f"Ventana de demanda: {order.required_date:%d/%m/%Y} — {detail['order_window_end']:%d/%m/%Y}")
    if settings.SMARTORDER_MAINTENANCE_UI:
        st.caption(f"Ventas usadas: {order.dataset.filename} · Inventario al crear: {order.inventory_batch.filename if order.inventory_batch else 'Sin inventario'}")
    if settings.SMARTORDER_MAINTENANCE_UI and not order.dataset.verified:
        st.warning("Las cantidades se propusieron manualmente con ventas históricas de referencia. Revisa la explicación de cada producto.")
    if not detail["sources_current"]:
        st.warning("La disponibilidad cambió. Revisa las cantidades antes de autorizar producción.")
        if settings.SMARTORDER_MAINTENANCE_UI:
            st.caption(f"Ventas actuales: {detail['current_sales'].filename if detail['current_sales'] else 'Sin archivo'} · Inventario actual: {detail['current_inventory'].filename if detail['current_inventory'] else 'Sin archivo'}")
        if user.is_staff and order.status != "exported":
            checked = st.checkbox("Revisé ventas e inventario vigentes para este pedido")
            if st.button("Confirmar revisión de disponibilidad", disabled=not checked):
                if action("order_revalidate", {"confirm_revalidation": "1"}, order_id=order_id) is not None:
                    st.rerun()
    for line in detail["lines"]:
        with st.container(border=True):
            st.write(f"**{line.product} · {line.get_status_display()}**")
            st.write(f"Solicitado: {line.requested_qty} {line.unit} · Aprobado: {line.approved_qty if line.approved_qty is not None else 'Pendiente'}")
            st.write(f"Precio unitario: USD {line.unit_price} · Descuento: {line.discount_percent} %")
            st.write(f"Compromiso futuro según inventario al confirmar: {line.future_quantity if line.future_quantity is not None else 'Por confirmar'} {line.unit}")
            st.caption("La existencia observada no constituye una reserva ni confirma entrega.")
            if settings.SMARTORDER_MAINTENANCE_UI:
                st.write(f"Base: {line.method or 'Manual'}")
                if line.reference_qty is not None:
                    st.write(f"Cantidad en período comparable: {line.reference_qty} {line.unit}")
            if line.suggested_qty is not None:
                st.write(f"Sugerencia: {line.suggested_qty} {line.unit}")
            st.caption(f"Motivo del vendedor: {line.reason or 'Cantidad sugerida aceptada'}")
            if line.review_note:
                st.write(f"Revisión: {line.review_note}")
            if user.is_staff and line.status == OrderLine.Status.PENDING and detail["sources_current"] and order.sale_confirmed:
                with st.form(f"review:{line.pk}"):
                    qty = st.text_input("Cantidad aprobada", value=str(line.requested_qty))
                    note = st.text_input("Motivo del ajuste o rechazo")
                    approve = st.form_submit_button("Autorizar producción", type="primary")
                    reject = st.form_submit_button("No autorizar producción")
                    if approve or reject:
                        if action("order_review", {"line_id": line.pk, "decision": "approve" if approve else "reject",
                                                   "approved_quantity": qty, "review_note": note}, order_id=order_id) is not None:
                            st.rerun()
    if user.is_staff and settings.SMARTORDER_MAINTENANCE_UI:
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


def commercial_panel(detail):
    order = detail["order"]
    st.subheader("Confirmación comercial y entrega")
    st.write("Pedido cancelado" if order.status == "cancelled" else "Pedido confirmado" if order.sale_confirmed else "Excepción comercial pendiente")
    st.write(f"Entrega solicitada: {order.required_date} · compromiso administrativo: {order.committed_date or 'Pendiente'}")
    if order.exception_reason:
        st.write(f"Solicitud: {order.exception_reason}")
    if order.commercial_review_note:
        st.write(f"Revisión: {order.commercial_review_note}")
    if not user.is_staff or order.status == "cancelled":
        return
    if not order.sale_confirmed and order.exception_reason:
        with st.form(f"commercial:{order.pk}"):
            note = st.text_input("Motivo de decisión comercial", max_chars=2000)
            approve = st.form_submit_button("Aprobar precios solicitados y confirmar venta")
            reject = st.form_submit_button("Rechazar excepción")
            if approve or reject:
                if action("commercial_review", {"decision": "approve" if approve else "reject", "review_note": note}, order_id=order.pk) is not None:
                    st.rerun()
    if order.sale_confirmed:
        with st.form(f"commitment:{order.pk}"):
            day = st.date_input("Fecha comprometida después de revisar capacidad", value=order.committed_date or order.required_date)
            if st.form_submit_button("Guardar compromiso de entrega"):
                if action("order_commitment", {"committed_date": day}, order_id=order.pk) is not None:
                    st.rerun()


def tariffs_screen():
    st.title("Clientes y tarifas")
    c = context("tariffs")
    st.caption("Los pedidos existentes conservan su precio. Solo administración define tarifas.")
    if c["clients"] and c["products"]:
        with st.form("tariff"):
            row1 = st.columns(3)
            client = row1[0].selectbox("Cliente", c["clients"])
            product = row1[1].selectbox("Producto", c["products"])
            unit = row1[2].selectbox("Unidad", ["kg", "unid"])
            row2 = st.columns(3)
            price = row2[0].text_input("Precio USD")
            discount = row2[1].text_input("Descuento %", value="0")
            submitted = row2[2].form_submit_button("Guardar tarifa", type="primary", use_container_width=True)
            if submitted:
                if action("tariffs", {"client": client, "product": product, "unit": unit, "unit_price": price, "discount_percent": discount}) is not None:
                    st.rerun()
    table([{"Cliente": row.client, "Producto": row.product, "Unidad": row.unit, "Precio USD": str(row.unit_price), "Descuento %": str(row.discount_percent)} for row in c["tariffs"]])
    st.subheader("Excepciones pendientes")
    table([{"Pedido": row.pk, "Cliente": row.client, "Motivo": row.exception_reason} for row in c["exceptions"]])
    st.caption("Revisa cada excepción en Pedidos y producción.")


def source_center_screen():
    st.title("Fuentes de datos")
    c = context("source_center")
    health = c["health"]
    st.caption(f"Histórico conservado: {c['dataset'].filename if c['dataset'] else 'Sin fuente'} · {'Verificado' if c['dataset'] and c['dataset'].verified else 'Referencia no verificada'}")
    st.write(f"Última sincronización correcta: {health['last_success'] or 'Sin datos'}")
    st.write(f"Corte: {health['observed_at'] or 'Sin datos'}")
    st.warning("Desactualizado o sin lectura automática") if health["stale"] else st.success("Lectura reciente")
    st.caption("Frecuencia: cada 5 minutos mientras el programador esté activo; vencimiento: 15 minutos.")
    if health["error"]:
        st.error(health["error"])
    if st.button("Sincronizar ahora", type="primary"):
        if action("source_center", {}) is not None:
            st.rerun()
    table([{"Inicio": row.started_at, "Estado": row.status, "Filas": row.rows, "Rechazadas": row.rejected, "Detalle": row.error, "Pedidos pendientes": str(row.details.get("pending_orders", []))} for row in c["runs"]])
    if c["dataset"]:
        dataset = c["dataset"]
        st.subheader("Cobertura histórica")
        st.caption(f"Procedencia: {dataset.get_provenance_display()} · Fechas: {dataset.get_date_semantics_display()}")
        st.caption("Solo la cobertura completa permite interpretar un día sin fila como cero; las unidades se confirman por intervalo.")
        if c["overlap_pending"]:
            st.warning(f"Hay {len(c['overlap_rows'])} línea(s) operativa(s) dentro del intervalo importado. Resuelve cada una antes de habilitar cantidades.")
        if c["coverage_rows"]:
            by_id = {row.pk: row for row in c["coverage_rows"]}
            selected_id = st.selectbox(
                "Serie e intervalo", options=list(by_id),
                format_func=lambda key: f"{by_id[key].client} · {by_id[key].product} · {by_id[key].unit or 'unidad pendiente'} · {by_id[key].start_date}–{by_id[key].end_date}",
            )
            item = by_id[selected_id]
            with st.form("coverage_review"):
                unit = st.text_input("Unidad histórica confirmada", value=item.unit, max_chars=50)
                status = st.selectbox("Cobertura", ["unknown", "complete", "no_series"],
                                      index=["unknown", "complete", "no_series"].index(item.status),
                                      format_func=lambda value: {"unknown": "Desconocida", "complete": "Completa", "no_series": "Serie todavía inexistente"}[value])
                start = st.date_input("Desde", value=item.start_date, min_value=dataset.first_date, max_value=c["coverage_end_limit"])
                end = st.date_input("Hasta", value=min(item.end_date, c["coverage_end_limit"]), min_value=dataset.first_date, max_value=c["coverage_end_limit"])
                if st.form_submit_button("Guardar revisión", type="primary"):
                    response = action("source_center", {
                        "action": "review_coverage", "dataset_id": dataset.pk,
                        "coverage_id": item.pk, "client": item.client, "product": item.product,
                        "unit": unit, "status": status, "start_date": start.isoformat(),
                        "end_date": end.isoformat(),
                    })
                    if response is not None:
                        st.rerun()
        with st.expander("Agregar otro intervalo o una serie todavía inexistente"):
            if c["coverage_clients"] and c["coverage_products"]:
                with st.form("coverage_add"):
                    client = st.selectbox("Cliente", c["coverage_clients"])
                    product = st.selectbox("Producto", c["coverage_products"])
                    unit = st.text_input("Unidad histórica confirmada", max_chars=50)
                    status = st.selectbox("Estado del intervalo", ["unknown", "complete", "no_series"],
                                          format_func=lambda value: {"unknown": "Desconocida", "complete": "Completa", "no_series": "Serie todavía inexistente"}[value])
                    start = st.date_input("Inicio del intervalo", value=dataset.first_date, min_value=dataset.first_date, max_value=c["coverage_end_limit"])
                    end = st.date_input("Fin del intervalo", value=min(dataset.last_date, c["coverage_end_limit"]), min_value=dataset.first_date, max_value=c["coverage_end_limit"])
                    if st.form_submit_button("Guardar intervalo"):
                        response = action("source_center", {
                            "action": "create_coverage", "dataset_id": dataset.pk,
                            "client": client, "product": product, "unit": unit,
                            "status": status, "start_date": start.isoformat(),
                            "end_date": end.isoformat(),
                        })
                        if response is not None:
                            st.rerun()
        for overlap in c["overlap_rows"]:
            order, line, candidates = overlap["order"], overlap["line"], list(overlap["candidates"])
            st.markdown(f"**Pedido {order.pk} · {order.client} · {line.product}**  \n{order.sale_confirmed_at:%d/%m/%Y} · {line.requested_qty} {line.unit or 'unidad pendiente'}")
            with st.form(f"operational_reconciliation_{line.pk}"):
                decision = st.selectbox(
                    "Decisión administrativa",
                    ["duplicate", "independent"] if candidates else ["independent"],
                    format_func=lambda value: {
                        "duplicate": "Representado en el archivo",
                        "independent": "Venta operativa independiente",
                    }[value],
                )
                imported_line_id = None
                if candidates:
                    imported_line_id = st.selectbox(
                        "Línea importada",
                        [candidate.pk for candidate in candidates],
                        format_func=lambda pk: next(
                            f"#{candidate.pk} · {candidate.quantity} {candidate.unit}"
                            for candidate in candidates if candidate.pk == pk
                        ),
                    )
                else:
                    st.caption("No hay línea compatible por cliente, producto, fecha, cantidad y unidad; solo puede registrarse como independiente.")
                if st.form_submit_button("Guardar conciliación"):
                    response = action("source_center", {
                        "action": "review_operational_sale", "dataset_id": dataset.pk,
                        "order_line_id": line.pk, "decision": decision,
                        "imported_line_id": imported_line_id or "",
                    })
                    if response is not None:
                        st.rerun()
    st.caption("Ubicaciones compartidas: cuotas iguales de planificación, sin reservas. Configura equivalencias y ubicaciones en Conexión y catálogos de Odoo.")
    with st.expander("Histórico y contingencias mediante Excel"):
        uploads()


def pilot_dashboard_screen():
    st.title("Control administrativo")
    c = context("pilot_dashboard", st.session_state.get("pilot_filters"))
    with st.form("pilot_period"):
        start = st.date_input("Desde", value=c["from_date"])
        end = st.date_input("Hasta", value=c["to_date"])
        if st.form_submit_button("Aplicar período"):
            if start > end:
                st.error("Seleccione un período válido.")
            else:
                st.session_state.pilot_filters = {"from_date": start, "to_date": end}
                st.rerun()
    st.metric("Venta confirmada USD", f"{c['confirmed_revenue']:,.2f}")
    st.caption("Después de descuentos; excluye cancelaciones y excepciones pendientes. No representa facturas ni cobros.")
    st.metric("Puntualidad", f"{c['punctuality']:.1f} %" if c["punctuality"] is not None else "Sin datos suficientes")
    st.caption(f"{c['on_time_orders']}/{c['due_orders']} pedidos vencidos con compromiso administrativo; {c['unknown_punctuality']} sin seguimiento suficiente.")
    st.subheader("Acciones pendientes")
    table([{"Pedido": row["order_id"], "Cliente": row["client"], "Acción": row["reason"]} for row in c["actions"]])
    if c["actions"]:
        pending = list(dict.fromkeys(row["order_id"] for row in c["actions"]))
        selected = st.selectbox("Pedido pendiente", pending)
        if st.button("Abrir pedido pendiente"):
            st.session_state.pending_order_target = selected
            st.session_state.pending_workspace = "Pedidos y producción"
            st.rerun()
    st.subheader("Cantidades por unidad")
    table([{label: (f"{row[key]:.1f}" if key == "fulfillment" else str(row[key])) if row[key] is not None else "Sin datos" for key, label in (("unit", "Unidad"), ("sold", "Vendido"), ("net", "Entrega neta"), ("outstanding", "Pendiente"), ("fulfillment", "Cumplimiento %"), ("pending_production", "Por revisar producción"), ("approved", "Autorizado"), ("produced", "Producido"))} for row in c["unit_totals"]])
    st.caption("Entrega neta = salidas menos devoluciones. Cumplimiento = entrega neta / vendido. Unidades separadas. Un seguimiento pendiente impide presentar resultados completos.")
    if c["health"]["stale"]:
        st.warning("Disponibilidad pendiente de validar. Los pedidos pueden registrarse bajo pedido.")


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
        location = st.text_input("Ubicación Odoo aprobada", help="Identificador positivo. Ubicaciones compartidas: cuotas iguales de planificación, sin reservas.")
        if st.form_submit_button("Guardar relación de cliente", type="primary"):
            if action("odoo_settings", {"action": "customer", "client": client, "code": code, "location_id": location}) is not None:
                st.rerun()
    st.subheader("Productos relacionados")
    table([{"Producto SmartOrder": row["product"], "Código Odoo": row["sku"],
            "Unidad de fuente": row["source_unit"] or "Sin unidad en fuente",
            "Unidad Odoo": row["unit"]} for row in c["products"]])
    st.subheader("Clientes relacionados")
    table([{"Cliente SmartOrder": row["client"], "Código Odoo": row["code"], "Ubicación": row.get("location_id")} for row in c["customers"]])


def operational_reports_screen():
    st.title("Resultados operativos")
    st.caption("Cumplimiento y cantidades reales registradas por venta y producto.")
    c = context("operational_reports", st.session_state.get("ops_report_filters"))
    if c.get("notice") and settings.SMARTORDER_MAINTENANCE_UI:
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
            "Entrega solicitada": row["required_date"].strftime("%d/%m/%Y"),
            **({"A tiempo": "Por confirmar" if row["on_time"] is None else "Sí" if row["on_time"] else "No", "Margen simulado": row["margin"] if row["margin"] is not None else "Por confirmar"} if settings.SMARTORDER_MAINTENANCE_UI else {}),
            "Actualizado": timezone.localtime(row["last_synced"]).strftime("%d/%m/%Y %H:%M") if row["last_synced"] else "Pendiente"} for row in c["rows"]])
    st.subheader("Sugerencia y ejecución de fabricación")
    st.caption("Comparación de reposición/fabricación por producto y unidad. La diferencia es producido menos sugerido; no mide error de demanda ni beneficio económico.")
    table([{"Venta": row["order_id"], "Producto": row["product"], "Unidad": row["unit"],
            **{label: str(row.get(key)) if row.get(key) is not None else "Por confirmar"
               for key, label in (("suggested", "Sugerido"), ("approved", "Autorizado"),
                                  ("produced", "Producido"), ("production_difference", "Diferencia de producción"))},
            "Método": row.get("method") or "Manual / sin sugerencia",
            "Venta creada": timezone.localtime(row["created_at"]).strftime("%d/%m/%Y %H:%M") if row.get("created_at") else "Por confirmar",
            **({"Fuente conservada de ventas": row.get("source", "Por confirmar"), "Fuente conservada de inventario": row.get("inventory_source") or "Por confirmar"} if settings.SMARTORDER_MAINTENANCE_UI else {})} for row in c["rows"]])
    st.subheader("Ajustes de inventario")
    st.caption("Diferencias entre las cantidades revisadas y las anteriores. Un ajuste requiere aprobación administrativa.")
    table([{"Unidad": row["unit"], "Correcciones": row["count"],
            "Diferencia acumulada": str(row["difference"]) if row["difference"] is not None else "Por confirmar"}
           for row in c.get("correction_totals", [])])
    table([{"Cliente": row["client"], "Producto": row["product"], "Unidad": row["unit"],
            **{label: str(row[key]) if row[key] is not None else "Por confirmar"
               for key, label in (("previous", "Lectura anterior"), ("observed", "Lectura aprobada"), ("difference", "Diferencia"))},
            "Fecha observada": row["observed_on"].strftime("%d/%m/%Y"),
            "Aprobada": timezone.localtime(row["approved_at"]).strftime("%d/%m/%Y %H:%M"),
            "Administrador": row["approved_by"], "Motivo": row["reason"], **({"Fuente": row["source"]} if settings.SMARTORDER_MAINTENANCE_UI else {})}
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
    st.caption("La solicitud del vendedor requiere revisión administrativa.")
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
        password = st.text_input("Contraseña", type="password", help="Al menos 8 caracteres para una cuenta nueva.")
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
    notification_summary = context("notification_inbox")
    notification_label = "Notificaciones"
    if notification_summary.get("unread_notification_count"):
        notification_label += f" · {notification_summary['unread_notification_count']}"
    screens = {("Control administrativo" if user.is_staff else "Mi trabajo"): pilot_dashboard_screen if user.is_staff else dashboard,
               "Productos y pedidos": products, notification_label: notification_inbox_screen,
               "Pedidos y producción": orders, "Correcciones de inventario": corrections}
    if user.is_staff:
        screens.update({"Clientes y tarifas": tariffs_screen, "Usuarios y cartera": accounts,
                        "Calendario y reposición": planning_screen,
                        "Resultados operativos": operational_reports_screen})
        if settings.SMARTORDER_MAINTENANCE_UI:
            screens.update({"Resumen de ventas": dashboard, "Fuentes de datos": source_center_screen, "Histórico y contingencias": uploads, "Conexión y catálogos de Odoo": odoo_settings_screen})
    else:
        screens["Conteo del cliente"] = client_stock_screen
    if st.session_state.get("pending_workspace"):
        st.session_state.workspace = st.session_state.pop("pending_workspace")
    if st.session_state.get("workspace") not in screens:
        st.session_state.workspace = next(iter(screens))
    page = st.radio("Espacios de trabajo", list(screens), key="workspace")
    if st.button("Cerrar sesión"):
        sign_out(st.session_state.session_key)
        st.session_state.clear()
        st.rerun()
    st.caption("El vendedor confirma ventas. Administración autoriza producción.")

st.html('<div class="vitali-ticket"><span>SMARTORDER AI</span><em>Pedidos internos</em></div>')
try:
    screens[page]()
except (PermissionDenied, Http404) as exc:
    st.error(str(exc) or "No tienes permiso para consultar este registro.")
finally:
    close_old_connections()
