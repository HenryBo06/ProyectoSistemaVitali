"""Contrato independiente de privacidad, invariantes y evaluacion del LAB."""
import csv
from datetime import date, timedelta
from hashlib import sha256
from io import BytesIO, StringIO
import unittest

from smartorder.product_priority import evaluate_product_priority, _order
from smartorder.synthetic_lab import (
    DEFAULT_AS_OF, SCENARIOS, UNITS, catalog, generate,
    injected_adversarial_cases, quarantine, rolling_backtest,
    run_audit, serialize_csv, transaction_quantity_diagnostic,
)


class AdvancedSyntheticLabTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rows, cls.units = generate(
            seed=123, clients=8, products=8, days=490, as_of=DEFAULT_AS_OF,
        )

    def test_only_fictitious_names_and_fixed_units(self):
        self.assertEqual(set(self.units.values()), set(UNITS))
        self.assertEqual(len({row.client for row in self.rows}), 8)
        self.assertEqual(len({row.product for row in self.rows}), 8)
        self.assertEqual({row.scenario for row in self.rows}, set(SCENARIOS))
        for row in self.rows:
            self.assertTrue(row.client.startswith("Cliente LAB-"))
            self.assertTrue(row.product.startswith("Producto LAB-"))
            self.assertEqual(row.unit, self.units[row.product])
            self.assertLess(row.day, DEFAULT_AS_OF)
            self.assertGreater(row.quantity, 0)
            self.assertGreater(row.price_cents, 0)
            self.assertEqual(row.row()["Monto_Venta_USD"],
                             f"{row.quantity * row.price_cents / 100:.2f}")

    def test_reproducible_seed_and_nontrivial_randomness(self):
        same, _ = generate(seed=123, clients=8, products=8, days=490)
        other, _ = generate(seed=124, clients=8, products=8, days=490)
        self.assertEqual(sha256(serialize_csv(self.rows)).digest(),
                         sha256(serialize_csv(same)).digest())
        self.assertNotEqual(sha256(serialize_csv(self.rows)).digest(),
                            sha256(serialize_csv(other)).digest())

    def test_every_adversarial_row_is_quarantined(self):
        dirty = injected_adversarial_cases(self.rows, DEFAULT_AS_OF)
        clean, rejected = quarantine([*self.rows, *dirty], self.units,
                                     as_of=DEFAULT_AS_OF)
        self.assertEqual(clean, self.rows)
        self.assertEqual(sum(rejected.values()), 8)
        self.assertEqual(len(rejected), 8)
        self.assertTrue(all(value == 1 for value in rejected.values()))

    def test_legitimate_multiple_orders_in_same_day_are_not_removed(self):
        pairs = {}
        for row in self.rows:
            key = row.client, row.product, row.day
            pairs[key] = pairs.get(key, 0) + 1
        self.assertTrue(any(value > 1 for value in pairs.values()))
        self.assertEqual(len({row.line_ref for row in self.rows}), len(self.rows))

    def test_seasonal_holiday_pattern_is_stronger_in_november_december(self):
        seasonal = [row for row in self.rows if row.scenario == "temporada_alta"
                    and row.product.endswith("03 (SIMULADO)")]
        high = sum(row.day.month in (11, 12) for row in seasonal)
        other = len(seasonal) - high
        self.assertGreater(high / 61, other / (490 - 61))

    def test_customer_preference_regime_shift(self):
        shift = DEFAULT_AS_OF - timedelta(days=490) + timedelta(days=int(490 * .53))
        changing = [r for r in self.rows if r.scenario == "cambio_preferencia"]
        before_a = sum(r.day < shift and r.product.endswith("01 (SIMULADO)") for r in changing)
        before_b = sum(r.day < shift and r.product.endswith("02 (SIMULADO)") for r in changing)
        after_a = sum(r.day >= shift and r.product.endswith("01 (SIMULADO)") for r in changing)
        after_b = sum(r.day >= shift and r.product.endswith("02 (SIMULADO)") for r in changing)
        self.assertGreater(before_a, before_b)
        self.assertGreater(after_b, after_a)

    def test_unknown_coverage_window_is_not_filled_with_fake_sales(self):
        closed = [r for r in self.rows if r.scenario == "cierre_sin_cobertura"]
        first = DEFAULT_AS_OF - timedelta(days=490)
        start = first + timedelta(days=int(490 * .55))
        end = start + timedelta(days=21)
        self.assertFalse(any(start <= r.day < end for r in closed))

    def test_rolling_test_set_is_after_training_and_no_future_peeking(self):
        report = rolling_backtest(self.rows, as_of=DEFAULT_AS_OF, horizon=7)
        first = min(row.day for row in self.rows)
        self.assertGreaterEqual(date.fromisoformat(
            report["inicio_reserva_fuera_de_entrenamiento"]
        ), first + timedelta(days=int(490 * .65)))
        self.assertGreater(report["total"]["escenarios_evaluados"], 0)
        self.assertGreater(report["total"]["clientes_evaluados"], 0)
        for method in ("hit1_adaptativo", "hit1_historico", "hit1_reciente",
                       "hit3_adaptativo", "aleatorio_esperado"):
            self.assertGreaterEqual(report["total"][method], 0)
            self.assertLessEqual(report["total"][method], 1)
        client = self.rows[0].client
        cutoff = DEFAULT_AS_OF - timedelta(days=95)
        full = [(r.day, r.product) for r in self.rows if r.client == client]
        known = sorted({p for d, p in full if d < cutoff})
        historical = _order(full, known, cutoff, "historical")
        before = evaluate_product_priority(full, as_of=cutoff, products=historical)
        injected = full + [(cutoff + timedelta(days=i), historical[-1]) for i in range(300)]
        after = evaluate_product_priority(injected, as_of=cutoff, products=historical)
        self.assertEqual(before, after)

    def test_quantity_metrics_stay_separate_by_unit_and_are_transaction_only(self):
        result = transaction_quantity_diagnostic(self.rows)
        self.assertEqual(set(result), set(UNITS))
        for unit in UNITS:
            self.assertGreater(result[unit]["transacciones_evaluadas"], 0)
            self.assertGreaterEqual(result[unit]["mae_mediana_4"], 0)
            self.assertGreaterEqual(result[unit]["mae_ultima"], 0)

    def test_report_does_not_claim_verified_company_data(self):
        report, rows = run_audit(
            seed=19, clients=8, products=8, days=420, as_of=DEFAULT_AS_OF,
        )
        self.assertFalse(report["datos_empresariales_utilizados"])
        self.assertTrue(report["nunca_importa_xlsx_real"])
        self.assertEqual(report["calidad"]["filas_validas"], len(rows))
        self.assertEqual(sum(report["calidad"]["ocho_anomalias_aisladas_en_cuarentena"].values()), 8)
        self.assertEqual(set(report["calidad"]["catalogo_unidades"].values()), set(UNITS))
        self.assertTrue(report["invariantes"]["sin_fuga_temporal"])
        self.assertTrue(report["invariantes"]["no_hay_pedidos_automaticos"])
        self.assertIn("NO VERIFICADA", report["cobertura_calendario"])

    def test_canonical_csv_has_no_unsafe_or_missing_references(self):
        text = serialize_csv(self.rows).decode("utf-8")
        reader = csv.DictReader(StringIO(text))
        self.assertEqual(reader.fieldnames[0:2], ["Fecha", "Cliente"])
        parsed = list(reader)
        self.assertEqual(len(parsed), len(self.rows))
        self.assertEqual(len({row["Referencia_linea"] for row in parsed}), len(parsed))
        self.assertTrue(all(row["Monto_Venta_USD"] for row in parsed))

    def test_offline_visual_report_is_aggregate_only_and_warns_of_saturation(self):
        from smartorder.synthetic_report import render_html
        report, rows = run_audit(
            seed=11, clients=8, products=8, days=420,
        )
        html = render_html(report)
        self.assertIn("NO SON DATOS EMPRESARIALES", html)
        self.assertIn("escenarios", html.lower())
        self.assertIn("kg", html)
        self.assertIn("SHA-256", html)
        self.assertNotIn("<script", html.lower())
        self.assertNotIn("https://", html)
        self.assertNotIn(rows[0].client, html)
        self.assertIn(report["huella_csv_sha256"], html)

    def test_invalid_parameters_are_rejected(self):
        with self.assertRaises(ValueError):
            generate(clients=2)
        with self.assertRaises(ValueError):
            generate(days=10)
        with self.assertRaises(ValueError):
            catalog(40)
        with self.assertRaises(ValueError):
            rolling_backtest(self.rows, as_of=DEFAULT_AS_OF, horizon=365)

    def test_xlsx_is_loadable_but_still_unverified_for_auto(self):
        from openpyxl import load_workbook
        from smartorder.data import load_sales
        from smartorder.recommendations import RecommendationEngine
        from scripts.auditar_smartorder_sintetico import export_xlsx
        from tempfile import TemporaryDirectory
        from pathlib import Path

        small, _ = generate(seed=9, clients=3, products=3, days=210)
        with TemporaryDirectory() as location:
            path = Path(location) / "ventas_SIMULADAS.xlsx"
            export_xlsx(small, path, {"huella_csv_sha256": "0" * 64})
            book = load_workbook(path, read_only=True, data_only=True)
            self.assertIn("Origen_SIMULADO", book.sheetnames)
            book.close()
            source = load_sales(path, provenance="simulated", date_semantics="sale_order")
            self.assertEqual(len(source.rows), len(small))
            self.assertEqual(source.coverage, {})
            output = RecommendationEngine(source).for_sale(
                DEFAULT_AS_OF, source_verified=False, as_of=DEFAULT_AS_OF,
            )
            self.assertFalse(output["eligible_for_auto"].any())
            self.assertTrue(output["quantity"].isna().all())
            self.assertTrue(output["forecast"].isna().all())


if __name__ == "__main__":
    unittest.main()
