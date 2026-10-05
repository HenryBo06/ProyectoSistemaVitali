"""Both interfaces present the same operational DTO and keep finance administrative."""
from datetime import date, datetime, timedelta, timezone as utc_timezone
from decimal import Decimal
from importlib.util import find_spec
from pathlib import Path
from unittest import skipUnless
from unittest.mock import patch

from django.http import HttpResponse
from django.core.exceptions import PermissionDenied
from django.template.loader import render_to_string
from django.test import RequestFactory, TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .models import (Assignment, InventoryAdjustment, InventoryBatch, InventoryItem, OdooCustomerMapping,
                     OdooOrderLink, OdooProductMapping, OrderLine, OrderRequest, StockCorrectionRequest)
from .services import import_sales
from .streamlit_bridge import screen_context, sign_in
from .tests import LocalCase, sales_book


@override_settings(SMARTORDER_ODOO_CONFIG=None)
class OperationalUITests(LocalCase, TransactionTestCase):
    def setUp(self):
        super().setUp()
        dataset = import_sales(sales_book(date.today() - timedelta(days=30), 30),
                               "fuente_privada.xlsx", self.admin, False, True)
        Assignment.objects.create(user=self.seller, client="Cliente A")
        self.order = OrderRequest.objects.create(seller=self.seller, client="Cliente A",
            dataset=dataset, required_date=date.today(), sale_confirmed=True)
        OrderLine.objects.create(order=self.order, product="Alitas", unit="kg", requested_qty=5)
        self.operation = {
            "enabled": True, "synced": True, "stale": True, "status": "En producción", "last_synced": timezone.now(),
            "error": "ERROR_PRIVADO_CONEXION", "sale_name": "S-LAB-001",
            "can_sync": True, "can_refresh": True, "can_authorize": True, "can_cancel": True,
            "lines": [{"key": "1", "product": "Alitas", "unit": "kg", "ordered": 5,
                       "approved": 3, "reserved": None, "produced": 1, "delivered": 1,
                       "returned": 0, "scrapped": 0, "stage": "En producción", "net_delivered": 1, "outstanding": 4,
                       "last_delivery_at": datetime(2026, 10, 3, 12, 30, tzinfo=utc_timezone.utc)}],
            "admin_finance": {"balance": Decimal("4"), "currency": "USD", "invoices": [
                {"name": "FACTURA_PRIVADA_001", "state": "Contabilizada", "total": 20,
                 "residual": 4, "currency": "USD", "type_label": "Factura",
                 "state_label": "Contabilizada", "payment_state_label": "Pago parcial"}]},
        }

    def test_django_panel_seller_privacy_and_admin_controls(self):
        request = RequestFactory().get("/")
        request.user = self.seller
        seller = render_to_string("operations/odoo_order_panel.html", {
            "request": request, "role": "vendedor", "order": self.order, "odoo": self.operation})
        self.assertIn("En producción", seller)
        self.assertIn("Por confirmar", seller)
        self.assertIn("Se conservan los últimos resultados registrados", seller)
        self.assertIn("entrega neta 1 kg", seller)
        self.assertIn("Pendiente 4 kg", seller)
        self.assertIn("03/10/2026 06:30", seller)
        for forbidden in ("FACTURA_PRIVADA_001", "ERROR_PRIVADO_CONEXION", "Facturación y saldo",
                          "Sincronizar venta con Odoo", "Cancelar operación en Odoo"):
            self.assertNotIn(forbidden, seller)
        request.user = self.admin
        admin = render_to_string("operations/odoo_order_panel.html", {
            "request": request, "role": "admin", "order": self.order, "odoo": self.operation})
        for expected in ("FACTURA_PRIVADA_001", "ERROR_PRIVADO_CONEXION", "confirm_action", "Pago parcial",
                         "Sincronizar venta con Odoo", "Actualizar avance de Odoo",
                         "Enviar cantidades autorizadas a fabricar", "Cancelar operación en Odoo"):
            self.assertIn(expected, admin)

    @skipUnless(find_spec("streamlit"), "Install requirements-streamlit.txt for native UI checks")
    def test_delivery_default_and_explicit_today_match_both_interfaces(self):
        from streamlit.testing.v1 import AppTest
        from .services import default_delivery_date

        today = date(2026, 10, 3)
        tomorrow = today + timedelta(days=1)
        products = tuple(f"Producto LAB-{index:03d}" for index in range(1, 9))
        first = date(2025, 5, 1)
        dataset = import_sales(sales_book(first, (today - first).days + 1, products=products),
            "fuente_privada_fecha.xlsx", self.admin, verified=True, full_snapshot_confirmed=True)
        batch = InventoryBatch.objects.create(filename="stock_privado.xlsx", digest="fecha-stock",
            uploaded_by=self.admin, active=True, unit_match_confirmed=True)
        InventoryItem.objects.bulk_create([InventoryItem(batch=batch, client="Cliente A", product=product,
            available=0, observed_on=today, unit="kg") for product in products])
        self.client.force_login(self.seller)
        key = sign_in(self.seller.username, "ClaveDelVendedor123")
        reason = "La fecha de entrega está fuera del rango de sugerencias"
        with patch("django.utils.timezone.localdate", return_value=today):
            self.assertEqual(default_delivery_date(None), today)
            with patch.object(dataset, "last_date", today + timedelta(days=500)):
                self.assertEqual(default_delivery_date(dataset), today + timedelta(days=365))
            for name in ("recommendations", "order_create"):
                response = self.client.get(reverse(name))
                shared = screen_context(name, key)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.context["required_date"], tomorrow)
                self.assertEqual(shared["required_date"], tomorrow)
                self.assertEqual(shared["min_date"], today)
                self.assertEqual(shared["max_date"], today + timedelta(days=365))
                self.assertEqual(len(shared["recommendations"]), 8)
                self.assertTrue(all(card["suggested"] is not None for card in shared["recommendations"]))
                for card in shared["recommendations"]:
                    self.assertNotIn("forecast", card)
                    self.assertNotIn("reference_qty", card)
                    self.assertNotIn("sales_usd_display", card)
                explicit = {"required_date": today.isoformat()}
                response = self.client.get(reverse(name), explicit)
                shared = screen_context(name, key, explicit)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(shared["required_date"], today)
                self.assertTrue(all(card["suggested"] is None and reason in card["explanation"]
                                    for card in shared["recommendations"]))
                self.assertContains(response, reason)
                for forbidden in (dataset.filename, "Ventas conocidas", "año anterior", "Fuente y cobertura"):
                    self.assertNotContains(response, forbidden)
            app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "streamlit_app.py"), default_timeout=30)
            app.session_state["session_key"] = key
            app.session_state["workspace"] = "Productos y pedidos"
            app.run()
            self.assertFalse(app.exception)
            self.assertEqual(app.date_input[0].value, tomorrow)
            self.assertEqual(sum(metric.label == "Cantidad sugerida" for metric in app.metric), 8)
            app.date_input[0].set_value(today).run()
            self.assertFalse(app.exception)
            self.assertEqual(app.date_input[0].value, today)
            self.assertEqual(sum(metric.label == "Cantidad para solicitar" for metric in app.metric), 8)
            visible = " ".join(item.value for item in [*app.caption, *app.markdown, *app.warning])
            self.assertIn(reason, visible)
            for forbidden in (dataset.filename, "Ventas conocidas", "año anterior", "Fuente y cobertura"):
                self.assertNotIn(forbidden, visible)
            app.multiselect[0].set_value([products[0]]).run()
            next(field for field in app.text_input if field.label == "Cantidad solicitada").set_value("2")
            next(field for field in app.text_input if field.label == "Motivo de cantidad manual o ajuste").set_value("Entrega acordada para hoy")
            next(button for button in app.button if button.label == "Confirmar venta").click().run()
            self.assertFalse(app.exception)
            saved = OrderRequest.objects.latest("pk")
            self.assertEqual(saved.required_date, today)
            self.assertTrue(saved.sale_confirmed)
            self.assertEqual(saved.lines.get().requested_qty, Decimal("2"))

    @skipUnless(find_spec("streamlit"), "Install requirements-streamlit.txt for native UI checks")
    def test_native_panel_privacy_and_confirmed_admin_action(self):
        from streamlit.testing.v1 import AppTest

        def context_with_operation(name, key, params=None, **route):
            result = screen_context(name, key, params, **route)
            if name == "order_detail":
                result["odoo"] = self.operation
            return result

        app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "streamlit_app.py"), default_timeout=30)
        seller_key = sign_in(self.seller.username, "ClaveDelVendedor123")
        app.session_state["session_key"] = seller_key
        app.session_state["workspace"] = "Pedidos y producción"
        with patch("operations.streamlit_bridge.screen_context", side_effect=context_with_operation):
            app.run()
            self.assertFalse(app.exception)
            self.assertEqual(app.dataframe[1].value.iloc[0]["Reservado"], "Por confirmar")
            self.assertTrue(any("Se conservan los últimos resultados registrados" in item.value for item in app.warning))
            self.assertNotIn("Resultados operativos", app.radio[0].options)
            self.assertNotIn("Conexión y catálogos de Odoo", app.radio[0].options)
            visible = " ".join(item.value for item in [*app.markdown, *app.warning, *app.caption])
            self.assertNotIn("FACTURA_PRIVADA_001", visible)
            self.assertNotIn("ERROR_PRIVADO_CONEXION", visible)
            self.assertFalse(any("Odoo" in button.label for button in app.button))
            admin_key = sign_in(self.admin.username, "ClaveAdministrativa123")
            app.session_state["session_key"] = admin_key
            app.run()
            self.assertFalse(app.exception)
            self.assertTrue(any(button.label == "Sincronizar venta con Odoo" for button in app.button))
            self.assertEqual(app.dataframe[2].value.iloc[0]["Estado de pago"], "Pago parcial")
            with patch("operations.streamlit_bridge.call_screen", return_value=(HttpResponse(status=302), [], admin_key)) as post:
                next(button for button in app.button if button.label == "Enviar cantidades autorizadas a fabricar").click().run()
                self.assertFalse(post.called)
                self.assertTrue(app.error)
                next(item for item in app.checkbox if item.label.startswith("Revisé las cantidades aprobadas")).check()
                next(button for button in app.button if button.label == "Enviar cantidades autorizadas a fabricar").click().run()
                self.assertFalse(app.exception)
                self.assertEqual(post.call_args.args[0], "order_odoo")
                self.assertEqual(post.call_args.kwargs["order_id"], self.order.pk)
                self.assertEqual(post.call_args.kwargs["data"], {"action": "authorize", "confirm_action": "1"})
            self.operation["enabled"] = False
            for key in ("can_sync", "can_refresh", "can_authorize", "can_cancel"):
                self.operation[key] = False
            app.run()
            self.assertFalse(app.exception)
            self.assertTrue(any("últimos resultados registrados" in item.value for item in app.info))
            self.assertFalse(any("Odoo" in button.label for button in app.button))
            self.assertEqual(app.dataframe[1].value.iloc[0]["Reservado"], "Por confirmar")

    @skipUnless(find_spec("streamlit"), "Install requirements-streamlit.txt for native UI checks")
    def test_settings_and_reports_match_in_both_interfaces_without_credentials(self):
        from streamlit.testing.v1 import AppTest

        config = {"enabled": True, "url": "http://127.0.0.1:8079", "database": "vitali_lab",
            "key_configured": True, "connection_status": "Conexión comprobada",
            "products": [{"product": "Alitas", "sku": "VIT-LAB-001", "unit": "uom.product_uom_kgm", "source_unit": "kg"}],
            "customers": [{"client": "Cliente A", "code": "VIT-CLIENT-001"}],
            "product_options": ["Alitas"], "client_options": ["Cliente A"], "api_key": "NO_MOSTRAR_CREDENCIAL"}
        updated = datetime(2026, 10, 3, 12, 30, tzinfo=utc_timezone.utc)
        report = {"notice": "Laboratorio con datos simulados", "from_date": None, "to_date": None,
            "totals": [{"unit": "kg", "ordered": 5, "delivered": 1, "pending": 4, "scrapped": 0, "fulfillment_percent": None}],
            "rows": [{"order_id": self.order.pk, "client": "Cliente A", **self.operation["lines"][0],
                      "pending": 4, "required_date": date.today(), "last_synced": updated,
                      "on_time": None, "margin": None}]}
        request = RequestFactory().get("/")
        request.user = self.admin
        for template, data in (("odoo_settings", config), ("operational_reports", report)):
            html = render_to_string(f"operations/{template}.html", {"request": request, "role": "admin", **data})
            self.assertNotIn("NO_MOSTRAR_CREDENCIAL", html)
            self.assertIn("VIT-LAB-001" if template == "odoo_settings" else "Laboratorio con datos simulados", html)
            if template == "operational_reports":
                self.assertIn("03/10/2026 06:30", html)
                self.assertNotIn("Por confirmar %", html)
        def fake_context(name, key, params=None, **route):
            if name == "odoo_settings":
                return config
            if name == "operational_reports":
                return report
            return screen_context(name, key, params, **route)
        app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "streamlit_app.py"), default_timeout=30)
        key = sign_in(self.admin.username, "ClaveAdministrativa123")
        app.session_state["session_key"] = key
        app.session_state["workspace"] = "Conexión y catálogos de Odoo"
        with patch("operations.streamlit_bridge.screen_context", side_effect=fake_context):
            app.run()
            self.assertFalse(app.exception)
            self.assertFalse(any("NO_MOSTRAR_CREDENCIAL" in item.value for item in app.markdown))
            for label, value in (("Producto SmartOrder", "Alitas"), ("Código de producto en Odoo", "VIT-LAB-001"),
                                 ("Unidad de la fuente", "kg"), ("Unidad de Odoo", "uom.product_uom_kgm")):
                next(field for field in app.text_input if field.label == label).set_value(value)
            with patch("operations.streamlit_bridge.call_screen", return_value=(HttpResponse(status=302), [], key)) as post:
                next(button for button in app.button if button.label == "Guardar relación de producto").click().run()
                self.assertEqual(post.call_args.kwargs["data"], {"action": "product", "product": "Alitas",
                    "sku": "VIT-LAB-001", "unit": "uom.product_uom_kgm", "source_unit": "kg"})
            app.radio[0].set_value("Resultados operativos").run()
            self.assertFalse(app.exception)
            self.assertTrue(any("Laboratorio con datos simulados" in item.value for item in app.info))
            self.assertEqual(len(app.dataframe), 3)
            self.assertEqual(app.dataframe[0].value.iloc[0]["Cumplimiento %"], "Por confirmar")
            self.assertEqual(app.dataframe[1].value.iloc[0]["Reservado"], "Por confirmar")
            self.assertEqual(app.dataframe[1].value.iloc[0]["Actualizado"], "03/10/2026 06:30")
            next(button for button in app.button if button.label == "Aplicar período").click().run()
            self.assertFalse(app.exception)
            self.assertEqual(app.session_state["ops_report_filters"], {})
            next(field for field in app.date_input if field.label == "Entrega desde").set_value(date(2026, 10, 4))
            next(field for field in app.date_input if field.label == "Entrega hasta").set_value(date(2026, 10, 3))
            next(button for button in app.button if button.label == "Aplicar período").click().run()
            self.assertTrue(any("fecha inicial" in item.value for item in app.error))
            self.assertEqual(app.session_state["ops_report_filters"], {})

    def test_operational_admin_endpoints_deny_seller(self):
        self.client.force_login(self.seller)
        for endpoint in ("odoo_settings", "operational_reports"):
            self.assertEqual(self.client.get(reverse(endpoint)).status_code, 403)
        with patch("operations.views.odoo_service.synchronize") as remote:
            for action in ("sync", "refresh", "authorize", "cancel"):
                self.assertEqual(self.client.post(reverse("order_odoo", kwargs={"order_id": self.order.pk}),
                    {"action": action, "confirm_action": "1"}).status_code, 403)
            for action in ("check", "product", "customer"):
                self.assertEqual(self.client.post(reverse("odoo_settings"), {"action": action}).status_code, 403)
            remote.assert_not_called()

    @skipUnless(find_spec("streamlit"), "Install requirements-streamlit.txt for native UI checks")
    def test_comparison_and_inventory_deltas_share_history_filters_units_and_privacy(self):
        from streamlit.testing.v1 import AppTest

        today = date.today()
        batch = InventoryBatch.objects.create(digest="a" * 64, filename="INVENTARIO_PRIVADO.xlsx",
            file="inventory/source.xlsx", uploaded_by=self.admin)
        new_batch = InventoryBatch.objects.create(digest="b" * 64, filename="NUEVA_FUENTE_PRIVADA.xlsx",
            file="inventory/new.xlsx", uploaded_by=self.admin)
        for source, product, unit, qty in ((batch, "Alitas", "kg", 5),
                                          (batch, "Empaque", "unid", 10), (new_batch, "Alitas", "kg", 20)):
            InventoryItem.objects.create(batch=source, client="Cliente A", product=product,
                unit=unit, available=qty, observed_on=today)
        changes = []
        for source, product, unit, qty, day in ((batch, "Alitas", "kg", 3, today - timedelta(days=1)),
                (batch, "Alitas", "kg", 4, today), (batch, "Empaque", "unid", 7, today),
                (new_batch, "Alitas", "kg", 19, today)):
            correction = StockCorrectionRequest.objects.create(seller=self.seller, client="Cliente A",
                product=product, unit=unit, proposed_available=qty, observed_on=day,
                reason="MOTIVO_PRIVADO_RECUENTO", status="approved", reviewer=self.admin)
            changes.append(InventoryAdjustment.objects.create(batch=source, correction=correction,
                client="Cliente A", product=product, available=qty, observed_on=day, approved_by=self.admin))
        line = self.order.lines.get()
        line.suggested_qty, line.approved_qty, line.method = 2, 3, "Promedio 28 días"
        line.save()
        self.order.inventory_batch = batch
        self.order.save()
        OdooOrderLink.objects.create(order=self.order, status="synced", last_synced=timezone.now(),
            snapshot={"lines": [{"key": str(line.pk), "ordered": 5, "produced": 1,
                                  "delivered": 2, "returned": 1}], "sale": {"name": "S-COMPARACION"}})
        params = {"from_date": today.isoformat(), "to_date": today.isoformat()}
        self.client.force_login(self.admin)
        local = self.client.get(reverse("operational_reports"), params)
        key = sign_in(self.admin.username, "ClaveAdministrativa123")
        shared = screen_context("operational_reports", key, params)
        for name in ("rows", "totals", "corrections", "correction_totals"):
            self.assertEqual(local.context_data[name], shared[name])
        comparison = shared["rows"][0]
        self.assertEqual((comparison["suggested"], comparison["approved"], comparison["produced"],
            comparison["production_difference"], comparison["net_delivered"]), (2, 3, 1, -1, 1))
        self.assertEqual(comparison["source_digest"], self.order.dataset.digest)
        self.assertEqual(comparison["inventory_source"], batch.filename)
        self.assertEqual([row["id"] for row in shared["corrections"]], [change.pk for change in changes[1:]])
        self.assertEqual([(row["previous"], row["difference"]) for row in shared["corrections"]],
                         [(3, 1), (10, -3), (20, -1)])
        by_unit = {row["unit"]: row for row in shared["correction_totals"]}
        self.assertEqual((by_unit["kg"]["count"], by_unit["kg"]["difference"], by_unit["unid"]["difference"]), (2, 0, -3))
        self.assertContains(local, "Diferencia de producción")
        self.assertContains(local, "MOTIVO_PRIVADO_RECUENTO")
        app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "streamlit_app.py"), default_timeout=30)
        app.session_state["session_key"], app.session_state["workspace"] = key, "Resultados operativos"
        app.session_state["ops_report_filters"] = params
        app.run()
        self.assertFalse(app.exception)
        self.assertEqual(app.dataframe[2].value.iloc[0]["Diferencia de producción"], "-1.000")
        self.assertEqual(list(app.dataframe[4].value["Diferencia"]), ["1.000", "-3.000", "-1.000"])
        self.client.force_login(self.seller)
        self.assertEqual(self.client.get(reverse("operational_reports"), params).status_code, 403)
        seller_key = sign_in(self.seller.username, "ClaveDelVendedor123")
        with self.assertRaises(PermissionDenied):
            screen_context("operational_reports", seller_key, params)
        app.session_state["session_key"], app.session_state["workspace"] = seller_key, "Pedidos y producción"
        app.run()
        self.assertFalse(app.exception)
        visible = " ".join(item.value for item in [*app.markdown, *app.caption, *app.info])
        for private in ("fuente_privada.xlsx", "INVENTARIO_PRIVADO.xlsx", "MOTIVO_PRIVADO_RECUENTO"):
            self.assertNotIn(private, visible)
        self.assertNotIn("Resultados operativos", app.radio[0].options)

    @skipUnless(find_spec("streamlit"), "Install requirements-streamlit.txt for native UI checks")
    def test_actual_shared_snapshot_context_has_no_seller_finance_or_private_source(self):
        from streamlit.testing.v1 import AppTest

        line = self.order.lines.get()
        snapshot_line = {**self.operation["lines"][0], "key": str(line.pk), "reserved": 2,
                         "last_delivery_at": self.operation['lines'][0]['last_delivery_at'].isoformat(),
                         "unit_cost": 0.8, "source": "FUENTE_INTERNA_NO_VISIBLE"}
        OdooOrderLink.objects.create(order=self.order, status="synced", last_synced=timezone.now(),
            last_error="ERROR_PRIVADO_CONEXION", snapshot={"sale": {"name": "S-LAB-REAL-DTO"},
                "lines": [snapshot_line], "invoices": self.operation["admin_finance"]["invoices"], "currency": "USD"})
        key = sign_in(self.seller.username, "ClaveDelVendedor123")
        shared = screen_context("order_detail", key, order_id=self.order.pk)["odoo"]
        self.assertNotIn("admin_finance", shared)
        self.assertNotIn("error", shared)
        self.assertNotIn("unit_cost", shared["lines"][0])
        self.assertNotIn("source", shared["lines"][0])
        self.client.force_login(self.seller)
        local = self.client.get(reverse("order_detail", args=[self.order.pk]))
        for forbidden in ("FACTURA_PRIVADA_001", "ERROR_PRIVADO_CONEXION", "FUENTE_INTERNA_NO_VISIBLE"):
            self.assertNotContains(local, forbidden)
        self.assertContains(local, "S-LAB-REAL-DTO")
        app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "streamlit_app.py"), default_timeout=30)
        app.session_state["session_key"] = key
        app.session_state["workspace"] = "Pedidos y producción"
        app.run()
        self.assertFalse(app.exception)
        visible = " ".join(item.value for item in [*app.markdown, *app.warning, *app.caption])
        self.assertIn("S-LAB-REAL-DTO", visible)
        for forbidden in ("FACTURA_PRIVADA_001", "ERROR_PRIVADO_CONEXION", "FUENTE_INTERNA_NO_VISIBLE"):
            self.assertNotIn(forbidden, visible)

    @skipUnless(find_spec("streamlit"), "Install requirements-streamlit.txt for native UI checks")
    def test_failed_snapshot_keeps_dated_results_and_native_cancel_uses_shared_route(self):
        from streamlit.testing.v1 import AppTest

        updated = datetime(2026, 10, 3, 12, 30, tzinfo=utc_timezone.utc)
        line = self.order.lines.get()
        OdooOrderLink.objects.create(order=self.order, status="failed", last_synced=updated,
            last_error="ERROR_PRIVADO_CONEXION", snapshot={"sale": {"name": "S-LAB-CONSERVADO", "state": "sale"},
                "lines": [{**self.operation["lines"][0], "key": str(line.pk), "authorized": False,
                           "last_delivery_at": self.operation['lines'][0]['last_delivery_at'].isoformat()}],
                "invoices": self.operation["admin_finance"]["invoices"], "currency": "USD"})
        app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "streamlit_app.py"), default_timeout=30)
        app.session_state["session_key"] = sign_in(self.seller.username, "ClaveDelVendedor123")
        app.session_state["workspace"] = "Pedidos y producción"
        with patch("operations.odoo.configuration", return_value={"enabled": True}), patch("operations.odoo.rpc") as rpc:
            self.client.force_login(self.seller)
            local = self.client.get(reverse("order_detail", args=[self.order.pk]))
            self.assertContains(local, "S-LAB-CONSERVADO")
            self.assertContains(local, "03/10/2026 06:30")
            self.assertContains(local, "Se conservan los últimos resultados registrados")
            self.assertNotContains(local, "FACTURA_PRIVADA_001")
            self.assertNotContains(local, "ERROR_PRIVADO_CONEXION")
            app.run()
            self.assertFalse(app.exception)
            self.assertTrue(any("03/10/2026 06:30" in item.value for item in app.caption))
            self.assertTrue(any("Se conservan los últimos resultados registrados" in item.value for item in app.warning))
            self.assertEqual(app.dataframe[1].value.iloc[0]["Reservado"], "Por confirmar")
            visible = " ".join(item.value for item in [*app.markdown, *app.warning, *app.caption])
            for private in ("FACTURA_PRIVADA_001", "ERROR_PRIVADO_CONEXION"):
                self.assertNotIn(private, visible)
            rpc.assert_not_called()
            app.session_state["session_key"] = sign_in(self.admin.username, "ClaveAdministrativa123")
            with patch("operations.views.odoo_service.synchronize") as synchronize:
                app.run()
                next(button for button in app.button if button.label == "Cancelar operación en Odoo").click().run()
                synchronize.assert_not_called()
                next(item for item in app.checkbox if item.label.startswith("Revisé la venta y sus operaciones")).check()
                next(button for button in app.button if button.label == "Cancelar operación en Odoo").click().run()
                self.assertFalse(app.exception)
                self.assertEqual(synchronize.call_args.args[0].pk, self.order.pk)
                self.assertEqual(synchronize.call_args.args[1], "cancel")
            self.order.refresh_from_db()
            self.assertEqual(self.order.status, OrderRequest.Status.CANCELLED)
            self.assertEqual(self.order.lines.get().status, OrderLine.Status.REJECTED)

    @skipUnless(find_spec("streamlit"), "Install requirements-streamlit.txt for native UI checks")
    def test_native_catalog_forms_save_through_shared_backend_and_keep_success_feedback(self):
        from streamlit.testing.v1 import AppTest

        app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "streamlit_app.py"), default_timeout=30)
        app.session_state["session_key"] = sign_in(self.admin.username, "ClaveAdministrativa123")
        app.session_state["workspace"] = "Conexión y catálogos de Odoo"
        with patch("operations.odoo.rpc", return_value={"version": "19.0"}) as rpc:
            app.run()
            self.assertFalse(app.exception)
            for label, value in (("Producto SmartOrder", "Alitas"), ("Código de producto en Odoo", "VIT-LAB-001"),
                                 ("Unidad de la fuente", "kg"), ("Unidad de Odoo", "uom.product_uom_kgm")):
                next(field for field in app.text_input if field.label == label).set_value(value)
            next(button for button in app.button if button.label == "Guardar relación de producto").click().run()
            self.assertFalse(app.exception)
            self.assertEqual(OdooProductMapping.objects.get(product="Alitas").sku, "VIT-LAB-001")
            self.assertTrue(any("Equivalencia guardada" in item.value for item in app.success))
            for label, value in (("Cliente SmartOrder", "Cliente A"), ("Código de cliente en Odoo", "VIT-CLIENT-001")):
                next(field for field in app.text_input if field.label == label).set_value(value)
            next(button for button in app.button if button.label == "Guardar relación de cliente").click().run()
            self.assertFalse(app.exception)
            self.assertEqual(OdooCustomerMapping.objects.get(client="Cliente A").code, "VIT-CLIENT-001")
            self.assertTrue(any("Equivalencia guardada" in item.value for item in app.success))
            rpc.assert_not_called()
            next(button for button in app.button if button.label == "Comprobar conexión").click().run()
            self.assertFalse(app.exception)
            rpc.assert_called_once_with("ping")
            self.assertTrue(any("Conexión comprobada" in item.value for item in app.success))
            app.session_state["action_notices"] = (self.admin.pk, True, [("success", "MENSAJE_PRIVADO_ADMIN")])
            app.session_state["session_key"] = sign_in(self.seller.username, "ClaveDelVendedor123")
            app.session_state["workspace"] = "Pedidos y producción"
            app.run()
            self.assertFalse(app.exception)
            self.assertFalse(any("MENSAJE_PRIVADO_ADMIN" in item.value for item in app.success))
            app.session_state["action_notices"] = (self.admin.pk, True, [("success", "MENSAJE_PRIVADO_ROL_ADMIN")])
            app.session_state["session_key"] = sign_in(self.admin.username, "ClaveAdministrativa123")
            self.admin.is_staff = False
            self.admin.save(update_fields=["is_staff"])
            app.session_state["workspace"] = "Mi trabajo"
            app.run()
            self.assertFalse(app.exception)
            self.assertFalse(any("MENSAJE_PRIVADO_ROL_ADMIN" in item.value for item in app.success))
