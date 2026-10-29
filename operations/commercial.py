"""Commercial controls shared by Django and Streamlit."""
from decimal import Decimal
from datetime import date, timedelta
from hashlib import sha256
import json
import uuid

from django.core import signing
from django.utils import timezone
from .models import PriceTariff


def salvadoran_event_candidates(year):
    """Candidate dates only; an administrator reviews the source before activation."""
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = (h + l - 7 * m + 114) % 31 + 1
    easter = date(year, month, day)
    legal_source = "https://www.asamblea.gob.sv/sites/default/files/documents/correspondencia/9664C877-2428-4D38-8476-C480CC8751AC.pdf"
    mtps_source = "https://www.mtps.gob.sv/2026/03/23/voceria-oficial-informa-sobre-derechos-laborales-en-fechas-de-asueto/"
    mothers_day_source = "https://www.asamblea.gob.sv/sites/default/files/documents/decretos/67F9738A-3C6C-4A57-8610-EEA40234E516.pdf"
    fathers_day_source = "https://www.asamblea.gob.sv/leyes-y-decretos/decretos-por-anios/2012/0"
    source = mtps_source if year == 2026 else legal_source
    events = [
        ("ano-nuevo", "Año Nuevo", "holiday", date(year, 1, 1), date(year, 1, 1), "Nacional", source),
        ("semana-santa", "Semana Santa", "holiday", easter - timedelta(days=3), easter - timedelta(days=1), "Nacional", source),
        ("dia-del-trabajo", "Día del Trabajo", "holiday", date(year, 5, 1), date(year, 5, 1), "Nacional", source),
        ("dia-de-la-madre", "Día de la Madre", "commercial", date(year, 5, 10), date(year, 5, 10), "Nacional", mothers_day_source),
        ("dia-del-padre", "Día del Padre", "commercial", date(year, 6, 17), date(year, 6, 17), "Nacional", fathers_day_source),
        ("fiestas-agostinas", "Fiestas Agostinas", "commercial", date(year, 8, 1), date(year, 8, 6), "San Salvador", source),
        ("asueto-3-agosto-san-salvador", "Asueto del 3 de agosto · San Salvador", "holiday", date(year, 8, 3), date(year, 8, 3), "San Salvador", source),
        ("asueto-5-agosto-san-salvador", "Asueto del 5 de agosto · San Salvador", "holiday", date(year, 8, 5), date(year, 8, 5), "San Salvador", source),
        ("asueto-6-agosto", "Asueto del 6 de agosto", "holiday", date(year, 8, 6), date(year, 8, 6), "Nacional", source),
        ("independencia", "Independencia", "holiday", date(year, 9, 15), date(year, 9, 15), "Nacional", source),
        ("dia-de-los-difuntos", "Día de los Difuntos", "holiday", date(year, 11, 2), date(year, 11, 2), "Nacional", source),
        ("navidad-comercial", "Temporada de Navidad", "commercial", date(year, 12, 15), date(year, 12, 24), "Nacional", ""),
        ("navidad", "Navidad", "holiday", date(year, 12, 25), date(year, 12, 25), "Nacional", source),
    ]
    return events


def submission_token(user):
    return signing.dumps({"seller": user.pk, "id": str(uuid.uuid4())}, salt="sale-submission")


def submission_identity(token, user, data):
    try:
        value = signing.loads(token, salt="sale-submission", max_age=86400)
        if value["seller"] != user.pk:
            raise ValueError
        identity = uuid.UUID(value["id"])
    except (signing.BadSignature, ValueError, KeyError, TypeError) as exc:
        raise ValueError("La revisión del pedido caducó. Abra un pedido nuevo y revise sus datos.") from exc
    # CSRF and presentation fields are excluded; commercial inputs are compared exactly.
    fields = ("client", "required_date", "product", "quantity", "unit", "reason", "event_id",
              "unit_price", "discount_percent", "note", "payment_terms", "exception_reason")
    digest = sha256(json.dumps({key: data.getlist(key) for key in fields},
                              sort_keys=True, ensure_ascii=True).encode()).hexdigest()
    return identity, digest


def tariffs_for(client):
    return {row.product.casefold(): row for row in PriceTariff.objects.filter(client=client)}


def decorate_cards(cards, client):
    tariffs = tariffs_for(client)
    for card in cards:
        rate = tariffs.get(card["product"].casefold())
        unit_mismatch = bool(
            card.get("suggestion_mode") != "client_restock" and rate and card.get("unit")
            and card["unit"].strip().casefold() != rate.unit.strip().casefold()
        )
        card.update(unit_price=rate.unit_price if rate else None,
                    discount_percent=rate.discount_percent if rate else Decimal(0),
                    order_unit=rate.unit if rate else "",
                    unit_mismatch=unit_mismatch,
                    tariff_available=bool(rate))
        if unit_mismatch:
            card["suggested"] = None
            card["eligible_for_auto"] = False
        elif rate and not card.get("unit"):
            # ponytail: a current tariff can price an order unit; it cannot fill
            # a missing historical unit or establish series evidence.
            card["tariff_available"] = False
    return cards


def commercial_price(client, product, unit, price, discount, exception_reason=""):
    from .services import nonnegative_decimal
    rate = PriceTariff.objects.filter(client=client, product=product).first()
    if exception_reason:
        price = nonnegative_decimal(price, "Precio solicitado", positive=True, places=4)
        discount = nonnegative_decimal(discount or "0", "Descuento solicitado")
        if discount >= 100 or discount != discount.quantize(Decimal("0.01")):
            raise ValueError("El descuento solicitado debe ser menor de 100 %, con hasta dos decimales.")
        return price, discount
    if rate is None or rate.unit != unit:
        raise ValueError(f"{product}: administración debe definir una tarifa para esta unidad o revisar una excepción.")
    if price not in (None, "") and nonnegative_decimal(price, "Precio", places=4) != rate.unit_price:
        raise ValueError("La tarifa cambió o el precio fue alterado. Revise el pedido nuevamente.")
    if discount not in (None, "") and nonnegative_decimal(discount, "Descuento") != rate.discount_percent:
        raise ValueError("El descuento lo define administración; solicite una excepción.")
    return rate.unit_price, rate.discount_percent


def source_health():
    from .models import SourceSync, InventoryBatch
    last = SourceSync.objects.order_by("-pk").first()
    good = SourceSync.objects.filter(status="success").order_by("-pk").first()
    batch = InventoryBatch.objects.filter(active=True).first()
    observed = batch.observed_at if batch and batch.source == "odoo" else None
    age = (timezone.now() - observed).total_seconds() if observed else None
    return {"last_success": good.completed_at if good else None, "observed_at": observed,
            "stale": age is None or age > 900, "age_minutes": round(age / 60, 1) if age is not None else None,
            "status": last.status if last else "never", "error": last.error if last else "",
            "rows": last.rows if last else 0, "rejected": last.rejected if last else 0}


def pilot_dashboard(start, end):
    from . import odoo
    from .models import OrderRequest, OrderLine
    orders = list(OrderRequest.objects.filter(required_date__range=(start, end)).exclude(
        status="cancelled").select_related("seller", "odoo_link").prefetch_related("lines"))
    revenue, totals, actions = Decimal(0), {}, []
    due, on_time, unknown_time = 0, 0, 0
    today = timezone.localdate()
    for order in orders:
        link = getattr(order, "odoo_link", None)
        valid = bool(link and link.status == "synced" and link.snapshot.get("revision") == link.revision
                     and link.last_synced and timezone.now() - link.last_synced <= timedelta(minutes=15))
        observed = {str(row["key"]): row for row in link.snapshot.get("lines", [])} if valid else {}
        if not order.sale_confirmed:
            if order.exception_reason:
                actions.append({"order_id": order.pk, "client": order.client, "reason": "Excepción comercial pendiente"})
            continue
        if not valid:
            actions.append({"order_id": order.pk, "client": order.client, "reason": "Seguimiento pendiente de actualizar"})
        complete, moments, known = True, [], True
        for line in order.lines.all():
            revenue += (line.requested_qty * line.unit_price * (1 - line.discount_percent / 100)).quantize(Decimal("0.01"))
            unit = totals.setdefault(line.unit, {"unit": line.unit, "sold": Decimal(0), "net": Decimal(0),
                "approved": Decimal(0), "produced": Decimal(0), "pending_production": Decimal(0), "outstanding": Decimal(0), "unknown": 0})
            unit["sold"] += line.requested_qty
            unit["approved"] += line.approved_qty or Decimal(0)
            if line.status == OrderLine.Status.PENDING:
                unit["pending_production"] += line.requested_qty
            row = observed.get(str(line.pk))
            if row is None:
                unit["unknown"] += 1
                complete = False
                known = False
            else:
                net = max(Decimal(0), Decimal(str(row["delivered"])) - Decimal(str(row["returned"])))
                unit["net"] += net
                unit["outstanding"] += max(Decimal(0), line.requested_qty - net)
                unit["produced"] += Decimal(str(row["produced"]))
                complete = complete and net >= line.requested_qty
                moment = odoo.delivery_moment(row)
                if moment:
                    moments.append(timezone.localtime(moment).date())
                if net < line.requested_qty:
                    actions.append({"order_id": order.pk, "client": order.client, "reason": line.product + ": entrega incompleta"})
            if line.future_quantity is None or line.future_quantity > 0:
                actions.append({"order_id": order.pk, "client": order.client, "reason": line.product + ": revisar disponibilidad"})
            if line.status == OrderLine.Status.PENDING:
                actions.append({"order_id": order.pk, "client": order.client, "reason": line.product + ": revisar producción"})
        if order.committed_date and order.committed_date < today:
            due += 1
            if not valid or not known or (complete and not moments):
                unknown_time += 1
            elif complete and max(moments) <= order.committed_date:
                on_time += 1
    for unit in totals.values():
        if unit["unknown"]:
            unit["outstanding"] = None
        unit["fulfillment"] = (100 * unit["net"] / unit["sold"]) if unit["sold"] and not unit["unknown"] else None
        if unit["unknown"]:
            unit["net"] = unit["produced"] = None
    return {"from_date": start, "to_date": end, "confirmed_revenue": revenue,
            "unit_totals": list(totals.values()), "due_orders": due, "on_time_orders": on_time,
            "unknown_punctuality": unknown_time,
            "punctuality": 100 * on_time / due if due and not unknown_time else None,
            "actions": actions, "health": source_health()}
