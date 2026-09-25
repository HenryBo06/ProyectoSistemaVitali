"""Reglas explícitas para pasar de demanda prevista a un pedido revisable."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_CEILING
from io import BytesIO
from typing import Iterable

from openpyxl import Workbook


def _decimal(value: object, label: str, *, positive: bool = False) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"{label} debe ser numérico.") from exc
    if not result.is_finite() or result < 0 or (positive and result == 0):
        raise ValueError(f"{label} debe ser {'mayor que cero' if positive else 'no negativo'}.")
    return result


@dataclass(frozen=True)
class OperationalInput:
    client: str
    product: str
    unit: str
    stock: Decimal
    pending: Decimal
    target_stock: Decimal
    minimum: Decimal
    pack_multiple: Decimal
    as_of: date

    @classmethod
    def create(cls, client: str, product: str, unit: str, stock: object,
               pending: object, target_stock: object, minimum: object,
               pack_multiple: object, as_of: date) -> "OperationalInput":
        if not client.strip() or not product.strip() or not unit.strip():
            raise ValueError("Cliente, producto y unidad son obligatorios.")
        if as_of > date.today():
            raise ValueError("La fecha de inventario no puede estar en el futuro.")
        return cls(client.strip(), product.strip(), unit.strip(),
                   _decimal(stock, "Inventario disponible"),
                   _decimal(pending, "Pedidos pendientes"),
                   _decimal(target_stock, "Inventario objetivo"),
                   _decimal(minimum, "Pedido mínimo"),
                   _decimal(pack_multiple, "Múltiplo de empaque", positive=True), as_of)


@dataclass(frozen=True)
class OrderSuggestion:
    predicted: Decimal
    raw: Decimal
    suggested: Decimal
    reason: str


def suggest_order(predicted: object, operational: OperationalInput) -> OrderSuggestion:
    demand = _decimal(predicted, "Pronóstico")
    raw = max(Decimal("0"), demand + operational.target_stock
              - operational.stock - operational.pending)
    if raw == 0:
        quantity = Decimal("0")
    else:
        needed = max(raw, operational.minimum)
        quantity = (needed / operational.pack_multiple).to_integral_value(
            rounding=ROUND_CEILING
        ) * operational.pack_multiple
    reason = (
        f"Demanda prevista {demand:g} + inventario objetivo {operational.target_stock:g} "
        f"− disponible {operational.stock:g} − pendiente {operational.pending:g}; "
        f"mínimo {operational.minimum:g}, múltiplo {operational.pack_multiple:g} "
        f"{operational.unit}."
    )
    return OrderSuggestion(demand, raw, quantity, reason)


def _safe_cell(value: object) -> object:
    if isinstance(value, str) and value.startswith(("=", "+", "-", "@")):
        return "'" + value
    return value


def export_approved(rows: Iterable[dict[str, object]]) -> bytes:
    columns = ("fecha_decision", "usuario", "cliente", "producto", "unidad",
               "fecha_pronostico", "pronostico", "sugerido", "aprobado", "motivo")
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Pedidos aprobados"
    sheet.append(columns)
    count = 0
    for row in rows:
        sheet.append([_safe_cell(row.get(column, "")) for column in columns])
        count += 1
    if not count:
        raise ValueError("No hay pedidos aprobados para exportar.")
    sheet.freeze_panes = "A2"
    sheet.auto_filter.ref = sheet.dimensions
    output = BytesIO()
    workbook.save(output)
    return output.getvalue()
