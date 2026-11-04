"""Contrato de acciones comerciales orientativas en datos simulados.

Estas pruebas jamás necesitan CRM, contactos, integración externa ni conexión.
"""
from decimal import Decimal
import unittest

from smartorder.seller_actions import suggested_seller_action


class SellerActionTests(unittest.TestCase):
    def setUp(self):
        self.card = {
            "product": "Producto LAB-01 (SIMULADO)",
            "suggestion_mode": "sale",
            "unit": "kg", "unit_confirmed": True,
            "order_unit": "kg", "unit_mismatch": False,
            "unit_price": Decimal("12.50"), "available": Decimal("10"),
            "historical_rank": 1, "priority_rank": 1, "historical_total": 4,
            "historical_sale_days": 18, "priority_method": "historical",
            "commercial_forecast": None, "suggested": None,
            "inventory_warning": "",
        }

    def action(self, **changes):
        return suggested_seller_action({**self.card, **changes})

    def test_product_history_leads_to_contact_not_auto_order(self):
        action = self.action()
        self.assertEqual(action["action_code"], "offer_product")
        self.assertIn("Contactar", action["action_title"])
        self.assertIn("compras registradas", action["action_reason"])
        self.assertIn("No existe una cantidad automática validada", action["action_reason"])
        self.assertIn("Preguntar al cliente qué cantidad requiere", action["action_checks"])
        self.assertIn("no crea pedidos", action["action_checks"])

    def test_audited_priority_explains_method_without_fake_probability(self):
        action = self.action(priority_method="recent_30d", priority_rank=2,
                             commercial_forecast=Decimal("16"))
        self.assertIn("comparación cronológica", action["action_reason"])
        self.assertIn("2 de 4", action["action_reason"])
        self.assertIn("cantidad comprometida", action["action_reason"])
        self.assertNotIn("probabilidad", action["action_reason"])

    def test_missing_evidence_is_not_claimed_as_model_sale(self):
        action = self.action(historical_rank=None, priority_rank=None,
                             historical_sale_days=0)
        self.assertEqual(action["action_code"], "explore_interest")
        self.assertIn("No hay evidencia histórica suficiente", action["action_reason"])

    def test_incompatible_units_prevent_sale_promise(self):
        action = self.action(unit="kg", order_unit="lb", unit_mismatch=True,
                             commercial_forecast=Decimal("1000"))
        self.assertEqual(action["action_code"], "resolve_unit")
        self.assertIn("kg", action["action_reason"])
        self.assertIn("lb", action["action_reason"])
        self.assertIn("No comprometer cantidad ni precio", action["action_checks"])

    def test_missing_stock_tariff_or_unit_adds_checks(self):
        action = self.action(unit_confirmed=False, unit_price=None,
                             available=None, inventory_warning="Revisar inventario")
        for statement in ("Confirmar la unidad", "Confirmar tarifa",
                          "Validar disponibilidad", "Preguntar al cliente"):
            self.assertIn(statement, action["action_checks"])

    def test_no_stock_blocks_commitment(self):
        action = self.action(available=Decimal("0"))
        self.assertIn("No prometer existencias", action["action_checks"])

    def test_delivery_beyond_audited_horizon_needs_manual_quantity(self):
        action = self.action(delivery_outside_forecast=True,
                             commercial_forecast=None)
        self.assertIn("excede el horizonte", action["action_checks"])

    def test_restock_without_count_asks_for_real_verification(self):
        action = self.action(suggestion_mode="client_restock", client_stock=None)
        self.assertEqual(action["action_code"], "verify_client_stock")
        self.assertIn("conteo", action["action_title"].lower())

    def test_restock_with_stock_but_missing_target(self):
        action = self.action(suggestion_mode="client_restock",
                             client_stock=Decimal("3"), client_stock_target=None)
        self.assertEqual(action["action_code"], "verify_restock_target")

    def test_restock_suggestion_still_requires_consent(self):
        action = self.action(suggestion_mode="client_restock",
                             client_stock=Decimal("3"),
                             client_stock_target=Decimal("7"),
                             suggested=Decimal("4"))
        self.assertEqual(action["action_code"], "confirm_restock")
        self.assertIn("aceptación del cliente", action["action_checks"])

    def test_restock_without_verified_amount_never_says_automatic(self):
        action = self.action(suggestion_mode="client_restock",
                             client_stock=Decimal("3"),
                             client_stock_target=Decimal("7"),
                             suggested=None)
        self.assertEqual(action["action_code"], "review_restock")
        self.assertIn("no sustentan una cantidad automática", action["action_reason"])

    def test_adviser_does_not_echo_confidential_fields(self):
        action = self.action(
            sales_usd_display="$9999999", reference_qty=Decimal("777777"),
            explanation="Secreto administrativo, ventas 777777",
            client="Cliente oculto",
        )
        combined = " ".join(action.values())
        for secret in ("9999999", "777777", "Cliente oculto", "Secreto administrativo"):
            self.assertNotIn(secret, combined)


if __name__ == "__main__":
    unittest.main()
