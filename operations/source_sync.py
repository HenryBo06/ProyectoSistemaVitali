"""Bounded Odoo reads, validated snapshots and recoverable order refreshes."""
from datetime import timedelta
from decimal import Decimal, ROUND_DOWN
from hashlib import sha256
import json

from django.core.files.base import ContentFile
from django.db import IntegrityError, transaction
from django.db.models import F
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from . import odoo
from .models import (Assignment, DatedFlow, InventoryBatch, InventoryItem,
    OdooCustomerMapping, OdooProductMapping, OrderRequest, SourceSync)
from .services import nonnegative_decimal


def moment(value):
    parsed = parse_datetime(value) if isinstance(value, str) else None
    if parsed is None:
        raise ValueError("La fuente no contiene una fecha de corte válida.")
    return timezone.make_aware(parsed, timezone.get_fixed_timezone(0)) if timezone.is_naive(parsed) else parsed


def quantity(value):
    result = nonnegative_decimal(value, "Cantidad de origen")
    if result != result.quantize(Decimal("0.001")):
        raise ValueError("La fuente tiene cantidades con más de tres decimales; revise la conversión.")
    return result


def allocate(total, clients, unit):
    # ponytail: approved location mapping implies equal planning quotas, never reservations.
    # Custom allocations require a separately approved policy before a real pilot.
    step = Decimal("1") if unit == "unid" else Decimal("0.001")
    if total % step:
        raise ValueError("Las unidades enteras no pueden repartirse como fracciones.")
    share = (total / len(clients) / step).to_integral_value(rounding=ROUND_DOWN) * step
    return [(client, share if index < len(clients) - 1 else total - share * (len(clients) - 1))
            for index, client in enumerate(sorted(clients))]


def validate_source(raw, customers, products):
    if not isinstance(raw, dict):
        raise ValueError("La respuesta de inventario no es válida.")
    observed = moment(raw.get("observed_at"))
    if observed > timezone.now() + timedelta(minutes=2) or observed < timezone.now() - timedelta(minutes=15):
        raise ValueError("El corte recibido está vencido o en el futuro.")
    expected_customers = {row.code for row in customers}
    expected_products = {row.sku: row for row in products}
    locations = {row.location_id for row in customers}
    for name, key, expected in (("customers", "code", expected_customers), ("products", "sku", set(expected_products)), ("locations", "id", locations)):
        values = raw.get(name)
        if not isinstance(values, list) or len(values) != len(expected) or any(not isinstance(row, dict) for row in values) or {row.get(key) for row in values} != expected:
            raise ValueError("El catálogo de origen no coincide con las equivalencias aprobadas.")
    for product in raw["products"]:
        mapping = expected_products[product["sku"]]
        if product.get("unit") != mapping.unit or (mapping.unit, mapping.source_unit) not in {
                ("uom.product_uom_kgm", "kg"), ("uom.product_uom_unit", "unid")}:
            raise ValueError("La unidad de origen no coincide con la equivalencia aprobada.")
    pairs = {(loc, sku) for loc in locations for sku in expected_products}
    stocks = raw.get("stocks")
    if not isinstance(stocks, list) or len(stocks) != len(pairs):
        raise ValueError("La lectura de existencias está incompleta.")
    by_location = {loc: [row.client for row in customers if row.location_id == loc] for loc in locations}
    items, seen = [], set()
    for row in stocks:
        pair = (row["location_id"], row["sku"])
        if pair not in pairs or pair in seen:
            raise ValueError("Existencia repetida o ubicación fuera de ámbito.")
        seen.add(pair)
        available, reserved = quantity(row["available"]), quantity(row["reserved"])
        physical = Decimal(str(row["quantity"]))
        if not physical.is_finite() or abs(physical) > Decimal("999999999999999") or available != max(Decimal(0), physical - reserved):
            raise ValueError("Existencia disponible y reservas no concilian.")
        mapping = expected_products[pair[1]]
        for client, share in allocate(available, by_location[pair[0]], mapping.source_unit):
            items.append(dict(client=client, product=mapping.product, unit=mapping.source_unit,
                              available=share, observed_on=timezone.localtime(observed).date()))
    flows, seen = [], set()
    movements = raw.get("movements")
    if not isinstance(movements, list) or len(movements) > 10000:
        raise ValueError("La lista de movimientos no es válida.")
    for row in movements:
        key = (row["id"], row["location_id"], row["kind"])
        if type(row["id"]) is not int or row["id"] <= 0 or key in seen or row["location_id"] not in locations or row["sku"] not in expected_products or row["kind"] not in DatedFlow.Kind.values:
            raise ValueError("Movimiento repetido o fuera de ámbito.")
        seen.add(key)
        mapping = expected_products[row["sku"]]
        due = timezone.localtime(moment(row["due_at"])).date()
        for client, share in allocate(quantity(row["quantity"]), by_location[row["location_id"]], mapping.source_unit):
            if share:
                flows.append(dict(client=client, product=mapping.product, unit=mapping.source_unit,
                                    quantity=share, due_date=due, kind=row["kind"],
                                    date_semantics=(DatedFlow.DateSemantics.EXPECTED_INCOMING
                                                    if row["kind"] == DatedFlow.Kind.INCOMING
                                                    else DatedFlow.DateSemantics.COMMITTED),
                                    source_reference=f"{row['id']}:{client}",
                                    reconciliation=DatedFlow.Reconciliation.PENDING))
    return observed, items, flows


def synchronize_sources(user, *, refresh_orders=True):
    if not user.is_active or not user.is_staff:
        raise ValueError("La sincronización requiere un administrador activo.")
    SourceSync.objects.filter(status="running", started_at__lt=timezone.now() - timedelta(minutes=30)).update(
        status="error", error="Ejecución interrumpida; se permite reintento.", completed_at=timezone.now())
    try:
        with transaction.atomic():
            run = SourceSync.objects.create()
    except IntegrityError as exc:
        raise ValueError("Ya hay una sincronización en curso.") from exc
    raw, saved_file = None, None
    try:
        customers = list(OdooCustomerMapping.objects.order_by("client"))
        products = list(OdooProductMapping.objects.order_by("product"))
        assigned = set(Assignment.objects.values_list("client", flat=True))
        if not customers or not products or any(not row.location_id for row in customers) or not assigned.issubset({row.client for row in customers}):
            raise ValueError("Revise las equivalencias y asigne una ubicación explícita a cada cliente de la cartera.")
        raw = odoo.rpc("read_sources", customer_codes=[row.code for row in customers],
                       product_skus=[row.sku for row in products], location_ids=sorted({row.location_id for row in customers}))
        observed, items, flows = validate_source(raw, customers, products)
        payload = json.dumps(raw, sort_keys=True, ensure_ascii=True, allow_nan=False).encode()
        fingerprint = sha256(payload).hexdigest()
        with transaction.atomic():
            batch = InventoryBatch.objects.filter(digest=fingerprint).first()
            if batch is None:
                InventoryBatch.objects.filter(active=True).update(active=False)
                batch = InventoryBatch(source="odoo", observed_at=observed, digest=fingerprint,
                    filename="Inventario Odoo " + observed.isoformat(), active=True,
                    unit_match_confirmed=True, uploaded_by=user,
                    notes="Cuotas iguales de planificación por ubicación aprobada; no son reservas. Histórico independiente conservado.")
                batch.file.save(fingerprint + ".json", ContentFile(payload), save=False)
                saved_file = batch.file.name
                batch.save()
                InventoryItem.objects.bulk_create([InventoryItem(batch=batch, **item) for item in items])
                DatedFlow.objects.bulk_create([DatedFlow(batch=batch, created_by=user, **item) for item in flows])
            elif not batch.active:
                current = InventoryBatch.objects.filter(active=True).first()
                if current and current.observed_at and current.observed_at > observed:
                    raise ValueError("Un reintento antiguo no puede sustituir una lectura más reciente.")
                InventoryBatch.objects.filter(active=True).update(active=False)
                batch.active = True
                batch.save(update_fields=["active"])
            run.status, run.fingerprint, run.rows = "success", fingerprint, len(items)
            run.details = {"batch_id": batch.pk, "company_id": raw["company_id"], "movements": len(flows)}
            run.completed_at = timezone.now()
            run.save()
        saved_file = None
    except (ValueError, KeyError, TypeError, ArithmeticError, IntegrityError, OSError) as exc:
        if saved_file:
            batch.file.storage.delete(saved_file)
        run.status, run.error, run.completed_at = "error", "Lectura rechazada: " + str(exc)[:450], timezone.now()
        run.rejected = len(raw.get("stocks", [])) if isinstance(raw, dict) and isinstance(raw.get("stocks"), list) else 0
        run.save()
        return run
    if refresh_orders:
        failures = []
        # ponytail: refresh 50 oldest readings per tick; larger fleets need paged jobs.
        orders = OrderRequest.objects.filter(sale_confirmed=True).exclude(status="cancelled").order_by(
            F("odoo_link__last_synced").asc(nulls_first=True), "pk")[:50]
        for order in orders:
            link = getattr(order, "odoo_link", None)
            try:
                odoo.synchronize(order, "refresh" if link and link.status == "synced" else "sync")
            except odoo.OdooError:
                failures.append(order.pk)
        run.details["pending_orders"] = failures
        run.save(update_fields=["details"])
    return run
