"""Commercial rules and source activation shared by both interfaces."""
from copy import deepcopy
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from . import commercial
from .models import (Assignment, InventoryBatch, OdooCustomerMapping, OdooOrderLink,
    OdooProductMapping, OrderRequest, PriceTariff, SourceSync)
from .services import import_inventory, import_sales, recommendation_cards
from .source_sync import synchronize_sources
from .streamlit_bridge import call_screen, screen_context, sign_in
from .tests import LocalCase, inventory_book, sales_book


@override_settings(SMARTORDER_ODOO_CONFIG=None, SMARTORDER_MAINTENANCE_UI=False)
class CommercialPilotTests(LocalCase, TestCase):
    def setUp(self):
        super().setUp()
        today = timezone.localdate()
        self.dataset = import_sales(sales_book(today - timedelta(days=30), 30), "simulated.xlsx", self.admin, False, True)
        import_inventory(inventory_book(today, available=0), "stock.xlsx", self.admin, True)
        Assignment.objects.create(user=self.seller, client="Cliente A")
        self.client.force_login(self.seller)
        self.token = self.client.get(reverse("order_create")).context["submission_token"]
        self.payload = {"client": "Cliente A", "required_date": today.isoformat(),
            "product": ["Alitas"], "quantity": ["5"], "unit": ["kg"], "reason": ["Cliente solicita"],
            "unit_price": ["12.5"], "discount_percent": ["10"], "submission_token": self.token}

    def submit(self, **values):
        return self.client.post(reverse("order_create"), {**self.payload, **values})

    def test_commercial_pages_do_not_expose_maintenance_or_file_provenance(self):
        self.submit()
        order = OrderRequest.objects.get()
        self.client.force_login(self.admin)
        for route, args in (("pilot_dashboard", []), ("order_detail", [order.pk]), ("orders", []), ("recommendations", []), ("operational_reports", []), ("dashboard", [])):
            response = self.client.get(reverse(route, args=args))
            self.assertEqual(response.status_code, 200)
            for forbidden in ("simulated.xlsx", "stock.xlsx", "Fuentes de datos", "Sincronizar venta", "Conexión y catálogos", "Cargar ventas", "Fuente y cobertura", "Descargar XLSX", "Ventas usadas:"):
                self.assertNotContains(response, forbidden)

    def test_admin_login_opens_decisions_before_historical_reports(self):
        self.client.logout()
        response = self.client.post(reverse("login"), {"username": self.admin.username, "password": "ClaveAdministrativa123", "next": "/"})
        self.assertRedirects(response, reverse("pilot_dashboard"))

    def test_unchanged_inventory_read_does_not_require_another_business_review(self):
        from .models import InventoryItem, DatedFlow
        from .views import _sources_current
        self.submit()
        order = OrderRequest.objects.get()
        old = order.inventory_batch
        old.source = "odoo"
        old.active = False
        old.save()
        current = InventoryBatch.objects.create(source="odoo", observed_at=timezone.now(), digest="new-cut", filename="cut", active=True, uploaded_by=self.admin)
        for row in old.items.all():
            InventoryItem.objects.create(batch=current, client=row.client, product=row.product, unit=row.unit, available=row.available, observed_on=row.observed_on)
        for row in old.dated_flows.all():
            DatedFlow.objects.create(batch=current, client=row.client, product=row.product, unit=row.unit, quantity=row.quantity, due_date=row.due_date, kind=row.kind, created_by=self.admin)
        self.assertTrue(_sources_current(order))
        current.items.filter(client=order.client).update(available=3)
        self.assertFalse(_sources_current(order))

    def test_price_and_discount_tampering_and_missing_tokens_are_rejected(self):
        for values in ({"unit_price": ["1"]}, {"discount_percent": ["99"]}, {"submission_token": ""}, {"unit": ["unid"]}):
            self.assertEqual(self.submit(**values).status_code, 200)
        self.assertFalse(OrderRequest.objects.exists())

    def test_retries_reuse_order_but_changed_content_is_rejected(self):
        self.assertEqual(self.submit().status_code, 302)
        self.assertEqual(self.submit().status_code, 302)
        self.assertEqual(OrderRequest.objects.count(), 1)
        self.assertEqual(self.submit(quantity=["6"]).status_code, 200)
        self.assertEqual(OrderRequest.objects.count(), 1)
        self.assertTrue(OrderRequest.objects.get().sale_confirmed)
        self.assertEqual(OrderRequest.objects.get().lines.get().future_quantity, 5)
        self.assertIsNone(OrderRequest.objects.get().lines.get().approved_qty)

    def test_exception_requires_admin_and_does_not_sync_or_authorize(self):
        with patch("operations.odoo.try_synchronize") as synchronize:
            self.assertEqual(self.submit(unit_price=["7"], exception_reason="Promoción especial").status_code, 302)
            synchronize.assert_not_called()
        order = OrderRequest.objects.get()
        self.assertFalse(order.sale_confirmed)
        self.assertEqual(self.client.post(reverse("commercial_review", args=[order.pk]), {"decision": "approve", "review_note": "Sí"}).status_code, 403)
        self.client.force_login(self.admin)
        self.assertEqual(self.client.post(reverse("order_review", args=[order.pk]), {"line_id": order.lines.get().pk, "decision": "approve"}).status_code, 403)
        self.client.post(reverse("commercial_review", args=[order.pk]), {"decision": "approve", "review_note": "Promoción aprobada"})
        order.refresh_from_db()
        self.assertTrue(order.sale_confirmed)
        self.assertIsNone(order.lines.get().approved_qty)
        self.assertEqual(order.lines.get().unit_price, 7)
        self.assertEqual(PriceTariff.objects.get(client="Cliente A", product="Alitas").unit_price, Decimal("12.5"))

    def test_missing_tariff_never_defaults_to_free_sale(self):
        PriceTariff.objects.all().delete()
        self.assertEqual(self.submit().status_code, 200)
        self.assertFalse(OrderRequest.objects.exists())

    def test_bound_form_preserves_values_after_validation_failure(self):
        result = self.submit(reason=[""], note="Conservar nota", payment_terms="Contado")
        self.assertEqual(result.status_code, 200)
        self.assertContains(result, 'value="5"')
        self.assertContains(result, "Conservar nota")
        self.assertContains(result, "Contado")
        self.assertEqual(result.context["submission_token"], self.token)

    def test_seller_cannot_access_admin_endpoints_through_bridge(self):
        from django.core.exceptions import PermissionDenied
        key = sign_in(self.seller.username, "ClaveDelVendedor123")
        for name in ("tariffs", "source_center", "pilot_dashboard"):
            self.assertEqual(self.client.get(reverse(name)).status_code, 403)
            with self.assertRaises(PermissionDenied):
                screen_context(name, key)
        context = screen_context("order_create", key)
        self.assertEqual(context["products"][0]["unit_price"], Decimal("12.5"))
        self.assertNotIn("health", context)
        self.assertIsNone(context["dataset"])

    def test_seller_cannot_edit_prices_after_confirmation(self):
        self.submit()
        order = OrderRequest.objects.get()
        line = order.lines.get()
        self.client.post(reverse("sale_change", args=[order.pk]), {"action": "edit", "line_id": line.pk,
            "quantity": "6", "unit_price": "1", "discount_percent": "10", "change_reason": "Intento"})
        line.refresh_from_db()
        self.assertEqual(line.unit_price, Decimal("12.5"))
        self.assertEqual(line.requested_qty, 5)

    def test_dashboard_reconciles_returns_and_hides_unknown_quantities(self):
        self.submit()
        order = OrderRequest.objects.get()
        line = order.lines.get()
        today = timezone.localdate()
        order.committed_date = today - timedelta(days=1)
        order.save()
        link = OdooOrderLink.objects.create(order=order, status="synced", last_synced=timezone.now(),
            snapshot={"revision": 1, "lines": [{"key": str(line.pk), "delivered": 5, "returned": 1, "produced": 3,
                "last_delivery_at": (timezone.now() - timedelta(days=2)).isoformat()}]})
        result = commercial.pilot_dashboard(today, today)
        self.assertEqual(result["confirmed_revenue"], Decimal("56.25"))
        self.assertEqual(result["unit_totals"][0]["outstanding"], 1)
        self.assertEqual(result["unit_totals"][0]["fulfillment"], 80)
        self.assertEqual(result["punctuality"], 0)
        link.last_synced = timezone.now() - timedelta(minutes=16)
        link.save()
        result = commercial.pilot_dashboard(today, today)
        self.assertIsNone(result["unit_totals"][0]["outstanding"])
        self.assertIsNone(result["punctuality"])

    def source(self):
        OdooCustomerMapping.objects.create(client="Cliente A", code="LAB-A", location_id=10)
        OdooProductMapping.objects.create(product="Alitas", sku="LAB-1", source_unit="kg", unit="uom.product_uom_kgm")
        return {"company_id": 1, "observed_at": timezone.now().isoformat(),
            "customers": [{"code": "LAB-A"}], "products": [{"sku": "LAB-1", "unit": "uom.product_uom_kgm"}],
            "locations": [{"id": 10}], "stocks": [{"location_id": 10, "sku": "LAB-1", "quantity": 8, "reserved": 2, "available": 6}],
            "movements": [{"id": 90, "location_id": 10, "sku": "LAB-1", "quantity": 2, "kind": "incoming", "due_at": timezone.now().isoformat()}]}

    def test_source_sync_is_idempotent_and_retains_rejected_readings(self):
        raw = self.source()
        before = InventoryBatch.objects.count()
        with patch("operations.odoo.rpc", return_value=raw):
            first = synchronize_sources(self.admin, refresh_orders=False)
            second = synchronize_sources(self.admin, refresh_orders=False)
        self.assertEqual((first.status, second.status), ("success", "success"))
        self.assertEqual(InventoryBatch.objects.count(), before + 1)
        batch = InventoryBatch.objects.get(active=True)
        self.assertEqual(batch.items.get().available, 6)
        self.assertEqual(batch.dated_flows.get().quantity, 2)
        bad = deepcopy(raw)
        bad["stocks"][0]["available"] = 8
        with patch("operations.odoo.rpc", return_value=bad):
            failed = synchronize_sources(self.admin, refresh_orders=False)
        self.assertEqual(failed.status, "error")
        self.assertEqual(failed.rejected, 1)
        self.assertEqual(InventoryBatch.objects.get(active=True).pk, batch.pk)
        self.assertTrue(batch.file.storage.exists(batch.file.name))

    def test_source_rejects_units_duplicates_and_stale_cuts(self):
        raw = self.source()
        for change in (lambda value: value["products"][0].update(unit="uom.product_uom_unit"),
                       lambda value: value["stocks"].append(value["stocks"][0]),
                       lambda value: value.update(observed_at=(timezone.now() - timedelta(minutes=16)).isoformat())):
            bad = deepcopy(raw)
            change(bad)
            with patch("operations.odoo.rpc", return_value=bad):
                self.assertEqual(synchronize_sources(self.admin, refresh_orders=False).status, "error")

    def test_bridge_uses_identical_confirmation_contract(self):
        key = sign_in(self.seller.username, "ClaveDelVendedor123")
        response, notices, key = call_screen("order_create", key, data=self.payload)
        self.assertEqual(response.status_code, 302)
        self.assertTrue(OrderRequest.objects.get().sale_confirmed)
        self.assertTrue(any(level == "success" for level, _ in notices))
