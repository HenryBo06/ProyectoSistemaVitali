"""Atomic, reference-scoped API; native Odoo owns stock and accounting."""
import hashlib
import html
import json
import re
from datetime import date, datetime, time
from decimal import Decimal, InvalidOperation

from psycopg2.errors import UniqueViolation

from odoo import api, fields, models, release
from odoo.exceptions import AccessError, ConcurrencyError, LockError, ValidationError
from odoo.tools import html2plaintext


class VitaliIntegration(models.Model):
    _name = "vitali.integration"
    _description = "Referencia externa SmartOrder"

    reference = fields.Char(required=True, index=True)
    company_id = fields.Many2one("res.company", required=True, ondelete="restrict")
    sale_id = fields.Many2one("sale.order", ondelete="restrict")
    revision = fields.Integer(default=0, required=True)
    payload_hash = fields.Char()
    last_payload = fields.Json()
    line_ids = fields.One2many("vitali.integration.line", "integration_id")
    _reference_unique = models.Constraint("unique(company_id, reference)", "La referencia externa ya existe.")

    def _operation(self):
        # RPC parameters and context are untrusted, including allowed_company_ids.
        if not (self.env.user.has_group("vitali_lab.group_integration")
                or self.env.user.has_group("base.group_system")):
            raise AccessError("Esta operación requiere el servicio de integración autorizado.")
        company_value = self.env["ir.config_parameter"].sudo().get_param("vitali_lab.integration_company_id")
        try:
            company_id = int(company_value) if company_value else self.env.ref("base.main_company").id
        except (TypeError, ValueError) as error:
            raise ValidationError("La compañía fija de integración no es válida.") from error
        company = self.env["res.company"].sudo().browse(company_id).exists()
        if not company:
            raise ValidationError("La compañía fija de integración no existe.")
        return self.sudo().with_context({}, allowed_company_ids=[company.id], lang="es_ES", tz="UTC").with_company(company)

    @staticmethod
    def _text(value, label, maximum=255, empty=False):
        if not isinstance(value, str) or len(value) > maximum or (not empty and not value.strip()):
            raise ValidationError("%s no es válido." % label)
        return value.strip() if not empty else value

    @staticmethod
    def _keys(value, allowed, required, label):
        if not isinstance(value, dict) or set(value) - set(allowed) or set(required) - set(value):
            raise ValidationError("Campos no válidos en %s." % label)

    @staticmethod
    def _number(value, label, maximum=1_000_000_000, positive=False):
        if isinstance(value, bool) or not isinstance(value, (str, int, float)) or len(str(value)) > 100:
            raise ValidationError("%s debe ser numérico." % label)
        try:
            number = Decimal(str(value))
        except InvalidOperation as error:
            raise ValidationError("%s debe ser numérico." % label) from error
        if not number.is_finite() or number < 0 or number > maximum or (positive and number == 0):
            raise ValidationError("%s está fuera de los límites permitidos." % label)
        return float(number)

    def _reference(self, value):
        value = self._text(value, "Referencia", 200)
        if not re.fullmatch(r"smartorder:[A-Za-z0-9:_-]+", value):
            raise ValidationError("La referencia debe pertenecer al espacio smartorder:.")
        return value

    def _payload(self, value):
        self._keys(value, ["reference", "customer", "delivery_date", "lines", "terms", "revision"],
                   ["reference", "customer", "delivery_date", "lines", "revision"], "venta")
        self._keys(value["customer"], ["code", "name"], ["code"], "cliente")
        revision = value["revision"]
        if isinstance(revision, bool) or not isinstance(revision, int) or not 1 <= revision <= 2_147_483_647:
            raise ValidationError("La revisión debe ser un contador entero positivo.")
        delivery = self._text(value["delivery_date"], "Fecha de entrega", 10)
        try:
            if date.fromisoformat(delivery).isoformat() != delivery:
                raise ValueError
        except ValueError as error:
            raise ValidationError("La fecha de entrega debe usar YYYY-MM-DD.") from error
        items = value["lines"]
        if not isinstance(items, list) or not 1 <= len(items) <= 250:
            raise ValidationError("La venta debe tener entre 1 y 250 líneas.")
        lines, keys = [], set()
        for item in items:
            self._keys(item, ["key", "sku", "quantity", "unit_price", "discount", "unit"],
                       ["key", "sku", "quantity", "unit_price", "discount", "unit"], "línea")
            key = self._text(item["key"], "Identificador de línea", 100)
            if key in keys:
                raise ValidationError("Los identificadores de línea deben ser únicos.")
            keys.add(key)
            lines.append({"key": key, "sku": self._text(item["sku"], "Código de producto", 100),
                          "quantity": self._number(item["quantity"], "Cantidad", positive=True),
                          "unit_price": self._number(item["unit_price"], "Precio"),
                          "discount": self._number(item["discount"], "Descuento", 100),
                          "unit": self._text(item["unit"], "Unidad XMLID", 100)})
        return {"reference": self._reference(value["reference"]), "revision": revision,
                "customer": {"code": self._text(value["customer"]["code"], "Código de cliente", 100),
                             "name": self._text(value["customer"].get("name", ""), "Nombre de cliente", empty=True)},
                "delivery_date": delivery, "terms": self._text(value.get("terms", ""), "Condiciones", 4000, empty=True),
                "lines": sorted(lines, key=lambda item: item["key"])}

    def _locked(self, reference, create=False):
        company_id = self.env.company.id
        lock_key = int.from_bytes(hashlib.sha256((str(company_id) + ":" + reference).encode()).digest()[:8], "big", signed=True)
        self.env.cr.execute("SELECT pg_advisory_xact_lock(%s)", (lock_key,))
        # FOR UPDATE makes concurrent changes retry under Odoo's repeatable-read isolation.
        self.env.cr.execute("SELECT id FROM vitali_integration WHERE company_id=%s AND reference=%s FOR UPDATE",
                            (company_id, reference))
        row = self.env.cr.fetchone()
        if row:
            record = self.browse(row[0])
            record.invalidate_recordset()
            return record
        if not create:
            raise ValidationError("La venta aún no está sincronizada.")
        try:
            with self.env.cr.savepoint():
                return self.create({"reference": reference, "company_id": company_id})
        except UniqueViolation as error:
            # First inserts can race before a repeatable-read snapshot sees the winner.
            # Odoo's request retry starts a fresh transaction; never create a second sale.
            raise ConcurrencyError("Reintentar la referencia con una transacción nueva.") from error

    def _warehouse(self):
        value = self.env["ir.config_parameter"].get_param("vitali_lab.integration_warehouse_id")
        if value:
            try:
                warehouse = self.env["stock.warehouse"].browse(int(value)).exists()
            except (TypeError, ValueError) as error:
                raise ValidationError("El almacén fijo no es válido.") from error
        else:
            warehouses = self.env["stock.warehouse"].search([("company_id", "=", self.env.company.id)], limit=2)
            if len(warehouses) != 1:
                raise ValidationError("Configura un almacén fijo para la integración.")
            warehouse = warehouses
        if not warehouse or warehouse.company_id != self.env.company:
            raise ValidationError("El almacén no pertenece a la compañía de integración.")
        return warehouse

    def _catalog(self, payload):
        partners = self.env["res.partner"].search([("ref", "=", payload["customer"]["code"]),
                                                 ("company_id", "in", [False, self.env.company.id])], limit=2)
        if len(partners) != 1:
            raise ValidationError("El código de cliente debe identificar un cliente activo único en Odoo.")
        # Native computed pricelists depend on feature groups that this restricted bot lacks.
        pricelist = partners.specific_property_product_pricelist or partners.property_product_pricelist
        if not pricelist:
            pricelist = self.env['product.pricelist'].search([('currency_id', '=', self.env.company.currency_id.id),
                ('company_id', 'in', [False, self.env.company.id])], limit=2)
        if len(pricelist) != 1 or pricelist.currency_id != self.env.company.currency_id:
            raise ValidationError("La lista de precios del cliente debe usar la moneda de la compañía.")
        products = {}
        for item in payload["lines"]:
            found = self.env["product.product"].search([("default_code", "=", item["sku"]),
                                                       ("company_id", "in", [False, self.env.company.id])], limit=2)
            if not re.fullmatch(r"[A-Za-z0-9_]+\.[A-Za-z0-9_]+", item["unit"]):
                raise ValidationError("La unidad debe ser un XMLID explícito.")
            unit = self.env.ref(item["unit"], raise_if_not_found=False)
            if len(found) != 1 or found.type != "consu" or not found.is_storable:
                raise ValidationError("El SKU debe identificar un producto almacenable activo único en Odoo.")
            # ponytail: unit must match exactly; explicit conversions can be added after catalog validation.
            if not unit or unit._name != "uom.uom" or unit != found.uom_id:
                raise ValidationError("La unidad XMLID debe coincidir con la unidad del producto.")
            products[item["key"]] = found
        return partners, products, pricelist

    def _lock_products(self, products):
        # Shared SKU locks serialize reservations across different external references.
        try:
            for product in products.sorted("id"):
                product.lock_for_update(allow_referencing=True)
        except LockError as error:
            # Native SKIP LOCKED returns LockError; JSON-2 retries ConcurrencyError.
            raise ConcurrencyError("Reintentar la reserva del producto con una transacción nueva.") from error

    def _editable(self):
        self.ensure_one()
        sale = self.sale_id
        self.env.cr.execute("SELECT id FROM sale_order WHERE id=%s FOR UPDATE", (sale.id,))
        self.env.cr.execute("SELECT id FROM stock_move WHERE sale_line_id IN "
                            "(SELECT id FROM sale_order_line WHERE order_id=%s) ORDER BY id FOR UPDATE", (sale.id,))
        sale.invalidate_recordset()
        sale.order_line.move_ids.invalidate_recordset()
        if self.line_ids.filtered("authorized") or sale.invoice_ids or sale.state == "cancel":
            raise ValidationError("La venta ya está autorizada, facturada o cancelada; no admite cambios directos.")
        if sale.order_line.move_ids.filtered(lambda move: move.state == "done" or move.picked):
            raise ValidationError("La operación ya comenzó; no admite cambios directos.")
        if self.env["mrp.production"].search_count([("sale_line_id", "in", sale.order_line.ids)]):
            raise ValidationError("La venta ya tiene fabricación vinculada; no admite cambios directos.")

    def _delivery(self):
        self.ensure_one()
        sale = self.sale_id
        warehouse = self._warehouse()
        destination = sale.partner_shipping_id.property_stock_customer
        picking = self.env["stock.picking"].create({"partner_id": sale.partner_shipping_id.id,
            "picking_type_id": warehouse.out_type_id.id, "location_id": warehouse.lot_stock_id.id,
            "location_dest_id": destination.id, "origin": sale.name, "move_type": "direct",
            "scheduled_date": sale.commitment_date})
        values = [{"product_id": item.sale_line_id.product_id.id, "product_uom_qty": item.sale_line_id.product_uom_qty,
                   "product_uom": item.sale_line_id.product_uom_id.id, "sale_line_id": item.sale_line_id.id,
                   "picking_id": picking.id, "picking_type_id": warehouse.out_type_id.id,
                   "location_id": warehouse.lot_stock_id.id, "location_dest_id": destination.id,
                   "company_id": self.company_id.id, "procure_method": "make_to_stock", "origin": sale.name,
                   "date": sale.commitment_date} for item in self.line_ids]
        moves = self.env["stock.move"].create(values)
        # Do not run product MTO rules or the scheduler on a commercial confirmation.
        moves._action_confirm(merge=False, create_proc=False)
        moves._action_assign()
        if self.env["mrp.production"].search_count([("sale_line_id", "in", sale.order_line.ids)]):
            raise ValidationError("La confirmación comercial no debe crear fabricación.")

    @api.model
    def ping(self):
        operation = self._operation()
        company = operation.env.company
        return {"version": release.version, "company": company.name, "company_id": company.id,
                "currency": company.currency_id.name}

    @api.model
    def sync_sale(self, payload):
        operation = self._operation()
        payload = operation._payload(payload)
        fingerprint = hashlib.sha256(json.dumps({key: value for key, value in payload.items() if key != "revision"},
                                                sort_keys=True, ensure_ascii=True).encode()).hexdigest()
        with operation.env.cr.savepoint():
            record = operation._locked(payload["reference"], create=True)
            previous = record.last_payload
            if record.sale_id:
                if payload["revision"] < record.revision:
                    return record._snapshot()
                if payload["revision"] == record.revision:
                    if record.payload_hash != fingerprint:
                        raise ValidationError("La misma revisión no puede contener datos diferentes.")
                    return record._snapshot()
                if record.payload_hash == fingerprint:
                    record.revision = payload["revision"]
                    return record._snapshot()
                if set(record.line_ids.mapped("key")) != {item["key"] for item in payload["lines"]}:
                    raise ValidationError("Los identificadores de las líneas existentes deben conservarse.")
            partner, products, pricelist = operation._catalog(payload)
            operation._lock_products(operation.env["product.product"].browse([product.id for product in products.values()])
                                     | record.sale_id.order_line.product_id)
            if record.sale_id:
                record._editable()
            warehouse = operation._warehouse()
            moment = datetime.combine(date.fromisoformat(payload["delivery_date"]), time(18))
            sale_values = {"partner_id": partner.id, "partner_invoice_id": partner.id, "partner_shipping_id": partner.id,
                           "pricelist_id": pricelist.id,
                           "company_id": operation.env.company.id, "warehouse_id": warehouse.id,
                           "client_order_ref": payload["reference"], "commitment_date": moment,
                           "note": html.escape(payload["terms"])}
            if record.sale_id:
                # Preserve the sale, its original line IDs, canceled deliveries, and chatter.
                record.sale_id._action_cancel()
                record.sale_id.action_draft()
                record.sale_id.write(sale_values)
            else:
                record.sale_id = operation.env["sale.order"].create(sale_values)
            current = {item.key: item for item in record.line_ids}
            for item in payload["lines"]:
                values = {"product_id": products[item["key"]].id, "product_uom_id": products[item["key"]].uom_id.id,
                          "product_uom_qty": item["quantity"], "price_unit": item["unit_price"], "discount": item["discount"]}
                if item["key"] in current:
                    current[item["key"]].sale_line_id.with_context(skip_procurement=True).write(values)
                    current[item["key"]].unit_xmlid = item["unit"]
                else:
                    values["order_id"] = record.sale_id.id
                    sale_line = operation.env["sale.order.line"].create(values)
                    operation.env["vitali.integration.line"].create({"integration_id": record.id, "key": item["key"],
                                                                  "sale_line_id": sale_line.id, "unit_xmlid": item["unit"]})
            record.sale_id.with_context(skip_procurement=True).action_confirm()
            record._delivery()
            record.write({"revision": payload["revision"], "payload_hash": fingerprint, "last_payload": payload})
            audit = json.dumps({"before": previous, "after": payload}, ensure_ascii=True, sort_keys=True)
            record.sale_id.message_post(body="SmartOrder: revisión %s, producción independiente. %s" % (record.revision, audit),
                                        subtype_xmlid="mail.mt_note")
            return record._snapshot()

    @api.model
    def authorize_production(self, reference, lines, revision):
        operation = self._operation()
        reference = operation._reference(reference)
        if not isinstance(lines, list) or not 1 <= len(lines) <= 250:
            raise ValidationError("Debes indicar las líneas y cantidades autorizadas.")
        requested, keys = [], set()
        for value in lines:
            operation._keys(value, ["key", "quantity"], ["key", "quantity"], "autorización")
            key = operation._text(value["key"], "Identificador de línea", 100)
            if key in keys:
                raise ValidationError("Una línea no puede autorizarse dos veces en la misma solicitud.")
            keys.add(key)
            requested.append((key, operation._number(value["quantity"], "Cantidad autorizada")))
        with operation.env.cr.savepoint():
            record = operation._locked(reference)
            record._check_revision(revision)
            if record.sale_id.state != "sale":
                raise ValidationError("Solo se autoriza fabricación para una venta confirmada.")
            current = {item.key: item for item in record.line_ids}
            plans = []
            locked_products = operation.env["product.product"]
            for key, quantity in requested:
                item = current.get(key)
                if not item or quantity > item.sale_line_id.product_uom_qty:
                    raise ValidationError("La autorización debe corresponder a una línea y no superar lo vendido.")
                if item.authorized:
                    if item.authorized_quantity != quantity:
                        raise ValidationError("La línea ya fue autorizada con otra cantidad.")
                    continue
                if quantity:
                    product = item.sale_line_id.product_id
                    bom = operation.env["mrp.bom"]._bom_find(product, company_id=record.company_id.id, bom_type="normal")[product]
                    if not bom:
                        raise ValidationError("El producto necesita una receta nativa antes de fabricar.")
                    locked_products |= product | bom.bom_line_ids.product_id | bom.byproduct_ids.product_id
                else:
                    bom = operation.env["mrp.bom"]
                plans.append((item, quantity, bom))
            operation._lock_products(locked_products)
            for item, quantity, bom in plans:
                if quantity:
                    product = item.sale_line_id.product_id
                    manufacturing = operation.env["mrp.production"].create({"product_id": product.id,
                        "product_uom_id": item.sale_line_id.product_uom_id.id, "product_qty": quantity, "bom_id": bom.id,
                        "company_id": record.company_id.id, "picking_type_id": operation._warehouse().manu_type_id.id,
                        "origin": record.sale_id.name, "sale_line_id": item.sale_line_id.id,
                        "date_deadline": record.sale_id.commitment_date})
                    manufacturing.action_confirm()
                item.write({"authorized": True, "authorized_quantity": quantity})
            if plans:
                record.sale_id.message_post(body="SmartOrder: autorización administrativa de fabricación registrada. "
                                            + json.dumps(requested, ensure_ascii=True), subtype_xmlid="mail.mt_note")
            return record._snapshot()

    @api.model
    def snapshot(self, reference):
        operation = self._operation()
        return operation._locked(operation._reference(reference))._snapshot()

    @api.model
    def cancel_sale(self, reference, revision):
        operation = self._operation()
        with operation.env.cr.savepoint():
            record = operation._locked(operation._reference(reference))
            record._check_revision(revision)
            if record.sale_id.state != "cancel":
                operation._lock_products(record.sale_id.order_line.product_id)
                record._editable()
                record.sale_id._action_cancel()
                record.sale_id.message_post(body="SmartOrder: venta cancelada antes de autorización operativa.",
                                            subtype_xmlid="mail.mt_note")
            return record._snapshot()

    def _check_revision(self, revision):
        self.ensure_one()
        if isinstance(revision, bool) or not isinstance(revision, int) or revision != self.revision:
            raise ValidationError("La revisión cambió; sincroniza la venta antes de continuar.")

    @staticmethod
    def _state(states, priorities):
        return next((state for state in priorities if state in states), None)

    def _snapshot(self):
        self.ensure_one()
        result = []
        for item in self.line_ids.sorted("key"):
            line = item.sale_line_id
            unit = line.product_uom_id
            moves = line.move_ids.filtered(lambda move: move.product_id == line.product_id and move.company_id == self.company_id)
            outgoing = moves.filtered(lambda move: move.location_dest_id.usage == "customer")
            returned = moves.filtered(lambda move: move.location_id.usage == "customer" and move.location_dest_id.usage == "internal")
            done = outgoing.filtered(lambda move: move.state == "done")
            delivered = sum(move.product_uom._compute_quantity(move.quantity, unit, round=False) for move in done)
            returned_qty = sum(move.product_uom._compute_quantity(move.quantity, unit, round=False)
                               for move in returned.filtered(lambda move: move.state == "done"))
            reserved = sum(move.product_uom._compute_quantity(move.quantity, unit, round=False)
                           for move in outgoing.filtered(lambda move: move.state not in ("done", "cancel")))
            manufacturing = self.env["mrp.production"].search([("sale_line_id", "=", line.id), ("company_id", "=", self.company_id.id)])
            finished = manufacturing.move_finished_ids.filtered(lambda move: move.product_id == line.product_id
                                                                 and move.state == "done" and move.location_dest_id.usage == "internal")
            produced = sum(move.product_uom._compute_quantity(move.quantity, unit, round=False) for move in finished)
            scraps = manufacturing.scrap_ids.filtered(lambda scrap: scrap.product_id == line.product_id and scrap.state == "done")
            scrapped = sum(scrap.product_uom_id._compute_quantity(scrap.scrap_qty, unit, round=False) for scrap in scraps)
            mo_states = sorted(set(manufacturing.mapped("state")))
            delivery_states = sorted(set(outgoing.filtered(lambda move: move.state != "cancel").picking_id.mapped("state")))
            if not delivery_states and outgoing:
                delivery_states = ["cancel"]
            times = [value for value in done.picking_id.mapped("date_done") if value]
            result.append({"key": item.key, "ordered": line.product_uom_qty, "reserved": max(0, reserved),
                "authorized": item.authorized, "authorized_quantity": item.authorized_quantity,
                "produced": produced, "delivered": delivered, "returned": returned_qty,
                "net_delivered": max(0, delivered - returned_qty), "outstanding": max(0, line.product_uom_qty - delivered + returned_qty),
                "scrapped": scrapped, "manufacturing_state": self._state(mo_states, ["progress", "to_close", "confirmed", "draft", "done", "cancel"]),
                "delivery_state": self._state(delivery_states, ["waiting", "confirmed", "assigned", "draft", "done", "cancel"]),
                "manufacturing_states": mo_states, "delivery_states": delivery_states,
                "mo_ids": manufacturing.ids, "picking_ids": outgoing.picking_id.ids,
                "unit": unit.name, "unit_xmlid": item.unit_xmlid, "unit_cost": line.product_id.standard_price,
                "price_unit": line.price_unit, "discount": line.discount,
                "last_delivery_at": max(times).isoformat() + "Z" if times else None})
        invoices = [{"id": invoice.id, "name": invoice.name, "state": invoice.state,
                     "payment_state": invoice.payment_state, "total": invoice.amount_total,
                     "residual": invoice.amount_residual, "currency": invoice.currency_id.name, "type": invoice.move_type}
                    for invoice in self.sale_id.invoice_ids]
        return {"reference": self.reference, "revision": self.revision,
                "currency": self.sale_id.currency_id.name,
                "commercial": {"reference": self.reference, "revision": self.revision,
                    "customer": {"code": self.sale_id.partner_id.ref, "name": self.sale_id.partner_id.name},
                    "delivery_date": self.sale_id.commitment_date.date().isoformat() if self.sale_id.commitment_date else None,
                    "terms": self.last_payload.get('terms', '') if html2plaintext(str(self.sale_id.note or '')).strip() == html2plaintext(html.escape(self.last_payload.get('terms', ''))).strip() else html2plaintext(str(self.sale_id.note or '')).strip(),
                    "lines": [{"key": item.key, "sku": item.sale_line_id.product_id.default_code,
                        "quantity": item.sale_line_id.product_uom_qty, "unit_price": item.sale_line_id.price_unit,
                        "discount": item.sale_line_id.discount, "unit": item.unit_xmlid if item.sale_line_id.product_uom_id == self.env.ref(item.unit_xmlid) else None}
                        for item in self.line_ids.sorted('key')]},
                "sale": {"id": self.sale_id.id, "name": self.sale_id.name, "state": self.sale_id.state},
                "lines": result, "invoices": invoices, "synced_at": fields.Datetime.now().isoformat() + "Z"}


class VitaliIntegrationLine(models.Model):
    _name = "vitali.integration.line"
    _description = "Línea externa SmartOrder"

    integration_id = fields.Many2one("vitali.integration", required=True, ondelete="cascade", index=True)
    key = fields.Char(required=True)
    sale_line_id = fields.Many2one("sale.order.line", required=True, ondelete="restrict")
    unit_xmlid = fields.Char(required=True)
    authorized = fields.Boolean(default=False)
    authorized_quantity = fields.Float(default=0)
    _key_unique = models.Constraint("unique(integration_id, key)", "La línea externa ya existe.")
    _sale_line_unique = models.Constraint("unique(sale_line_id)", "La línea de venta ya está vinculada.")
