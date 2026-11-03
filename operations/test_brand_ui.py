"""Presentation tokens stay aligned between Django and Streamlit."""

from pathlib import Path

from django.test import SimpleTestCase, TestCase
from django.urls import reverse


ROOT = Path(__file__).resolve().parents[1]


class BrandPresentationTests(SimpleTestCase):
    def test_existing_vitali_mark_is_reused_across_both_interfaces(self):
        logo = (ROOT / "operations/static/operations/favicon.svg").read_text(encoding="utf-8")
        base = (ROOT / "operations/templates/operations/base.html").read_text(encoding="utf-8")
        login = (ROOT / "operations/templates/operations/login.html").read_text(encoding="utf-8")
        navigation = (ROOT / "operations/templates/operations/navigation.html").read_text(encoding="utf-8")
        streamlit = (ROOT / "streamlit_app.py").read_text(encoding="utf-8")
        local_css = (ROOT / "operations/static/operations/css/local-frontend.css").read_text(encoding="utf-8")
        self.assertIn('fill="#e07c2a"', logo)
        self.assertIn('class="brand-symbol"', base)
        self.assertIn('class="auth-brand-symbol"', login)
        self.assertIn('class="nav-intro-mark"', navigation)
        self.assertIn("VITALI_MARK_URI", streamlit)
        self.assertIn("vitali-symbol", streamlit)
        self.assertIn("--v-spectrum", local_css)

    def test_django_and_streamlit_share_juice_flight_tokens(self):
        django_css = (ROOT / "operations/static/operations/css/app.css").read_text(encoding="utf-8")
        streamlit_css = (ROOT / "operations/static/operations/css/streamlit.css").read_text(encoding="utf-8")
        for css in (django_css, streamlit_css):
            self.assertIn("--v-flight", css)
            self.assertIn("#5ba744", css)
            self.assertIn("#0c6f7c", css)
            self.assertIn("#e07c2a", css)
            self.assertIn("#e7eef1", css)


class LoginPresentationTests(TestCase):
    def test_login_screen_shows_vitali_wordmark(self):
        response = self.client.get(reverse("login"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "VITALI")
        self.assertContains(response, "SmartOrder AI")
        self.assertContains(response, "app.css")
