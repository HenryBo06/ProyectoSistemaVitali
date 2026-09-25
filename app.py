"""Interfaz local de SmartOrder AI para administración y vendedores."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path
import os
import sqlite3
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st

from smartorder.data import SalesData, load_sales
from smartorder.forecast import DemandForecaster, ForecastReport
from smartorder.orders import OperationalInput, export_approved, suggest_order
from smartorder.storage import Store


ROOT = Path(__file__).resolve().parent
PRIVATE = Path(os.environ.get("SMARTORDER_HOME", ROOT / ".local")).resolve()
DEFAULT_EXCEL = ROOT / "Demo_Ventas_Avicola_2025_IA_Pedidos_v2.xlsx"
TODAY = datetime.now(ZoneInfo("America/El_Salvador")).date()

st.set_page_config(page_title="SmartOrder AI | Vitali", page_icon="📦", layout="wide")
st.markdown("""
<style>
  :root { --ink:#173247; --green:#397657; --paper:#f5f8f5; --line:#d6e2da; }
  .stApp { background:var(--paper); color:var(--ink); }
  h1, h2, h3 { color:var(--ink); letter-spacing:-.025em; }
  h1 { font-family:'Arial Narrow','Segoe UI',sans-serif; font-weight:800; }
  [data-testid="stMetric"] { background:#fff; border:1px solid var(--line);
    border-radius:12px; padding:12px 16px; }
  [data-testid="stSidebar"] { background:#eaf1eb; }
  .evidence { background:#173247; color:#fff; border-radius:12px;
    padding:13px 18px; margin:2px 0 22px; font-size:.92rem; }
  .evidence strong { color:#c2e7c5; }
  .section-note { color:#486170; font-size:.94rem; }
  .stButton > button[kind="primary"] { background:#397657; border-color:#397657; }
</style>
""", unsafe_allow_html=True)


@st.cache_data(show_spinner=False)
def parsed(raw: bytes) -> SalesData:
    return load_sales(raw)


@st.cache_data(show_spinner="Evaluando el modelo y preparando recomendaciones…")
def forecasted(raw: bytes, as_of: date) -> ForecastReport:
    return DemandForecaster().run(load_sales(raw), as_of)


def header(role: str, data: SalesData | None = None) -> None:
    st.title("SmartOrder AI")
    st.caption("Vitali Alimentos · Pedidos sugeridos basados en ventas observadas")
    if data is not None:
        st.markdown(
            f'<div class="evidence"><strong>Fuente:</strong> {len(data.rows):,} ventas '
            f'· <strong>Cobertura:</strong> {data.first_date:%d/%m/%Y}–{data.last_date:%d/%m/%Y} '
            f'· <strong>Vista:</strong> {role}</div>', unsafe_allow_html=True,
        )


def setup_or_login(store: Store) -> dict:
    if not store.has_users():
        header("Configuración inicial")
        st.info("Cree la cuenta administradora inicial. La aplicación guarda datos y cuentas solo en este equipo.")
        with st.form("bootstrap"):
            username = st.text_input("Usuario administrador")
            password = st.text_input("Contraseña (12 caracteres mínimo)", type="password")
            repeat = st.text_input("Repita la contraseña", type="password")
            submitted = st.form_submit_button("Crear administrador", type="primary")
        if submitted:
            if password != repeat:
                st.error("Las contraseñas no coinciden.")
            else:
                try:
                    store.bootstrap_admin(username, password)
                    st.success("Administrador creado. Inicie sesión.")
                    st.rerun()
                except (ValueError, PermissionError) as exc:
                    st.error(str(exc))
        st.stop()

    user_id = st.session_state.get("user_id")
    user = store.user(user_id) if isinstance(user_id, int) else None
    if user is not None:
        return user
    header("Acceso")
    with st.form("login"):
        username = st.text_input("Usuario")
        password = st.text_input("Contraseña", type="password")
        submitted = st.form_submit_button("Entrar", type="primary")
    if submitted:
        candidate = store.authenticate(username, password)
        if candidate:
            st.session_state["user_id"] = candidate["id"]
            st.rerun()
        st.error("Credenciales inválidas o cuenta temporalmente bloqueada.")
    st.stop()


def source_bytes(store: Store) -> tuple[bytes | None, str]:
    active = store.active_dataset()
    if active:
        path = Path(active["path"])
        if not path.is_file():
            st.error("No se encuentra el Excel activo. Un administrador debe cargarlo de nuevo.")
            return None, active["filename"]
        return path.read_bytes(), active["filename"]
    if DEFAULT_EXCEL.is_file():
        return DEFAULT_EXCEL.read_bytes(), DEFAULT_EXCEL.name
    return None, "Sin histórico"


def metrics(data: SalesData) -> None:
    cells = st.columns(4)
    cells[0].metric("Ventas registradas", f"{len(data.rows):,}")
    cells[1].metric("Clientes", len(data.clients))
    cells[2].metric("Productos", data.rows["Producto"].nunique())
    cells[3].metric("Ventas USD", f"${data.rows['Monto_Venta_USD'].sum():,.0f}")


def model_metrics(report: ForecastReport) -> None:
    st.subheader("Precisión medida")
    columns = st.columns(4)
    columns[0].metric("WAPE · XGBoost", "n. d." if report.xgboost_wape is None else f"{report.xgboost_wape:.1f}%")
    columns[1].metric("WAPE · promedio", "n. d." if report.baseline_wape is None else f"{report.baseline_wape:.1f}%")
    columns[2].metric("MAE · XGBoost", "n. d." if report.xgboost_mae is None else f"{report.xgboost_mae:.1f}")
    columns[3].metric("MAE · promedio", "n. d." if report.baseline_mae is None else f"{report.baseline_mae:.1f}")
    st.caption(
        f"Evaluación con {report.validation_windows} períodos de siete días no superpuestos "
        "del tramo final del histórico. El error mide ventas observadas; no demuestra "
        "reducción de mermas ni desempeño entre años."
    )


def admin_home(store: Store, user: dict, data: SalesData, raw: bytes) -> None:
    st.header("Centro de administración")
    st.write("Cobertura de datos, desempeño del modelo y adopción de recomendaciones.")
    metrics(data)
    monthly = data.rows.assign(Mes=data.rows["Fecha"].dt.to_period("M").astype(str)).groupby(
        "Mes", as_index=False
    )["Monto_Venta_USD"].sum()
    left, right = st.columns([3, 2])
    with left:
        st.subheader("Ventas mensuales")
        st.line_chart(monthly.set_index("Mes")["Monto_Venta_USD"])
    with right:
        st.subheader("Productos con más ventas")
        top = data.rows.groupby("Producto")["Monto_Venta_USD"].sum().nlargest(8)
        st.bar_chart(top)
    st.caption("Los productos se comparan por USD: la columna de cantidades mezcla kg y unidades.")
    decisions = store.decisions(user["id"])
    c1, c2, c3 = st.columns(3)
    c1.metric("Decisiones registradas", len(decisions))
    c2.metric("Vendedores activos", sum(
        u["role"] == "vendedor" and u["active"] for u in store.list_users(user["id"])
    ))
    c3.metric("Último dato", data.last_date.strftime("%d/%m/%Y"))
    if decisions:
        decisions_frame = pd.DataFrame(decisions)
        same = (decisions_frame["aprobado"] - decisions_frame["sugerido"]).abs() < 0.001
        st.metric("Aceptación sin cambios", f"{same.mean() * 100:.1f}%")
        st.subheader("Decisiones por vendedor")
        st.bar_chart(decisions_frame.groupby("usuario").size())
    try:
        model_metrics(forecasted(raw, TODAY))
    except ValueError as exc:
        st.warning(str(exc))


def admin_data(store: Store, user: dict, data: SalesData | None, raw: bytes | None,
               filename: str) -> None:
    st.header("Datos y modelo")
    st.write("Suba un XLSX con las diez columnas de ventas. Cada carga activada sustituye el histórico anterior.")
    if data is not None:
        st.info(f"Activo: {filename} · {len(data.rows):,} registros · "
                f"{data.first_date:%d/%m/%Y} a {data.last_date:%d/%m/%Y}")
        if data.recalculated_amounts:
            st.warning(f"Se calcularon {data.recalculated_amounts} montos sin valor guardado en Excel.")
        st.caption(f"Hojas leídas: {', '.join(data.sheets)} · Huella SHA-256: {data.digest[:16]}…")
    uploaded = st.file_uploader("Cargar histórico de ventas", type=["xlsx"], max_upload_size=20)
    if uploaded is not None:
        try:
            incoming = parsed(uploaded.getvalue())
            st.success(f"Archivo válido: {len(incoming.rows):,} registros, "
                       f"{incoming.first_date:%d/%m/%Y}–{incoming.last_date:%d/%m/%Y}.")
            st.dataframe(incoming.rows.head(8), hide_index=True)
            if st.button("Activar este histórico", type="primary"):
                destination = PRIVATE / "uploads" / f"{incoming.digest}.xlsx"
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(uploaded.getvalue())
                store.activate_dataset(user["id"], incoming.digest, str(destination),
                                       uploaded.name, incoming.first_date.isoformat(),
                                       incoming.last_date.isoformat(), len(incoming.rows))
                st.rerun()
        except (ValueError, OSError) as exc:
            st.error(str(exc))
    if data is not None and raw is not None:
        st.subheader("Calidad y evaluación")
        st.write(f"Pares cliente–producto: {data.rows[['Cliente','Producto']].drop_duplicates().shape[0]}")
        st.write(f"Fechas distintas: {data.rows['Fecha'].nunique()}")
        st.write("Filas repetidas por día, cliente y producto se suman como transacciones distintas.")
        try:
            report = forecasted(raw, TODAY)
            model_metrics(report)
            st.warning(report.model_status) if report.source_end < TODAY - timedelta(days=28) else st.info(report.model_status)
        except ValueError as exc:
            st.warning(str(exc))
    previous = store.datasets(user["id"])
    if previous:
        st.subheader("Cargas anteriores")
        st.dataframe(pd.DataFrame(previous), hide_index=True)


def admin_users(store: Store, user: dict, data: SalesData | None) -> None:
    st.header("Usuarios y permisos")
    users = store.list_users(user["id"])
    st.dataframe(pd.DataFrame(users), hide_index=True)
    with st.form("new_user"):
        st.subheader("Crear cuenta")
        username = st.text_input("Usuario nuevo")
        password = st.text_input("Contraseña inicial (12 caracteres mínimo)", type="password")
        role = st.selectbox("Rol", ["vendedor", "admin"])
        submitted = st.form_submit_button("Crear cuenta", type="primary")
    if submitted:
        try:
            store.create_user(user["id"], username, password, role)
            st.success("Cuenta creada.")
            st.rerun()
        except (ValueError, PermissionError, sqlite3.IntegrityError) as exc:
            # SQLite informa aquí si el nombre de usuario ya existe.
            st.error(str(exc))
    sellers = [u for u in users if u["role"] == "vendedor"]
    if sellers and data is not None:
        st.subheader("Clientes asignados")
        selected = st.selectbox("Vendedor", sellers, format_func=lambda u: u["username"])
        current = store.allowed_clients(selected["id"])
        clients = st.multiselect("Clientes visibles", data.clients, default=[
            client for client in current if client in data.clients
        ])
        if st.button("Guardar asignaciones"):
            store.set_assignments(user["id"], selected["id"], clients)
            st.success("Asignaciones guardadas.")
    if len(users) > 1:
        st.subheader("Administrar cuenta")
        target = st.selectbox("Cuenta", [u for u in users if u["id"] != user["id"]],
                              format_func=lambda u: u["username"])
        active = st.toggle("Cuenta activa", value=bool(target["active"]))
        if st.button("Guardar estado"):
            store.set_user_active(user["id"], target["id"], active)
            st.rerun()
        with st.form("reset_password"):
            fresh = st.text_input("Nueva contraseña", type="password")
            reset = st.form_submit_button("Restablecer contraseña")
        if reset:
            try:
                store.reset_password(user["id"], target["id"], fresh)
                st.success("Contraseña actualizada.")
            except ValueError as exc:
                st.error(str(exc))


def comparable_products(data: SalesData, client: str, as_of: date) -> None:
    previous_year = as_of.year - 1
    selected = data.rows[
        (data.rows["Cliente"] == client)
        & (data.rows["Fecha"].dt.year == previous_year)
        & (data.rows["Fecha"].dt.month == as_of.month)
    ]
    st.subheader(f"Productos más comprados · {as_of:%m}/{previous_year}")
    if selected.empty:
        st.info("No hay ventas registradas en el mismo mes del año anterior.")
        return
    ranked = selected.groupby("Producto", as_index=False).agg(
        Ventas_USD=("Monto_Venta_USD", "sum"),
        Compras=("Producto", "size"),
        Cantidad_en_unidad_fuente=("Cantidad_kg_unid", "sum"),
    ).sort_values("Ventas_USD", ascending=False)
    st.dataframe(ranked, hide_index=True, width="stretch")
    st.caption("El orden usa monto vendido; kg y unidades no se suman entre productos.")


def vendor_home(store: Store, user: dict, data: SalesData) -> None:
    st.header("Mi cartera")
    allowed = [client for client in store.allowed_clients(user["id"]) if client in data.clients]
    if not allowed:
        st.info("Un administrador debe asignarle clientes antes de mostrar recomendaciones.")
        return
    filtered = data.rows[data.rows["Cliente"].isin(allowed)]
    c1, c2, c3 = st.columns(3)
    c1.metric("Clientes asignados", len(allowed))
    c2.metric("Productos con ventas", filtered["Producto"].nunique())
    c3.metric("Ventas registradas USD", f"${filtered['Monto_Venta_USD'].sum():,.0f}")
    client = st.selectbox("Cliente", allowed)
    comparable_products(data, client, TODAY)
    st.caption("La recomendación detallada se encuentra en ‘Recomendaciones’.")


def vendor_recommendations(store: Store, user: dict, data: SalesData, raw: bytes) -> None:
    st.header("Recomendaciones para pedidos")
    allowed = [client for client in store.allowed_clients(user["id"]) if client in data.clients]
    if not allowed:
        st.info("Solicite a un administrador que le asigne clientes.")
        return
    client = st.selectbox("Cliente", allowed)
    first_possible = data.last_date + timedelta(days=1)
    as_of = st.date_input("Fecha de análisis", value=max(TODAY, first_possible),
                          min_value=first_possible,
                          max_value=max(TODAY + timedelta(days=30), first_possible))
    if as_of <= data.last_date:
        st.warning("El análisis debe comenzar después del último dato cargado.")
        return
    try:
        report = forecasted(raw, as_of)
    except ValueError as exc:
        st.warning(f"Todavía no se puede calcular una recomendación: {exc}")
        return
    source_stale = report.source_end < as_of - timedelta(days=28)
    if source_stale:
        st.warning("🟡 Revisión necesaria: " + report.model_status)
    else:
        st.success("🟢 Histórico reciente: " + report.model_status)
    st.caption(f"Horizonte: {as_of:%d/%m/%Y}–{as_of + timedelta(days=6):%d/%m/%Y}.")
    predictions = report.rows[report.rows["Cliente"] == client]
    st.dataframe(predictions.rename(columns={
        "Producto": "Producto", "Pronostico_7d": "Pronóstico · 7 días",
        "Metodo": "Método aplicado", "XGBoost_7d": "Estimación XGBoost",
        "Mismo_periodo_ano_anterior": "Mismos días · año anterior",
        "Promedio_semanal_mismo_mes": "Promedio semanal · mismo mes anterior",
        "Ultimos_28_dias": "Últimos 28 días", "Situacion": "Lectura comercial",
    }).drop(columns=["Cliente"]), hide_index=True, width="stretch")
    product = st.selectbox("Producto para revisar", predictions["Producto"].tolist())
    selected = predictions[predictions["Producto"] == product].iloc[0]
    st.subheader(product)
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Pronóstico · 7 días", f"{selected['Pronostico_7d']:,.1f}")
    c2.metric("Estimación XGBoost", f"{selected['XGBoost_7d']:,.1f}")
    previous = selected["Mismo_periodo_ano_anterior"]
    c3.metric("Mismos días, año anterior", "Sin dato" if pd.isna(previous) else f"{previous:,.1f}")
    recent = selected["Ultimos_28_dias"]
    c4.metric("Ventas · 28 días previos", "Sin dato reciente" if pd.isna(recent) else f"{recent:,.1f}")
    st.caption(f"Método aplicado: {selected['Metodo']}. La estimación XGBoost se muestra para comparación.")
    st.write(selected["Situacion"])
    st.caption("Cantidad en la medida original del Excel. Confirme kg, unidades o docenas antes de pedir.")

    saved = store.operational(user["id"], client, product)
    st.subheader("Datos para calcular el pedido")
    st.write("Ingrese valores observados; el sistema no asigna inventarios ni reglas de Vitali por defecto.")
    with st.form(f"operational_{client}_{product}"):
        unit = st.text_input("Unidad del producto (kg, unidad, docena…)",
                             value=saved["unit"] if saved else "")
        left, middle, right = st.columns(3)
        stock = left.text_input("Inventario disponible", value=saved["stock"] if saved else "")
        pending = middle.text_input("Pedidos pendientes", value=saved["pending"] if saved else "")
        target = right.text_input("Inventario objetivo", value=saved["target_stock"] if saved else "")
        left, middle = st.columns(2)
        minimum = left.text_input("Pedido mínimo", value=saved["minimum"] if saved else "")
        multiple = middle.text_input("Múltiplo de empaque", value=saved["pack_multiple"] if saved else "")
        stock_date_limit = min(TODAY, as_of)
        initial_stock_date = date.fromisoformat(saved["as_of"]) if saved else stock_date_limit
        stock_date = st.date_input("Fecha de observación del inventario",
                                   value=min(initial_stock_date, stock_date_limit),
                                   max_value=stock_date_limit)
        submitted = st.form_submit_button("Guardar datos operativos", type="primary")
    if submitted:
        try:
            operational = OperationalInput.create(
                client, product, unit, stock, pending, target, minimum, multiple, stock_date
            )
            store.save_operational(user["id"], operational)
            st.success("Datos guardados. Ya puede revisar el pedido sugerido.")
            st.rerun()
        except (ValueError, PermissionError) as exc:
            st.error(str(exc))
    if not saved:
        st.error("🔴 Faltan datos operativos. Complete el formulario para calcular un pedido.")
        return

    operational = OperationalInput.create(
        client, product, saved["unit"], saved["stock"], saved["pending"],
        saved["target_stock"], saved["minimum"], saved["pack_multiple"],
        date.fromisoformat(saved["as_of"]),
    )
    suggestion = suggest_order(selected["Pronostico_7d"], operational)
    if operational.as_of > as_of:
        st.error("Actualice la fecha del inventario: es posterior al período pronosticado.")
        return
    if operational.as_of < as_of - timedelta(days=7):
        st.warning("El inventario observado tiene más de siete días respecto al análisis. Verifíquelo antes de aprobar.")
    st.success(f"Pedido sugerido: {suggestion.suggested:g} {operational.unit}")
    st.caption(suggestion.reason)
    with st.form(f"decision_{client}_{product}_{as_of}"):
        approved = st.number_input("Cantidad final aprobada", min_value=0.0,
                                   value=float(suggestion.suggested), step=1.0)
        reason = st.text_area("Motivo del ajuste (obligatorio si cambia la cantidad)")
        acknowledged = (st.checkbox(
            "Revisé que el histórico no contiene ventas recientes para esta fecha"
        ) if source_stale else True)
        confirmed = st.form_submit_button("Aprobar y registrar pedido", type="primary")
    if confirmed:
        if not acknowledged:
            st.error("Confirme la revisión del histórico antes de aprobar.")
            return
        try:
            store.save_decision(
                user["id"], dataset_digest=data.digest, client=client, product=product,
                forecast_date=as_of, forecast=float(suggestion.predicted),
                suggested=float(suggestion.suggested), approved=approved,
                operational=operational, reason=reason,
            )
            st.success("Pedido aprobado y registrado. Puede descargarlo en Historial.")
        except (ValueError, PermissionError) as exc:
            st.error(str(exc))


def history(store: Store, user: dict) -> None:
    st.header("Historial de decisiones")
    rows = store.decisions(user["id"])
    if not rows:
        st.info("Todavía no hay pedidos aprobados.")
        return
    display = pd.DataFrame(rows)
    st.dataframe(display[["fecha_decision", "usuario", "cliente", "producto",
                          "unidad", "fecha_pronostico", "pronostico", "sugerido",
                          "aprobado", "motivo"]], hide_index=True, width="stretch")
    latest = {}
    for row in rows:
        key = (row["usuario"], row["cliente"], row["producto"], row["fecha_pronostico"])
        latest.setdefault(key, row)
    output = export_approved(latest.values())
    st.download_button("Descargar pedidos aprobados (.xlsx)", output,
                       file_name="pedidos_aprobados_vitali.xlsx",
                       mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    st.caption("La descarga contiene la decisión más reciente por vendedor, cliente, producto y fecha.")


def main() -> None:
    store = Store(PRIVATE / "smartorder.sqlite3")
    user = setup_or_login(store)
    st.sidebar.caption(f"{user['username']} · {user['role']}")
    if st.sidebar.button("Cerrar sesión"):
        st.session_state.pop("user_id", None)
        st.rerun()
    navigation = (["Resumen", "Datos y modelo", "Usuarios", "Historial"]
                  if user["role"] == "admin" else
                  ["Mi cartera", "Recomendaciones", "Historial"])
    page = st.sidebar.radio("Navegación", navigation)
    raw, filename = source_bytes(store)
    data = None
    if raw is not None:
        try:
            data = parsed(raw)
        except ValueError as exc:
            st.error(f"No se pudo leer el histórico activo: {exc}")
    header("Administración" if user["role"] == "admin" else "Vendedor", data)
    if page == "Usuarios":
        admin_users(store, user, data)
    elif page == "Datos y modelo":
        admin_data(store, user, data, raw, filename)
    elif page == "Historial":
        history(store, user)
    elif data is None:
        st.info("Un administrador debe cargar un Excel de ventas para comenzar.")
    elif page == "Resumen":
        admin_home(store, user, data, raw)
    elif page == "Mi cartera":
        vendor_home(store, user, data)
    elif page == "Recomendaciones":
        vendor_recommendations(store, user, data, raw)


if __name__ == "__main__":
    main()
