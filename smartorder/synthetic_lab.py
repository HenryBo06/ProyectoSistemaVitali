"""Laboratorio 100 % sintetico y reproducible para SmartOrder.

No carga archivos empresariales, no consulta red, no usa Django/BD ni autoriza
cantidades o produccion. El objetivo de ranking es Hit@1 condicional a compra
observada; dias sin filas NO son ventas cero ni prueba de cobertura completa.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, replace
from datetime import date, timedelta
from hashlib import sha256
from math import ceil
from random import Random
from statistics import mean, median
from time import perf_counter
from typing import Iterable
import csv
import io
import json

from smartorder.product_priority import _order, evaluate_product_priority


UNITS = ("kg", "lb", "unidad")
SCENARIOS = (
    "semanal_estable", "quincenal", "temporada_alta",
    "cambio_preferencia", "intermitente", "promocion_puntual",
    "cierre_sin_cobertura", "tendencia_creciente",
)
FIELDNAMES = (
    "Fecha", "Cliente", "Zona_Geografica", "Canal_Distribucion",
    "Canal_Venta", "Producto", "Categoria", "Cantidad_kg_unid",
    "Precio_Unitario_USD", "Monto_Venta_USD", "Unidad",
    "Referencia_pedido", "Referencia_linea",
)
DEFAULT_AS_OF = date(2026, 11, 1)


@dataclass(frozen=True)
class SyntheticSale:
    day: date
    client: str
    product: str
    unit: str
    quantity: int
    price_cents: int
    order_ref: str
    line_ref: str
    scenario: str

    def row(self) -> dict[str, object]:
        return {
            "Fecha": self.day.isoformat(),
            "Cliente": self.client,
            "Zona_Geografica": "Zona LAB ficticia",
            "Canal_Distribucion": "Distribucion LAB",
            "Canal_Venta": "Pedido LAB simulado",
            "Producto": self.product,
            "Categoria": "Simulacion avicola (no comercial)",
            "Cantidad_kg_unid": self.quantity,
            "Precio_Unitario_USD": f"{self.price_cents / 100:.2f}",
            "Monto_Venta_USD": f"{self.quantity * self.price_cents / 100:.2f}",
            "Unidad": self.unit,
            "Referencia_pedido": self.order_ref,
            "Referencia_linea": self.line_ref,
        }


def catalog(products: int) -> dict[str, str]:
    if not 3 <= products <= 12:
        raise ValueError("products debe estar entre 3 y 12")
    return {f"Producto LAB-{index + 1:02d} (SIMULADO)": UNITS[index % len(UNITS)]
            for index in range(products)}


def _scheduled(scenario: str, product_index: int, offset: int, day: date,
               total_days: int, client_index: int) -> tuple[bool, float]:
    """Patrones ficticios con regularidad, estacionalidad y cambios de regimen."""
    switch = int(total_days * 0.53)
    if scenario == "cierre_sin_cobertura" and int(total_days * .55) <= offset < int(total_days * .55) + 21:
        return False, 1.0
    period = (7, 14, 21, 28)[product_index % 4]
    magnitude = 1.0
    if scenario == "semanal_estable" and product_index == 0:
        period = 7
    elif scenario == "quincenal" and product_index == 1:
        period = 14
    elif scenario == "temporada_alta" and product_index == 2:
        period = 3 if day.month in (11, 12) else 28
        magnitude = 2.0 if day.month in (11, 12) else 1.0
    elif scenario == "cambio_preferencia" and product_index == 0:
        period = 4 if offset < switch else 29
    elif scenario == "cambio_preferencia" and product_index == 1:
        period = 29 if offset < switch else 4
    elif scenario == "intermitente" and product_index == 2:
        period = 41
    elif scenario == "promocion_puntual" and product_index == 3:
        in_promo = int(total_days * .7) <= offset < int(total_days * .7) + 9
        if in_promo:
            return True, 3.5
        period = 29
    elif scenario == "tendencia_creciente" and product_index == 0:
        magnitude = 1.0 + 2.0 * offset / total_days
    active = (offset + client_index + product_index * 3) % period == 0
    return active, magnitude


def generate(*, seed: int = 20261103, clients: int = 12, products: int = 8,
             days: int = 630, as_of: date = DEFAULT_AS_OF) -> tuple[list[SyntheticSale], dict[str, str]]:
    """Solo crea clientes ficticios; as_of es el primer dia fuera de la fuente."""
    if not 3 <= clients <= 250 or not 180 <= days <= 1100 or not isinstance(as_of, date):
        raise ValueError("clients 3..250, days 180..1100 y fecha as_of valida")
    units_by_product = catalog(products)
    rng = Random(seed)
    first = as_of - timedelta(days=days)
    rows: list[SyntheticSale] = []
    seq = 0
    for customer_index in range(clients):
        client = f"Cliente LAB-{customer_index + 1:03d} (SIMULADO)"
        scenario = SCENARIOS[customer_index % len(SCENARIOS)]
        for offset in range(days):
            day = first + timedelta(days=offset)
            for pindex, (product, unit) in enumerate(units_by_product.items()):
                scheduled, factor = _scheduled(scenario, pindex, offset, day, days, customer_index)
                if not scheduled or rng.random() < 0.05:
                    continue
                basic_quantity = 3 + customer_index % 4 * 3 + pindex % 4 * 2 + rng.randrange(4)
                quantity = max(1, round(basic_quantity * factor))
                price_cents = 220 + (pindex * 85)
                # Two distinct orders on one day are legitimate transactions.
                repeats = 2 if rng.random() < 0.012 else 1
                for _ in range(repeats):
                    seq += 1
                    rows.append(SyntheticSale(
                        day, client, product, unit, quantity, price_cents,
                        f"LAB-PED-{seq:08d}", f"LAB-LIN-{seq:08d}", scenario,
                    ))
    return sorted(rows, key=lambda sale: (
        sale.day, sale.client, sale.product, sale.line_ref,
    )), units_by_product


def injected_adversarial_cases(rows: list[SyntheticSale], as_of: date) -> list[SyntheticSale]:
    """Siete defectos artificiales aislados: nunca se mezclan con ventas limpias."""
    first = rows[0]
    return [
        replace(first, unit="caja", line_ref="LAB-ADVERSO-UNIT"),
        replace(first, unit="lb" if first.unit != "lb" else "kg", line_ref="LAB-ADVERSO-MISMATCH"),
        replace(first, quantity=-5, line_ref="LAB-ADVERSO-NEG"),
        replace(first, quantity=1_000_001, line_ref="LAB-ADVERSO-OUTLIER"),
        replace(first, client="", line_ref="LAB-ADVERSO-EMPTY"),
        replace(first, day=as_of + timedelta(days=1), line_ref="LAB-ADVERSO-FUTURE"),
        replace(first, line_ref=first.line_ref, order_ref="LAB-ADVERSO-DUPLICATE"),
        replace(first, price_cents=0, line_ref="LAB-ADVERSO-PRICE"),
    ]


def quarantine(rows: Iterable[SyntheticSale], units_by_product: dict[str, str], *,
               as_of: date) -> tuple[list[SyntheticSale], dict[str, int]]:
    """Validacion fail-closed de fuente para la demostracion; no ETL productivo."""
    valid: list[SyntheticSale] = []
    rejected: Counter[str] = Counter()
    seen_refs: set[str] = set()
    for row in rows:
        if not row.client or not row.product or not row.line_ref or not row.order_ref:
            reason = "campos_obligatorios"
        elif row.day >= as_of:
            reason = "fecha_fuera_del_corte"
        elif row.unit not in UNITS:
            reason = "unidad_ajena_al_catalogo"
        elif row.product not in units_by_product or units_by_product[row.product] != row.unit:
            reason = "unidad_incompatible_por_producto"
        elif row.quantity <= 0:
            reason = "cantidad_no_positiva"
        elif row.quantity > 1_000_000:
            reason = "cantidad_extrema_en_cuarentena"
        elif row.price_cents <= 0:
            reason = "precio_no_positivo"
        elif row.line_ref in seen_refs:
            reason = "referencia_linea_repetida"
        else:
            reason = ""
        if reason:
            rejected[reason] += 1
        else:
            seen_refs.add(row.line_ref)
            valid.append(row)
    return valid, dict(sorted(rejected.items()))


def serialize_csv(rows: Iterable[SyntheticSale]) -> bytes:
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=FIELDNAMES, lineterminator="\n")
    writer.writeheader()
    for sale in rows:
        writer.writerow(sale.row())
    return buffer.getvalue().encode("utf-8")


def _metrics(cases: list[dict]) -> dict:
    n = len(cases)
    return {
        "escenarios_evaluados": n,
        "clientes_evaluados": len({case["client"] for case in cases}),
        "hit1_adaptativo": round(mean(case["adaptive"] for case in cases), 4) if n else None,
        "hit1_historico": round(mean(case["historical"] for case in cases), 4) if n else None,
        "hit1_reciente": round(mean(case["recent"] for case in cases), 4) if n else None,
        "hit3_adaptativo": round(mean(case["adaptive_top3"] for case in cases), 4) if n else None,
        "aleatorio_esperado": round(mean(case["random_expectation"] for case in cases), 4) if n else None,
        "cambios_a_metodo_auditado": sum(case["validated"] for case in cases),
    }


def rolling_backtest(rows: list[SyntheticSale], *, as_of: date, horizon: int,
                     client_limit: int = 24) -> dict:
    """Cortes posteriores a 65 % del calendario; cero informacion futura al modelo."""
    if horizon not in (7, 30):
        raise ValueError("Horizonte permitido 7 o 30 dias")
    if not 1 <= client_limit <= 250:
        raise ValueError("client_limit debe estar entre 1 y 250")
    first = min(row.day for row in rows)
    days = (as_of - first).days
    holdout_start = first + timedelta(days=ceil(days * .65))
    cutoffs = [
        holdout_start + timedelta(days=28 * ix)
        for ix in range(max(0, (as_of - horizon - holdout_start).days // 28 + 1))
    ]
    grouped: dict[str, list[SyntheticSale]] = defaultdict(list)
    for row in rows:
        if row.day < as_of:
            grouped[row.client].append(row)
    evaluated_clients = sorted(grouped)[:client_limit]
    samples: list[dict] = []
    skipped = Counter()
    for cutoff in cutoffs:
        for client in evaluated_clients:
            series = grouped[client]
            prior = [(sale.day, sale.product) for sale in series if sale.day < cutoff]
            future = {sale.product for sale in series
                      if cutoff <= sale.day < cutoff + timedelta(days=horizon)}
            candidates = sorted({product for _, product in prior})
            if len(candidates) < 3:
                skipped["menos_de_tres_productos_previamente_comprados"] += 1
                continue
            if not future:
                skipped["sin_compra_observada_en_horizonte"] += 1
                continue
            baseline = _order(prior, candidates, cutoff, "historical")
            recent = _order(prior, candidates, cutoff, "recent_30d")
            # Se entrega SOLO el pasado al selector y se puntua contra futuro separado.
            decision = evaluate_product_priority(
                prior, as_of=cutoff, products=baseline,
            )
            ranked = decision.ranked
            samples.append({
                "client": client, "scenario": series[0].scenario,
                "adaptive": int(ranked[0] in future),
                "historical": int(baseline[0] in future),
                "recent": int(recent[0] in future),
                "adaptive_top3": int(bool(set(ranked[:3]) & future)),
                "random_expectation": len(set(candidates) & future) / len(candidates),
                "validated": int(decision.validated),
            })
    return {
        "horizonte_dias": horizon,
        "inicio_reserva_fuera_de_entrenamiento": holdout_start.isoformat(),
        "cortes_fecha": len(cutoffs),
        "clientes_muestreados": len(evaluated_clients),
        "casos_excluidos": dict(sorted(skipped.items())),
        "total": _metrics(samples),
        "por_patron": {
            pattern: _metrics([row for row in samples if row["scenario"] == pattern])
            for pattern in SCENARIOS
        },
        "interpretacion": "Hit@1 condicional a venta observada. La preferencia se decide solo con eventos anteriores al corte.",
    }


def transaction_quantity_diagnostic(rows: list[SyntheticSale]) -> dict:
    """Error por transaccion existente, sin sumar unidades heterogeneas."""
    histories: dict[tuple[str, str, str], list[SyntheticSale]] = defaultdict(list)
    for row in rows:
        histories[(row.client, row.product, row.unit)].append(row)
    errors = defaultdict(lambda: {"median": [], "last": []})
    for (client, product, unit), sales in histories.items():
        sales.sort(key=lambda x: (x.day, x.line_ref))
        if len(sales) < 7:
            continue
        first_test = max(5, int(len(sales) * .7))
        for idx in range(first_test, len(sales)):
            history = [item.quantity for item in sales[max(0, idx - 4):idx]]
            quantity = sales[idx].quantity
            errors[unit]["median"].append(abs(quantity - median(history)))
            errors[unit]["last"].append(abs(quantity - history[-1]))
    return {
        unit: {
            "transacciones_evaluadas": len(errors[unit]["median"]),
            "mae_mediana_4": round(mean(errors[unit]["median"]), 4) if errors[unit]["median"] else None,
            "mae_ultima": round(mean(errors[unit]["last"]), 4) if errors[unit]["last"] else None,
        } for unit in UNITS
    }


def run_audit(*, seed: int = 20261103, clients: int = 12, products: int = 8,
              days: int = 630, as_of: date = DEFAULT_AS_OF,
              evaluation_client_limit: int = 24) -> tuple[dict, list[SyntheticSale]]:
    start = perf_counter()
    rows, units = generate(seed=seed, clients=clients, products=products, days=days, as_of=as_of)
    generated_at = perf_counter()
    if not rows:
        raise ValueError("El escenario no genero transacciones")
    validated, rejected = quarantine(
        [*rows, *injected_adversarial_cases(rows, as_of)], units, as_of=as_of,
    )
    assert validated == rows and sum(rejected.values()) == 8, "Cuarentena sintetica insegura"
    measures = {
        str(horizon): rolling_backtest(
            rows, as_of=as_of, horizon=horizon, client_limit=evaluation_client_limit,
        ) for horizon in (7, 30)
    }
    if not any(value["total"]["escenarios_evaluados"] for value in measures.values()):
        raise ValueError("Datos insuficientes para cortes retrospectivos; amplie days")
    by_unit = Counter(row.unit for row in rows)
    by_pattern = Counter(row.scenario for row in rows)
    observed_cells = len({(row.day, row.client, row.product) for row in rows})
    digest = sha256(serialize_csv(rows)).hexdigest()
    # Ejemplo de invariancia: filas futuras no afectan lo que se sabia al corte.
    sample_client = rows[0].client
    same_client = [(r.day, r.product) for r in rows if r.client == sample_client]
    probe = as_of - timedelta(days=30)
    candidates = _order(
        [(d, p) for d, p in same_client if d < probe],
        sorted({p for d, p in same_client if d < probe}), probe, "historical",
    )
    original = evaluate_product_priority(same_client, as_of=probe, products=candidates)
    extra_future = [(as_of + timedelta(days=100 + day), candidates[-1]) for day in range(20)]
    injected = evaluate_product_priority(
        same_client + extra_future, as_of=probe, products=candidates,
    )
    assert original == injected, "Fuga temporal detectada en simulacion"
    evaluated_at = perf_counter()
    return ({
        "laboratorio": "SmartOrder AI — AUDITORIA TOTALMENTE SIMULADA",
        "fuente": "generador_local_sin_archivos_externos",
        "nunca_importa_xlsx_real": True,
        "datos_empresariales_utilizados": False,
        "semantica_fecha": "fecha_de_venta_o_pedido_simulado; NO entrega real",
        "cobertura_calendario": "NO VERIFICADA; dias ausentes NO son cero",
        "permisos_operativos": "solo diagnostico; no activa cantidad ni pedidos",
        "parametros": {
            "semilla": seed, "clientes": clients, "productos": products,
            "dias": days, "fecha_corte_exclusiva": as_of.isoformat(),
            "clientes_maximos_evaluados": evaluation_client_limit,
        },
        "huella_csv_sha256": digest,
        "calidad": {
            "filas_validas": len(rows),
            "fechas_distintas": len({row.day for row in rows}),
            "clientes_distintos": len({row.client for row in rows}),
            "productos_distintos": len({row.product for row in rows}),
            "conteo_por_unidad": dict(sorted(by_unit.items())),
            "conteo_por_escenario": dict(sorted(by_pattern.items())),
            "celdas_cliente_producto_fecha_con_venta": observed_cells,
            "ocupacion_registros_no_equivale_cobertura":
                round(observed_cells / (clients * products * days), 6),
            "ocho_anomalias_aisladas_en_cuarentena": rejected,
            "catalogo_unidades": units,
        },
        "priorizacion_retroactiva": measures,
        "cantidades_por_transaccion_y_unidad": transaction_quantity_diagnostic(rows),
        "invariantes": {
            "sin_fuga_temporal": True,
            "unidades_separadas": all(row.unit == units[row.product] for row in rows),
            "lineas_unicas": len({row.line_ref for row in rows}) == len(rows),
            "no_hay_pedidos_automaticos": True,
        },
        "desempeno_observado_segundos": {
            "generacion": round(generated_at - start, 3),
            "evaluacion_y_analisis": round(evaluated_at - generated_at, 3),
            "total": round(evaluated_at - start, 3),
        },
        "limitaciones": [
            "Las tasas no estiman precision comercial de Vitali ni del XGBoost de cantidades.",
            "El Hit@1 se condiciona a que haya compras observadas; no mide ventas perdidas.",
            "Dias sin filas no tienen cobertura confirmada ni equivalen a demanda cero.",
            "La evaluacion sin reentrenamiento completo no reemplaza pruebas de aceptacion.",
            "No se agregan errores entre unidades kg, lb y unidad.",
        ],
    }, rows)
