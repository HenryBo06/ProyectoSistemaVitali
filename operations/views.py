"""Role-aware local screens and the sales-to-production workflow."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from hashlib import sha256
import json
import os
import secrets
from urllib.parse import urlencode

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model, login
from django.contrib.auth.decorators import login_required
from django.contrib.auth.password_validation import validate_password
from django.contrib.auth.views import LoginView
from django.contrib.sessions.models import Session
from types import SimpleNamespace
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Count, Q, Sum
from django.db.models.functions import TruncMonth
from django.http import FileResponse, Http404, HttpResponse, HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect
from django.template.response import TemplateResponse
from django.urls import reverse
from django.utils import timezone
from django.utils.text import slugify
from django.views.decorators.http import require_http_methods, require_POST

from smartorder.inventory import inventory_template
from smartorder.notifications import notify_internal
from smartorder.orders import export_production
from .models import (
    AccessEvent, Assignment, ClientStockCount, ClientStockMovement, CommercialEvent, DatedFlow,
    InventoryAdjustment, InventoryBatch, InventoryItem, LocationIncoming,
    LocationStockSnapshot, NotificationLog,
    OrderCoverage, OrderLine, OrderRequest, RestockTarget, SalesDataset, SalesLine,
    SalesCoverage, UserNotification,
    StockCorrectionRequest,
    OdooProductMapping, OdooCustomerMapping, OdooOrderLink,
)
from .services import (
    active_inventory, active_sales, default_delivery_date, import_inventory, import_sales,
    nonnegative_decimal, operational_overlap_pending, recommendation_cards,
    top_products_for, vitali_restock_plan,
    operational_overlap_rows, review_operational_sale, save_sales_coverage,
)

User = get_user_model()
from . import odoo as odoo_service
from . import commercial
from .models import PriceTariff, SourceSync


def _role(user) -> str:
    return "admin" if user.is_staff else "vendedor"


def _base(request, **context):
    return {"role": _role(request.user), "maintenance_ui": settings.SMARTORDER_MAINTENANCE_UI and request.user.is_staff,
            "nav": request.resolver_match.url_name if request.resolver_match else "",
            "unread_notification_count": _visible_notifications(request.user).filter(
                read_at__isnull=True
            ).count(), **context}


def _source_status(dataset: SalesDataset | None) -> str:
    if dataset is None:
        return "empty"
    if not dataset.verified or not dataset.full_snapshot_confirmed:
        return "reference"
    return "stale" if (date.today() - dataset.last_date).days > 7 else "current"


def _is_local(request) -> bool:
    return (
        settings.DEBUG
        and request.META.get("REMOTE_ADDR") in {"127.0.0.1", "::1"}
        and request.get_host().split(":")[0] in {"127.0.0.1", "localhost", "[::1]"}
    )


def admin_required(view):
    @login_required
    def wrapped(request, *args, **kwargs):
        if not request.user.is_staff:
            raise PermissionDenied("Esta acción requiere administración.")
        return view(request, *args, **kwargs)
    return wrapped


def _sources_current(order: OrderRequest) -> bool:
    sales = active_sales()
    inventory = active_inventory()
    sales_id = sales.pk if sales else None
    inventory_id = inventory.pk if inventory else None
    inventory_matches = order.inventory_batch_id == inventory_id or order.inventory_revalidated_against_id == inventory_id
    previous = order.inventory_revalidated_against or order.inventory_batch
    if not inventory_matches and previous and inventory and previous.source == inventory.source == "odoo":
        # ponytail: compare this customer's quantities, not the timestamp of a fresh read.
        inventory_matches = all(
            list(model.objects.filter(batch=previous, client=order.client).order_by(*fields).values_list(*fields))
            == list(model.objects.filter(batch=inventory, client=order.client).order_by(*fields).values_list(*fields))
            for model, fields in ((InventoryItem, ("product", "unit", "available")),
                                  (DatedFlow, ("product", "unit", "quantity", "due_date", "kind")),
                                  (InventoryAdjustment, ("product", "available", "observed_on")))
        )
    return (
        sales_id is not None
        and (order.dataset_id == sales_id or order.revalidated_against_id == sales_id)
        and inventory_matches
    )


def _assigned_clients(user) -> list[str]:
    return list(Assignment.objects.filter(user=user).order_by("client").values_list("client", flat=True))


def _client_allowed(user, client: str) -> bool:
    return user.is_staff or Assignment.objects.filter(user=user, client=client).exists()


def _invalidate_sessions(user):
    # ponytail: scan database sessions for this local installation; use indexed
    # user-session ownership if session volume grows beyond a single-PC workload.
    for session in Session.objects.filter(expire_date__gt=timezone.now()):
        if str(session.get_decoded().get("_auth_user_id")) == str(user.pk):
            session.delete()


def _seller_cards(cards):
    fields = (
        "product", "category", "unit", "unit_confirmed", "suggested", "unit_price",
        "discount_percent", "tariff_available", "available", "stock_unit", "stock_date",
        "inventory_warning", "reference_date", "reference_qty", "client_stock",
        "client_stock_location", "client_stock_target", "client_stock_projected",
        "client_stock_incoming", "client_stock_outgoing", "suggestion_mode",
        "suggestion_event", "delivery_outside_forecast", "order_unit", "unit_mismatch",
    )

    def seller_explanation(card):
        if card.get("suggestion_mode") == "client_restock":
            return card.get("explanation") or "Se requiere revisión manual de las existencias del cliente."
        if card.get("unit_mismatch"):
            return (f"El histórico está en {card.get('unit')}; la tarifa de este cliente usa "
                    f"{card.get('order_unit')}. No se convierte la cantidad; indica una cantidad manual "
                    f"en {card.get('order_unit')}.")
        if card.get("delivery_outside_forecast"):
            return "La fecha de entrega supera el horizonte de siete días; indica la cantidad manualmente."
        unit = card.get("unit") or ""
        event = card.get("suggestion_event")
        if event and card.get("reference_date") and card.get("reference_qty") is not None:
            text = (f"Este cliente registró una venta de {card['reference_qty']:g} {unit} el "
                    f"{card['reference_date']:%d/%m/%Y} durante {event}.")
        elif event:
            text = f"Oportunidad para revisar: la entrega coincide con {event}."
        else:
            text = "Se revisaron las ventas registradas de este cliente para este producto y fecha."
        if card.get("suggested") is not None:
            return f"{text} Sugerimos revisar {card['suggested']:g} {unit} para esta entrega; puedes ajustarla."
        return f"{text} No hay evidencia suficiente para proponer una cantidad automática; revísala manualmente."

    return [{**{key: card.get(key) for key in fields},
             "state": "suggested" if card["suggested"] is not None else "manual",
             "explanation": seller_explanation(card)}
            for card in sorted(cards, key=lambda card: card["product"].casefold())]


def _seller_order(order):
    return SimpleNamespace(pk=order.pk, id=order.pk, client=order.client,
        seller=SimpleNamespace(username=order.seller.username), required_date=order.required_date,
        note=order.note, created_at=order.created_at, status=order.status,
        sale_confirmed=order.sale_confirmed, payment_terms=order.payment_terms,
        committed_date=order.committed_date, exception_reason=order.exception_reason,
        commercial_review_note=order.commercial_review_note,
        line_count=order.lines.count(), status_display=order.get_status_display(),
        get_status_display=order.get_status_display())


def _required_date(value: str) -> date:
    try:
        day = date.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("Indique una fecha de entrega válida.") from exc
    today = timezone.localdate()
    if not today <= day <= today + timedelta(days=settings.SMARTORDER_DELIVERY_DAYS):
        raise ValueError("La entrega debe estar dentro del horizonte configurado.")
    return day


def _xlsx_upload(request, field: str) -> tuple[bytes, str]:
    uploaded = request.FILES.get(field)
    if uploaded is None:
        raise ValueError("Seleccione un archivo XLSX.")
    if uploaded.size > 20 * 1024 * 1024:
        raise ValueError("El archivo supera el límite de 20 MB.")
    if not uploaded.name.lower().endswith(".xlsx"):
        raise ValueError("Seleccione un archivo .xlsx.")
    return uploaded.read(), uploaded.name


def _create_user_notification(recipient, event_key, title, body, *, client="", order=None,
                              kind=UserNotification.Kind.OPERATIONAL, target_url=""):
    key = sha256(f"{recipient.pk}:{event_key}".encode("utf-8")).hexdigest()
    return UserNotification.objects.get_or_create(event_key=key, defaults={
        "recipient": recipient, "kind": kind, "title": title, "body": body,
        "client": client, "order": order, "target_url": target_url,
    })[0]


def _notification_recipients(title, order):
    if title in {"Venta confirmada; producción pendiente", "Excepción comercial pendiente",
                 "Corrección de inventario pendiente"}:
        return User.objects.filter(is_staff=True)
    if title in {"Pedido revisado", "Resultado de excepción comercial",
                 "Compromiso de entrega actualizado", "Avance de producción o entrega actualizado"} and order:
        owner = Assignment.objects.filter(client=order.client).select_related("user").first()
        return [owner.user] if owner else []
    return []


def _notification(event_key: str, title: str, request, order: OrderRequest | None = None):
    target = reverse("order_detail", args=[order.pk]) if order else reverse("orders")
    for recipient in _notification_recipients(title, order):
        body = (f"La venta para {order.client} requiere revisión administrativa."
                if title == "Venta confirmada; producción pendiente" and order else
                f"La excepción comercial de {order.client} requiere revisión."
                if title == "Excepción comercial pendiente" and order else
                f"La corrección de inventario de {order.client if order else 'la cartera'} requiere revisión."
                if title == "Corrección de inventario pendiente" else
                (f"La excepción comercial fue {'aprobada y la venta quedó confirmada' if order.sale_confirmed else 'rechazada'} "
                 f"para {order.client}. Abre el pedido para revisar el resultado.")
                if title == "Resultado de excepción comercial" and order else
                f"La fecha de entrega para {order.client} quedó comprometida el {order.committed_date:%d/%m/%Y}."
                if title == "Compromiso de entrega actualizado" and order and order.committed_date else
                f"Administración actualizó el avance de producción o entrega para {order.client}."
                if title == "Avance de producción o entrega actualizado" and order else
                _review_notification_body(order)
                if title == "Pedido revisado" and order else
                "Administración revisó una línea de tu pedido.")
        _create_user_notification(recipient, event_key, title, body,
                                  client=order.client if order else "", order=order,
                                  target_url=target)
    if not os.environ.get("SMARTORDER_TELEGRAM_BOT_TOKEN") or not os.environ.get("SMARTORDER_TELEGRAM_CHAT_ID"):
        return
    if NotificationLog.objects.filter(event_key=event_key).exists():
        return
    link = getattr(request, "smartorder_home_url", None) or request.build_absolute_uri(reverse("orders"))
    sent, detail = notify_internal(title, link)
    NotificationLog.objects.create(
        event_key=event_key, channel="telegram", recipient="personal interno",
        status=NotificationLog.Status.SENT if sent else NotificationLog.Status.FAILED,
        order=order, error="" if sent else detail,
        sent_at=timezone.now() if sent else None,
    )


def _review_notification_body(order):
    line = order.lines.filter(reviewed_at__isnull=False).order_by("-reviewed_at", "-pk").first()
    if line is None:
        return f"Administración revisó el pedido de {order.client}."
    if line.status == OrderLine.Status.APPROVED:
        return (f"Administración autorizó producir {line.approved_qty:g} {line.unit} de "
                f"{line.product} para {order.client}. La entrega aún requiere seguimiento.")
    return (f"Administración registró una decisión sobre {line.product} para {order.client}. "
            "Abre el pedido para consultar el estado.")


def _ensure_event_notifications(user, today=None):
    if user.is_staff:
        return
    today = today or timezone.localdate()
    clients = _assigned_clients(user)
    if not clients:
        return
    dataset = active_sales()
    zones = {}
    if dataset:
        for client, zone in SalesLine.objects.filter(dataset=dataset, client__in=clients).values_list(
            "client", "zone"
        ).distinct():
            zones.setdefault(client, set()).add(zone.casefold())
    national = {"nacional", "national", "el salvador", ""}
    for event in CommercialEvent.objects.filter(
        active=True, reviewed=True, start_date__in=(today, today + timedelta(days=7))
    ):
        territory = event.territory.strip().casefold()
        for client in clients:
            if territory not in national and territory not in zones.get(client, set()):
                continue
            product_row = SalesLine.objects.filter(dataset=dataset, client=client).values(
                "product"
            ).annotate(total=Sum("quantity")).order_by("-total", "product").first() if dataset else None
            if not product_row:
                continue
            product = product_row["product"]
            target = reverse("order_create") + "?" + urlencode({
                "client": client, "product": product,
                "required_date": event.start_date.isoformat(), "event_id": event.pk,
            })
            day_label = "hoy" if event.start_date == today else "dentro de 7 días"
            notice_day = today.isoformat()
            _create_user_notification(
                user, f"event:{event.pk}:{client}:{product}:{event.start_date.isoformat()}:{notice_day}",
                f"Revisa una venta para {event.title}",
                f"{event.title} empieza {day_label} ({event.start_date:%d/%m/%Y}). "
                f"Revisa si conviene ofrecer {product} a {client}.",
                client=client, kind=UserNotification.Kind.EVENT, target_url=target,
            )


def _visible_notifications(user):
    rows = UserNotification.objects.filter(recipient=user).select_related("order")
    if not user.is_staff:
        clients = _assigned_clients(user)
        rows = rows.filter(client__in=clients) if clients else rows.none()
    return rows


@login_required
@require_http_methods(["GET", "POST"])
def notification_inbox(request):
    if not request.user.is_staff:
        _ensure_event_notifications(request.user)
    rows = _visible_notifications(request.user)
    if request.method == "POST":
        notice = get_object_or_404(rows, pk=request.POST.get("notification_id"))
        action = request.POST.get("action")
        if action in {"read", "open"} and notice.read_at is None:
            notice.read_at = timezone.now()
            notice.save(update_fields=["read_at"])
        elif action == "unread" and notice.read_at is not None:
            notice.read_at = None
            notice.save(update_fields=["read_at"])
        if action == "open":
            if notice.client and not _client_allowed(request.user, notice.client):
                raise PermissionDenied("El cliente ya no pertenece a tu cartera.")
            if notice.order_id:
                if not _client_allowed(request.user, notice.order.client):
                    raise PermissionDenied("Este pedido ya no pertenece a tu cartera.")
                return redirect("order_detail", order_id=notice.order_id)
            target = notice.target_url
            if target.startswith("/") and not target.startswith("//"):
                if target.partition("?")[0] == reverse("order_create") and not request.user.is_staff:
                    return redirect(target)
                if request.user.is_staff:
                    return redirect(target)
            messages.info(request, "La novedad quedó marcada como leída.")
        elif action not in {"read", "unread"}:
            return HttpResponseBadRequest("Acción de notificación inválida.")
        return redirect("notification_inbox")
    visible = list(rows[:100])
    return TemplateResponse(request, "operations/notification_inbox.html", _base(
        request, notifications=visible,
        operational_notifications=[row for row in visible if row.kind == UserNotification.Kind.OPERATIONAL],
        event_notifications=[row for row in visible if row.kind == UserNotification.Kind.EVENT],
    ))


@login_required
@require_http_methods(["GET", "POST"])
def client_stock(request):
    if request.user.is_staff:
        raise PermissionDenied("El conteo de existencias del cliente corresponde al vendedor.")
    today = timezone.localdate()
    dataset = active_sales()
    clients = _assigned_clients(request.user)
    client = request.POST.get("client", "") if request.method == "POST" else request.GET.get("client", "")
    client = client or (clients[0] if clients else "")
    if client and client not in clients:
        raise PermissionDenied("El cliente no pertenece a tu cartera.")
    products = list(dataset.lines.filter(client=client).values_list("product", flat=True)
                    .distinct().order_by("product")) if dataset and client else []
    if request.method == "POST":
        try:
            action = request.POST.get("action", "count")
            product = request.POST.get("product", "").strip()
            location = request.POST.get("location", "").strip()
            unit = request.POST.get("unit", "").strip()
            if product not in products:
                raise ValueError("Selecciona un producto de este cliente.")
            if not location or len(location) > 160:
                raise ValueError("Indica la ubicación del conteo.")
            if not unit or len(unit) > 50:
                raise ValueError("Indica la unidad del registro.")
            if action == "count":
                observed = date.fromisoformat(request.POST.get("observed_on", ""))
                if observed != today:
                    raise ValueError("El conteo debe corresponder al día de hoy.")
                amount = nonnegative_decimal(request.POST.get("available", ""), "Existencia contada")
                ClientStockCount.objects.create(
                    client=client, product=product, location=location, unit=unit,
                    available=amount, observed_on=today, reported_by=request.user,
                )
                messages.success(request, "Conteo del cliente guardado para la fecha de hoy.")
            elif action == "movement":
                kind = request.POST.get("kind", "")
                effective = date.fromisoformat(request.POST.get("effective_date", ""))
                if kind not in ClientStockMovement.Kind.values:
                    raise ValueError("Selecciona una entrada o salida válida.")
                if not today < effective <= today + timedelta(days=7):
                    raise ValueError("El movimiento debe estar entre mañana y los próximos siete días.")
                if request.POST.get("confirmed") != "1":
                    raise ValueError("Confirma el movimiento reportado por el cliente.")
                amount = nonnegative_decimal(request.POST.get("quantity", ""), "Cantidad del movimiento", positive=True)
                ClientStockMovement.objects.create(
                    client=client, product=product, location=location, kind=kind,
                    unit=unit, quantity=amount, effective_date=effective,
                    reference=request.POST.get("reference", "").strip()[:200],
                    confirmed=True, reported_by=request.user,
                )
                messages.success(request, "Movimiento del cliente confirmado por el vendedor.")
            else:
                raise ValueError("Acción de existencias inválida.")
            return redirect(f"{reverse('client_stock')}?{urlencode({'client': client})}")
        except (ValueError, TypeError) as exc:
            messages.error(request, str(exc))
    latest = {}
    for row in ClientStockCount.objects.filter(client=client).order_by(
        "product", "location", "-observed_on", "-created_at", "-pk"
    ):
        latest.setdefault((row.product, row.location.strip().casefold()), row)
    targets = {row.product: row for row in RestockTarget.objects.filter(
        scope=RestockTarget.Scope.CLIENT, owner_key=f"client:{client}"
    )}
    product_rows = []
    for product in products:
        location_counts = [row for (name, _), row in latest.items() if name == product]
        current = bool(location_counts and all(row.observed_on == today for row in location_counts)
                       and len({row.unit.strip().casefold() for row in location_counts}) == 1)
        count_status = (
            f"{sum((row.available for row in location_counts), Decimal('0')):g} "
            f"{location_counts[0].unit} en {len(location_counts)} ubicación(es), hoy"
            if current else
            " · ".join(f"{row.available:g} {row.unit} en {row.location} · {row.observed_on:%d/%m/%Y}"
                       for row in location_counts) if location_counts else "Sin conteo"
        )
        product_rows.append({"product": product, "counts": location_counts,
                             "count_status": count_status, "target": targets.get(product)})
    movements = ClientStockMovement.objects.filter(
        client=client, effective_date__gte=today,
    ).order_by("effective_date", "-created_at")[:100]
    return TemplateResponse(request, "operations/client_stock.html", _base(
        request, clients=clients, selected_client=client, products=products,
        today=today, counts=latest, targets=targets, product_rows=product_rows,
        movements=movements, min_movement_date=today + timedelta(days=1),
        max_movement_date=today + timedelta(days=7),
    ))


@login_required
@admin_required
@require_http_methods(["GET", "POST"])
def planning(request):
    today = timezone.localdate()
    dataset = active_sales()
    clients = list(dataset.lines.values_list("client", flat=True).distinct().order_by("client")) if dataset else []
    products = list(dataset.lines.values_list("product", flat=True).distinct().order_by("product")) if dataset else []
    planning_clients = sorted(set(clients) | set(OdooCustomerMapping.objects.values_list("client", flat=True)))
    planning_products = sorted(set(products) | set(OdooProductMapping.objects.values_list("product", flat=True)))
    locations = list(OdooCustomerMapping.objects.exclude(location_id__isnull=True)
                     .values_list("location_id", flat=True).distinct().order_by("location_id"))
    if request.method == "POST":
        action = request.POST.get("action", "")
        try:
            if action == "seed_calendar":
                year = int(request.POST.get("year", today.year))
                if not today.year - 1 <= year <= today.year + 3:
                    raise ValueError("El año debe estar entre el anterior y los próximos tres años.")
                created = 0
                for key, title, kind, start, end, territory, source_url in commercial.salvadoran_event_candidates(year):
                    event, was_created = CommercialEvent.objects.get_or_create(
                        key=key, start_date=start, territory=territory,
                        defaults={"title": title, "kind": kind, "end_date": end,
                                  "source": source_url, "created_by": request.user},
                    )
                    if not was_created and not event.reviewed and not event.source and source_url:
                        event.source = source_url
                        event.save(update_fields=["source"])
                    created += int(was_created)
                messages.success(request, f"Se agregaron {created} candidatos de calendario para {year}. Revisa sus fuentes antes de activarlos.")
            elif action == "create_event":
                title = request.POST.get("title", "").strip()
                start = date.fromisoformat(request.POST.get("start_date", ""))
                end = date.fromisoformat(request.POST.get("end_date", ""))
                kind = request.POST.get("kind", "")
                territory = request.POST.get("territory", "Nacional").strip() or "Nacional"
                source_url = request.POST.get("source", "").strip()
                if not title or len(title) > 160 or end < start:
                    raise ValueError("Completa el nombre y un rango de fechas válido.")
                if kind not in CommercialEvent.Kind.values:
                    raise ValueError("Selecciona un tipo de evento válido.")
                if kind == CommercialEvent.Kind.HOLIDAY and not source_url:
                    raise ValueError("Un asueto requiere una fuente para poder revisarse.")
                key = slugify(request.POST.get("key", "") or title)[:100]
                event = CommercialEvent(
                    key=key, title=title, kind=kind, start_date=start, end_date=end,
                    territory=territory, source=source_url, created_by=request.user,
                )
                event.full_clean()
                event.save()
                messages.success(request, "Evento agregado como candidato pendiente de revisión.")
            elif action == "review_event":
                event = get_object_or_404(CommercialEvent, pk=request.POST.get("event_id"))
                reviewed = request.POST.get("reviewed") == "1"
                active = request.POST.get("active") == "1"
                event.source = request.POST.get("source", event.source).strip()
                if reviewed and event.kind == CommercialEvent.Kind.HOLIDAY and not event.source:
                    raise ValueError("Agrega una fuente antes de revisar este asueto.")
                event.reviewed = reviewed
                event.active = active
                event.reviewed_by = request.user if reviewed else None
                event.reviewed_at = timezone.now() if reviewed else None
                event.full_clean()
                event.save(update_fields=["source", "reviewed", "active", "reviewed_by", "reviewed_at"])
                messages.success(request, "Estado del evento actualizado.")
            elif action == "save_target":
                scope = request.POST.get("scope", "")
                product = request.POST.get("product", "").strip()
                unit = request.POST.get("unit", "").strip()
                amount = nonnegative_decimal(request.POST.get("target_quantity", ""), "Objetivo")
                if product not in planning_products or not unit or len(unit) > 50:
                    raise ValueError("Selecciona un producto y una unidad válidos.")
                if scope == RestockTarget.Scope.CLIENT:
                    client = request.POST.get("client", "")
                    if client not in planning_clients:
                        raise ValueError("Selecciona un cliente de la cartera administrativa.")
                    owner_key = f"client:{client}"
                elif scope == RestockTarget.Scope.LOCATION:
                    location_id = int(request.POST.get("location_id", ""))
                    if location_id not in locations:
                        raise ValueError("Selecciona una ubicación Vitali vinculada en el catálogo.")
                    owner_key = f"location:{location_id}"
                else:
                    raise ValueError("Selecciona un alcance válido.")
                RestockTarget.objects.update_or_create(
                    scope=scope, owner_key=owner_key, product=product,
                    defaults={"unit": unit, "target_quantity": amount, "updated_by": request.user},
                )
                messages.success(request, "Objetivo de existencias actualizado.")
            elif action in {"save_location_stock", "save_location_incoming"}:
                location_id = int(request.POST.get("location_id", ""))
                if location_id not in locations:
                    raise ValueError("Selecciona una ubicación vinculada en el catálogo.")
                location_name = request.POST.get("location_name", "").strip()
                product = request.POST.get("product", "").strip()
                unit = request.POST.get("unit", "").strip()
                if not location_name or len(location_name) > 160 or product not in planning_products or not unit or len(unit) > 50:
                    raise ValueError("Completa ubicación, producto y unidad.")
                if action == "save_location_stock":
                    amount = nonnegative_decimal(request.POST.get("available", ""), "Conteo físico")
                    observed = date.fromisoformat(request.POST.get("observed_on", ""))
                    if observed != today:
                        raise ValueError("El conteo físico debe corresponder al día de hoy.")
                    LocationStockSnapshot.objects.create(
                        location_id=location_id, location_name=location_name, product=product,
                        unit=unit, available=amount, observed_on=today, reported_by=request.user,
                    )
                    messages.success(request, "Conteo físico guardado. No modifica el inventario de Odoo.")
                else:
                    amount = nonnegative_decimal(request.POST.get("quantity", ""), "Entrada confirmada")
                    due = date.fromisoformat(request.POST.get("due_date", ""))
                    if not today <= due <= today + timedelta(days=365):
                        raise ValueError("La fecha de entrada debe estar entre hoy y los próximos 365 días.")
                    confirmed = request.POST.get("confirmed") == "1"
                    reference = request.POST.get("reference", "").strip()[:160]
                    if confirmed and not reference:
                        raise ValueError("Una entrada confirmada necesita referencia para evitar duplicados.")
                    LocationIncoming.objects.create(
                        location_id=location_id, location_name=location_name, product=product,
                        unit=unit, quantity=amount, due_date=due,
                        reference=reference,
                        confirmed=confirmed, recorded_by=request.user,
                    )
                    messages.success(request, "Entrada registrada para planificación; no autoriza fabricación.")
            elif action == "confirm_dated_flow":
                flow = get_object_or_404(
                    DatedFlow.objects.select_related("batch"), pk=request.POST.get("flow_id")
                )
                batch = active_inventory()
                if flow.batch_id and flow.batch_id != (batch.pk if batch else None):
                    raise ValueError("Solo se pueden confirmar flujos de la carga de inventario activa.")
                client = request.POST.get("client", flow.client).strip()
                product = request.POST.get("product", flow.product).strip()
                unit = request.POST.get("unit", flow.unit).strip()
                kind = request.POST.get("kind", flow.kind)
                date_semantics = request.POST.get("date_semantics", flow.date_semantics)
                reconciliation = request.POST.get("reconciliation", flow.reconciliation)
                source_reference = request.POST.get("source_reference", flow.source_reference).strip()[:160]
                try:
                    quantity = nonnegative_decimal(request.POST.get("quantity", flow.quantity), "Cantidad del flujo")
                    due_date = date.fromisoformat(request.POST.get("due_date", flow.due_date.isoformat()))
                except (TypeError, ValueError) as exc:
                    raise ValueError("Cantidad o fecha del flujo inválida.") from exc
                allowed_clients = set(OdooCustomerMapping.objects.values_list("client", flat=True)) | set(clients)
                if (client not in allowed_clients or product not in planning_products or not unit
                        or len(unit) > 50 or kind not in DatedFlow.Kind.values
                        or date_semantics not in DatedFlow.DateSemantics.values
                        or reconciliation not in DatedFlow.Reconciliation.values):
                    raise ValueError("Confirma cliente, producto, unidad y significado de fecha válidos.")
                if due_date < today or due_date > today + timedelta(days=365):
                    raise ValueError("La fecha del flujo debe estar entre hoy y los próximos 365 días.")
                if (kind == DatedFlow.Kind.INCOMING) != (date_semantics == DatedFlow.DateSemantics.EXPECTED_INCOMING):
                    raise ValueError("El significado de fecha debe corresponder a entrada o compromiso de cliente.")
                related_line = None
                related_id = request.POST.get("related_order_line", "").strip()
                if related_id:
                    related_line = OrderLine.objects.filter(pk=related_id, order__client=client,
                                                             product=product).select_related("order").first()
                    if related_line is None or related_line.unit.strip().casefold() != unit.casefold():
                        raise ValueError("La línea vinculada debe coincidir en cliente, producto y unidad.")
                    if (not related_line.order.sale_confirmed
                            or related_line.order.status in {OrderRequest.Status.CANCELLED, OrderRequest.Status.SUPERSEDED}
                            or related_line.status == OrderLine.Status.REJECTED):
                        raise ValueError("El pedido vinculado no es una venta confirmada vigente.")
                if reconciliation == DatedFlow.Reconciliation.DUPLICATE and kind == DatedFlow.Kind.CUSTOMER_COMMITMENT and related_line is None:
                    raise ValueError("Una representación duplicada requiere vincular el pedido y la línea existentes.")
                if reconciliation == DatedFlow.Reconciliation.DUPLICATE and kind == DatedFlow.Kind.INCOMING:
                    original = DatedFlow.objects.filter(
                        kind=DatedFlow.Kind.INCOMING, confirmed=True,
                        reconciliation=DatedFlow.Reconciliation.UNIQUE,
                        source_reference=source_reference, client=client, product=product,
                        unit__iexact=unit, due_date=due_date,
                    ).exclude(pk=flow.pk).exists()
                    if not original:
                        raise ValueError("La recepción duplicada necesita la referencia de una entrada única ya confirmada.")
                    if related_line is not None:
                        raise ValueError("Una entrada duplicada se concilia por referencia de recepción, no por línea de pedido.")
                if reconciliation != DatedFlow.Reconciliation.DUPLICATE and related_line is not None:
                    raise ValueError("La línea vinculada solo se usa cuando el flujo representa un duplicado.")
                flow.client, flow.product, flow.unit = client, product, unit
                flow.kind, flow.date_semantics = kind, date_semantics
                flow.source_reference, flow.reconciliation = source_reference, reconciliation
                flow.related_order_line = related_line
                flow.quantity, flow.due_date = quantity, due_date
                flow.confirmed = request.POST.get("confirmed") == "1"
                if flow.confirmed and reconciliation not in {
                    DatedFlow.Reconciliation.UNIQUE, DatedFlow.Reconciliation.DUPLICATE,
                }:
                    raise ValueError("Conciliar como compromiso independiente o duplicado antes de confirmar.")
                if flow.confirmed and flow.batch_id and not source_reference:
                    raise ValueError("El flujo importado necesita su referencia de origen antes de confirmarse.")
                flow.confirmed_by = request.user if flow.confirmed else None
                flow.confirmed_at = timezone.now() if flow.confirmed else None
                flow.save(update_fields=["client", "product", "unit", "kind", "date_semantics",
                                         "source_reference", "reconciliation", "related_order_line",
                                         "quantity", "due_date", "confirmed", "confirmed_by", "confirmed_at"])
                messages.success(request, "Flujo revisado; solo el compromiso único confirmado participa en la planificación.")
            elif action == "review_client_movement":
                movement = get_object_or_404(ClientStockMovement, pk=request.POST.get("movement_id"))
                reconciliation = request.POST.get("reconciliation", "")
                valid = {value for value, _ in ClientStockMovement._meta.get_field("reconciliation").choices}
                if reconciliation not in valid:
                    raise ValueError("Seleccione un estado de conciliación válido.")
                related_id = request.POST.get("related_order_line", "").strip()
                related_line = None
                if related_id:
                    related_line = OrderLine.objects.filter(
                        pk=related_id, order__client=movement.client, product=movement.product,
                    ).select_related("order").first()
                    if related_line is None or related_line.unit.strip().casefold() != movement.unit.strip().casefold():
                        raise ValueError("La línea vinculada debe coincidir en cliente, producto y unidad.")
                    if (not related_line.order.sale_confirmed
                            or related_line.order.status in {OrderRequest.Status.CANCELLED, OrderRequest.Status.SUPERSEDED}
                            or related_line.status == OrderLine.Status.REJECTED):
                        raise ValueError("El pedido vinculado no es una venta confirmada vigente.")
                if reconciliation == DatedFlow.Reconciliation.DUPLICATE and related_line is None:
                    raise ValueError("Un movimiento duplicado requiere vincular la línea existente.")
                if reconciliation != DatedFlow.Reconciliation.DUPLICATE and related_line is not None:
                    raise ValueError("La línea vinculada solo se usa para conciliar un duplicado.")
                movement.reconciliation, movement.related_order_line = reconciliation, related_line
                movement.save(update_fields=["reconciliation", "related_order_line"])
                messages.success(request, "Movimiento conciliado; los duplicados no se vuelven a sumar.")
            else:
                return HttpResponseBadRequest("Acción de planificación inválida.")
        except (ValueError, TypeError, ValidationError, IntegrityError) as exc:
            detail = "; ".join(exc.messages) if isinstance(exc, ValidationError) else str(exc)
            messages.error(request, detail)
        return redirect("planning")

    plan = vitali_restock_plan(dataset, today)
    targets = RestockTarget.objects.all()
    stock_counts = LocationStockSnapshot.objects.order_by("-observed_on", "-created_at")[:100]
    incoming = LocationIncoming.objects.order_by("-due_date", "-created_at")[:100]
    batch = active_inventory()
    dated_flows = DatedFlow.objects.filter(due_date__gte=today).filter(
        Q(batch=batch) | Q(batch__isnull=True)
    ).select_related("batch").order_by("due_date", "client", "product")[:100] if batch else (
        DatedFlow.objects.filter(batch__isnull=True, due_date__gte=today)
        .order_by("due_date", "client", "product")[:100]
    )
    for flow in dated_flows:
        flow.candidate_order_lines = list(OrderLine.objects.filter(
            order__client=flow.client, product=flow.product,
        ).select_related("order").order_by("-order__created_at", "pk")[:50])
    client_movements = list(ClientStockMovement.objects.filter(
        effective_date__gte=today,
    ).select_related("reported_by", "related_order_line").order_by("effective_date", "client", "product")[:100])
    for movement in client_movements:
        movement.candidate_order_lines = list(OrderLine.objects.filter(
            order__client=movement.client, product=movement.product,
        ).select_related("order").order_by("-order__created_at", "pk")[:50])
    return TemplateResponse(request, "operations/planning.html", _base(
          request, today=today, events=CommercialEvent.objects.all(), clients=planning_clients,
          products=planning_products, locations=locations, targets=targets,
          stock_counts=stock_counts, incoming=incoming, dated_flows=dated_flows,
          client_movements=client_movements,
        restock_plan=plan,
    ))


class SmartLoginView(LoginView):
    template_name = "operations/login.html"
    redirect_authenticated_user = True

    def get_success_url(self):
        target = super().get_success_url()
        return reverse("pilot_dashboard") if self.request.user.is_staff and target == reverse("dashboard") else target

    def dispatch(self, request, *args, **kwargs):
        if not User.objects.exists() and _is_local(request):
            return redirect("setup")
        return super().dispatch(request, *args, **kwargs)


@require_http_methods(["GET", "POST"])
def setup(request):
    if User.objects.exists():
        return redirect("login")
    local_setup = _is_local(request)
    if not local_setup and len(settings.SMARTORDER_SETUP_CODE) < 32:
        raise PermissionDenied("La cuenta inicial requiere un código privado de al menos 32 caracteres.")
    if request.method == "POST":
        setup_code = request.POST.get("setup_code", "")
        if not local_setup and not secrets.compare_digest(setup_code, settings.SMARTORDER_SETUP_CODE):
            messages.error(request, "El código de instalación no es válido.")
        else:
            username = request.POST.get("username", "").strip()
            password = request.POST.get("password", "")
            if not username:
                messages.error(request, "Escriba un nombre de usuario.")
            else:
                try:
                    validate_password(password)
                    with transaction.atomic():
                        if User.objects.exists():
                            raise ValueError("La cuenta inicial ya existe.")
                        user = User.objects.create_superuser(username=username, password=password)
                    login(request, user)
                    return redirect("dashboard")
                except (ValidationError, ValueError, IntegrityError) as exc:
                    message = "; ".join(exc.messages) if isinstance(exc, ValidationError) else str(exc)
                    messages.error(request, message)
    return TemplateResponse(request, "operations/setup.html", {
        "role": "setup", "remote_setup": not local_setup,
    })


@login_required
def dashboard(request):
    if not request.user.is_staff:
        clients = _assigned_clients(request.user)
        today = timezone.localdate()
        _ensure_event_notifications(request.user, today)
        selected = request.GET.get("client", "")
        if selected and selected not in clients:
            raise PermissionDenied("El cliente no pertenece a su cartera.")
        attention = OrderRequest.objects.filter(
            client__in=clients, sale_confirmed=True,
            status__in=(OrderRequest.Status.PENDING, OrderRequest.Status.REVIEWED,
                        OrderRequest.Status.EXPORTED),
        ).order_by("-updated_at")[:5]
        upcoming = OrderRequest.objects.filter(
            client__in=clients, sale_confirmed=True, committed_date__gte=today,
            status__in=(OrderRequest.Status.REVIEWED, OrderRequest.Status.EXPORTED),
        ).order_by("committed_date", "pk")[:5]
        opportunities = []
        if dataset := active_sales():
            for event in CommercialEvent.objects.filter(
                active=True, reviewed=True,
                start_date__range=(today, today + timedelta(days=7)),
            ):
                territory = event.territory.strip().casefold()
                for client in clients:
                    zone = set(SalesLine.objects.filter(dataset=dataset, client=client)
                               .values_list("zone", flat=True).distinct())
                    if territory not in {"nacional", "national", "el salvador", ""} and not any(
                        item.casefold() == territory for item in zone
                    ):
                        continue
                    product_row = SalesLine.objects.filter(dataset=dataset, client=client).values(
                        "product"
                    ).annotate(total=Sum("quantity")).order_by("-total", "product").first()
                    if not product_row:
                        continue
                    product = product_row["product"]
                    opportunities.append({
                        "event_id": event.pk, "client": client, "product": product,
                        "event": event.title,
                        "date": event.start_date,
                        "url": reverse("order_create") + "?" + urlencode({
                            "client": client, "product": product,
                            "required_date": event.start_date.isoformat(), "event_id": event.pk,
                        }),
                    })
                    if len(opportunities) == 3:
                        break
                if len(opportunities) == 3:
                    break
        return TemplateResponse(request, "operations/seller_dashboard.html", _base(
            request, clients=clients,
            order_count=OrderRequest.objects.filter(client__in=clients).count(),
            attention_orders=[_seller_order(order) for order in attention],
            upcoming_orders=[_seller_order(order) for order in upcoming],
            opportunities=opportunities,
        ))
    dataset = active_sales()
    role = _role(request.user)
    clients = (
        list(dataset.lines.values_list("client", flat=True).distinct().order_by("client"))
        if dataset and role == "admin" else _assigned_clients(request.user)
    )
    selected_client = request.GET.get("client", "")
    if selected_client and selected_client not in clients:
        raise PermissionDenied("El cliente no pertenece a su cartera.")
    period_start = dataset.first_date if dataset else date.today()
    period_end = dataset.last_date if dataset else date.today()
    try:
        if request.GET.get("start_date"):
            period_start = date.fromisoformat(request.GET["start_date"])
        if request.GET.get("end_date"):
            period_end = date.fromisoformat(request.GET["end_date"])
        if period_start > period_end:
            raise ValueError("La fecha inicial debe ser anterior a la final.")
    except ValueError as exc:
        messages.error(request, str(exc))
        period_start, period_end = (
            (dataset.first_date, dataset.last_date) if dataset else (date.today(), date.today())
        )
    base_lines = (
        SalesLine.objects.filter(dataset=dataset, date__range=(period_start, period_end))
        if dataset else SalesLine.objects.none()
    )
    if role == "vendedor":
        base_lines = base_lines.filter(client__in=clients)
    if selected_client:
        base_lines = base_lines.filter(client=selected_client)
    revenue = base_lines.aggregate(total=Sum("amount"))["total"] or Decimal("0")
    n_clients = base_lines.values("client").distinct().count()
    n_products = base_lines.values("product").distinct().count()
    pending_qs = OrderRequest.objects.filter(lines__status=OrderLine.Status.PENDING)
    if role == "vendedor":
        pending_qs = pending_qs.filter(seller=request.user)
    pending_count = pending_qs.distinct().count()
    monthly = list(base_lines.annotate(month=TruncMonth("date"))
                   .values("month").annotate(total=Sum("amount")).order_by("month"))
    by_channel = list(base_lines.values("sales_channel")
                      .annotate(total=Sum("amount")).order_by("-total")[:6])
    tops = list(base_lines.values("product").annotate(
        revenue=Sum("amount"), days_with_sale=Count("date", distinct=True)
    ).order_by("-revenue")[:8])
    monthly_sales = [
        {"label": row["month"].strftime("%m/%Y"), "value_display": f"${row['total']:,.2f}"}
        for row in monthly
    ]
    channel_sales = [
        {"label": row["sales_channel"] or "No especificado",
         "value_display": f"${row['total']:,.2f}"}
        for row in by_channel
    ]
    top_products = [
        {"label": row["product"], "value_display": f"${row['revenue']:,.2f}",
         "days_with_sale": row["days_with_sale"]}
        for row in tops
    ]
    source_status = _source_status(dataset)
    return TemplateResponse(request, "operations/dashboard.html", _base(
        request, dataset=dataset, has_sales=base_lines.exists(),
        source_status=source_status, source_name=dataset.filename if dataset else "",
        latest_sale_date=dataset.last_date if dataset else None,
        period_start=period_start, period_end=period_end,
        clients=clients, selected_client=selected_client,
        kpis={
            "sales_usd_display": f"${revenue:,.2f}",
            "clients_display": len(clients) if role == "vendedor" else n_clients,
            "products_display": n_products,
            "pending_orders_display": pending_count,
        },
        monthly_sales=monthly_sales, channel_sales=channel_sales,
        top_products=top_products,
        chart_monthly={
            "labels": [row["month"].strftime("%m/%Y") for row in monthly],
            "values": [float(row["total"]) for row in monthly],
        },
        chart_channels={
            "labels": [row["sales_channel"] or "No especificado" for row in by_channel],
            "values": [float(row["total"]) for row in by_channel],
        },
        chart_products={
            "labels": [row["product"] for row in tops],
            "values": [float(row["revenue"]) for row in tops],
        },
    ))


@admin_required
@require_http_methods(["GET", "POST"])
def sales_upload(request):
    if request.method == "POST":
        try:
            raw, filename = _xlsx_upload(request, "sales_file")
            verified = "verified" in request.POST
            full_snapshot = "confirmed_full_snapshot" in request.POST
            provenance = request.POST.get("provenance", "")
            date_semantics = request.POST.get("date_semantics", "")
            if provenance not in {SalesDataset.Provenance.REAL, SalesDataset.Provenance.SIMULATED}:
                raise ValueError("Indique si el archivo contiene ventas reales o simuladas.")
            if date_semantics not in SalesDataset.DateSemantics.values:
                raise ValueError("Indique si la fecha del archivo es de venta/pedido o de entrega realizada.")
            if verified and "confirmed_origin" not in request.POST:
                raise ValueError("Para verificar la fuente, confirme su procedencia.")
            if verified and not full_snapshot:
                raise ValueError("Confirme que el Excel representa el histórico completo y reemplaza la carga anterior.")
            dataset = import_sales(raw, filename, request.user, verified, full_snapshot,
                                   provenance=provenance, date_semantics=date_semantics)
            messages.success(request, f"Ventas cargadas: {dataset.row_count:,} filas. Fuente {'verificada' if verified else 'sin verificar'}.")
            return redirect("sales_upload")
        except ValueError as exc:
            messages.error(request, str(exc))
        except IntegrityError:
            messages.error(request, "Otro pedido ya cubre alguno de esos días; actualice la página y revise las solicitudes.")
    datasets = SalesDataset.objects.select_related("uploaded_by").order_by("-created_at")[:12]
    active = active_sales()
    preview = None
    if active:
        preview = {
            "filename": active.filename, "first_date": active.first_date,
            "last_date": active.last_date, "row_count": active.row_count,
            "verified": active.verified, "notes": active.notes.splitlines(),
              "provenance": active.get_provenance_display(),
              "date_semantics": active.get_date_semantics_display(),
            "clients": active.lines.values("client").distinct().count(),
            "products": active.lines.values("product").distinct().count(),
        }
    return TemplateResponse(request, "operations/sales_upload.html", _base(
        request, datasets=datasets, preview=preview, dataset=active,
        active_source=active, sales_imports=datasets,
    ))


@admin_required
@require_http_methods(["GET", "POST"])
def inventory_upload(request):
    if request.method == "POST":
        try:
            raw, filename = _xlsx_upload(request, "inventory_file")
            unit_match = "unit_match_confirmed" in request.POST
            batch = import_inventory(raw, filename, request.user, unit_match)
            messages.success(request, f"Inventario oficial cargado: {batch.items.count()} productos por cliente.")
            return redirect("inventory_upload")
        except (ValueError, IntegrityError) as exc:
            messages.error(request, str(exc))
    batch = active_inventory()
    preview = None
    if batch:
        preview = {
            "filename": batch.filename, "items": batch.items.count(),
            "flows": batch.dated_flows.count(),
            "oldest": batch.items.order_by("observed_on").values_list("observed_on", flat=True).first(),
            "newest": batch.items.order_by("-observed_on").values_list("observed_on", flat=True).first(),
        }
    return TemplateResponse(request, "operations/inventory_upload.html", _base(
        request, batch=batch, preview=preview, active_inventory=batch,
        inventory_imports=InventoryBatch.objects.select_related("uploaded_by").order_by("-created_at")[:12],
        inventory_items=batch.items.order_by("client", "product")[:100] if batch else [],
    ))


@admin_required
def inventory_template_download(request):
    response = HttpResponse(
        inventory_template(), content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    response["Content-Disposition"] = 'attachment; filename="plantilla_inventario_vitali.xlsx"'
    return response


@admin_required
@require_http_methods(["GET", "POST"])
def users(request):
    dataset = active_sales()
    client_options = list(dataset.lines.values_list("client", flat=True).distinct().order_by("client")) if dataset else []
    if request.method == "POST":
        action = request.POST.get("action", "create")
        try:
            with transaction.atomic():
                if action == "create":
                    username = request.POST.get("username", "").strip()
                    password = request.POST.get("password", "")
                    role = request.POST.get("role", "")
                    if role not in {"admin", "vendedor"}:
                        raise ValueError("Seleccione un rol válido.")
                    validate_password(password)
                    user = User.objects.create_user(
                        username=username, password=password, is_staff=(role == "admin")
                    )
                    selected = request.POST.getlist("clients") if role == "vendedor" else []
                    _assign(user, selected, client_options)
                    messages.success(request, f"Cuenta {username} creada.")
                elif action == "assign":
                    user = get_object_or_404(User, pk=request.POST.get("user_id"))
                    if user.is_staff:
                        raise ValueError("Las cuentas administrativas no tienen cartera.")
                    _assign(user, request.POST.getlist("clients"), client_options)
                    messages.success(request, f"Cartera de {user.username} actualizada.")
                elif action == "toggle":
                    user = get_object_or_404(User, pk=request.POST.get("user_id"))
                    if user == request.user and user.is_active:
                        raise ValueError("No puede desactivar su propia cuenta.")
                    if user.is_staff and user.is_active and User.objects.filter(
                        is_staff=True, is_active=True
                    ).count() <= 1:
                        raise ValueError("Debe quedar al menos una cuenta administrativa activa.")
                    user.is_active = not user.is_active
                    user.save(update_fields=["is_active"])
                    _invalidate_sessions(user)
                    messages.success(request, "Estado de la cuenta actualizado.")
                elif action == "reset_password":
                    user = get_object_or_404(User, pk=request.POST.get("user_id"))
                    password = request.POST.get("password", "")
                    validate_password(password, user)
                    user.set_password(password)
                    user.save(update_fields=["password"])
                    _invalidate_sessions(user)
                    messages.success(request, f"Contraseña de {user.username} actualizada.")
                elif action == "role":
                    user = get_object_or_404(User.objects.select_for_update(), pk=request.POST.get("user_id"))
                    role = request.POST.get("role")
                    if role not in {"admin", "vendedor"}:
                        raise ValueError("Seleccione un rol válido.")
                    if role == "vendedor" and user.is_staff and User.objects.filter(is_staff=True, is_active=True).count() <= 1:
                        raise ValueError("Debe quedar al menos un administrador activo.")
                    user.is_staff = role == "admin"
                    user.is_superuser = False
                    user.save(update_fields=["is_staff", "is_superuser"])
                    if user.is_staff:
                        Assignment.objects.filter(user=user).delete()
                    _invalidate_sessions(user)
                    messages.success(request, "Rol actualizado; se cerraron las sesiones de la cuenta.")
                else:
                    raise ValueError("Acción de usuario desconocida.")
                AccessEvent.objects.create(actor=request.user, action=action, target=str(user.pk),
                    details={"role": _role(user), "active": user.is_active,
                             "clients": _assigned_clients(user)})
            return redirect("users")
        except (ValueError, ValidationError, IntegrityError) as exc:
            message = "; ".join(exc.messages) if isinstance(exc, ValidationError) else str(exc)
            messages.error(request, message)
    accounts = list(User.objects.order_by("username"))
    assignments = {}
    for assignment in Assignment.objects.select_related("user"):
        assignments.setdefault(assignment.user_id, []).append(assignment.client)
    display_users = [
        {"id": account.pk, "username": account.username,
         "role": "admin" if account.is_staff else "vendedor",
         "is_active": account.is_active,
         "assigned_clients": assignments.get(account.pk, [])}
        for account in accounts
    ]
    return TemplateResponse(request, "operations/users.html", _base(
        request, accounts=accounts, users=display_users, assignments=assignments,
        access_events=AccessEvent.objects.select_related("actor").order_by("-created_at")[:50],
        clients=client_options, client_options=client_options,
    ))


def _assign(user, selected: list[str], valid_clients: list[str]):
    if len(selected) != len(set(selected)):
        raise ValueError("No repita clientes en la asignación.")
    if any(client not in valid_clients for client in selected):
        raise ValueError("Solo puede asignar clientes del archivo de ventas activo.")
    previous = list(User.objects.filter(assigned_clients__client__in=selected).exclude(pk=user.pk).distinct())
    Assignment.objects.filter(client__in=selected).exclude(user=user).delete()
    Assignment.objects.filter(user=user).delete()
    Assignment.objects.bulk_create([Assignment(user=user, client=client) for client in selected])
    for account in [user, *previous]:
        _invalidate_sessions(account)


@login_required
def recommendations(request):
    dataset = active_sales()
    today = timezone.localdate()
    default_delivery = default_delivery_date(dataset, today)
    clients = _assigned_clients(request.user) if not request.user.is_staff else (
        list(dataset.lines.values_list("client", flat=True).distinct().order_by("client")) if dataset else []
    )
    client = request.GET.get("client", "")
    if client and client not in clients:
        raise PermissionDenied("El cliente no pertenece a su cartera.")
    if not client and clients:
        client = clients[0]
    try:
        delivery = _required_date(request.GET.get("required_date", default_delivery.isoformat()))
    except ValueError as exc:
        messages.error(request, str(exc))
        delivery = default_delivery
    mode = "vitali" if request.user.is_staff else request.GET.get("mode", "sale")
    if mode not in {"sale", "client_restock", "vitali"} or (not request.user.is_staff and mode == "vitali"):
        raise PermissionDenied("Modo de sugerencia inválido.")
    event_id = request.GET.get("event_id")
    cards = recommendation_cards(dataset, delivery, client, today=today, mode=mode,
                                 event_id=event_id) if dataset and client else []
    safe_cards = _seller_cards(commercial.decorate_cards(cards, client))
    return TemplateResponse(request, "operations/recommendations.html", _base(
        request, clients=clients, selected_client=client, client=client,
        suggestion_mode=mode,
        required_date=delivery, horizon_end=delivery + timedelta(days=6),
        min_date=today, max_date=today + timedelta(days=settings.SMARTORDER_DELIVERY_DAYS),
        recommendations=cards if request.user.is_staff else safe_cards,
        catalog_available=bool(dataset and dataset.lines.exists()),
        client_in_catalog=bool(dataset and client and dataset.lines.filter(client=client).exists()),
        dataset=dataset if request.user.is_staff else None,
        source_status=_source_status(dataset) if request.user.is_staff else "",
        source_name=dataset.filename if dataset and request.user.is_staff else "",
        latest_sale_date=dataset.last_date if dataset and request.user.is_staff else None,
    ))


@login_required
@require_http_methods(["GET", "POST"])
def order_create(request):
    if request.user.is_staff:
        raise PermissionDenied("Los pedidos los crea el vendedor.")
    dataset = active_sales()
    today = timezone.localdate()
    default_delivery = default_delivery_date(dataset, today)
    if not dataset:
        messages.error(request, "El catálogo aún no está disponible. Administración debe habilitarlo.")
        return redirect("dashboard")
    clients = _assigned_clients(request.user)
    refresh_only = request.method == "POST" and request.POST.get("action") == "refresh"
    client = request.POST.get("client", "") if request.method == "POST" else request.GET.get("client", "")
    if not client and clients:
        client = clients[0]
    if not clients and request.method == "GET":
        if client:
            raise PermissionDenied("El cliente no pertenece a su cartera.")
        messages.warning(request, "Aún no tienes clientes asignados. Administración debe asignarte una cartera.")
        return TemplateResponse(request, "operations/order_form.html", _base(
            request, clients=[], selected_client="", products=[], recommendations=[],
            required_date=default_delivery, window_end=default_delivery + timedelta(days=6),
            min_date=today, max_date=today + timedelta(days=settings.SMARTORDER_DELIVERY_DAYS),
            source_status=_source_status(dataset), no_clients=True,
        ))
    if client not in clients:
        raise PermissionDenied("El cliente no pertenece a su cartera.")
    mode = request.POST.get("suggestion_mode", "sale") if request.method == "POST" else request.GET.get("mode", "sale")
    if mode not in {"sale", "client_restock"}:
        raise PermissionDenied("Modo de sugerencia inválido.")
    previous_client = request.POST.get("previous_client", client) if request.method == "POST" else client
    client_context_changed = bool(refresh_only and previous_client != client)
    date_error = False
    try:
        delivery = _required_date(
            request.POST.get("required_date", "") if request.method == "POST"
            else request.GET.get("required_date", default_delivery.isoformat())
        )
    except ValueError as exc:
        messages.error(request, str(exc))
        delivery = default_delivery
        date_error = True
    event_id = request.POST.get("event_id", "") if request.method == "POST" else request.GET.get("event_id", "")
    cards = recommendation_cards(dataset, delivery, client, today=today, mode=mode,
                                 event_id=event_id)
    token = request.POST.get("submission_token") if request.method == "POST" else commercial.submission_token(request.user)
    if request.method == "POST" and not refresh_only:
        try:
            if date_error:
                raise ValueError("Corrija la fecha solicitada; el pedido no fue confirmado.")
            identity, fingerprint = commercial.submission_identity(token, request.user, request.POST)
            previous = OrderRequest.objects.filter(submission_id=identity, seller=request.user).first()
            if previous:
                if previous.submission_hash != fingerprint:
                    raise ValueError("Este envío ya fue registrado con otro contenido. Abra un pedido nuevo.")
                messages.success(request, f"El pedido {previous.pk} ya estaba registrado; no se duplicó.")
                return redirect("order_detail", order_id=previous.pk)
            exception_reason = request.POST.get("exception_reason", "").strip()
            if len(exception_reason) > 2000:
                raise ValueError("La excepción debe tener hasta 2,000 caracteres.")
            products = request.POST.getlist("product")
            quantities = request.POST.getlist("quantity")
            units = request.POST.getlist("unit")
            reasons = request.POST.getlist("reason")
            prices = request.POST.getlist("unit_price") or [""] * len(products)
            discounts = request.POST.getlist("discount_percent") or [""] * len(products)
            if len(prices) != len(products) or len(discounts) != len(products):
                raise ValueError("Los precios y descuentos están incompletos.")
            if not (len(products) == len(quantities) == len(units) == len(reasons)):
                raise ValueError("Las líneas del pedido están incompletas.")
            if len(products) > 100:
                raise ValueError("El pedido admite hasta 100 productos.")
            by_product = {card["product"].casefold(): card for card in cards}
            chosen = []
            seen = set()
            for product, amount, unit, reason, price, discount in zip(products, quantities, units, reasons, prices, discounts):
                product = product.strip()
                if not str(amount).strip():
                    if product.casefold() in by_product:
                        continue
                    if not product and not unit.strip() and not reason.strip():
                        continue
                    raise ValueError("Complete producto y cantidad en cada línea utilizada.")
                if not product:
                    raise ValueError("Complete producto y cantidad en cada línea utilizada.")
                product_key = product.casefold()
                if product_key in seen:
                    raise ValueError(f"Producto repetido: {product}.")
                seen.add(product_key)
                if len(product) > 255:
                    raise ValueError("El nombre de producto es demasiado largo.")
                quantity = nonnegative_decimal(amount, "Cantidad solicitada", positive=True)
                unit = unit.strip()
                if not unit or len(unit) > 50:
                    raise ValueError(f"{product}: indique una unidad de medida válida.")
                reason = reason.strip()
                card = by_product.get(product_key)
                if card:
                    product = card["product"]
                if card is None and not reason:
                    raise ValueError(f"{product}: explique por qué solicita un producto nuevo.")
                if card is not None and (
                    card["suggested"] is None or
                    abs(quantity - card["suggested"]) > Decimal("0.001")
                ) and not reason:
                    raise ValueError(f"{product}: explique la cantidad manual o el ajuste.")
                if len(reason) > 2000:
                    raise ValueError("El motivo debe tener 2,000 caracteres o menos.")
                price, discount = commercial.commercial_price(client, product, unit, price, discount, exception_reason)
                chosen.append((product, unit, quantity, reason, card, price, discount))
            if not chosen:
                raise ValueError("Indique al menos un producto con cantidad positiva.")
            with transaction.atomic():
                # Lock the client owner while confirming the sale; distinct sales
                # may share a demand week, unlike the legacy replenishment requests.
                owner = Assignment.objects.select_for_update().filter(client=client, user=request.user).first()
                if owner is None:
                    raise PermissionDenied("El cliente ya no pertenece a su cartera.")
                previous = OrderRequest.objects.filter(submission_id=identity).first()
                if previous:
                    if previous.submission_hash != fingerprint or previous.seller_id != request.user.pk:
                        raise ValueError("El identificador de envío ya tiene otro contenido.")
                    return redirect("order_detail", order_id=previous.pk)
                for product, unit, quantity, reason, card, price, discount in chosen:
                    if not exception_reason:
                        if not PriceTariff.objects.select_for_update().filter(client=client, product=product).exists():
                            raise ValueError("La tarifa cambió. Revise el pedido nuevamente.")
                        commercial.commercial_price(client, product, unit, price, discount)
                order = OrderRequest.objects.create(
                    seller=request.user, client=client, dataset=dataset,
                    inventory_batch=active_inventory(), required_date=delivery,
                    note=request.POST.get("note", "").strip()[:2000],
                    sale_confirmed=not bool(exception_reason), exception_reason=exception_reason,
                    sale_confirmed_at=timezone.now() if not exception_reason else None,
                    submission_id=identity, submission_hash=fingerprint,
                    payment_terms=request.POST.get("payment_terms", "").strip()[:255],
                )
                for product, unit, quantity, reason, card, price, discount in chosen:
                    available = (card or {}).get("available")
                    # ponytail: future quantity is a snapshot, not a stock reservation;
                    # Odoo will own reservations and fulfillment in the integration phase.
                    future = None
                    if (available is not None and card["stock_date"] == timezone.localdate()
                            and card.get("stock_unit") and unit.strip().casefold() == card["stock_unit"].strip().casefold()
                            and not card.get("inventory_warning")):
                        future = max(Decimal("0"), quantity - available)
                    line = OrderLine.objects.create(
                        order=order, product=product, unit=unit,
                        reference_qty=(card or {}).get("reference_qty"),
                        method=(card or {}).get("method", "Producto nuevo, pedido manual"),
                        suggested_qty=(card or {}).get("suggested"),
                        suggestion_mode=mode,
                        suggestion_reason=(card or {}).get("explanation", "")[:2000],
                        suggestion_event=(card or {}).get("suggestion_event", "")[:255],
                        suggestion_as_of=today,
                        requested_qty=quantity, reason=reason,
                        unit_price=price, discount_percent=discount, future_quantity=future,
                    )
                AccessEvent.objects.create(actor=request.user, action="sale_confirm", target=str(order.pk), details={"client": client, "lines": len(chosen)})
            _notification(f"order:{order.pk}:submitted", "Venta confirmada; producción pendiente", request, order)
            if order.sale_confirmed:
                odoo_service.try_synchronize(order)
            messages.success(request, f"Venta {order.pk} confirmada. La autorización de producción es independiente."
                             if order.sale_confirmed else f"Pedido {order.pk} pendiente de aprobación de excepción comercial; no confirma venta.")
            return redirect("order_detail", order_id=order.pk)
        except IntegrityError:
            messages.error(request, "No se pudo registrar el pedido. Reintente con los mismos datos para comprobar su estado.")
        except ValueError as exc:
            messages.error(request, str(exc))
    safe_cards = _seller_cards(commercial.decorate_cards(cards, client))
    submitted = {name: request.POST.getlist(name) for name in ("product", "quantity", "unit", "reason", "unit_price", "discount_percent")}
    incompatible_lines = []
    for card in safe_cards:
        if card["product"] in submitted["product"]:
            index = submitted["product"].index(card["product"])
            old_quantity = submitted["quantity"][index] if index < len(submitted["quantity"]) else ""
            old_unit = submitted["unit"][index] if index < len(submitted["unit"]) else ""
            old_reason = submitted["reason"][index] if index < len(submitted["reason"]) else ""
            if client_context_changed:
                expected_unit = (card.get("order_unit") or card.get("unit")) or ""
                compatible = bool(old_unit.strip() and expected_unit.strip()
                                  and old_unit.strip().casefold() == expected_unit.strip().casefold())
                card["entered_quantity"] = old_quantity if compatible else ""
                card["entered_reason"] = old_reason if compatible else ""
                card["entered_unit"] = expected_unit
                if not compatible and (old_quantity.strip() or old_reason.strip()):
                    incompatible_lines.append(card["product"])
            else:
                card["entered_quantity"] = old_quantity
                card["entered_unit"] = old_unit
                card["entered_reason"] = old_reason
                for name in ("unit_price", "discount_percent"):
                    if index < len(submitted[name]):
                        card["entered_" + name] = submitted[name][index]
    initial_product = (
        request.GET.get("product", "") if request.method == "GET"
        else request.POST.get("selected_product", "")
    )
    selected_products = request.POST.getlist("selected_product") if request.method == "POST" else []
    if initial_product and initial_product not in selected_products:
        selected_products.append(initial_product)
    known_product_names = {card["product"] for card in safe_cards}
    if client_context_changed:
        incompatible_lines.extend(product for product in selected_products
                                  if product not in known_product_names)
        if request.POST.get("exception_reason", "").strip():
            incompatible_lines.append("Excepción comercial")
    selected_products = [product for product in selected_products if product in known_product_names]
    extra_line = {}
    known_products = {card["product"] for card in safe_cards}
    for index, product in enumerate(submitted["product"]):
        if product not in known_products:
            if client_context_changed:
                if any(values[index].strip() for values in submitted.values() if index < len(values)):
                    incompatible_lines.append("Producto adicional")
            else:
                extra_line = {name: values[index] for name, values in submitted.items() if index < len(values)}
    form_data = request.POST.copy() if request.method == "POST" else {}
    if client_context_changed:
        form_data["exception_reason"] = ""
        if incompatible_lines:
            messages.warning(
                request,
                "Se regeneraron las condiciones del nuevo cliente y se limpiaron estas líneas incompatibles: "
                + ", ".join(dict.fromkeys(incompatible_lines)) + ".",
            )
        else:
            messages.info(request, "Unidad y tarifa se actualizaron para el nuevo cliente.")
    return TemplateResponse(request, "operations/order_form.html", _base(
        request, clients=clients, selected_client=client, client=client,
        required_date=delivery, horizon_end=delivery + timedelta(days=6),
        window_end=delivery + timedelta(days=6),
        min_date=today, max_date=today + timedelta(days=settings.SMARTORDER_DELIVERY_DAYS),
        source_status="",
        recommendations=safe_cards, products=safe_cards, dataset=None,
        product_lines=sorted({card.get("category") or "Sin línea" for card in safe_cards}),
        suggestion_mode=mode,
        submission_token=token or commercial.submission_token(request.user),
        event_id=event_id,
        selected_products=selected_products,
        selected_product=initial_product if initial_product in known_product_names else "",
        form_data=form_data, incompatible_lines=list(dict.fromkeys(incompatible_lines)),
        extra_line=extra_line,
    ))


@login_required
def orders(request):
    rows = OrderRequest.objects.select_related("seller", "dataset").prefetch_related("lines")
    if not request.user.is_staff:
        rows = rows.filter(client__in=_assigned_clients(request.user))
    source = active_sales()
    displayed = list(rows.order_by("-created_at")[:100])
    for order in displayed:
        order.line_count = order.lines.count()
        order.status_display = order.get_status_display()
    line_qs = OrderLine.objects.filter(order__in=displayed)
    approved = (
        [line for line in OrderLine.objects.filter(status=OrderLine.Status.APPROVED)
         .select_related("order", "order__dataset", "order__seller")
         .order_by("order__required_date", "product")
         if _sources_current(line.order)]
        if request.user.is_staff else []
    )
    return TemplateResponse(request, "operations/orders.html", _base(
        request, orders=displayed if request.user.is_staff else [_seller_order(order) for order in displayed],
        source_status=_source_status(source) if request.user.is_staff else "",
        approved_lines=approved,
        order_counts={
            "pending": line_qs.filter(status=OrderLine.Status.PENDING).count(),
            "approved": line_qs.filter(status=OrderLine.Status.APPROVED).count(),
            "exported": line_qs.filter(status=OrderLine.Status.EXPORTED).count(),
        },
    ))


@login_required
def order_detail(request, order_id: int):
    order = get_object_or_404(
        OrderRequest.objects.select_related("seller", "dataset"), pk=order_id
    )
    if not _client_allowed(request.user, order.client):
        raise PermissionDenied("Este pedido no pertenece a su cuenta.")
    order_lines = list(order.lines.select_related("reviewer").order_by("id"))
    changeable = order.sale_confirmed and order.status != OrderRequest.Status.CANCELLED and all(
        line.status == OrderLine.Status.PENDING for line in order_lines
    )
    changes = {
        "can_edit_sale": changeable and (request.user.is_staff or settings.SMARTORDER_SELLER_EDIT),
        "can_cancel_sale": changeable and (request.user.is_staff or settings.SMARTORDER_SELLER_CANCEL),
    }
    changes['odoo'] = odoo_service.order_context(order, admin=request.user.is_staff)
    if request.user.is_staff:
        changes['odoo']['can_authorize'] = changes['odoo']['can_authorize'] and _sources_current(order) and order.status != OrderRequest.Status.CANCELLED
    if not request.user.is_staff:
        fields = ("id", "product", "unit", "requested_qty", "suggested_qty", "reason",
                  "unit_price", "discount_percent", "future_quantity", "status",
                  "approved_qty", "review_note")
        lines = [{**{key: getattr(line, key) for key in fields},
                  "get_status_display": line.get_status_display()} for line in order_lines]
        return TemplateResponse(request, "operations/seller_order_detail.html", _base(
            request, order=_seller_order(order), lines=lines, **changes,
        ))
    return TemplateResponse(request, "operations/order_detail.html", _base(
        request, order=order, lines=order_lines, **changes,
        order_window_end=order.required_date + timedelta(days=6),
        source_is_active=order.dataset_id == (active_sales().pk if active_sales() else None),
        sources_current=_sources_current(order),
        current_sales=active_sales(), current_inventory=active_inventory(),
    ))


@login_required
@require_POST
def sale_change(request, order_id: int):
    with transaction.atomic():
        order = get_object_or_404(OrderRequest.objects.select_for_update(), pk=order_id)
        if not _client_allowed(request.user, order.client):
            raise PermissionDenied("El cliente no pertenece a su cartera.")
        lines = list(order.lines.select_for_update().order_by("id"))
        if not order.sale_confirmed or order.status == OrderRequest.Status.CANCELLED or any(
            line.status != OrderLine.Status.PENDING for line in lines
        ):
            messages.error(request, "La ejecución ya comenzó; contacte a administración para registrar la corrección.")
            return redirect("order_detail", order_id=order.pk)
        reason = request.POST.get("change_reason", "").strip()
        if not reason or len(reason) > 2000:
            messages.error(request, "Indique un motivo de hasta 2,000 caracteres.")
            return redirect("order_detail", order_id=order.pk)
        action = request.POST.get("action")
        if action == "cancel":
            if not request.user.is_staff and not settings.SMARTORDER_SELLER_CANCEL:
                raise PermissionDenied("La cancelación requiere administración según la configuración.")
            if OdooOrderLink.objects.filter(order=order).exists() and odoo_service.configuration().get('enabled'):
                try:
                    odoo_service.synchronize(order, 'cancel')
                except odoo_service.OdooError as exc:
                    messages.error(request, str(exc) if settings.SMARTORDER_MAINTENANCE_UI and request.user.is_staff else "No se pudo confirmar la cancelación. El pedido se conserva; vuelva a intentarlo.")
                    return redirect('order_detail', order_id=order.pk)
            order.status = OrderRequest.Status.CANCELLED
            order.save(update_fields=["status", "updated_at"])
            order.lines.update(status=OrderLine.Status.REJECTED, review_note=reason)
        elif action == "edit":
            if not request.user.is_staff and not settings.SMARTORDER_SELLER_EDIT:
                raise PermissionDenied("La edición requiere administración según la configuración.")
            try:
                line = next((line for line in lines if str(line.pk) == request.POST.get("line_id")), None)
                if line is None:
                    raise ValueError("Producto inválido.")
                quantity = nonnegative_decimal(request.POST.get("quantity"), "Cantidad", positive=True)
                price = nonnegative_decimal(request.POST.get("unit_price"), "Precio", places=4)
                discount = nonnegative_decimal(request.POST.get("discount_percent"), "Descuento")
                if discount > 100 or discount != discount.quantize(Decimal("0.01")):
                    raise ValueError("Descuento entre 0 y 100 %, con hasta dos decimales.")
                if not request.user.is_staff and (price != line.unit_price or discount != line.discount_percent):
                    raise ValueError("El precio y descuento están fijados por administración.")
            except ValueError as exc:
                messages.error(request, str(exc))
                return redirect("order_detail", order_id=order.pk)
            previous_link = OdooOrderLink.objects.filter(order=order).values('revision').first()
            try:
                # A savepoint reverts the edit while the outer transaction preserves its pending error.
                with transaction.atomic():
                    AccessEvent.objects.create(actor=request.user, action="sale_edit_before", target=str(order.pk),
                        details={"line": line.pk, "quantity": str(line.requested_qty),
                                 "price": str(line.unit_price), "discount": str(line.discount_percent)})
                    line.requested_qty, line.unit_price, line.discount_percent = quantity, price, discount
                    line.reason = reason
                    line.future_quantity = None  # edited stock needs rechecking, never reuse a stale snapshot
                    line.save(update_fields=["requested_qty", "unit_price", "discount_percent", "reason", "future_quantity"])
                    odoo_service.changed(order)
                    if previous_link and odoo_service.configuration().get('enabled'):
                        odoo_service.synchronize(order)
            except odoo_service.OdooError as exc:
                if previous_link:
                    OdooOrderLink.objects.filter(order=order, revision=previous_link['revision']).update(
                        status='error', last_error=str(exc)[:500], updated_at=timezone.now())
                messages.error(request, str(exc) if settings.SMARTORDER_MAINTENANCE_UI and request.user.is_staff else "No se pudo confirmar el cambio. Se conserva la versión anterior; vuelva a intentarlo.")
                return redirect('order_detail', order_id=order.pk)
        else:
            return HttpResponseBadRequest("Acción inválida.")
        AccessEvent.objects.create(actor=request.user, action="sale_" + action, target=str(order.pk), details={"reason": reason})
    if action == 'edit':
        odoo_service.try_synchronize(order)
    messages.success(request, "Venta actualizada; el historial de cambios se conserva.")
    return redirect("order_detail", order_id=order.pk)


@admin_required
@require_POST
def order_revalidate(request, order_id: int):
    if request.POST.get("confirm_revalidation") != "1":
        messages.error(request, "Confirme que revisó ventas e inventario vigentes para este pedido.")
        return redirect("order_detail", order_id=order_id)
    with transaction.atomic():
        order = get_object_or_404(OrderRequest.objects.select_for_update(), pk=order_id)
        if order.status == OrderRequest.Status.EXPORTED:
            raise PermissionDenied("Un pedido exportado ya no se puede revalidar.")
        sales = active_sales()
        if sales is None:
            raise PermissionDenied("No hay ventas activas.")
        order.revalidated_against = sales
        order.inventory_revalidated_against = active_inventory()
        order.revalidated_by = request.user
        order.revalidated_at = timezone.now()
        if order.status == OrderRequest.Status.SUPERSEDED:
            order.status = (
                OrderRequest.Status.PENDING if order.lines.filter(
                    status=OrderLine.Status.PENDING
                ).exists() else OrderRequest.Status.REVIEWED
            )
        order.save(update_fields=[
            "revalidated_against", "inventory_revalidated_against", "revalidated_by",
            "revalidated_at", "status", "updated_at",
        ])
    messages.success(request, "Disponibilidad revisada. Compruebe cada cantidad antes de autorizar producción.")
    return redirect("order_detail", order_id=order.pk)


@admin_required
@require_POST
def order_review(request, order_id: int):
    with transaction.atomic():
        order = get_object_or_404(OrderRequest.objects.select_for_update(), pk=order_id)
        if order.exception_reason and not order.sale_confirmed:
            raise PermissionDenied("Apruebe primero la excepción comercial.")
        if not _sources_current(order):
            raise PermissionDenied("La disponibilidad cambió; revise las cantidades antes de autorizar producción.")
        line = get_object_or_404(OrderLine.objects.select_for_update(), pk=request.POST.get("line_id"), order=order)
        if line.status != OrderLine.Status.PENDING:
            messages.error(request, "Esta línea ya fue revisada.")
            return redirect("order_detail", order_id=order.pk)
        decision = request.POST.get("decision", "")
        note = request.POST.get("review_note", "").strip()
        if decision == "approve":
            try:
                quantity = nonnegative_decimal(
                    request.POST.get("approved_quantity", line.requested_qty),
                    "Cantidad aprobada", positive=True,
                )
            except ValueError as exc:
                messages.error(request, str(exc))
                return redirect("order_detail", order_id=order.pk)
            if quantity != line.requested_qty and not note:
                messages.error(request, "Explique el cambio de cantidad.")
                return redirect("order_detail", order_id=order.pk)
            if quantity > line.requested_qty and OdooOrderLink.objects.filter(order=order).exists():
                messages.error(request, "La fabricación vinculada a esta venta no debe superar lo vendido. Registre la producción adicional por separado en Odoo.")
                return redirect("order_detail", order_id=order.pk)
            line.status = OrderLine.Status.APPROVED
            line.approved_qty = quantity
        elif decision == "reject":
            if not note:
                messages.error(request, "Explique el rechazo.")
                return redirect("order_detail", order_id=order.pk)
            line.status = OrderLine.Status.REJECTED
            line.approved_qty = Decimal("0")
        else:
            return HttpResponseBadRequest("Decisión inválida.")
        line.reviewer = request.user
        line.review_note = note[:2000]
        line.reviewed_at = timezone.now()
        line.save()
        if line.status == OrderLine.Status.REJECTED:
            line.coverages.all().delete()
        if not order.lines.filter(status=OrderLine.Status.PENDING).exists():
            order.status = OrderRequest.Status.REVIEWED
            order.save(update_fields=["status", "updated_at"])
    _notification(f"order:{order.pk}:review:{line.pk}", "Pedido revisado", request, order)
    if line.status == OrderLine.Status.APPROVED:
        odoo_service.try_synchronize(order, authorize=True)
    messages.success(request, "Producto revisado.")
    return redirect("order_detail", order_id=order.pk)


@admin_required
@require_POST
def export_batch(request):
    action = request.POST.get("action", "download")
    try:
        ids = sorted({int(value) for value in request.POST.getlist("line_ids")})
    except ValueError:
        return HttpResponseBadRequest("Selección inválida.")
    if not ids or len(ids) > 500:
        messages.error(request, "Seleccione entre 1 y 500 productos aprobados.")
        return redirect("orders")
    with transaction.atomic():
        lines = list(OrderLine.objects.select_for_update().select_related(
            "order", "order__seller", "reviewer"
        ).filter(pk__in=ids).order_by("id"))
        if len(lines) != len(ids) or any(
            line.status != OrderLine.Status.APPROVED or
            not _sources_current(line.order)
            for line in lines
        ):
            raise PermissionDenied("Solo puede exportar productos aprobados de la fuente activa.")
        if action == "confirm":
            if "shared_confirm" not in request.POST:
                messages.error(request, "Confirme que compartió el archivo con producción.")
                return redirect("orders")
            if request.session.get("prepared_export_line_ids") != ids:
                messages.error(request, "Descargue primero exactamente este lote y luego confirme el envío.")
                return redirect("orders")
            now = timezone.now()
            for line in lines:
                line.status = OrderLine.Status.EXPORTED
                line.exported_at = now
            OrderLine.objects.bulk_update(lines, ["status", "exported_at"])
            for order in {line.order for line in lines}:
                if not order.lines.filter(status__in=[
                    OrderLine.Status.PENDING, OrderLine.Status.APPROVED
                ]).exists():
                    order.status = OrderRequest.Status.EXPORTED
                    order.save(update_fields=["status", "updated_at"])
            request.session.pop("prepared_export_line_ids", None)
            messages.success(request, "Lote marcado como compartido con producción.")
            return redirect("orders")
        if action != "download":
            return HttpResponseBadRequest("Acción desconocida.")
        records = [{
            "estado": "Aprobado", "id": line.pk, "cliente": line.order.client,
            "producto": line.product, "unidad": line.unit,
            "cantidad_produccion": line.approved_qty,
            "fecha_requerida": line.order.required_date.isoformat(),
            "fecha_pronostico": line.order.required_date.isoformat(),
            "usuario": line.order.seller.username, "revisor": line.reviewer.username,
            "fecha_revision": line.reviewed_at.date().isoformat(),
            "nota_revision": line.review_note,
        } for line in lines]
        payload = export_production(records)
        request.session["prepared_export_line_ids"] = ids
    response = HttpResponse(
        payload, content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    response["Content-Disposition"] = 'attachment; filename="pedidos_produccion_vitali.xlsx"'
    return response


@login_required
@require_http_methods(["GET", "POST"])
def correction_requests(request):
    batch = active_inventory()
    if request.method == "POST" and not request.user.is_staff:
        try:
            client = request.POST.get("client", "").strip()
            product = request.POST.get("product", "").strip()
            if not _client_allowed(request.user, client):
                raise PermissionDenied("Cliente fuera de su cartera.")
            if not product:
                raise ValueError("Seleccione un producto.")
            proposed = nonnegative_decimal(request.POST.get("claimed_stock"), "Existencia propuesta")
            reason = request.POST.get("reason", "").strip()
            if not reason:
                raise ValueError("Explique la corrección solicitada.")
            item = InventoryItem.objects.filter(batch=batch, client=client, product=product).first() if batch else None
            if item is None:
                raise ValueError("No hay existencia oficial de este producto para solicitar corrección.")
            correction = StockCorrectionRequest.objects.create(
                seller=request.user, client=client, product=product,
                unit=item.unit, proposed_available=proposed,
                observed_on=date.today(), reason=reason[:2000],
            )
            _notification(f"correction:{correction.pk}:submitted",
                          "Corrección de inventario pendiente", request)
            messages.success(request, "Corrección enviada a administración.")
            return redirect("correction_requests")
        except ValueError as exc:
            messages.error(request, str(exc))
    elif request.method == "POST":
        action = request.POST.get("action")
        note = request.POST.get("review_note", "").strip()
        if action not in {"approve", "reject"}:
            return HttpResponseBadRequest("Decisión inválida.")
        if action == "reject" and not note:
            messages.error(request, "Explique el rechazo.")
            return redirect("correction_requests")
        try:
            with transaction.atomic():
                correction = get_object_or_404(
                    StockCorrectionRequest.objects.select_for_update(),
                    pk=request.POST.get("correction_id"),
                )
                if correction.status != StockCorrectionRequest.Status.PENDING:
                    raise ValueError("Esta corrección ya fue revisada.")
                batch = active_inventory()
                if action == "approve":
                    if batch is None or batch.created_at > correction.created_at:
                        raise ValueError("El inventario cambió desde la solicitud; pida una nueva corrección.")
                    if not InventoryItem.objects.filter(
                        batch=batch, client=correction.client, product=correction.product
                    ).exists():
                        raise ValueError("El producto ya no figura en el inventario activo.")
                    InventoryAdjustment.objects.create(
                        batch=batch, correction=correction, client=correction.client,
                        product=correction.product, available=correction.proposed_available,
                        observed_on=correction.observed_on, approved_by=request.user,
                    )
                correction.status = (
                    StockCorrectionRequest.Status.APPROVED if action == "approve"
                    else StockCorrectionRequest.Status.REJECTED
                )
                correction.reviewer = request.user
                correction.review_note = note[:2000]
                correction.reviewed_at = timezone.now()
                correction.save()
            messages.success(request, "Corrección revisada.")
        except (ValueError, IntegrityError) as exc:
            messages.error(request, str(exc) if isinstance(exc, ValueError)
                           else "La corrección ya fue aplicada por otra persona.")
        return redirect("correction_requests")
    rows = StockCorrectionRequest.objects.select_related("seller", "reviewer").order_by("-created_at")
    if not request.user.is_staff:
        rows = rows.filter(seller=request.user)
    client_options = _assigned_clients(request.user) if not request.user.is_staff else []
    products = list(SalesLine.objects.filter(dataset=active_sales(), client__in=client_options)
                    .values_list("product", flat=True).distinct()) if client_options else []
    return TemplateResponse(request, "operations/corrections.html", _base(
        request, corrections=rows[:100], clients=client_options, products=products,
        batch=batch if request.user.is_staff else None,
    ))


@admin_required
@require_POST
def order_odoo(request, order_id):
    order = get_object_or_404(OrderRequest, pk=order_id)
    action = request.POST.get('action')
    if action not in {'sync', 'refresh', 'authorize', 'cancel'}:
        return HttpResponseBadRequest('Acción inválida.')
    if action in {'authorize', 'cancel'} and request.POST.get('confirm_action') != '1':
        messages.error(request, 'Confirme la operación antes de continuar.')
        return redirect('order_detail', order_id=order.pk)
    if action == 'authorize' and (not _sources_current(order) or order.status == OrderRequest.Status.CANCELLED):
        raise PermissionDenied('Revise la disponibilidad antes de enviar la producción.')
    try:
        previous_snapshot = OdooOrderLink.objects.filter(order=order).values_list(
            "snapshot", flat=True
        ).first()
        odoo_service.synchronize(order, action)
        if action == 'cancel':
            order.status = OrderRequest.Status.CANCELLED
            order.save(update_fields=['status', 'updated_at'])
            order.lines.update(status=OrderLine.Status.REJECTED, review_note='Cancelación administrativa en Odoo')
        AccessEvent.objects.create(actor=request.user, action='odoo_' + action, target=str(order.pk))
        current_link = OdooOrderLink.objects.filter(order=order).values(
            "snapshot", "updated_at"
        ).first()
        current_snapshot = current_link["snapshot"] if current_link else None
        if action == "refresh" and current_snapshot is not None and current_snapshot != previous_snapshot:
            digest = sha256(json.dumps(current_snapshot, sort_keys=True, default=str).encode()).hexdigest()
            transition = current_link["updated_at"].isoformat()
            _notification(f"order:{order.pk}:progress:{transition}:{digest}",
                          "Avance de producción o entrega actualizado", request, order)
        messages.success(request, 'Operación registrada.')
    except odoo_service.OdooError as exc:
        messages.error(request, str(exc) if settings.SMARTORDER_MAINTENANCE_UI else 'La operación está pendiente. El pedido se conserva; vuelva a intentarlo.')
    return redirect('order_detail', order_id=order.pk)


@admin_required
@require_http_methods(['GET', 'POST'])
def odoo_settings(request):
    status = 'Sin comprobar'
    try:
        config = odoo_service.configuration()
    except odoo_service.OdooError as exc:
        config = {'enabled': False}
        status = str(exc)
    if request.method == 'POST':
        action = request.POST.get('action')
        try:
            if action == 'check':
                odoo_service.rpc('ping')
                status = 'Conexión comprobada'
                messages.success(request, status)
            elif action in {'product', 'customer'}:
                values = {name: request.POST.get(name, '').strip() for name in
                          (('product', 'sku', 'unit', 'source_unit') if action == 'product' else ('client', 'code'))}
                limits = {'product': 255, 'sku': 128, 'unit': 128, 'source_unit': 50, 'client': 255, 'code': 128}
                if any((not value and name != 'source_unit') or len(value) > limits[name] or '\x00' in value for name, value in values.items()):
                    raise ValueError('Complete los códigos y la unidad explícita de origen.')
                if action == 'product':
                    if not values['unit'].startswith('uom.'):
                        raise ValueError('Use el identificador Odoo de unidad; por ejemplo uom.product_uom_kgm.')
                    product = values.pop('product')
                    OdooProductMapping.objects.update_or_create(product=product, defaults=values)
                else:
                    client = values.pop('client')
                    location = request.POST.get('location_id', '').strip()
                    if location:
                        if not location.isdecimal() or not 0 < int(location) <= 2147483647:
                            raise ValueError('La ubicación debe ser un identificador positivo de Odoo.')
                        values['location_id'] = int(location)
                    OdooCustomerMapping.objects.update_or_create(client=client, defaults=values)
                AccessEvent.objects.create(actor=request.user, action='odoo_mapping', target=action)
                messages.success(request, 'Equivalencia guardada. Reintente las ventas pendientes cuando corresponda.')
                return redirect('odoo_settings')
            else:
                return HttpResponseBadRequest('Acción inválida.')
        except (ValueError, IntegrityError) as exc:
            messages.error(request, str(exc) if isinstance(exc, ValueError) else 'El código ya corresponde a otro registro.')
    public = {name: config.get(name) for name in ('enabled', 'url', 'database')}
    public.update(key_configured=bool(config.get('key')), connection_status=status,
        products=list(OdooProductMapping.objects.order_by('product').values('product', 'sku', 'unit', 'source_unit')),
        customers=list(OdooCustomerMapping.objects.order_by('client').values('client', 'code', 'location_id')),
        product_options=list(SalesLine.objects.filter(dataset=active_sales()).values_list('product', flat=True).distinct()),
        client_options=list(SalesLine.objects.filter(dataset=active_sales()).values_list('client', flat=True).distinct()))
    return TemplateResponse(request, 'operations/odoo_settings.html', _base(request, **public))


@admin_required
def operational_reports(request):
    try:
        start = date.fromisoformat(request.GET['from_date']) if request.GET.get('from_date') else None
        end = date.fromisoformat(request.GET['to_date']) if request.GET.get('to_date') else None
        if start and end and start > end:
            raise ValueError
    except ValueError:
        messages.error(request, 'Seleccione un período válido.')
        start = end = None
    return TemplateResponse(request, 'operations/operational_reports.html', _base(request, **odoo_service.reports(start, end)))


@admin_required
@require_http_methods(["GET", "POST"])
def tariffs(request):
    dataset = active_sales()
    clients = list(dataset.lines.values_list("client", flat=True).distinct().order_by("client")) if dataset else []
    products = list(dataset.lines.values_list("product", flat=True).distinct().order_by("product")) if dataset else []
    if request.method == "POST":
        try:
            client, product = request.POST.get("client"), request.POST.get("product")
            if client not in clients or product not in products:
                raise ValueError("Seleccione un cliente y producto del catálogo.")
            unit = request.POST.get("unit", "").strip()
            if unit not in {"kg", "unid"}:
                raise ValueError("Seleccione kg o unid; otras conversiones requieren revisión.")
            price = nonnegative_decimal(request.POST.get("unit_price"), "Precio", positive=True, places=4)
            discount = nonnegative_decimal(request.POST.get("discount_percent", "0"), "Descuento")
            if discount >= 100 or discount != discount.quantize(Decimal("0.01")):
                raise ValueError("Descuento menor de 100 %, con hasta dos decimales.")
            with transaction.atomic():
                rate, _ = PriceTariff.objects.update_or_create(client=client, product=product,
                    defaults={"unit": unit, "unit_price": price, "discount_percent": discount, "updated_by": request.user})
                AccessEvent.objects.create(actor=request.user, action="tariff_set", target=str(rate.pk),
                    details={"client": client, "product": product, "price": str(price), "discount": str(discount), "unit": unit})
            messages.success(request, "Tarifa guardada. Los pedidos existentes conservan su precio.")
            return redirect("tariffs")
        except (ValueError, IntegrityError) as exc:
            messages.error(request, str(exc))
    return TemplateResponse(request, "operations/tariffs.html", _base(request,
        clients=clients, products=products, tariffs=list(PriceTariff.objects.order_by("client", "product")),
        exceptions=list(OrderRequest.objects.filter(sale_confirmed=False).exclude(exception_reason="").exclude(status="cancelled"))))


@admin_required
@require_POST
def commercial_review(request, order_id):
    reviewed = False
    with transaction.atomic():
        order = get_object_or_404(OrderRequest.objects.select_for_update(), pk=order_id)
        note = request.POST.get("review_note", "").strip()
        decision = request.POST.get("decision")
        if order.sale_confirmed or not order.exception_reason or order.status == "cancelled":
            messages.error(request, "La excepción ya fue resuelta o no está pendiente.")
        elif not note or len(note) > 2000 or decision not in {"approve", "reject"}:
            messages.error(request, "Indique una decisión y su motivo, hasta 2,000 caracteres.")
        else:
            order.sale_confirmed = decision == "approve"
            order.sale_confirmed_at = timezone.now() if order.sale_confirmed else None
            if decision == "reject":
                order.status = "cancelled"
                order.lines.update(status=OrderLine.Status.REJECTED)
            order.commercial_review_note, order.commercial_reviewed_by = note, request.user
            order.commercial_reviewed_at = timezone.now()
            order.save(update_fields=["sale_confirmed", "sale_confirmed_at", "status",
                                      "commercial_review_note", "commercial_reviewed_by",
                                      "commercial_reviewed_at", "updated_at"])
            AccessEvent.objects.create(actor=request.user, action="commercial_" + decision, target=str(order.pk), details={"note": note})
            messages.success(request, "Excepción revisada. La producción conserva su autorización independiente.")
            reviewed = True
    if reviewed:
        _notification(f"order:{order.pk}:commercial:{decision}",
                      "Resultado de excepción comercial", request, order)
    if order.sale_confirmed:
        odoo_service.try_synchronize(order)
    return redirect("order_detail", order_id=order.pk)


@admin_required
@require_POST
def order_commitment(request, order_id):
    order = get_object_or_404(OrderRequest, pk=order_id)
    try:
        if not order.sale_confirmed or order.status == "cancelled":
            raise ValueError("Primero confirme una venta vigente.")
        day = _required_date(request.POST.get("committed_date", ""))
        previous_day = order.committed_date
        with transaction.atomic():
            order.committed_date = day
            order.save(update_fields=["committed_date", "updated_at"])
            change_event = None
            if previous_day != day:
                change_event = AccessEvent.objects.create(
                    actor=request.user, action="delivery_commitment", target=str(order.pk),
                    details={"previous_date": previous_day.isoformat() if previous_day else None,
                             "date": day.isoformat()},
                )
        messages.success(request, "Fecha comprometida registrada por administración.")
        if previous_day != day:
            _notification(f"order:{order.pk}:commitment-change:{change_event.pk}",
                          "Compromiso de entrega actualizado", request, order)
    except ValueError as exc:
        messages.error(request, str(exc))
    return redirect("order_detail", order_id=order.pk)


@admin_required
def pilot_dashboard(request):
    today = timezone.localdate()
    try:
        start = date.fromisoformat(request.GET.get("from_date", today.replace(day=1).isoformat()))
        end = date.fromisoformat(request.GET.get("to_date", today.isoformat()))
        if start > end:
            raise ValueError
    except ValueError:
        messages.error(request, "Seleccione un período válido.")
        start, end = today.replace(day=1), today
    return TemplateResponse(request, "operations/pilot_dashboard.html", _base(request, **commercial.pilot_dashboard(start, end)))


@admin_required
@require_http_methods(["GET", "POST"])
def source_center(request):
    if request.method == "POST":
        try:
            action = request.POST.get("action", "sync")
            if action == "sync":
                from .source_sync import synchronize_sources
                run = synchronize_sources(request.user)
                if run.status == "success":
                    messages.success(request, "Inventario actualizado. Los pedidos con error permanecen pendientes de reintento.")
                else:
                    messages.error(request, run.error)
            elif action in {"review_coverage", "create_coverage"}:
                dataset = get_object_or_404(SalesDataset, pk=request.POST.get("dataset_id"))
                client = request.POST.get("client", "").strip()
                product = request.POST.get("product", "").strip()
                clients = set(dataset.lines.values_list("client", flat=True).distinct()) | set(
                    Assignment.objects.values_list("client", flat=True)
                )
                products = set(dataset.lines.values_list("product", flat=True).distinct()) | set(
                    OdooProductMapping.objects.values_list("product", flat=True)
                )
                if client not in clients or product not in products:
                    raise ValueError("El cliente y el producto deben pertenecer al catálogo administrativo.")
                coverage = save_sales_coverage(
                    dataset, request.user, client=client, product=product,
                    unit=request.POST.get("unit", ""),
                    start_date=request.POST.get("start_date", ""),
                    end_date=request.POST.get("end_date", ""),
                    status=request.POST.get("status", ""),
                    coverage_id=(int(request.POST["coverage_id"])
                                 if request.POST.get("coverage_id") else None),
                )
                AccessEvent.objects.create(actor=request.user, action="sales_coverage_review",
                    target=str(coverage.pk), details={"dataset": dataset.pk, "status": coverage.status,
                                                       "unit": coverage.unit})
                messages.success(request, "Cobertura y unidad registradas para este intervalo.")
            elif action == "review_operational_sale":
                dataset = get_object_or_404(SalesDataset, pk=request.POST.get("dataset_id"))
                result = review_operational_sale(
                    dataset, request.user,
                    order_line_id=int(request.POST.get("order_line_id", "")),
                    decision=request.POST.get("decision", ""),
                    imported_line_id=(int(request.POST["imported_line_id"])
                                      if request.POST.get("imported_line_id") else None),
                )
                AccessEvent.objects.create(actor=request.user, action="sales_overlap_reconciled",
                    target=str(result.pk), details={"dataset": dataset.pk,
                                                    "order_line": result.order_line_id,
                                                    "decision": result.decision,
                                                    "imported_line": result.imported_line_id})
                messages.success(request, "La línea operativa quedó conciliada con la fuente histórica.")
            else:
                return HttpResponseBadRequest("Acción de fuente inválida.")
        except (ValueError, TypeError, IntegrityError) as exc:
            messages.error(request, str(exc))
        return redirect("source_center")
    dataset = active_sales()
    coverage_rows = dataset.coverage.select_related("reviewed_by").order_by(
        "client", "product", "unit", "start_date"
    )[:500] if dataset else []
    coverage_clients = sorted(set(dataset.lines.values_list("client", flat=True).distinct()) |
                              set(Assignment.objects.values_list("client", flat=True))) if dataset else []
    coverage_products = sorted(set(dataset.lines.values_list("product", flat=True).distinct()) |
                               set(OdooProductMapping.objects.values_list("product", flat=True))) if dataset else []
    overlap_rows = operational_overlap_rows(dataset) if dataset else []
    return TemplateResponse(request, "operations/source_center.html", _base(request,
        today=timezone.localdate(), coverage_end_limit=timezone.localdate() - timedelta(days=1),
        health=commercial.source_health(), runs=list(SourceSync.objects.order_by("-pk")[:30]),
        batch=active_inventory(), dataset=dataset, customers=list(OdooCustomerMapping.objects.order_by("client")),
        coverage_rows=coverage_rows, coverage_clients=coverage_clients,
        coverage_products=coverage_products,
        overlap_rows=overlap_rows, overlap_pending=bool(overlap_rows)))


@admin_required
def sales_source_download(request, dataset_id: int):
    dataset = get_object_or_404(SalesDataset, pk=dataset_id)
    try:
        return FileResponse(dataset.file.open("rb"), as_attachment=True, filename=dataset.filename)
    except (OSError, ValueError) as exc:
        raise Http404("El archivo original no está disponible.") from exc
