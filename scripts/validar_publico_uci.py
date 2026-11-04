"""Backtest EXTERNO de prioridad de productos, separado de la IA operativa de Vitali.

Fuente UCI Online Retail (2010-2011), CC BY 4.0. No representa ventas
avicolas, no importa registros en Django y no habilita cantidades automaticas.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
from statistics import mean, median
from urllib.request import Request, urlopen
from zipfile import ZipFile

from openpyxl import load_workbook

SOURCE_URL = "https://archive.ics.uci.edu/static/public/352/online+retail.zip"
SOURCE_DOI = "https://doi.org/10.24432/C5BW33"
CUTOFFS = [date(2011, m, 1) for m in range(5, 12)]


def read_sales(source: bytes):
    """Lectura read-only: cancelaciones/devoluciones y clientes no identificados fuera."""
    workbook = load_workbook(BytesIO(source), read_only=True, data_only=True)
    events = []
    dropped = Counter()
    try:
        sheet = workbook.active
        iterator = sheet.iter_rows(values_only=True)
        headers = [str(v).strip().lower().replace(" ", "") for v in next(iterator)]
        needed = ("invoiceno", "stockcode", "quantity", "invoicedate", "unitprice", "customerid")
        if not set(needed).issubset(headers):
            raise ValueError(f"Columnas esperadas UCI ausentes: {set(needed) - set(headers)}")
        pos = {name: headers.index(name) for name in needed}
        for row in iterator:
            invoice, sku = row[pos["invoiceno"]], row[pos["stockcode"]]
            qty, stamp = row[pos["quantity"]], row[pos["invoicedate"]]
            price, customer = row[pos["unitprice"]], row[pos["customerid"]]
            if not invoice or str(invoice).upper().startswith("C"):
                dropped["cancelacion_o_sin_factura"] += 1
                continue
            if customer is None or str(customer).strip() == "":
                dropped["sin_cliente"] += 1
                continue
            if not sku or not isinstance(qty, (int, float)) or not isinstance(price, (int, float)) or qty <= 0 or price <= 0:
                dropped["linea_invalida_o_gratuita"] += 1
                continue
            if isinstance(stamp, datetime):
                day = stamp.date()
            elif isinstance(stamp, date):
                day = stamp
            elif isinstance(stamp, (int, float)) and 25000 <= stamp <= 60000:
                day = date(1899, 12, 30) + timedelta(days=int(stamp))
            else:
                try:
                    day = datetime.fromisoformat(str(stamp)).date()
                except ValueError:
                    dropped["fecha_invalida"] += 1
                    continue
            customer_id = str(int(customer)) if isinstance(customer, (int, float)) else str(customer)
            events.append((day, customer_id, str(sku), str(invoice), float(qty), float(qty * price)))
    finally:
        workbook.close()
    events.sort()
    return events, dict(dropped)


def rank(scores, candidates):
    return sorted(candidates, key=lambda p: (-scores.get(p, (0, 0))[0], -scores.get(p, (0, 0))[1], p))


def frequency(events, *, as_of, recent_days=None):
    """Solo datos anteriores a as_of. La recurrencia cuenta dias distintos."""
    days = defaultdict(set)
    amounts = Counter()
    for day, _, product, _, _, amount in events:
        if day >= as_of or (recent_days is not None and day < as_of - timedelta(days=recent_days)):
            continue
        days[product].add(day)
        amounts[product] += amount
    return {p: (len(ds), amounts[p]) for p, ds in days.items()}


def evaluate(events):
    by_client = defaultdict(list)
    for item in events:
        by_client[item[1]].append(item)
    results = {}
    for horizon in (7, 30):
        cases = []
        for cutoff in CUTOFFS:
            global_scores = frequency(events, as_of=cutoff)
            for customer, rows in by_client.items():
                prior = [r for r in rows if r[0] < cutoff]
                future = {r[2] for r in rows if cutoff <= r[0] < cutoff + timedelta(days=horizon)}
                if len({r[0] for r in prior}) < 3 or not future:
                    continue
                client_scores = frequency(prior, as_of=cutoff)
                if len(client_scores) < 3:
                    continue
                recent_scores = frequency(prior, as_of=cutoff, recent_days=30)
                candidates = list(client_scores)
                chosen = rank(client_scores, candidates)
                global_top = rank(global_scores, candidates)
                recent_top = rank(recent_scores, candidates)
                cases.append({
                    "client": customer,
                    "hit_top1": int(chosen[0] in future),
                    "hit_top3": int(bool(set(chosen[:3]) & future)),
                    "global_top1": int(global_top[0] in future),
                    "recent_top1": int(recent_top[0] in future),
                    "random_expectation_top1": len(set(candidates) & future) / len(candidates),
                })
        results[str(horizon)] = {"evaluaciones_cliente_corte_activas": len(cases),
            "clientes_distintos": len({x["client"] for x in cases}),
            "hit_top1": round(mean(x["hit_top1"] for x in cases), 5) if cases else None,
            "hit_top3": round(mean(x["hit_top3"] for x in cases), 5) if cases else None,
            "global_top1": round(mean(x["global_top1"] for x in cases), 5) if cases else None,
            "recent_top1": round(mean(x["recent_top1"] for x in cases), 5) if cases else None,
            "random_expectation_top1": round(mean(x["random_expectation_top1"] for x in cases), 5) if cases else None,
        }
    # Solo cantidad de la siguiente transaccion del MISMO producto, no demanda diaria.
    invoice_lines = Counter()
    invoice_days = {}
    for day, client, product, invoice, qty, _ in events:
        key = (client, product, invoice)
        invoice_lines[key] += qty
        invoice_days[key] = day
    by_pair = defaultdict(list)
    for (client, product, invoice), qty in invoice_lines.items():
        by_pair[(client, product)].append((invoice_days[(client, product, invoice)], invoice, qty))
    errors_median, errors_last = [], []
    for series in by_pair.values():
        series.sort()
        if len(series) < 6:
            continue
        start = max(4, int(len(series) * 0.75))
        for i in range(start, len(series)):
            history = [r[2] for r in series[max(0, i - 4):i]]
            actual = series[i][2]
            errors_median.append(abs(actual - median(history)))
            errors_last.append(abs(actual - history[-1]))
    results["cantidad_por_transaccion"] = {
        "evaluaciones": len(errors_median),
        "mae_mediana_ultimas_4": round(mean(errors_median), 4) if errors_median else None,
        "mae_ultima_cantidad": round(mean(errors_last), 4) if errors_last else None,
        "nota": "Comparacion solo por cliente-producto y factura; no es un pronostico de demanda por fecha.",
    }
    return results


def download_source():
    req = Request(SOURCE_URL, headers={"User-Agent": "SmartOrder-Audit/1.0 educational reproducible backtest"})
    with urlopen(req, timeout=120) as response:
        raw = response.read(45 * 1024 * 1024 + 1)
    if len(raw) > 45 * 1024 * 1024:
        raise ValueError("Fuente descargada excede limite de 45 MiB")
    with ZipFile(BytesIO(raw)) as archive:
        names = [name for name in archive.namelist() if name.lower().endswith(".xlsx") and not name.startswith("__MACOSX")]
        if len(names) != 1:
            raise ValueError(f"Se esperaba un unico XLSX en el ZIP: {names}")
        with archive.open(names[0]) as f:
            content = f.read(40 * 1024 * 1024 + 1)
        if len(content) > 40 * 1024 * 1024:
            raise ValueError("XLSX excede limite de 40 MiB")
    return content


def test_no_future_leakage():
    base = [(date(2011, 4, 1), "a", "p1", "1", 1., 3.), (date(2011, 4, 2), "a", "p2", "2", 1., 2.)]
    before = frequency(base, as_of=date(2011, 5, 1))
    after = frequency(base + [(date(2011, 6, 1), "a", "p2", "3", 100., 2000.)], as_of=date(2011, 5, 1))
    assert before == after, "Se detecto uso de ventas futuras"
    assert rank(before, list(before)) == rank(after, list(after))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--download-uci", action="store_true")
    parser.add_argument("--file", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if not args.download_uci and not args.file:
        parser.error("Indicar --download-uci o --file")
    test_no_future_leakage()
    content = download_source() if args.download_uci else args.file.read_bytes()
    events, discarded = read_sales(content)
    if not events:
        raise ValueError("No hay transacciones validas para la prueba")
    report = {
        "fuente": SOURCE_DOI if args.download_uci else str(args.file),
        "licencia": "CC BY 4.0 (UCI Online Retail)",
        "sha256_archivo_xlsx": sha256(content).hexdigest(),
        "filas_validas": len(events),
        "filas_excluidas": discarded,
        "periodo": [events[0][0].isoformat(), events[-1][0].isoformat()],
        "cortes": [c.isoformat() for c in CUTOFFS],
        "resultados": evaluate(events),
        "advertencia": "Sector UK regalos 2010-2011. No mide acierto de IA Vitali ni valida datos, unidades o ventas avicolas.",
    }
    serialized = json.dumps(report, indent=2, ensure_ascii=False)
    print(serialized)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(serialized + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
