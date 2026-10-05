"""Odoo shell: restricted bot and synthetic catalog, preserving existing records."""
import json
import secrets
from datetime import datetime, timedelta
from pathlib import Path

from odoo import Command

assert env.cr.dbname == 'vitali_lab', 'Only the isolated laboratory may be provisioned'
integration = env['vitali.integration']._operation()
company = integration.env.company
company.logo = False  # No verified corporate logo is supplied; omit the native placeholder.
warehouse = integration._warehouse()
group = env.ref('vitali_lab.group_integration')
env.ref('base.user_admin').group_ids = [Command.link(env.ref('uom.group_uom').id)]
service = env['res.users'].with_context(active_test=False).search([('login', '=', 'vitali_connector')])
values = {'name': 'Servicio SmartOrder (laboratorio)', 'active': True,
          'group_ids': [Command.set([group.id])], 'company_id': company.id,
          'company_ids': [Command.set([company.id])], 'lang': 'es_ES', 'tz': 'America/El_Salvador'}
if service:
    service.write(values)
else:
    # An unknown random password is never retained; the bot uses only its expiring API key.
    service = env['res.users'].create({**values, 'login': 'vitali_connector', 'password': secrets.token_urlsafe(48)})
assert service.all_group_ids == group and service.share
params = env['ir.config_parameter'].sudo()
params.set_param('vitali_lab.integration_company_id', company.id)
params.set_param('vitali_lab.integration_warehouse_id', warehouse.id)
pricelist = env['product.pricelist'].search([('currency_id', '=', company.currency_id.id), ('company_id', 'in', [False, company.id])], limit=1)
if not pricelist:
    pricelist = env['product.pricelist'].create({'name': 'Vitali LAB USD (simulado)', 'currency_id': company.currency_id.id, 'company_id': company.id})
for index in range(1, 6):
    name = f'Cliente Vitali LAB-{index:03d} (simulado)'
    code = f'VIT-LAB-CLIENT-{index:03d}'
    customer = env['res.partner'].search([('ref', '=', code)]) or env['res.partner'].search([('name', '=', name)])
    assert len(customer) <= 1, 'Resolve duplicated synthetic customer before continuing'
    if customer:
        customer.write({'ref': code, 'property_product_pricelist': pricelist.id, 'lang': 'es_ES'})
    else:
        env['res.partner'].create({'name': name, 'ref': code, 'customer_rank': 1, 'company_id': company.id, 'property_product_pricelist': pricelist.id, 'lang': 'es_ES'})
    assert (customer or env['res.partner'].search([('ref', '=', code)])).property_product_pricelist.currency_id == company.currency_id

# ponytail: add one marked inventory lot for the integrated demo; the original seed stays reserved.
if not params.get_param('vitali_lab.integration_stock_version'):
    product = env['product.product'].search([('default_code', '=', 'VIT-LAB-PT-001')])
    assert len(product) == 1
    lot = env['stock.lot'].create({'name': 'LAB-PT-INTEGRACION-001', 'product_id': product.id, 'company_id': company.id})
    quant = env['stock.quant'].with_context(inventory_mode=True).create({'product_id': product.id,
        'location_id': warehouse.lot_stock_id.id, 'lot_id': lot.id, 'inventory_quantity': 5})
    quant.action_apply_inventory()
    params.set_param('vitali_lab.integration_stock_version', '1')

path = Path('.local-odoo/smartorder.json')
existing = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
keep = False
if existing.get('key') and existing.get('expires_at'):
    keep = datetime.fromisoformat(existing['expires_at']) > datetime.utcnow() + timedelta(days=1)
    if keep:
        keep = env['res.users.apikeys']._check_credentials(scope='rpc', key=existing['key']) == service.id
if not keep:
    expiry = datetime.utcnow() + timedelta(days=90)
    key = env['res.users.apikeys'].with_user(service).sudo()._generate('rpc', 'SmartOrder laboratorio', expiry)
    existing = {'key': key, 'expires_at': expiry.isoformat()}
config = {**existing, 'enabled': True, 'url': 'http://127.0.0.1:8079', 'database': 'vitali_lab'}
env.cr.commit()
temporary = path.with_suffix('.tmp')
temporary.write_text(json.dumps(config, indent=2), encoding='utf-8')
temporary.replace(path)
assert integration.with_user(service).ping()['currency'] == 'USD'
print('PASS: restricted service, five synthetic customer codes, explicit USD and private expiring API key configured')
