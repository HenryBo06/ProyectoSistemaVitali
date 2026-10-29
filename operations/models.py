"""Persisted sales sources, stock and the reviewed production workflow."""

from django.conf import settings
from django.db import models
from django.db.models import F, Q
import uuid


class SalesDataset(models.Model):
    class Provenance(models.TextChoices):
        UNKNOWN = "unknown", "Por confirmar"
        REAL = "real", "Real"
        SIMULATED = "simulated", "Simulada"

    class DateSemantics(models.TextChoices):
        SALE_ORDER = "sale_order", "Fecha de venta o pedido"
        DELIVERY = "delivery", "Fecha de entrega realizada"

    digest = models.CharField(max_length=64, unique=True)
    filename = models.CharField(max_length=255)
    file = models.FileField(upload_to="sales/")
    provenance = models.CharField(max_length=16, choices=Provenance.choices, default=Provenance.UNKNOWN)
    date_semantics = models.CharField(max_length=16, choices=DateSemantics.choices, default=DateSemantics.SALE_ORDER)
    operational_overlap_reviewed = models.BooleanField(default=False)
    operational_overlap_reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True,
        related_name="reviewed_sales_overlaps",
    )
    operational_overlap_reviewed_at = models.DateTimeField(null=True, blank=True)
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
    class Provenance(models.TextChoices):
        UNKNOWN = "unknown", "Por confirmar"
        REAL = "real", "Real"
        SIMULATED = "simulated", "Simulada"
        OPERATIONAL = "operational", "Operativa"

    dataset = models.ForeignKey(SalesDataset, on_delete=models.CASCADE, related_name="lines")
    date = models.DateField()
    requested_delivery_date = models.DateField(null=True, blank=True)
    committed_delivery_date = models.DateField(null=True, blank=True)
    provenance = models.CharField(max_length=16, choices=Provenance.choices, default=Provenance.UNKNOWN)
    unit = models.CharField(max_length=50, blank=True)
    source_order_ref = models.CharField(max_length=160, blank=True)
    source_line_ref = models.CharField(max_length=160, blank=True)
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
    source = models.CharField(max_length=16, default="excel")
    observed_at = models.DateTimeField(null=True, blank=True)
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

    class DateSemantics(models.TextChoices):
        REQUESTED = "requested", "Entrega solicitada"
        COMMITTED = "committed", "Entrega comprometida"
        EXPECTED_INCOMING = "expected_incoming", "Recepción prevista"

    class Reconciliation(models.TextChoices):
        PENDING = "pending", "Pendiente de conciliar"
        UNIQUE = "unique", "Compromiso independiente"
        DUPLICATE = "duplicate", "Representa un compromiso existente"
        AMBIGUOUS = "ambiguous", "Coincidencia ambigua"

    # Null batch permits an administrative entry between complete inventory uploads.
    batch = models.ForeignKey(
        InventoryBatch, on_delete=models.PROTECT, null=True, blank=True, related_name="dated_flows"
    )
    kind = models.CharField(max_length=32, choices=Kind.choices)
    date_semantics = models.CharField(max_length=24, choices=DateSemantics.choices, default=DateSemantics.REQUESTED)
    source_reference = models.CharField(max_length=160, blank=True)
    reconciliation = models.CharField(max_length=16, choices=Reconciliation.choices, default=Reconciliation.PENDING)
    related_order_line = models.ForeignKey(
        "OrderLine", on_delete=models.PROTECT, null=True, blank=True,
        related_name="representing_flows",
    )
    client = models.CharField(max_length=255)
    product = models.CharField(max_length=255)
    unit = models.CharField(max_length=50, blank=True)
    quantity = models.DecimalField(max_digits=18, decimal_places=3)
    due_date = models.DateField()
    confirmed = models.BooleanField(default=False)
    confirmed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True,
        related_name="confirmed_dated_flows",
    )
    confirmed_at = models.DateTimeField(null=True, blank=True)
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
    submission_id = models.UUIDField(null=True, blank=True, unique=True, editable=False)
    submission_hash = models.CharField(max_length=64, blank=True)
    exception_reason = models.TextField(blank=True)
    commercial_review_note = models.TextField(blank=True)
    commercial_reviewed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
        null=True, blank=True, related_name="commercial_reviews")
    commercial_reviewed_at = models.DateTimeField(null=True, blank=True)
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
    committed_date = models.DateField(null=True, blank=True)
    sale_confirmed_at = models.DateTimeField(null=True, blank=True)
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
    suggestion_mode = models.CharField(max_length=24, default="sale")
    suggestion_reason = models.TextField(blank=True)
    suggestion_event = models.CharField(max_length=255, blank=True)
    suggestion_as_of = models.DateField(null=True, blank=True)
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


class UserNotification(models.Model):
    class Kind(models.TextChoices):
        OPERATIONAL = "operational", "Novedad operativa"
        EVENT = "event", "Oportunidad de temporada"

    recipient = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="smartorder_notifications"
    )
    event_key = models.CharField(max_length=64, unique=True)
    kind = models.CharField(max_length=16, choices=Kind.choices, default=Kind.OPERATIONAL)
    title = models.CharField(max_length=160)
    body = models.TextField()
    target_url = models.CharField(max_length=500, blank=True)
    client = models.CharField(max_length=255, blank=True)
    order = models.ForeignKey(
        OrderRequest, on_delete=models.PROTECT, null=True, blank=True, related_name="inbox_notifications"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    read_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at", "-pk"]
        indexes = [models.Index(fields=["recipient", "read_at", "-created_at"])]


class CommercialEvent(models.Model):
    class Kind(models.TextChoices):
        HOLIDAY = "holiday", "Asueto"
        COMMERCIAL = "commercial", "Temporada comercial"
        LOCAL = "local", "Celebración local"

    key = models.SlugField(max_length=100)
    title = models.CharField(max_length=160)
    kind = models.CharField(max_length=16, choices=Kind.choices)
    start_date = models.DateField()
    end_date = models.DateField()
    territory = models.CharField(max_length=160, blank=True, default="Nacional")
    source = models.URLField(max_length=500, blank=True)
    active = models.BooleanField(default=False)
    reviewed = models.BooleanField(default=False)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="commercial_events_created"
    )
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True,
        related_name="commercial_events_reviewed",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["start_date", "title"]
        constraints = [
            models.CheckConstraint(condition=Q(end_date__gte=F("start_date")), name="commercial_event_dates_in_order"),
            models.UniqueConstraint(fields=["key", "start_date", "territory"], name="commercial_event_once_per_territory"),
        ]


class ClientStockCount(models.Model):
    client = models.CharField(max_length=255)
    product = models.CharField(max_length=255)
    location = models.CharField(max_length=160)
    unit = models.CharField(max_length=50)
    available = models.DecimalField(max_digits=18, decimal_places=3)
    observed_on = models.DateField()
    reported_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="client_stock_counts"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-observed_on", "-created_at"]
        indexes = [models.Index(fields=["client", "product", "-observed_on"])]
        constraints = [models.CheckConstraint(condition=Q(available__gte=0), name="client_stock_nonnegative")]


class ClientStockMovement(models.Model):
    class Kind(models.TextChoices):
        INCOMING = "incoming", "Entrada al cliente"
        OUTGOING = "outgoing", "Salida del cliente"

    client = models.CharField(max_length=255)
    product = models.CharField(max_length=255)
    location = models.CharField(max_length=160)
    kind = models.CharField(max_length=16, choices=Kind.choices)
    unit = models.CharField(max_length=50)
    quantity = models.DecimalField(max_digits=18, decimal_places=3)
    effective_date = models.DateField()
    reference = models.CharField(max_length=200, blank=True)
    confirmed = models.BooleanField(default=False)
    reconciliation = models.CharField(
        max_length=16, choices=DatedFlow.Reconciliation.choices,
        default=DatedFlow.Reconciliation.PENDING,
    )
    related_order_line = models.ForeignKey(
        OrderLine, on_delete=models.PROTECT, null=True, blank=True,
        related_name="representing_stock_movements",
    )
    reported_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="client_stock_movements"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["effective_date", "client", "product", "location"]
        indexes = [models.Index(fields=["client", "product", "effective_date", "confirmed"])]
        constraints = [models.CheckConstraint(condition=Q(quantity__gt=0), name="client_stock_movement_positive")]


class RestockTarget(models.Model):
    class Scope(models.TextChoices):
        CLIENT = "client", "Cliente"
        LOCATION = "location", "Ubicación de Vitali"

    scope = models.CharField(max_length=16, choices=Scope.choices)
    # Store `client:<name>` or `location:<id>` so both scopes have a non-null unique key.
    owner_key = models.CharField(max_length=255)
    product = models.CharField(max_length=255)
    unit = models.CharField(max_length=50)
    target_quantity = models.DecimalField(max_digits=18, decimal_places=3)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="restock_targets"
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["scope", "owner_key", "product"]
        constraints = [
            models.UniqueConstraint(fields=["scope", "owner_key", "product"], name="restock_target_once"),
            models.CheckConstraint(condition=Q(target_quantity__gte=0), name="restock_target_nonnegative"),
        ]


class SalesCoverage(models.Model):
    class Status(models.TextChoices):
        UNKNOWN = "unknown", "Cobertura desconocida"
        COMPLETE = "complete", "Cobertura completa"
        NO_SERIES = "no_series", "Serie todavía inexistente"

    dataset = models.ForeignKey(SalesDataset, on_delete=models.CASCADE, related_name="coverage")
    client = models.CharField(max_length=255)
    product = models.CharField(max_length=255)
    unit = models.CharField(max_length=50, blank=True)
    start_date = models.DateField()
    end_date = models.DateField()
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.UNKNOWN)
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True,
        related_name="sales_coverage_reviews",
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["client", "product", "unit", "start_date"]
        constraints = [
            models.CheckConstraint(condition=Q(end_date__gte=F("start_date")), name="sales_coverage_dates_in_order"),
            models.UniqueConstraint(
                fields=["dataset", "client", "product", "unit", "start_date", "end_date"],
                name="sales_coverage_interval_once",
            ),
        ]


class SalesOperationalReconciliation(models.Model):
    class Decision(models.TextChoices):
        DUPLICATE = "duplicate", "Representado en el archivo"
        INDEPENDENT = "independent", "Venta operativa independiente"

    dataset = models.ForeignKey(
        SalesDataset, on_delete=models.CASCADE, related_name="operational_reconciliations",
    )
    order_line = models.ForeignKey(
        "OrderLine", on_delete=models.PROTECT, related_name="sales_reconciliations",
    )
    imported_line = models.ForeignKey(
        SalesLine, on_delete=models.PROTECT, null=True, blank=True,
        related_name="operational_reconciliations",
    )
    decision = models.CharField(max_length=16, choices=Decision.choices)
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
        related_name="sales_operational_reconciliations",
    )
    reviewed_at = models.DateTimeField()

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["dataset", "order_line"], name="sales_reconciliation_once_per_order_line"),
            models.UniqueConstraint(fields=["dataset", "imported_line"], condition=Q(imported_line__isnull=False),
                                    name="sales_reconciliation_imported_line_once"),
        ]


class LocationStockSnapshot(models.Model):
    """Administrative physical count, separate from customer allocations."""

    location_id = models.PositiveIntegerField()
    location_name = models.CharField(max_length=160)
    product = models.CharField(max_length=255)
    unit = models.CharField(max_length=50)
    available = models.DecimalField(max_digits=18, decimal_places=3)
    observed_on = models.DateField()
    reported_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="location_stock_counts"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["location_id", "product", "-observed_on", "-created_at"]
        indexes = [models.Index(fields=["location_id", "product", "-observed_on"])]
        constraints = [models.CheckConstraint(condition=Q(available__gte=0), name="location_stock_nonnegative")]


class LocationIncoming(models.Model):
    """Confirmed expected receipts for planning; recording one does not authorize production."""

    location_id = models.PositiveIntegerField()
    location_name = models.CharField(max_length=160)
    product = models.CharField(max_length=255)
    unit = models.CharField(max_length=50)
    quantity = models.DecimalField(max_digits=18, decimal_places=3)
    due_date = models.DateField()
    reference = models.CharField(max_length=160, blank=True)
    confirmed = models.BooleanField(default=False)
    recorded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="location_incoming"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["due_date", "location_id", "product"]
        indexes = [models.Index(fields=["location_id", "product", "due_date", "confirmed"])]
        constraints = [models.CheckConstraint(condition=Q(quantity__gte=0), name="location_incoming_nonnegative")]


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
    location_id = models.PositiveIntegerField(null=True, blank=True)
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


class PriceTariff(models.Model):
    client = models.CharField(max_length=255)
    product = models.CharField(max_length=255)
    unit = models.CharField(max_length=50)
    unit_price = models.DecimalField(max_digits=18, decimal_places=4)
    discount_percent = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["client", "product"], name="one_client_product_tariff"),
            models.CheckConstraint(condition=Q(unit_price__gt=0), name="tariff_price_positive"),
            models.CheckConstraint(condition=Q(discount_percent__gte=0) & Q(discount_percent__lt=100), name="tariff_discount_range"),
        ]


class SourceSync(models.Model):
    started_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=16, default="running")
    fingerprint = models.CharField(max_length=64, blank=True)
    rows = models.PositiveIntegerField(default=0)
    rejected = models.PositiveIntegerField(default=0)
    details = models.JSONField(default=dict)
    error = models.CharField(max_length=500, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["status"], condition=Q(status="running"), name="one_running_source_sync")]
