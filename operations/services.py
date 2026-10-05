"""ETL and the small amount of shared business logic used by the local web app."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from functools import lru_cache
from pathlib import Path

from django.conf import settings
from django.core.files.base import ContentFile
from django.db import transaction
from django.db.models import Count, Sum
from django.utils import timezone

from smartorder.data import load_sales
from smartorder.inventory import load_inventory
from .models import (
    DatedFlow, InventoryAdjustment, InventoryBatch, InventoryItem, SalesDataset, SalesLine,
)

MONEY = Decimal("0.01")
QUANTITY = Decimal("0.001")


def nonnegative_decimal(value: object, label: str, *, positive: bool = False) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ValueError(f"{label} debe ser un número válido.") from exc
    if not result.is_finite() or result < 0 or (positive and result == 0):
        qualifier = "positivo" if positive else "no negativo"
        raise ValueError(f"{label} debe ser un número {qualifier}.")
    if result > Decimal("999999999999"):
        raise ValueError(f"{label} excede el máximo permitido.")
    quantized = result.quantize(QUANTITY, rounding=ROUND_HALF_UP)
    if quantized != result or (positive and quantized == 0):
        raise ValueError(f"{label} admite hasta tres decimales y debe conservar su valor.")
    return quantized


def active_sales() -> SalesDataset | None:
    return SalesDataset.objects.filter(active=True).first()


def active_inventory() -> InventoryBatch | None:
    return InventoryBatch.objects.filter(active=True).first()


def default_delivery_date(dataset: SalesDataset | None, today: date | None = None) -> date:
    today = today or timezone.localdate()
    candidate = max(today, dataset.last_date + timedelta(days=1)) if dataset else today
    return min(candidate, today + timedelta(days=settings.SMARTORDER_DELIVERY_DAYS))


def import_sales(raw: bytes, filename: str, user, verified: bool, full_snapshot_confirmed: bool = False) -> SalesDataset:
    parsed = load_sales(raw)
    if SalesDataset.objects.filter(digest=parsed.digest).exists():
        raise ValueError("Este mismo archivo de ventas ya fue cargado.")
    filename = Path(filename).name[:255]
    if not filename.lower().endswith(".xlsx"):
        raise ValueError("Seleccione un archivo .xlsx de ventas.")
    uploaded_path = None
    try:
        with transaction.atomic():
            SalesDataset.objects.filter(active=True).update(active=False)
            dataset = SalesDataset(
                digest=parsed.digest, filename=filename, first_date=parsed.first_date,
                last_date=parsed.last_date, row_count=len(parsed.rows), verified=verified,
                full_snapshot_confirmed=full_snapshot_confirmed, active=True, notes="\n".join(parsed.normalization_notes), uploaded_by=user,
            )
            dataset.file.save(f"{parsed.digest}.xlsx", ContentFile(raw), save=False)
            uploaded_path = dataset.file.name
            dataset.save()
            columns = parsed.rows.columns.tolist()
            for start in range(0, len(parsed.rows), 1000):
                chunk = parsed.rows.iloc[start:start + 1000]
                lines = []
                for values in chunk.itertuples(index=False, name=None):
                    row = dict(zip(columns, values))
                    lines.append(SalesLine(
                        dataset=dataset,
                        date=row["Fecha"].date(),
                        client=row["Cliente"], product=row["Producto"],
                        category=row["Categoria"], zone=row["Zona_Geografica"],
                        distribution_channel=row["Canal_Distribucion"],
                        sales_channel=row["Canal_Venta"],
                        quantity=nonnegative_decimal(row["Cantidad_kg_unid"], "Cantidad"),
                        unit_price=Decimal(str(row["Precio_Unitario_USD"])).quantize(Decimal("0.0001")),
                        amount=Decimal(str(row["Monto_Venta_USD"])).quantize(MONEY),
                    ))
                SalesLine.objects.bulk_create(lines, batch_size=1000)
        return dataset
    except Exception:
        if uploaded_path:
            dataset.file.storage.delete(uploaded_path)
        raise


def import_inventory(raw: bytes, filename: str, user,
                     unit_match_confirmed: bool = False) -> InventoryBatch:
    parsed = load_inventory(raw)
    if unit_match_confirmed:
        if any(not row.unit for row in parsed.stocks):
            raise ValueError("Todas las filas necesitan unidad antes de confirmar equivalencia con ventas.")
        units_by_product = {}
        for row in parsed.stocks:
            units_by_product.setdefault(row.product, set()).add(row.unit.casefold())
        if any(len(units) > 1 for units in units_by_product.values()):
            raise ValueError("Un mismo producto tiene unidades diferentes entre clientes; revise equivalencias.")
    if InventoryBatch.objects.filter(digest=parsed.digest).exists():
        raise ValueError("Este mismo archivo de inventario ya fue cargado.")
    filename = Path(filename).name[:255]
    if not filename.lower().endswith(".xlsx"):
        raise ValueError("Seleccione un archivo .xlsx de inventario.")
    uploaded_path = None
    try:
        with transaction.atomic():
            InventoryBatch.objects.filter(active=True).update(active=False)
            batch = InventoryBatch(
                digest=parsed.digest, filename=filename, active=True, uploaded_by=user,
                unit_match_confirmed=unit_match_confirmed, notes=f"Hojas leídas: {', '.join(parsed.sheets)}",
            )
            batch.file.save(f"{parsed.digest}.xlsx", ContentFile(raw), save=False)
            uploaded_path = batch.file.name
            batch.save()
            InventoryItem.objects.bulk_create([
                InventoryItem(
                    batch=batch, client=item.client, product=item.product, unit=item.unit,
                    available=nonnegative_decimal(item.available, "Existencia"),
                    observed_on=item.as_of,
                ) for item in parsed.stocks
            ], batch_size=1000)
            DatedFlow.objects.bulk_create([
                DatedFlow(
                    batch=batch, kind=item.kind, client=item.client, product=item.product,
                    quantity=nonnegative_decimal(item.quantity, "Cantidad"),
                    due_date=item.due_date, created_by=user,
                ) for item in parsed.flows
            ], batch_size=1000)
        return batch
    except Exception:
        if uploaded_path:
            batch.file.storage.delete(uploaded_path)
        raise


@lru_cache(maxsize=4)
def sales_data_for(digest: str, file_path: str):
    # ponytail: source files are immutable and keyed by digest; restart clears this per-process cache.
    return load_sales(Path(file_path))


@lru_cache(maxsize=24)
def forecast_rows(digest: str, file_path: str, delivery: date, verified: bool, today: date):
    from smartorder.recommendations import RecommendationEngine
    source = sales_data_for(digest, file_path)
    return RecommendationEngine(source).for_delivery(
        delivery, source_verified=verified, as_of=today,
    )


def recommendation_cards(dataset: SalesDataset, delivery: date, client: str,
                         today: date | None = None) -> list[dict]:
    today = today or timezone.localdate()
    if delivery > today + timedelta(days=7):
        # ponytail: permit future sales without extrapolating a model audited only
        # for seven-day lead times; extend model evaluation before enabling forecasts.
        rows = [{"Cliente": owner, "Producto": product,
                 "explanation": "Entrega futura; cantidad manual y planificación pendiente."}
                for owner, product in dataset.lines.filter(client=client)
                .values_list("client", "product").distinct()]
    else:
        frame = forecast_rows(dataset.digest, dataset.file.path, delivery,
                              dataset.verified and dataset.full_snapshot_confirmed, today)
        if client:
            frame = frame[frame["Cliente"] == client]
        rows = frame.to_dict("records")
    batch = active_inventory()
    inventory = {
        (item.client, item.product): item
        for item in InventoryItem.objects.filter(batch=batch)
    } if batch else {}
    adjustments = {}
    if batch:
        for change in InventoryAdjustment.objects.filter(batch=batch).order_by("created_at", "pk"):
            adjustments[(change.client, change.product)] = change
    flows = list(DatedFlow.objects.filter(batch=batch)) if batch else []
    commercial = {
        row["product"]: row
        for row in dataset.lines.filter(client=client).values("product").annotate(
            revenue=Sum("amount"), days_with_sale=Count("date", distinct=True)
        )
    }
    categories = {}
    for product, category in dataset.lines.filter(client=client).values_list(
        "product", "category"
    ).distinct():
        categories.setdefault(product, category)
    cards = []
    delivery_outside_forecast = delivery <= dataset.last_date or delivery > today + timedelta(days=7)
    for row in rows:
        key = (row["Cliente"], row["Producto"])
        item = inventory.get(key)
        change = adjustments.get(key)
        available = Decimal(str(change.available if change else item.available)) if item else None
        stock_date = (change.observed_on if change else item.observed_on) if item else None
        due_flows = [flow for flow in flows if (flow.client, flow.product) == key]
        incoming = sum((flow.quantity for flow in due_flows
                        if flow.kind == DatedFlow.Kind.INCOMING
                        and today <= flow.due_date <= delivery), Decimal("0"))
        commitments_before = sum((flow.quantity for flow in due_flows
                                  if flow.kind == DatedFlow.Kind.CUSTOMER_COMMITMENT
                                  and today <= flow.due_date < delivery), Decimal("0"))
        commitments = sum((flow.quantity for flow in due_flows
                           if flow.kind == DatedFlow.Kind.CUSTOMER_COMMITMENT
                           and delivery <= flow.due_date <= delivery + timedelta(days=6)),
                          Decimal("0"))
        predicted = row.get("forecast")
        eligible = bool(row.get("eligible_for_auto")) and predicted is not None
        stock_current = stock_date == today
        units_confirmed = bool(batch and batch.unit_match_confirmed and item and item.unit)
        metrics = commercial.get(row["Producto"], {})
        reference_start = delivery.replace(year=delivery.year - 1) if (
            delivery.month != 2 or delivery.day != 29
        ) else delivery.replace(year=delivery.year - 1, day=28)
        reference_period = (
            f"{reference_start:%d/%m/%Y}–{reference_start + timedelta(days=6):%d/%m/%Y}"
            if row.get("reference_previous_year_7d") is not None else ""
        )
        suggested = None
        if eligible and stock_current and units_confirmed:
            forecast = nonnegative_decimal(predicted, "Pronóstico")
            # A customer commitment within the forecast week may already be
            # represented in sales history, so use the greater demand value.
            projected_stock = max(Decimal("0"), available + incoming - commitments_before)
            demand_to_cover = max(forecast, commitments)
            # ponytail: no minimum/pack rounding without an official product catalogue;
            # admin can add those rules once SKU and conversions are available.
            suggested = max(Decimal("0"), demand_to_cover - projected_stock)
            suggested = suggested.quantize(QUANTITY, rounding=ROUND_HALF_UP)
        cards.append({
            "client": row["Cliente"], "product": row["Producto"],
            "category": categories.get(row["Producto"], ""),
            "sales_usd_display": f"${metrics.get('revenue', Decimal('0')):,.2f}",
            "days_with_sale": metrics.get("days_with_sale", 0),
            "reference_period": reference_period,
            "unit_confirmed": units_confirmed,
            "state": "suggested" if suggested is not None else (
                "reference" if row.get("reference_previous_year_7d") is not None else "manual"
            ),
            "reference_qty": row.get("reference_previous_year_7d"),
            "forecast": predicted if eligible else None,
            "delivery_outside_forecast": delivery_outside_forecast,
            "method": row.get("method", ""),
            "explanation": " ".join(filter(None, [
                row.get("evidence"), row.get("explanation"),
                ("Inventario sin equivalencia de unidad confirmada." if not units_confirmed else ""),
            ])),
            "suggested": suggested,
            "unit": item.unit if item else "",
            "available": available,
            "stock_date": stock_date,
            "incoming": incoming,
            "commitments_before": commitments_before,
            "commitments": commitments,
            "inventory_warning": (
                "No hay inventario oficial actualizado; indique una cantidad manual."
                if not stock_current else
                "Administración aún no confirmó que la unidad del inventario coincide con ventas."
                if not units_confirmed else ""
            ),
        })
    return sorted(cards, key=lambda card: commercial.get(card["product"], {}).get(
        "revenue", Decimal("0")
    ), reverse=True)


def top_products_for(dataset: SalesDataset, clients: list[str] | None = None,
                     limit: int = 8) -> list[dict]:
    lines = dataset.lines.all()
    if clients is not None:
        lines = lines.filter(client__in=clients)
    rows = lines.values("product").annotate(revenue=Sum("amount")).order_by("-revenue")[:limit]
    return [{"product": row["product"], "revenue": row["revenue"]} for row in rows]
