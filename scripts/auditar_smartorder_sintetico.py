"""Ejecuta escenarios avicolas LAB 100 % simulados; genera evidencia local opcional.

No ingiere archivos externos ni usa credenciales. No modificar la VM.
"""
from __future__ import annotations

import argparse
from datetime import date
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from smartorder.synthetic_lab import FIELDNAMES, run_audit, serialize_csv
from smartorder.synthetic_report import render_html


def export_xlsx(rows, path, report):
    from openpyxl import Workbook

    workbook = Workbook(write_only=True)
    sales = workbook.create_sheet("Ventas_SIMULADAS")
    sales.append(FIELDNAMES)
    for row in rows:
        raw = row.row()
        sales.append([row.day if key == "Fecha" else (
            int(raw[key]) if key == "Cantidad_kg_unid" else
            float(raw[key]) if key in ("Precio_Unitario_USD", "Monto_Venta_USD") else
            raw[key]
        ) for key in FIELDNAMES])
    origin = workbook.create_sheet("Origen_SIMULADO")
    origin.append(("Caracteristica", "Descripcion"))
    for label, value in (
        ("Procedencia", "100% SIMULADO; datos creados por codigo, no provienen de Vitali."),
        ("Regla fecha", "Fecha de venta/pedido simulado; NO fecha de entrega fisica."),
        ("Unidad", "kg, lb y unidad segun catalogo ficticio, sin conversiones."),
        ("Cobertura", "DESCONOCIDA; dias sin ventas registradas no prueban demanda cero."),
        ("Huella CSV", report["huella_csv_sha256"]),
        ("Advertencia", "NO usar como dato empresarial verificado ni autorizar pedidos automaticos."),
    ):
        origin.append((label, value))
    workbook.save(path)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, default=20261103)
    parser.add_argument("--clients", type=int, default=12)
    parser.add_argument("--products", type=int, default=8)
    parser.add_argument("--days", type=int, default=630)
    parser.add_argument("--as-of", default="2026-11-01",
                        help="Primera fecha EXCLUIDA del historico, AAAA-MM-DD.")
    parser.add_argument("--evaluation-client-limit", type=int, default=24)
    parser.add_argument("--output-dir", default=".local-synthetic-audit")
    parser.add_argument("--export-csv", action="store_true")
    parser.add_argument("--export-xlsx", action="store_true")
    options = parser.parse_args()
    try:
        cutoff = date.fromisoformat(options.as_of)
        report, rows = run_audit(
            seed=options.seed, clients=options.clients,
            products=options.products, days=options.days, as_of=cutoff,
            evaluation_client_limit=options.evaluation_client_limit,
        )
        output = Path(options.output_dir).resolve()
        output.mkdir(parents=True, exist_ok=True)
        (output / "smartorder_auditoria_SINTETICA.json").write_text(
            json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8",
        )
        (output / "smartorder_informe_SINTETICO.html").write_text(
            render_html(report), encoding="utf-8",
        )
        if options.export_csv:
            (output / "ventas_SINTETICAS.csv").write_bytes(serialize_csv(rows))
        if options.export_xlsx:
            # The current importer caps the product-client-day grid at 250k cells.
            if options.clients * options.products * options.days > 250000:
                raise ValueError("XLSX demasiado grande para importador actual (250k celdas); use solo JSON/CSV para estres")
            export_xlsx(rows, output / "ventas_SINTETICAS.xlsx", report)
    except ValueError as exc:
        parser.error(str(exc))
    totals = report["priorizacion_retroactiva"]
    print(f"LAB SIMULADO OK | {len(rows)} registros | {len(report['calidad']['ocho_anomalias_aisladas_en_cuarentena'])} tipos de anomalias aisladas")
    for horizon in ("7", "30"):
        value = totals[horizon]["total"]
        print(f"Hit@1 {horizon}d (condicional): adaptativo={value['hit1_adaptativo']}, historico={value['hit1_historico']}, escenarios={value['escenarios_evaluados']}")
    print(f"Huella SHA-256: {report['huella_csv_sha256']}")
    print(f"Informe: {output / 'smartorder_auditoria_SINTETICA.json'}")


if __name__ == "__main__":
    main()
