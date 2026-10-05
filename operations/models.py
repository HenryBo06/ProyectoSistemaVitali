"""Persisted sales sources, stock and the reviewed production workflow."""

from django.conf import settings
from django.db import models
from django.db.models import F, Q
import uuid


class SalesDataset(models.Model):
    digest = models.CharField(max_length=64, unique=True)
    filename = models.CharField(max_length=255)
    file = models.FileField(upload_to="sales/")
    first_date = models.DateField()
    last_date = models.DateField()
    row_count = models.PositiveIntegerField()
    verified = models.BooleanField(default=False)
    full_snapshot_confirmed = models.BooleanField(default=False)
    active = models.BooleanField(default=False)
    notes = models.TextField(blank=True)
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="sales_uploads"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=Q(last_date__gte=F("first_date")),
                name="sales_dates_in_order",
            ),
            models.UniqueConstraint(
                fields=["active"], condition=Q(active=True), name="one_active_sales_dataset"
            ),
        ]

    def __str__(self) -> str:
        return self.filename


class SalesLine(models.Model):
    dataset = models.ForeignKey(SalesDataset, on_delete=models.CASCADE, related_name="lines")
    date = models.DateField()
    client = models.CharField(max_length=255)
    product = models.CharField(max_length=255)
    category = models.CharField(max_length=255, blank=True)
    zone = models.CharField(max_length=255, blank=True)
    distribution_channel = models.CharField(max_length=255, blank=True)
    sales_channel = models.CharField(max_length=255, blank=True)
    quantity = models.DecimalField(max_digits=18, decimal_places=3)
    unit_price = models.DecimalField(max_digits=18, decimal_places=4)
    amount = models.DecimalField(max_digits=20, decimal_places=2)

    class Meta:
        indexes = [
            models.Index(fields=["dataset", "date"]),
            models.Index(fields=["dataset", "client", "product", "date"]),
        ]
        constraints = [
            models.CheckConstraint(condition=Q(quantity__gte=0), name="sales_quantity_nonnegative"),
            models.CheckConstraint(condition=Q(unit_price__gte=0), name="sales_price_nonnegative"),
            models.CheckConstraint(condition=Q(amount__gte=0), name="sales_amount_nonnegative"),
        ]


class Assignment(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="assigned_clients"
    )
    # One active seller owns each client in the current client-product scope.
    client = models.CharField(max_length=255, unique=True)

    def __str__(self) -> str:
        return f"{self.client} → {self.user.username}"


class InventoryBatch(models.Model):
    digest = models.CharField(max_length=64, unique=True)
    filename = models.CharField(max_length=255)
    file = models.FileField(upload_to="inventory/")
    unit_match_confirmed = models.BooleanField(default=False)
    active = models.BooleanField(default=False)
    notes = models.TextField(blank=True)
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="inventory_uploads"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["active"], condition=Q(active=True), name="one_active_inventory_batch"
            )
        ]

    def __str__(self) -> str:
        return self.filename


class InventoryItem(models.Model):
    batch = models.ForeignKey(InventoryBatch, on_delete=models.CASCADE, related_name="items")
    client = models.CharField(max_length=255)
    product = models.CharField(max_length=255)
    unit = models.CharField(max_length=50, blank=True)
    available = models.DecimalField(max_digits=18, decimal_places=3)
    observed_on = models.DateField()

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["batch", "client", "product"], name="inventory_pair_once"),
            models.CheckConstraint(condition=Q(available__gte=0), name="inventory_available_nonnegative"),
        ]
        indexes = [models.Index(fields=["batch", "client", "product"])]


class DatedFlow(models.Model):
    class Kind(models.TextChoices):
        INCOMING = "incoming", "Entrada prevista"
        CUSTOMER_COMMITMENT = "customer_commitment", "Compromiso con cliente"

    # Null batch permits an administrative entry between complete inventory uploads.
    batch = models.ForeignKey(
        InventoryBatch, on_delete=models.PROTECT, null=True, blank=True, related_name="dated_flows"
    )
    kind = models.CharField(max_length=32, choices=Kind.choices)
    client = models.CharField(max_length=255)
    product = models.CharField(max_length=255)
    unit = models.CharField(max_length=50, blank=True)
    quantity = models.DecimalField(max_digits=18, decimal_places=3)
    due_date = models.DateField()
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="dated_flows"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [models.Index(fields=["client", "product", "due_date", "kind"])]
        constraints = [
            models.CheckConstraint(condition=Q(quantity__gte=0), name="dated_flow_quantity_nonnegative")
        ]


class OrderRequest(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Pendiente de producción"
        REVIEWED = "reviewed", "Producción revisada"
        EXPORTED = "exported", "Exportado"
        SUPERSEDED = "superseded", "Fuente sustituida"
        CANCELLED = "cancelled", "Cancelado"

    seller = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="order_requests"
    )
    client = models.CharField(max_length=255)
    dataset = models.ForeignKey(SalesDataset, on_delete=models.PROTECT, related_name="orders")
    revalidated_against = models.ForeignKey(
        SalesDataset, on_delete=models.PROTECT, null=True, blank=True,
        related_name="revalidated_orders",
    )
    revalidated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True,
        related_name="revalidated_orders",
    )
    revalidated_at = models.DateTimeField(null=True, blank=True)
    inventory_batch = models.ForeignKey(
        InventoryBatch, on_delete=models.PROTECT, null=True, blank=True,
        related_name="orders_at_creation",
    )
    inventory_revalidated_against = models.ForeignKey(
        InventoryBatch, on_delete=models.PROTECT, null=True, blank=True,
        related_name="revalidated_orders",
    )
    required_date = models.DateField()
    note = models.TextField(blank=True)
    sale_confirmed = models.BooleanField(default=False)
    payment_terms = models.CharField(max_length=255, blank=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [models.Index(fields=["client", "required_date", "status"])]

    def __str__(self) -> str:
        return f"Pedido {self.pk} · {self.client}"


class OrderLine(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Pendiente de producción"
        APPROVED = "approved", "Producción autorizada"
        REJECTED = "rejected", "Rechazado"
        EXPORTED = "exported", "Exportado"

    order = models.ForeignKey(OrderRequest, on_delete=models.PROTECT, related_name="lines")
    product = models.CharField(max_length=255)
    unit = models.CharField(max_length=50, blank=True)
    reference_qty = models.DecimalField(max_digits=18, decimal_places=3, null=True, blank=True)
    method = models.CharField(max_length=100, blank=True)
    suggested_qty = models.DecimalField(max_digits=18, decimal_places=3, null=True, blank=True)
    requested_qty = models.DecimalField(max_digits=18, decimal_places=3)
    reason = models.TextField(blank=True)
    unit_price = models.DecimalField(max_digits=18, decimal_places=4, default=0)
    discount_percent = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    future_quantity = models.DecimalField(max_digits=18, decimal_places=3, null=True, blank=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING)
    approved_qty = models.DecimalField(max_digits=18, decimal_places=3, null=True, blank=True)
    reviewer = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True,
        related_name="reviewed_order_lines",
    )
    review_note = models.TextField(blank=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)
    exported_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["order", "product"], name="order_product_once"),
            models.CheckConstraint(condition=Q(unit_price__gte=0), name="sale_price_nonnegative"),
            models.CheckConstraint(condition=Q(discount_percent__gte=0) & Q(discount_percent__lte=100), name="sale_discount_range"),
            models.CheckConstraint(condition=Q(future_quantity__isnull=True) | Q(future_quantity__gte=0), name="future_quantity_nonnegative"),
            models.CheckConstraint(condition=Q(requested_qty__gte=0), name="requested_qty_nonnegative"),
            models.CheckConstraint(
                condition=Q(approved_qty__isnull=True) | Q(approved_qty__gte=0),
                name="approved_qty_nonnegative",
            ),
            models.CheckConstraint(
                condition=Q(suggested_qty__isnull=True) | Q(suggested_qty__gte=0),
                name="suggested_qty_nonnegative",
            ),
        ]
        indexes = [models.Index(fields=["status", "order"])]


class OrderCoverage(models.Model):
    line = models.ForeignKey(OrderLine, on_delete=models.CASCADE, related_name="coverages")
    client = models.CharField(max_length=255)
    product = models.CharField(max_length=255)
    day = models.DateField()

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["client", "product", "day"], name="one_order_per_product_day"
            )
        ]


class StockCorrectionRequest(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Pendiente"
        APPROVED = "approved", "Aprobada"
        REJECTED = "rejected", "Rechazada"

    seller = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="stock_corrections"
    )
    client = models.CharField(max_length=255)
    product = models.CharField(max_length=255)
    unit = models.CharField(max_length=50, blank=True)
    proposed_available = models.DecimalField(max_digits=18, decimal_places=3)
    observed_on = models.DateField()
    reason = models.TextField()
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING)
    reviewer = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True,
        related_name="reviewed_stock_corrections",
    )
    review_note = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=Q(proposed_available__gte=0), name="stock_correction_nonnegative"
            )
        ]


class InventoryAdjustment(models.Model):
    """Approved stock correction; imported inventory rows stay unchanged."""

    batch = models.ForeignKey(
        InventoryBatch, on_delete=models.PROTECT, related_name="adjustments"
    )
    correction = models.OneToOneField(
        StockCorrectionRequest, on_delete=models.PROTECT, related_name="adjustment"
    )
    client = models.CharField(max_length=255)
    product = models.CharField(max_length=255)
    available = models.DecimalField(max_digits=18, decimal_places=3)
    observed_on = models.DateField()
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="inventory_adjustments"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [models.Index(fields=["batch", "client", "product", "-created_at"])]
        constraints = [
            models.CheckConstraint(
                condition=Q(available__gte=0), name="inventory_adjustment_nonnegative"
            )
        ]


class NotificationLog(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Pendiente"
        SENT = "sent", "Enviado"
        FAILED = "failed", "Error"

    event_key = models.CharField(max_length=160, unique=True)
    channel = models.CharField(max_length=32, default="telegram")
    recipient = models.CharField(max_length=255)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING)
    order = models.ForeignKey(
        OrderRequest, on_delete=models.PROTECT, null=True, blank=True, related_name="notifications"
    )
    error = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    sent_at = models.DateTimeField(null=True, blank=True)


class AccessEvent(models.Model):
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="access_events")
    action = models.CharField(max_length=64)
    target = models.CharField(max_length=255)
    details = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)


class OdooProductMapping(models.Model):
    product = models.CharField(max_length=255, unique=True)
    sku = models.CharField(max_length=128, unique=True)
    unit = models.CharField(max_length=128)
    source_unit = models.CharField(max_length=50)


class OdooCustomerMapping(models.Model):
    client = models.CharField(max_length=255, unique=True)
    code = models.CharField(max_length=128, unique=True)


class OdooOrderLink(models.Model):
    order = models.OneToOneField(OrderRequest, on_delete=models.PROTECT, related_name='odoo_link')
    reference = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    revision = models.PositiveIntegerField(default=1)
    status = models.CharField(max_length=16, default='pending')
    snapshot = models.JSONField(default=dict)
    last_error = models.CharField(max_length=500, blank=True)
    last_synced = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)
