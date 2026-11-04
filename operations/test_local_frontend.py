"""Regression checks for the Django-only local frontend refresh."""

from pathlib import Path

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse


ROOT = Path(__file__).resolve().parents[1]


class LocalFrontendAssetsTests(SimpleTestCase):
    def test_seller_typography_is_scaled_without_reducing_inputs(self):
        css = (ROOT / "operations/static/operations/css/local-frontend.css").read_text(encoding="utf-8")
        streamlit = (ROOT / "operations/static/operations/css/streamlit.css").read_text(encoding="utf-8")
        self.assertIn(".seller-page-heading h1 { font-size: 1.34rem;", css)
        self.assertIn(".seller-product-summary h2 { font-size: .96rem;", css)
        self.assertIn(".seller-page-heading + .d-flex .btn", css)
        self.assertIn(".seller-restock-facts strong { margin-top: .1rem; font-size: .9rem;", css)
        self.assertIn("h1 { font-size: 1.44rem !important;", streamlit)
        self.assertIn("h2 { font-size: 1.08rem !important;", streamlit)
        self.assertIn("main:has(.seller-density-marker) [data-testid=\"stMetricValue\"] { font-size: 1.2rem", streamlit)


    def test_seller_navigation_is_single_row_and_semantic(self):
        template = (ROOT / "operations/templates/operations/recommendations.html").read_text(encoding="utf-8")
        css = (ROOT / "operations/static/operations/css/local-frontend.css").read_text(encoding="utf-8")
        streamlit = (ROOT / "operations/static/operations/css/streamlit.css").read_text(encoding="utf-8")
        self.assertIn('class="seller-mode-tabs"', template)
        self.assertIn('aria-label="Tipo de consulta comercial"', template)
        self.assertIn(".seller-mode-tabs {", css)
        self.assertIn("grid-template-columns: repeat(3, minmax(0, 1fr));", css)
        self.assertIn(".seller-mode-tab:focus-visible", css)
        self.assertIn("padding-left: 3.1rem;", streamlit)

    def test_restock_summary_spans_full_card_width(self):
        local_css = (ROOT / "operations/static/operations/css/local-frontend.css").read_text(encoding="utf-8")
        template = (ROOT / "operations/templates/operations/recommendations.html").read_text(encoding="utf-8")
        self.assertIn("seller-restock-summary", template)
        self.assertIn(".seller-product-summary .seller-restock-summary {", local_css)
        self.assertIn("grid-column: 1 / -1;", local_css)
        self.assertIn("grid-template-columns: repeat(2, minmax(0, 1fr));", local_css)

    def test_django_uses_separate_local_stylesheet_without_touching_streamlit(self):
        base = (ROOT / "operations/templates/operations/base.html").read_text(encoding="utf-8")
        local_css = (ROOT / "operations/static/operations/css/local-frontend.css").read_text(encoding="utf-8")
        streamlit = (ROOT / "streamlit_app.py").read_text(encoding="utf-8")
        self.assertIn("operations/css/local-frontend.css", base)
        self.assertIn(".local-app .app-header", local_css)
        self.assertIn(".local-app .auth-card", local_css)
        self.assertNotIn("local-frontend.css", streamlit)


class LocalFrontendViewTests(TestCase):
    def assertModernDjangoShell(self, response):
        self.assertContains(
            response,
            'href="/static/operations/css/local-frontend.css?v=seller-brand-20261103a"',
        )

    def test_login_has_branded_access_form(self):
        response = self.client.get(reverse("login"))
        self.assertEqual(response.status_code, 200)
        self.assertModernDjangoShell(response)
        for text in ("VITALI", "SmartOrder AI", "Iniciar sesión", "local-auth",
                     "name=\"username\"", "name=\"password\"", "csrfmiddlewaretoken"):
            self.assertContains(response, text)

    @override_settings(DEBUG=True)
    def test_local_first_run_does_not_ask_for_installation_code(self):
        response = self.client.get(reverse("setup"), REMOTE_ADDR="127.0.0.1", HTTP_HOST="127.0.0.1:8000")
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
        self.assertModernDjangoShell(admin_home)
        self.assertContains(admin_home, "Control administrativo")
        self.assertContains(admin_home, "autorización de producción corresponden a administración")
        self.assertEqual(self.client.get(reverse("users")).status_code, 200)

        self.client.force_login(seller)
        seller_home = self.client.get(reverse("dashboard"))
        self.assertEqual(seller_home.status_code, 200)
        self.assertModernDjangoShell(seller_home)
        self.assertContains(seller_home, "Gestión de mi cartera")
        self.assertContains(seller_home, "administración autoriza producción")
        self.assertEqual(self.client.get(reverse("users")).status_code, 403)
