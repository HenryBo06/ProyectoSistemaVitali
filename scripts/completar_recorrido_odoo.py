"""Odoo shell: preserve a real synthetic integrated manufacturing/delivery/payment scenario."""
import json
from datetime import datetime
from pathlib import Path

assert env.cr.dbname == 'vitali_lab', 'Synthetic laboratory only'
evidence = json.loads(Path('.local-web-lab/comprobacion.json').read_text(encoding='utf-8'))['integration']
params = env['ir.config_parameter'].sudo()
completed_path = Path('.local-odoo/evidence/integrated-operation.json')
if not evidence and completed_path.is_file():
    evidence = json.loads(completed_path.read_text(encoding='utf-8'))
    assert params.get_param('vitali_lab.complete.' + evidence['reference']), 'Completed evidence does not match this laboratory'
assert evidence, 'First run comprobar_smartorder_lab.py --odoo'
record = env['vitali.integration'].search([('reference', '=', evidence['reference'])])
assert len(record) == 1 and record.sale_id.id == evidence['sale_id']
parameter = 'vitali_lab.complete.' + evidence['reference']
if params.get_param(parameter):
    print('PRESERVED: integrated operational scenario already completed')
else:
    sale = record.sale_id
    production = env['mrp.production'].browse(evidence['manufacturing_ids'])
    assert len(production) == 1 and production.state == 'confirmed' and production.product_qty == 3
    assert record.line_ids.authorized and record.line_ids.authorized_quantity == 3
    warehouse = record._warehouse()
    product = production.product_id
    snapshots = [{'step': 'Autorización separada', 'snapshot': record._snapshot()}]
    production.action_assign()
    lot = env['stock.lot'].create({'name': 'LAB-INTEGRADO-' + str(sale.id), 'product_id': product.id, 'company_id': record.company_id.id})
    production.write({'lot_producing_ids': [(6, 0, lot.ids)], 'qty_producing': 3})
    production._set_qty_producing()
    snapshots.append({'step': 'Producción iniciada', 'snapshot': record._snapshot()})
    assert all(abs(move.quantity - move.product_uom_qty) < .001 for move in production.move_raw_ids), 'Receive missing materials before producing'
    production.move_raw_ids.picked = True
    production.button_mark_done()
    assert production.state == 'done'
    sale.picking_ids.action_assign()
    snapshots.append({'step': 'Producción lista; faltante visible', 'snapshot': record._snapshot()})
    picking = sale.picking_ids.filtered(lambda item: item.state not in ('done', 'cancel'))
    assert len(picking) == 1
    missing = max(0, 8 - sum(picking.move_ids.mapped('quantity')))
    purchase = env['purchase.order']
    if missing:
        supplier = env['res.partner'].search([('name', '=', 'Proveedor Vitali LAB-001 (simulado)')])
        assert len(supplier) == 1
        supplier.lang = 'es_ES'
        purchase = env['purchase.order'].create({'partner_id': supplier.id, 'partner_ref': 'LAB-REPOSICION-' + sale.name,
            'picking_type_id': warehouse.in_type_id.id, 'order_line': [(0, 0, {'product_id': product.id,
                'product_qty': missing, 'product_uom_id': product.uom_id.id, 'price_unit': 2, 'date_planned': datetime.now()})]})
        purchase.button_confirm()
        receipt = purchase.picking_ids
        receipt.move_ids.quantity = missing
        receipt.move_line_ids.lot_id = env['stock.lot'].create({'name': 'LAB-COMPRA-' + str(sale.id), 'product_id': product.id, 'company_id': record.company_id.id})
        receipt.move_ids.picked = True
        receipt.button_validate()
        assert receipt.state == 'done'
        purchase.action_create_invoice()
        purchase.invoice_ids.invoice_date = datetime.now().date()
        purchase.invoice_ids.action_post()
    picking.action_assign()
    assert abs(sum(picking.move_ids.mapped('quantity')) - 8) < .001
    snapshots.append({'step': 'Reservado para entregar', 'snapshot': record._snapshot()})
    picking.move_ids.picked = True
    picking.button_validate()
    assert picking.state == 'done'
    invoice = sale._create_invoices()
    invoice.action_post()
    assert abs(invoice.amount_total - 30.4) < .001
    cash = env['account.journal'].create({'name': 'Caja integración LAB ' + str(sale.id), 'code': 'LAB' + str(sale.id), 'type': 'cash'})
    method = cash.inbound_payment_method_line_ids[:1]
    method.payment_account_id = cash.default_account_id
    env['account.payment.register'].with_context(active_model='account.move', active_ids=invoice.ids).create({
        'amount': 20, 'journal_id': cash.id, 'payment_method_line_id': method.id})._create_payments()
    snapshots.append({'step': 'Entrega completa y cobro parcial', 'snapshot': record._snapshot()})
    wizard = env['stock.return.picking'].with_context(active_id=picking.id, active_ids=picking.ids, active_model='stock.picking').create({'picking_id': picking.id})
    wizard.product_return_moves.quantity = 1
    returned = wizard._create_return()
    returned.move_ids.quantity = 1
    returned.move_ids.picked = True
    returned.button_validate()
    assert returned.state == 'done'
    credit = invoice._reverse_moves(default_values_list=[{'ref': 'SIMULADO: devolución 1 kg; ' + sale.name}], cancel=False)
    credit.invoice_line_ids.filtered(lambda line: line.product_id == product).quantity = 1
    credit.action_post()
    assert abs(credit.amount_total - 3.8) < .001
    (invoice.line_ids + credit.line_ids).filtered(lambda line: line.account_id.account_type == 'asset_receivable' and not line.reconciled).reconcile()
    balance = invoice.amount_residual
    assert abs(balance - 6.6) < .001
    env['account.payment.register'].with_context(active_model='account.move', active_ids=invoice.ids).create({
        'amount': balance, 'journal_id': cash.id, 'payment_method_line_id': method.id})._create_payments()
    final = record._snapshot()
    assert final['lines'][0]['net_delivered'] == 7 and final['lines'][0]['outstanding'] == 1
    assert invoice.amount_residual == 0 and invoice.payment_state == 'paid' and credit.amount_residual == 0
    snapshots.append({'step': 'Devolución y nota de crédito conciliadas; 1 kg pendiente', 'snapshot': final})
    params.set_param(parameter, '1')
    env.cr.commit()
    destination = Path('.local-odoo/evidence/integrated-operation.json')
    destination.write_text(json.dumps({'status': 'passed', 'sale_id': sale.id, 'reference': record.reference,
        'additional_purchase_kg': missing, 'purchase_id': purchase.id or None, 'invoice_usd': 30.4,
        'credit_usd': 3.8, 'payments_usd': [20, 6.6], 'balance_usd': 0, 'net_delivered_kg': 7,
        'pending_after_return_kg': 1, 'snapshots': snapshots}, ensure_ascii=False, indent=2), encoding='utf-8')
    print('PASS: authorized 3 kg produced, shortage replenished, 8 kg delivered, 1 kg returned, USD 30.40 invoice / 3.80 credit / zero balance; pending 1 kg remains visible')
