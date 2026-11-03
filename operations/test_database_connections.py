"""Enterprise connection settings: administrative only, secretless and read-only."""

from unittest.mock import MagicMock, patch
import os

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from .models import AccessEvent, ExternalDatabaseConnection
from .database_connections import validate_connection_fields, test_postgres_readonly


class ExternalDatabaseConnectionTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.admin = User.objects.create_user(username="db_admin", password="SafeTestPassword!1",
                                            is_staff=True)
        cls.seller = User.objects.create_user(username="db_seller", password="SafeTestPassword!1")
        cls.fields = {
            "alias": "vitali-sales", "name": "Ventas Vitali",
            "purpose": "sales", "host": "localhost", "port": "5432",
            "database": "vitali", "username": "read_only",
            "password_env": "SMARTORDER_SOURCE_VITALI_PASSWORD", "sslmode": "prefer",
        }

    def test_admin_can_register_without_storing_password(self):
        self.client.force_login(self.admin)
        response = self.client.post(reverse("database_connections"), {
            "action": "save", **self.fields,
        })
        self.assertRedirects(response, reverse("database_connections"))
        record = ExternalDatabaseConnection.objects.get(alias="vitali-sales")
        self.assertFalse(record.enabled)
        self.assertEqual(record.password_env, "SMARTORDER_SOURCE_VITALI_PASSWORD")
        self.assertFalse(hasattr(record, "password"))
        page = self.client.get(reverse("database_connections"))
        self.assertContains(page, "Conexiones de bases de datos")
        self.assertContains(page, "Ventas Vitali")

    def test_seller_is_denied_and_cannot_probe_sources(self):
        self.client.force_login(self.seller)
        self.assertEqual(self.client.get(reverse("database_connections")).status_code, 403)
        self.assertEqual(self.client.post(reverse("database_connections"), {
            "action": "save", **self.fields,
        }).status_code, 403)
        self.assertEqual(ExternalDatabaseConnection.objects.count(), 0)

    def test_admin_cannot_enable_before_successful_check(self):
        self.client.force_login(self.admin)
        self.client.post(reverse("database_connections"), {"action": "save", **self.fields})
        item = ExternalDatabaseConnection.objects.get()
        self.client.post(reverse("database_connections"), {
            "action": "toggle", "connection_id": item.pk, "enabled": "yes",
        })
        item.refresh_from_db()
        self.assertFalse(item.enabled)

    def test_connection_test_only_selects_in_readonly_transaction(self):
        item = ExternalDatabaseConnection.objects.create(
            **validate_connection_fields(self.fields), created_by=self.admin)
        conn = MagicMock()
        conn.__enter__.return_value = conn
        conn.execute.return_value.fetchone.return_value = (1,)
        with patch.dict(os.environ, {"SMARTORDER_SOURCE_VITALI_PASSWORD": "private-demo",
                                   "SMARTORDER_SOURCE_DB_ALLOWED_HOSTS": "localhost"}), \
             patch("operations.database_connections.psycopg.connect", return_value=conn) as connect:
            self.client.force_login(self.admin)
            self.client.post(reverse("database_connections"), {
                "action": "test", "connection_id": item.pk,
            })
        item.refresh_from_db()
        self.assertTrue(item.last_test_ok)
        self.assertIsNotNone(item.last_tested_at)
        self.assertEqual([call.args[0] for call in conn.execute.call_args_list],
                         ["BEGIN READ ONLY", "SELECT 1", "ROLLBACK"])
        self.assertEqual(connect.call_args.kwargs["options"],
                         "-c default_transaction_read_only=on -c statement_timeout=3000")
        self.assertEqual(connect.call_args.kwargs["password"], "private-demo")
        self.assertNotIn("private-demo", self.client.get(reverse("database_connections")).content.decode())
        self.assertTrue(AccessEvent.objects.filter(action="external_db_test").exists())

    def test_denied_host_and_missing_secret_do_not_open_network(self):
        item = ExternalDatabaseConnection.objects.create(
            **validate_connection_fields(self.fields), created_by=self.admin)
        with patch.dict(os.environ, {"SMARTORDER_SOURCE_DB_ALLOWED_HOSTS": "db.vitali.local"},
                        clear=False), patch("operations.database_connections.psycopg.connect") as connect:
            with self.assertRaisesRegex(ValueError, "no autorizado"):
                test_postgres_readonly(item)
            connect.assert_not_called()
        with patch.dict(os.environ, {"SMARTORDER_SOURCE_DB_ALLOWED_HOSTS": "localhost",
                                    "SMARTORDER_SOURCE_VITALI_PASSWORD": ""}), \
             patch("operations.database_connections.psycopg.connect") as connect:
            with self.assertRaisesRegex(ValueError, "Credencial privada"):
                test_postgres_readonly(item)
            connect.assert_not_called()

    def test_no_arbitrary_sql_and_unvalidated_tls(self):
        with self.assertRaisesRegex(ValueError, "TLS"):
            validate_connection_fields({**self.fields, "host": "db.example.com", "sslmode": "prefer"})
        with self.assertRaisesRegex(ValueError, "variable"):
            validate_connection_fields({**self.fields, "password_env": "DATABASE_URL"})
