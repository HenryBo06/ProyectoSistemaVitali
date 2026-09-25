"""Datos deterministas y cuentas locales para presentar SmartOrder sin preparación manual."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from io import BytesIO
from pathlib import Path

import numpy as np
from openpyxl import Workbook

from .data import HEADERS, load_sales
from .storage import Store


DEMO_ADMIN = ("admin.demo", "VitaliDemo2026!")
DEMO_SELLER = ("vendedor.demo", "VitaliDemo2026!")


def demo_workbook(today: date) -> bytes:
    """Construye 15 meses de ventas sintéticas con tendencia y estacionalidad conocidas."""
    clients = {
        "Supermercado Central": ("Centro", "Supermercado", "Retail", 1.18),
        "Distribuidora Norte": ("Norte", "Distribuidor", "Mayorista", 1.42),
        "Restaurante El Buen Sabor": ("Centro", "Horeca", "Horeca", 0.82),
        "Mercado La Familia": ("Occidente", "Mercado", "Retail", 0.96),
    }
    products = {
        "Pechuga de pollo": ("Fresco", 42.0, 3.70),
        "Pierna de pollo": ("Fresco", 55.0, 2.80),
        "Pollo entero": ("Fresco", 35.0, 3.10),
        "Alitas de pollo": ("Fresco", 28.0, 3.45),
        "Huevos medianos": ("Huevos", 68.0, 0.18),
    }
    generator = np.random.default_rng(73)
    start = today - timedelta(days=455)
    book = Workbook(write_only=True)
    sheet = book.create_sheet("Ventas_demo")
    sheet.append(HEADERS)
    for offset in range(455):
        day = start + timedelta(days=offset)
        annual = 1 + 0.17 * np.sin(2 * np.pi * day.timetuple().tm_yday / 365)
        weekend = 1.13 if day.weekday() in (4, 5) else 0.94 if day.weekday() == 0 else 1
        growth = 0.88 + 0.24 * offset / 454
        for client, (zone, distribution, channel, client_factor) in clients.items():
            for product, (category, base, price) in products.items():
                promotion = 1.35 if (offset + len(client) + len(product)) % 43 < 4 else 1
                mean = base * client_factor * annual * weekend * growth * promotion
                quantity = max(0, round(float(generator.normal(mean, mean * 0.12)), 2))
                sheet.append((
                    datetime.combine(day, datetime.min.time()), client, zone, distribution,
                    channel, product, category, quantity, price, round(quantity * price, 2),
                ))
    output = BytesIO()
    book.save(output)
    return output.getvalue()


def seed_local_demo(store: Store, directory: Path, today: date) -> bool:
    """Inicializa una instalación aislada; nunca modifica una instalación con usuarios."""
    if store.has_users():
        return False
    directory.mkdir(parents=True, exist_ok=True)
    raw = demo_workbook(today)
    data = load_sales(raw)
    path = directory / "ventas_demo_vitali.xlsx"
    path.write_bytes(raw)
    admin_id = store.bootstrap_admin(*DEMO_ADMIN)
    seller_id = store.create_user(admin_id, *DEMO_SELLER, role="vendedor")
    store.activate_dataset(
        admin_id, data.digest, str(path), path.name, data.first_date.isoformat(),
        data.last_date.isoformat(), len(data.rows),
    )
    store.set_assignments(admin_id, seller_id, data.clients)
    return True
