"""Priorización descriptiva de productos con evaluación cronológica independiente.

Objetivo: producto que reaparece en ventas observadas durante los próximos 7
días, condicionado a que el cliente tenga ventas registradas en el período.
No predice cantidades ni interpreta ausencia de registros como demanda cero.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import date, timedelta
from math import ceil
from typing import Iterable, Sequence


METHOD_LABELS = {
    "historical": "Recurrencia histórica",
    "recent_30d": "Recurrencia de los últimos 30 días",
    "decay_28d": "Recurrencia ponderada por actualidad",
}
METHODS = tuple(METHOD_LABELS)
HORIZON_DAYS = 7
MIN_SELECTION_CASES = 8
MIN_AUDIT_CASES = 6
MAX_EVALUATION_WEEKS = 52
MAX_DAILY_OBSERVATIONS = 5000


@dataclass(frozen=True)
class PriorityDecision:
    method: str
    ranked: tuple[str, ...]
    validated: bool = False
    selection_cases: int = 0
    audit_cases: int = 0
    audit_hits: int = 0
    baseline_audit_hits: int = 0

    @property
    def method_label(self) -> str:
        return METHOD_LABELS[self.method]


def _order(history: Sequence[tuple[date, str]], candidates: Sequence[str],
           origin: date, method: str) -> tuple[str, ...]:
    """Solo observa ventas estrictamente anteriores al corte de decisión."""
    distinct = {(day, product) for day, product in history if day < origin}
    all_days = Counter(product for _, product in distinct)
    if method == "recent_30d":
        metric = Counter(
            product for day, product in distinct
            if origin - timedelta(days=30) <= day
        )
    elif method == "decay_28d":
        metric = Counter()
        for day, product in distinct:
            metric[product] += 0.5 ** ((origin - day).days / 28.0)
    elif method == "historical":
        metric = all_days
    else:
        raise ValueError("Método de prioridad desconocido.")
    return tuple(sorted(candidates, key=lambda product: (
        -metric.get(product, 0), -all_days[product], product.casefold(), product,
    )))


def evaluate_product_priority(
    observations: Iterable[tuple[date, str]], *, as_of: date,
    products: Sequence[str], allow_override: bool = True,
) -> PriorityDecision:
    """Selecciona por partición antigua y acepta SOLO si supera auditoría posterior.

    Una sola serie por cliente; nunca cruza historiales entre clientes. Las
    ventanas futuras se cierran antes de as_of y la selección de método se hace
    con el 60% cronológicamente inicial de los cortes. El 40% posterior audita
    la elección sin seleccionar otro método sobre la auditoría.
    """
    candidates = tuple(dict.fromkeys(products))
    base = PriorityDecision("historical", candidates)
    if not candidates or not isinstance(as_of, date):
        return base
    events = sorted({
        (day, product) for day, product in observations
        if isinstance(day, date) and day < as_of and product in candidates
    })
    if len(events) < 20 or len(events) > MAX_DAILY_OBSERVATIONS:
        return base
    first, last = events[0][0], events[-1][0]
    span = (last - first).days
    origins = [
        first + timedelta(days=56 + index * HORIZON_DAYS)
        for index in range(max(0, (span - 56 - (HORIZON_DAYS - 1)) // HORIZON_DAYS + 1))
    ][-MAX_EVALUATION_WEEKS:]
    if len(origins) < MIN_SELECTION_CASES + MIN_AUDIT_CASES:
        return base
    split = int(len(origins) * 0.6)
    results = {
        "selection": {"cases": 0, "hits": Counter()},
        "audit": {"cases": 0, "hits": Counter()},
    }
    for index, origin in enumerate(origins):
        before = [(day, product) for day, product in events if day < origin]
        future = {
            product for day, product in events
            if origin <= day < origin + timedelta(days=HORIZON_DAYS)
        }
        known = tuple(sorted({product for _, product in before}))
        if len(known) < 3 or not future:
            # Sin venta registrada no hay un resultado positivo/negativo de demanda.
            continue
        part = results["selection" if index < split else "audit"]
        part["cases"] += 1
        for method in METHODS:
            chosen = _order(before, known, origin, method)[0]
            part["hits"][method] += int(chosen in future)

    selection, audit = results["selection"], results["audit"]
    sel_cases, aud_cases = selection["cases"], audit["cases"]
    if sel_cases < MIN_SELECTION_CASES or aud_cases < MIN_AUDIT_CASES:
        return PriorityDecision("historical", candidates, selection_cases=sel_cases,
                                audit_cases=aud_cases, audit_hits=audit["hits"]["historical"],
                                baseline_audit_hits=audit["hits"]["historical"])

    # Elegir SOLO usando la partición de selección. No optimizar con la auditoría.
    chosen = max(METHODS[1:], key=lambda method: (selection["hits"][method], -METHODS.index(method)))
    min_selection_gain = max(2, ceil(0.05 * sel_cases))
    min_audit_gain = max(2, ceil(0.05 * aud_cases))
    validated = bool(
        allow_override
        and selection["hits"][chosen] >= selection["hits"]["historical"] + min_selection_gain
        and audit["hits"][chosen] >= audit["hits"]["historical"] + min_audit_gain
    )
    if not validated:
        return PriorityDecision("historical", candidates, selection_cases=sel_cases,
                                audit_cases=aud_cases, audit_hits=audit["hits"]["historical"],
                                baseline_audit_hits=audit["hits"]["historical"])
    return PriorityDecision(
        chosen, _order(events, candidates, as_of, chosen), True,
        sel_cases, aud_cases, audit["hits"][chosen], audit["hits"]["historical"],
    )
