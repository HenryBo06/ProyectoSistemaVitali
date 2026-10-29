"""ETL and the small amount of shared business logic used by the local web app."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from functools import lru_cache
from hashlib import sha256
import json
from pathlib import Path

from django.conf import settings
from django.core.files.base import ContentFile
from django.db import transaction
from django.db.models import Count, Q, Sum
from django.utils import timezone

from smartorder.data import load_sales
from smartorder.data import SalesData
from smartorder.inventory import load_inventory
from .models import (
    ClientStockCount, ClientStockMovement, CommercialEvent, DatedFlow, InventoryAdjustment, InventoryBatch,
    InventoryItem, LocationIncoming, LocationStockSnapshot, OdooCustomerMapping,
    OrderLine, OrderRequest, PriceTariff, RestockTarget, SalesCoverage, SalesDataset, SalesLine,
    SalesOperationalReconciliation,
)

MONEY = Decimal("0.01")
QUANTITY = Decimal("0.001")


def nonnegative_decimal(value: object, label: str, *, positive: bool = False, places: int = 3) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ValueError(f"{label} debe ser un número válido.") from exc
    if not result.is_finite() or result < 0 or (positive and result == 0):
        qualifier = "positivo" if positive else "no negativo"
        raise ValueError(f"{label} debe ser un número {qualifier}.")
    if result > Decimal("999999999999"):
        raise ValueError(f"{label} excede el máximo permitido.")
    quantized = result.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)
    if quantized != result or (positive and quantized == 0):
        raise ValueError(f"{label} admite hasta {places} decimales y debe conservar su valor.")
    return quantized


def active_sales() -> SalesDataset | None:
    return SalesDataset.objects.filter(active=True).first()


def active_inventory() -> InventoryBatch | None:
    return InventoryBatch.objects.filter(active=True).first()


def default_delivery_date(dataset: SalesDataset | None, today: date | None = None) -> date:
    today = today or timezone.localdate()
    candidate = max(today, dataset.last_date + timedelta(days=1)) if dataset else today
    return min(candidate, today + timedelta(days=settings.SMARTORDER_DELIVERY_DAYS))


def _optional_day(value):
    if value is None:
        return None
    try:
        if value != value:
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(value, date):
        return value
    if hasattr(value, "date"):
        return value.date()
    return date.fromisoformat(str(value))


def import_sales(raw: bytes, filename: str, user, verified: bool,
                 full_snapshot_confirmed: bool = False, *,
                 provenance: str = SalesDataset.Provenance.UNKNOWN,
                 date_semantics: str = SalesDataset.DateSemantics.SALE_ORDER) -> SalesDataset:
    if provenance not in SalesDataset.Provenance.values:
        raise ValueError("Confirme si la fuente es real o simulada.")
    if date_semantics not in SalesDataset.DateSemantics.values:
        raise ValueError("Confirme el significado de las fechas del archivo.")
    parsed = load_sales(raw, date_semantics=date_semantics, provenance=provenance)
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
                provenance=provenance, date_semantics=date_semantics,
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
                        requested_delivery_date=_optional_day(row.get("Fecha_entrega_solicitada")),
                        committed_delivery_date=_optional_day(row.get("Fecha_entrega_comprometida")),
                        provenance=provenance,
                        unit=str(row.get("Unidad") or "").strip()[:50],
                        source_order_ref=str(row.get("Referencia_pedido") or "").strip()[:160],
                        source_line_ref=str(row.get("Referencia_linea") or "").strip()[:160],
                        client=row["Cliente"], product=row["Producto"],
                        category=row["Categoria"], zone=row["Zona_Geografica"],
                        distribution_channel=row["Canal_Distribucion"],
                        sales_channel=row["Canal_Venta"],
                        quantity=nonnegative_decimal(row["Cantidad_kg_unid"], "Cantidad"),
                        unit_price=Decimal(str(row["Precio_Unitario_USD"])).quantize(Decimal("0.0001")),
                        amount=Decimal(str(row["Monto_Venta_USD"])).quantize(MONEY),
                    ))
                SalesLine.objects.bulk_create(lines, batch_size=1000)
            coverage = [
                SalesCoverage(
                    dataset=dataset, client=client, product=product, unit=unit,
                    start_date=dataset.first_date, end_date=dataset.last_date,
                    status=SalesCoverage.Status.UNKNOWN,
                )
                for client, product, unit in parsed.rows[
                    ["Cliente", "Producto", "Unidad"]
                ].fillna("").drop_duplicates().itertuples(index=False, name=None)
            ]
            SalesCoverage.objects.bulk_create(coverage, batch_size=1000)
        return dataset
    except Exception:
        if uploaded_path:
            dataset.file.storage.delete(uploaded_path)
        raise


def save_sales_coverage(dataset: SalesDataset, user, *, client: str, product: str,
                        unit: str, start_date: str, end_date: str, status: str,
                        coverage_id: int | None = None) -> SalesCoverage:
    if status not in SalesCoverage.Status.values:
        raise ValueError("Seleccione un estado de cobertura válido.")
    try:
        start, end = date.fromisoformat(start_date), date.fromisoformat(end_date)
    except (TypeError, ValueError) as exc:
        raise ValueError("El intervalo de cobertura necesita fechas válidas.") from exc
    unit = unit.strip()
    if not client.strip() or not product.strip() or len(unit) > 50:
        raise ValueError("Cliente, producto y unidad deben estar identificados.")
    if status == SalesCoverage.Status.COMPLETE and not unit:
        raise ValueError("Una cobertura completa requiere una unidad histórica confirmada.")
    if start < dataset.first_date or end > timezone.localdate() - timedelta(days=1) or end < start:
        raise ValueError("La cobertura debe quedar dentro del archivo y terminar a más tardar en el último día cerrado.")
    if (status == SalesCoverage.Status.NO_SERIES and dataset.lines.filter(
            client=client, product=product, date__range=(start, end)).exists()):
        raise ValueError("Una serie con registros en este intervalo no puede marcarse como inexistente.")
    if coverage_id:
        coverage = SalesCoverage.objects.filter(dataset=dataset, pk=coverage_id).first()
        if coverage is None:
            raise ValueError("La cobertura ya no pertenece al archivo seleccionado.")
        coverage.client, coverage.product, coverage.unit = client, product, unit
        coverage.start_date, coverage.end_date, coverage.status = start, end, status
    else:
        coverage, created = SalesCoverage.objects.get_or_create(
            dataset=dataset, client=client, product=product, unit=unit,
            start_date=start, end_date=end,
            defaults={"status": status, "reviewed_by": user, "reviewed_at": timezone.now()},
        )
        if not created:
            coverage.status = status
    coverage.reviewed_by, coverage.reviewed_at = user, timezone.now()
    coverage.save()
    _cached_sales_data.cache_clear()
    _cached_sale_forecast_rows.cache_clear()
    sales_data_with_confirmed_orders.cache_clear()
    return coverage


def _active_confirmed_orders_between(start: date, end: date):
    return OrderRequest.objects.filter(
        sale_confirmed=True, sale_confirmed_at__date__range=(start, end),
    ).exclude(status__in=(OrderRequest.Status.CANCELLED, OrderRequest.Status.SUPERSEDED)).filter(
        Q(exception_reason="") | Q(commercial_reviewed_at__isnull=False)
    ).prefetch_related("lines")


def _imported_operational_refs(dataset: SalesDataset) -> set[tuple[str, str]]:
    return set(dataset.lines.exclude(source_order_ref="").exclude(source_line_ref="")
               .values_list("source_order_ref", "source_line_ref"))


def _line_duplicate_candidates(dataset: SalesDataset, order, line):
    if dataset.date_semantics != SalesDataset.DateSemantics.SALE_ORDER or not line.unit.strip():
        return SalesLine.objects.none()
    return dataset.lines.filter(
        client=order.client, product=line.product,
        date=timezone.localtime(order.sale_confirmed_at).date(),
        quantity=line.requested_qty, unit__iexact=line.unit.strip(),
    ).exclude(operational_reconciliations__dataset=dataset).order_by("pk")


def operational_overlap_rows(dataset: SalesDataset) -> list[dict]:
    """Return unresolved order lines and exact imported candidates for admin reconciliation."""
    if dataset.date_semantics != SalesDataset.DateSemantics.SALE_ORDER:
        return []
    imported_refs = _imported_operational_refs(dataset)
    reconciliations = {
        row.order_line_id: row for row in dataset.operational_reconciliations.select_related(
            "imported_line", "reviewed_by"
        )
    }
    rows = []
    for order in _active_confirmed_orders_between(dataset.first_date, dataset.last_date):
        for line in order.lines.exclude(status=OrderLine.Status.REJECTED).order_by("pk"):
            if (str(order.pk), str(line.pk)) in imported_refs or line.pk in reconciliations:
                continue
            rows.append({
                "order": order,
                "line": line,
                "candidates": _line_duplicate_candidates(dataset, order, line),
            })
    return rows


def review_operational_sale(dataset: SalesDataset, user, *, order_line_id: int,
                            decision: str, imported_line_id: int | None = None):
    if not user.is_active or not user.is_staff:
        raise ValueError("La conciliación requiere un administrador activo.")
    if decision not in SalesOperationalReconciliation.Decision.values:
        raise ValueError("Seleccione una decisión de conciliación válida.")
    order = _active_confirmed_orders_between(dataset.first_date, dataset.last_date).filter(
        lines__pk=order_line_id,
    ).first()
    if order is None or order.sale_confirmed_at is None:
        raise ValueError("La venta operativa ya no está confirmada o no pertenece al período.")
    line = order.lines.get(pk=order_line_id)
    if (str(order.pk), str(line.pk)) in _imported_operational_refs(dataset):
        raise ValueError("Esta línea ya está identificada directamente en el archivo.")
    imported_line = None
    if decision == SalesOperationalReconciliation.Decision.DUPLICATE:
        if not imported_line_id:
            raise ValueError("Seleccione la línea importada que representa este pedido.")
        imported_line = _line_duplicate_candidates(dataset, order, line).filter(
            pk=imported_line_id,
        ).first()
        if imported_line is None:
            raise ValueError("La línea importada debe coincidir en cliente, producto, fecha, cantidad y unidad.")
    elif imported_line_id:
        raise ValueError("Una venta independiente no puede enlazarse a una línea importada.")
    result, _ = SalesOperationalReconciliation.objects.update_or_create(
        dataset=dataset, order_line=line,
        defaults={"decision": decision, "imported_line": imported_line,
                  "reviewed_by": user, "reviewed_at": timezone.now()},
    )
    _cached_sale_forecast_rows.cache_clear()
    sales_data_with_confirmed_orders.cache_clear()
    return result
    _cached_sale_forecast_rows.cache_clear()


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


@lru_cache(maxsize=24)
def _cached_sales_data(digest: str, file_path: str, date_semantics: str,
                       provenance: str, coverage_signature: tuple):
    # ponytail: immutable file content and the admin-reviewed coverage signature key the cache.
    source = load_sales(Path(file_path), date_semantics=date_semantics, provenance=provenance)
    rows = source.rows.copy()
    coverage = {}
    unit_intervals = {}
    for client, product, unit, start, end, status in coverage_signature:
        unit_key = unit.strip().casefold()
        if unit_key:
            unit_intervals.setdefault((client, product), []).append((start, end, unit))
        if status == SalesCoverage.Status.COMPLETE and unit_key:
            coverage.setdefault((client, product, unit_key), []).append((start, end))
    if "Unidad" not in rows:
        rows["Unidad"] = ""
    for index, row in rows[rows["Unidad"].fillna("").astype(str).str.strip().eq("")].iterrows():
        matches = {unit for start, end, unit in unit_intervals.get((row["Cliente"], row["Producto"]), ())
                   if start <= row["Fecha"].date() <= end}
        if len(matches) == 1:
            rows.at[index, "Unidad"] = next(iter(matches))
    return SalesData(
        rows, source.digest, source.sheets, source.recalculated_amounts,
        source.normalization_notes, date_semantics=date_semantics,
        provenance=provenance,
        coverage={key: tuple(sorted(intervals)) for key, intervals in coverage.items()},
    )


def sales_data_for(digest: str, file_path: str, dataset_id: int | None = None):
    dataset = SalesDataset.objects.filter(pk=dataset_id).first() if dataset_id else None
    signature = tuple(dataset.coverage.order_by(
        "client", "product", "unit", "start_date", "end_date"
    ).values_list("client", "product", "unit", "start_date", "end_date", "status")) if dataset else ()
    return _cached_sales_data(
        digest, file_path,
        dataset.date_semantics if dataset else SalesDataset.DateSemantics.SALE_ORDER,
        dataset.provenance if dataset else SalesDataset.Provenance.UNKNOWN,
        signature,
    )


def _operational_sales(dataset_id: int, after: date, through: date) -> list[tuple]:
    if through <= after:
        return []
    orders = OrderRequest.objects.filter(
        sale_confirmed=True, sale_confirmed_at__date__gt=after,
        sale_confirmed_at__date__lte=through,
    ).exclude(status__in=(OrderRequest.Status.CANCELLED, OrderRequest.Status.SUPERSEDED)).filter(
        Q(exception_reason="") | Q(commercial_reviewed_at__isnull=False)
    ).prefetch_related("lines")
    rows = []
    for order in orders:
        local_sale_date = timezone.localtime(order.sale_confirmed_at).date()
        for line in order.lines.exclude(status=OrderLine.Status.REJECTED).order_by("pk"):
            rows.append((order.pk, line.pk, order.client, local_sale_date.isoformat(),
                         line.product, str(line.requested_qty), line.unit,
                         str(line.unit_price), str(line.discount_percent)))
    return sorted(rows)


@lru_cache(maxsize=48)
def sales_data_with_confirmed_orders(digest: str, file_path: str, dataset_id: int,
                                     dataset_last_date: date, cutoff: date, operations_key: str):
    """Append confirmed sales once; overlaps need a line-level administrative decision."""
    base = sales_data_for(digest, file_path, dataset_id)
    if base.date_semantics != SalesDataset.DateSemantics.SALE_ORDER:
        return base
    dataset = SalesDataset.objects.get(pk=dataset_id)
    source_rows = _operational_sales(dataset_id, dataset.first_date - timedelta(days=1), cutoff)
    if not source_rows:
        return base
    from datetime import datetime
    import pandas as pd

    catalog = {}
    for row in SalesLine.objects.filter(dataset_id=dataset_id).values(
        "client", "product", "category", "zone", "distribution_channel", "sales_channel"
    ):
        catalog.setdefault((row["client"], row["product"]), row)
    by_line = {line.pk: line for line in OrderLine.objects.filter(
        pk__in=[row[1] for row in source_rows]
    ).select_related("order")}
    reconciliations = {
        row.order_line_id: row for row in SalesOperationalReconciliation.objects.filter(
            dataset=dataset,
        ).select_related("imported_line")
    }
    imported_refs = set(SalesLine.objects.filter(dataset_id=dataset_id).exclude(
        source_order_ref=""
    ).exclude(source_line_ref="").values_list("source_order_ref", "source_line_ref"))
    records = []
    for order_id, line_id, client, day, product, quantity, unit, price, discount in source_rows:
        if (str(order_id), str(line_id)) in imported_refs:
            continue
        row_day = date.fromisoformat(day)
        if dataset.first_date <= row_day <= dataset.last_date:
            reconciliation = reconciliations.get(line_id)
            if (reconciliation is None
                    or reconciliation.decision != SalesOperationalReconciliation.Decision.INDEPENDENT):
                continue
        if not unit:
            continue
        line = by_line[line_id]
        metadata = catalog.get((client, product), {})
        amount = Decimal(quantity) * Decimal(price) * (Decimal("1") - Decimal(discount) / 100)
        records.append({
            "Fecha": pd.Timestamp(datetime.fromisoformat(day)), "Cliente": client,
            "Zona_Geografica": metadata.get("zone") or "No especificado",
            "Canal_Distribucion": metadata.get("distribution_channel") or "No especificado",
            "Canal_Venta": "SmartOrder · venta confirmada",
            "Producto": product, "Categoria": metadata.get("category") or "Sin categoría",
            "Cantidad_kg_unid": float(quantity), "Precio_Unitario_USD": float(price),
            "Monto_Venta_USD": float(amount), "Unidad": unit,
            "Fecha_entrega_solicitada": None, "Fecha_entrega_comprometida": None,
            "Referencia_pedido": str(order_id), "Referencia_linea": str(line_id),
            "Origen_operativo": "operational",
        })
    rows = base.rows.copy()
    if "Origen_operativo" not in rows:
        rows["Origen_operativo"] = "archivo_importado"
    if not records:
        return SalesData(base.rows, base.digest, base.sheets, base.recalculated_amounts,
                         base.normalization_notes + (
                             f"Se excluyeron {len(source_rows)} ventas operativas por falta de unidad, conciliación pendiente o duplicación en el archivo.",
                         ), date_semantics=base.date_semantics, provenance=base.provenance,
                         coverage=base.coverage)
    rows = pd.concat((rows, pd.DataFrame(records)), ignore_index=True)
    combined_digest = sha256(f"{base.digest}:{operations_key}".encode()).hexdigest()
    return SalesData(rows, combined_digest, base.sheets + ("Ventas operativas confirmadas",),
                     base.recalculated_amounts, base.normalization_notes + (
            f"Se agregaron {len(records)} líneas confirmadas independientes o posteriores al archivo; "
                         "cada pedido y línea operativa se identifica para evitar reingresos.",
                     ), date_semantics=base.date_semantics, provenance=base.provenance,
                     coverage=base.coverage)


def forecast_rows(digest: str, file_path: str, delivery: date, verified: bool, today: date,
                  dataset_id: int | None = None):
    from smartorder.recommendations import RecommendationEngine
    source = sales_data_for(digest, file_path, dataset_id)
    return RecommendationEngine(source).for_delivery(
        delivery, source_verified=verified, as_of=today,
    )


def delivery_week_forecast_rows(digest: str, file_path: str, start: date, verified: bool,
                               today: date, dataset_id: int | None = None):
    from smartorder.recommendations import RecommendationEngine
    source = sales_data_for(digest, file_path, dataset_id)
    return RecommendationEngine(source).for_delivery_week(
        start, source_verified=verified, as_of=today,
    )


def operational_overlap_pending(dataset: SalesDataset) -> bool:
    """A quantity stays blocked until every ambiguous operational line is reconciled."""
    return bool(operational_overlap_rows(dataset))


def sale_forecast_rows(digest: str, file_path: str, delivery: date, verified: bool,
                       today: date, event_title: str = "", event_history_dates: tuple = (),
                       dataset_id: int | None = None, dataset_last_date: date | None = None):
    operations_key = ""
    dataset = SalesDataset.objects.filter(pk=dataset_id).first() if dataset_id else None
    evidence_key = {
        "date_semantics": dataset.date_semantics if dataset else SalesDataset.DateSemantics.SALE_ORDER,
        "provenance": dataset.provenance if dataset else SalesDataset.Provenance.UNKNOWN,
        "coverage": list(dataset.coverage.order_by(
            "client", "product", "unit", "start_date", "end_date"
        ).values_list("client", "product", "unit", "start_date", "end_date", "status"))
        if dataset else [],
        "reconciliations": list(dataset.operational_reconciliations.order_by(
            "order_line_id"
        ).values_list("order_line_id", "decision", "imported_line_id", "reviewed_at"))
        if dataset else [],
    }
    if verified and dataset is not None and dataset_last_date is not None:
        operational_rows = _operational_sales(
            dataset_id, dataset.first_date - timedelta(days=1), today - timedelta(days=1),
        )
        evidence_key["overlap_pending"] = operational_overlap_pending(dataset)
        operations_key = sha256(json.dumps(
            {"rows": operational_rows, "evidence": evidence_key}, sort_keys=True, default=str
        ).encode()).hexdigest()
    return _cached_sale_forecast_rows(
        digest, file_path, delivery, verified, today, event_title, event_history_dates,
        dataset_id, dataset_last_date, operations_key,
    )


@lru_cache(maxsize=48)
def _cached_sale_forecast_rows(digest: str, file_path: str, delivery: date, verified: bool,
                               today: date, event_title: str, event_history_dates: tuple,
                               dataset_id: int | None, dataset_last_date: date | None,
                               operations_key: str):
    from smartorder.recommendations import RecommendationEngine
    source = sales_data_for(digest, file_path, dataset_id)
    if verified and dataset_id is not None and dataset_last_date is not None:
        source = sales_data_with_confirmed_orders(
            digest, file_path, dataset_id, dataset_last_date, today - timedelta(days=1),
            operations_key,
        )
    event = ({"title": event_title, "history_dates": event_history_dates}
             if event_title else None)
    return RecommendationEngine(source).for_sale(
        delivery, source_verified=verified, as_of=today, event=event,
    )


def _sale_event(dataset: SalesDataset, client: str, delivery: date,
                event_id: str | int | None = None) -> dict | None:
    zone_names = {zone.strip().casefold() for zone in dataset.lines.filter(
        client=client
    ).exclude(zone="").values_list("zone", flat=True).distinct()}
    national = {"nacional", "national", "el salvador", ""}
    candidates = list(CommercialEvent.objects.filter(
        active=True, reviewed=True, start_date__lte=delivery, end_date__gte=delivery,
    ).order_by("territory", "start_date"))
    eligible = [row for row in candidates
                if row.territory.strip().casefold() in national
                or row.territory.strip().casefold() in zone_names]
    if event_id:
        event = next((row for row in eligible if str(row.pk) == str(event_id)), None)
        if event is None:
            return None
    else:
        event = None
    if event is None:
        event = next((row for row in eligible if row.territory.strip().casefold() in national), None)
    if event is None:
        event = next((row for row in eligible if row.territory.strip().casefold() in zone_names), None)
    if event is None:
        return None
    offset = (delivery - event.start_date).days
    previous = CommercialEvent.objects.filter(
        key=event.key, territory=event.territory, active=True, reviewed=True,
        end_date__lt=event.start_date,
    ).order_by("start_date")
    equivalent_days = tuple(row.start_date + timedelta(days=offset)
                            for row in previous if offset <= (row.end_date - row.start_date).days)
    return {"id": event.pk, "title": event.title, "history_dates": equivalent_days}


def _confirmed_order_lines(start: date, end: date, client: str | None = None,
                           *, committed_only: bool = False):
    orders = OrderRequest.objects.filter(sale_confirmed=True).exclude(
        status__in=(OrderRequest.Status.CANCELLED, OrderRequest.Status.SUPERSEDED)
    ).filter(Q(exception_reason="") | Q(commercial_reviewed_at__isnull=False)).prefetch_related("lines")
    if client is not None:
        orders = orders.filter(client=client)
    for order in orders:
        if committed_only and order.committed_date is None:
            continue
        due = order.committed_date or order.required_date
        if not start <= due <= end:
            continue
        for line in order.lines.exclude(status=OrderLine.Status.REJECTED):
            yield order, line, due


def _dated_flow_commitments(start: date, end: date, batch: InventoryBatch | None,
                            *, client: str | None = None, product: str | None = None,
                            date_semantics: str | None = None):
    flows = DatedFlow.objects.filter(
        kind=DatedFlow.Kind.CUSTOMER_COMMITMENT, due_date__range=(start, end),
    ).select_related("related_order_line")
    flows = flows.filter(Q(batch=batch) | Q(batch__isnull=True)) if batch else flows.filter(batch__isnull=True)
    if client is not None:
        flows = flows.filter(client=client)
    if product is not None:
        flows = flows.filter(product=product)
    if date_semantics is not None:
        flows = flows.filter(date_semantics=date_semantics)
    amounts, units, ambiguous, references = {}, {}, set(), {}
    for flow in flows.order_by("due_date", "pk"):
        key = (flow.client, flow.product)
        if not flow.confirmed:
            ambiguous.add(key)
            continue
        if flow.reconciliation == DatedFlow.Reconciliation.DUPLICATE:
            if flow.related_order_line_id is None:
                ambiguous.add(key)
            continue
        if flow.reconciliation != DatedFlow.Reconciliation.UNIQUE:
            ambiguous.add(key)
            continue
        identity = flow.source_reference.strip()
        value = (flow.quantity, flow.unit.strip().casefold(), flow.due_date)
        if identity and identity in references:
            if references[identity] != (key, value):
                ambiguous.add(key)
            continue
        if identity:
            references[identity] = (key, value)
        amounts[key] = amounts.get(key, Decimal("0")) + flow.quantity
        units.setdefault(key, set()).add(flow.unit.strip().casefold())
    return amounts, units, ambiguous


def _dated_flow_incoming(start: date, end: date, batch: InventoryBatch | None,
                         location_by_client: dict[str, int]):
    flows = DatedFlow.objects.filter(
        kind=DatedFlow.Kind.INCOMING, due_date__range=(start, end),
    ).select_related("related_order_line")
    flows = flows.filter(Q(batch=batch) | Q(batch__isnull=True)) if batch else flows.filter(batch__isnull=True)
    amounts, units, ambiguous, references = {}, {}, set(), {}
    by_reference, without_reference = {}, []
    for flow in flows.order_by("due_date", "pk"):
        location_id = location_by_client.get(flow.client)
        if location_id is None:
            continue
        key = (location_id, flow.product)
        if not flow.confirmed or flow.reconciliation in {
            DatedFlow.Reconciliation.PENDING, DatedFlow.Reconciliation.AMBIGUOUS,
        }:
            ambiguous.add(key)
            continue
        signature = (flow.quantity, flow.unit.strip().casefold(), flow.due_date)
        identity = (key, flow.source_reference.strip().casefold()) if flow.source_reference.strip() else None
        if identity:
            by_reference.setdefault(identity, []).append((flow, signature))
        else:
            without_reference.append((key, flow, signature))

    def add(key, quantity, unit):
        amounts[key] = amounts.get(key, Decimal("0")) + quantity
        units.setdefault(key, set()).add(unit)

    for identity, entries in by_reference.items():
        key, _ = identity
        signatures = {signature for _, signature in entries}
        if len(signatures) != 1:
            ambiguous.add(key)
            continue
        signature = next(iter(signatures))
        if not any(flow.reconciliation == DatedFlow.Reconciliation.UNIQUE
                   for flow, _ in entries):
            ambiguous.add(key)
            continue
        add(key, signature[0], signature[1])
        references[identity] = signature

    for key, flow, signature in without_reference:
        if flow.reconciliation != DatedFlow.Reconciliation.UNIQUE:
            ambiguous.add(key)
            continue
        add(key, signature[0], signature[1])
    return amounts, units, ambiguous, references


def recommendation_cards(dataset: SalesDataset, delivery: date, client: str,
                         today: date | None = None, mode: str = "sale",
                         event_id: str | int | None = None) -> list[dict]:
    today = today or timezone.localdate()
    source_verified = bool(
        dataset.verified and dataset.full_snapshot_confirmed
        and not operational_overlap_pending(dataset)
    )
    if mode == "sale":
        if delivery > today + timedelta(days=7):
            rows = [{"Cliente": owner, "Producto": product, "forecast": None,
                     "quantity": None, "eligible_for_auto": False, "method": "Revisión manual",
                     "evidence_status": "outside_horizon",
                     "evidence": "La entrega está fuera del horizonte de siete días; indique cantidad manual.",
                     "suggestion_event": ""}
                    for owner, product in dataset.lines.filter(client=client)
                    .values_list("client", "product").distinct()] if client else []
        elif dataset.date_semantics == SalesDataset.DateSemantics.DELIVERY:
            frame = forecast_rows(
                dataset.digest, dataset.file.path, delivery, source_verified, today, dataset.pk,
            )
            rows = frame[frame["Cliente"] == client].to_dict("records") if client else frame.to_dict("records")
        else:
            event = _sale_event(dataset, client, delivery, event_id) if client else None
            frame = sale_forecast_rows(
                dataset.digest, dataset.file.path, delivery,
                source_verified, today,
                event["title"] if event else "",
                tuple(event["history_dates"]) if event else (),
                dataset.pk, dataset.last_date,
            )
            rows = frame[frame["Cliente"] == client].to_dict("records") if client else frame.to_dict("records")
            for row in rows:
                row["commercial_forecast"] = row.get("commercial_forecast") or row.get("forecast")
                row["forecast"] = None
                row["quantity"] = None
                row["eligible_for_auto"] = False
                row["evidence_status"] = "incompatible_dates"
                row["method"] = "Revisión manual"
                row["evidence"] = (
                    "El archivo contiene fechas de venta o pedido, no de entrega. "
                    "La cantidad para esta entrega se confirma manualmente. "
                    + str(row.get("evidence") or "")
                )
    elif mode == "client_restock":
        rows = []
        target = RestockTarget.objects.filter(
            scope=RestockTarget.Scope.CLIENT, owner_key=f"client:{client}"
        )
        products = set(dataset.lines.filter(client=client).values_list("product", flat=True).distinct())
        products.update(target.values_list("product", flat=True))
        products.update(ClientStockCount.objects.filter(client=client).values_list("product", flat=True).distinct())
        products.update(ClientStockMovement.objects.filter(client=client).values_list("product", flat=True).distinct())
        batch = active_inventory()
        flows, flow_units, ambiguous_flows = _dated_flow_commitments(
            today + timedelta(days=1), delivery, batch, client=client,
            date_semantics=DatedFlow.DateSemantics.COMMITTED,
        ) if delivery >= today + timedelta(days=1) else ({}, {}, set())
        same_day_flows = DatedFlow.objects.filter(
            kind=DatedFlow.Kind.CUSTOMER_COMMITMENT, confirmed=True, due_date=today,
            date_semantics=DatedFlow.DateSemantics.COMMITTED, client=client,
        ).filter(Q(batch=batch) | Q(batch__isnull=True)) if batch else DatedFlow.objects.filter(
            kind=DatedFlow.Kind.CUSTOMER_COMMITMENT, confirmed=True, due_date=today,
            date_semantics=DatedFlow.DateSemantics.COMMITTED, batch__isnull=True, client=client,
        )
        same_day_flow_products = set(same_day_flows.values_list("product", flat=True))
        order_receipts, order_units = {}, {}
        for order, line, due in _confirmed_order_lines(
                today + timedelta(days=1), delivery, client, committed_only=True):
            order_receipts[line.product] = order_receipts.get(line.product, Decimal("0")) + line.requested_qty
            order_units.setdefault(line.product, set()).add(line.unit.strip().casefold())
        movement_rows = list(ClientStockMovement.objects.filter(
            client=client, effective_date__gte=today, effective_date__lte=delivery,
        ).select_related("related_order_line__order").order_by("effective_date", "pk")) if delivery >= today else []
        same_day_order_products = {line.product for _, line, _ in _confirmed_order_lines(
            today, today, client, committed_only=True,
        )}
        for product in products:
            if delivery > today + timedelta(days=7):
                rows.append({
                    "Cliente": client, "Producto": product, "forecast": None,
                    "quantity": None, "eligible_for_auto": False, "unit_confirmed": False,
                    "evidence_status": "outside_horizon",
                    "method": "Revisión manual", "evidence":
                    "La reposición fuera de siete días requiere revisión manual.",
                })
                continue
            latest_by_location = {}
            for count in ClientStockCount.objects.filter(client=client, product=product).order_by(
                "location", "-observed_on", "-created_at", "-pk"
            ):
                latest_by_location.setdefault(count.location.strip().casefold(), count)
            location_counts = list(latest_by_location.values())
            goal = target.filter(product=product).first()
            units_match = bool(location_counts and goal and all(
                count.observed_on == today and count.unit.strip().casefold() == goal.unit.strip().casefold()
                and count.unit.strip()
                for count in location_counts
            ))
            current = sum((count.available for count in location_counts), Decimal("0")) if units_match else None
            movements = [movement for movement in movement_rows if movement.product == product]
            count_locations = set(latest_by_location)
            confirmed_movements = [movement for movement in movements if movement.confirmed]

            def movement_resolved(movement):
                if (goal is None or movement.unit.strip().casefold() != goal.unit.strip().casefold()
                        or movement.location.strip().casefold() not in count_locations):
                    return False
                if (movement.reconciliation == DatedFlow.Reconciliation.UNIQUE
                        and movement.related_order_line_id is None):
                    return True
                if (movement.reconciliation != DatedFlow.Reconciliation.DUPLICATE
                        or movement.related_order_line_id is None
                        or movement.kind != ClientStockMovement.Kind.INCOMING):
                    return False
                line = movement.related_order_line
                order = line.order
                return bool(
                    line.status != OrderLine.Status.REJECTED
                    and order.sale_confirmed
                    and order.status not in (OrderRequest.Status.CANCELLED, OrderRequest.Status.SUPERSEDED)
                    and (not order.exception_reason or order.commercial_reviewed_at is not None)
                    and order.client == client and line.product == product
                    and order.committed_date == movement.effective_date
                    and line.requested_qty == movement.quantity
                    and line.unit.strip().casefold() == movement.unit.strip().casefold()
                )

            movement_units_match = bool(goal) and all(
                movement_resolved(movement) for movement in confirmed_movements
            )
            movement_ambiguous = any(
                movement.effective_date == today or not movement_resolved(movement)
                for movement in confirmed_movements
            )
            order_incoming = order_receipts.get(product, Decimal("0"))
            imported_incoming = flows.get((client, product), Decimal("0"))
            incoming_units = order_units.get(product, set()) | flow_units.get((client, product), set())
            if goal and any(unit != goal.unit.strip().casefold() for unit in incoming_units):
                movement_units_match = False
            flow_ambiguous = ((client, product) in ambiguous_flows
                              or product in same_day_flow_products
                              or product in same_day_order_products)
            order_flow_ambiguous = bool(incoming_units and "" in incoming_units)
            units_match = units_match and movement_units_match
            external_incoming = sum((movement.quantity for movement in confirmed_movements
                                     if movement.kind == ClientStockMovement.Kind.INCOMING
                                     and movement.effective_date > today
                                     and movement.reconciliation == DatedFlow.Reconciliation.UNIQUE
                                     and movement.related_order_line_id is None), Decimal("0"))
            outgoing = sum((movement.quantity for movement in confirmed_movements
                            if movement.kind == ClientStockMovement.Kind.OUTGOING
                            and movement.effective_date > today
                            and movement.reconciliation == DatedFlow.Reconciliation.UNIQUE
                            and movement.related_order_line_id is None), Decimal("0"))
            incoming = order_incoming + imported_incoming + external_incoming
            ambiguous = flow_ambiguous or order_flow_ambiguous or movement_ambiguous
            projected = max(Decimal("0"), current + incoming - outgoing) if units_match else None
            amount = max(Decimal("0"), goal.target_quantity - projected) if units_match and goal else None
            complete = bool(units_match and not ambiguous)
            stock_label = " · ".join(
                f"{count.available:g} {count.unit} en {count.location}"
                for count in location_counts
            )
            rows.append({
                "Cliente": client, "Producto": product,
                "forecast": amount if complete else None, "quantity": amount if complete else None,
                "eligible_for_auto": amount is not None and complete,
                "unit": goal.unit if units_match else (location_counts[0].unit if location_counts else ""),
                "unit_confirmed": units_match,
                "evidence_status": "validated" if amount is not None and complete else "manual",
                "method": "Diferencia frente al objetivo de existencias" if amount is not None else "Revisión manual",
                "evidence": (
                    f"Conteo de hoy: {current:g} {goal.unit}; pedidos independientes: {order_incoming:g}; "
                    f"flujos conciliados: {imported_incoming:g}; movimientos confirmados: "
                    f"{external_incoming:g}; "
                    f"salidas confirmadas: {outgoing:g}; stock proyectado para {delivery:%d/%m/%Y}: "
                    f"{projected:g}; objetivo: {goal.target_quantity:g} {goal.unit}."
                    if amount is not None and complete else
                    f"Brecha parcial conocida {amount:g} {goal.unit}; hay compromisos o movimientos sin conciliar."
                    if amount is not None and ambiguous else
                    "Se requiere un conteo vigente en cada ubicación conocida, un objetivo y movimientos "
                    "confirmados con unidades compatibles."
                ),
                "client_stock": current,
                "client_stock_location": stock_label,
                "client_stock_target": goal.target_quantity if goal else None,
                "client_stock_projected": projected,
                "client_stock_incoming": incoming if units_match else None,
                "client_stock_outgoing": outgoing if units_match else None,
            })
    elif mode == "vitali":
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
    else:
        raise ValueError("Modo de sugerencia desconocido.")
    batch = active_inventory()
    inventory = {
        (item.client, item.product): item
        for item in InventoryItem.objects.filter(batch=batch)
    } if batch else {}
    adjustments = {}
    if batch:
        for change in InventoryAdjustment.objects.filter(batch=batch).order_by("created_at", "pk"):
            adjustments[(change.client, change.product)] = change
    flows = list(DatedFlow.objects.filter(
        batch=batch, confirmed=True, reconciliation=DatedFlow.Reconciliation.UNIQUE,
    )) if batch else []
    commercial = {
        row["product"]: row
        for row in dataset.lines.filter(client=client).values("product").annotate(
            revenue=Sum("amount"), days_with_sale=Count("date", distinct=True)
        )
    }
    tariffs = {
        row.product: row for row in PriceTariff.objects.filter(client=client)
    }
    categories = {}
    for product, category in dataset.lines.filter(client=client).values_list(
        "product", "category"
    ).distinct():
        categories.setdefault(product, category)
    cards = []
    delivery_outside_forecast = (
        mode == "vitali" and (delivery <= dataset.last_date or delivery > today + timedelta(days=7))
    ) or (mode == "sale" and delivery > today + timedelta(days=7))
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
        predicted = row.get("quantity")
        if mode == "client_restock":
            predicted = row.get("quantity")
        eligible = bool(row.get("eligible_for_auto")) and predicted is not None
        stock_current = stock_date == today and (not batch or batch.source != "odoo" or
            bool(batch.observed_at and timezone.now() - batch.observed_at <= timedelta(minutes=15)))
        tariff = tariffs.get(row["Producto"])
        unit = row.get("Unidad") or row.get("unit") or ""
        if not unit and mode in {"vitali", "client_restock"} and item and item.unit:
            unit = item.unit
        stock_unit_confirmed = bool(batch and batch.unit_match_confirmed and item and item.unit)
        units_confirmed = bool(row.get("unit_confirmed") or row.get("Unidad"))
        metrics = commercial.get(row["Producto"], {})
        evaluation = None
        if "audit_coverage" in row:
            evaluation = {
                "automatic": eligible,
                "windows": int(row.get("audit_windows") or 0),
                "targets": int(row.get("audit_targets") or 0),
                "coverage": row.get("audit_coverage"),
                "mae": row.get("audit_mae"),
                "baseline_mae": row.get("baseline_mae"),
                "overforecast": row.get("audit_overforecast"),
                "baseline_overforecast": row.get("baseline_overforecast"),
                "event": row.get("suggestion_event", ""),
            }
        reference_start = delivery.replace(year=delivery.year - 1) if (
            delivery.month != 2 or delivery.day != 29
        ) else delivery.replace(year=delivery.year - 1, day=28)
        reference_period = (
            f"{reference_start:%d/%m/%Y}–{reference_start + timedelta(days=6):%d/%m/%Y}"
            if row.get("reference_previous_year_7d") is not None else ""
        )
        suggested = None
        if mode == "sale" and dataset.date_semantics == SalesDataset.DateSemantics.DELIVERY and eligible and units_confirmed:
            suggested = nonnegative_decimal(predicted, "Pronóstico")
        elif mode == "client_restock" and eligible:
            suggested = nonnegative_decimal(predicted, "Reposición sugerida")
        elif mode == "vitali" and eligible and stock_current and units_confirmed:
            forecast = nonnegative_decimal(predicted, "Pronóstico")
            suggested = forecast
        cards.append({
            "client": row["Cliente"], "product": row["Producto"],
            "category": categories.get(row["Producto"], ""),
            "sales_usd_display": f"${metrics.get('revenue', Decimal('0')):,.2f}",
            "days_with_sale": metrics.get("days_with_sale", 0),
            "reference_period": reference_period,
            "unit_confirmed": units_confirmed,
            "stock_unit": item.unit if item and item.unit else "",
            "state": "suggested" if suggested is not None else (
                "reference" if row.get("reference_previous_year_7d") is not None else "manual"
            ),
            "forecast": predicted if eligible else None,
            "commercial_forecast": row.get("commercial_forecast"),
            "quantity": suggested,
            "mode": mode,
            "target_date": row.get("target_date", delivery),
            "cutoff_date": row.get("cutoff_date", today - timedelta(days=1)),
            "evidence_status": row.get("evidence_status", "manual"),
            "authorized_reference": ({
                "date": row.get("reference_date"),
                "quantity": row.get("reference_qty"),
                "unit": unit,
            } if row.get("reference_date") and row.get("reference_qty") is not None else None),
            "delivery_outside_forecast": delivery_outside_forecast,
            "method": row.get("method", ""),
            "reference_date": row.get("reference_date"),
            "reference_qty": row.get("reference_qty"),
            "suggestion_event": row.get("suggestion_event", ""),
            "sale_evaluation": evaluation,
            "client_stock": row.get("client_stock"),
            "client_stock_location": row.get("client_stock_location", ""),
            "client_stock_target": row.get("client_stock_target"),
            "client_stock_projected": row.get("client_stock_projected"),
            "client_stock_incoming": row.get("client_stock_incoming"),
            "client_stock_outgoing": row.get("client_stock_outgoing"),
            "suggestion_mode": mode,
            "explanation": " ".join(filter(None, [
                row.get("evidence"), row.get("explanation"),
                ("La unidad necesaria para este cálculo no está confirmada." if not units_confirmed else ""),
            ])),
            "suggested": suggested,
            "unit": unit,
            "order_unit": tariff.unit if tariff else "",
            "target_kind": row.get("target_kind", mode),
            "available": available,
            "stock_date": stock_date,
            "incoming": incoming,
            "commitments_before": commitments_before,
            "commitments": commitments,
            "inventory_warning": (
                "Disponibilidad pendiente de validar; puedes registrar la cantidad bajo pedido."
                if not stock_current else
                "Administración aún no confirmó la unidad del inventario."
                if not stock_unit_confirmed else ""
            ),
        })
    return sorted(cards, key=lambda card: (
        card["suggested"] is None,
        -(card["suggested"] or Decimal("0")),
        -commercial.get(card["product"], {}).get("revenue", Decimal("0")),
        card["product"].casefold(),
    ))


def vitali_restock_plan(dataset: SalesDataset | None, today: date | None = None) -> list[dict]:
    """Plan a seven-day delivery period; a suggestion never authorizes production."""
    today = today or timezone.localdate()
    end = today + timedelta(days=6)
    from smartorder.recommendations import RecommendationEngine

    assignment = dict(OdooCustomerMapping.objects.exclude(location_id__isnull=True).values_list(
        "client", "location_id"
    ))
    batch = active_inventory()
    demand = {}
    forecasts = {}
    commitments = {}
    units = {}
    ambiguous = set()
    forecasted = set()
    expected = set()
    cutoff = today - timedelta(days=1)
    evidence_end = None
    engine = None
    if dataset:
        source = sales_data_for(dataset.digest, dataset.file.path, dataset.pk)
        engine = RecommendationEngine(source)
        closed_dates = []
        for value in source.rows.get("Fecha", []):
            day = value.date() if hasattr(value, "date") else value
            if day <= cutoff:
                closed_dates.append(day)
        closed_dates.extend(min(last, cutoff) for intervals in source.coverage.values()
                            for start, last in intervals if start <= cutoff)
        evidence_end = max(closed_dates, default=None)
    source_ready = bool(
        dataset and dataset.verified and dataset.full_snapshot_confirmed
        and dataset.date_semantics == SalesDataset.DateSemantics.DELIVERY
        and evidence_end is not None and 0 <= (cutoff - evidence_end).days <= 7
        and not operational_overlap_pending(dataset)
    )
    if engine:
        for client, product, unit in engine.available_series(cutoff):
            expected.add((client, product))
            if unit.strip():
                units.setdefault((client, product), set()).add(unit)
    if source_ready:
        # ponytail: one seven-day target window avoids adding seven overlapping weekly forecasts.
        frame = engine.for_delivery_week(today, source_verified=True, as_of=today)
        for row in frame.to_dict("records"):
            key = (row["Cliente"], row["Producto"])
            if row.get("eligible_for_auto") and row.get("forecast") is not None:
                forecasts[key] = Decimal(str(row["forecast"]))
                forecasted.add(key)

    for order, line, due in _confirmed_order_lines(today + timedelta(days=1), end):
        key = (order.client, line.product)
        commitments[key] = commitments.get(key, Decimal("0")) + line.requested_qty
        units.setdefault(key, set()).add(line.unit.strip().casefold())
    flow_amounts, flow_units, flow_ambiguous = _dated_flow_commitments(
        today + timedelta(days=1), end, batch,
    )
    for key, quantity in flow_amounts.items():
        commitments[key] = commitments.get(key, Decimal("0")) + quantity
    for key, values in flow_units.items():
        units.setdefault(key, set()).update(values)
    ambiguous.update(flow_ambiguous)
    same_day_orders = {(order.client, line.product) for order, line, _ in
                       _confirmed_order_lines(today, today)}
    same_day_flows = DatedFlow.objects.filter(
        kind=DatedFlow.Kind.CUSTOMER_COMMITMENT, due_date=today,
    ).filter(Q(batch=batch) | Q(batch__isnull=True)) if batch else DatedFlow.objects.filter(
        kind=DatedFlow.Kind.CUSTOMER_COMMITMENT, due_date=today, batch__isnull=True,
    )
    ambiguous.update(same_day_orders)
    ambiguous.update((row.client, row.product) for row in same_day_flows)

    # A delivery forecast estimates total period demand, so confirmed commitments are a floor,
    # not an additive second forecast of those same deliveries.
    for key in set(forecasts) | set(commitments):
        demand[key] = max(forecasts.get(key, Decimal("0")), commitments.get(key, Decimal("0")))

    location_demand = {}
    location_units = {}
    location_keys = {}
    location_ambiguous = set()
    unassigned = {}
    for key in expected:
        location_id = assignment.get(key[0])
        if location_id is not None:
            location_keys.setdefault((location_id, key[1]), set()).add(key)
    for key, quantity in demand.items():
        location_id = assignment.get(key[0])
        if location_id is None:
            unassigned[key[1]] = unassigned.get(key[1], Decimal("0")) + quantity
            continue
        pair = (location_id, key[1])
        location_demand[pair] = location_demand.get(pair, Decimal("0")) + quantity
        location_units.setdefault(pair, set()).update(units.get(key, set()))
        location_keys.setdefault(pair, set()).add(key)
        if key in ambiguous:
            location_ambiguous.add(pair)
    for client, product in ambiguous:
        location_id = assignment.get(client)
        if location_id is not None:
            location_ambiguous.add((location_id, product))

    counts = {}
    for row in LocationStockSnapshot.objects.order_by(
        "location_id", "product", "-observed_on", "-created_at", "-pk"
    ):
        counts.setdefault((row.location_id, row.product), row)
    targets = {
        (int(row.owner_key.partition(":")[2]), row.product): row
        for row in RestockTarget.objects.filter(scope=RestockTarget.Scope.LOCATION,
                                                 owner_key__startswith="location:")
    }
    incoming_by_unit = {}
    incoming_units = {}
    incoming_ambiguous = set()
    incoming_references = {}
    flow_receipts, flow_receipt_units, flow_receipt_ambiguous, flow_receipt_refs = _dated_flow_incoming(
        today + timedelta(days=1), end, batch, assignment,
    )
    for pair, quantity in flow_receipts.items():
        accepted_units = flow_receipt_units.get(pair, set())
        if len(accepted_units) != 1:
            incoming_ambiguous.add(pair)
            continue
        unit_key = next(iter(accepted_units))
        incoming_units.setdefault(pair, set()).add(unit_key)
        incoming_by_unit[(pair, unit_key)] = incoming_by_unit.get((pair, unit_key), Decimal("0")) + quantity
    incoming_references.update(flow_receipt_refs)
    incoming_ambiguous.update(flow_receipt_ambiguous)
    same_day_receipts = DatedFlow.objects.filter(kind=DatedFlow.Kind.INCOMING, due_date=today)
    same_day_receipts = same_day_receipts.filter(Q(batch=batch) | Q(batch__isnull=True)) if batch else same_day_receipts.filter(batch__isnull=True)
    incoming_ambiguous.update((assignment[row.client], row.product)
                              for row in same_day_receipts if assignment.get(row.client) is not None)
    for row in LocationIncoming.objects.filter(confirmed=True, due_date__range=(today, end)):
        pair = (row.location_id, row.product)
        unit = row.unit.strip().casefold()
        incoming_units.setdefault(pair, set()).add(unit)
        if row.due_date == today:
            incoming_ambiguous.add(pair)
            continue
        if row.reference:
            identity = (pair, row.reference.strip().casefold())
            signature = (row.quantity, unit, row.due_date)
            if identity in incoming_references:
                if incoming_references[identity] != signature:
                    incoming_ambiguous.add(pair)
                continue
            incoming_references[identity] = signature
        incoming_by_unit[(pair, unit)] = incoming_by_unit.get((pair, unit), Decimal("0")) + row.quantity

    result = []
    all_keys = set(counts) | set(targets) | set(incoming_units) | set(location_demand)
    for pair in sorted(all_keys):
        location_id, product = pair
        stock, goal = counts.get(pair), targets.get(pair)
        unit = (stock.unit if stock else goal.unit if goal else "").strip()
        unit_key = unit.casefold()
        unit_confirmed = bool(
            unit_key and stock and goal and stock.unit.strip().casefold() == goal.unit.strip().casefold()
            and (not goal.unit.strip() or goal.unit.strip().casefold() == unit_key)
        )
        observed_units = location_units.get(pair, set()) | incoming_units.get(pair, set())
        if observed_units and ("" in observed_units or observed_units != {unit_key}):
            unit_confirmed = False
        current = stock.available if stock and stock.observed_on == today else None
        receipts = incoming_by_unit.get((pair, unit_key), Decimal("0")) if unit_confirmed else Decimal("0")
        target_buffer = goal.target_quantity if goal and unit_confirmed else Decimal("0")
        demand_qty = location_demand.get(pair, Decimal("0"))
        need = (max(Decimal("0"), demand_qty + target_buffer - current - receipts)
                if current is not None and goal and unit_confirmed else None)
        series = location_keys.get(pair, set())
        forecast_complete = bool(source_ready and series and series <= forecasted)
        auto = bool(need is not None and forecast_complete and pair not in location_ambiguous
                    and pair not in incoming_ambiguous)
        known_reference = next((
            (order.pk, line.pk) for order, line, _ in _confirmed_order_lines(today, end)
            if assignment.get(order.client) == location_id and line.product == product
        ), None)
        result.append({
            "location_id": location_id,
            "location": stock.location_name if stock else (goal.owner_key if goal else f"Ubicación {location_id}"),
            "product": product, "unit": unit, "current": current,
            "target": goal.target_quantity if goal else None, "demand": demand_qty,
            "incoming": receipts, "need": need, "suggested": need if auto else None,
            "quantity": need if auto else None, "mode": "vitali_restock",
            "target_date": end, "cutoff_date": today - timedelta(days=1),
            "evidence_status": "validated" if auto else "manual",
            "authorized_reference": {"order": known_reference[0], "line": known_reference[1]}
            if known_reference else None,
            "unit_confirmed": unit_confirmed, "state": "plan" if auto else "manual",
            "explanation": (
                f"Período {today:%d/%m}–{end:%d/%m}: demanda {demand_qty:g} + reserva final "
                f"{target_buffer:g} − conteo físico {current:g} − entradas confirmadas {receipts:g}."
                if auto else
                f"Brecha parcial conocida {need:g} {unit}; compromisos o entradas pendientes de conciliar."
                if need is not None and (pair in location_ambiguous or pair in incoming_ambiguous) else
                f"Brecha parcial conocida {need:g} {unit}; no hay cobertura evaluada del período completo."
                if need is not None and not forecast_complete else
                "Revisión manual: falta conteo de hoy, objetivo, unidad compatible o conciliación de compromisos."
            ),
        })
    for product, quantity in sorted(unassigned.items()):
        result.append({
            "location_id": None, "location": "Sin ubicación asignada", "product": product,
            "unit": "", "current": None, "target": None, "demand": quantity,
            "incoming": Decimal("0"), "need": None, "suggested": None, "quantity": None,
            "mode": "vitali_restock", "target_date": end, "cutoff_date": today - timedelta(days=1),
            "evidence_status": "manual", "authorized_reference": None,
            "unit_confirmed": False, "state": "manual",
            "explanation": "Hay demanda confirmada de clientes sin ubicación Vitali asignada.",
        })
    return result


def top_products_for(dataset: SalesDataset, clients: list[str] | None = None,
                     limit: int = 8) -> list[dict]:
    lines = dataset.lines.all()
    if clients is not None:
        lines = lines.filter(client__in=clients)
    rows = lines.values("product").annotate(revenue=Sum("amount")).order_by("-revenue")[:limit]
    return [{"product": row["product"], "revenue": row["revenue"]} for row in rows]
