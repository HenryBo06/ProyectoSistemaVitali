import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from streamlit.testing.v1 import AppTest


class DemoAppTests(unittest.TestCase):
    def _open_role(self, label: str, expected_header: str,
                   destination: str, destination_header: str) -> None:
        with TemporaryDirectory() as folder, patch.dict(os.environ, {
            "SMARTORDER_LOCAL_MODE": "1",
            "SMARTORDER_DEMO_MODE": "1",
            "SMARTORDER_HOME": str(Path(folder) / "demo"),
            "STREAMLIT_SERVER_ADDRESS": "127.0.0.1",
        }):
            app = AppTest.from_file("../app.py", default_timeout=30).run()
            self.assertFalse(app.exception)
            button = next(item for item in app.button if item.label == label)
            button.click().run(timeout=30)
            self.assertFalse(app.exception)
            self.assertIn(expected_header, [item.value for item in app.header])
            navigation = next(item for item in app.radio if item.label == "Navegación")
            navigation.set_value(destination).run(timeout=30)
            self.assertFalse(app.exception)
            self.assertIn(destination_header, [item.value for item in app.header])

    def test_demo_admin_entry(self):
        self._open_role(
            ":material/admin_panel_settings: Entrar como administración",
            "Centro de administración",
            "Centro IA",
            "Centro IA",
        )

    def test_demo_seller_entry(self):
        self._open_role(
            ":material/badge: Entrar como vendedor",
            "Mi cartera",
            "Recomendaciones",
            "Recomendaciones para pedidos",
        )

    def test_public_demo_button_creates_isolated_demo(self):
        with TemporaryDirectory() as folder, patch.dict(os.environ, {
            "SMARTORDER_LOCAL_MODE": "",
            "SMARTORDER_DEMO_MODE": "",
            "SMARTORDER_PUBLIC_DEMO": "",
            "SMARTORDER_HOME": str(Path(folder) / "cloud"),
            "STREAMLIT_SERVER_ADDRESS": "0.0.0.0",
        }):
            app = AppTest.from_file("../app.py", default_timeout=30).run()
            self.assertFalse(app.exception)
            entry = next(
                item for item in app.button
                if item.label == ":material/science: Probar demo pública"
            )
            entry.click().run(timeout=30)
            self.assertFalse(app.exception)
            labels = [item.label for item in app.button]
            self.assertIn(
                ":material/admin_panel_settings: Entrar como administración", labels
            )
            self.assertIn(":material/badge: Entrar como vendedor", labels)


if __name__ == "__main__":
    unittest.main()
