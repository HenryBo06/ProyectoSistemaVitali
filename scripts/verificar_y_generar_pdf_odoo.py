"""Generate and check native Odoo PDFs in its shell; leave the seed unchanged."""
from datetime import datetime, timezone
from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
import re
import subprocess

from odoo.tools.pdf import PdfReader


assert env.cr.dbname == "vitali_lab", "Only the independent synthetic lab is allowed"
root = Path.cwd().resolve()
assert (root / "odoo_addons/vitali_lab/__manifest__.py").is_file(), "Run from the project root"
config = env["ir.config_parameter"].sudo()
sale = env["sale.order"].browse(int(config.get_param("vitali_lab.sale_id"))).exists()
production = env["mrp.production"].browse(int(config.get_param("vitali_lab.manufacturing_id"))).exists()
assert sale and production and sale.state == "sale" and production.state == "draft"
assert sale.order_line.product_uom_qty == 8 and production.product_qty == 3
assert production.sale_line_id.order_id == sale
original_invoices = sale.invoice_ids.ids
original_deliveries = {p.id: p.state for p in sale.picking_ids}
original_language = sale.partner_id.lang
original_logo = sale.company_id.logo
renderer = env["ir.actions.report"].with_user(env.ref("base.user_admin")).with_context(
    lang="es_ES", report_pdf_no_attachment=True)
documents = []
try:
    sale.partner_id.lang = "es_ES"
    sale.company_id.logo = False  # no official logo was supplied; avoid the native placeholder.
    sale_pdf, sale_type = renderer._render_qweb_pdf("sale.action_report_saleorder", sale.ids)
    assert sale_type == "pdf"
    production.action_confirm()
    production.action_assign()
    lot = env["stock.lot"].create({"name": "LAB-PDF-ROLLBACK", "product_id": production.product_id.id,
                                  "company_id": production.company_id.id})
    production.write({"lot_producing_ids": [(6, 0, lot.ids)], "qty_producing": 3})
    production._set_qty_producing()
    production.move_raw_ids.write({"picked": True})
    production.button_mark_done()
    assert production.state == "done" and abs(production.qty_produced - 3) < .001
    picking = sale.picking_ids.filtered(lambda p: p.state != "cancel")
    picking.action_assign()
    assert all(abs(m.quantity - m.product_uom_qty) < .001 for m in picking.move_ids)
    picking.move_ids.write({"picked": True})
    picking.button_validate()
    assert picking.state == "done" and sale.delivery_status == "full"
    invoice = sale._create_invoices()
    invoice.narration = "SIMULADO. Fabricación autorizada y entrega de 8 kg completada para esta prueba. Sin validez fiscal."
    invoice.action_post()
    assert invoice.state == "posted" and abs(invoice.amount_total - 30.4) < .001
    invoice_pdf, invoice_type = renderer._render_qweb_pdf("account.account_invoices", invoice.ids)
    assert invoice_type == "pdf"
    for filename, content, reference in (
        ("venta_vitali_simulada.pdf", sale_pdf, sale.name),
        ("factura_vitali_simulada.pdf", invoice_pdf, invoice.name),
    ):
        assert content.startswith(b"%PDF-") and b"%%EOF" in content[-1024:]
        reader = PdfReader(BytesIO(content))
        assert not reader.is_encrypted and reader.pages
        text = "\n".join(page.extract_text() or "" for page in reader.pages)
        assert "Vitali" in text and reference in text and "LAB" in text, "Missing native document content"
        assert "Your logo" not in text and ("Pedido" in text or "Factura" in text), "Keep the reports in Spanish without placeholders"
        assert re.search(r"30[.,]40", "".join(text.split())), "Missing USD 30.40 total"
        documents.append((filename, content, text, len(reader.pages), reference))
finally:
    # ponytail: use native reports/workflow in one reverted lab transaction, without report attachments.
    env.cr.rollback()
    env.invalidate_all()

assert sale.state == "sale" and production.state == "draft"
assert sale.invoice_ids.ids == original_invoices
assert {p.id: p.state for p in sale.picking_ids} == original_deliveries
assert sale.partner_id.lang == original_language and sale.company_id.logo == original_logo
output = root / ".local-odoo/evidence/pdf"
output.mkdir(parents=True, exist_ok=True)
metadata = {"generated_at": datetime.now(timezone.utc).isoformat(), "database": "vitali_lab",
            "native_reports": True, "seed_preserved": True, "fiscal_validation": False, "documents": []}
for filename, content, text, pages, reference in documents:
    (output / filename).write_bytes(content)
    (output / (Path(filename).stem + ".txt")).write_text(text, encoding="utf-8")
    metadata["documents"].append({"file": filename, "pages": pages, "reference": reference,
                                  "sha256": sha256(content).hexdigest(), "total_usd": "30.40"})
wkhtml = root / ".local-odoo/wkhtmltopdf/bin/wkhtmltopdf.exe"
metadata["renderer"] = subprocess.run([str(wkhtml), "--version"], check=True, capture_output=True, text=True).stdout.strip()
(output / "comprobacion.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
print("PASS: two native PDFs, USD 30.40, readable pages, separate production authorization; seed preserved")
print(output)
