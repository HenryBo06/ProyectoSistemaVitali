"""Small native-operation check; run in Odoo shell. All test mutations roll back."""
assert env.cr.dbname == "vitali_lab", "This check is only for the synthetic lab"
config = env["ir.config_parameter"].sudo()
sale = env["sale.order"].browse(int(config.get_param("vitali_lab.sale_id")))
production = env["mrp.production"].browse(int(config.get_param("vitali_lab.manufacturing_id")))
assert sale.state == "sale" and production.state == "draft", "Keep the seed pending authorization"
assert sale.order_line.product_uom_qty == 8 and production.product_qty == 3
assert production.sale_line_id.order_id == sale
assert env.ref("base.user_admin").has_group("mrp.group_mrp_manager")
assert env["res.users"].search_count([("share", "=", False), ("id", "!=", 1)]) == 1
assert env["ir.module.module"].search_count([("name", "in", ["vitali_lab", "sale_management", "purchase_stock", "mrp", "maintenance", "account"]), ("state", "=", "installed")]) == 6

try:
    production.action_confirm()
    production.action_assign()
    assert production.state == "confirmed"
    lot = env["stock.lot"].create({"name": "LAB-CHECK-ROLLBACK", "product_id": production.product_id.id,
                                  "company_id": production.company_id.id})
    production.write({"lot_producing_ids": [(6, 0, lot.ids)], "qty_producing": 3})
    production._set_qty_producing()
    for move in production.move_raw_ids:
        assert abs(move.quantity - move.product_uom_qty) < .001
        move.picked = True
    production.button_mark_done()
    assert production.state == "done", "Manufacturing must post stock movements"
    assert abs(production.qty_produced - 3) < .001
    picking = sale.picking_ids.filtered(lambda p: p.state != "cancel")
    picking.action_assign()
    assert all(abs(m.quantity - m.product_uom_qty) < .001 for m in picking.move_ids)
    picking.move_ids.write({"picked": True})
    picking.button_validate()
    assert picking.state == "done" and sale.delivery_status == "full"
    invoice = sale._create_invoices()
    invoice.action_post()
    assert invoice.state == "posted" and abs(invoice.amount_total - 30.4) < .001
    print("PASS: separate authorization -> manufacture 3 kg -> deliver 8 kg -> invoice USD 30.40")
finally:
    # ponytail: real native workflow checked in one rolled-back transaction; seed stays reviewable.
    env.cr.rollback()
