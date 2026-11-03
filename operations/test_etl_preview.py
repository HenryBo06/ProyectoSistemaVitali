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
        self.assertEqual(SalesDataset.objects.count(), 0)

