"""ETL source drift and validation guardrails, independent of production data."""
from io import BytesIO
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from openpyxl import Workbook

from smartorder.etl import preview_sales
from .models import SalesDataset


class EtlPreviewTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        U = get_user_model()
        cls.admin = U.objects.create_user(username="etl_admin", password="secretpass-1",
                                           is_staff=True)
        cls.seller = U.objects.create_user(username="etl_seller", password="secretpass-1")

    def test_csv_alias_mapping_and_no_mutation(self):
        raw = b"fecha,cliente,producto,cantidad,precio,unidad\n2026-01-10,C1,Alitas,2,5,kg\n"
        report = preview_sales(raw, "venta.csv")
        self.assertTrue(report["ready_to_review"])
        self.assertEqual(report["row_count"], 1)
        self.assertEqual(report["mapping"]["Cantidad_kg_unid"], "cantidad")
        self.assertEqual(report["date_min"], "2026-01-10")
        self.assertEqual(SalesDataset.objects.count(), 0)

    def test_custom_mapping_handles_changed_headers(self):
        raw = b"DiaFactura;Cuenta;Articulo;Volumen;PrecioNeto;TipoUnidad\n10/01/2026;C1;Alitas;2,5;10,00;kg\n"
        report = preview_sales(raw, "nuevo.csv", manual={
            "Fecha": "DiaFactura", "Cliente": "Cuenta", "Producto": "Articulo",
            "Cantidad_kg_unid": "Volumen", "Precio_Unitario_USD": "PrecioNeto",
            "Unidad": "TipoUnidad",
        }, date_format="%d/%m/%Y", decimal_separator=",")
        self.assertTrue(report["ready_to_review"])
        self.assertEqual(report["samples"][0]["Cantidad_kg_unid"], "2.5")

    def test_rejects_ambiguous_dates_and_decimal_comma_with_comma_csv(self):
        raw = b"Fecha,Cliente,Producto,Cantidad,Precio\n10/01/2026,A,B,2,10\n"
        report = preview_sales(raw, "ventas.csv")
        self.assertEqual(report["invalid_rows"], 1)
        self.assertIn("fecha", report["issues"][0])
        with self.assertRaisesRegex(ValueError, "punto y coma"):
            preview_sales(raw, "ventas.csv", decimal_separator=",")

    def test_missing_required_blocks_preview(self):
        report = preview_sales(b"Fecha,Cliente,Producto,Cantidad\n2026-01-10,A,B,2\n",
                               "faltante.csv")
        self.assertFalse(report["ready_to_review"])
        self.assertIn("Precio_Unitario_USD", report["missing_required"])

    def test_reference_duplicates_flagged_and_never_silently_discarded(self):
        raw = ("Fecha,Cliente,Producto,Cantidad,Precio,Referencia_pedido,Referencia_linea\n"
               "2026-01-10,A,B,2,10,P1,L1\n"
               "2026-01-10,A,B,2,10,P1,L1\n").encode()
        report = preview_sales(raw, "ventas.csv")
        self.assertFalse(report["ready_to_review"])
        self.assertEqual(report["duplicate_references"], 1)

    def test_xlsx_preview(self):
        book = Workbook()
        sheet = book.active
        sheet.append(["Fecha", "Cliente", "Producto", "Cantidad", "Precio"])
        sheet.append(["2026-01-10", "A", "B", 2, 10])
        buff = BytesIO()
        book.save(buff)
        self.assertTrue(preview_sales(buff.getvalue(), "ventas.xlsx")["ready_to_review"])

    def test_unknown_xlsx_sheet_is_rejected_not_silently_replaced(self):
        book = Workbook()
        sheet = book.active
        sheet.title = "Ventas reales"
        sheet.append(["Fecha", "Cliente", "Producto", "Cantidad", "Precio"])
        sheet.append(["2026-01-10", "A", "B", 2, 10])
        buffer = BytesIO()
        book.save(buffer)
        with self.assertRaisesRegex(ValueError, "no existe"):
            preview_sales(buffer.getvalue(), "ventas.xlsx", sheet_name="Otra hoja")

    def test_sparse_extreme_dates_reject_forecast_grid_expansion(self):
        raw = (
            "Fecha,Cliente,Producto,Cantidad,Precio\n"
            "2026-01-01,A,B,2,10\n"
            "3026-01-01,A,B,2,10\n"
        ).encode("utf-8")
        result = preview_sales(raw, "ventas.csv")
        self.assertFalse(result["ready_to_review"])
        self.assertEqual(result["row_count"], 2)
        self.assertGreater(result["estimated_daily_cells"], 250_000)
        self.assertIn("límite", " ".join(result["issues"]))

    def test_admin_web_review_and_seller_forbidden(self):
        raw = b"fecha,cliente,producto,cantidad,precio\n2026-01-10,A,B,2,10\n"
        url = reverse("etl_preview")
        self.client.force_login(self.seller)
        self.assertEqual(self.client.get(url).status_code, 403)
        self.assertEqual(self.client.post(url, {"etl_file": SimpleUploadedFile("a.csv", raw)}).status_code, 403)
        self.client.force_login(self.admin)
        response = self.client.post(url, {
            "etl_file": SimpleUploadedFile("a.csv", raw),
            "date_format": "%Y-%m-%d", "decimal_separator": ".",
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Estructura preliminar válida")
        self.assertEqual(SalesDataset.objects.count(), 0)


    def test_versioned_mapping_and_schema_drift_tracking(self):
        import json
        from .models import EtlMappingProfile, EtlInspectionRun
        self.client.force_login(self.admin)
        url = reverse("etl_preview")
        mapping = {"Fecha": "Dia", "Cliente": "Cuenta",
                   "Producto": "Articulo", "Cantidad_kg_unid": "Volumen",
                   "Precio_Unitario_USD": "Tarifa"}
        for i in range(2):
            self.client.post(url, {
                "action": "save_profile", "alias": "ventas-ingeniero",
                "name": "Ventas externas", "mapping": json.dumps(mapping),
                "date_format": "%Y-%m-%d", "decimal_separator": ".",
            })
        profiles = list(EtlMappingProfile.objects.filter(alias="ventas-ingeniero").order_by("version"))
        self.assertEqual([p.version for p in profiles], [1, 2])
        self.assertEqual(profiles[0].mapping, mapping)
        raw1 = b"Dia,Cuenta,Articulo,Volumen,Tarifa\n2026-01-10,A,B,2,10\n"
        raw2 = b"Dia,Cuenta,Articulo,Volumen,Tarifa,Extra\n2026-01-11,A,B,2,10,nuevo\n"
        for payload in (raw1, raw2):
            response = self.client.post(url, {
                "action": "preview", "profile_id": profiles[0].pk,
                "etl_file": SimpleUploadedFile("original.csv", payload),
            })
            self.assertEqual(response.status_code, 200)
        self.assertEqual(EtlInspectionRun.objects.filter(profile=profiles[0]).count(), 2)
        self.assertTrue(EtlInspectionRun.objects.filter(
            profile=profiles[0], schema_changed=True).exists())
        # Repeating the changed schema must remain blocked for this immutable
        # profile version, not become the baseline automatically.
        third = self.client.post(url, {
            "action": "preview", "profile_id": profiles[0].pk,
            "etl_file": SimpleUploadedFile("original.csv", raw2),
        })
        self.assertContains(third, "Cambio de estructura respecto")
        self.assertTrue(EtlInspectionRun.objects.filter(
            profile=profiles[0], schema_changed=True).count() >= 2)
        self.assertEqual(SalesDataset.objects.count(), 0)


    def test_reviewed_etl_loads_csv_into_existing_sales_models(self):
        import json
        from .models import EtlMappingProfile, EtlInspectionRun, EtlLoadRun, SalesLine
        self.client.force_login(self.admin)
        url = reverse("etl_preview")
        mapping = {
            "Fecha": "DiaFactura", "Cliente": "Cuenta", "Producto": "Articulo",
            "Cantidad_kg_unid": "Volumen", "Precio_Unitario_USD": "Tarifa",
            "Unidad": "TipoUnidad", "Referencia_pedido": "Pedido",
            "Referencia_linea": "Linea",
        }
        self.client.post(url, {
            "action": "save_profile", "alias": "ventas-etl",
            "name": "Prueba ETL", "mapping": json.dumps(mapping),
        })
        profile = EtlMappingProfile.objects.get(alias="ventas-etl")
        original = (
            b"DiaFactura,Cuenta,Articulo,Volumen,Tarifa,TipoUnidad,Pedido,Linea\n"
            b"2026-01-10,C1,Alitas,2,5,kg,P1,L1\n"
        )
        response = self.client.post(url, {
            "profile_id": profile.pk, "etl_file": SimpleUploadedFile("ventas.csv", original),
        })
        self.assertEqual(response.status_code, 200)
        inspection = EtlInspectionRun.objects.get(profile=profile)
        self.assertEqual(inspection.issue_count, 0)
        form = {
            "action": "load", "inspection_id": inspection.pk,
            "etl_file": SimpleUploadedFile("ventas.csv", original),
            "provenance": "simulated", "date_semantics": "sale_order",
            "confirmed_origin": "on", "confirmed_full_snapshot": "on",
            "approved_load": "on",
        }
        loaded = self.client.post(url, form)
        self.assertEqual(loaded.status_code, 302)
        dataset = SalesDataset.objects.get(active=True)
        self.assertEqual(dataset.row_count, 1)
        line = SalesLine.objects.get(dataset=dataset)
        self.assertEqual(line.client, "C1")
        self.assertEqual(str(line.quantity), "2.000")
        self.assertEqual(line.source_order_ref, "P1")
        self.assertEqual(EtlLoadRun.objects.get(dataset=dataset).source_sha256,
                         inspection.source_sha256)
        self.assertEqual(SalesDataset.objects.count(), 1)
        retry = self.client.post(url, {
            **form, "etl_file": SimpleUploadedFile("ventas.csv", original),
        })
        self.assertEqual(retry.status_code, 200)
        self.assertEqual(SalesDataset.objects.count(), 1)

    def test_etl_load_rejects_changed_file_and_missing_unit(self):
        import json
        from .models import EtlMappingProfile, EtlInspectionRun
        self.client.force_login(self.admin)
        url = reverse("etl_preview")
        self.client.post(url, {
            "action": "save_profile", "alias": "ventas-carga",
            "name": "Carga controlada",
            "mapping": json.dumps({
                "Fecha": "Fecha", "Cliente": "Cliente", "Producto": "Producto",
                "Cantidad_kg_unid": "Cantidad", "Precio_Unitario_USD": "Precio",
            }),
        })
        profile = EtlMappingProfile.objects.get(alias="ventas-carga")
        original = b"Fecha,Cliente,Producto,Cantidad,Precio\n2026-01-10,A,B,2,10\n"
        self.client.post(url, {
            "profile_id": profile.pk, "etl_file": SimpleUploadedFile("ventas.csv", original),
        })
        inspection = EtlInspectionRun.objects.get(profile=profile)
        common = {
            "action": "load", "inspection_id": inspection.pk,
            "provenance": "real", "date_semantics": "sale_order",
            "confirmed_origin": "on", "confirmed_full_snapshot": "on",
            "approved_load": "on",
        }
        mismatch = self.client.post(url, {
            **common, "etl_file": SimpleUploadedFile(
                "ventas.csv", b"Fecha,Cliente,Producto,Cantidad,Precio\n2026-01-10,A,B,3,10\n"),
        })
        self.assertEqual(mismatch.status_code, 200)
        self.assertEqual(SalesDataset.objects.count(), 0)
        no_unit = self.client.post(url, {
            **common, "etl_file": SimpleUploadedFile("ventas.csv", original),
        })
        self.assertEqual(no_unit.status_code, 200)
        self.assertEqual(SalesDataset.objects.count(), 0)

    def test_etl_load_is_admin_only_even_with_valid_inspection(self):
        from .models import EtlInspectionRun
        self.client.force_login(self.seller)
        response = self.client.post(reverse("etl_preview"), {
            "action": "load", "inspection_id": "1",
            "etl_file": SimpleUploadedFile("ventas.csv", b"irrelevant"),
            "confirmed_origin": "on", "confirmed_full_snapshot": "on",
            "approved_load": "on",
        })
        self.assertEqual(response.status_code, 403)
        self.assertEqual(EtlInspectionRun.objects.count(), 0)


    def test_multiple_units_count_toward_daily_grid_limit(self):
        from smartorder.data import load_sales
        csv_data = (
            "Fecha,Cliente,Producto,Cantidad,Precio,Unidad\n"
            "2026-01-01,A,B,1,2,kg\n"
            "2026-01-01,A,B,1,2,caja\n"
            "2026-01-01,A,B,1,2,unidad\n"
            "2220-01-01,A,B,1,2,bolsa\n"
        ).encode()
        report = preview_sales(csv_data, "multi.csv")
        self.assertFalse(report["ready_to_review"])
        self.assertGreater(report["estimated_daily_cells"], 250_000)
        book = Workbook()
        sheet = book.active
        sheet.append(["Fecha", "Cliente", "Producto", "Cantidad", "Precio", "Unidad"])
        for unit, day in [("kg", "2026-01-01"), ("caja", "2026-01-01"),
                          ("unidad", "2026-01-01"), ("bolsa", "2220-01-01")]:
            sheet.append([day, "A", "B", 1, 2, unit])
        output = BytesIO()
        book.save(output)
        with self.assertRaisesRegex(ValueError, "250,000"):
            load_sales(output.getvalue())

    def test_canonical_xlsx_preserves_untrusted_text_as_literals(self):
        from smartorder.etl import canonical_sales_xlsx
        from smartorder.data import load_sales
        raw = (
            b"Fecha,Cliente,Producto,Cantidad,Precio,Unidad\n"
            b"2026-01-10,=1+1,B,2,5,kg\n"
        )
        canonical, report = canonical_sales_xlsx(raw, "ventas.csv")
        self.assertTrue(report["ready_to_review"])
        book = load_sales(canonical)
        self.assertEqual(book.rows.iloc[0]["Cliente"], "=1+1")
