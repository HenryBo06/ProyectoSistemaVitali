"""The native interface must preserve the shared account and production boundaries."""

from datetime import date, timedelta
from pathlib import Path
from unittest import skipUnless
from importlib.util import find_spec

from django.core.exceptions import PermissionDenied
from django.test import TransactionTestCase, override_settings

from .models import Assignment, InventoryAdjustment, OrderLine, OrderRequest, SalesDataset, StockCorrectionRequest
from .services import import_inventory, import_sales
from .streamlit_bridge import call_screen, request_for, screen_context, sign_in
from .tests import LocalCase, inventory_book, sales_book


@override_settings(SMARTORDER_ODOO_CONFIG=None)
class StreamlitBridgeTests(LocalCase, TransactionTestCase):
    def setUp(self):
        super().setUp()
        self.dataset = import_sales(sales_book(date.today() - timedelta(days=30), 30),
                                    "ventas.xlsx", self.admin, False, True)
        Assignment.objects.create(user=self.seller, client="Cliente A")

    @override_settings(SMARTORDER_SETUP_CODE="setup-code-for-tests-only-32-characters")
    def test_remote_setup_is_available_only_with_the_configured_code(self):
        from django.contrib.auth import get_user_model

        Assignment.objects.all().delete()
        SalesDataset.objects.all().delete()
        get_user_model().objects.all().delete()
        with override_settings(SMARTORDER_SETUP_CODE="corto"):
            with self.assertRaises(PermissionDenied):
                call_screen("setup")
        response, _, _ = call_screen("setup")
        self.assertEqual(response.status_code, 200)
        response, notices, _ = call_screen("setup", data={
            "username": "jefe_vitali", "password": "NuevaClaveSegura#2026",
            "setup_code": "incorrecto",
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(notices[0][0], "error")
        self.assertFalse(get_user_model().objects.exists())
        response, _, _ = call_screen("setup", data={
            "username": "jefe_vitali", "password": "NuevaClaveSegura#2026",
            "setup_code": "setup-code-for-tests-only-32-characters",
        })
        self.assertEqual(response.status_code, 302)
        self.assertTrue(get_user_model().objects.get(username="jefe_vitali").is_staff)

    def test_seller_request_admin_review_and_exact_export_session(self):
        seller_key = sign_in(self.seller.username, "ClaveDelVendedor123")
        admin_key = sign_in(self.admin.username, "ClaveAdministrativa123")
        with self.assertRaises(PermissionDenied):
            screen_context("users", seller_key)
        with self.assertRaises(PermissionDenied):
            screen_context("dashboard", seller_key, {"client": "Otra cartera"})
        response, notices, seller_key = call_screen("order_create", seller_key, data={
            "client": "Cliente A", "required_date": date.today(), "product": ["Alitas"],
            "quantity": ["4"], "unit": ["kg"], "reason": ["Pedido confirmado con el cliente"],
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(notices[0][0], "success")
        order = OrderRequest.objects.get()
        line = order.lines.get()
        call_screen("order_review", admin_key, order_id=order.pk, data={
            "line_id": line.pk, "decision": "approve", "approved_quantity": "4", "review_note": "",
        })
        response, _, admin_key = call_screen("export_batch", admin_key, data={"line_ids": [line.pk], "action": "download"})
        self.assertTrue(response.content.startswith(b"PK"))
        self.assertEqual(request_for(admin_key).session["prepared_export_line_ids"], [line.pk])
        call_screen("export_batch", admin_key, data={"line_ids": [line.pk], "action": "confirm", "shared_confirm": True})
        line.refresh_from_db()
        self.assertEqual(line.status, OrderLine.Status.EXPORTED)

    @skipUnless(find_spec("streamlit"), "Install requirements-streamlit.txt for native UI checks")
    def test_seller_sees_production_answer_in_both_interfaces_without_admin_sources(self):
        from decimal import Decimal
        from django.test import Client
        from django.urls import reverse
        from streamlit.testing.v1 import AppTest

        seller_key = sign_in(self.seller.username, "ClaveDelVendedor123")
        admin_key = sign_in(self.admin.username, "ClaveAdministrativa123")
        call_screen("order_create", seller_key, data={
            "client": "Cliente A", "required_date": date.today(), "product": ["Alitas"],
            "quantity": ["4"], "unit": ["kg"], "reason": ["Cliente confirmó"],
        })
        order = OrderRequest.objects.get()
        line = order.lines.get()
        call_screen("order_review", admin_key, order_id=order.pk, data={
            "line_id": line.pk, "decision": "approve", "approved_quantity": "3",
            "review_note": "Entrega parcial acordada",
        })
        detail = screen_context("order_detail", seller_key, order_id=order.pk)
        self.assertEqual(detail["lines"][0]["approved_qty"], Decimal("3"))
        self.assertNotIn("method", detail["lines"][0])
        self.assertFalse(hasattr(detail["order"], "dataset"))
        self.client.force_login(self.seller)
        local = self.client.get(reverse("order_detail", args=[order.pk]))
        self.assertContains(local, "Cantidad autorizada para producción")
        self.assertContains(local, "Entrega parcial acordada")
        app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "streamlit_app.py"), default_timeout=30)
        app.session_state["session_key"] = seller_key
        app.session_state["workspace"] = "Pedidos y producción"
        app.run()
        self.assertFalse(app.exception)
        visible = " ".join(item.value for item in app.markdown)
        self.assertIn("Cantidad autorizada para producción: 3.000 kg", visible)
        self.assertIn("Respuesta de administración: Entrega parcial acordada", visible)
        self.assertNotIn("ventas.xlsx", visible)
        outsider = Client()
        other = self.seller.__class__.objects.create_user("otro_vendedor", password="OtraClaveFirme2026")
        outsider.force_login(other)
        self.assertEqual(outsider.get(reverse("order_detail", args=[order.pk])).status_code, 403)
        with self.assertRaises(PermissionDenied):
            screen_context("order_detail", sign_in(other.username, "OtraClaveFirme2026"), order_id=order.pk)

    def test_changed_password_invalidates_streamlit_session(self):
        key = sign_in(self.seller.username, "ClaveDelVendedor123")
        self.assertTrue(request_for(key).user.is_authenticated)
        self.seller.set_password("NuevaClaveSegura2026")
        self.seller.save()
        self.assertFalse(request_for(key).user.is_authenticated)
        with self.assertRaises(PermissionDenied):
            call_screen("setup", key, data={"username": "otro", "password": "UnaClaveMuySegura2026"})

    @skipUnless(find_spec("streamlit"), "Install requirements-streamlit.txt for native UI checks")
    def test_native_future_sale_edit_cancel_and_admin_detail_match_local(self):
        from streamlit.testing.v1 import AppTest
        from django.urls import reverse

        import_inventory(inventory_book(date.today()), "stock_paridad.xlsx", self.admin, True)
        app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "streamlit_app.py"), default_timeout=30)
        app.session_state["session_key"] = sign_in(self.seller.username, "ClaveDelVendedor123")
        app.session_state["workspace"] = "Productos y pedidos"
        app.run()
        app.date_input[0].set_value(date.today() + timedelta(days=30)).run()
        app.multiselect[0].select("Alitas").run()
        for label, value in (("Cantidad solicitada", "5"), ("Precio unitario USD", "12.5"),
                             ("Descuento %", "10"), ("Motivo de cantidad manual o ajuste", "Entrega futura acordada"),
                             ("Condiciones comerciales", "30 días")):
            next(field for field in app.text_input if field.label == label).set_value(value)
        next(button for button in app.button if button.label == "Confirmar venta").click().run()
        self.assertFalse(app.exception)
        self.assertFalse(app.error)
        order = OrderRequest.objects.get()
        line = order.lines.get()
        self.assertEqual(str(line.future_quantity), "3.000")
        self.client.force_login(self.seller)
        local = self.client.get(reverse("order_detail", args=[order.pk]))
        self.assertContains(local, "30 días")
        self.assertContains(local, "Compromiso futuro")
        app.radio[0].set_value("Pedidos y producción").run()
        next(field for field in app.text_input if field.label == "Cantidad").set_value("6")
        next(field for field in app.text_input if field.label == "Motivo del cambio").set_value("Cliente ajustó cantidad")
        next(button for button in app.button if button.label == "Guardar cambio").click().run()
        self.assertFalse(app.exception)
        line.refresh_from_db()
        self.assertEqual(str(line.requested_qty), "6.000")
        self.assertIsNone(line.future_quantity)
        with override_settings(SMARTORDER_SELLER_EDIT=False, SMARTORDER_SELLER_CANCEL=False):
            app.run()
            self.assertFalse(any(button.label in {"Guardar cambio", "Cancelar venta"} for button in app.button))
        app.session_state["session_key"] = sign_in(self.admin.username, "ClaveAdministrativa123")
        app.run()
        self.assertFalse(app.exception)
        self.assertTrue(any("Ventas usadas: ventas.xlsx" in item.value for item in app.caption))
        self.assertTrue(any("Precio unitario: USD 12.5000" in item.value for item in app.markdown))
        self.assertTrue(any("Condiciones comerciales: 30 días" in item.value for item in app.markdown))
        app.session_state["session_key"] = sign_in(self.seller.username, "ClaveDelVendedor123")
        app.run()
        next(field for field in app.text_input if field.label == "Motivo de cancelación").set_value("Cliente canceló")
        next(button for button in app.button if button.label == "Cancelar venta").click().run()
        self.assertFalse(app.exception)
        order.refresh_from_db()
        self.assertEqual(order.status, OrderRequest.Status.CANCELLED)

    @skipUnless(find_spec("streamlit"), "Install requirements-streamlit.txt for native UI checks")
    def test_native_inventory_correction_and_import_details(self):
        from streamlit.testing.v1 import AppTest
        from django.urls import reverse

        import_inventory(inventory_book(date.today()), "inventario_paridad.xlsx", self.admin, True)
        app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "streamlit_app.py"), default_timeout=30)
        app.session_state["session_key"] = sign_in(self.seller.username, "ClaveDelVendedor123")
        app.session_state["workspace"] = "Correcciones de inventario"
        app.run()
        next(field for field in app.text_input if field.label == "Existencia propuesta").set_value("8")
        app.text_area[0].set_value("Conteo de hoy")
        next(button for button in app.button if button.label == "Solicitar corrección").click().run()
        self.assertFalse(app.exception)
        correction = StockCorrectionRequest.objects.get()
        app.session_state["session_key"] = sign_in(self.admin.username, "ClaveAdministrativa123")
        app.run()
        next(button for button in app.button if button.label == "Aprobar corrección").click().run()
        self.assertFalse(app.exception)
        self.assertEqual(str(InventoryAdjustment.objects.get(correction=correction).available), "8.000")
        app.radio[0].set_value("Datos en Excel").run()
        self.assertFalse(app.exception)
        self.assertTrue(any("inventario_paridad.xlsx" in item.value for item in app.info))
        self.assertTrue(any("2 existencias" in item.value for item in app.info))
        self.assertTrue(any("Equivalencia de unidad confirmada" in item.value for item in app.caption))
        self.client.force_login(self.admin)
        self.assertContains(self.client.get(reverse("inventory_upload")), "inventario_paridad.xlsx")
        self.assertContains(self.client.get(reverse("sales_upload")), f"{self.dataset.row_count} filas")

    @skipUnless(find_spec("streamlit"), "Install requirements-streamlit.txt for native UI checks")
    def test_native_login_role_navigation_products_and_empty_filters(self):
        from streamlit.testing.v1 import AppTest

        app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "streamlit_app.py"), default_timeout=30).run()
        self.assertFalse(app.exception)
        app.text_input[0].set_value(self.seller.username)
        app.text_input[1].set_value("incorrecta")
        app.button[0].click().run()
        self.assertTrue(app.error)
        app.text_input[0].set_value(self.seller.username)
        app.text_input[1].set_value("ClaveDelVendedor123")
        app.button[0].click().run()
        self.assertFalse(app.exception)
        self.assertTrue(any(title.value == "Mi trabajo" for title in app.title))
        self.assertNotIn("Datos en Excel", app.radio[0].options)
        self.assertFalse(app.date_input)
        self.assertFalse(any("Ventas del período" in metric.label for metric in app.metric))
        app.radio[0].set_value("Productos y pedidos").run()
        self.assertFalse(app.exception)
        self.assertTrue(any(metric.value == "Decisión manual" for metric in app.metric))
        self.assertTrue(any(button.label == "Confirmar venta" for button in app.button))
        app.multiselect[0].select("Alitas").run()
        next(field for field in app.text_input if field.label == "Cantidad solicitada").set_value("4")
        next(field for field in app.text_input if field.label == "Unidad").set_value("kg")
        next(button for button in app.button if button.label == "Confirmar venta").click().run()
        self.assertTrue(app.error)  # a manual amount needs an explanation
        self.assertEqual(next(field for field in app.text_input if field.label == "Cantidad solicitada").value, "4")
        next(field for field in app.text_input if field.label == "Motivo de cantidad manual o ajuste").set_value("Cliente confirmó el pedido")
        next(button for button in app.button if button.label == "Confirmar venta").click().run()
        self.assertFalse(app.exception)
        self.assertEqual(OrderRequest.objects.count(), 1)
        app.radio[0].set_value("Pedidos y producción").run()
        self.assertFalse(app.exception)
        app.radio[0].set_value("Correcciones de inventario").run()
        self.assertFalse(app.exception)
        next(button for button in app.button if button.label == "Cerrar sesión").click().run()
        self.assertFalse(app.exception)
        self.assertEqual(app.subheader[0].value, "Entrar a tu cuenta")
        app.text_input[0].set_value(self.admin.username)
        app.text_input[1].set_value("ClaveAdministrativa123")
        app.button[0].click().run()
        self.assertIn("Datos en Excel", app.radio[0].options)
        app.radio[0].set_value("Pedidos y producción").run()
        next(button for button in app.button if button.label == "Autorizar producción").click().run()
        self.assertFalse(app.exception)
        self.assertEqual(OrderLine.objects.get().status, OrderLine.Status.APPROVED)
        app.multiselect[0].select(OrderLine.objects.get().pk).run()
        next(button for button in app.button if button.label == "Preparar XLSX").click().run()
        self.assertFalse(app.exception)
        self.assertTrue(app.session_state["export_file"][1].startswith(b"PK"))
        for page in ("Datos en Excel", "Usuarios y cartera", "Productos y pedidos", "Correcciones de inventario"):
            app.radio[0].set_value(page).run()
            self.assertFalse(app.exception, page)


class StreamlitSetupTests(TransactionTestCase):
    @override_settings(DEBUG=True)
    def test_first_admin_setup_requires_explicit_local_permission(self):
        with self.assertRaises(PermissionDenied):
            call_screen("setup", data={"username": "inicial", "password": "ClaveInicialFirme2026"})
        response, _, key = call_screen("setup", local_setup=True, data={
            "username": "inicial", "password": "ClaveInicialFirme2026",
        })
        self.assertEqual(response.status_code, 302)
        self.assertTrue(request_for(key).user.is_staff)
