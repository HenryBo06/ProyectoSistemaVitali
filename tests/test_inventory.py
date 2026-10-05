from datetime import date, timedelta
from io import BytesIO
import unittest

from openpyxl import Workbook

from smartorder.inventory import inventory_template, load_inventory


def sample(rows, flows=()):
    book = Workbook()
    sheet = book.active
    sheet.title = "Inventario"
    sheet.append(("Cliente", "Producto", "Existencia_Disponible", "Fecha_Corte", "Unidad"))
    for row in rows:
        sheet.append(row)
    extra = book.create_sheet("Movimientos")
    extra.append(("Tipo", "Cliente", "Producto", "Cantidad", "Fecha"))
    for row in flows:
        extra.append(row)
    output = BytesIO()
    book.save(output)
    return output.getvalue()


class InventoryTests(unittest.TestCase):
    def test_template_round_trip_and_separate_flows(self):
        self.assertTrue(inventory_template().startswith(b"PK"))
        data = load_inventory(sample(
            [("Cliente A", "Alitas", 20, date.today(), "kg")],
            [("Entrada", "Cliente A", "Alitas", 5, date.today() + timedelta(days=2)),
             ("Compromiso_Cliente", "Cliente A", "Alitas", 8, date.today() + timedelta(days=4))],
        ))
        self.assertEqual(data.stocks[0].available, 20)
        self.assertEqual([flow.kind for flow in data.flows],
                         ["incoming", "customer_commitment"])

    def test_duplicate_stock_is_rejected(self):
        raw = sample([("A", "Pollo", 1, date.today(), ""),
                      ("A", "Pollo", 2, date.today(), "")])
        with self.assertRaisesRegex(ValueError, "repetidos"):
            load_inventory(raw)

    def test_unknown_flow_is_rejected(self):
        raw = sample([("A", "Pollo", 1, date.today(), "")],
                     [("Ajuste", "A", "Pollo", 2, date.today() + timedelta(days=1))])
        with self.assertRaisesRegex(ValueError, "desconocido"):
            load_inventory(raw)

    def test_future_stock_cutoff_is_rejected(self):
        raw = sample([("A", "Pollo", 2, date.today() + timedelta(days=1), "kg")])
        with self.assertRaisesRegex(ValueError, "no puede estar en el futuro"):
            load_inventory(raw)

    def test_invalid_quantity_is_rejected(self):
        raw = sample([("A", "Pollo", -1, date.today(), "")])
        with self.assertRaisesRegex(ValueError, "no negativo"):
            load_inventory(raw)


if __name__ == "__main__":
    unittest.main()
