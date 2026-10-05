"""Run with odoo-bin shell: seed only the dedicated synthetic Vitali database."""
import json
from pathlib import Path
from datetime import datetime, timedelta

assert env.cr.dbname == "vitali_lab", "Only the separate laboratory may be seeded"
config = env["ir.config_parameter"].sudo()
if config.get_param("vitali_lab.seed_version"):
    print("Vitali laboratory already prepared; records preserved")
else:
    credentials = json.loads(Path(".local-odoo/credentials.json").read_text(encoding="utf-8"))
    env["res.lang"]._activate_and_install_lang("es_ES")
    company = env.company
    company.write({"name": "Vitali · Laboratorio simulado", "currency_id": env.ref("base.USD").id,
                   "country_id": env.ref("base.sv").id})
    env["account.chart.template"].try_loading("generic_coa", company, install_demo=False)
    # ponytail: generic accounts and no taxes for synthetic checks, not a fiscal SV setup.
    company.write({"account_fiscal_country_id": env.ref("base.sv").id})
    admin = env.ref("base.user_admin")
    admin.write({"login": "vitali_admin", "name": "Administrador Vitali · Laboratorio",
                 "password": credentials["admin"], "lang": "es_ES", "tz": "America/El_Salvador"})
    config.set_param("web.base.url", "http://127.0.0.1:8079")
    config.set_param("web.base.url.freeze", "True")
    warehouse = env.ref("stock.warehouse0")
    warehouse.write({"name": "Vitali · Bodega de pruebas", "code": "VIT"})
    customer = env["res.partner"].create({"name": "Cliente Vitali LAB-001 (simulado)", "customer_rank": 1,
                                           "user_id": admin.id, "country_id": company.country_id.id})
    env["res.partner"].create({"name": "Proveedor Vitali LAB-001 (simulado)", "supplier_rank": 1})
    kg = env.ref("uom.product_uom_kgm")
    raw = env["product.product"].create({"name": "Insumo avícola LAB (simulado)", "default_code": "VIT-LAB-MP-001",
        "type": "consu", "is_storable": True, "tracking": "lot", "uom_id": kg.id,
        "list_price": 2, "standard_price": 1.5, "taxes_id": [(5, 0, 0)], "supplier_taxes_id": [(5, 0, 0)]})
    finished = env["product.product"].create({"name": "Producto avícola LAB (simulado)", "default_code": "VIT-LAB-PT-001",
        "type": "consu", "is_storable": True, "tracking": "lot", "uom_id": kg.id,
        "list_price": 4, "standard_price": 2, "taxes_id": [(5, 0, 0)], "supplier_taxes_id": [(5, 0, 0)]})
    bom = env["mrp.bom"].create({"product_tmpl_id": finished.product_tmpl_id.id, "product_qty": 1,
        "product_uom_id": kg.id, "type": "normal", "bom_line_ids": [(0, 0, {"product_id": raw.id,
        "product_qty": 1.15, "product_uom_id": kg.id})]})
    for product, quantity, lot_name in ((raw, 20, "LAB-MP-001"), (finished, 5, "LAB-PT-001")):
        lot = env["stock.lot"].create({"name": lot_name, "product_id": product.id, "company_id": company.id})
        quant = env["stock.quant"].with_context(inventory_mode=True).create({"product_id": product.id,
            "location_id": warehouse.lot_stock_id.id, "lot_id": lot.id, "inventory_quantity": quantity})
        quant.action_apply_inventory()
    sale = env["sale.order"].create({"partner_id": customer.id, "user_id": admin.id,
        "client_order_ref": "VIT-LAB-VENTA-001", "commitment_date": datetime.now() + timedelta(days=3),
        "note": "SIMULADO. Faltan 3 kg; producción pendiente de autorización administrativa.",
        "order_line": [(0, 0, {"product_id": finished.id, "product_uom_qty": 8,
            "product_uom_id": kg.id, "price_unit": 4, "discount": 5})]})
    sale.action_confirm()
    assert not env["mrp.production"].search_count([("sale_line_id", "in", sale.order_line.ids)]), "Sale must not authorize production"
    manufacturing = env["mrp.production"].create({"product_id": finished.id, "product_qty": 3,
        "product_uom_id": kg.id, "bom_id": bom.id, "picking_type_id": warehouse.manu_type_id.id,
        "origin": sale.name, "sale_line_id": sale.order_line.id, "user_id": admin.id})
    assert manufacturing.state == "draft"
    env["maintenance.equipment"].create({"name": "Equipo de proceso LAB (simulado)"})
    env["ir.attachment"].create({"name": "trazabilidad-laboratorio.txt", "raw": b"VITALI LAB: synthetic evidence; no business data.",
        "res_model": "res.company", "res_id": company.id, "mimetype": "text/plain"})
    config.set_param("vitali_lab.seed_version", "1")
    config.set_param("vitali_lab.sale_id", sale.id)
    config.set_param("vitali_lab.manufacturing_id", manufacturing.id)
    env.cr.commit()
    print(f"Synthetic laboratory prepared: sale {sale.name}; manufacturing {manufacturing.name} remains draft")
