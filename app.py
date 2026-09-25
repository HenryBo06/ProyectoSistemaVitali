"""Interfaz local de SmartOrder AI para administración y vendedores."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from hmac import compare_digest
from pathlib import Path
import os
import sqlite3
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st

from smartorder.data import SalesData, load_sales
from smartorder.demo import DEMO_ADMIN, DEMO_SELLER, seed_local_demo
from smartorder.forecast import DemandForecaster, ForecastReport
from smartorder.orders import OperationalInput, export_production, suggest_order
from smartorder.storage import Store


ROOT = Path(__file__).resolve().parent
DEFAULT_EXCEL = ROOT / "Demo_Ventas_Avicola_2025_IA_Pedidos_v2.xlsx"
TODAY = datetime.now(ZoneInfo("America/El_Salvador")).date()

st.set_page_config(page_title="SmartOrder AI | Vitali", page_icon="📦", layout="wide")


def environment_flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


PUBLIC_DEMO_FORCED = environment_flag("SMARTORDER_PUBLIC_DEMO")
PUBLIC_DEMO = PUBLIC_DEMO_FORCED or st.query_params.get("demo") == "1"
configured_home = os.environ.get("SMARTORDER_HOME")
if PUBLIC_DEMO:
    demo_root = Path(configured_home).resolve() if configured_home else ROOT
    PRIVATE = (demo_root / "public-demo").resolve()
else:
    PRIVATE = Path(configured_home or ROOT / ".local").resolve()
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


def local_demo_mode() -> bool:
    safe_addresses = ("127.0.0.1", "::1")
    return (
        os.environ.get("SMARTORDER_DEMO_MODE") == "1"
        and os.environ.get("SMARTORDER_LOCAL_MODE") == "1"
        and (
            st.get_option("server.address") in safe_addresses
            or os.environ.get("STREAMLIT_SERVER_ADDRESS") in safe_addresses
        )
    )


def demo_mode() -> bool:
    return PUBLIC_DEMO or local_demo_mode()


def public_demo_entry() -> None:
    if demo_mode():
        return
    st.caption("¿Solo quiere probar el sistema con datos de ejemplo?")
    if st.button(
        ":material/science: Probar demo pública", width="content", type="secondary"
    ):
        st.query_params["demo"] = "1"
        st.rerun()


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
        public_demo_entry()
        local_setup = (os.environ.get("SMARTORDER_LOCAL_MODE") == "1"
                       and st.get_option("server.address") in ("127.0.0.1", "::1"))
        setup_code = os.environ.get("SMARTORDER_SETUP_CODE", "")
        if not local_setup and len(setup_code) < 20:
            st.error("Falta el código de instalación para crear el primer administrador.")
            st.info("Si esta aplicación está en Streamlit Community Cloud, abra App settings "
                    "→ Secrets y agregue SMARTORDER_SETUP_CODE como valor privado de al "
                    "menos 20 caracteres. Después recargue esta página. Para uso en su "
                    "propio equipo, iníciela con iniciar_smartorder.cmd.")
            st.stop()
        st.info("Cree la cuenta administradora inicial. Los datos se guardan en el "
                "almacenamiento configurado para esta instalación.")
        with st.form("bootstrap"):
            entered_code = (st.text_input("Código de instalación", type="password")
                            if not local_setup else "")
            username = st.text_input("Usuario administrador")
            password = st.text_input("Contraseña (12 caracteres mínimo)", type="password")
            repeat = st.text_input("Repita la contraseña", type="password")
            submitted = st.form_submit_button("Crear administrador", type="primary")
        if submitted:
            if not local_setup and not compare_digest(entered_code, setup_code):
                st.error("Código de instalación incorrecto.")
            elif password != repeat:
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
    if user is not None and st.session_state.get("session_version") == user["session_version"]:
        return user
    st.session_state.pop("user_id", None)
    st.session_state.pop("session_version", None)
    header("Acceso")
    public_demo_entry()
    if demo_mode():
        st.info(
            "Modo de presentación. Puede recorrer ambos perfiles sin configurar cuentas."
        )
        admin_column, seller_column = st.columns(2)
        if admin_column.button(
            ":material/admin_panel_settings: Entrar como administración",
            width="stretch", type="primary",
        ):
            candidate = store.authenticate(*DEMO_ADMIN)
            if candidate:
                st.session_state["user_id"] = candidate["id"]
                st.session_state["session_version"] = candidate["session_version"]
                st.rerun()
        if seller_column.button(
            ":material/badge: Entrar como vendedor", width="stretch"
        ):
            candidate = store.authenticate(*DEMO_SELLER)
            if candidate:
                st.session_state["user_id"] = candidate["id"]
                st.session_state["session_version"] = candidate["session_version"]
                st.rerun()
        st.caption(
            "La demo usa datos sintéticos y almacenamiento aislado. No contiene información real."
        )
    with st.form("login"):
        username = st.text_input("Usuario")
        password = st.text_input("Contraseña", type="password")
        submitted = st.form_submit_button("Entrar", type="primary")
    if submitted:
        candidate = store.authenticate(username, password)
        if candidate:
            st.session_state["user_id"] = candidate["id"]
            st.session_state["session_version"] = candidate["session_version"]
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


def metrics(rows: pd.DataFrame) -> None:
    cells = st.columns(4)
    cells[0].metric("Ventas del período", f"${rows['Monto_Venta_USD'].sum():,.0f}", border=True)
    cells[1].metric("Registros de venta", f"{len(rows):,}", border=True)
    cells[2].metric("Clientes con ventas", rows["Cliente"].nunique(), border=True)
    cells[3].metric("Productos vendidos", rows["Producto"].nunique(), border=True)


def model_metrics(report: ForecastReport) -> None:
    st.subheader("Errores por producto")
    display = report.metrics_by_product.rename(columns={
        "WAPE_XGBoost": "WAPE XGBoost (%)", "WAPE_promedio": "WAPE promedio (%)",
        "MAE_XGBoost": "MAE XGBoost", "MAE_promedio": "MAE promedio",
        "Metodo_ganador": "Método aplicado", "Mejora_WAPE_pp": "Ventaja XGBoost (pp)",
        "Error_P80_metodo": "Error orientativo P80",
    })
    st.dataframe(display.round(1), hide_index=True, width="stretch")
    st.caption(
        f"Evaluación con {report.validation_windows} períodos de siete días no superpuestos "
        "del tramo final del histórico. WAPE es el error porcentual; MAE conserva la "
        "unidad de cada producto. No se suman kg y unidades ni se demuestra reducción "
        "de mermas o desempeño entre años."
    )


def admin_home(store: Store, user: dict, data: SalesData) -> None:
    st.header("Centro de administración")
    st.write("Ventas del archivo cargado y pedidos internos que requieren atención.")
    period = st.date_input("Período de ventas", value=(data.first_date, data.last_date),
                           min_value=data.first_date, max_value=data.last_date)
    if len(period) != 2:
        st.info("Seleccione fecha inicial y final para ver los indicadores.")
        return
    client = st.selectbox("Cliente", ["Todos"] + data.clients)
    rows = data.rows[data.rows["Fecha"].dt.date.between(period[0], period[1])]
    if client != "Todos":
        rows = rows[rows["Cliente"] == client]
    if rows.empty:
        st.info("No hay ventas en el período y cliente seleccionados.")
    else:
        st.caption(f"Ventas del archivo cargado: {period[0]:%d/%m/%Y}–{period[1]:%d/%m/%Y}.")
        metrics(rows)
        monthly = rows.assign(Mes=rows["Fecha"].dt.to_period("M").astype(str)).groupby(
            "Mes", as_index=False
        )["Monto_Venta_USD"].sum()
        left, right = st.columns([3, 2])
        with left:
            st.subheader("Evolución mensual · USD")
            st.line_chart(monthly.set_index("Mes")["Monto_Venta_USD"])
        with right:
            st.subheader("Participación por canal de venta")
            channels = rows.groupby("Canal_Venta", as_index=False)["Monto_Venta_USD"].sum()
            st.vega_lite_chart(channels, {
                "mark": {"type": "arc", "innerRadius": 55},
                "encoding": {
                    "theta": {"field": "Monto_Venta_USD", "type": "quantitative"},
                    "color": {"field": "Canal_Venta", "type": "nominal", "title": "Canal"},
                    "tooltip": [
                        {"field": "Canal_Venta", "type": "nominal", "title": "Canal"},
                        {"field": "Monto_Venta_USD", "type": "quantitative", "title": "Ventas USD", "format": ",.2f"},
                    ],
                },
            }, width="stretch")
        st.subheader("Productos con mayores ventas · USD")
        top = rows.groupby("Producto")["Monto_Venta_USD"].sum().sort_values(ascending=False)
        st.bar_chart(top, horizontal=True)
        st.caption("Las barras usan USD; no se suman cantidades de productos con medidas distintas.")
    decisions = store.decisions(user["id"])
    st.subheader("Pedidos internos")
    c1, c2, c3 = st.columns(3)
    c1.metric(
        "Pendientes de revisión", sum(d["estado"] == "Pendiente" for d in decisions),
        border=True,
    )
    c2.metric(
        "Listos para producción", sum(d["estado"] == "Aprobado" for d in decisions),
        border=True,
    )
    c3.metric(
        "Exportados a producción", sum(d["estado"] == "Exportado" for d in decisions),
        border=True,
    )
    active_sellers = sum(
        u["role"] == "vendedor" and u["active"] for u in store.list_users(user["id"])
    )
    st.caption(f"{active_sellers} vendedores activos · último registro de ventas: "
               f"{data.last_date:%d/%m/%Y}. Revise la bandeja Producción para actuar.")


def admin_data(store: Store, user: dict, data: SalesData | None, raw: bytes | None,
               filename: str) -> None:
    st.header("Datos y modelo")
    st.write(
        "Suba un XLSX con Fecha, Cliente, Producto, Cantidad y Precio unitario. "
        "El importador reconoce nombres equivalentes y documenta los campos de segmentación faltantes. "
        "Cada carga activada sustituye el histórico anterior."
    )
    if data is not None:
        st.info(f"Activo: {filename} · {len(data.rows):,} registros · "
                f"{data.first_date:%d/%m/%Y} a {data.last_date:%d/%m/%Y}")
        if data.recalculated_amounts:
            st.warning(f"Se calcularon {data.recalculated_amounts} montos sin valor guardado en Excel.")
        st.caption(f"Hojas leídas: {', '.join(data.sheets)} · Huella SHA-256: {data.digest[:16]}…")
        if data.normalization_notes:
            with st.expander(
                f"Adaptaciones aplicadas al archivo ({len(data.normalization_notes)})",
                icon=":material/rule:",
            ):
                for note in data.normalization_notes:
                    st.markdown(f"- {note}")
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
            st.warning(report.model_status) if report.source_end < TODAY - timedelta(days=28) else st.info(report.model_status)
            with st.expander("Diagnóstico del pronóstico · administración"):
                model_metrics(report)
        except ValueError as exc:
            st.warning(str(exc))
    previous = store.datasets(user["id"])
    if previous:
        st.subheader("Cargas anteriores")
        st.dataframe(pd.DataFrame(previous), hide_index=True)


def admin_ai_center(data: SalesData, raw: bytes) -> None:
    st.header("Centro IA", icon=":material/model_training:")
    st.write(
        "Evidencia del entrenamiento, desempeño fuera de muestra y demanda proyectada. "
        "Este panel explica cuándo usar XGBoost y cuándo conservar el promedio histórico."
    )
    as_of = max(TODAY, data.last_date + timedelta(days=1))
    try:
        report = forecasted(raw, as_of)
    except ValueError as exc:
        st.warning(f"No es posible evaluar el modelo: {exc}")
        return

    xgboost_wins = int((report.metrics_by_product["Metodo_ganador"] == "XGBoost").sum())
    total_products = len(report.metrics_by_product)
    with st.container(horizontal=True):
        st.metric("Productos evaluados", total_products, border=True)
        st.metric("XGBoost supera al promedio", xgboost_wins, border=True)
        st.metric(
            "WAPE mediano aplicado",
            "No disponible" if report.median_wape is None else f"{report.median_wape:.1f}%",
            border=True,
        )
        st.metric("Calidad de preparación", f"{data.quality_score}/100", border=True)

    left, right = st.columns([3, 2])
    with left:
        with st.container(border=True):
            st.subheader("Demanda proyectada por producto")
            portfolio = report.rows.groupby("Producto", as_index=False).agg(
                Pronostico_7d=("Pronostico_7d", "sum"),
                Rango_bajo_7d=("Rango_bajo_7d", "sum"),
                Rango_alto_7d=("Rango_alto_7d", "sum"),
                Clientes=("Cliente", "nunique"),
            ).sort_values("Pronostico_7d", ascending=False)
            st.bar_chart(
                portfolio.set_index("Producto")["Pronostico_7d"], horizontal=True
            )
            st.dataframe(
                portfolio,
                hide_index=True,
                column_config={
                    "Pronostico_7d": st.column_config.NumberColumn(
                        "Pronóstico 7 días", format="%.1f"
                    ),
                    "Rango_bajo_7d": st.column_config.NumberColumn(
                        "Rango bajo", format="%.1f"
                    ),
                    "Rango_alto_7d": st.column_config.NumberColumn(
                        "Rango alto", format="%.1f"
                    ),
                },
            )
    with right:
        with st.container(border=True):
            st.subheader("Qué impulsa el modelo")
            st.bar_chart(
                report.feature_importance.head(8).set_index("Variable")["Importancia"],
                horizontal=True,
            )
            st.caption(
                "Importancia relativa del último entrenamiento. Describe el modelo; no prueba "
                "causalidad comercial."
            )

    with st.container(border=True):
        st.subheader("Comparación de métodos por producto")
        model_metrics(report)

    with st.container(border=True):
        st.subheader("Decisiones que permite tomar")
        st.markdown(
            """
            - **Planificar abastecimiento:** concentrar revisión en productos con mayor demanda proyectada.
            - **Elegir el método más confiable:** aplicar XGBoost solo donde superó al promedio histórico.
            - **Dimensionar incertidumbre:** usar el rango orientativo para preparar escenarios de inventario.
            - **Mejorar la fuente:** priorizar campos faltantes que reducen la calidad y trazabilidad del análisis.
            """
        )


def admin_users(store: Store, user: dict, data: SalesData | None) -> None:
    st.header("Usuarios y permisos")
    users = store.list_users(user["id"])
    st.dataframe(pd.DataFrame(users), hide_index=True)
    with st.form("new_user"):
        st.subheader("Crear cuenta")
        username = st.text_input("Usuario nuevo")
        password = st.text_input("Contraseña inicial (12 caracteres mínimo)", type="password")
        role = st.selectbox("Rol", ["vendedor", "admin"])
        initial_clients = st.multiselect("Clientes iniciales si es vendedor",
                                         data.clients if data is not None else [])
        submitted = st.form_submit_button("Crear cuenta", type="primary")
    if submitted:
        try:
            new_user_id = store.create_user(user["id"], username, password, role)
            if role == "vendedor" and initial_clients:
                store.set_assignments(user["id"], new_user_id, initial_clients)
            st.success("Cuenta creada.")
            st.rerun()
        except (ValueError, PermissionError, sqlite3.IntegrityError) as exc:
            # SQLite informa aquí si el nombre de usuario ya existe.
            st.error(str(exc))
    sellers = [u for u in users if u["role"] == "vendedor"]
    if sellers and data is not None:
        without_clients = [u["username"] for u in sellers if not any(
            client in data.clients for client in store.allowed_clients(u["id"])
        )]
        if without_clients:
            st.warning("Sin clientes válidos en el Excel activo: " + ", ".join(without_clients))
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
    st.subheader(f"Productos del cliente · {as_of:%m}/{previous_year}")
    if selected.empty:
        selected = data.rows[data.rows["Cliente"] == client]
        if selected.empty:
            st.info("Este cliente no tiene productos en el Excel activo.")
            return
        st.info("No hay ventas del mismo mes del año anterior. Se muestra todo el "
                "histórico disponible para este cliente.")
    ranked = selected.groupby("Producto", as_index=False).agg(
        Ventas_USD=("Monto_Venta_USD", "sum"),
        Registros_de_venta=("Producto", "size"),
        Cantidad_en_unidad_fuente=("Cantidad_kg_unid", "sum"),
    ).sort_values("Ventas_USD", ascending=False)
    st.bar_chart(ranked.set_index("Producto")["Ventas_USD"], horizontal=True)
    ranked = ranked.rename(columns={
        "Ventas_USD": "Ventas (USD)",
        "Registros_de_venta": "Registros de venta",
        "Cantidad_en_unidad_fuente": "Cantidad (medida del Excel)",
    })
    st.dataframe(ranked, hide_index=True, width="stretch")
    st.caption("Ventas del archivo cargado. El orden usa USD; kg y unidades no se suman "
               "entre productos. Cada registro del Excel se cuenta como una venta.")


def vendor_home(store: Store, user: dict, data: SalesData) -> None:
    st.header("Mi cartera")
    assigned = store.allowed_clients(user["id"])
    allowed = [client for client in assigned if client in data.clients]
    if not allowed:
        st.info("Sus clientes asignados no aparecen en el Excel activo. "
                "Pida a administración que revise el archivo y sus asignaciones." if assigned else
                "Un administrador debe asignarle clientes para mostrar productos y recomendaciones.")
        return
    filtered = data.rows[data.rows["Cliente"].isin(allowed)]
    c1, c2, c3 = st.columns(3)
    c1.metric("Clientes asignados", len(allowed), border=True)
    c2.metric("Productos con ventas", filtered["Producto"].nunique(), border=True)
    c3.metric("Ventas registradas USD", f"${filtered['Monto_Venta_USD'].sum():,.0f}", border=True)
    client = st.selectbox("Cliente", allowed)
    comparable_products(data, client, TODAY)
    st.caption("La recomendación detallada se encuentra en ‘Recomendaciones’.")


def vendor_recommendations(store: Store, user: dict, data: SalesData, raw: bytes) -> None:
    st.header("Recomendaciones para pedidos")
    assigned = store.allowed_clients(user["id"])
    allowed = [client for client in assigned if client in data.clients]
    if not allowed:
        st.info("Sus asignaciones no coinciden con el Excel activo; administración debe actualizarlas."
                if assigned else "Solicite a administración la asignación de al menos un cliente.")
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
        "Producto": "Producto", "Nivel_atencion": "Atención",
        "Pronostico_7d": "Pronóstico · 7 días",
        "Rango_bajo_7d": "Rango bajo", "Rango_alto_7d": "Rango alto",
        "Metodo": "Método aplicado", "XGBoost_7d": "Estimación XGBoost",
        "Mismo_periodo_ano_anterior": "Mismos días · año anterior",
        "Promedio_semanal_mismo_mes": "Promedio semanal · mismo mes anterior",
        "Ultimos_28_dias": "Últimos 28 días", "Situacion": "Lectura comercial",
    }).drop(columns=["Cliente"]), hide_index=True, width="stretch")
    product = st.selectbox("Producto para revisar", predictions["Producto"].tolist())
    selected = predictions[predictions["Producto"] == product].iloc[0]
    st.subheader(product)
    attention_color = {
        "Alta": "red", "Media": "orange", "Baja": "green",
        "Revisión necesaria": "blue",
    }.get(selected["Nivel_atencion"], "gray")
    st.badge(
        f"Atención {selected['Nivel_atencion'].lower()}",
        color=attention_color,
        icon=":material/priority_high:",
    )
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Pronóstico · 7 días", f"{selected['Pronostico_7d']:,.1f}", border=True)
    c2.metric("Estimación XGBoost", f"{selected['XGBoost_7d']:,.1f}", border=True)
    c3.metric(
        "Rango orientativo",
        f"{selected['Rango_bajo_7d']:,.1f}–{selected['Rango_alto_7d']:,.1f}",
        border=True,
    )
    recent = selected["Ultimos_28_dias"]
    c4.metric(
        "Ventas · 28 días previos",
        "Sin dato reciente" if pd.isna(recent) else f"{recent:,.1f}",
        border=True,
    )
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
    with st.expander("Simular un escenario antes de enviar", icon=":material/tune:"):
        st.caption("La simulación no modifica los datos guardados ni crea un pedido.")
        scenario_left, scenario_middle, scenario_right = st.columns(3)
        demand_change = scenario_left.slider(
            "Cambio esperado de demanda", -40, 60, 0, 5, format="%d%%"
        )
        simulated_stock = scenario_middle.number_input(
            "Inventario disponible simulado", min_value=0.0,
            value=float(operational.stock), step=1.0,
        )
        simulated_pending = scenario_right.number_input(
            "Pedidos pendientes simulados", min_value=0.0,
            value=float(operational.pending), step=1.0,
        )
        scenario_operational = OperationalInput.create(
            client, product, operational.unit, simulated_stock, simulated_pending,
            operational.target_stock, operational.minimum, operational.pack_multiple,
            operational.as_of,
        )
        scenario_prediction = float(selected["Pronostico_7d"]) * (1 + demand_change / 100)
        scenario_order = suggest_order(scenario_prediction, scenario_operational)
        st.metric(
            "Pedido del escenario", f"{scenario_order.suggested:g} {operational.unit}",
            delta=f"{float(scenario_order.suggested - suggestion.suggested):+,.0f} vs. sugerido",
            border=True,
        )
    with st.form(f"decision_{client}_{product}_{as_of}"):
        approved = st.number_input("Cantidad que propone solicitar", min_value=0.0,
                                   value=float(suggestion.suggested), step=1.0)
        reason = st.text_area("Motivo del ajuste (obligatorio si cambia la cantidad)")
        requested_date = st.date_input("Fecha requerida de entrega",
                                       value=as_of, min_value=as_of,
                                       max_value=as_of + timedelta(days=6))
        seller_note = st.text_area("Indicaciones para administración y producción (opcional)")
        acknowledged = (st.checkbox(
            "Revisé que el histórico no contiene ventas recientes para esta fecha"
        ) if source_stale else True)
        confirmed = st.form_submit_button("Enviar pedido a revisión", type="primary")
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
                requested_date=requested_date, seller_note=seller_note,
            )
            st.success("Pedido enviado a administración. Aún no se entrega a producción.")
        except (ValueError, PermissionError) as exc:
            st.error(str(exc))


def history(store: Store, user: dict) -> None:
    st.header("Historial de pedidos")
    rows = store.decisions(user["id"])
    if not rows:
        st.info("Todavía no hay pedidos internos.")
        return
    display = pd.DataFrame(rows)
    st.dataframe(display[["id", "estado", "fecha_decision", "usuario", "cliente",
                          "producto", "unidad", "fecha_pronostico", "fecha_requerida",
                          "pronostico", "sugerido", "aprobado", "cantidad_produccion",
                          "revisor", "nota_revision"]], hide_index=True, width="stretch")
    st.caption("Pendiente: espera revisión administrativa. Aprobado: listo para exportar. "
               "Exportado: administración confirmó la entrega del archivo a producción. "
               "Fuente sustituida: el Excel cambió antes del envío.")


def production_queue(store: Store, user: dict, data: SalesData | None) -> None:
    st.header("Pedidos para producción")
    st.write("Revise cada solicitud del vendedor antes de preparar el archivo para producción.")
    rows = store.decisions(user["id"])
    active_digest = data.digest if data is not None else None
    outdated = [row for row in rows if row["estado"] == "Fuente sustituida" or
                (row["estado"] in ("Pendiente", "Aprobado") and
                 row["dataset_digest"] != active_digest)]
    if outdated:
        st.warning(f"{len(outdated)} pedido(s) corresponden a otro Excel o falta la fuente "
                   "activa. Se conservan en Historial; genere pedidos nuevos con el archivo vigente.")
    pending = [row for row in rows if row["estado"] == "Pendiente"
               and row["dataset_digest"] == active_digest]
    approved = [row for row in rows if row["estado"] == "Aprobado"
                and row["dataset_digest"] == active_digest]
    exported = [row for row in rows if row["estado"] == "Exportado"]
    cards = st.columns(3)
    cards[0].metric("Pendientes de revisión", len(pending))
    cards[1].metric("Aprobados para producción", len(approved))
    cards[2].metric("Exportados", len(exported))

    st.subheader("Revisión administrativa")
    if pending:
        st.dataframe(pd.DataFrame(pending)[["id", "fecha_decision", "usuario", "cliente",
                                             "producto", "unidad", "fecha_requerida",
                                             "pronostico", "sugerido", "aprobado",
                                             "nota_vendedor"]], hide_index=True, width="stretch")
        by_id = {row["id"]: row for row in pending}
        selected_id = st.selectbox("Pedido a revisar", list(by_id),
                                   format_func=lambda value: (
                                       f"#{value} · {by_id[value]['cliente']} · "
                                       f"{by_id[value]['producto']}"
                                   ))
        selected = by_id[selected_id]
        with st.form("review_order"):
            decision = st.radio("Decisión", ["Aprobar para producción", "Rechazar"],
                                horizontal=True)
            quantity = st.number_input("Cantidad para producción · " + selected["unidad"],
                                       min_value=0.0, value=float(selected["aprobado"]),
                                       step=1.0)
            note = st.text_area("Motivo del rechazo o cambio de cantidad")
            reviewed = st.form_submit_button("Registrar revisión", type="primary")
        if reviewed:
            try:
                store.review_decision(user["id"], selected_id,
                                      "approved" if decision.startswith("Aprobar") else "rejected",
                                      quantity if decision.startswith("Aprobar") else 0.0,
                                      note)
                st.rerun()
            except (ValueError, PermissionError) as exc:
                st.error(str(exc))
    else:
        st.info("No hay solicitudes pendientes.")

    st.subheader("Archivo para producción")
    st.caption("Se exportan nombre de producto, unidad confirmada, cantidad y fecha requerida. "
               "El Excel actual no contiene códigos SKU; compruebe la unidad antes de revisar.")
    if approved:
        by_id = {row["id"]: row for row in approved}
        selected_ids = st.multiselect("Pedidos aprobados para el lote", list(by_id),
                                      default=list(by_id),
                                      format_func=lambda value: (
                                          f"#{value} · {by_id[value]['producto']} · "
                                          f"{by_id[value]['cantidad_produccion']:g} "
                                          f"{by_id[value]['unidad']}"
                                      ))
        if st.button("Preparar archivo", disabled=not selected_ids):
            st.session_state["production_batch_ids"] = selected_ids
            st.rerun()
        batch_ids = st.session_state.get("production_batch_ids", [])
        if batch_ids and all(identifier in by_id for identifier in batch_ids):
            batch = [by_id[identifier] for identifier in batch_ids]
            st.download_button("Descargar XLSX para producción", export_production(batch),
                               file_name="pedidos_para_produccion.xlsx",
                               mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
            delivered = st.checkbox("Confirmo que descargué y compartí el archivo con producción",
                                    key="delivered_" + "_".join(map(str, batch_ids)))
            if st.button("Registrar entrega del lote a producción", type="primary",
                         disabled=not delivered):
                try:
                    store.mark_exported(user["id"], batch_ids)
                    st.session_state.pop("production_batch_ids", None)
                    st.rerun()
                except (ValueError, PermissionError) as exc:
                    st.error(str(exc))
    else:
        st.info("Primero apruebe al menos un pedido en la revisión administrativa.")
    if exported:
        st.subheader("Productos exportados")
        st.dataframe(pd.DataFrame(exported)[["id", "cliente", "producto", "unidad",
                                              "cantidad_produccion", "fecha_requerida",
                                              "fecha_exportacion"]], hide_index=True,
                     width="stretch")


def main() -> None:
    store = Store(PRIVATE / "smartorder.sqlite3")
    if demo_mode():
        seed_local_demo(store, PRIVATE / "uploads", TODAY)
    user = setup_or_login(store)
    if demo_mode():
        st.sidebar.badge("Demo académica", color="blue", icon=":material/science:")
    st.sidebar.caption(f"{user['username']} · {user['role']}")
    if st.sidebar.button("Cerrar sesión"):
        st.session_state.pop("user_id", None)
        st.session_state.pop("session_version", None)
        st.rerun()
    if PUBLIC_DEMO and not PUBLIC_DEMO_FORCED:
        if st.sidebar.button(":material/logout: Salir de la demo"):
            st.session_state.pop("user_id", None)
            st.session_state.pop("session_version", None)
            st.query_params.clear()
            st.rerun()
    navigation = (["Resumen", "Centro IA", "Producción", "Datos y modelo", "Usuarios", "Historial"]
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
    elif page == "Producción":
        production_queue(store, user, data)
    elif page == "Datos y modelo":
        admin_data(store, user, data, raw, filename)
    elif page == "Centro IA" and data is not None and raw is not None:
        admin_ai_center(data, raw)
    elif page == "Historial":
        history(store, user)
    elif data is None:
        st.info("Un administrador debe cargar un Excel de ventas para comenzar.")
    elif page == "Resumen":
        admin_home(store, user, data)
    elif page == "Mi cartera":
        vendor_home(store, user, data)
    elif page == "Recomendaciones":
        vendor_recommendations(store, user, data, raw)


if __name__ == "__main__":
    main()
