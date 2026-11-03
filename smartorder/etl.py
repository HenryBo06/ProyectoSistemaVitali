"""Versioned source-shape inspection for commercial ETL, without activating data.

The extract/transform/validate step is deliberately separate from importing a
verified complete history. No source content or credentials are logged.
"""
from __future__ import annotations

import csv
from datetime import datetime, date
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from io import BytesIO, StringIO
from pathlib import Path
import json
import re

from openpyxl import load_workbook

from .data import COLUMN_ALIASES, CORE_HEADERS, HEADERS, MAX_BYTES, MAX_ROWS, MAX_DAILY_CELLS, _slug

VERSION = "sales-etl-preview-v1"
NUMBER_FIELDS = {"Cantidad_kg_unid", "Precio_Unitario_USD", "Monto_Venta_USD"}
DATE_FIELDS = {"Fecha", "Fecha_entrega_solicitada", "Fecha_entrega_comprometida"}
REQUIRED = set(CORE_HEADERS)
SAFE_PREVIEW = {"Fecha", "Cliente", "Producto", "Cantidad_kg_unid", "Precio_Unitario_USD", "Monto_Venta_USD", "Unidad"}


def _headers(raw_headers):
    names = [str(x or "").strip() for x in raw_headers]
    if not any(names) or any(not x for x in names):
        raise ValueError("Todas las columnas de la primera fila necesitan un encabezado.")
    slugs = [_slug(x) for x in names]
    if len(set(slugs)) != len(slugs):
        raise ValueError("Existen encabezados repetidos después de normalizarlos.")
    return names


def _extract(raw: bytes, filename: str, sheet_name: str = ""):
    extension = Path(filename).suffix.lower()
    if not raw or len(raw) > MAX_BYTES:
        raise ValueError("El archivo debe tener contenido y no superar 20 MB.")
    if extension == ".csv":
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise ValueError("El CSV debe ser UTF-8 (con o sin BOM).") from exc
        if "\0" in text:
            raise ValueError("El archivo contiene caracteres binarios.")
        # Choose a delimiter only when the header is unambiguous.
        candidates = []
        first_line = text.splitlines()[0] if text.splitlines() else ""
        for sep in (",", ";", "\t"):
            try:
                header = next(csv.reader([first_line], delimiter=sep, strict=True), [])
                if len(header) >= 2:
                    candidates.append((len(header), sep))
            except csv.Error:
                continue
        if not candidates:
            raise ValueError("No se identificaron columnas CSV; use coma, punto y coma o tabulador.")
        candidates.sort(reverse=True)
        if len(candidates) > 1 and candidates[0][0] == candidates[1][0]:
            raise ValueError("El delimitador es ambiguo; revise el archivo.")
        delim = candidates[0][1]
        reader = csv.reader(StringIO(text, newline=""), delimiter=delim, strict=True)
        header = _headers(next(reader, []))
        return header, reader, "csv", delim, None
    if extension == ".xlsx":
        if not raw.startswith(b"PK"):
            raise ValueError("El archivo indicado como XLSX no es un Excel válido.")
        try:
            book = load_workbook(BytesIO(raw), read_only=True, data_only=True)
        except Exception as exc:
            raise ValueError("No se pudo leer el libro XLSX.") from exc
        if sheet_name and sheet_name not in book.sheetnames:
            book.close()
            raise ValueError(
                "La hoja de Excel solicitada no existe. Revise el nombre y seleccione la hoja correcta."
            )
        selected = book[sheet_name] if sheet_name else book.worksheets[0]
        reader = selected.iter_rows(values_only=True)
        header = _headers(next(reader, []))
        return header, reader, "xlsx", "", book
    raise ValueError("Solo se admiten CSV UTF-8 o Excel XLSX en esta etapa ETL.")


def _mapping(source_headers: list[str], manual: dict | None):
    if not isinstance(manual, dict):
        raise ValueError("El mapeo de columnas debe ser un objeto.")
    aliases = {name: {_slug(name), *(_slug(a) for a in COLUMN_ALIASES.get(name, ()))}
               for name in HEADERS}
    by_slug = {_slug(name): name for name in source_headers}
    result = {}
    for canonical in HEADERS:
        explicit = manual.get(canonical, "")
        if explicit:
            if not isinstance(explicit, str) or explicit not in source_headers:
                raise ValueError(f"El campo {canonical} apunta a una columna inexistente.")
            result[canonical] = explicit
        else:
            found = [by_slug[key] for key in aliases[canonical] if key in by_slug]
            if len(found) == 1:
                result[canonical] = found[0]
            elif len(found) > 1:
                raise ValueError(f"Se encontraron varias columnas posibles para {canonical}; seleccione una.")
    used = list(result.values())
    if len(set(used)) != len(used):
        raise ValueError("Una columna de origen no puede representar dos conceptos diferentes.")
    unknown = [key for key in manual if key not in HEADERS]
    if unknown:
        raise ValueError("El mapeo contiene destinos no reconocidos.")
    return result


def _date(value, fmt: str):
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if not isinstance(value, str):
        raise ValueError("fecha inválida")
    try:
        return datetime.strptime(value.strip(), fmt).date().isoformat()
    except ValueError as exc:
        raise ValueError("fecha inválida para el formato seleccionado") from exc


def _decimal(value, symbol: str):
    if isinstance(value, bool):
        raise ValueError("no es un número")
    if isinstance(value, (float, int, Decimal)):
        number = Decimal(str(value))
    else:
        string = str(value or "").strip()
        if symbol == ",":
            expression = r"[0-9]+(?:,[0-9]+)?"
            string = string.replace(",", ".")
        else:
            expression = r"[0-9]+(?:\.[0-9]+)?"
        if not re.fullmatch(expression, str(value or "").strip()):
            raise ValueError("formato numérico ambiguo o separador de millares")
        try:
            number = Decimal(string)
        except InvalidOperation as exc:
            raise ValueError("número no válido") from exc
    if not number.is_finite() or number < 0:
        raise ValueError("cantidad o precio fuera de rango")
    return number


def preview_sales(raw: bytes, filename: str, *, manual: dict | None = None,
                  date_format: str = "%Y-%m-%d", decimal_separator: str = ".",
                  sheet_name: str = "") -> dict:
    """Profile a source. Errors prevent loading; no records are persisted."""
    if date_format not in {"%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y"}:
        raise ValueError("Seleccione explícitamente el formato de fecha.")
    if decimal_separator not in {".", ","}:
        raise ValueError("Seleccione punto o coma decimal.")
    header, rows, kind, delimiter, book = _extract(raw, filename, sheet_name)
    try:
        if kind == "csv" and delimiter == "," and decimal_separator == ",":
            raise ValueError("CSV con coma decimal requiere punto y coma o tabulador como separador.")
        mapping = _mapping(header, manual or {})
        missing = sorted(REQUIRED - mapping.keys())
        samples = []
        issues = []
        count = 0
        valid = 0
        clients = set()
        products = set()
        dates = []
        pairs = set()
        unit_missing = 0
        duplicate_refs = set()
        duplicate_count = 0
        max_issues = 25
        if missing:
            issues.append("Faltan destinos obligatorios: " + ", ".join(missing)
                          + ". Configure el mapeo antes de cargar.")
        byname = {name: i for i, name in enumerate(header)}
        try:
            for row_number, row in enumerate(rows, start=2):
                if not any(cell is not None and str(cell).strip() for cell in row):
                    continue
                count += 1
                if count > MAX_ROWS:
                    raise ValueError(f"El origen supera el límite de {MAX_ROWS:,} filas.")
                if len(row) != len(header):
                    if len(issues) < max_issues:
                        issues.append(f"Fila {row_number}: número de columnas inesperado.")
                    continue
                if missing:
                    continue
                try:
                    record = {}
                    for canonical, original in mapping.items():
                        value = row[byname[original]]
                        if canonical in DATE_FIELDS:
                            if value is None or str(value).strip() == "":
                                if canonical in REQUIRED:
                                    raise ValueError(f"Falta {canonical}")
                                continue
                            record[canonical] = _date(value, date_format)
                        elif canonical in NUMBER_FIELDS:
                            if value is None or str(value).strip() == "":
                                if canonical in REQUIRED:
                                    raise ValueError(f"Falta {canonical}")
                                continue
                            record[canonical] = _decimal(value, decimal_separator)
                        else:
                            record[canonical] = str(value or "").strip()
                    if not record.get("Cliente") or not record.get("Producto"):
                        raise ValueError("Cliente y Producto son obligatorios.")
                    amount = record.get("Monto_Venta_USD")
                    expected = record["Cantidad_kg_unid"] * record["Precio_Unitario_USD"]
                    if amount is not None and abs(expected - amount) > Decimal("0.02"):
                        raise ValueError("Monto de venta no coincide con cantidad por precio.")
                    if amount is None:
                        record["Monto_Venta_USD"] = expected
                    key = (record.get("Referencia_pedido"), record.get("Referencia_linea"))
                    if all(key):
                        if key in duplicate_refs:
                            duplicate_count += 1
                        duplicate_refs.add(key)
                    if not record.get("Unidad"):
                        unit_missing += 1
                    clients.add(record["Cliente"])
                    products.add(record["Producto"])
                    pairs.add((record["Cliente"], record["Producto"]))
                    dates.append(record["Fecha"])
                    valid += 1
                    if len(samples) < 5:
                        samples.append({name: str(record.get(name, "")) for name in SAFE_PREVIEW})
                except (ValueError, KeyError, IndexError) as exc:
                    if len(issues) < max_issues:
                        issues.append(f"Fila {row_number}: {exc}")
        except csv.Error as exc:
            raise ValueError("El CSV contiene comillas o estructura inválida.") from exc
        if count == 0:
            issues.append("La fuente no contiene registros.")
        if duplicate_count:
            issues.append(f"{duplicate_count} referencias de línea repetidas; revise duplicados.")
        daily_cells = 0
        if dates:
            start = date.fromisoformat(min(dates))
            end = date.fromisoformat(max(dates))
            daily_cells = len(pairs) * ((end - start).days + 1)
            if daily_cells > MAX_DAILY_CELLS:
                issues.append(
                    f"El rango temporal y {len(pairs)} combinaciones cliente-producto generarían "
                    f"{daily_cells:,} celdas diarias, por encima del límite de "
                    f"{MAX_DAILY_CELLS:,} del motor de inferencia. Revise fechas extremas o "
                    "segmente el análisis antes de activar datos."
                )
        unused = [x for x in header if x not in mapping.values()]
        notes = []
        if unused:
            notes.append(f"{len(unused)} columna(s) sin mapear: " + ", ".join(unused[:12]))
        if unit_missing:
            notes.append(f"{unit_missing} fila(s) sin unidad confirmada; no habilitar cantidades predictivas.")
        if not mapping.get("Referencia_linea"):
            notes.append("No existe identificador de línea; futuras cargas incrementales necesitan claves estables.")
        schema_digest = sha256(json.dumps(header, ensure_ascii=False).encode("utf-8")).hexdigest()
        return {
            "version": VERSION, "filename": Path(filename).name, "format": kind,
            "sha256": sha256(raw).hexdigest(), "schema_sha256": schema_digest,
            "headers": header, "mapping": mapping, "missing_required": missing,
            "row_count": count, "valid_rows": valid, "invalid_rows": count - valid,
            "date_min": min(dates) if dates else None, "date_max": max(dates) if dates else None,
            "client_count": len(clients), "product_count": len(products),
            "duplicate_references": duplicate_count,
            "estimated_daily_cells": daily_cells, "unmapped": unused,
            "notes": notes, "issues": issues, "samples": samples,
            "ready_to_review": count > 0 and valid == count and not issues,
        }
    finally:
        if book is not None:
            book.close()


def canonical_sales_xlsx(raw: bytes, filename: str, *, manual: dict | None = None,
                         date_format: str = "%Y-%m-%d", decimal_separator: str = ".",
                         sheet_name: str = "") -> tuple[bytes, dict]:
    """Revalidate and normalize one approved CSV/XLSX source for the existing importer.

    Preview and Load share extraction, mapping and row rules. The returned XLSX
    is an internal canonical artifact, not a new independent history parser.
    The original bytes are neither logged nor stored by the preview.
    """
    from openpyxl import Workbook

    report = preview_sales(raw, filename, manual=manual, date_format=date_format,
                           decimal_separator=decimal_separator, sheet_name=sheet_name)
    if not report["ready_to_review"]:
        raise ValueError("La fuente presenta problemas ETL; inspeccione y corrija antes de cargar.")

    headers, rows, kind, _, book = _extract(raw, filename, sheet_name)
    target = Workbook(write_only=True)
    sheet = target.create_sheet("Ventas")
    sheet.append(list(HEADERS))
    positions = {header: index for index, header in enumerate(headers)}
    processed = 0
    try:
        for row in rows:
            if not any(value is not None and str(value).strip() for value in row):
                continue
            if len(row) != len(headers):
                raise ValueError("La estructura cambió durante la normalización.")
            result = {}
            for canonical, source in report["mapping"].items():
                value = row[positions[source]]
                if canonical in DATE_FIELDS:
                    result[canonical] = (_date(value, date_format)
                                         if value is not None and str(value).strip() else None)
                elif canonical in NUMBER_FIELDS:
                    result[canonical] = (float(_decimal(value, decimal_separator))
                                         if value is not None and str(value).strip() else None)
                else:
                    result[canonical] = str(value if value is not None else "").strip()
            sheet.append([result.get(name) for name in HEADERS])
            processed += 1
        if processed != report["valid_rows"]:
            raise ValueError("El total de registros cambió durante la normalización.")
        output = BytesIO()
        target.save(output)
        normalized = output.getvalue()
        if len(normalized) > MAX_BYTES:
            raise ValueError("El Excel normalizado supera los 20 MB; divida el lote.")
        return normalized, report
    except csv.Error as exc:
        raise ValueError("La fuente contiene una estructura CSV inválida.") from exc
    finally:
        if book is not None:
            book.close()
