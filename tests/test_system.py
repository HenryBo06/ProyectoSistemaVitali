from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from decimal import Decimal
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from openpyxl import Workbook, load_workbook

from smartorder.data import HEADERS, load_sales
from smartorder.demo import DEMO_ADMIN, DEMO_SELLER, seed_local_demo
from smartorder.forecast import DemandForecaster
from smartorder.orders import OperationalInput, export_production, suggest_order
from smartorder.storage import Store


def workbook_bytes(records):
    book = Workbook()
    sheet = book.active
    sheet.title = "Ventas2025"
    sheet.append(HEADERS)
    for day, client, product, quantity in records:
        sheet.append((datetime.combine(day, datetime.min.time()), client, "Central",
                      "Cadena Supermercados", "Retail", product, "Pollo",
                      quantity, 2.0, quantity * 2.0))
    output = BytesIO()
    book.save(output)
    return output.getvalue()


class SalesTests(unittest.TestCase):
    def test_duplicate_transactions_and_missing_day(self):
        start = date(2025, 1, 1)
        raw = workbook_bytes([
            (start, "Cliente A", "Pollo", 2),
            (start, "Cliente A", "Pollo", 3),
            (start + timedelta(days=2), "Cliente A", "Pollo", 4),
        ])
        data = load_sales(raw)
        self.assertEqual(len(data.rows), 3)
        self.assertEqual(data.daily()["Cantidad_kg_unid"].tolist(), [5, 0, 4])

    def test_rejects_inconsistent_amount(self):
        raw = bytearray(workbook_bytes([(date(2025, 1, 1), "A", "Pollo", 1)]))
        book = load_workbook(BytesIO(raw))
        book.active["J2"] = 500
        stream = BytesIO()
        book.save(stream)
        with self.assertRaisesRegex(ValueError, "monto no coincide"):
            load_sales(stream.getvalue())

    def test_rejects_corrupt_xlsx(self):
        with self.assertRaisesRegex(ValueError, "XLSX legible"):
            load_sales(b"PKnot-a-real-workbook")

    def test_rejects_daily_grid_that_would_exhaust_memory(self):
        start = date(2020, 1, 1)
        records = [(start, f"Cliente {number}", "Pollo", 1) for number in range(80)]
        records.append((date(2029, 12, 31), "Cliente 0", "Pollo", 1))
        with self.assertRaisesRegex(ValueError, "250,000 combinaciones"):
            load_sales(workbook_bytes(records))

    def test_accepts_business_aliases_and_documents_missing_segments(self):
        book = Workbook()
        sheet = book.active
        sheet.title = "Pedidos_2025"
        sheet.append(("Fecha", "Cliente", "Tipo_Cliente", "Producto", "Categoria",
                      "Cantidad", "Precio_Unitario_USD", "Venta_Total_USD"))
        sheet.append((datetime(2025, 1, 1), "Cliente A", "Distribuidor", "Pollo",
                      "Fresco", 12, 3.5, 42))
        stream = BytesIO()
        book.save(stream)
        data = load_sales(stream.getvalue())
        self.assertEqual(data.rows.iloc[0]["Cantidad_kg_unid"], 12)
        self.assertEqual(data.rows.iloc[0]["Canal_Venta"], "Distribuidor")
        self.assertEqual(data.rows.iloc[0]["Zona_Geografica"], "No especificado")
        self.assertTrue(any("Zona_Geografica" in note for note in data.normalization_notes))


class ForecastTests(unittest.TestCase):
    def test_minimum_coverage_is_96_calendar_days(self):
        start = date(2025, 1, 1)
        records = [(start + timedelta(days=i), "Cliente A", "Pollo", 1)
                   for i in range(96)]
        report = DemandForecaster().run(load_sales(workbook_bytes(records)),
                                        start + timedelta(days=96))
        self.assertEqual(report.validation_windows, 8)

    def test_uses_real_previous_month_when_recent_sales_unavailable(self):
        start = date(2025, 1, 1)
        records = [(start + timedelta(days=i), "Cliente A", "Pollo", 10 if i % 7 == 0 else 0)
                   for i in range(365)]
        data = load_sales(workbook_bytes(records))
        report = DemandForecaster().run(data, date(2026, 9, 25))
        row = report.rows.iloc[0]
        self.assertEqual(row["Metodo"], "Mismo mes del año anterior")
        self.assertAlmostEqual(row["Pronostico_7d"], row["Promedio_semanal_mismo_mes"], places=2)
        self.assertTrue(row["Pronostico_7d"] >= 0)
        self.assertEqual(report.validation_windows, 8)
        self.assertEqual(report.metrics_by_product["Producto"].tolist(), ["Pollo"])

    def test_model_errors_are_reported_separately_by_product(self):
        start = date(2025, 1, 1)
        records = [
            (start + timedelta(days=day), "Cliente A", product, quantity)
            for day in range(365)
            for product, quantity in (("Pollo", 1), ("Huevos", 1000))
        ]
        report = DemandForecaster().run(load_sales(workbook_bytes(records)), date(2026, 1, 1))
        self.assertEqual(set(report.metrics_by_product["Producto"]), {"Pollo", "Huevos"})
        self.assertEqual(len(report.metrics_by_product), 2)
        self.assertIn("Metodo_ganador", report.metrics_by_product)
        self.assertIn("Rango_bajo_7d", report.rows)
        self.assertIn("Rango_alto_7d", report.rows)
        self.assertAlmostEqual(report.feature_importance["Importancia"].sum(), 1.0, places=5)


class DemoTests(unittest.TestCase):
    def test_local_demo_seeds_isolated_roles_and_recent_data(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            store = Store(root / "demo.sqlite3")
            today = date(2026, 9, 25)
            self.assertTrue(seed_local_demo(store, root / "uploads", today))
            self.assertFalse(seed_local_demo(store, root / "uploads", today))
            self.assertEqual(store.authenticate(*DEMO_ADMIN)["role"], "admin")
            seller = store.authenticate(*DEMO_SELLER)
            self.assertEqual(seller["role"], "vendedor")
            self.assertGreaterEqual(len(store.allowed_clients(seller["id"])), 4)
            active = store.active_dataset()
            data = load_sales(active["path"])
            self.assertEqual(data.last_date, today - timedelta(days=1))

    def test_concurrent_demo_seed_is_idempotent(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            database = root / "demo.sqlite3"
            today = date(2026, 9, 25)
            stores = (Store(database), Store(database))
            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(
                    lambda store: seed_local_demo(store, root / "uploads", today),
                    stores,
                ))
            self.assertEqual(sorted(results), [False, True])
            self.assertEqual(Store(database).authenticate(*DEMO_ADMIN)["role"], "admin")
            self.assertEqual(Store(database).authenticate(*DEMO_SELLER)["role"], "vendedor")


class OrderTests(unittest.TestCase):
    def test_rounding_and_zero_demand(self):
        values = OperationalInput.create("A", "Pollo", "kg", 20, 10, 5, 12, 6, date.today())
        self.assertEqual(suggest_order(30, values).suggested, Decimal("12"))
        self.assertEqual(suggest_order(1, values).suggested, Decimal("0"))
        with self.assertRaisesRegex(ValueError, "mayor que cero"):
            OperationalInput.create("A", "Pollo", "kg", 0, 0, 0, 0, 0, date.today())

    def test_excel_export_escapes_formula_text(self):
        row = {"id": 1, "estado": "Aprobado", "cliente": "=HYPERLINK(\"bad\")",
               "producto": "Pollo", "unidad": "kg", "cantidad_produccion": 6,
               "fecha_requerida": "2026-09-25", "fecha_pronostico": "2026-09-25",
               "usuario": "vendedor", "revisor": "admin", "fecha_revision": "2026-09-25",
               "nota_revision": ""}
        book = load_workbook(BytesIO(export_production([row])))
        self.assertEqual(book.active["B2"].data_type, "s")
        self.assertEqual(book["Resumen por producto"]["D2"].value, 6)
        with self.assertRaisesRegex(ValueError, "revisados"):
            export_production([{**row, "estado": "Pendiente"}])


class AccessTests(unittest.TestCase):
    def test_vendor_cannot_use_unassigned_client_or_admin_actions(self):
        with TemporaryDirectory() as folder:
            store = Store(Path(folder) / "test.sqlite3")
            admin = store.bootstrap_admin("admin", "long-admin-password")
            seller = store.create_user(admin, "seller", "long-seller-password", "vendedor")
            store.set_assignments(admin, seller, ["Cliente A"])
            self.assertEqual(store.authenticate("seller", "long-seller-password")["id"], seller)
            with self.assertRaises(PermissionError):
                store.create_user(seller, "another", "another-password", "admin")
            wrong = OperationalInput.create("Cliente B", "Pollo", "kg", 1, 0, 2, 0, 1, date.today())
            with self.assertRaises(PermissionError):
                store.save_operational(seller, wrong)
            right = OperationalInput.create("Cliente A", "Pollo", "kg", 1, 0, 2, 0, 1, date.today())
            store.save_operational(seller, right)
            with self.assertRaisesRegex(ValueError, "Explique"):
                store.save_decision(seller, dataset_digest="abc", client="Cliente A",
                                    product="Pollo", forecast_date=date.today(), forecast=10,
                                    suggested=11, approved=12, operational=right, reason="")

    def test_internal_order_requires_admin_review_before_production(self):
        with TemporaryDirectory() as folder:
            store = Store(Path(folder) / "test.sqlite3")
            admin = store.bootstrap_admin("admin", "long-admin-password")
            seller = store.create_user(admin, "seller", "long-seller-password", "vendedor")
            store.set_assignments(admin, seller, ["Cliente A"])
            operational = OperationalInput.create(
                "Cliente A", "Pollo", "kg", 0, 0, 0, 0, 1, date.today()
            )
            store.save_decision(
                seller, dataset_digest="abc", client="Cliente A", product="Pollo",
                forecast_date=date.today(), forecast=10, suggested=10, approved=10,
                operational=operational, reason="", requested_date=date.today(),
            )
            pending = store.decisions(seller)[0]
            self.assertEqual(pending["estado"], "Pendiente")
            with self.assertRaisesRegex(ValueError, "revisados"):
                export_production([pending])
            with self.assertRaises(PermissionError):
                store.review_decision(seller, pending["id"], "approved", 10, "")
            store.review_decision(admin, pending["id"], "approved", 10, "")
            approved = store.decisions(admin)[0]
            self.assertEqual(approved["estado"], "Aprobado")
            self.assertEqual(load_workbook(BytesIO(export_production([approved]))).active["E2"].value, 10)
            with self.assertRaises(PermissionError):
                store.mark_exported(seller, [pending["id"]])
            store.mark_exported(admin, [pending["id"]])
            self.assertEqual(store.decisions(seller)[0]["estado"], "Exportado")
            with self.assertRaisesRegex(ValueError, "Ya existe"):
                store.save_decision(
                    seller, dataset_digest="abc", client="Cliente A", product="Pollo",
                    forecast_date=date.today(), forecast=10, suggested=10, approved=10,
                    operational=operational, reason="",
                )

    def test_password_reset_revokes_existing_session_version(self):
        with TemporaryDirectory() as folder:
            store = Store(Path(folder) / "test.sqlite3")
            admin = store.bootstrap_admin("admin", "long-admin-password")
            seller = store.create_user(admin, "seller", "long-seller-password", "vendedor")
            old_version = store.authenticate("seller", "long-seller-password")["session_version"]
            store.reset_password(admin, seller, "replacement-password")
            self.assertNotEqual(store.user(seller)["session_version"], old_version)

    def test_newer_request_supersedes_same_client_product_and_week(self):
        with TemporaryDirectory() as folder:
            store = Store(Path(folder) / "test.sqlite3")
            admin = store.bootstrap_admin("admin", "long-admin-password")
            first = store.create_user(admin, "seller1", "long-seller-password", "vendedor")
            second = store.create_user(admin, "seller2", "long-seller-password", "vendedor")
            store.set_assignments(admin, first, ["Cliente A"])
            store.set_assignments(admin, second, ["Cliente A"])
            operational = OperationalInput.create(
                "Cliente A", "Pollo", "kg", 0, 0, 0, 0, 1, date.today()
            )
            for seller in (first, second):
                store.save_decision(
                    seller, dataset_digest="abc", client="Cliente A", product="Pollo",
                    forecast_date=date.today(), forecast=10, suggested=10,
                    approved=10, operational=operational, reason="",
                )
            orders = store.decisions(admin)
            self.assertEqual([row["estado"] for row in orders], ["Pendiente", "Sustituido"])
            with self.assertRaisesRegex(ValueError, "sustituido"):
                store.review_decision(admin, orders[1]["id"], "approved", 10, "")

    def test_replaced_excel_blocks_old_order_review_and_export(self):
        with TemporaryDirectory() as folder:
            store = Store(Path(folder) / "test.sqlite3")
            admin = store.bootstrap_admin("admin", "long-admin-password")
            seller = store.create_user(admin, "seller", "long-seller-password", "vendedor")
            store.set_assignments(admin, seller, ["Cliente A"])
            stock = OperationalInput.create("Cliente A", "Pollo", "kg", 0, 0, 0, 0,
                                            1, date.today())
            store.activate_dataset(admin, "old", "old.xlsx", "old.xlsx",
                                   "2025-01-01", "2025-12-31", 365)
            store.save_decision(seller, dataset_digest="old", client="Cliente A",
                                product="Pollo", forecast_date=date.today(), forecast=10,
                                suggested=10, approved=10, operational=stock, reason="")
            order_id = store.decisions(admin)[0]["id"]
            store.review_decision(admin, order_id, "approved", 10, "")
            store.activate_dataset(admin, "new", "new.xlsx", "new.xlsx",
                                   "2026-01-01", "2026-12-31", 365)
            self.assertEqual(store.decisions(admin)[0]["estado"], "Fuente sustituida")
            with self.assertRaisesRegex(ValueError, "lote cambió"):
                store.mark_exported(admin, [order_id])
            with self.assertRaisesRegex(ValueError, "Excel activo cambió"):
                store.save_decision(seller, dataset_digest="old", client="Cliente A",
                                    product="Pollo", forecast_date=date.today(), forecast=10,
                                    suggested=10, approved=10, operational=stock, reason="")
            store.save_decision(seller, dataset_digest="new", client="Cliente A",
                                product="Pollo", forecast_date=date.today(), forecast=10,
                                suggested=10, approved=10, operational=stock, reason="")
            self.assertEqual(store.decisions(admin)[0]["estado"], "Pendiente")

    def test_overlapping_forecasts_cannot_create_duplicate_production_orders(self):
        with TemporaryDirectory() as folder:
            store = Store(Path(folder) / "test.sqlite3")
            admin = store.bootstrap_admin("admin", "long-admin-password")
            seller = store.create_user(admin, "seller", "long-seller-password", "vendedor")
            store.set_assignments(admin, seller, ["Cliente A"])
            start = date.today()
            stock = OperationalInput.create("Cliente A", "Pollo", "kg", 0, 0, 0, 0, 1, start)
            store.save_decision(seller, dataset_digest="abc", client="Cliente A",
                                product="Pollo", forecast_date=start, forecast=10,
                                suggested=10, approved=10, operational=stock, reason="")
            with self.assertRaisesRegex(ValueError, "superpuestos"):
                store.save_decision(seller, dataset_digest="abc", client="Cliente A",
                                    product="Pollo", forecast_date=start + timedelta(days=1),
                                    forecast=10, suggested=10, approved=10,
                                    operational=stock, reason="")
            order_id = store.decisions(admin)[0]["id"]
            store.review_decision(admin, order_id, "approved", 10, "")
            with self.assertRaisesRegex(ValueError, "superpuestos"):
                store.save_decision(seller, dataset_digest="abc", client="Cliente A",
                                    product="Pollo", forecast_date=start + timedelta(days=6),
                                    forecast=10, suggested=10, approved=10,
                                    operational=stock, reason="")
            store.save_decision(seller, dataset_digest="abc", client="Cliente A",
                                product="Pollo", forecast_date=start + timedelta(days=7),
                                forecast=10, suggested=10, approved=10,
                                operational=stock, reason="")

    def test_exported_order_still_blocks_overlap_after_excel_change(self):
        with TemporaryDirectory() as folder:
            store = Store(Path(folder) / "test.sqlite3")
            admin = store.bootstrap_admin("admin", "long-admin-password")
            seller = store.create_user(admin, "seller", "long-seller-password", "vendedor")
            store.set_assignments(admin, seller, ["Cliente A"])
            start = date.today()
            stock = OperationalInput.create("Cliente A", "Pollo", "kg", 0, 0, 0, 0, 1, start)
            for digest in ("old", "new"):
                store.activate_dataset(admin, digest, digest + ".xlsx", digest + ".xlsx",
                                       "2025-01-01", "2025-12-31", 365)
                if digest == "old":
                    store.save_decision(seller, dataset_digest=digest, client="Cliente A",
                                        product="Pollo", forecast_date=start, forecast=10,
                                        suggested=10, approved=10, operational=stock, reason="")
                    order_id = store.decisions(admin)[0]["id"]
                    store.review_decision(admin, order_id, "approved", 10, "")
                    store.mark_exported(admin, [order_id])
            with self.assertRaisesRegex(ValueError, "superpuestos"):
                store.save_decision(seller, dataset_digest="new", client="Cliente A",
                                    product="Pollo", forecast_date=start + timedelta(days=1),
                                    forecast=10, suggested=10, approved=10,
                                    operational=stock, reason="")


if __name__ == "__main__":
    unittest.main()
