"""Pruebas deterministas de ranking de productos sin datos reales de Vitali."""
import unittest
from datetime import date, timedelta

from smartorder.product_priority import (
    METHOD_LABELS, evaluate_product_priority,
)


class ProductPriorityTests(unittest.TestCase):
    def setUp(self):
        first = date(2025, 1, 1)
        self.first = first
        # Frecuencia histórica dominada por Legado; tras un cambio sostenido
        # solo Nuevo se sigue comprando. Extra evita una lista trivial binaria.
        events = [(first, "Nuevo"), (first + timedelta(days=1), "Extra")]
        events += [(first + timedelta(days=i), "Legado") for i in range(0, 80, 2)]
        events += [(first + timedelta(days=i), "Nuevo") for i in range(80, 201, 4)]
        self.events = events
        self.products = ("Legado", "Nuevo", "Extra")
        self.as_of = first + timedelta(days=202)

    def test_held_out_recent_trend_can_outperform_historical_priority(self):
        decision = evaluate_product_priority(self.events, as_of=self.as_of,
                                              products=self.products)
        self.assertTrue(decision.validated)
        self.assertIn(decision.method, ("recent_30d", "decay_28d"))
        self.assertEqual(decision.ranked[0], "Nuevo")
        self.assertGreaterEqual(decision.selection_cases, 8)
        self.assertGreaterEqual(decision.audit_cases, 6)
        self.assertGreaterEqual(decision.audit_hits - decision.baseline_audit_hits, 2)
        self.assertTrue(METHOD_LABELS[decision.method])

    def test_future_records_never_change_selection_or_ranking(self):
        original = evaluate_product_priority(self.events, as_of=self.as_of,
                                              products=self.products)
        injected = self.events + [
            (self.as_of, "Legado"),
            (self.as_of + timedelta(days=8), "Legado"),
            (self.as_of + timedelta(days=80), "Extra"),
        ]
        future = evaluate_product_priority(injected, as_of=self.as_of,
                                            products=self.products)
        self.assertEqual(original, future)

    def test_same_day_multiple_lines_count_as_one_purchase_day(self):
        normal = evaluate_product_priority(self.events, as_of=self.as_of,
                                            products=self.products)
        duplicated = self.events + [(self.first + timedelta(days=10), "Legado")] * 90
        self.assertEqual(normal, evaluate_product_priority(
            duplicated, as_of=self.as_of, products=self.products,
        ))

    def test_sparse_data_falls_back_without_false_audit_confidence(self):
        early = [(self.first + timedelta(days=i), "Legado") for i in range(12)]
        decision = evaluate_product_priority(early, as_of=self.as_of,
                                              products=self.products)
        self.assertFalse(decision.validated)
        self.assertEqual(decision.method, "historical")
        self.assertEqual(decision.ranked, self.products)

    def test_cutoff_before_first_observation_cannot_learn_from_future(self):
        decision = evaluate_product_priority(
            self.events, as_of=self.first, products=self.products,
        )
        self.assertFalse(decision.validated)
        self.assertEqual(decision.ranked, self.products)

    def test_unproven_seasonal_override_keeps_reference_order(self):
        decision = evaluate_product_priority(
            self.events, as_of=self.as_of, products=self.products, allow_override=False,
        )
        self.assertFalse(decision.validated)
        self.assertEqual(decision.ranked, self.products)
        self.assertGreaterEqual(decision.audit_cases, 6)


if __name__ == "__main__":
    unittest.main()
