"""Explicit, non-destructive demo-only commercial data for the example XLSX.

Never mark an uncertain source verified, infer units of historical records,
overwrite existing tariffs, or connect to a real ERP.
"""
from __future__ import annotations

from collections import defaultdict
from decimal import Decimal, ROUND_HALF_UP
from statistics import median

from django.db import transaction
from django.utils import timezone

from .models import AccessEvent, ClientStockCount, PriceTariff, RestockTarget, SalesDataset, SalesLine


LAB_FILE = "Demo_Ventas_Avicola_2025_IA_Pedidos_v2.xlsx"
LAB_LOCATION = "LAB - conteo simulado (no operativo)"
# Presentation assignments apply only to invented *tariffs* and demo counts.
# They are NOT evidence of the unit in historical sales.
LAB_PRESENTATIONS = {
    "Alitas": "kg",
    "Chorizo de Pollo": "lb",
    "Huevos Docena": "unid",
    "Jamon de Pavo": "lb",
    "Muslo y Encuentro": "kg",
    "Nuggets de Pollo": "kg",
    "Pechuga de Pollo": "kg",
    "Pollo Entero Fresco": "kg",
}


def demo_allowed(dataset: SalesDataset | None) -> bool:
    return bool(
        dataset and dataset.filename == LAB_FILE
        and dataset.provenance != SalesDataset.Provenance.REAL
        and dataset.active
    )


def demo_pairs(dataset: SalesDataset) -> list[tuple[str, str, str, Decimal]]:
    """Use real product labels in the fictitious XLSX, but deliberately synthetic tariffs."""
    if not demo_allowed(dataset):
        raise ValueError("Solo se permiten datos de laboratorio con el Excel de demostración conocido.")
    prices = defaultdict(list)
    for client, product, raw in SalesLine.objects.filter(dataset=dataset).values_list(
        "client", "product", "unit_price"
    ):
        if product not in LAB_PRESENTATIONS:
            raise ValueError(
                f"Producto fuera del laboratorio reconocido: {product}. "
                "No se asignarán unidades inventadas.")
        value = Decimal(raw)
        if value > 0:
            prices[(client, product)].append(value)
    result = []
    for (client, product), values in sorted(prices.items()):
        mid = Decimal(str(median(values))).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        # Tariffs are explicitly synthetic, not a verified commercial price.
        result.append((client, product, LAB_PRESENTATIONS[product], max(Decimal("0.01"), mid)))
    if not result:
        raise ValueError("No hay registros válidos en el histórico de demostración.")
    return result


def seed_demo_commercial(dataset: SalesDataset, actor) -> dict[str, int]:
    if not actor.is_staff or not actor.is_active:
        raise ValueError("Solo un administrador activo puede preparar este laboratorio.")
    rows = demo_pairs(dataset)
    count = {"tariffs": 0, "targets": 0, "counts": 0, "pairs": len(rows)}
    with transaction.atomic():
        for client, product, unit, price in rows:
            _, created = PriceTariff.objects.get_or_create(
                client=client, product=product,
                defaults={
                    "unit": unit, "unit_price": price,
                    "pricing_currency": "USD", "quoted_price": price,
                    "discount_percent": Decimal("0.00"), "updated_by": actor,
                },
            )
            count["tariffs"] += int(created)
            _, created = RestockTarget.objects.get_or_create(
                scope=RestockTarget.Scope.CLIENT,
                owner_key=f"client:{client}", product=product,
                defaults={
                    "unit": unit, "target_quantity": Decimal("20.000"),
                    "updated_by": actor,
                },
            )
            count["targets"] += int(created)
            # One illustrative stock count per pair for this LAB location.
            # A second execution must not create today's fake stock again.
            if not ClientStockCount.objects.filter(
                client=client, product=product, location=LAB_LOCATION
            ).exists():
                ClientStockCount.objects.create(
                    client=client, product=product, location=LAB_LOCATION,
                    unit=unit, available=Decimal("6.000"),
                    observed_on=timezone.localdate(), reported_by=actor,
                )
                count["counts"] += 1
        if any(count[key] for key in ("tariffs", "targets", "counts")):
            AccessEvent.objects.create(
                actor=actor, action="lab_commercial_seed", target=str(dataset.pk),
                details={"source_sha256": dataset.digest, **count,
                         "mode": "synthetic_only_no_source_verification",
                         "location": LAB_LOCATION},
            )
    return count
