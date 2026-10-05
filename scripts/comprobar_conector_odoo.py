"""Run in the dedicated Odoo shell after updating vitali_lab; changes roll back."""
from copy import deepcopy
from datetime import date, timedelta
from uuid import uuid4

from odoo import Command
from odoo.exceptions import AccessError, ValidationError

assert env.cr.dbname == "vitali_lab", "This check is only for the isolated synthetic lab"


def denied(function, error=ValidationError):
    try:
        with env.cr.savepoint():
            function()
    except error:
        return
    raise AssertionError("Expected the operation to be rejected")


try:
    integration = env["vitali.integration"]
    company = integration._operation().env.company
    warehouse = integration._operation()._warehouse()
    unit = env.ref("uom.product_uom_kgm")
    suffix = uuid4().hex
    pricelist = env['product.pricelist'].search([('currency_id', '=', company.currency_id.id), ('company_id', 'in', [False, company.id])], limit=1)
    if not pricelist:
        pricelist = env['product.pricelist'].create({'name': 'Self-check USD', 'currency_id': company.currency_id.id, 'company_id': company.id})
    customer = env["res.partner"].create({"name": "Connector self-check (synthetic)", "ref": "CHECK-" + suffix,
                                          'property_product_pricelist': pricelist.id,
                                          "company_id": company.id})
    product = env["product.product"].with_company(company).create({"name": "Connector finished self-check (synthetic)",
        "default_code": "CHECK-PT-" + suffix, "type": "consu", "is_storable": True, "uom_id": unit.id,
        "company_id": company.id, "taxes_id": [Command.clear()], "standard_price": 2})
    raw = env["product.product"].with_company(company).create({"name": "Connector raw self-check (synthetic)",
        "default_code": "CHECK-MP-" + suffix, "type": "consu", "is_storable": True, "uom_id": unit.id,
        "company_id": company.id, "taxes_id": [Command.clear()]})
    env["mrp.bom"].create({"product_tmpl_id": product.product_tmpl_id.id, "product_id": product.id,
        "product_qty": 1, "product_uom_id": unit.id, "company_id": company.id,
        "bom_line_ids": [Command.create({"product_id": raw.id, "product_qty": 1, "product_uom_id": unit.id})]})
    env["stock.quant"]._update_available_quantity(product, warehouse.lot_stock_id, 5)
    env["stock.quant"]._update_available_quantity(raw, warehouse.lot_stock_id, 20)
    service = env["res.users"].create({"name": "Connector service self-check (synthetic)", "login": "check-" + suffix,
        "group_ids": [Command.set([env.ref("vitali_lab.group_integration").id])],
        "company_id": company.id, "company_ids": [Command.set([company.id])]})
    api = integration.with_user(service)
    assert service.all_group_ids == env.ref("vitali_lab.group_integration"), "Service must not inherit native application groups"
    assert api.ping()["company_id"] == company.id
    for model in ("sale.order", "stock.move", "stock.picking", "mrp.production", "account.move"):
        denied(lambda model=model: env[model].with_user(service).search([], limit=1), AccessError)
    denied(lambda: integration.with_user(env.ref("base.public_user")).ping(), AccessError)

    payload = {"reference": "smartorder:" + str(uuid4()), "revision": 1,
        "customer": {"code": customer.ref, "name": customer.name},
        "delivery_date": (date.today() + timedelta(days=10)).isoformat(), "terms": "Synthetic credit terms",
        "lines": [{"key": "1", "sku": product.default_code, "quantity": 8, "unit_price": 4,
                   "discount": 5, "unit": "uom.product_uom_kgm"}]}
    first = api.sync_sale(payload)
    retry = api.sync_sale(deepcopy(payload))
    assert first["sale"]["id"] == retry["sale"]["id"] and first["sale"]["state"] == "sale"
    assert retry["lines"][0]["ordered"] == 8 and retry["lines"][0]["reserved"] == 5
    assert retry["lines"][0]["manufacturing_state"] is None and retry["lines"][0]["last_delivery_at"] is None
    assert not retry["lines"][0]["mo_ids"] and retry["lines"][0]["unit_cost"] == 2
    assert not env["mrp.production"].search_count([("sale_line_id.order_id", "=", first["sale"]["id"])])
    assert env["vitali.integration"].search_count([("reference", "=", payload["reference"])]) == 1
    for field, value in (("quantity", -1), ("unit_price", "NaN"), ("discount", 101), ("unit", "kg")):
        invalid = deepcopy(payload)
        invalid["revision"] = 2
        invalid["lines"][0][field] = value
        denied(lambda invalid=invalid: api.sync_sale(invalid))
    invalid = deepcopy(payload)
    invalid["company_id"] = company.id
    denied(lambda: api.sync_sale(invalid))

    original_line_id = env["vitali.integration"].search([("reference", "=", payload["reference"])]).line_ids.sale_line_id.id
    revised = deepcopy(payload)
    revised["revision"] = 2
    revised["lines"][0]["quantity"] = 9
    revised["lines"][0]["unit_price"] = 5
    changed = api.sync_sale(revised)
    assert changed["sale"]["id"] == first["sale"]["id"] and changed["lines"][0]["reserved"] == 5
    assert env["vitali.integration"].search([("reference", "=", payload["reference"])]).line_ids.sale_line_id.id == original_line_id
    assert api.sync_sale(payload)["lines"][0]["ordered"] == 9, "Older revisions must not overwrite current data"
    invalid = deepcopy(revised)
    invalid["lines"][0]["quantity"] = 10
    denied(lambda: api.sync_sale(invalid))
    denied(lambda: api.authorize_production(payload["reference"], [{"key": "1", "quantity": 10}], revision=2))
    denied(lambda: api.authorize_production(payload["reference"], [{"key": "1", "quantity": 3}], revision=1))
    denied(lambda: api.cancel_sale(payload["reference"], revision=1))
    authorized = api.authorize_production(payload["reference"], [{"key": "1", "quantity": 3}], revision=2)
    repeated = api.authorize_production(payload["reference"], [{"key": "1", "quantity": 3}], revision=2)
    assert authorized["lines"][0]["mo_ids"] == repeated["lines"][0]["mo_ids"]
    assert len(repeated["lines"][0]["mo_ids"]) == 1 and repeated["lines"][0]["manufacturing_state"] == "confirmed"
    assert repeated["lines"][0]["authorized"] and repeated["lines"][0]["authorized_quantity"] == 3
    denied(lambda: api.authorize_production(payload["reference"], [{"key": "1", "quantity": 2}], revision=2))
    invalid = deepcopy(revised)
    invalid["revision"] = 3
    invalid["lines"][0]["quantity"] = 10
    denied(lambda: api.sync_sale(invalid))
    denied(lambda: api.cancel_sale(payload["reference"], revision=2))
    assert api.snapshot(payload["reference"])["lines"][0]["produced"] == 0, "Authorization is not production"

    canceled_payload = deepcopy(payload)
    canceled_payload["reference"] = "smartorder:" + str(uuid4())
    canceled_payload["lines"][0]["quantity"] = 1
    created = api.sync_sale(canceled_payload)
    canceled = api.cancel_sale(canceled_payload["reference"], revision=1)
    assert canceled["sale"]["id"] == created["sale"]["id"] and canceled["sale"]["state"] == "cancel"
    assert canceled["lines"][0]["reserved"] == 0 and not canceled["lines"][0]["mo_ids"]
    assert api.cancel_sale(canceled_payload["reference"], revision=1)["sale"]["state"] == "cancel"
    assert api.sync_sale(canceled_payload)['sale']['state'] == 'cancel'
    reopening = deepcopy(canceled_payload)
    reopening['revision'] = 2
    reopening['lines'][0]['quantity'] = 2
    denied(lambda: api.sync_sale(reopening))
    assert env["sale.order"].browse(created["sale"]["id"]).exists(), "Cancellation must preserve the sale"

    print("PASS: restricted service; native reservations; stable keys; revision and retry idempotency; separate production; cancellation; null pending states")
finally:
    env.cr.rollback()
