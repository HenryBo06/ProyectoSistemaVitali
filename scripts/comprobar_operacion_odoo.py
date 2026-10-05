"""Native Community 19 operational checks, restricted to the synthetic lab.

Run through odoo-bin shell; business records always roll back. Evidence is local JSON.
"""
import json
from datetime import datetime, timedelta
from pathlib import Path
from uuid import uuid4

from odoo import api
from odoo.exceptions import LockError

assert env.cr.dbname == "vitali_lab", "Synthetic laboratory only"
root = Path.cwd()
tag = "LAB-CHECK-" + uuid4().hex[:10]
results = []
company = env.company
warehouse = env.ref("stock.warehouse0")
kg = env.ref("uom.product_uom_kgm")
admin = env.ref("base.user_admin")
config = env["ir.config_parameter"].sudo()
seed_sale_id = int(config.get_param("vitali_lab.sale_id"))
seed_mo_id = int(config.get_param("vitali_lab.manufacturing_id"))


def close(left, right):
    assert abs(left - right) < .001, (left, right)


def passed(name, **values):
    results.append({"scenario": name, "status": "passed", **values})
    print("PASS:", name, json.dumps(values, ensure_ascii=True))


def product(name, code, cost, price):
    return env["product.product"].create({
        "name": tag + " " + name, "default_code": tag + code,
        "type": "consu", "is_storable": True, "tracking": "lot", "uom_id": kg.id,
        "standard_price": cost, "list_price": price,
        "taxes_id": [(5, 0, 0)], "supplier_taxes_id": [(5, 0, 0)],
        "use_expiration_date": True, "expiration_time": 30,
        "removal_time": 1, "alert_time": 2,
    })


def finish_picking(picking):
    picking.move_ids.write({"picked": True})
    action = picking.button_validate()
    if isinstance(action, dict) and action.get("res_model") == "stock.backorder.confirmation":
        env[action["res_model"]].with_context(**action.get("context", {})).create({}).process()
    assert picking.state == "done", (picking.name, picking.state, action)


def make_move(test_env, prod, qty, source, destination):
    move = test_env["stock.move"].create({
        "product_id": prod.id, "product_uom_qty": qty,
        "product_uom": prod.uom_id.id, "location_id": source.id,
        "location_dest_id": destination.id, "company_id": company.id,
    })
    move._action_confirm()
    move._action_assign()
    return move


def manufacture(production, quantity, lot_name):
    lot = env["stock.lot"].create({"name": tag + lot_name,
        "product_id": production.product_id.id, "company_id": company.id})
    production.write({"lot_producing_ids": [(6, 0, lot.ids)], "qty_producing": quantity})
    production._set_qty_producing()
    production.move_raw_ids.write({"picked": True})
    action = production.button_mark_done()
    if isinstance(action, dict) and action.get("res_model") == "mrp.production.backorder":
        wizard = env[action["res_model"]].with_context(**action.get("context", {})).create({})
        wizard.action_backorder()
    assert production.state == "done", (production.name, production.state, action)
    close(production.qty_produced, quantity)
    return lot


try:
    supplier = env["res.partner"].create({"name": tag + " supplier", "supplier_rank": 1})
    customer = env["res.partner"].create({"name": tag + " customer", "customer_rank": 1,
                                        "user_id": admin.id})
    raw = product("raw material", "-MP", 2, 3)
    finished = product("finished product", "-PT", 2.5, 4)
    bom = env["mrp.bom"].create({"product_tmpl_id": finished.product_tmpl_id.id,
        "product_qty": 1, "product_uom_id": kg.id, "type": "normal",
        "bom_line_ids": [(0, 0, {"product_id": raw.id, "product_qty": 1.15,
                                 "product_uom_id": kg.id})]})

    purchase = env["purchase.order"].create({"partner_id": supplier.id,
        "partner_ref": tag, "picking_type_id": warehouse.in_type_id.id,
        "order_line": [(0, 0, {"product_id": raw.id, "product_qty": 10,
            "product_uom_id": kg.id, "price_unit": 2, "date_planned": datetime.now()})]})
    purchase.button_confirm()
    receipt = purchase.picking_ids
    raw_lot = env["stock.lot"].create({"name": tag + "-RECEIVED", "product_id": raw.id,
                                      "company_id": company.id})
    for move in receipt.move_ids:
        move.quantity = 10
        move.move_line_ids.write({"lot_id": raw_lot.id})
    finish_picking(receipt)
    close(purchase.order_line.qty_received, 10)
    close(env["stock.quant"]._get_available_quantity(raw, warehouse.lot_stock_id), 10)
    purchase.action_create_invoice()
    bill = purchase.invoice_ids
    bill.write({"invoice_date": datetime.now().date()})
    bill.action_post()
    close(bill.amount_total, 20)
    assert bill.state == "posted"
    passed("purchase_receipt_lot_vendor_bill", received_kg=10, bill_usd=20,
           lot_traceability=receipt.move_line_ids.lot_id == raw_lot)

    production = env["mrp.production"].create({"product_id": finished.id, "product_qty": 4,
        "product_uom_id": kg.id, "bom_id": bom.id,
        "picking_type_id": warehouse.manu_type_id.id, "origin": tag, "user_id": admin.id})
    assert production.state == "draft"
    production.action_confirm()
    production.action_assign()
    manufacture(production, 2, "-PT-FIRST")
    backorder = env["mrp.production"].search([("origin", "=", tag), ("id", "!=", production.id)])
    assert len(backorder) == 1 and backorder.state == "confirmed"
    close(backorder.product_qty, 2)
    backorder.action_assign()
    manufacture(backorder, 2, "-PT-SECOND")
    close(env["stock.quant"]._get_available_quantity(finished, warehouse.lot_stock_id), 4)
    scrap = env["stock.scrap"].create({"product_id": raw.id, "scrap_qty": .2,
        "product_uom_id": kg.id, "lot_id": raw_lot.id,
        "location_id": warehouse.lot_stock_id.id, "company_id": company.id,
        "origin": tag + " explicit simulated loss"})
    scrap.action_validate()
    assert scrap.state == "done"
    close(env["stock.quant"]._get_available_quantity(raw, warehouse.lot_stock_id), 5.2)
    shortage = env["mrp.production"].create({"product_id": finished.id, "product_qty": 10,
        "product_uom_id": kg.id, "bom_id": bom.id,
        "picking_type_id": warehouse.manu_type_id.id, "origin": tag + "-SHORTAGE"})
    shortage.action_confirm()
    shortage.action_assign()
    close(shortage.move_raw_ids.product_uom_qty, 11.5)
    close(shortage.move_raw_ids.quantity, 5.2)
    assert shortage.state != "done" and shortage.move_raw_ids.state == "partially_available"
    shortage.action_cancel()
    passed("partial_manufacturing_backorder_shortage_scrap", manufactured_kg=4,
           first_batch_kg=2, backorder_kg=2, raw_consumed_kg=4.6, scrap_kg=.2,
           raw_remaining_kg=5.2, shortage_required_kg=11.5)

    sale = env["sale.order"].create({"partner_id": customer.id, "user_id": admin.id,
        "client_order_ref": tag, "commitment_date": datetime.now() + timedelta(days=3),
        "order_line": [(0, 0, {"product_id": finished.id, "product_uom_qty": 4,
                               "product_uom_id": kg.id, "price_unit": 4})]})
    sale.action_confirm()
    assert not env["mrp.production"].search_count([("sale_line_id", "in", sale.order_line.ids)])
    delivery = sale.picking_ids
    delivery.action_assign()
    delivery.move_ids.quantity = 2
    finish_picking(delivery)
    close(sale.order_line.qty_delivered, 2)
    remaining_delivery = sale.picking_ids.filtered(lambda p: p.state not in ("done", "cancel"))
    close(remaining_delivery.move_ids.product_uom_qty, 2)
    remaining_delivery.action_assign()
    finish_picking(remaining_delivery)
    close(sale.order_line.qty_delivered, 4)
    return_wizard = env["stock.return.picking"].with_context(active_id=delivery.id,
        active_ids=delivery.ids, active_model="stock.picking").create({"picking_id": delivery.id})
    return_wizard.product_return_moves.write({"quantity": 1})
    returned = return_wizard._create_return()
    returned.move_ids.quantity = 1
    finish_picking(returned)
    close(sale.order_line.qty_delivered, 3)
    close(env["stock.quant"]._get_available_quantity(finished, warehouse.lot_stock_id), 1)
    passed("partial_delivery_backorder_return", first_delivered_kg=2,
           second_delivered_kg=2, returned_kg=1, net_delivered_kg=3,
           traceable_return=returned.move_ids.origin_returned_move_id == delivery.move_ids)

    invoice = sale._create_invoices()
    invoice.action_post()
    close(invoice.amount_total, 16)
    cash = env["account.journal"].create({"name": tag + " cash", "code": "CHKCS", "type": "cash"})
    method = cash.inbound_payment_method_line_ids[:1]
    method.payment_account_id = cash.default_account_id
    payment_wizard = env["account.payment.register"].with_context(active_model="account.move",
        active_ids=invoice.ids).create({"amount": 10, "journal_id": cash.id,
                                        "payment_method_line_id": method.id})
    payment = payment_wizard._create_payments()
    assert payment.state in ("paid", "in_process")
    close(invoice.amount_residual, 6)
    refund = invoice._reverse_moves(default_values_list=[{"ref": tag + " return credit"}], cancel=False)
    refund.invoice_line_ids.filtered(lambda line: line.product_id == finished).quantity = 1
    refund.action_post()
    close(refund.amount_total, 4)
    receivable = (invoice.line_ids + refund.line_ids).filtered(
        lambda line: line.account_id.account_type == "asset_receivable" and not line.reconciled)
    receivable.reconcile()
    close(invoice.amount_residual, 2)
    final_payment = env["account.payment.register"].with_context(active_model="account.move",
        active_ids=invoice.ids).create({"amount": 2, "journal_id": cash.id,
                                        "payment_method_line_id": method.id})._create_payments()
    close(invoice.amount_residual, 0)
    assert invoice.payment_state == "paid" and refund.amount_residual == 0
    passed("invoice_partial_payment_credit_note_reconciliation", invoice_usd=16,
           first_payment_usd=10, residual_after_first_usd=6, credit_usd=4,
           final_payment_usd=2, final_residual_usd=0, payment_state=invoice.payment_state,
           fiscal_validation="synthetic_generic_accounts_no_SV_tax_validation")

    expired = product("expired lot", "-EXP", 1, 2)
    expired_lot = env["stock.lot"].create({"name": tag + "-EXPIRED", "product_id": expired.id,
        "company_id": company.id, "expiration_date": datetime.now() - timedelta(days=1),
        "removal_date": datetime.now() - timedelta(days=2)})
    quant = env["stock.quant"].with_context(inventory_mode=True).create({
        "product_id": expired.id, "location_id": warehouse.lot_stock_id.id,
        "lot_id": expired_lot.id, "inventory_quantity": 3})
    quant.action_apply_inventory()
    expired_move = make_move(env, expired, 1, warehouse.lot_stock_id,
                             env.ref("stock.stock_location_customers"))
    close(expired_move.quantity, 0)
    assert expired_lot.product_expiry_alert
    expiry_delivery = env["stock.picking"].create({
        "picking_type_id": warehouse.out_type_id.id,
        "location_id": warehouse.lot_stock_id.id,
        "location_dest_id": env.ref("stock.stock_location_customers").id,
        "move_ids": [(0, 0, {"product_id": expired.id, "product_uom_qty": 1,
            "product_uom": kg.id, "location_id": warehouse.lot_stock_id.id,
            "location_dest_id": env.ref("stock.stock_location_customers").id})],
    })
    expiry_delivery.action_confirm()
    expiry_delivery.move_ids.quantity = 1
    expiry_delivery.move_line_ids.write({"lot_id": expired_lot.id, "picked": True})
    expiry_action = expiry_delivery.button_validate()
    assert isinstance(expiry_action, dict) and expiry_action.get("res_model") == "expiry.picking.confirmation"
    assert expiry_delivery.state != "done", "Expired stock must remain unposted until explicit decision"
    quarantine = env["stock.location"].create({"name": tag + " quarantine",
        "usage": "internal", "location_id": warehouse.view_location_id.id,
        "company_id": company.id})
    quarantined_product = product("quarantined lot", "-BLOCK", 1, 2)
    quarantine_lot = env["stock.lot"].create({"name": tag + "-BLOCKED",
        "product_id": quarantined_product.id, "company_id": company.id})
    quarantine_quant = env["stock.quant"].with_context(inventory_mode=True).create({
        "product_id": quarantined_product.id, "location_id": quarantine.id,
        "lot_id": quarantine_lot.id, "inventory_quantity": 3})
    quarantine_quant.action_apply_inventory()
    blocked_move = make_move(env, quarantined_product, 1, warehouse.lot_stock_id,
                             env.ref("stock.stock_location_customers"))
    close(blocked_move.quantity, 0)
    passed("expired_and_quarantined_lot_no_automatic_reservation", expired_reserved_kg=0,
           quarantined_reserved_kg=0, expired_physical_kg=3, quarantined_physical_kg=3,
           manual_expired_delivery_requires_confirmation=True,
           barrier="native_expiry_removal_date_and_location_outside_reservable_stock")

    equipment = env["maintenance.equipment"].create({"name": tag + " process equipment"})
    request = env["maintenance.request"].create({"name": tag + " corrective shutdown",
        "equipment_id": equipment.id, "maintenance_type": "corrective", "user_id": admin.id,
        "schedule_date": datetime.now(), "duration": 2,
        "description": "SIMULATED: administrator pauses affected manufacturing until repair."})
    assert equipment.maintenance_open_count == 1 and not request.stage_id.done
    held_production = env["mrp.production"].create({"product_id": finished.id, "product_qty": 1,
        "product_uom_id": kg.id, "bom_id": bom.id,
        "picking_type_id": warehouse.manu_type_id.id, "origin": tag + "-MAINTENANCE-HOLD"})
    assert held_production.state == "draft", "Administrator leaves affected work unauthorized during repair"
    finished_stage = env["maintenance.stage"].search([("done", "=", True)], limit=1)
    assert finished_stage, "Native closed maintenance stage is required"
    request.stage_id = finished_stage
    assert request.close_date and equipment.maintenance_open_count == 0
    held_production.with_user(admin).action_confirm()
    assert held_production.state == "confirmed"
    held_production.action_cancel()
    passed("corrective_maintenance_open_repair_close", scheduled_shutdown_hours=2,
           open_requests_after_close=0, close_date=str(request.close_date),
           production_interlock="administrator_manual_hold_not_automatic_capacity_block")
finally:
    # ponytail: native operations in one transaction; no persistent fixture database or framework.
    env.cr.rollback()

assert env["sale.order"].browse(seed_sale_id).state == "sale"
assert env["mrp.production"].browse(seed_mo_id).state == "draft"
assert not env["product.product"].search_count([("default_code", "like", tag + "%")])
passed("seed_and_business_records_preserved", seed_sale_id=seed_sale_id, seed_mo_id=seed_mo_id)

# ponytail: native row lock serializes reservations per product; callers must use this same gate.
# Direct independent stock calls that omit it are outside this connector's concurrency guarantee.
raw_seed = env["product.product"].search([("default_code", "=", "VIT-LAB-MP-001")])
available_before = env["stock.quant"]._get_available_quantity(raw_seed, warehouse.lot_stock_id)
assert available_before > 0, "Concurrency check needs positive synthetic seed stock"
requested = available_before * .75
with env.registry.cursor() as cursor_one, env.registry.cursor() as cursor_two:
    first_env = api.Environment(cursor_one, admin.id, {})
    second_env = api.Environment(cursor_two, admin.id, {})
    first_product = first_env["product.product"].browse(raw_seed.id)
    second_product = second_env["product.product"].browse(raw_seed.id)
    try:
        first_product.lock_for_update(allow_referencing=True)
        first_move = make_move(first_env, first_product, requested,
            first_env["stock.location"].browse(warehouse.lot_stock_id.id),
            first_env.ref("stock.stock_location_customers"))
        first_env.flush_all()
        close(first_move.quantity, requested)
        try:
            second_product.lock_for_update(allow_referencing=True)
        except LockError:
            rejected_while_first_active = True
        else:
            raise AssertionError("Concurrent reservation must not bypass the product lock")
        assert not second_env["stock.move"].search_count([("id", "=", first_move.id)])
        cursor_one.rollback()
        second_env.invalidate_all()
        second_product.lock_for_update(allow_referencing=True)
        retry_move = make_move(second_env, second_product, requested,
            second_env["stock.location"].browse(warehouse.lot_stock_id.id),
            second_env.ref("stock.stock_location_customers"))
        close(retry_move.quantity, requested)
        reserved = sum(second_env["stock.quant"].search([
            ("product_id", "=", raw_seed.id), ("location_id", "child_of", warehouse.lot_stock_id.id)
        ]).mapped("reserved_quantity"))
        assert reserved <= available_before + .001
        passed("two_transactions_product_lock_reservation_retry", available_kg=available_before,
               request_each_kg=requested, second_rejected_while_first_active=rejected_while_first_active,
               retry_reserved_kg=retry_move.quantity, aggregate_reserved_kg=reserved,
               serialization_gate="product.product.lock_for_update(allow_referencing=True)")
    finally:
        cursor_one.rollback()
        cursor_two.rollback()
env.invalidate_all()
close(env["stock.quant"]._get_available_quantity(raw_seed, warehouse.lot_stock_id), available_before)

evidence = root / ".local-odoo" / "evidence"
evidence.mkdir(parents=True, exist_ok=True)
(evidence / "operational-scenarios.json").write_text(json.dumps({
    "checked_at_utc": datetime.utcnow().isoformat() + "Z", "database": "vitali_lab",
    "business_records_rolled_back": True, "results": results,
}, ensure_ascii=False, indent=2), encoding="utf-8")
print("PASS: all native operational scenarios; evidence saved; business changes rolled back")
