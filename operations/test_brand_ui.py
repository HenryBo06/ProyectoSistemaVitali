"""Presentation tokens stay aligned between Django and Streamlit."""

from pathlib import Path

from django.test import SimpleTestCase, TestCase
from django.urls import reverse


ROOT = Path(__file__).resolve().parents[1]


class BrandPresentationTests(SimpleTestCase):
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
