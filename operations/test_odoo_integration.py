"""Shared Odoo contract: no network, real credentials or persistent laboratory changes."""
from datetime import date, timedelta
from decimal import Decimal
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from . import odoo
from .models import AccessEvent, Assignment, OdooCustomerMapping, OdooOrderLink, OdooProductMapping, OrderLine, OrderRequest
from .services import import_inventory, import_sales
from .tests import LocalCase, inventory_book, sales_book


@override_settings(SMARTORDER_ODOO_CONFIG=None)
class OdooIntegrationTests(LocalCase, TestCase):
    def setUp(self):
        super().setUp()
        self.dataset = import_sales(sales_book(date.today() - timedelta(days=30), 30),
                                    "private-sales.xlsx", self.admin, False, True)
        self.inventory = import_inventory(inventory_book(date.today()), "stock.xlsx", self.admin, True)
        Assignment.objects.create(user=self.seller, client="Cliente A")
        self.order = OrderRequest.objects.create(seller=self.seller, client="Cliente A",
            dataset=self.dataset, inventory_batch=self.inventory,
            required_date=date.today() + timedelta(days=3), sale_confirmed=True, payment_terms="30 días")
        self.line = OrderLine.objects.create(order=self.order, product="Alitas", unit="kg",
            requested_qty=5, unit_price=Decimal("12.50"), discount_percent=10)
        OdooProductMapping.objects.create(product="Alitas", sku="LAB-ALITAS",
            source_unit="kg", unit="uom.product_uom_kgm")
        OdooCustomerMapping.objects.create(client="Cliente A", code="LAB-CLIENT-A")
        self.link = OdooOrderLink.objects.create(order=self.order)
        self.client.force_login(self.admin)

    def snapshot(self, **line_values):
        return {"reference": "smartorder:" + str(self.link.reference),
            "revision": self.link.revision,
            "commercial": odoo.sale_payload(self.order, self.link),
            "sale": {"name": "LAB-SALE", "id": 90, "state": "sale"}, "currency": "USD", "invoices": [],
            "lines": [{"key": str(self.line.pk), "ordered": 5, "reserved": 0,
                "produced": 0, "delivered": 0, "returned": 0, "net_delivered": 0,
                "scrapped": 0, "unit_cost": 2, "authorized": False, **line_values}]}

    def enabled(self, url="http://127.0.0.1:8079"):
        path = Path(self.tmp.name) / "odoo-private-test.json"
        path.write_text(json.dumps({"enabled": True, "url": url, "database": "unit_tests_only",
                                    "key": "TEST-KEY-NO-REAL-CREDENTIAL"}), encoding="utf-8")
        return override_settings(SMARTORDER_ODOO_CONFIG=str(path))

    def test_payload_requires_explicit_customer_product_and_exact_source_unit(self):
        payload = odoo.sale_payload(self.order, self.link)
        self.assertEqual(payload["customer"], {"code": "LAB-CLIENT-A", "name": "Cliente A"})
        self.assertEqual(payload["delivery_date"], self.order.required_date.isoformat())
        self.assertEqual(payload["terms"], "30 días")
        self.assertEqual(payload["lines"][0], {"key": str(self.line.pk), "sku": "LAB-ALITAS",
            "quantity": 5, "unit_price": 12.5, "discount": 10, "unit": "uom.product_uom_kgm"})
        with patch("operations.odoo.rpc") as remote:
            self.line.unit = "unid"
            self.line.save(update_fields=["unit"])
            with self.assertRaises(odoo.OdooError):
                odoo.synchronize(self.order)
            remote.assert_not_called()
        self.line.unit = "kg"
        self.line.save(update_fields=["unit"])
        OdooCustomerMapping.objects.all().delete()
        with self.assertRaises(odoo.OdooError):
            odoo.sale_payload(self.order, self.link)

    def test_seller_sees_operational_fields_without_finance_cost_codes_or_errors(self):
        result = self.snapshot(reserved=5, unit_cost=77, sku="PRIVATE-SKU")
        result["invoices"] = [{"name": "PRIVATE-INVOICE", "residual": 1000, "state": "posted",
                               "type": "out_invoice", "payment_state": "partial"},
                              {"name": "PRIVATE-DRAFT", "residual": 500, "state": "draft"},
                              {"name": "PRIVATE-REFUND", "residual": 0, "state": "posted",
                               "type": "out_refund", "payment_state": "paid"}]
        odoo.accept_snapshot(self.link, result)
        self.link.last_error = "PRIVATE-CONNECTION-DETAIL"
        self.link.save(update_fields=["last_error"])
        public = odoo.order_context(self.order)
        self.assertEqual(public["lines"][0]["stage"], "Para entregar")
        self.assertFalse(public["can_authorize"])
        serialized = json.dumps(public, default=str)
        for private in ("unit_cost", "admin_finance", "PRIVATE-INVOICE", "PRIVATE-SKU",
                        "PRIVATE-CONNECTION-DETAIL", "commercial", str(self.link.reference), "TEST-KEY-NO-REAL-CREDENTIAL"):
            self.assertNotIn(private, serialized)
        self.client.force_login(self.seller)
        response = self.client.get(reverse("order_detail", args=[self.order.pk]))
        self.assertEqual(response.status_code, 200)
        for private in ("PRIVATE-INVOICE", "PRIVATE-SKU", "PRIVATE-CONNECTION-DETAIL"):
            self.assertNotContains(response, private)
        admin = odoo.order_context(self.order, admin=True)
        self.assertEqual(admin["admin_finance"]["balance"], Decimal("1000"))
        invoice = admin["admin_finance"]["invoices"][0]
        self.assertEqual((invoice["state_label"], invoice["type_label"], invoice["payment_state_label"]),
                         ("Contabilizada", "Factura", "Cobro parcial"))
        self.assertEqual(admin["admin_finance"]["invoices"][2]["payment_state_label"], "Conciliada")

    def test_all_integration_mutations_settings_and_reports_reject_seller(self):
        self.client.force_login(self.seller)
        with patch("operations.odoo.rpc") as remote:
            for action in ("sync", "refresh", "authorize", "cancel"):
                response = self.client.post(reverse("order_odoo", args=[self.order.pk]),
                    {"action": action, "confirm_action": "1"})
                self.assertEqual(response.status_code, 403)
            for route in ("odoo_settings", "operational_reports"):
                self.assertEqual(self.client.get(reverse(route)).status_code, 403)
            self.assertEqual(self.client.post(reverse("odoo_settings"),
                {"action": "product", "product": "Alitas", "sku": "OTHER"}).status_code, 403)
            remote.assert_not_called()

    def test_production_authorization_rejects_stale_sources_before_remote_call(self):
        self.line.status, self.line.approved_qty = OrderLine.Status.APPROVED, Decimal("5")
        self.line.save(update_fields=["status", "approved_qty"])
        import_inventory(inventory_book(date.today(), available=99), "new-stock.xlsx", self.admin, True)
        with patch("operations.odoo.rpc") as remote:
            response = self.client.post(reverse("order_odoo", args=[self.order.pk]),
                {"action": "authorize", "confirm_action": "1"})
            self.assertEqual(response.status_code, 403)
            remote.assert_not_called()

    def test_offline_keeps_confirmed_sale_and_production_pending(self):
        with self.enabled(), patch("operations.odoo.rpc", side_effect=odoo.OdooError("TEST OFFLINE")):
            odoo.try_synchronize(self.order)
        self.order.refresh_from_db()
        self.line.refresh_from_db()
        self.link.refresh_from_db()
        self.assertTrue(self.order.sale_confirmed)
        self.assertEqual(self.order.status, OrderRequest.Status.PENDING)
        self.assertEqual(self.line.status, OrderLine.Status.PENDING)
        self.assertIsNone(self.line.approved_qty)
        self.assertEqual(self.link.status, "error")
        self.assertFalse(odoo.order_context(self.order)["synced"])
        self.assertEqual(odoo.order_context(self.order)["status"], "Pendiente de actualizar")

    def test_retry_reuses_reference_and_revision_without_another_local_link(self):
        with patch("operations.odoo.rpc", side_effect=[odoo.OdooError("LOST RESPONSE"),
                    self.snapshot(), self.snapshot()]) as remote:
            with self.assertRaises(odoo.OdooError):
                odoo.synchronize(self.order)
            odoo.synchronize(self.order)
            odoo.synchronize(self.order)
        payloads = [call.kwargs["payload"] for call in remote.call_args_list]
        self.assertEqual(len({payload["reference"] for payload in payloads}), 1)
        self.assertEqual({payload["revision"] for payload in payloads}, {1})
        self.assertEqual(OdooOrderLink.objects.filter(order=self.order).count(), 1)
        self.link.refresh_from_db()
        self.assertEqual(self.link.status, "synced")
        self.assertEqual(self.link.last_error, "")

    def test_revision_change_excludes_old_snapshot_from_results_until_resynchronized(self):
        odoo.accept_snapshot(self.link, self.snapshot(delivered=5, net_delivered=5))
        odoo.changed(self.order)
        self.link.refresh_from_db()
        self.assertEqual(self.link.revision, 2)
        self.assertEqual(self.link.status, "pending")
        self.assertEqual(odoo.reports()["rows"], [])
        self.assertEqual(odoo.order_context(self.order)["status"], "Pendiente de actualizar")
        with patch("operations.odoo.rpc", return_value=self.snapshot()) as remote:
            odoo.synchronize(self.order)
        self.assertEqual(remote.call_args.kwargs["payload"]["revision"], 2)

    def test_reports_use_net_returns_discounts_and_separate_units_without_rpc(self):
        unit_line = OrderLine.objects.create(order=self.order, product="Chorizo", unit="unid",
            requested_qty=10, unit_price=3, discount_percent=0)
        OdooProductMapping.objects.create(product="Chorizo", sku="LAB-CHORIZO", source_unit="unid",
                                         unit="uom.product_uom_unit")
        result = self.snapshot(delivered=5, returned=1, net_delivered=4)
        result["lines"].append({"key": str(unit_line.pk), "ordered": 10, "reserved": 0,
            "produced": 8, "delivered": 8, "returned": 1, "net_delivered": 7,
            "scrapped": 2, "unit_cost": 1})
        odoo.accept_snapshot(self.link, result)
        with patch("operations.odoo.rpc") as remote:
            reports = odoo.reports()
            remote.assert_not_called()
        totals = {total["unit"]: total for total in reports["totals"]}
        self.assertEqual(set(totals), {"kg", "unid"})
        self.assertEqual((totals["kg"]["delivered"], totals["kg"]["pending"],
                          totals["kg"]["fulfillment_percent"]), (4, 1, 80))
        self.assertEqual((totals["unid"]["delivered"], totals["unid"]["pending"],
                          totals["unid"]["fulfillment_percent"]), (7, 3, 70))
        by_product = {row["product"]: row for row in reports["rows"]}
        self.assertEqual(by_product["Alitas"]["margin"], Decimal("37"))
        self.assertEqual(by_product["Chorizo"]["margin"], Decimal("14"))
        self.assertEqual(by_product["Alitas"]["stage"], "Entrega parcial")
        self.assertIsNone(by_product["Alitas"]["on_time"])
        self.assertEqual(odoo.reports(self.order.required_date + timedelta(days=1))["rows"], [])

    @override_settings(TIME_ZONE="America/El_Salvador")
    def test_on_time_uses_local_delivery_day_and_only_completed_net_delivery(self):
        utc_day = self.order.required_date + timedelta(days=1)
        result = self.snapshot(delivered=5, net_delivered=5,
            last_delivery_at=utc_day.isoformat() + "T03:00:00+00:00")
        odoo.accept_snapshot(self.link, result)
        with timezone.override("America/El_Salvador"):
            self.assertIs(odoo.reports()["rows"][0]["on_time"], True)
            delivery = odoo.order_context(self.order)["lines"][0]
            self.assertTrue(timezone.is_aware(delivery["last_delivery_at"]))
            self.assertEqual(timezone.localtime(delivery["last_delivery_at"]).date(), self.order.required_date)
            self.assertEqual((delivery["net_delivered"], delivery["outstanding"]), (5, 0))
        result["lines"][0].update(returned=1, net_delivered=4)
        odoo.accept_snapshot(self.link, result)
        self.assertIsNone(odoo.reports()["rows"][0]["on_time"])
        delivery = odoo.order_context(self.order)["lines"][0]
        self.assertEqual((delivery["net_delivered"], delivery["outstanding"]), (4, 1))
        self.assertIsNone(odoo.delivery_moment({"last_delivery_at": "2026-99-99"}))

    def test_rejected_remote_edit_or_cancel_preserves_local_sale_and_revision(self):
        odoo.accept_snapshot(self.link, self.snapshot())
        url = reverse("sale_change", args=[self.order.pk])
        with self.enabled(), patch("operations.odoo.rpc", side_effect=odoo.OdooError("REMOTE EXECUTION ALREADY STARTED")):
            for action in ("edit", "cancel"):
                response = self.client.post(url, {"action": action, "change_reason": "Customer correction",
                    "line_id": str(self.line.pk), "quantity": "6", "unit_price": "19", "discount_percent": "5"})
                self.assertEqual(response.status_code, 302)
                self.order.refresh_from_db()
                self.line.refresh_from_db()
                self.link.refresh_from_db()
                self.assertEqual(self.order.status, OrderRequest.Status.PENDING)
                self.assertEqual(self.line.requested_qty, Decimal("5"))
                self.assertEqual(self.line.unit_price, Decimal("12.50"))
                self.assertEqual(self.link.revision, 1)

    def test_invalid_remote_snapshot_is_rejected_before_marking_complete(self):
        for change in ({"ordered": "NaN"}, {"reserved": "Infinity"}, {"produced": -1},
                       {"delivered": "not a quantity"}, {"net_delivered": "NaN"},
                       {"unit_cost": "Infinity"}, {"authorized_quantity": -1},
                       {"net_delivered": 99}, {"net_delivered": None}, {"authorized": "false"},
                       {"last_delivery_at": "2026-99-99"}):
            with self.subTest(change=change), self.assertRaises(odoo.OdooError):
                odoo.accept_snapshot(self.link, self.snapshot(**change))
            self.link.refresh_from_db()
            self.assertEqual(self.link.status, "pending")
            self.assertEqual(self.link.snapshot, {})
        for result in (self.snapshot(key="foreign-sale-line"),
                       {**self.snapshot(), "reference": "smartorder:FOREIGN"},
                       {**self.snapshot(), "reference": None},
                       {**self.snapshot(), "revision": None},
                       {**self.snapshot(), "revision": True},
                       {**self.snapshot(), "revision": 10**100},
                       {**self.snapshot(), "lines": []},
                       {**self.snapshot(), "invoices": [{"residual": "NaN"}]},
                       {**self.snapshot(), "sale": None}, {**self.snapshot(), "sale": {}}, []):
            with self.assertRaises(odoo.OdooError):
                odoo.accept_snapshot(self.link, result)

    def test_late_remote_response_preserves_new_local_revision_without_false_success(self):
        def completed_after_edit(*args, **kwargs):
            odoo.changed(self.order)
            return self.snapshot()

        with patch("operations.odoo.rpc", side_effect=completed_after_edit), self.assertRaises(odoo.OdooError):
            odoo.synchronize(self.order)
        self.link.refresh_from_db()
        self.assertEqual((self.link.revision, self.link.status, self.link.snapshot), (2, "pending", {}))
        self.assertIsNone(self.link.last_synced)
        self.assertEqual(self.link.last_error, "")

    def test_cancel_requires_remote_cancelled_state_and_invalid_actions_never_call_rpc(self):
        for result in (self.snapshot(), {**self.snapshot(), "sale": None}):
            with patch("operations.odoo.rpc", return_value=result), self.assertRaises(odoo.OdooError):
                odoo.synchronize(self.order, "cancel")
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, OrderRequest.Status.PENDING)
        with patch("operations.odoo.rpc") as remote, self.assertRaises(odoo.OdooError):
            odoo.synchronize(self.order, "arbitrary")
        remote.assert_not_called()

    def test_authorization_stops_before_manufacturing_if_sync_response_is_obsolete(self):
        self.line.status, self.line.approved_qty = OrderLine.Status.APPROVED, Decimal("3")
        self.line.save(update_fields=["status", "approved_qty"])

        def late_snapshot(*args, **kwargs):
            odoo.changed(self.order)
            return self.snapshot()

        with patch("operations.odoo.rpc", side_effect=late_snapshot) as remote, self.assertRaises(odoo.OdooError):
            odoo.synchronize(self.order, "authorize")
        self.assertEqual(remote.call_count, 1)
        self.assertEqual(remote.call_args.args[0], "sync_sale")
        self.link.refresh_from_db()
        self.assertEqual((self.link.revision, self.link.status), (2, "pending"))

    def test_authorize_and_cancel_send_exact_revision_and_preserve_reference(self):
        self.line.status, self.line.approved_qty = OrderLine.Status.APPROVED, Decimal("3")
        self.line.save(update_fields=["status", "approved_qty"])
        with patch("operations.odoo.rpc", side_effect=[self.snapshot(),
                self.snapshot(authorized=True, authorized_quantity=3)]) as remote:
            odoo.synchronize(self.order, "authorize")
        authorization = remote.call_args_list[1]
        self.assertEqual(authorization.args[0], "authorize_production")
        self.assertEqual(authorization.kwargs, {"reference": "smartorder:" + str(self.link.reference),
            "revision": 1, "lines": [{"key": str(self.line.pk), "quantity": 3}]})
        # Separate cancellation response verifies the protocol; Odoo enforces the execution gate.
        cancelled = self.snapshot()
        cancelled["sale"]["state"] = "cancel"
        with patch("operations.odoo.rpc", return_value=cancelled) as remote:
            odoo.synchronize(self.order, "cancel")
        self.assertEqual(remote.call_args.kwargs, {"reference": "smartorder:" + str(self.link.reference), "revision": 1})

    def test_matching_remote_advanced_revision_is_adopted_without_another_sale(self):
        result = self.snapshot()
        result["revision"] = result["commercial"]["revision"] = 3
        with patch("operations.odoo.rpc", return_value=result):
            odoo.synchronize(self.order, "refresh")
        self.link.refresh_from_db()
        self.assertEqual((self.link.revision, self.link.status), (3, "synced"))
        self.assertEqual(OdooOrderLink.objects.filter(order=self.order).count(), 1)
        self.assertFalse(odoo.order_context(self.order)["stale"])

    def test_authorize_control_only_offers_approved_quantities_not_already_acknowledged(self):
        self.line.status, self.line.approved_qty = OrderLine.Status.EXPORTED, Decimal("3")
        self.line.save(update_fields=["status", "approved_qty"])
        with self.enabled():
            self.assertTrue(odoo.order_context(self.order, admin=True)["can_authorize"])
            odoo.accept_snapshot(self.link, self.snapshot(authorized=True, authorized_quantity=3))
            self.assertFalse(odoo.order_context(self.order, admin=True)["can_authorize"])
            odoo.accept_snapshot(self.link, self.snapshot(authorized=False, authorized_quantity=0))
            self.assertTrue(odoo.order_context(self.order, admin=True)["can_authorize"])

    def test_divergent_remote_revision_remains_pending_and_never_authorizes(self):
        odoo.accept_snapshot(self.link, self.snapshot())
        self.line.status, self.line.approved_qty = OrderLine.Status.APPROVED, Decimal("3")
        self.line.save(update_fields=["status", "approved_qty"])
        result = self.snapshot(ordered=6)
        result["revision"] = result["commercial"]["revision"] = 2
        result["commercial"]["lines"][0]["quantity"] = 6
        with self.enabled(), patch("operations.odoo.rpc", return_value=result) as remote:
            with self.assertRaises(odoo.OdooError):
                odoo.synchronize(self.order, "authorize")
            self.assertEqual(remote.call_count, 1)
            public = odoo.order_context(self.order, admin=True)
            self.assertTrue(public["stale"])
            self.assertFalse(public["can_authorize"])
            self.assertEqual(public["status"], "Pendiente de actualizar")
        self.link.refresh_from_db()
        self.assertEqual((self.link.revision, self.link.status), (1, "error"))
        self.assertEqual(self.link.snapshot["revision"], 1)

    def test_refresh_reconciles_already_effective_remote_cancel_idempotently(self):
        result = self.snapshot(delivery_state="cancel")
        result["sale"]["state"] = "cancel"
        with patch("operations.odoo.rpc", return_value=result):
            odoo.synchronize(self.order, "refresh")
            odoo.synchronize(self.order, "refresh")
        self.order.refresh_from_db()
        self.line.refresh_from_db()
        self.link.refresh_from_db()
        self.assertEqual(self.order.status, OrderRequest.Status.CANCELLED)
        self.assertEqual(self.line.status, OrderLine.Status.REJECTED)
        self.assertEqual((self.link.revision, self.link.status), (1, "synced"))
        self.assertEqual(OdooOrderLink.objects.filter(order=self.order).count(), 1)
        self.assertEqual(odoo.reports()["rows"], [])

    def test_edit_lost_remote_response_recovers_by_resending_identical_change(self):
        odoo.accept_snapshot(self.link, self.snapshot())
        accepted, lost = {}, True

        def remote_sale(method, **kwargs):
            nonlocal lost
            self.assertEqual(method, "sync_sale")
            payload = json.loads(json.dumps(kwargs["payload"]))
            if accepted:
                self.assertEqual(accepted, payload)
            else:
                accepted.update(payload)  # simulated Odoo commit survives the local rollback
            if lost:
                lost = False
                raise odoo.OdooError("RESPONSE LOST AFTER REMOTE COMMIT")
            result = self.snapshot(ordered=6)
            result.update(revision=2, commercial=accepted)
            return result

        change = {"action": "edit", "change_reason": "Customer correction", "line_id": str(self.line.pk),
                  "quantity": "6", "unit_price": "19", "discount_percent": "5"}
        with self.enabled(), patch("operations.odoo.rpc", side_effect=remote_sale):
            self.assertEqual(self.client.post(reverse("sale_change", args=[self.order.pk]), change).status_code, 302)
            self.line.refresh_from_db()
            self.link.refresh_from_db()
            self.assertEqual((self.line.requested_qty, self.link.revision), (5, 1))
            self.assertEqual(self.link.status, "error")
            self.assertTrue(odoo.order_context(self.order)["stale"])
            self.assertEqual(odoo.order_context(self.order)["status"], "Pendiente de actualizar")
            self.assertEqual(self.client.post(reverse("sale_change", args=[self.order.pk]), change).status_code, 302)
        self.line.refresh_from_db()
        self.link.refresh_from_db()
        self.assertEqual((self.line.requested_qty, self.line.unit_price, self.line.discount_percent), (6, 19, 5))
        self.assertEqual((self.link.revision, self.link.status), (2, "synced"))
        self.assertEqual(accepted["reference"], "smartorder:" + str(self.link.reference))
        self.assertEqual(OdooOrderLink.objects.filter(order=self.order).count(), 1)

    def test_local_transaction_failure_after_remote_commit_recovers_same_edit(self):
        odoo.accept_snapshot(self.link, self.snapshot())
        accepted = {}
        create_event = AccessEvent.objects.create

        def remote_sale(method, **kwargs):
            self.assertEqual(method, "sync_sale")
            payload = json.loads(json.dumps(kwargs["payload"]))
            if accepted:
                self.assertEqual(accepted, payload)
            else:
                accepted.update(payload)
            result = self.snapshot(ordered=6)
            result.update(revision=2, commercial=accepted)
            return result

        def fail_local_audit(**kwargs):
            if kwargs["action"] == "sale_edit":
                raise RuntimeError("SIMULATED LOCAL TRANSACTION FAILURE AFTER REMOTE COMMIT")
            return create_event(**kwargs)

        change = {"action": "edit", "change_reason": "Customer correction", "line_id": str(self.line.pk),
                  "quantity": "6", "unit_price": "19", "discount_percent": "5"}
        with self.enabled(), patch("operations.odoo.rpc", side_effect=remote_sale):
            with patch("operations.views.AccessEvent.objects.create", side_effect=fail_local_audit):
                with self.assertRaises(RuntimeError):
                    self.client.post(reverse("sale_change", args=[self.order.pk]), change)
            self.line.refresh_from_db()
            self.link.refresh_from_db()
            self.assertEqual((self.line.requested_qty, self.link.revision), (5, 1))
            self.assertEqual(self.client.post(reverse("sale_change", args=[self.order.pk]), change).status_code, 302)
        self.line.refresh_from_db()
        self.link.refresh_from_db()
        self.assertEqual((self.line.requested_qty, self.line.unit_price, self.line.discount_percent), (6, 19, 5))
        self.assertEqual((self.link.revision, self.link.status), (2, "synced"))
        self.assertEqual(AccessEvent.objects.filter(action="sale_edit").count(), 1)
        self.assertEqual(OdooOrderLink.objects.filter(order=self.order).count(), 1)

    def test_rpc_refuses_external_or_ambiguous_urls_and_credential_redirects(self):
        with patch("operations.odoo.build_opener") as opener:
            for url in ("https://127.0.0.1:8079", "http://example.com", "http://localhost.evil",
                        "http://127.0.0.1:8079/api", "http://user:pass@127.0.0.1",
                        "http://127.0.0.1?other=1", "file:///private",
                        "http://127.0.0.1:invalid", "http://127.0.0.1:99999"):
                with self.enabled(url), self.assertRaises(odoo.OdooError):
                    odoo.rpc("ping")
            opener.assert_not_called()
        with self.assertRaises(odoo.OdooError):
            odoo.NoRedirect().redirect_request(None, None, 302, "Redirect", {}, "http://elsewhere")

    def test_rpc_uses_no_proxy_and_rejects_nonfinite_or_oversized_response(self):
        for raw in (b'{"value": NaN}', b'{"value": 1e999}', b"[1,2]", b"x" * 2_000_001):
            opener = MagicMock()
            opener.open.return_value.__enter__.return_value.read.return_value = raw
            with self.enabled(), patch("operations.odoo.build_opener", return_value=opener) as build:
                with self.assertRaises(odoo.OdooError):
                    odoo.rpc("ping")
                self.assertEqual(build.call_args.args[0].proxies, {})
                self.assertIsInstance(build.call_args.args[1], odoo.NoRedirect)
                request = opener.open.call_args.args[0]
                self.assertEqual(request.get_header("X-odoo-database"), "unit_tests_only")
                self.assertEqual(opener.open.call_args.kwargs["timeout"], 15)
