"""Checks de horizontes, origen y validación temporal de las recomendaciones."""

import unittest
from datetime import date, timedelta

import pandas as pd

from smartorder.data import SalesData
from smartorder.recommendations import RecommendationEngine


def sales(first: date, days: int, *, spike_day: int | None = None) -> SalesData:
    records = []
    for offset in range(days):
        amount = 100.0 if offset == spike_day else 1.0
        records.append({"Fecha": pd.Timestamp(first + timedelta(days=offset)),
                        "Cliente": "Cliente A", "Producto": "Producto X",
                        "Cantidad_kg_unid": amount})
    return SalesData(pd.DataFrame(records), "test", ("Ventas",), 0)


class RecommendationTests(unittest.TestCase):
    def test_unverified_year_old_data_is_reference_and_manual(self):
        first = date(2025, 1, 1)
        engine = RecommendationEngine(sales(first, 365, spike_day=270))
        result = engine.for_delivery(date(2026, 9, 28), False, date(2026, 9, 28)).iloc[0]
        expected_start = date(2025, 9, 28)
        expected = 7 + 99 * (expected_start <= first + timedelta(days=270)
                              <= expected_start + timedelta(days=6))
        self.assertEqual(result["reference_previous_year_7d"], expected)
        self.assertIsNone(result["forecast"])
        self.assertFalse(result["eligible_for_auto"])
        self.assertEqual(result["horizon_end"], date(2026, 10, 4))

    def test_verified_regular_sales_can_be_recommended_after_backtest(self):
        first = date(2025, 1, 1)
        data = sales(first, 365)
        engine = RecommendationEngine(data)
        as_of = data.last_date + timedelta(days=1)
        result = engine.for_delivery(as_of + timedelta(days=3), True, as_of).iloc[0]
        self.assertEqual(result["forecast"], 7.0)
        self.assertTrue(result["eligible_for_auto"])
        self.assertEqual(result["delivery_date"], as_of + timedelta(days=3))
        self.assertEqual(result["horizon_end"], as_of + timedelta(days=9))
        backtest = engine.backtest(3)
        self.assertEqual(backtest.windows, 16)
        self.assertEqual(set(backtest.scores["partition"]), {"selección", "auditoría"})

    def test_sales_through_today_can_support_delivery_tomorrow(self):
        data = sales(date(2025, 1, 1), 365)
        today = data.last_date
        result = RecommendationEngine(data).for_delivery(
            today + timedelta(days=1), True, today
        ).iloc[0]
        self.assertEqual(result["forecast"], 7.0)
        self.assertTrue(result["eligible_for_auto"])

    def test_future_sales_cannot_enter_training_before_cutoff(self):
        first = date(2025, 1, 1)
        cutoff = first + timedelta(days=250)
        plain = RecommendationEngine(sales(first, 365))
        with_future_spike = RecommendationEngine(sales(first, 365, spike_day=260))
        predicted_plain = plain._xgboost(cutoff, 3)
        predicted_spike = with_future_spike._xgboost(cutoff, 3)
        self.assertIsNotNone(predicted_plain)
        self.assertAlmostEqual(float(predicted_plain[0]), float(predicted_spike[0]), places=6)

    def test_stale_verified_source_stays_manual(self):
        data = sales(date(2025, 1, 1), 365)
        as_of = date(2026, 2, 1)
        result = RecommendationEngine(data).for_delivery(as_of, True, as_of).iloc[0]
        self.assertIsNone(result["forecast"])
        self.assertFalse(result["eligible_for_auto"])

    def test_prior_year_becomes_candidate_only_after_comparable_year_exists(self):
        first = date(2025, 1, 1)
        one_year = RecommendationEngine(sales(first, 365)).backtest(0)
        two_years = RecommendationEngine(sales(first, 730)).backtest(0)
        seasonal = "Mismos 7 días del año anterior"
        self.assertNotIn(seasonal, set(one_year.scores["method"]))
        self.assertIn(seasonal, set(two_years.scores["method"]))


if __name__ == "__main__":
    unittest.main()
