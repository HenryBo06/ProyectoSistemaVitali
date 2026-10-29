"""Seed explicit SIMULATED tariffs and location quotas in the independent pilot only."""
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
HOME = ROOT / ".local-web-pilot"
os.environ["DJANGO_SETTINGS_MODULE"] = "vitali_web.settings"
os.environ["SMARTORDER_WEB_HOME"] = str(HOME)
os.environ["SMARTORDER_DATABASE_URL"] = json.loads((HOME / "postgres.json").read_text())["url"]

import django
django.setup()
from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import transaction
from operations.models import AccessEvent, OdooCustomerMapping, OdooProductMapping, PriceTariff
from operations.services import active_sales

assert settings.DATABASES["default"]["NAME"] == "smartorder_pilot"
admin = get_user_model().objects.get(username="lab_admin_01", is_staff=True)
catalog = json.loads((ROOT / ".local-odoo/evidence/expanded-catalog.json").read_text(encoding="utf-8"))
points = catalog["sales_point_location_ids"]
assert catalog["status"] == "passed" and len(points) == 3
source = active_sales()
assert source and "simulad" in source.filename.casefold(), "Seed only explicitly synthetic history, never company pricing"
with transaction.atomic():
    for customer in OdooCustomerMapping.objects.all():
        number = int(customer.code.rsplit("-", 1)[1])
        assert customer.code.startswith("VIT-LAB-CLIENT-")
        customer.location_id = points[(number - 1) % 3]
        customer.save(update_fields=["location_id"])
        for product in OdooProductMapping.objects.all():
            latest = source.lines.filter(client=customer.client, product=product.product).order_by("-date", "-pk").first()
            assert latest and latest.unit_price > 0
            PriceTariff.objects.get_or_create(client=customer.client, product=product.product,
                defaults={"unit": product.source_unit, "unit_price": latest.unit_price, "updated_by": admin})
    AccessEvent.objects.create(actor=admin, action="pilot_simulated_setup", target="smartorder_pilot",
        details={"policy": "SIMULATED equal quotas by reviewed LAB sales point; historical sample rates, not official company prices"})
print("Piloto simulado: tarifas y ubicaciones explícitas preparadas; originales preservados.")
