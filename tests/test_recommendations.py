"""Checks de horizontes, origen y validación temporal de las recomendaciones."""

import unittest
from datetime import date, timedelta

import numpy as np
import pandas as pd

from smartorder.data import SalesData
from smartorder.recommendations import RecommendationEngine


def sales(first: date, days: int, *, spike_day: int | None = None,
          date_semantics: str = "sale_order", provenance: str = "simulated") -> SalesData:
    records = []
    for offset in range(days):
        amount = 100.0 if offset == spike_day else 1.0
        records.append({"Fecha": pd.Timestamp(first + timedelta(days=offset)),
                        "Cliente": "Cliente A", "Producto": "Producto X",
                        "Unidad": "kg",
                        "Cantidad_kg_unid": amount})
    return SalesData(
        pd.DataFrame(records), "test", ("Ventas",), 0,
        date_semantics=date_semantics, provenance=provenance,
        coverage={("Cliente A", "Producto X", "kg"): ((first, first + timedelta(days=days - 1)),)},
    )


def sales_with_events(first: date, days: int, event_dates: tuple[date, ...]) -> SalesData:
    event_days = set(event_dates)
    records = [{"Fecha": pd.Timestamp(day), "Cliente": "Cliente A", "Producto": "Producto X", "Unidad": "kg",
                "Cantidad_kg_unid": 20.0 if day in event_days else 1.0}
               for day in (first + timedelta(days=offset) for offset in range(days))]
    return SalesData(
        pd.DataFrame(records), "event-test", ("Ventas",), 0,
        date_semantics="sale_order", provenance="simulated",
        coverage={("Cliente A", "Producto X", "kg"): ((first, first + timedelta(days=days - 1)),)},
    )


class RecommendationTests(unittest.TestCase):
    def test_score_reports_signed_bias_and_marks_zero_actual_denominator_unavailable(self):
        score = RecommendationEngine._score(
            np.array([10.0, 0.0]), np.array([12.0, 1.0])
        )
        self.assertAlmostEqual(score["signed_bias"], 0.3)
        self.assertAlmostEqual(score["wape"], 0.3)

        zero_actual = RecommendationEngine._score(
            np.array([0.0, 0.0]), np.array([0.0, 0.0])
        )
        self.assertIsNone(zero_actual["signed_bias"])
        self.assertIsNone(zero_actual["wape"])

    def test_unverified_year_old_data_is_reference_and_manual(self):
        first = date(2025, 1, 1)
        engine = RecommendationEngine(sales(first, 365, spike_day=270, date_semantics="delivery"))
        result = engine.for_delivery(date(2026, 9, 28), False, date(2026, 9, 28)).iloc[0]
        self.assertEqual(result["reference_date"], first + timedelta(days=364))
        self.assertEqual(result["reference_qty"], 1.0)
        self.assertIsNone(result["forecast"])
        self.assertFalse(result["eligible_for_auto"])
        self.assertEqual(result["target_date"], date(2026, 9, 28))

    def test_verified_regular_sales_can_be_recommended_after_backtest(self):
        first = date(2025, 1, 1)
        data = sales(first, 365, date_semantics="delivery")
        engine = RecommendationEngine(data)
        as_of = data.last_date + timedelta(days=1)
        result = engine.for_delivery(as_of + timedelta(days=3), True, as_of).iloc[0]
        self.assertEqual(result["forecast"], 1.0)
        self.assertTrue(result["eligible_for_auto"])
        self.assertEqual(result["target_date"], as_of + timedelta(days=3))
        self.assertEqual(result["quantity"], 1.0)
        backtest = engine.backtest(3)
        self.assertEqual(backtest.windows, 16)
        self.assertEqual(set(backtest.scores["partition"]), {"selección", "auditoría"})
        self.assertEqual(backtest.audit_targets, 8)
        self.assertEqual(backtest.eligible_audit_targets, 8)
        self.assertEqual(backtest.audit_coverage, 1.0)
        comparison = backtest.audit_comparison()
        self.assertEqual(comparison["selected"]["targets"], 8)
        self.assertEqual(comparison["baseline_28d"]["targets"], 8)
        self.assertIsNotNone(comparison["selected"]["signed_bias"])

    def test_sales_through_today_can_support_delivery_tomorrow(self):
        data = sales(date(2025, 1, 1), 365, date_semantics="delivery")
        today = data.last_date
        result = RecommendationEngine(data).for_delivery(
            today + timedelta(days=1), True, today
        ).iloc[0]
        self.assertEqual(result["forecast"], 1.0)
        self.assertTrue(result["eligible_for_auto"])

    def test_delivery_week_is_a_separate_total_objective(self):
        data = sales(date(2025, 1, 1), 365, date_semantics="delivery")
        today = data.last_date
        result = RecommendationEngine(data).for_delivery_week(
            today + timedelta(days=1), True, today
        ).iloc[0]
        self.assertEqual(result["forecast"], 7.0)
        self.assertIsNone(result["quantity"])
        self.assertEqual(result["target_kind"], "delivery_period_total")
        self.assertTrue(result["eligible_for_auto"])

    def test_sale_order_dates_never_create_a_delivery_quantity(self):
        data = sales(date(2025, 1, 1), 365, date_semantics="sale_order")
        today = data.last_date + timedelta(days=1)
        result = RecommendationEngine(data).for_delivery(today + timedelta(days=1), True, today).iloc[0]
        self.assertIsNone(result["quantity"])
        self.assertIsNone(result["forecast"])
        self.assertEqual(result["evidence_status"], "incompatible_dates")
        self.assertFalse(result["eligible_for_auto"])

    def test_sale_suggestion_is_for_the_requested_day_not_a_week_total(self):
        data = sales(date(2025, 1, 1), 365, date_semantics="sale_order")
        today = data.last_date
        result = RecommendationEngine(data).for_sale(today + timedelta(days=1), True, today).iloc[0]
        self.assertEqual(result["forecast"], 1.0)
        self.assertTrue(result["eligible_for_auto"])
        self.assertEqual(result["audit_windows"], 8)
        self.assertEqual(result["audit_targets"], 8)
        self.assertEqual(result["audit_coverage"], 1.0)
        self.assertIsNotNone(result["audit_mae"])
        self.assertIsNotNone(result["baseline_mae"])
        self.assertIsNotNone(result["audit_overforecast"])
        self.assertIsNotNone(result["baseline_overforecast"])

    def test_sale_suggestion_uses_only_days_before_the_target(self):
        first = date(2025, 1, 1)
        target = first + timedelta(days=210)
        before = RecommendationEngine(sales(first, 300))._sale_forecast(target, 0)
        after = RecommendationEngine(sales(first, 365, spike_day=350))._sale_forecast(target, 0)
        self.assertEqual(before, after)

    def test_future_and_open_day_sales_do_not_change_a_closed_historical_result(self):
        first = date(2025, 1, 1)
        today = first + timedelta(days=300)
        target = today
        plain = RecommendationEngine(sales(first, 301))
        future = RecommendationEngine(sales(first, 365, spike_day=330))
        baseline = plain.for_sale(target, True, today)
        changed = future.for_sale(target, True, today)
        self.assertEqual(baseline.iloc[0]["cutoff_date"], today - timedelta(days=1))
        self.assertEqual(changed.iloc[0]["cutoff_date"], baseline.iloc[0]["cutoff_date"])
        self.assertEqual(changed.iloc[0]["forecast"], baseline.iloc[0]["forecast"])
        self.assertEqual(changed.iloc[0]["reference_qty"], baseline.iloc[0]["reference_qty"])

        base = sales(first, 301)
        extra_units = pd.DataFrame([
            {"Fecha": pd.Timestamp(today), "Cliente": "Cliente A", "Producto": "Producto X",
             "Unidad": "", "Cantidad_kg_unid": 50.0},
            {"Fecha": pd.Timestamp(today + timedelta(days=1)), "Cliente": "Cliente A",
             "Producto": "Producto X", "Unidad": "caja", "Cantidad_kg_unid": 80.0},
        ])
        with_open_or_future_series = SalesData(
            pd.concat([base.rows, extra_units], ignore_index=True), "open-day-unit", base.sheets, 0,
            date_semantics=base.date_semantics, provenance=base.provenance,
            coverage=base.coverage,
        )
        unchanged = RecommendationEngine(with_open_or_future_series).for_sale(target, True, today)
        pd.testing.assert_frame_equal(unchanged, baseline)

    def test_future_only_series_does_not_change_weekly_forecast_or_audit_metrics(self):
        first = date(2024, 1, 1)
        as_of = first + timedelta(days=364)
        cutoff = as_of - timedelta(days=1)
        records = [{
            "Fecha": pd.Timestamp(first + timedelta(days=offset)),
            "Cliente": "A", "Producto": "X", "Unidad": "kg",
            "Cantidad_kg_unid": float(1 + (offset // 7) % 4),
        } for offset in range((cutoff - first).days + 1)]
        base = SalesData(
            pd.DataFrame(records), "weekly-base", ("Ventas",), 0,
            date_semantics="delivery", provenance="real",
            coverage={("A", "X", "kg"): ((first, cutoff),)},
        )
        future_row = pd.DataFrame([{
            "Fecha": pd.Timestamp(as_of + timedelta(days=2)), "Cliente": "FUTURO",
            "Producto": "X", "Unidad": "kg", "Cantidad_kg_unid": 100.0,
        }])
        with_future = SalesData(
            pd.concat([base.rows, future_row], ignore_index=True), "weekly-future",
            base.sheets, 0, date_semantics=base.date_semantics,
            provenance=base.provenance, coverage=base.coverage,
        )
        target = as_of + timedelta(days=1)
        original = RecommendationEngine(base).for_delivery_week(target, True, as_of)
        changed = RecommendationEngine(with_future).for_delivery_week(target, True, as_of)
        pd.testing.assert_frame_equal(original, changed)

    def test_unknown_unit_and_incomplete_series_coverage_stay_manual(self):
        first = date(2025, 1, 1)
        records = [{"Fecha": pd.Timestamp(first + timedelta(days=i)), "Cliente": "Cliente A",
                    "Producto": "Producto X", "Unidad": "", "Cantidad_kg_unid": 1.0}
                   for i in range(365)]
        unknown = SalesData(pd.DataFrame(records), "unknown", ("Ventas",), 0,
                            provenance="real")
        result = RecommendationEngine(unknown).for_sale(
            first + timedelta(days=365), True, first + timedelta(days=365)
        ).iloc[0]
        self.assertIsNone(result["forecast"])
        self.assertEqual(result["evidence_status"], "unknown_unit")

        known = sales(first, 365)
        partial = SalesData(known.rows, known.digest, known.sheets, known.recalculated_amounts,
                            date_semantics=known.date_semantics, provenance=known.provenance,
                            coverage={("Cliente A", "Producto X", "kg"): ((first, first + timedelta(days=100)),)})
        result = RecommendationEngine(partial).for_sale(
            first + timedelta(days=365), True, first + timedelta(days=365)
        ).iloc[0]
        self.assertIsNone(result["forecast"])
        self.assertEqual(result["evidence_status"], "unknown_coverage")
        self.assertIn("cobertura", result["evidence"])

    def test_sale_suggestion_stays_manual_without_verified_fresh_data(self):
        data = sales(date(2025, 1, 1), 365)
        today = data.last_date + timedelta(days=1)
        unverified = RecommendationEngine(data).for_sale(today + timedelta(days=1), False, today).iloc[0]
        stale_as_of = today + timedelta(days=20)
        stale = RecommendationEngine(data).for_sale(stale_as_of, True, stale_as_of).iloc[0]
        self.assertIsNone(unverified["forecast"])
        self.assertIsNone(stale["forecast"])

    def test_event_sale_needs_four_comparable_dates_and_shows_the_event_reference(self):
        target = date(2026, 4, 2)
        event_dates = (
            date(2022, 4, 14), date(2023, 4, 6), date(2024, 3, 28), date(2025, 4, 17),
        )
        first = date(2021, 1, 1)
        data = sales_with_events(first, (target - first).days, event_dates)
        engine = RecommendationEngine(data)
        result = engine.for_sale(target, True, target, event={
            "title": "Semana Santa", "history_dates": event_dates,
        }).iloc[0]
        self.assertEqual(result["forecast"], 20.0)
        self.assertTrue(result["eligible_for_auto"])
        self.assertEqual(result["reference_date"], event_dates[-1])
        self.assertEqual(result["reference_qty"], 20.0)
        self.assertIn("celebraciones equivalentes", result["evidence"])
        self.assertEqual(result["audit_windows"], 2)
        self.assertEqual(result["audit_targets"], 2)
        self.assertEqual(result["audit_coverage"], 1.0)
        self.assertIsNotNone(result["audit_mae"])
        self.assertIsNotNone(result["baseline_mae"])
        self.assertIsNotNone(result["audit_overforecast"])
        self.assertIsNotNone(result["baseline_overforecast"])

        insufficient = engine.for_sale(target, True, target, event={
            "title": "Semana Santa", "history_dates": event_dates[:3],
        }).iloc[0]
        self.assertIsNone(insufficient["forecast"])
        self.assertFalse(insufficient["eligible_for_auto"])
        self.assertIn("3 fechas equivalentes", insufficient["evidence"])

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
        data = sales(date(2025, 1, 1), 365, date_semantics="delivery")
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
