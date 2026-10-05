"""Exportación segura de los pedidos aprobados para producción."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from io import BytesIO
from typing import Iterable

from openpyxl import Workbook


def _decimal(value: object, label: str) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"{label} debe ser numérico.") from exc
    if not result.is_finite() or result < 0:
        raise ValueError(f"{label} debe ser no negativo.")
    return result


def _safe_cell(value: object) -> object:
    if isinstance(value, str) and value.startswith(("=", "+", "-", "@")):
        return "'" + value
    return value


def export_production(rows: Iterable[dict[str, object]]) -> bytes:
    orders = list(rows)
    if not orders:
        raise ValueError("No hay pedidos revisados para exportar.")
    if any(row["estado"] not in ("Aprobado", "Exportado") for row in orders):
        raise ValueError("Solo se pueden exportar pedidos revisados por administración.")
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Pedidos para produccion"
    sheet.append(("Pedido_ID", "Cliente", "Producto", "Unidad", "Cantidad_Produccion",
                  "Fecha_Requerida", "Inicio_Semana", "Vendedor", "Revisado_Por",
                  "Fecha_Revision", "Nota_Revision"))
    totals: dict[tuple[str, str, str], Decimal] = {}
    for row in orders:
        quantity = _decimal(row["cantidad_produccion"], "Cantidad para producción")
        sheet.append([_safe_cell(value) for value in (
            row["id"], row["cliente"], row["producto"], row["unidad"],
            float(quantity), row["fecha_requerida"], row["fecha_pronostico"],
            row["usuario"], row["revisor"], row["fecha_revision"],
            row["nota_revision"],
        )])
        key = (str(row["producto"]), str(row["unidad"]), str(row["fecha_requerida"]))
        totals[key] = totals.get(key, Decimal("0")) + quantity
    sheet.freeze_panes = "A2"
    sheet.auto_filter.ref = sheet.dimensions
    summary = workbook.create_sheet("Resumen por producto")
    summary.append(("Producto", "Unidad", "Fecha_Requerida", "Cantidad_Total"))
    for (product, unit, delivery), quantity in sorted(totals.items()):
        summary.append([_safe_cell(product), _safe_cell(unit), delivery, float(quantity)])
    summary.freeze_panes = "A2"
    summary.auto_filter.ref = summary.dimensions
    output = BytesIO()
    workbook.save(output)
    return output.getvalue()
