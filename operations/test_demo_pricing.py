"""Regression tests for explicit LAB seeding and USD/GTQ tariff quotations."""
from datetime import timedelta
from decimal import Decimal, ROUND_HALF_UP

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from . import commercial
from .demo_catalog import LAB_FILE, LAB_LOCATION
from .models import AccessEvent, ClientStockCount, PriceTariff, RestockTarget, SalesDataset
from .services import import_sales
from .tests import LocalCase, sales_book


@override_settings(SMARTORDER_ODOO_CONFIG=None)
class DemoPricingTests(LocalCase, TestCase):
    def setUp(self):
        super().setUp()
        self.dataset = import_sales(
            sales_book(timezone.localdate() - timedelta(days=40), 40,
                       products=("Alitas", "Chorizo de Pollo")),
            LAB_FILE, self.admin, verified=False,
            provenance=SalesDataset.Provenance.UNKNOWN,
        )

    def test_usd_two_decimal_tariff_rejects_four_decimal_input(self):
        self.client.force_login(self.admin)
        response = self.client.post(reverse("tariffs"), {
            "client": "Cliente A", "product": "Alitas", "unit": "kg",
            "unit_price": "0.0006", "price_currency": "USD", "discount_percent": "0",
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "hasta 2 decimales")
        self.assertEqual(PriceTariff.objects.get(client="Cliente A", product="Alitas").unit_price,
                         Decimal("12.5000"))

    def test_gtq_quote_preserves_reference_and_converts_to_usd_accounting(self):
        self.client.force_login(self.admin)
        day = timezone.localdate().isoformat()
        response = self.client.post(reverse("tariffs"), {
            "client": "Cliente A", "product": "Chorizo de Pollo", "unit": "lb",
            "unit_price": "76.41", "price_currency": "GTQ",
            "gtq_per_usd": "7.640820", "fx_date": day, "discount_percent": "1.50",
        }, follow=True)
        self.assertEqual(response.status_code, 200)
        rate = PriceTariff.objects.get(client="Cliente A", product="Chorizo de Pollo")
        usd = (Decimal("76.41") / Decimal("7.640820")).quantize(
            Decimal("0.0001"), rounding=ROUND_HALF_UP)
        self.assertEqual(rate.pricing_currency, "GTQ")
        self.assertEqual(rate.quoted_price, Decimal("76.41"))
        self.assertEqual(rate.unit_price, usd)
        self.assertEqual(rate.fx_rate, Decimal("7.640820"))
        self.assertEqual(rate.fx_date, timezone.localdate())
        self.assertContains(response, "Q 76.41")
        self.assertContains(response, f"$ {usd:.2f}")
        self.assertEqual(rate.unit, "lb")

    def test_gtq_without_explicit_rate_is_rejected(self):
        self.client.force_login(self.admin)
        response = self.client.post(reverse("tariffs"), {
            "client": "Cliente A", "product": "Chorizo de Pollo", "unit": "lb",
            "unit_price": "76.41", "price_currency": "GTQ",
            "discount_percent": "0",
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "tipo de cambio")
        self.assertFalse(PriceTariff.objects.filter(product="Chorizo de Pollo").exists())

    def test_gtq_future_reference_date_is_rejected(self):
        with self.assertRaisesMessage(ValueError, "no puede ser futura"):
            commercial.normalize_quoted_tariff(
                "45.00", "GTQ", "7.640820",
                (timezone.localdate() + timedelta(days=1)).isoformat(),
            )

    def test_lab_seeding_keeps_manual_tariffs_and_is_idempotent(self):
        self.client.force_login(self.admin)
        first = self.client.post(reverse("tariffs"), {
            "action": "seed_lab_commercial", "confirm_lab": "on",
        }, follow=True)
        self.assertEqual(first.status_code, 200)
        self.assertContains(first, "Datos simulados preparados")
        self.assertEqual(PriceTariff.objects.count(), 3)
        self.assertEqual(RestockTarget.objects.count(), 2)
        self.assertEqual(ClientStockCount.objects.filter(location=LAB_LOCATION).count(), 2)
        self.assertEqual(
            PriceTariff.objects.get(client="Cliente A", product="Alitas").unit_price,
            Decimal("12.5000"),
        )
        created = PriceTariff.objects.get(client="Cliente A", product="Chorizo de Pollo")
        self.assertEqual(created.pricing_currency, "USD")
        self.assertEqual(created.unit_price, created.quoted_price)
        self.assertEqual(created.unit, "lb")
        self.assertEqual(created.unit_price.as_tuple().exponent, -4)
        self.dataset.refresh_from_db()
        self.assertFalse(self.dataset.verified)
        self.assertFalse(self.dataset.full_snapshot_confirmed)
        self.assertEqual(
            AccessEvent.objects.filter(action="lab_commercial_seed",
                                       target=str(self.dataset.pk)).count(), 1,
        )
        again = self.client.post(reverse("tariffs"), {
            "action": "seed_lab_commercial", "confirm_lab": "on",
        }, follow=True)
        self.assertEqual(again.status_code, 200)
        self.assertEqual(PriceTariff.objects.count(), 3)
        self.assertEqual(RestockTarget.objects.count(), 2)
        self.assertEqual(ClientStockCount.objects.filter(location=LAB_LOCATION).count(), 2)
        self.assertEqual(AccessEvent.objects.filter(action="lab_commercial_seed").count(), 1)

    def test_requires_explicit_synthetic_confirmation(self):
        self.client.force_login(self.admin)
        response = self.client.post(reverse("tariffs"), {
            "action": "seed_lab_commercial",
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Confirma que esta instancia")
        self.assertEqual(RestockTarget.objects.count(), 0)

    def test_seller_cannot_seed_commercial_demo(self):
        self.client.force_login(self.seller)
        response = self.client.post(reverse("tariffs"), {
            "action": "seed_lab_commercial", "confirm_lab": "on",
        })
        self.assertEqual(response.status_code, 403)
        self.assertEqual(RestockTarget.objects.count(), 0)

    def test_only_known_demo_dataset_can_be_seeded(self):
        self.client.force_login(self.admin)
        self.dataset.filename = "ventas_reales.xlsx"
        self.dataset.save(update_fields=["filename"])
        response = self.client.post(reverse("tariffs"), {
            "action": "seed_lab_commercial", "confirm_lab": "on",
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Solo se permiten datos de laboratorio")
        self.assertEqual(RestockTarget.objects.count(), 0)

    def test_quotations_shown_in_seller_cards_without_changing_usd_order_price(self):
        from .models import Assignment
        Assignment.objects.create(user=self.seller, client="Cliente A")
        self.client.force_login(self.admin)
        self.client.post(reverse("tariffs"), {
            "client": "Cliente A", "product": "Alitas", "unit": "kg",
            "unit_price": "76.41", "price_currency": "GTQ",
            "gtq_per_usd": "7.640820",
            "fx_date": timezone.localdate().isoformat(),
            "discount_percent": "0",
        })
        self.client.force_login(self.seller)
        response = self.client.get(reverse("recommendations"), {
            "client": "Cliente A", "mode": "sale",
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Q 76.41")
        card = next(x for x in response.context_data["recommendations"]
                    if x["product"] == "Alitas")
        self.assertEqual(card["tariff_currency"], "GTQ")
        self.assertEqual(card["tariff_quoted_price"], Decimal("76.41"))
        self.assertNotIn("sales_usd_display", card)
