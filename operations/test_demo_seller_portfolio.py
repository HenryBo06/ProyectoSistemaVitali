"""The demo seller seeder never invents users or steals clients."""
from datetime import timedelta
from io import StringIO

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .demo_catalog import LAB_FILE, seed_demo_seller_portfolio
from .models import AccessEvent, Assignment, SalesDataset, SalesLine
from .services import import_sales
from .tests import LocalCase, sales_book


@override_settings(SMARTORDER_ODOO_CONFIG=None)
class SellerDemoPortfolioTests(LocalCase, TestCase):
    def setUp(self):
        super().setUp()
        self.dataset = import_sales(
            sales_book(timezone.localdate() - timedelta(days=45), 45),
            LAB_FILE, self.admin, verified=False,
            provenance=SalesDataset.Provenance.UNKNOWN,
        )

    def add_client(self, name):
        source_line = SalesLine.objects.filter(dataset=self.dataset).first()
        source_line.pk = None
        source_line.client = name
        source_line.save()

    def test_admin_one_click_seeds_existing_seller_and_enables_inference(self):
        self.add_client("Cliente B LAB")
        initial_users = get_user_model().objects.count()
        initial_rows = SalesLine.objects.filter(dataset=self.dataset).count()
        self.client.force_login(self.admin)
        page = self.client.get(reverse("users"))
        self.assertContains(page, "Seeder de cartera para el vendedor (LAB)")
        response = self.client.post(reverse("users"), {
            "action": "seed_demo_seller", "user_id": str(self.seller.pk),
            "confirm_lab": "on",
        }, follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "2 clientes nuevos")
        self.assertEqual(
            set(Assignment.objects.filter(user=self.seller).values_list("client", flat=True)),
            {"Cliente A", "Cliente B LAB"},
        )
        self.assertEqual(get_user_model().objects.count(), initial_users)
        self.assertEqual(SalesLine.objects.filter(dataset=self.dataset).count(), initial_rows)
        self.dataset.refresh_from_db()
        self.assertFalse(self.dataset.verified)
        self.assertFalse(self.dataset.full_snapshot_confirmed)
        self.assertEqual(
            AccessEvent.objects.filter(action="lab_seller_portfolio_seed").count(), 1
        )
        self.client.force_login(self.seller)
        suggestions = self.client.get(reverse("recommendations"), {
            "client": "Cliente A", "mode": "sale",
        })
        self.assertEqual(suggestions.status_code, 200)
        self.assertTrue(suggestions.context_data["recommendations"])
        self.assertContains(suggestions, "Sugerencia de venta para este cliente")
        self.assertContains(suggestions, "Acción sugerida")

    def test_repeated_seed_keeps_portfolio_and_logs_only_new_assignments(self):
        result1 = seed_demo_seller_portfolio(self.dataset, self.seller, self.admin)
        result2 = seed_demo_seller_portfolio(self.dataset, self.seller, self.admin)
        self.assertEqual(result1["new"], 1)
        self.assertEqual(result2["new"], 0)
        self.assertEqual(result2["already_owned"], 1)
        self.assertEqual(Assignment.objects.count(), 1)
        self.assertEqual(AccessEvent.objects.filter(action="lab_seller_portfolio_seed").count(), 1)

    def test_own_clients_kept_and_other_seller_clients_not_transferred(self):
        another = get_user_model().objects.create_user(
            username="other_demo_seller", password="FakeDemoPassword231")
        self.add_client("Cliente B LAB")
        self.add_client("Cliente C LAB")
        Assignment.objects.create(client="Cliente A", user=another)
        Assignment.objects.create(client="Cliente C LAB", user=self.seller)
        outcome = seed_demo_seller_portfolio(self.dataset, self.seller, self.admin)
        self.assertEqual(outcome["new"], 1)
        self.assertEqual(outcome["already_owned"], 1)
        self.assertEqual(outcome["owned_by_others"], 1)
        self.assertEqual(Assignment.objects.get(client="Cliente A").user_id, another.pk)
        self.assertEqual(Assignment.objects.get(client="Cliente B LAB").user_id, self.seller.pk)
        self.assertEqual(Assignment.objects.get(client="Cliente C LAB").user_id, self.seller.pk)

    def test_no_confirmation_does_not_seed(self):
        self.client.force_login(self.admin)
        response = self.client.post(reverse("users"), {
            "action": "seed_demo_seller", "user_id": self.seller.pk,
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Confirma la preparación")
        self.assertFalse(Assignment.objects.exists())

    def test_seller_cannot_seed_from_admin_route(self):
        self.client.force_login(self.seller)
        response = self.client.post(reverse("users"), {
            "action": "seed_demo_seller", "user_id": self.seller.pk,
            "confirm_lab": "on",
        })
        self.assertEqual(response.status_code, 403)
        self.assertFalse(Assignment.objects.exists())

    def test_not_active_or_admin_user_is_rejected(self):
        with self.assertRaisesMessage(ValueError, "cuenta vendedora activa"):
            seed_demo_seller_portfolio(self.dataset, self.admin, self.admin)
        self.seller.is_active = False
        self.seller.save(update_fields=["is_active"])
        with self.assertRaisesMessage(ValueError, "cuenta vendedora activa"):
            seed_demo_seller_portfolio(self.dataset, self.seller, self.admin)
        self.assertFalse(Assignment.objects.exists())

    def test_real_source_even_with_same_file_name_is_rejected(self):
        self.dataset.provenance = SalesDataset.Provenance.REAL
        self.dataset.save(update_fields=["provenance"])
        with self.assertRaisesMessage(ValueError, "Excel ficticio"):
            seed_demo_seller_portfolio(self.dataset, self.seller, self.admin)
        self.assertFalse(Assignment.objects.exists())

    def test_management_command_preview_then_idempotent_seed(self):
        out = StringIO()
        call_command(
            "seed_demo_seller",
            username=self.seller.username, actor=self.admin.username,
            dry_run=True, stdout=out,
        )
        self.assertIn("1 clientes disponibles", out.getvalue())
        self.assertFalse(Assignment.objects.exists())
        out = StringIO()
        call_command(
            "seed_demo_seller",
            username=self.seller.username, actor=self.admin.username,
            stdout=out,
        )
        self.assertIn("1 clientes añadidos", out.getvalue())
        self.assertEqual(Assignment.objects.count(), 1)
        call_command(
            "seed_demo_seller",
            username=self.seller.username, actor=self.admin.username,
            stdout=StringIO(),
        )
        self.assertEqual(Assignment.objects.count(), 1)

    def test_management_command_does_not_create_unknown_user(self):
        with self.assertRaises(CommandError):
            call_command(
                "seed_demo_seller", username="no_existe",
                actor=self.admin.username, stdout=StringIO(),
            )
        self.assertEqual(get_user_model().objects.count(), 2)
