"""Privacy and confirmed-sale boundaries shared by both interfaces."""
from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from .models import AccessEvent, Assignment, OrderLine, OrderRequest
from .services import import_inventory, import_sales
from .streamlit_bridge import request_for, screen_context, sign_in
from .tests import LocalCase, inventory_book, sales_book


@override_settings(SMARTORDER_ODOO_CONFIG=None)
class SalesRoleTests(LocalCase, TestCase):
    def setUp(self):
        super().setUp()
        self.dataset = import_sales(sales_book(date.today() - timedelta(days=30), 30),
                                    "privado.xlsx", self.admin, False, True)
        import_inventory(inventory_book(date.today()), "stock.xlsx", self.admin, True)
        Assignment.objects.create(user=self.seller, client="Cliente A")
        self.client.force_login(self.seller)

    def create_sale(self, **extra):
        payload = {"client": "Cliente A", "required_date": (date.today() + timedelta(days=30)).isoformat(),
                   "product": ["Alitas"], "quantity": ["5"], "unit": ["kg"],
                   "reason": ["Cliente confirmó"], "unit_price": ["12.5"],
                   "discount_percent": ["10"], "payment_terms": "30 días"}
        payload.update(extra)
        return self.client.post(reverse("order_create"), payload)

    def test_linked_production_cannot_exceed_confirmed_sale(self):
        from .models import OdooOrderLink
        self.create_sale()
        order = OrderRequest.objects.get()
        line = order.lines.get()
        OdooOrderLink.objects.create(order=order)
        self.client.force_login(self.admin)
        response = self.client.post(reverse('order_review', args=[order.pk]), {
            'line_id': line.pk, 'decision': 'approve', 'approved_quantity': '6', 'review_note': 'Cantidad adicional'})
        self.assertEqual(response.status_code, 302)
        line.refresh_from_db()
        self.assertEqual(line.status, OrderLine.Status.PENDING)
        self.assertIsNone(line.approved_qty)

    def test_seller_context_and_html_do_not_contain_historical_data(self):
        key = sign_in(self.seller.username, "ClaveDelVendedor123")
        for page in ("dashboard", "recommendations", "order_create"):
            response = self.client.get(reverse(page))
            self.assertEqual(response.status_code, 200)
            self.assertNotContains(response, "privado.xlsx")
            self.assertNotContains(response, "Ventas del histórico")
            context = screen_context(page, key)
            self.assertNotIn("chart_monthly", context)
            self.assertIsNone(context.get("dataset"))
            for card in context.get("recommendations", []):
                for field in ("reference_qty", "sales_usd_display", "incoming", "method", "forecast"):
                    self.assertNotIn(field, card)
        self.create_sale()
        detail = screen_context("order_detail", key, order_id=OrderRequest.objects.get().pk)
        self.assertFalse(hasattr(detail["order"], "dataset"))
        self.assertNotIn("reference_qty", detail["lines"][0])
        self.client.force_login(self.admin)
        self.assertContains(self.client.get(reverse("dashboard")), "privado.xlsx")

    def test_sale_is_confirmed_without_production_approval_and_can_repeat_week(self):
        self.assertEqual(self.create_sale().status_code, 302)
        order = OrderRequest.objects.get()
        self.assertTrue(order.sale_confirmed)
        self.assertEqual(order.payment_terms, "30 días")
        line = order.lines.get()
        self.assertEqual(line.status, OrderLine.Status.PENDING)
        self.assertIsNone(line.approved_qty)
        self.assertEqual(line.future_quantity, Decimal("3"))
        self.assertEqual(line.unit_price, Decimal("12.5"))
        self.assertEqual(line.discount_percent, Decimal("10"))
        self.assertFalse(line.coverages.exists())
        self.create_sale()
        self.assertEqual(OrderRequest.objects.count(), 2)

    def test_invalid_prices_and_discounts_never_create_sales(self):
        for fields in ({"unit_price": ["NaN"]}, {"unit_price": ["-1"]},
                       {"discount_percent": ["101"]}, {"discount_percent": ["1.001"]}):
            self.create_sale(**fields)
        self.assertFalse(OrderRequest.objects.exists())

    def test_reassignment_transfers_visibility_and_invalidates_sessions(self):
        self.create_sale()
        order = OrderRequest.objects.get()
        old_key = sign_in(self.seller.username, "ClaveDelVendedor123")
        other = get_user_model().objects.create_user("nuevo", password="ClaveNuevaFirme2026")
        new_key = sign_in(other.username, "ClaveNuevaFirme2026")
        self.client.force_login(self.admin)
        self.assertEqual(self.client.post(reverse("users"), {
            "action": "assign", "user_id": other.pk, "clients": ["Cliente A"]}).status_code, 302)
        self.assertFalse(request_for(old_key).user.is_authenticated)
        self.assertFalse(request_for(new_key).user.is_authenticated)
        self.client.force_login(self.seller)
        self.assertEqual(self.client.get(reverse("order_detail", args=[order.pk])).status_code, 403)
        self.client.force_login(other)
        self.assertEqual(self.client.get(reverse("order_detail", args=[order.pk])).status_code, 200)
        self.assertEqual(OrderRequest.objects.get().seller_id, self.seller.pk)
        self.assertTrue(AccessEvent.objects.filter(action="assign").exists())

    def test_edit_cancel_and_execution_boundary(self):
        self.create_sale()
        order = OrderRequest.objects.get()
        line = order.lines.get()
        url = reverse("sale_change", args=[order.pk])
        edit = {"action": "edit", "line_id": str(line.pk), "quantity": "6",
                "unit_price": "15", "discount_percent": "20", "change_reason": "Cliente ajustó"}
        self.client.post(url, edit)
        line.refresh_from_db()
        self.assertEqual(line.requested_qty, Decimal("6"))
        self.assertTrue(AccessEvent.objects.filter(action="sale_edit_before").exists())
        with override_settings(SMARTORDER_SELLER_CANCEL=False):
            self.assertEqual(self.client.post(url, {"action": "cancel", "change_reason": "Cambio"}).status_code, 403)
        line.status = OrderLine.Status.APPROVED
        line.save()
        self.client.post(url, {"action": "cancel", "change_reason": "Cambio"})
        order.refresh_from_db()
        self.assertNotEqual(order.status, OrderRequest.Status.CANCELLED)
        line.status = OrderLine.Status.PENDING
        line.save()
        self.client.post(url, {"action": "cancel", "change_reason": "Cambio"})
        order.refresh_from_db()
        self.assertEqual(order.status, OrderRequest.Status.CANCELLED)

    def test_role_change_closes_session_and_last_admin_is_preserved(self):
        key = sign_in(self.seller.username, "ClaveDelVendedor123")
        self.client.force_login(self.admin)
        self.client.post(reverse("users"), {"action": "role", "user_id": self.seller.pk, "role": "admin"})
        self.assertFalse(request_for(key).user.is_authenticated)
        self.assertFalse(Assignment.objects.filter(user=self.seller).exists())
        self.client.post(reverse("users"), {"action": "role", "user_id": self.seller.pk, "role": "vendedor"})
        self.client.post(reverse("users"), {"action": "role", "user_id": self.admin.pk, "role": "vendedor"})
        self.admin.refresh_from_db()
        self.assertTrue(self.admin.is_staff)

    def test_both_interfaces_hide_changes_disallowed_by_settings_or_partial_execution(self):
        self.create_sale()
        order = OrderRequest.objects.get()
        key = sign_in(self.seller.username, "ClaveDelVendedor123")
        url = reverse("order_detail", args=[order.pk])
        with override_settings(SMARTORDER_SELLER_EDIT=False, SMARTORDER_SELLER_CANCEL=False):
            response = self.client.get(url)
            self.assertNotContains(response, "Guardar cambio")
            self.assertNotContains(response, "Cancelar venta")
            native = screen_context("order_detail", key, order_id=order.pk)
            self.assertFalse(native["can_edit_sale"])
            self.assertFalse(native["can_cancel_sale"])
            self.client.force_login(self.admin)
            self.assertContains(self.client.get(url), "Guardar cambio")
            self.assertContains(self.client.get(url), "Cancelar venta")
        order.lines.update(status=OrderLine.Status.APPROVED)
        OrderLine.objects.create(order=order, product="Chorizo", unit="kg", requested_qty=1)
        # One authorized line already closes direct changes for the whole sale.
        response = self.client.get(url)
        self.assertNotContains(response, "Guardar cambio")
        self.assertNotContains(response, "Cancelar venta")
        native = screen_context("order_detail", key, order_id=order.pk)
        self.assertFalse(native["can_edit_sale"])
        self.assertFalse(native["can_cancel_sale"])
