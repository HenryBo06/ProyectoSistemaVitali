"""Restore an offline lab backup into a new temporary database; verify attachment bytes."""
from datetime import datetime
from contextlib import closing
import configparser
import hashlib
import json
import os
from pathlib import Path
import subprocess
import shutil
import sys

import psycopg2
from psycopg2 import sql

root = Path(__file__).resolve().parents[1]
backup = Path(sys.argv[1]).resolve()
assert (backup / 'vitali_lab.dump').is_file(), 'Choose a completed lab backup'
credentials = json.loads((root / '.local-odoo/credentials.json').read_text(encoding='utf-8'))
connection = psycopg2.connect(host='127.0.0.1', port=55432, dbname='postgres',
                            user='postgres', password=credentials['postgres'])
connection.autocommit = True
database = 'vitali_restore_' + datetime.now().strftime('%Y%m%d%H%M%S%f')
created = False
try:
    with connection.cursor() as cursor:
        cursor.execute(sql.SQL('CREATE DATABASE {} OWNER vitali_odoo').format(sql.Identifier(database)))
    created = True
    # ponytail: only this uniquely created scratch database is removed; original lab is never restored over.
    subprocess.run([r'C:\Program Files\PostgreSQL\18\bin\pg_restore.exe', '-h', '127.0.0.1',
        '-p', '55432', '-U', 'vitali_odoo', '--exit-on-error', '-d', database,
        str(backup / 'vitali_lab.dump')], env={**os.environ, 'PGPASSWORD': credentials['database']}, check=True)
    with closing(psycopg2.connect(host='127.0.0.1', port=55432, dbname=database,
                         user='vitali_odoo', password=credentials['database'])) as restored:
        with restored.cursor() as cursor:
            cursor.execute("SELECT state FROM sale_order WHERE client_order_ref = 'VIT-LAB-VENTA-001'")
            assert cursor.fetchone() == ('sale',)
            cursor.execute("SELECT state, product_qty FROM mrp_production WHERE origin = 'S00001'")
            assert cursor.fetchone() == ('draft', 3.0)
            cursor.execute("SELECT store_fname, checksum FROM ir_attachment WHERE name = 'trazabilidad-laboratorio.txt'")
            filename, checksum = cursor.fetchone()
            attachment = (backup / 'filestore' / filename).resolve()
            assert attachment.is_relative_to(backup / 'filestore')
            contents = attachment.read_bytes()
            assert contents == b'VITALI LAB: synthetic evidence; no business data.'
            assert hashlib.sha1(contents).hexdigest() == checksum
    destination = root / '.local-odoo' / 'restores' / database
    destination.mkdir(parents=True, exist_ok=False)
    data = destination / 'data'
    shutil.copytree(backup / 'filestore', data / 'filestore' / database)
    config = configparser.ConfigParser(interpolation=None)
    config.read(root / '.local-odoo/odoo.conf', encoding='utf-8-sig')
    config['options'].update({'db_name': database, 'dbfilter': '^' + database + '$',
                             'data_dir': str(data), 'logfile': str(destination / 'restoration.log')})
    config_path = destination / 'restored.conf'
    with config_path.open('w', encoding='utf-8') as stream:
        config.write(stream)
    # Restore the filestore under the new DB name, then let Odoo itself read it.
    check = """
import json
from pathlib import Path
attachment = env['ir.attachment'].search([('name', '=', 'trazabilidad-laboratorio.txt')])
assert attachment.raw == b'VITALI LAB: synthetic evidence; no business data.'
assert env['sale.order'].search([('client_order_ref','=','VIT-LAB-VENTA-001')]).state == 'sale'
integrations = env['vitali.integration'].search([])
for record in integrations:
    snapshot = record._snapshot()
    assert snapshot['sale']['id'] == record.sale_id.id and snapshot['reference'] == record.reference
evidence = {'status':'passed','native_attachment_read':True,'references_recovered':len(integrations),
            'sales_recovered':env['sale.order'].search_count([]),'database':env.cr.dbname}
if env['ir.config_parameter'].sudo().get_param('vitali_lab.expanded_catalog_version'):
    customers = env['res.partner'].search([('ref','in',[f'VIT-LAB-CLIENT-{n:03d}' for n in range(1,11)])])
    products = env['product.product'].search([('default_code','in',[f'VIT-LAB-PT-{n:03d}' for n in range(1,9)])])
    points = env['stock.location'].search([('name','in',[f'Punto de venta LAB-{n:03d} (simulado)' for n in range(1,4)]),('company_id','=',env.company.id)])
    transit = env['stock.location'].search([('name','=','Tránsito Vitali LAB (simulado)'),('company_id','=',env.company.id)])
    center = env['mrp.workcenter'].search([('code','=','VIT-LAB-CENTRO-001')])
    assert len(customers) == 10 and len(products) == 8 and len(points) == 3 and len(transit) == len(center) == 1
    assert all(p.usage == 'internal' for p in points) and transit.usage == 'transit'
    assert center.resource_calendar_id.name == 'Calendario LAB continuo (simulado)'
    for product in products.filtered(lambda p: p.default_code != 'VIT-LAB-PT-001'):
        bom = env['mrp.bom'].search([('product_tmpl_id','=',product.product_tmpl_id.id)])
        assert len(bom) == 1 and len(bom.bom_line_ids) == 2 and bom.operation_ids.workcenter_id == center
    assert env['product.product'].search_count([('default_code','in',['VIT-LAB-MP-AMPL-001','VIT-LAB-EMP-001'])]) == 2
    evidence['expanded_catalog'] = {'customers':len(customers),'finished_products':len(products),
        'sales_points':len(points),'transit':True,'boms_with_packaging':7,'capacity_calendar':True}
Path(env.context['restore_evidence']).write_text(json.dumps(evidence,indent=2),encoding='utf-8')
env.cr.rollback()
print('PASS: native Odoo application reads restored documents, references and filestore')
"""
    check_path = destination / 'check.py'
    check_path.write_text(check, encoding='utf-8')
    code = 'env = env(context={"restore_evidence": ' + repr(str(destination / 'evidence.json')) + '})\n' + 'exec(open(' + repr(str(check_path)) + ',encoding="utf-8").read())'
    subprocess.run([str(root / '.local-odoo/venv/Scripts/python.exe'), str(root / '.local-odoo/source/odoo-bin'),
        'shell', '-c', str(config_path), '-d', database, '--no-http'], input=code, text=True,
        encoding='utf-8', cwd=root, check=True)
    print('PASS: isolated database and native filestore restoration; evidence:', destination / 'evidence.json')
finally:
    if created:
        with connection.cursor() as cursor:
            cursor.execute(sql.SQL('DROP DATABASE {}').format(sql.Identifier(database)))
    connection.close()
