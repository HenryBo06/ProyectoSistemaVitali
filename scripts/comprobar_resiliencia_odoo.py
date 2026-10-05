"""Real JSON-2 first-create race, lost response and safe cancellation in the synthetic lab."""
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
import json
from pathlib import Path
from threading import Barrier
from urllib.error import HTTPError
from urllib.request import Request, build_opener, ProxyHandler
from uuid import uuid4

root = Path(__file__).resolve().parents[1]
config = json.loads((root / '.local-odoo/smartorder.json').read_text(encoding='utf-8'))
assert config['database'] == 'vitali_lab' and config['url'] == 'http://127.0.0.1:8079'


def rpc(method, **values):
    request = Request(config['url'] + '/json/2/vitali.integration/' + method,
        data=json.dumps(values, allow_nan=False).encode(), method='POST',
        headers={'Authorization': 'Bearer ' + config['key'], 'X-Odoo-Database': config['database'], 'Content-Type': 'application/json'})
    with build_opener(ProxyHandler({})).open(request, timeout=30) as response:
        return json.load(response)


reference = 'smartorder:resilience-' + uuid4().hex
payload = {'reference': reference, 'revision': 1, 'customer': {'code': 'VIT-LAB-CLIENT-005', 'name': 'Cliente Vitali LAB-005 (simulado)'},
    'delivery_date': (date.today() + timedelta(days=3)).isoformat(), 'terms': 'SIMULADO: concurrencia y respuesta perdida',
    'lines': [{'key': 'resilience-1', 'sku': 'VIT-LAB-PT-001', 'quantity': 1, 'unit_price': 4, 'discount': 0, 'unit': 'uom.product_uom_kgm'}]}
gate = Barrier(5)


def first_create(_):
    gate.wait(timeout=15)
    return rpc('sync_sale', payload=payload)


with ThreadPoolExecutor(max_workers=5) as workers:
    replies = list(workers.map(first_create, range(5)))
ids = {reply['sale']['id'] for reply in replies}
assert len(ids) == 1 and all(not reply['lines'][0]['mo_ids'] for reply in replies)
# The caller discards a successful reply: the next identical attempt must recover it.
rpc('sync_sale', payload=payload)
recovered = rpc('sync_sale', payload=payload)
assert recovered['sale']['id'] in ids
invalid = {**payload, 'lines': [{**payload['lines'][0], 'quantity': 2}]}
try:
    rpc('sync_sale', payload=invalid)
except HTTPError as error:
    assert error.code >= 400
else:
    raise AssertionError('A reused revision with different data was accepted')
try:
    rpc('cancel_sale', reference=reference, revision=2)
except HTTPError as error:
    assert error.code >= 400
else:
    raise AssertionError('Cancellation with a stale revision was accepted')
canceled = rpc('cancel_sale', reference=reference, revision=1)
assert canceled['sale']['state'] == 'cancel' and canceled['lines'][0]['reserved'] == 0
assert rpc('cancel_sale', reference=reference, revision=1)['sale']['id'] in ids
evidence = {'status': 'passed', 'reference': reference, 'sale_id': ids.pop(), 'simultaneous_first_creates': 5,
            'lost_response_retry': True, 'conflicting_payload_rejected': True, 'stale_cancel_rejected': True,
            'idempotent_cancel': True, 'automatic_manufacturing': False}
destination = root / '.local-odoo/evidence/resilience.json'
destination.parent.mkdir(parents=True, exist_ok=True)
destination.write_text(json.dumps(evidence, indent=2), encoding='utf-8')
print('PASS: five simultaneous first creates, discarded response retry, revision conflicts and idempotent cancellation')
