"""Tests for opt-in Streamlit demo accounts; no real credentials are used."""

import secrets
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth import authenticate, get_user_model
from django.core.exceptions import ImproperlyConfigured
from django.test import TestCase
from django.urls import reverse

from .demo_bootstrap import provision_demo_accounts


class DemoBootstrapTests(TestCase):
    def setUp(self):
        self.config = {
            "enabled": True,
            "admin_username": "lab_admin_01",
            "admin_password": secrets.token_urlsafe(24),
            "seller_username": "lab_vendedor_01",
            "seller_password": secrets.token_urlsafe(24),
        }

    def test_creates_both_roles_and_allows_login(self):
        self.assertTrue(provision_demo_accounts(**self.config))
        User = get_user_model()
        self.assertEqual(User.objects.count(), 2)
        admin = authenticate(username=self.config["admin_username"],
                             password=self.config["admin_password"])
        seller = authenticate(username=self.config["seller_username"],
                              password=self.config["seller_password"])
        self.assertIsNotNone(admin)
        self.assertIsNotNone(seller)
        self.assertTrue(admin.is_staff)
        self.assertFalse(admin.is_superuser)
        self.assertFalse(seller.is_staff)
        self.assertFalse(seller.is_superuser)

        self.client.force_login(admin)
        self.assertEqual(self.client.get(reverse("users")).status_code, 200)
        self.client.force_login(seller)
        self.assertEqual(self.client.get(reverse("users")).status_code, 403)

    def test_second_run_never_overwrites_passwords_or_accounts(self):
        self.assertTrue(provision_demo_accounts(**self.config))
        changed = dict(self.config, admin_password=secrets.token_urlsafe(24))
        self.assertFalse(provision_demo_accounts(**changed))
        admin = get_user_model().objects.get(username=self.config["admin_username"])
        self.assertTrue(admin.check_password(self.config["admin_password"]))
        self.assertFalse(admin.check_password(changed["admin_password"]))
        self.assertEqual(get_user_model().objects.count(), 2)

    def test_existing_accounts_are_preserved_without_seeding(self):
        User = get_user_model()
        existing_password = secrets.token_urlsafe(24)
        existing = User.objects.create_user(username="existing", password=existing_password)
        self.assertFalse(provision_demo_accounts(**self.config))
        self.assertEqual(User.objects.count(), 1)
        existing.refresh_from_db()
        self.assertTrue(existing.check_password(existing_password))

    def test_disabled_or_postgresql_never_seeds(self):
        self.assertFalse(provision_demo_accounts(**dict(self.config, enabled=False)))
        with patch.dict(settings.DATABASES["default"], {"ENGINE": "django.db.backends.postgresql"}):
            self.assertFalse(provision_demo_accounts(**self.config))
        self.assertFalse(get_user_model().objects.exists())

    def test_incomplete_or_weak_credentials_fail_without_creating_users(self):
        with self.assertRaises(ImproperlyConfigured):
            provision_demo_accounts(**dict(self.config, seller_password=""))
        with self.assertRaises(ImproperlyConfigured):
            provision_demo_accounts(**dict(self.config, seller_password="123"))
        with self.assertRaises(ImproperlyConfigured):
            provision_demo_accounts(**dict(self.config, seller_username=self.config["admin_username"]))
        self.assertFalse(get_user_model().objects.exists())
