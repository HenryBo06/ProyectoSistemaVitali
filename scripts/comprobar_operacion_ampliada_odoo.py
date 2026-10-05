"""Odoo shell: native operational exceptions, wholly rolled back in vitali_lab."""
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

assert env.cr.dbname == "vitali_lab", "Synthetic laboratory only"
# ponytail: one native transaction is enough; no extra module, fixture DB or dependency.
env = env(context={**env.context, "mail_notrack": True, "mail_create_nosubscribe": True,
                   "mail_notify_force_send": False})
tag = "LAB-EXPANDED-" + uuid4().hex[:10]
results = []
company = env.company
warehouse = env.ref("stock.warehouse0")
kg = env.ref("uom.product_uom_kgm")
unit = env.ref("uom.product_uom_unit")
admin = env.ref("base.user_admin")
stock = warehouse.lot_stock_id
params = env["ir.config_parameter"].sudo()
seed_sale_id = int(params.get_param("vitali_lab.sale_id"))
seed_mo_id = int(params.get_param("vitali_lab.manufacturing_id"))
integrated = env["vitali.integration"].search([("sale_id.name", "=", "S00011")])
assert len(integrated) == 1, "Preserved integrated scenario S00011 must exist"
before_integrated = integrated._snapshot()
before_integrated.pop("synced_at", None)
before_seed = (env["sale.order"].browse(seed_sale_id).state,
               env["mrp.production"].browse(seed_mo_id).state)
seed_products = env["product.product"].search([("default_code", "in",
    ["VIT-LAB-MP-001", "VIT-LAB-PT-001"])])
seed_quants = env["stock.quant"].search([("product_id", "in", seed_products.ids)])
before_seed_stock = seed_quants.read(["quantity", "reserved_quantity", "location_id", "lot_id"])


def close(left, right):
    assert abs(left - right) < .001, (left, right)


def passed(name, **values):
    results.append({"scenario": name, "status": "passed", **values})
    print("PASS:", name, json.dumps(values, ensure_ascii=True))


def product(name, uom=kg, tracking="lot"):
    return env["product.product"].create({
        "name": tag + " " + name, "default_code": tag + "-" + name,
        "type": "consu", "is_storable": True, "tracking": tracking,
        "uom_id": uom.id, "standard_price": 2, "list_price": 4,
        "taxes_id": [(5, 0, 0)], "supplier_taxes_id": [(5, 0, 0)],
    })


def lot(prod, suffix, **values):
    return env["stock.lot"].create({"name": tag + "-" + suffix,
        "product_id": prod.id, "company_id": company.id, **values})


def quantity(prod, location, batch=None):
    return env["stock.quant"]._get_available_quantity(prod, location, lot_id=batch)


def physical(prod, location):
    return sum(env["stock.quant"].search([("product_id", "=", prod.id),
        ("location_id", "=", location.id)]).mapped("quantity"))


def count(prod, location, qty, batch=None):
    quant = env["stock.quant"].with_context(inventory_mode=True).search([
        ("product_id", "=", prod.id), ("location_id", "=", location.id),
        ("lot_id", "=", batch.id if batch else False)], limit=1)
    if not quant:
        quant = env["stock.quant"].with_context(inventory_mode=True).create({
            "product_id": prod.id, "location_id": location.id,
            "lot_id": batch.id if batch else False, "inventory_quantity": qty})
    else:
        quant.with_context(inventory_mode=True).inventory_quantity = qty
    previous = quant.quantity
    difference = quant.inventory_diff_quantity
    action = quant.with_context(inventory_name=tag + " administrator count").action_apply_inventory()
    assert not action, action
    close(quant.quantity, qty)
    return previous, difference


def finish(picking, qty=None, batch=None):
    if qty is not None:
        picking.move_ids.quantity = qty
    if batch:
        picking.move_line_ids.lot_id = batch
    picking.move_ids.picked = True
    action = picking.button_validate()
    if isinstance(action, dict) and action.get("res_model") == "stock.backorder.confirmation":
        env[action["res_model"]].with_context(**action.get("context", {})).create({}).process()
    assert picking.state == "done", (picking.name, picking.state, action)


def transfer(prod, qty, source, destination, reason):
    picking = env["stock.picking"].create({"picking_type_id": warehouse.int_type_id.id,
        "location_id": source.id, "location_dest_id": destination.id, "origin": tag + " " + reason,
        "move_ids": [(0, 0, {"product_id": prod.id, "product_uom_qty": qty,
            "product_uom": prod.uom_id.id, "location_id": source.id,
            "location_dest_id": destination.id, "company_id": company.id})]})
    picking.action_confirm()
    picking.action_assign()
    return picking


def scrap(prod, qty, location, batch, reason):
    record = env["stock.scrap"].create({"product_id": prod.id, "scrap_qty": qty,
        "product_uom_id": prod.uom_id.id, "lot_id": batch.id,
        "location_id": location.id, "company_id": company.id, "origin": tag + " " + reason})
    record.action_validate()
    assert record.state == "done" and record.move_ids.move_line_ids.lot_id == batch
    return record


try:
    raw = product("raw")
    supplier = env["res.partner"].create({"name": tag + " supplier", "supplier_rank": 1})
    purchase = env["purchase.order"].create({"partner_id": supplier.id, "partner_ref": tag,
        "picking_type_id": warehouse.in_type_id.id,
        "order_line": [(0, 0, {"product_id": raw.id, "product_qty": 10,
            "product_uom_id": kg.id, "price_unit": 2, "date_planned": datetime.now()})]})
    assert purchase.state == "draft", "Request for quotation precedes purchasing"
    purchase.button_confirm()
    receipt = purchase.picking_ids
    raw_lot = lot(raw, "RAW")
    finish(receipt, 6, raw_lot)
    close(purchase.order_line.qty_received, 6)
    pending = purchase.picking_ids.filtered(lambda p: p.state not in ("done", "cancel"))
    assert len(pending) == 1
    close(pending.move_ids.product_uom_qty, 4)
    finish(pending, 4, raw_lot)
    close(purchase.order_line.qty_received, 10)
    wizard = env["stock.return.picking"].with_context(active_id=receipt.id,
        active_ids=receipt.ids, active_model="stock.picking").create({"picking_id": receipt.id})
    wizard.product_return_moves.quantity = 2
    returned = wizard._create_return()
    finish(returned, 2, raw_lot)
    assert returned.move_ids.origin_returned_move_id == receipt.move_ids
    close(quantity(raw, stock), 8)
    close(purchase.order_line.qty_received, 8)
    passed("partial_purchase_and_supplier_return", requested_kg=10, first_received_kg=6,
           backorder_received_kg=4, returned_supplier_kg=2, net_received_kg=8,
           native_return_link=True)

    points = env["stock.location"].create([{"name": tag + " Point " + str(i), "usage": "internal",
        "location_id": warehouse.view_location_id.id, "company_id": company.id} for i in (1, 2, 3)])
    transit = env["stock.location"].create({"name": tag + " in transit", "usage": "transit",
        "location_id": warehouse.view_location_id.id, "company_id": company.id})
    distributed = product("distributed")
    distribution_lot = lot(distributed, "DISTRIBUTION")
    count(distributed, stock, 12, distribution_lot)
    shipment = transfer(distributed, 6, stock, transit, "dispatch six kg to Point 1")
    finish(shipment)
    close(quantity(distributed, stock), 6)
    close(physical(distributed, transit), 6)
    reception = transfer(distributed, 6, transit, points[0], "receive Point 1")
    finish(reception, 4)
    close(quantity(distributed, points[0]), 4)
    close(physical(distributed, transit), 2)
    close(quantity(distributed, transit), 0)  # Two remaining kg belong to the native reception backorder.
    pending_reception = env["stock.picking"].search([("backorder_id", "=", reception.id)])
    assert len(pending_reception) == 1
    finish(pending_reception)
    for point, amount in ((points[1], 3), (points[2], 2)):
        finish(transfer(distributed, amount, stock, point, "administrator internal transfer"))
    damaged = scrap(distributed, 1, points[0], distribution_lot, "damage at receipt; administrator reject")
    previous, difference = count(distributed, points[1], 2, distribution_lot)
    close(previous, 3)
    close(difference, -1)
    adjustment = env["stock.move"].search([("product_id", "=", distributed.id),
        ("is_inventory", "=", True), ("location_id", "=", points[1].id), ("state", "=", "done")])
    assert len(adjustment) == 1 and adjustment.move_line_ids.lot_id == distribution_lot
    balances = [quantity(distributed, loc) for loc in [stock, *points]]
    assert balances == [1, 5, 2, 2], balances
    close(physical(distributed, transit), 0)
    close(sum(balances) + damaged.scrap_qty - difference, 12)
    passed("three_points_transit_partial_receipt_damage_inventory_difference",
           dispatched_kg=6, first_received_kg=4, initially_in_transit_kg=2,
           final_in_transit_kg=0, damaged_kg=1, count_difference_kg=-1,
           final_location_kg={"central": 1, "point_1": 5, "point_2": 2, "point_3": 2},
           native_count_move_and_lot=True, mass_balance_kg=12)

    quarantine = env["stock.location"].create({"name": tag + " quarantine", "usage": "internal",
        "location_id": warehouse.view_location_id.id, "company_id": company.id})
    controlled = product("quality")
    controlled_lot = lot(controlled, "QUALITY")
    count(controlled, quarantine, 5, controlled_lot)
    blocked = transfer(controlled, 2, stock, points[0], "blocked pending quality review")
    close(blocked.move_ids.quantity, 0)
    blocked.action_cancel()
    released = transfer(controlled, 3, quarantine, stock,
        "manual quality release; administrator accepts visual simulated inspection")
    released.message_post(body="SIMULATED QUALITY: administrator accepts 3 kg; no laboratory certificate.")
    finish(released)
    accepted = transfer(controlled, 2, stock, points[0], "reserve released quality lot")
    close(accepted.move_ids.quantity, 2)
    assert accepted.move_line_ids.lot_id == controlled_lot
    accepted.action_cancel()
    rejected = scrap(controlled, 2, quarantine, controlled_lot,
        "manual quality reject; damaged packaging; administrator authorizes discard")
    close(quantity(controlled, quarantine), 0)
    close(quantity(controlled, stock), 3)
    assert released.message_ids.filtered(lambda m: "SIMULATED QUALITY" in (m.body or ""))
    passed("manual_quality_hold_release_and_reject", held_kg=5, reserved_before_release_kg=0,
           released_kg=3, reserved_after_release_kg=2, rejected_kg=2,
           remaining_quarantine_kg=0, native_moves_lot_and_reason=True,
           quality_scope="administrative_inspection_and_location_gate_no_quality_enterprise")

    perishable = product("fefo")
    perishable.use_expiration_date = True
    fefo = env["product.removal"].search([("method", "=", "fefo")], limit=1)
    assert fefo, "Native product_expiry FEFO strategy must be installed"
    category = env["product.category"].create({"name": tag + " FEFO", "removal_strategy_id": fefo.id})
    perishable.categ_id = category
    late = lot(perishable, "LATE", expiration_date=datetime.now() + timedelta(days=10),
               removal_date=datetime.now() + timedelta(days=9))
    early = lot(perishable, "EARLY", expiration_date=datetime.now() + timedelta(days=4),
                removal_date=datetime.now() + timedelta(days=3))
    count(perishable, stock, 2, late)
    count(perishable, stock, 2, early)
    prioritized = transfer(perishable, 1, stock, points[0], "FEFO reservation")
    assert prioritized.move_line_ids.lot_id == early, "Earlier withdrawal date must win over receipt order"
    prioritized.action_cancel()
    passed("fefo_lot_priority", reserved_kg=1, selected_lot="EARLY",
           basis="native_removal_date", late_received_first=True)

    packaged = product("packaged")
    packaging = product("packaging", unit, "none")
    count(packaging, stock, 3)
    start = (datetime.now() + timedelta(days=2)).replace(hour=9, minute=0, second=0, microsecond=0)
    calendar = env["resource.calendar"].create({"name": tag + " production calendar", "tz": "UTC",
        "attendance_ids": [(0, 0, {"name": "Continuous synthetic day " + str(day),
            "dayofweek": str(day), "hour_from": 0, "hour_to": 24,
            "day_period": "morning"}) for day in range(7)]})
    center = env["mrp.workcenter"].create({"name": tag + " packing center", "code": tag,
        "resource_calendar_id": calendar.id, "time_efficiency": 100,
        "costs_hour": 6, "time_start": 5, "time_stop": 5,
        "capacity_ids": [(0, 0, {"product_id": packaged.id, "product_uom_id": kg.id,
            "capacity": 2, "time_start": 5, "time_stop": 5})]})
    bom = env["mrp.bom"].create({"product_tmpl_id": packaged.product_tmpl_id.id,
        "product_qty": 1, "product_uom_id": kg.id, "type": "normal", "company_id": company.id,
        "bom_line_ids": [(0, 0, {"product_id": raw.id, "product_qty": 1, "product_uom_id": kg.id}),
            (0, 0, {"product_id": packaging.id, "product_qty": 1, "product_uom_id": unit.id})],
        "operation_ids": [(0, 0, {"name": "Simulated packing", "workcenter_id": center.id,
            "time_mode": "manual", "time_cycle_manual": 30})]})
    production = env["mrp.production"].create({"product_id": packaged.id, "product_qty": 4,
        "product_uom_id": kg.id, "bom_id": bom.id, "picking_type_id": warehouse.manu_type_id.id,
        "origin": tag, "date_start": start, "user_id": admin.id})
    assert production.state == "draft"
    production.with_user(admin).action_confirm()
    production.action_assign()
    packaging_move = production.move_raw_ids.filtered(lambda m: m.product_id == packaging)
    close(packaging_move.product_uom_qty, 4)
    close(packaging_move.quantity, 3)
    assert packaging_move.state == "partially_available" and production.state != "done"
    count(packaging, stock, 4)
    production.action_assign()
    close(packaging_move.quantity, 4)
    workorder = production.workorder_ids
    assert len(workorder) == 1
    close(workorder.duration_expected, 70)
    production.button_plan()
    initial_start, initial_end = workorder.date_start, workorder.date_finished
    close((initial_end - initial_start).total_seconds() / 60, 70)
    assert initial_start == start
    equipment = env["maintenance.equipment"].create({"name": tag + " packing equipment",
        "note": "SIMULATED equipment for workcenter " + center.display_name})
    request = env["maintenance.request"].create({"name": tag + " urgent corrective packing stop",
        "equipment_id": equipment.id, "maintenance_type": "corrective", "user_id": admin.id,
        "priority": "3", "schedule_date": start, "schedule_end": start + timedelta(hours=2),
        "description": "SIMULATED: administrator blocks packing calendar until repair."})
    close(request.duration, 2)
    downtime = env["resource.calendar.leaves"].create({"name": tag + " administrator maintenance hold",
        "calendar_id": calendar.id, "resource_id": center.resource_id.id,
        "date_from": start, "date_to": start + timedelta(hours=2), "time_type": "leave"})
    workorder._plan_workorder(replan=True)
    assert workorder.date_start >= downtime.date_to
    delayed_start = workorder.date_start
    assert request.priority == "3" and equipment.maintenance_open_count == 1
    closed = env["maintenance.stage"].search([("done", "=", True)], limit=1)
    assert closed
    request.stage_id = closed
    assert request.close_date and equipment.maintenance_open_count == 0
    downtime.unlink()
    workorder._plan_workorder(replan=True)
    assert workorder.date_start == initial_start
    second = production.copy({"product_qty": 4, "origin": tag + " second capacity demand",
                              "date_start": start})
    second.action_confirm()
    second.button_plan()
    assert second.workorder_ids.date_start >= workorder.date_finished, (
        second.workorder_ids.date_start, workorder.date_start, workorder.date_finished)
    second.action_cancel()
    finished_lot = lot(packaged, "PACKAGED")
    production.write({"lot_producing_ids": [(6, 0, finished_lot.ids)], "qty_producing": 4})
    production._set_qty_producing()
    production.move_raw_ids.picked = True
    production.button_mark_done()
    assert production.state == "done" and workorder.state == "done"
    close(quantity(packaged, stock), 4)
    close(quantity(packaging, stock), 0)
    close(quantity(raw, stock), 4)
    passed("packaging_shortage_capacity_timing_and_maintenance_planning",
           manufactured_kg=4, raw_consumed_kg=4, packaging_consumed_units=4,
           initial_packaging_shortage_units=1, parallel_capacity_kg=2,
           cycle_minutes=30, setup_minutes=5, cleanup_minutes=5, expected_minutes=70,
           initial_start=str(initial_start), maintenance_delayed_start=str(delayed_start),
           maintenance_hold_hours=2, repair_releases_original_slot=True,
           second_order_cannot_overlap=True,
           maintenance_gate="administrator_adds_removes_native_resource_calendar_leave")
    preventive = env["maintenance.request"].create({"name": tag + " preventive weekly cleaning",
        "equipment_id": equipment.id, "maintenance_type": "preventive", "user_id": admin.id,
        "priority": "2", "schedule_date": start + timedelta(days=7),
        "schedule_end": start + timedelta(days=7, hours=1),
        "recurring_maintenance": True, "repeat_interval": 1, "repeat_unit": "week",
        "description": "SIMULATED: weekly cleaning and packaging inspection; administrator responsible."})
    assert preventive.user_id == admin and preventive.maintenance_team_id
    close(preventive.duration, 1)
    preventive.stage_id = closed
    subsequent = env["maintenance.request"].search([("name", "=", preventive.name),
        ("id", "!=", preventive.id)])
    assert len(subsequent) == 1 and not subsequent.stage_id.done
    assert subsequent.schedule_date == preventive.schedule_date + timedelta(days=7)
    assert preventive.close_date and subsequent.user_id == admin
    passed("preventive_maintenance_responsibility_priority_and_next_ticket",
           maintenance_type="preventive", priority="2", responsible="administrator",
           duration_hours=1, recurrence_days=7, closed_and_next_ticket_created=True)
finally:
    env.cr.rollback()
    env.invalidate_all()

assert (env["sale.order"].browse(seed_sale_id).state,
        env["mrp.production"].browse(seed_mo_id).state) == before_seed
assert env["stock.quant"].browse(seed_quants.ids).read(
    ["quantity", "reserved_quantity", "location_id", "lot_id"]) == before_seed_stock
after_integrated = env["vitali.integration"].browse(integrated.id)._snapshot()
after_integrated.pop("synced_at", None)
assert after_integrated == before_integrated, (after_integrated, before_integrated)
assert not env["product.product"].search_count([("default_code", "like", tag + "%")])
passed("seed_integrated_cycle_and_business_records_preserved", seed_sale_id=seed_sale_id,
       seed_mo_id=seed_mo_id, integrated_sale="S00011", business_transaction="rolled_back")
destination = Path(".local-odoo/evidence/operational-expanded.json")
destination.parent.mkdir(parents=True, exist_ok=True)
destination.write_text(json.dumps({"status": "passed", "database": env.cr.dbname,
    "checked_at_utc": datetime.now(timezone.utc).isoformat(), "scenarios": results,
    "business_records_persisted": False,
    "scope": "Native Community synthetic operations; administrator only; no fiscal certification"},
    ensure_ascii=False, indent=2), encoding="utf-8")
print("PASS: expanded native scenarios; evidence:", destination)
