"""Regression checks for the Django-only local frontend refresh."""

from pathlib import Path

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase
from django.urls import reverse


ROOT = Path(__file__).resolve().parents[1]


class LocalFrontendAssetsTests(SimpleTestCase):
    def test_django_uses_separate_local_stylesheet_without_touching_streamlit(self):
        base = (ROOT / "operations/templates/operations/base.html").read_text(encoding="utf-8")
        local_css = (ROOT / "operations/static/operations/css/local-frontend.css").read_text(encoding="utf-8")
        streamlit = (ROOT / "streamlit_app.py").read_text(encoding="utf-8")
        self.assertIn("operations/css/local-frontend.css", base)
        self.assertIn(".local-app .app-header", local_css)
        self.assertIn(".local-app .auth-card", local_css)
        self.assertNotIn("local-frontend.css", streamlit)


class LocalFrontendViewTests(TestCase):
    def test_login_has_branded_access_form(self):
        response = self.client.get(reverse("login"))
        self.assertEqual(response.status_code, 200)
        for text in ("VITALI", "SmartOrder AI", "Iniciar sesión", "local-auth",
                     "name=\"username\"", "name=\"password\"", "csrfmiddlewaretoken"):
            self.assertContains(response, text)

    def test_local_first_run_does_not_ask_for_installation_code(self):
        response = self.client.get(reverse("setup"), REMOTE_ADDR="127.0.0.1")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "En el programa local no necesitas código de instalación")
        self.assertNotContains(response, 'name="setup_code"')

    def test_admin_and_seller_keep_distinct_navigation_and_authorization(self):
        User = get_user_model()
        admin = User.objects.create_user(username="ui_admin", password="safe-password-12345",
                                         is_staff=True)
        seller = User.objects.create_user(username="ui_seller", password="safe-password-54321",
                                          is_staff=False)

        self.client.force_login(admin)
        admin_home = self.client.get(reverse("pilot_dashboard"))
        self.assertEqual(admin_home.status_code, 200)
        self.assertContains(admin_home, "Control administrativo")
        self.assertContains(admin_home, "autorización de producción corresponden a administración")
        self.assertContains(admin_home, "local-frontend.css")
        self.assertEqual(self.client.get(reverse("users")).status_code, 200)

        self.client.force_login(seller)
        seller_home = self.client.get(reverse("dashboard"))
        self.assertEqual(seller_home.status_code, 200)
        self.assertContains(seller_home, "Gestión de mi cartera")
        self.assertContains(seller_home, "exclusivamente a administración")
        self.assertEqual(self.client.get(reverse("users")).status_code, 403)
