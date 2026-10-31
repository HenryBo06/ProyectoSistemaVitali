"""Add a reproducible, delivery-dated synthetic dataset to the isolated class LAB."""
from datetime import timedelta
from io import BytesIO
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.preparar_smartorder_lab import LAB_HOME, bootstrap, prepare  # noqa: E402

FILENAME = "ventas_simuladas_fechas_entrega_estables.xlsx"


def main():
    # prepare() and bootstrap() fail closed unless all state stays under .local-web-lab.
    prepare()
    bootstrap()
    from django.contrib.auth import get_user_model
    from django.utils import timezone
    from openpyxl import Workbook
    from operations.models import SalesCoverage, SalesDataset
    from operations.services import import_sales

    existing = SalesDataset.objects.filter(filename=FILENAME).first()
    if existing:
        if (existing.provenance != SalesDataset.Provenance.SIMULATED
                or existing.date_semantics != SalesDataset.DateSemantics.DELIVERY):
            raise RuntimeError("El archivo con ese nombre no es la fuente sintética esperada; no se cambió.")
        print("PRESERVED: dataset sintético de entrega ya existe; no se modificó.")
        return

    admin = get_user_model().objects.filter(username="lab_admin_01", is_staff=True).first()
    if admin is None:
        raise RuntimeError("Falta la cuenta administrativa LAB; no se creó ni cambió ninguna cuenta.")

    today = timezone.localdate()
    end = today - timedelta(days=1)
    start = today - timedelta(days=550)
    clients = [f"Cliente Vitali LAB-{n:03d} (simulado)" for n in range(1, 6)]
    product = "Producto avícola LAB (simulado)"
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Ventas"
    sheet.append(("Fecha", "Cliente", "Producto", "Cantidad", "Precio Unitario", "Unidad",
                  "Zona", "Canal Distribucion", "Canal Venta", "Categoria"))
    day = start
    while day <= end:
        for index, client in enumerate(clients):
            sheet.append((day, client, product, 10 + index, 4, "kg", "Zona LAB simulada",
                          "Canal LAB simulado", "Venta LAB simulada", "Avícola LAB simulada"))
        day += timedelta(days=1)
    source = BytesIO()
    workbook.save(source)
    workbook.close()

    dataset = import_sales(
        source.getvalue(), FILENAME, admin, verified=True, full_snapshot_confirmed=True,
        provenance=SalesDataset.Provenance.SIMULATED,
        date_semantics=SalesDataset.DateSemantics.DELIVERY,
    )
    SalesCoverage.objects.filter(dataset=dataset).update(status=SalesCoverage.Status.COMPLETE)
    dataset.notes = (
        "SIMULADO: patrón constante por cliente para presentación y pruebas; "
        "no representa ventas ni demanda real de Vitali."
    )
    dataset.save(update_fields=["notes"])
    print(f"PASS: fuente sintética aislada, {dataset.row_count} filas, cinco clientes LAB.")
    print(f"Base y archivo están bajo: {LAB_HOME}")


if __name__ == "__main__":
    main()
