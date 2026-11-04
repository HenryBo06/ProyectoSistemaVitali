"""Comprobar que la orientación de IA es exclusiva para vendedores."""
from datetime import timedelta
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from .models import Assignment
from .services import import_sales, import_inventory
from .streamlit_bridge import screen_context, sign_in
from .tests import LocalCase, sales_book, inventory_book


@override_settings(SMARTORDER_ODOO_CONFIG=None)
class SellerActionsRoleTests(LocalCase, TestCase):
    def setUp(self):
        super().setUp()
        import_sales(sales_book(timezone.localdate() - timedelta(days=30), 30),
                     "sintetico-lab.xlsx", self.admin, False, True)
        import_inventory(inventory_book(timezone.localdate()),
                         "inventario-lab.xlsx", self.admin, True)
        Assignment.objects.create(user=self.seller, client="Cliente A")

    def test_unassigned_seller_sees_actionable_empty_state_not_missing_sales(self):
        from .models import SalesDataset
        Assignment.objects.filter(user=self.seller).delete()
        self.client.force_login(self.seller)
        response = self.client.get(reverse("recommendations"))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context_data["clients"])
        self.assertTrue(SalesDataset.objects.filter(active=True).exists())
        self.assertContains(response, "Aún no tienes clientes asignados")
        self.assertContains(response, "Sin clientes asignados")
        self.assertNotContains(response, "Este cliente no tiene ventas registradas")
        self.assertNotContains(response, "Crear venta")
        self.assertContains(response, 'type="submit" disabled')

    def test_admin_can_assign_unowned_clients_without_taking_other_seller_portfolio(self):
        from django.contrib.auth import get_user_model
        from .models import SalesDataset, SalesLine
        other = get_user_model().objects.create_user(
            username="otro_vendedor", password="ClaveDemoVendedor234")
        Assignment.objects.filter(user=self.seller).delete()
        Assignment.objects.create(user=other, client="Cliente A")
        dataset = SalesDataset.objects.get(active=True)
        extra_line = SalesLine.objects.filter(dataset=dataset).first()
        extra_line.pk = None
        extra_line.client = "Cliente B"
        extra_line.save()
        self.client.force_login(self.admin)
        listing = self.client.get(reverse("users"))
        self.assertEqual(listing.status_code, 200)
        self.assertContains(listing, "cliente del histórico activo aún sin vendedor")
        response = self.client.post(reverse("users"), {
            "action": "assign_unassigned", "user_id": self.seller.pk,
        }, follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "asignados a")
        self.assertTrue(Assignment.objects.filter(user=self.seller, client="Cliente B").exists())
        self.assertTrue(Assignment.objects.filter(user=other, client="Cliente A").exists())
        self.assertEqual(Assignment.objects.filter(client="Cliente A").count(), 1)

    def test_seller_cannot_use_admin_fast_assignment(self):
        self.client.force_login(self.seller)
        response = self.client.post(reverse("users"), {
            "action": "assign_unassigned", "user_id": self.seller.pk,
        })
        self.assertEqual(response.status_code, 403)
        self.assertEqual(Assignment.objects.count(), 1)
        self.assertTrue(Assignment.objects.filter(user=self.seller, client="Cliente A").exists())

    def test_seller_has_specific_action_in_web_and_shared_streamlit_context(self):
        self.client.force_login(self.seller)
        web = self.client.get(reverse("recommendations"), {"client": "Cliente A", "mode": "sale"})
        self.assertEqual(web.status_code, 200)
        cards = web.context_data["recommendations"]
        self.assertTrue(cards)
        first = cards[0]
        self.assertIn(first["action_code"], ("offer_product", "explore_interest", "resolve_unit"))
        self.assertTrue(first["action_title"])
        self.assertIn("action_checks", first)
        self.assertContains(web, "Sugerencia de venta para este cliente")
        self.assertContains(web, "Acción sugerida")
        key = sign_in(self.seller.username, "ClaveDelVendedor123")
        shared = screen_context("recommendations", key, params={
            "client": "Cliente A", "mode": "sale",
        })["recommendations"]
        self.assertEqual(first["action_title"], shared[0]["action_title"])
        self.assertEqual(first["action_checks"], shared[0]["action_checks"])
        self.assertNotIn("sales_usd_display", first)

    def test_admin_does_not_get_seller_actions(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse("recommendations"))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Sugerencia de venta para este cliente")
        self.assertNotContains(response, "Acción sugerida")
        self.assertNotContains(response, "IA · Sugerencias de venta")
        for card in response.context_data["recommendations"]:
            self.assertNotIn("action_code", card)
            self.assertNotIn("action_title", card)
