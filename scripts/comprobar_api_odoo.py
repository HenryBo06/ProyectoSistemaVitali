"""Local JSON-2 smoke test, not the SmartOrder synchronization connector."""
import json
from pathlib import Path
import subprocess
import requests

root = Path(__file__).resolve().parents[1]
subprocess.run([str(root / '.local-odoo/venv/Scripts/python.exe'),
    str(root / '.local-odoo/source/odoo-bin'), 'shell', '-c', str(root / '.local-odoo/odoo.conf'),
    '-d', 'vitali_lab', '--no-http'], input='''
import json
from datetime import datetime, timedelta
from pathlib import Path
assert env.cr.dbname == 'vitali_lab'
key = env['res.users.apikeys'].with_user(env.ref('base.user_admin')).sudo()._generate(
    'rpc', 'Local JSON-2 probe', datetime.now() + timedelta(minutes=30))
Path('.local-odoo/api-probe.json').write_text(json.dumps({'key': key}), encoding='utf-8')
env.cr.commit()
''', text=True, cwd=root, check=True, capture_output=True)
url = "http://127.0.0.1:8079/json/2/"
key = json.loads((root / ".local-odoo/api-probe.json").read_text(encoding="utf-8"))["key"]
headers = {"Authorization": "Bearer " + key, "X-Odoo-Database": "vitali_lab"}
session = requests.Session()
session.trust_env = False


def call(model, method, values):
    response = session.post(url + model + "/" + method, headers=headers, json=values, timeout=30)
    response.raise_for_status()
    return response.json()


product = call("product.product", "search_read", {
    "domain": [["default_code", "=", "VIT-LAB-PT-001"]], "fields": ["id", "name", "uom_id"], "limit": 1})[0]
customer = call("res.partner", "search_read", {
    "domain": [["name", "=", "Cliente Vitali LAB-001 (simulado)"]], "fields": ["id"], "limit": 1})[0]
existing = call("sale.order", "search_read", {
    "domain": [["client_order_ref", "=", "VIT-LAB-API-SMOKE"]], "fields": ["id", "state"], "limit": 1})
if existing:
    draft_id = existing[0]["id"]
else:
    # ponytail: one sequential local probe; this search/create is not concurrent-safe integration.
    draft_id = call("sale.order", "create", {"vals_list": [{"partner_id": customer["id"],
        "client_order_ref": "VIT-LAB-API-SMOKE", "note": "Borrador sintético de comprobación JSON-2.",
        "order_line": [[0, 0, {"product_id": product["id"], "product_uom_qty": 1,
            "product_uom_id": product["uom_id"][0], "price_unit": 4}]]}]})[0]
draft = call("sale.order", "read", {"ids": [draft_id], "fields": ["name", "state"]})[0]
assert draft["state"] == "draft", "API probe must not confirm the sale"
unauthorized = session.post(url + "product.product/search_read", json={"domain": []}, timeout=30)
assert unauthorized.status_code in (401, 403)
print("PASS: authenticated JSON-2 product read and synthetic draft " + draft["name"] + "; unauthenticated request denied")
