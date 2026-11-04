"""Integration checks for the local sales-to-production workflow."""

from datetime import date, timedelta
from decimal import Decimal
from io import BytesIO
from tempfile import TemporaryDirectory

from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from openpyxl import Workbook, load_workbook

from .models import (
    Assignment, DatedFlow, InventoryAdjustment, InventoryBatch, InventoryItem,
    OrderCoverage, OrderLine, OrderRequest, SalesCoverage, SalesDataset, SalesLine,
    StockCorrectionRequest,
)
from .services import import_inventory, import_sales, save_sales_coverage


def sales_book(first: date, days: int, *, multiplier: int = 1,
               products: tuple[str, ...] = ("Alitas", "Chorizo")) -> bytes:
    book = Workbook()
    sheet = book.active
    sheet.title = "Ventas"
    sheet.append(("Fecha", "Cliente", "Producto", "Cantidad_kg_unid",
                  "Precio_Unitario_USD", "Monto_Venta_USD"))
    for offset in range(days):
        day = first + timedelta(days=offset)
        for index, product in enumerate(products, start=1):
            qty = index * multiplier
            sheet.append((day, "Cliente A", product, qty, 10, qty * 10))
    output = BytesIO()
    book.save(output)
    return output.getvalue()


def inventory_book(cutoff: date, *, available: int = 2) -> bytes:
    book = Workbook()
    sheet = book.active
    sheet.title = "Inventario"
    sheet.append(("Cliente", "Producto", "Existencia_Disponible", "Fecha_Corte", "Unidad"))
    sheet.append(("Cliente A", "Alitas", available, cutoff, "kg"))
    sheet.append(("Cliente A", "Chorizo", available, cutoff, "kg"))
    flow = book.create_sheet("Movimientos")
    flow.append(("Tipo", "Cliente", "Producto", "Cantidad", "Fecha"))
    flow.append(("Entrada", "Cliente A", "Alitas", 1, cutoff + timedelta(days=1)))
    flow.append(("Compromiso_Cliente", "Cliente A", "Alitas", 3,
                 cutoff + timedelta(days=2)))
    output = BytesIO()
    book.save(output)
    return output.getvalue()


class LocalCase:
    def post_sale(self, payload):
        payload = dict(payload)
        if "submission_token" not in payload:
            page = self.client.get(reverse("order_create"), {"client": payload.get("client", "Cliente A")})
            if page.status_code == 200:
                payload["submission_token"] = page.context["submission_token"]
        return self.client.post(reverse("order_create"), payload)

    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.media_override = override_settings(MEDIA_ROOT=self.tmp.name)
        self.media_override.enable()
        self.addCleanup(self.media_override.disable)
        User = get_user_model()
        self.admin = User.objects.create_user(
            "admin_vitali", password="ClaveAdministrativa123", is_staff=True
        )
        self.seller = User.objects.create_user(
            "vendedor_vitali", password="ClaveDelVendedor123"
        )
        from .models import PriceTariff
        PriceTariff.objects.create(client="Cliente A", product="Alitas", unit="kg", unit_price="12.5", discount_percent=10, updated_by=self.admin)
        PriceTariff.objects.create(client="Cliente A", product="Chorizo", unit="kg", unit_price=10, updated_by=self.admin)



class DataAndSchemaTests(LocalCase, TestCase):
    def test_sales_and_inventory_etl_preserve_source_and_dated_flows(self):
        today = timezone.localdate()
        sales = import_sales(sales_book(today - timedelta(days=364), 365),
                             "ventas.xlsx", self.admin, verified=True)
        sales.full_snapshot_confirmed = True
        sales.save(update_fields=["full_snapshot_confirmed"])
        self.assertTrue(sales.file.name.startswith("sales/"))
        self.assertEqual(sales.lines.count(), 730)
        self.assertEqual(SalesLine.objects.filter(dataset=sales, product="Alitas").count(), 365)
        stock = import_inventory(inventory_book(today), "inventario.xlsx", self.admin)
        stock.unit_match_confirmed = True
        stock.save(update_fields=["unit_match_confirmed"])
        self.assertTrue(stock.file.name.startswith("inventory/"))
        self.assertEqual(InventoryItem.objects.filter(batch=stock).count(), 2)
        self.assertEqual(
            list(DatedFlow.objects.filter(batch=stock).values_list("kind", flat=True)),
            ["incoming", "customer_commitment"],
        )

    def test_weekly_replacement_preserves_request_pending_revalidation(self):
        first = date(2025, 1, 1)
        old = import_sales(sales_book(first, 40), "primero.xlsx", self.admin, verified=False)
        request = OrderRequest.objects.create(
            seller=self.seller, client="Cliente A", dataset=old,
            required_date=timezone.localdate(),
        )
        OrderLine.objects.create(
            order=request, product="Alitas", method="Pedido manual",
            requested_qty=Decimal("5"), reason="Reposición solicitada",
        )
        new = import_sales(sales_book(first, 40, multiplier=2),
                           "segundo.xlsx", self.admin, verified=False)
        request.refresh_from_db()
        old.refresh_from_db()
        self.assertEqual(request.status, OrderRequest.Status.PENDING)
        self.assertIsNone(request.revalidated_against)
        self.assertFalse(old.active)
        self.assertTrue(new.active)

    def test_only_one_seller_can_own_a_client(self):
        Assignment.objects.create(user=self.seller, client="Cliente A")
        with self.assertRaises(IntegrityError):
            Assignment.objects.create(user=self.admin, client="Cliente A")

    def test_day_level_coverage_is_unique_across_orders(self):
        dataset = import_sales(sales_book(date(2025, 1, 1), 2),
                               "ventas.xlsx", self.admin, verified=False)
        day = timezone.localdate()
        first = OrderRequest.objects.create(
            seller=self.seller, client="Cliente A", dataset=dataset, required_date=day,
        )
        second = OrderRequest.objects.create(
            seller=self.seller, client="Cliente A", dataset=dataset,
            required_date=day + timedelta(days=1),
        )
        line_a = OrderLine.objects.create(
            order=first, product="Alitas", requested_qty=Decimal("5"), method="manual"
        )
        line_b = OrderLine.objects.create(
            order=second, product="Alitas", requested_qty=Decimal("5"), method="manual"
        )
        OrderCoverage.objects.create(line=line_a, client="Cliente A", product="Alitas", day=day)
        with self.assertRaises(IntegrityError):
            OrderCoverage.objects.create(line=line_b, client="Cliente A", product="Alitas", day=day)

    def test_adjustment_preserves_imported_inventory_row(self):
        batch = import_inventory(inventory_book(timezone.localdate()),
                                 "inventario.xlsx", self.admin)
        original = batch.items.get(client="Cliente A", product="Alitas")
        correction = StockCorrectionRequest.objects.create(
            seller=self.seller, client="Cliente A", product="Alitas", unit="kg",
            proposed_available=Decimal("9"), observed_on=timezone.localdate(),
            reason="Conteo físico actualizado",
        )
        InventoryAdjustment.objects.create(
            batch=batch, correction=correction, client="Cliente A", product="Alitas",
            available=Decimal("9"), observed_on=timezone.localdate(), approved_by=self.admin,
        )
        original.refresh_from_db()
        self.assertEqual(original.available, Decimal("2"))
        self.assertEqual(batch.adjustments.get(correction=correction).available, Decimal("9"))


class LocalWorkflowTests(LocalCase, TestCase):
    def _upload_sales(self, raw: bytes, *, verified: bool = False,
                      full_snapshot: bool = False,
                      date_semantics: str = SalesDataset.DateSemantics.SALE_ORDER) -> SalesDataset:
        self.client.force_login(self.admin)
        fields = {
            "sales_file": SimpleUploadedFile(
                "ventas.xlsx", raw,
                content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            ),
            "provenance": SalesDataset.Provenance.SIMULATED,
            "date_semantics": date_semantics,
        }
        if verified:
            fields.update(verified="on", confirmed_origin="on")
        if full_snapshot:
            fields["confirmed_full_snapshot"] = "on"
        response = self.client.post(reverse("sales_upload"), fields)
        self.assertEqual(response.status_code, 302)
        return SalesDataset.objects.get(active=True)

    def _upload_inventory(self, raw: bytes, *, units_confirmed: bool = False) -> InventoryBatch:
        self.client.force_login(self.admin)
        fields = {
            "inventory_file": SimpleUploadedFile(
                "inventario.xlsx", raw,
                content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            ),
        }
        if units_confirmed:
            fields["unit_match_confirmed"] = "on"
        response = self.client.post(reverse("inventory_upload"), fields)
        self.assertEqual(response.status_code, 302)
        return InventoryBatch.objects.get(active=True)

    def _confirm_sales_units(self, dataset: SalesDataset, unit: str = "kg"):
        end = min(dataset.last_date, timezone.localdate() - timedelta(days=1))
        for client, product in dataset.lines.values_list("client", "product").distinct():
            coverage_id = dataset.coverage.filter(
                client=client, product=product,
            ).values_list("pk", flat=True).first()
            save_sales_coverage(
                dataset, self.admin, client=client, product=product, unit=unit,
                start_date=dataset.first_date.isoformat(), end_date=end.isoformat(),
                status=SalesCoverage.Status.COMPLETE, coverage_id=coverage_id,
            )
            self.assertEqual(dataset.coverage.filter(client=client, product=product).count(), 1)

    def _assign_seller(self):
        self.client.force_login(self.admin)
        response = self.client.post(reverse("users"), {
            "action": "assign", "user_id": self.seller.pk, "clients": ["Cliente A"],
        })
        self.assertEqual(response.status_code, 302)
        self.assertTrue(Assignment.objects.filter(user=self.seller, client="Cliente A").exists())

    @override_settings(DEBUG=True)
    def test_setup_is_local_and_single_use(self):
        from .models import PriceTariff
        PriceTariff.objects.all().delete()
        get_user_model().objects.all().delete()
        denied = self.client.get(reverse("setup"), REMOTE_ADDR="192.0.2.10", HTTP_HOST="localhost")
        self.assertEqual(denied.status_code, 403)
        response = self.client.post(reverse("setup"), {
            "username": "jefe_vitali", "password": "NuevaClaveLocal#2026",
        }, REMOTE_ADDR="127.0.0.1", HTTP_HOST="localhost")
        self.assertEqual(response.status_code, 302)
        self.assertTrue(get_user_model().objects.get(username="jefe_vitali").is_staff)
        self.assertEqual(self.client.get(reverse("setup"), HTTP_HOST="localhost").status_code, 302)

    def test_password_minimum_is_eight_characters(self):
        with self.assertRaises(ValidationError):
            validate_password("J7!mQ2#")
        validate_password("J7!mQ2#v")

    @override_settings(SMARTORDER_SETUP_CODE="setup-code-for-tests-only-32-characters")
    def test_remote_setup_requires_the_configured_code(self):
        from .models import PriceTariff
        PriceTariff.objects.all().delete()
        get_user_model().objects.all().delete()
        remote = {"REMOTE_ADDR": "192.0.2.10", "HTTP_HOST": "localhost"}
        self.assertEqual(self.client.get(reverse("setup"), **remote).status_code, 200)
        with override_settings(SMARTORDER_SETUP_CODE="demasiado-corto"):
            self.assertEqual(self.client.get(reverse("setup"), **remote).status_code, 403)
        invalid = self.client.post(reverse("setup"), {
            "username": "jefe_vitali", "password": "NuevaClaveSegura#2026",
            "setup_code": "incorrecto",
        }, **remote)
        self.assertEqual(invalid.status_code, 200)
        self.assertFalse(get_user_model().objects.exists())
        valid = self.client.post(reverse("setup"), {
            "username": "jefe_vitali", "password": "NuevaClaveSegura#2026",
            "setup_code": "setup-code-for-tests-only-32-characters",
        }, **remote)
        self.assertEqual(valid.status_code, 302)
        self.assertTrue(get_user_model().objects.get(username="jefe_vitali").is_staff)

    def test_seller_cannot_upload_manage_users_review_or_export(self):
        self.client.force_login(self.seller)
        for name in ("sales_upload", "inventory_upload", "inventory_template", "users"):
            self.assertEqual(self.client.get(reverse(name)).status_code, 403, name)
        self.assertEqual(self.client.post(reverse("export_batch"), {"line_ids": ["1"]}).status_code, 403)
        self._upload_sales(sales_book(date(2025, 1, 1), 5))
        self.client.force_login(self.seller)
        self.assertEqual(self.client.get(reverse("order_create") + "?client=Otro").status_code, 403)
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(reverse("order_create")).status_code, 403)

    def test_post_without_csrf_is_blocked(self):
        strict = Client(enforce_csrf_checks=True)
        strict.force_login(self.admin)
        response = strict.post(reverse("sales_upload"), {"verified": ""})
        self.assertEqual(response.status_code, 403)

    def test_2025_reference_yields_manual_multi_product_review_and_export(self):
        today = timezone.localdate()
        year = today.year - 1
        first = date(year, 1, 1)
        days = (date(year + 1, 1, 1) - first).days
        dataset = self._upload_sales(sales_book(first, days))
        self.assertFalse(dataset.verified)
        self._assign_seller()
        self.client.force_login(self.seller)
        recommendation = self.client.get(reverse("recommendations"), {
            "client": "Cliente A", "required_date": today.isoformat(),
        })
        self.assertEqual(recommendation.status_code, 200)
        self.assertTrue(all(card["suggested"] is None
                            for card in recommendation.context["recommendations"]))
        response = self.post_sale({
            "client": "Cliente A", "required_date": today.isoformat(),
            "note": "Revisar para producción",
            "product": ["Alitas", "Chorizo"], "quantity": ["5", "3"],
            "unit": ["kg", "kg"], "reason": ["Pedido manual", "Pedido manual"],
        })
        self.assertEqual(response.status_code, 302)
        order = OrderRequest.objects.get(seller=self.seller)
        self.assertEqual(order.lines.count(), 2)
        self.assertTrue(all(line.suggested_qty is None for line in order.lines.all()))
        lines = {line.product: line for line in order.lines.all()}
        self.client.force_login(self.admin)
        self.client.post(reverse("order_review", args=[order.pk]), {
            "line_id": lines["Alitas"].pk, "decision": "approve",
            "approved_quantity": "4", "review_note": "Ajustado por administración",
        })
        self.client.post(reverse("order_review", args=[order.pk]), {
            "line_id": lines["Chorizo"].pk, "decision": "reject",
            "review_note": "Sin capacidad para este producto",
        })
        order.refresh_from_db()
        self.assertEqual(order.status, OrderRequest.Status.REVIEWED)
        premature = self.client.post(reverse("export_batch"), {
            "line_ids": [str(lines["Alitas"].pk)], "action": "confirm",
        })
        self.assertEqual(premature.status_code, 302)
        lines["Alitas"].refresh_from_db()
        self.assertEqual(lines["Alitas"].status, OrderLine.Status.APPROVED)
        export = self.client.post(reverse("export_batch"), {
            "line_ids": [str(lines["Alitas"].pk)], "action": "download",
        })
        self.assertEqual(export.status_code, 200)
        self.assertTrue(export.content.startswith(b"PK"))
        workbook = load_workbook(BytesIO(export.content), read_only=True)
        self.assertEqual(workbook.active["C2"].value, "Alitas")
        self.assertEqual(workbook.active["E2"].value, 4)
        workbook.close()
        confirmed = self.client.post(reverse("export_batch"), {
            "line_ids": [str(lines["Alitas"].pk)], "action": "confirm",
            "shared_confirm": "1",
        })
        self.assertEqual(confirmed.status_code, 302)
        lines["Alitas"].refresh_from_db()
        order.refresh_from_db()
        self.assertEqual(lines["Alitas"].status, OrderLine.Status.EXPORTED)
        self.assertEqual(order.status, OrderRequest.Status.EXPORTED)

    def test_recent_verified_source_and_stock_can_offer_explained_quantity(self):
        today = timezone.localdate()
        dataset = self._upload_sales(
            sales_book(today - timedelta(days=365), 365), verified=True, full_snapshot=True,
            date_semantics=SalesDataset.DateSemantics.DELIVERY,
        )
        self._confirm_sales_units(dataset)
        self.assertTrue(dataset.verified)
        stock = self._upload_inventory(inventory_book(today), units_confirmed=True)
        self.assertTrue(stock.active)
        DatedFlow.objects.filter(batch=stock).update(
            confirmed=True, reconciliation=DatedFlow.Reconciliation.UNIQUE,
        )
        # A confirmed movement for another client must not affect this
        # customer's recommendation or export commitment totals.
        DatedFlow.objects.create(
            batch=stock, kind=DatedFlow.Kind.INCOMING,
            client="Cliente B", product="Alitas", unit="kg",
            quantity=Decimal("999"), due_date=today + timedelta(days=1),
            confirmed=True, reconciliation=DatedFlow.Reconciliation.UNIQUE,
            created_by=self.admin,
        )
        self._assign_seller()
        self.client.force_login(self.seller)
        response = self.client.get(reverse("recommendations"), {
            "client": "Cliente A", "required_date": (today + timedelta(days=3)).isoformat(),
        })
        self.assertEqual(response.status_code, 200)
        cards = {card["product"]: card for card in response.context["recommendations"]}
        self.assertContains(response, "decision-suggested")
        self.assertNotContains(response, "Corte de inventario:")
        self.assertNotContains(response, "Decisión manual")
        self.assertEqual(cards["Alitas"]["state"], "suggested")
        self.assertIsNotNone(cards["Alitas"]["suggested"])
        self.assertNotIn("incoming", cards["Alitas"])
        self.assertNotIn("commitments_before", cards["Alitas"])
        self.assertNotIn("sales_usd_display", cards["Alitas"])
        from .services import recommendation_cards
        internal = next(card for card in recommendation_cards(dataset, today + timedelta(days=3), "Cliente A", mode="vitali") if card["product"] == "Alitas")
        self.assertEqual(internal["incoming"], Decimal("1"))
        self.assertEqual(internal["commitments_before"], Decimal("3"))
        self.assertEqual(internal["commitments"], Decimal("0"))
        response = self.post_sale({
            "client": "Cliente A",
            "required_date": (today + timedelta(days=3)).isoformat(),
            "product": ["Alitas", "Chorizo"],
            "quantity": [str(cards["Alitas"]["suggested"]),
                         str(cards["Chorizo"]["suggested"])],
            "unit": ["kg", "kg"], "reason": ["", ""],
        })
        self.assertEqual(response.status_code, 302)
        order = OrderRequest.objects.get(seller=self.seller)
        self.assertEqual(order.lines.count(), 2)
        self.assertTrue(all(line.suggested_qty is not None for line in order.lines.all()))

    def test_historical_product_priority_is_visible_with_stale_unverified_sales(self):
        from datetime import timedelta
        first = timezone.localdate() - timedelta(days=400)
        workbook = Workbook()
        sheet = workbook.active
        sheet.append(("Fecha", "Cliente", "Producto", "Cantidad_kg_unid",
                      "Precio_Unitario_USD", "Monto_Venta_USD", "Unidad"))
        for day, product, quantity in [
            (0, "Alitas", 2), (1, "Alitas", 3), (2, "Alitas", 1),
            (1, "Chorizo", 800),
        ]:
            sheet.append((first + timedelta(days=day), "Cliente A", product,
                          quantity, 10, quantity * 10, "kg" if product == "Alitas" else "lb"))
        output = BytesIO()
        workbook.save(output)
        self._upload_sales(output.getvalue())
        self._assign_seller()
        self.client.force_login(self.seller)
        response = self.client.get(reverse("recommendations"), {
            "client": "Cliente A", "required_date": timezone.localdate().isoformat(),
        })
        self.assertEqual(response.status_code, 200)
        cards = response.context["recommendations"]
        self.assertEqual([item["product"] for item in cards], ["Alitas", "Chorizo"])
        self.assertEqual([item["historical_rank"] for item in cards], [1, 2])
        self.assertEqual([item["historical_sale_days"] for item in cards], [3, 1])
        self.assertTrue(all(item["suggested"] is None for item in cards))
        self.assertContains(response, "Prioridad histórica 1 de 2")
        self.assertContains(response, "3 días con ventas registradas")
        self.assertContains(response, "No es una predicción")
        self.assertNotIn("sales_usd_display", cards[0])
        from .services import recommendation_cards
        backend = recommendation_cards(
            SalesDataset.objects.get(active=True), timezone.localdate(),
            "Cliente A", mode="sale")
        self.assertEqual(backend[0]["historical_rank"], 1)
        self.assertGreater(backend[1]["sales_usd_display"], "$0.00")
        self.assertEqual(backend[1]["historical_sale_days"], 1)

    def test_dated_historical_ranking_changes_with_target_and_never_uses_future_sales(self):
        """Calendar-relative product priority is descriptive and cutoff-safe."""
        from .services import recommendation_cards
        book = Workbook()
        tab = book.active
        tab.append(("Fecha", "Cliente", "Producto", "Cantidad_kg_unid",
                    "Precio_Unitario_USD", "Monto_Venta_USD", "Unidad"))
        for index, day in enumerate((3, 6, 9)):
            tab.append((date(2025, 11, day), "Cliente A", "Alitas",
                        2 + index, 10, (2 + index) * 10, "kg"))
        for day in (4, 8, 12, 16):
            tab.append((date(2025, 12, day), "Cliente A", "Chorizo",
                        3, 100, 300, "lb"))
        tab.append((date(2025, 11, 7), "Cliente A", "Chorizo", 500, 100, 50000, "lb"))
        tab.append((date(2025, 12, 10), "Cliente A", "Alitas", 1, 10, 10, "kg"))
        buffer = BytesIO()
        book.save(buffer)
        dataset = self._upload_sales(buffer.getvalue())
        self._assign_seller()
        nov_today, nov_target = date(2026, 11, 2), date(2026, 11, 5)
        dec_today, dec_target = date(2026, 12, 2), date(2026, 12, 5)
        november = recommendation_cards(dataset, nov_target, "Cliente A", today=nov_today)
        december = recommendation_cards(dataset, dec_target, "Cliente A", today=dec_today)
        self.assertEqual([r["product"] for r in november], ["Alitas", "Chorizo"])
        self.assertEqual([r["product"] for r in december], ["Chorizo", "Alitas"])
        self.assertEqual([r["historical_seasonal_days"] for r in november], [3, 1])
        self.assertEqual([r["historical_seasonal_days"] for r in december], [4, 1])
        self.assertTrue(all(r["suggested"] is None for r in november + december))
        self.assertTrue(all(r["commercial_forecast"] is None for r in november + december))
        self.assertEqual(november[0]["historical_cutoff_date"], nov_today - timedelta(days=1))

        # In March 2025 the November/December sales don't exist yet.
        # No future-only product can appear as a historical purchase.
        march = recommendation_cards(
            dataset, date(2025, 3, 7), "Cliente A",
            today=date(2025, 3, 4),
        )
        self.assertEqual(march, [])

    def test_historical_rank_replay_excludes_later_sales_from_same_dataset(self):
        from .services import recommendation_cards
        book = Workbook()
        tab = book.active
        tab.append(("Fecha", "Cliente", "Producto", "Cantidad_kg_unid",
                    "Precio_Unitario_USD", "Monto_Venta_USD", "Unidad"))
        for day in (date(2025, 1, 3), date(2025, 2, 3), date(2025, 3, 3)):
            tab.append((day, "Cliente A", "Alitas", 2, 5, 10, "kg"))
        for day in (date(2025, 1, 4), date(2025, 6, 3), date(2025, 6, 4),
                    date(2025, 7, 5), date(2025, 9, 3)):
            tab.append((day, "Cliente A", "Chorizo", 1, 1000, 1000, "lb"))
        buffer = BytesIO()
        book.save(buffer)
        dataset = self._upload_sales(buffer.getvalue())
        before = recommendation_cards(
            dataset, date(2025, 4, 8), "Cliente A",
            today=date(2025, 4, 5),
        )
        self.assertEqual([r["product"] for r in before], ["Alitas", "Chorizo"])
        self.assertEqual([r["historical_sale_days"] for r in before], [3, 1])
        self.assertTrue(all(r["historical_cutoff_date"] == date(2025, 4, 4) for r in before))
        after = recommendation_cards(
            dataset, date(2025, 10, 8), "Cliente A",
            today=date(2025, 10, 5),
        )
        self.assertEqual([r["product"] for r in after], ["Chorizo", "Alitas"])
        self.assertEqual(after[0]["historical_sale_days"], 5)

    def test_unconfirmed_inventory_units_do_not_change_customer_sale_quantity(self):
        today = timezone.localdate()
        dataset = self._upload_sales(
            sales_book(today - timedelta(days=365), 365, products=("Alitas",)),
            verified=True, full_snapshot=True,
            date_semantics=SalesDataset.DateSemantics.DELIVERY,
        )
        self._confirm_sales_units(dataset)
        batch = self._upload_inventory(inventory_book(today, available=0), units_confirmed=False)
        self.assertFalse(batch.unit_match_confirmed)
        self._assign_seller()
        self.client.force_login(self.seller)
        response = self.client.get(reverse("recommendations"), {
            "client": "Cliente A", "required_date": today.isoformat(),
        })
        self.assertEqual(response.status_code, 200)
        card = response.context["recommendations"][0]
        self.assertIsNotNone(card["suggested"])
        self.assertEqual(card["available"], Decimal("0.000"))

    def test_empty_sales_filter_keeps_active_source_and_reset_action(self):
        dataset = self._upload_sales(sales_book(date(2025, 1, 1), 40))
        self.client.force_login(self.admin)
        response = self.client.get(reverse("dashboard"), {
            "start_date": "2024-01-01", "end_date": "2024-01-31",
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["dataset"].pk, dataset.pk)
        self.assertContains(response, "Sin ventas para estos filtros")
        self.assertContains(response, "Restablecer filtros")
        self.assertContains(response, 'id="filter-start"')
        self.assertNotContains(response, "Aún no hay ventas activas")

    def test_role_pages_render_real_metrics_and_seller_products(self):
        today = date.today()
        self._upload_sales(sales_book(today - timedelta(days=365), 365))
        self._upload_inventory(inventory_book(today))
        self._assign_seller()
        self.client.force_login(self.admin)
        for page in ("dashboard", "sales_upload", "inventory_upload", "users",
                     "orders", "correction_requests"):
            response = self.client.get(reverse(page))
            self.assertEqual(response.status_code, 200, page)
        dashboard = self.client.get(reverse("dashboard"))
        self.assertContains(dashboard, "monthlyChart")
        self.assertContains(dashboard, "channelChart")
        self.assertContains(dashboard, "productsChart")
        self.assertContains(dashboard, "Alitas")
        template = self.client.get(reverse("inventory_template"))
        self.assertTrue(template.content.startswith(b"PK"))
        self.client.force_login(self.seller)
        for page in ("dashboard", "recommendations", "order_create",
                     "orders", "correction_requests"):
            response = self.client.get(reverse(page))
            self.assertEqual(response.status_code, 200, page)
        products = self.client.get(reverse("recommendations"))
        self.assertContains(products, "Alitas")
        self.assertNotContains(products, "Referencia histórica")
        self.assertNotContains(products, "WAPE")

    def test_confirmed_unit_cannot_be_changed_in_post(self):
        today = timezone.localdate()
        dataset = self._upload_sales(
            sales_book(today - timedelta(days=365), 365), verified=True, full_snapshot=True,
            date_semantics=SalesDataset.DateSemantics.DELIVERY,
        )
        self._confirm_sales_units(dataset)
        self._upload_inventory(inventory_book(today), units_confirmed=True)
        self._assign_seller()
        self.client.force_login(self.seller)
        response = self.client.get(reverse("recommendations"), {
            "client": "Cliente A", "required_date": (today + timedelta(days=3)).isoformat(),
        })
        card = {row["product"]: row for row in response.context["recommendations"]}["Alitas"]
        self.assertIsNotNone(card["suggested"])
        created = self.post_sale({
            "client": "Cliente A", "required_date": (today + timedelta(days=3)).isoformat(),
            "product": ["Alitas"], "quantity": [str(card["suggested"])],
            "unit": ["docenas"], "reason": ["manipulado"],
        })
        self.assertEqual(created.status_code, 200)
        self.assertFalse(OrderRequest.objects.exists())

    def test_correction_preserves_import_and_updates_effective_stock(self):
        today = date.today()
        self._upload_sales(sales_book(today - timedelta(days=365), 365))
        batch = self._upload_inventory(inventory_book(today), units_confirmed=True)
        self._assign_seller()
        self.client.force_login(self.seller)
        sent = self.client.post(reverse("correction_requests"), {
            "client": "Cliente A", "product": "Alitas",
            "claimed_stock": "9", "reason": "Conteo verificado hoy",
        })
        self.assertEqual(sent.status_code, 302)
        from .models import StockCorrectionRequest
        correction = StockCorrectionRequest.objects.get()
        self.client.force_login(self.admin)
        accepted = self.client.post(reverse("correction_requests"), {
            "correction_id": correction.pk, "action": "approve",
        })
        self.assertEqual(accepted.status_code, 302)
        self.assertEqual(InventoryItem.objects.get(
            batch=batch, client="Cliente A", product="Alitas"
        ).available, Decimal("2"))
        self.assertEqual(InventoryAdjustment.objects.get(correction=correction).available, Decimal("9"))
        from .services import recommendation_cards
        cards = recommendation_cards(SalesDataset.objects.get(active=True), today, "Cliente A", mode="vitali")
        self.assertEqual(next(card for card in cards if card["product"] == "Alitas")["available"],
                         Decimal("9"))

    def test_replaced_sales_file_blocks_review_of_old_request(self):
        first = date(2025, 1, 1)
        self._upload_sales(sales_book(first, 40))
        self._assign_seller()
        self.client.force_login(self.seller)
        created = self.post_sale({
            "client": "Cliente A", "required_date": date.today().isoformat(),
            "product": ["Alitas"], "quantity": ["5"], "unit": ["kg"],
            "reason": ["Pedido manual por rotación"],
        })
        self.assertEqual(created.status_code, 302)
        order = OrderRequest.objects.get(seller=self.seller)
        line = order.lines.get()
        self._upload_sales(sales_book(first, 40, multiplier=3))
        order.refresh_from_db()
        self.assertEqual(order.status, OrderRequest.Status.PENDING)
        self.client.force_login(self.admin)
        review = self.client.post(reverse("order_review", args=[order.pk]), {
            "line_id": line.pk, "decision": "approve", "approved_quantity": "5",
        })
        self.assertEqual(review.status_code, 403)
        revalidated = self.client.post(reverse("order_revalidate", args=[order.pk]), {
            "confirm_revalidation": "1",
        })
        self.assertEqual(revalidated.status_code, 302)
        order.refresh_from_db()
        self.assertEqual(order.revalidated_against, SalesDataset.objects.get(active=True))
        review = self.client.post(reverse("order_review", args=[order.pk]), {
            "line_id": line.pk, "decision": "approve", "approved_quantity": "5",
        })
        self.assertEqual(review.status_code, 302)
        line.refresh_from_db()
        self.assertEqual(line.status, OrderLine.Status.APPROVED)



    def test_admin_can_reset_seller_password_and_toggle_access(self):
        self.client.force_login(self.admin)
        page = self.client.get(reverse("users"))
        self.assertContains(page, "Restablecer contraseñas")
        changed = self.client.post(reverse("users"), {
            "action": "reset_password", "user_id": self.seller.pk,
            "password": "NuevaClaveDelVendedor2026",
        })
        self.assertEqual(changed.status_code, 302)
        self.seller.refresh_from_db()
        self.assertTrue(self.seller.check_password("NuevaClaveDelVendedor2026"))
        self.assertFalse(self.seller.check_password("ClaveDelVendedor123"))
        disabled = self.client.post(reverse("users"), {
            "action": "toggle", "user_id": self.seller.pk,
        })
        self.assertEqual(disabled.status_code, 302)
        self.seller.refresh_from_db()
        self.assertFalse(self.seller.is_active)
        self.assertFalse(self.client.login(
            username=self.seller.username, password="NuevaClaveDelVendedor2026"
        ))

    def test_form_can_submit_one_selected_product_with_other_products_empty(self):
        self._upload_sales(sales_book(date(2025, 1, 1), 10))
        self._assign_seller()
        self.client.force_login(self.seller)
        response = self.post_sale({
            "client": "Cliente A", "required_date": date.today().isoformat(),
            "product": ["Alitas", "Chorizo", ""],
            "quantity": ["4", "", ""],
            "unit": ["kg", "", ""],
            "reason": ["Pedido manual", "", ""],
        })
        self.assertEqual(response.status_code, 302)
        order = OrderRequest.objects.get(seller=self.seller)
        self.assertEqual(list(order.lines.values_list("product", flat=True)), ["Alitas"])
        self.assertContains(self.client.get(reverse("order_detail", args=[order.pk])), "Venta #")
        self.client.force_login(self.admin)
        admin_detail = self.client.get(reverse("order_detail", args=[order.pk]))
        self.assertContains(admin_detail, "Guardar")
        self.assertContains(admin_detail, 'name="approved_quantity" value="4.000"')

    def test_partial_new_product_is_rejected_instead_of_silently_dropped(self):
        self._upload_sales(sales_book(date(2025, 1, 1), 10))
        self._assign_seller()
        self.client.force_login(self.seller)
        response = self.post_sale({
            "client": "Cliente A", "required_date": date.today().isoformat(),
            "product": ["Alitas", ""], "quantity": ["4", "2"],
            "unit": ["kg", "kg"], "reason": ["Conteo manual", ""],
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Complete producto y cantidad")
        self.assertFalse(OrderRequest.objects.exists())


class LegacyMigrationTests(TestCase):
    def test_old_accounts_import_read_only_and_passwords_work(self):
        from hashlib import pbkdf2_hmac
        from pathlib import Path
        from django.contrib.auth.hashers import check_password
        from django.core.management import call_command
        import os
        import sqlite3

        with TemporaryDirectory() as folder:
            path = Path(folder) / "legacy.sqlite3"
            salt = os.urandom(16)
            password = "ClaveAnteriorFirme123"
            digest = pbkdf2_hmac("sha256", password.encode(), salt, 600_000)
            from contextlib import closing
            with closing(sqlite3.connect(path)) as db:
                db.execute("CREATE TABLE users(id INTEGER, username TEXT, salt BLOB, password_hash BLOB, role TEXT, active INTEGER)")
                db.execute("CREATE TABLE assignments(user_id INTEGER, client TEXT)")
                db.execute("INSERT INTO users VALUES(1,?,?,?,?,1)",
                           ("jefe", salt, digest, "admin"))
                db.execute("INSERT INTO assignments VALUES(1,'Cliente A')")
                db.commit()
            before = path.read_bytes()
            call_command("import_legacy_local", path=path, verbosity=0)
            account = get_user_model().objects.get(username="jefe")
            self.assertTrue(account.is_staff)
            self.assertTrue(check_password(password, account.password))
            self.assertTrue(Assignment.objects.filter(user=account, client="Cliente A").exists())
            self.assertEqual(path.read_bytes(), before)
