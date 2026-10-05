"""Role-aware local screens and the sales-to-production workflow."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
import json
import os
import secrets

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
from django.db.models import Count, Sum
from django.db.models.functions import TruncMonth
from django.http import HttpResponse, HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect
from django.template.response import TemplateResponse
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_http_methods, require_POST

from smartorder.inventory import inventory_template
from smartorder.notifications import notify_internal
from smartorder.orders import export_production
from .models import (
    AccessEvent, Assignment, DatedFlow, InventoryAdjustment, InventoryBatch, InventoryItem,
    NotificationLog, OrderCoverage, OrderLine, OrderRequest, SalesDataset, SalesLine,
    StockCorrectionRequest,
    OdooProductMapping, OdooCustomerMapping, OdooOrderLink,
)
from .services import (
    active_inventory, active_sales, default_delivery_date, import_inventory, import_sales,
    nonnegative_decimal, recommendation_cards, top_products_for,
)

User = get_user_model()
from . import odoo as odoo_service


def _role(user) -> str:
    return "admin" if user.is_staff else "vendedor"


def _base(request, **context):
    return {"role": _role(request.user), "nav": request.resolver_match.url_name if request.resolver_match else "", **context}


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
    return (
        sales_id is not None
        and (order.dataset_id == sales_id or order.revalidated_against_id == sales_id)
        and (order.inventory_batch_id == inventory_id
             or order.inventory_revalidated_against_id == inventory_id)
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
    fields = ("product", "category", "unit", "unit_confirmed", "suggested",
              "available", "stock_date", "inventory_warning")
    return [{**{key: card[key] for key in fields},
             "state": "suggested" if card["suggested"] is not None else "manual",
             "explanation": "Cantidad orientativa disponible." if card["suggested"] is not None
             else "La fecha de entrega está fuera del rango de sugerencias; indique cantidad y motivo."
             if card.get("delivery_outside_forecast")
             else "Sin sugerencia válida; indique cantidad y motivo."}
            for card in sorted(cards, key=lambda card: card["product"].casefold())]


def _seller_order(order):
    return SimpleNamespace(pk=order.pk, id=order.pk, client=order.client,
        seller=SimpleNamespace(username=order.seller.username), required_date=order.required_date,
        note=order.note, created_at=order.created_at, status=order.status,
        sale_confirmed=order.sale_confirmed, payment_terms=order.payment_terms,
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


def _notification(event_key: str, title: str, request, order: OrderRequest | None = None):
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


class SmartLoginView(LoginView):
    template_name = "operations/login.html"
    redirect_authenticated_user = True

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
        selected = request.GET.get("client", "")
        if selected and selected not in clients:
            raise PermissionDenied("El cliente no pertenece a su cartera.")
        return TemplateResponse(request, "operations/seller_dashboard.html", _base(
            request, clients=clients,
            order_count=OrderRequest.objects.filter(client__in=clients).count(),
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
            if verified and "confirmed_origin" not in request.POST:
                raise ValueError("Para verificar la fuente, confirme su origen.")
            if verified and not full_snapshot:
                raise ValueError("Confirme que el Excel representa el histórico completo y reemplaza la carga anterior.")
            dataset = import_sales(raw, filename, request.user, verified, full_snapshot)
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
    cards = recommendation_cards(dataset, delivery, client, today=today) if dataset and client else []
    return TemplateResponse(request, "operations/recommendations.html", _base(
        request, clients=clients, selected_client=client, client=client,
        required_date=delivery, horizon_end=delivery + timedelta(days=6),
        min_date=today, max_date=today + timedelta(days=settings.SMARTORDER_DELIVERY_DAYS),
        recommendations=cards if request.user.is_staff else _seller_cards(cards),
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
        messages.error(request, "Administración debe cargar ventas antes de solicitar producción.")
        return redirect("dashboard")
    clients = _assigned_clients(request.user)
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
    try:
        delivery = _required_date(
            request.POST.get("required_date", "") if request.method == "POST"
            else request.GET.get("required_date", default_delivery.isoformat())
        )
    except ValueError as exc:
        messages.error(request, str(exc))
        delivery = default_delivery
        if request.method == "POST":
            return redirect("order_create")
    cards = recommendation_cards(dataset, delivery, client, today=today)
    if request.method == "POST":
        try:
            products = request.POST.getlist("product")
            quantities = request.POST.getlist("quantity")
            units = request.POST.getlist("unit")
            reasons = request.POST.getlist("reason")
            prices = request.POST.getlist("unit_price") or ["0"] * len(products)
            discounts = request.POST.getlist("discount_percent") or ["0"] * len(products)
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
                if card and card["unit_confirmed"] and unit != card["unit"]:
                    raise ValueError(f"{product}: la unidad debe coincidir con el inventario confirmado.")
                if card is None and not reason:
                    raise ValueError(f"{product}: explique por qué solicita un producto nuevo.")
                if card is not None and (
                    card["suggested"] is None or
                    abs(quantity - card["suggested"]) > Decimal("0.001")
                ) and not reason:
                    raise ValueError(f"{product}: explique la cantidad manual o el ajuste.")
                if len(reason) > 2000:
                    raise ValueError("El motivo debe tener 2,000 caracteres o menos.")
                price = nonnegative_decimal(price or "0", "Precio unitario")
                discount = nonnegative_decimal(discount or "0", "Descuento")
                if discount > 100 or discount != discount.quantize(Decimal("0.01")):
                    raise ValueError("El descuento debe estar entre 0 y 100 %.")
                chosen.append((product, unit, quantity, reason, card, price, discount))
            if not chosen:
                raise ValueError("Indique al menos un producto con cantidad positiva.")
            with transaction.atomic():
                # Lock the client owner while confirming the sale; distinct sales
                # may share a demand week, unlike the legacy replenishment requests.
                owner = Assignment.objects.select_for_update().filter(client=client, user=request.user).first()
                if owner is None:
                    raise PermissionDenied("El cliente ya no pertenece a su cartera.")
                order = OrderRequest.objects.create(
                    seller=request.user, client=client, dataset=dataset,
                    inventory_batch=active_inventory(), required_date=delivery,
                    note=request.POST.get("note", "").strip()[:2000],
                    sale_confirmed=True,
                    payment_terms=request.POST.get("payment_terms", "").strip()[:255],
                )
                for product, unit, quantity, reason, card, price, discount in chosen:
                    available = (card or {}).get("available")
                    # ponytail: future quantity is a snapshot, not a stock reservation;
                    # Odoo will own reservations and fulfillment in the integration phase.
                    future = None
                    if available is not None and card["stock_date"] == date.today() and card["unit_confirmed"]:
                        future = max(Decimal("0"), quantity - available)
                    line = OrderLine.objects.create(
                        order=order, product=product, unit=unit,
                        reference_qty=(card or {}).get("reference_qty"),
                        method=(card or {}).get("method", "Producto nuevo, pedido manual"),
                        suggested_qty=(card or {}).get("suggested"),
                        requested_qty=quantity, reason=reason,
                        unit_price=price, discount_percent=discount, future_quantity=future,
                    )
                AccessEvent.objects.create(actor=request.user, action="sale_confirm", target=str(order.pk), details={"client": client, "lines": len(chosen)})
            _notification(f"order:{order.pk}:submitted", "Venta confirmada; producción pendiente", request, order)
            odoo_service.try_synchronize(order)
            messages.success(request, f"Venta {order.pk} confirmada. La autorización de producción es independiente.")
            return redirect("order_detail", order_id=order.pk)
        except (ValueError, IntegrityError) as exc:
            messages.error(request, str(exc))
    return TemplateResponse(request, "operations/order_form.html", _base(
        request, clients=clients, selected_client=client, client=client,
        required_date=delivery, horizon_end=delivery + timedelta(days=6),
        window_end=delivery + timedelta(days=6),
        min_date=today, max_date=today + timedelta(days=settings.SMARTORDER_DELIVERY_DAYS),
        source_status="",
        recommendations=_seller_cards(cards), products=_seller_cards(cards), dataset=None,
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
                    messages.error(request, str(exc))
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
                price = nonnegative_decimal(request.POST.get("unit_price"), "Precio")
                discount = nonnegative_decimal(request.POST.get("discount_percent"), "Descuento")
                if discount > 100 or discount != discount.quantize(Decimal("0.01")):
                    raise ValueError("Descuento entre 0 y 100 %, con hasta dos decimales.")
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
                messages.error(request, str(exc))
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
    messages.success(request, "Fuentes actuales reconocidas para este pedido. Revise cada cantidad antes de exportar.")
    return redirect("order_detail", order_id=order.pk)


@admin_required
@require_POST
def order_review(request, order_id: int):
    with transaction.atomic():
        order = get_object_or_404(OrderRequest.objects.select_for_update(), pk=order_id)
        if not _sources_current(order):
            raise PermissionDenied("Ventas o inventario cambiaron; administración debe revalidar las fuentes antes de revisar.")
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
        raise PermissionDenied('Revalide las fuentes antes de enviar la producción.')
    try:
        odoo_service.synchronize(order, action)
        if action == 'cancel':
            order.status = OrderRequest.Status.CANCELLED
            order.save(update_fields=['status', 'updated_at'])
            order.lines.update(status=OrderLine.Status.REJECTED, review_note='Cancelación administrativa en Odoo')
        AccessEvent.objects.create(actor=request.user, action='odoo_' + action, target=str(order.pk))
        messages.success(request, 'Operación comprobada en Odoo.')
    except odoo_service.OdooError as exc:
        messages.error(request, str(exc))
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
        customers=list(OdooCustomerMapping.objects.order_by('client').values('client', 'code')),
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
