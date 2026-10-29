"""Recomendaciones de ventas para los siete días posteriores a una entrega.

Las cifras son ventas observadas, no demanda perdida por falta de existencias.
Una referencia histórica sin validación nunca se convierte en pedido automático.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

import numpy as np
import pandas as pd

from .data import SalesData


METHODS = ("Últimos 7 días", "Promedio de 28 días", "Promedio de 56 días",
           "Mismos 7 días del año anterior", "XGBoost")
# ponytail: 8 semanas para escoger y 8 posteriores para auditar; con varios
# años y mayor volumen, ampliar ventanas antes de recalibrar por cliente.
MIN_BACKTEST_WINDOWS = 16
MIN_POSITIVE_AUDIT_PERIODS = 5
SELECTION_WINDOWS = MIN_BACKTEST_WINDOWS // 2
SeriesKey = tuple[str, str, str]


@dataclass(frozen=True)
class BacktestReport:
    """Comparación por cliente, producto y unidad con ventanas cronológicas separadas."""

    scores: pd.DataFrame
    winners: dict[str, str]
    eligible_products: frozenset[str]
    windows: int
    audit_targets: int = 0
    eligible_audit_targets: int = 0
    series_winners: dict[SeriesKey, str] = field(default_factory=dict)
    eligible_series: frozenset[SeriesKey] = frozenset()

    @property
    def audit_coverage(self) -> float | None:
        if self.audit_targets == 0:
            return None
        return self.eligible_audit_targets / self.audit_targets

    def audit_comparison(self) -> dict[str, dict[str, float | int | None]]:
        """Resume los métodos elegidos y la base de 28 días en las mismas semanas."""
        empty = {"targets": 0, "mae": None, "wape": None, "signed_bias": None}
        if self.scores.empty or not (self.series_winners or self.winners):
            return {"selected": empty.copy(), "baseline_28d": empty.copy()}

        audit = self.scores[self.scores["partition"] == "auditoría"]
        selected_rows = []
        baseline_rows = []
        winners = self.series_winners or {("", product, ""): method for product, method in self.winners.items()}
        for (client, product, unit), method in winners.items():
            if "Cliente" not in self.scores:
                selected = audit[(audit["Producto"] == product) & (audit["method"] == method)]
                baseline = audit[(audit["Producto"] == product) & (audit["method"] == METHODS[1])]
            else:
                selected = audit[
                    (audit["Cliente"] == client) & (audit["Producto"] == product)
                    & (audit["Unidad"] == unit) & (audit["method"] == method)
                ]
                baseline = audit[
                    (audit["Cliente"] == client) & (audit["Producto"] == product)
                    & (audit["Unidad"] == unit) & (audit["method"] == METHODS[1])
                ]
            if not selected.empty and not baseline.empty:
                selected_rows.append(selected.iloc[0])
                baseline_rows.append(baseline.iloc[0])

        def aggregate(rows) -> dict[str, float | int | None]:
            if not rows:
                return empty.copy()
            targets = sum(int(row["periods"]) for row in rows)
            actual_total = sum(float(row["actual_total"]) for row in rows)
            absolute_error = sum(float(row["absolute_error_total"]) for row in rows)
            signed_error = sum(float(row["signed_error_total"]) for row in rows)
            return {
                "targets": targets,
                "mae": absolute_error / targets if targets else None,
                "wape": absolute_error / actual_total if actual_total > 0 else None,
                "signed_bias": signed_error / actual_total if actual_total > 0 else None,
            }

        return {
            "selected": aggregate(selected_rows),
            "baseline_28d": aggregate(baseline_rows),
        }


class RecommendationEngine:
    """Conserva las cantidades separadas por producto y cliente."""

    def __init__(self, data: SalesData) -> None:
        self.data = data
        self._daily = data.daily().pivot(
            index="Fecha", columns=["Cliente", "Producto", "Unidad"], values="Cantidad_kg_unid"
        ).sort_index(axis=1)
        self._pairs = list(self._daily.columns)
        rows = data.rows.copy()
        if "Unidad" not in rows:
            rows["Unidad"] = ""
        rows["Unidad"] = rows["Unidad"].fillna("").astype(str).str.strip().str.casefold()
        self._pair_first_dates = {
            key: pd.Timestamp(first_seen).date()
            for key, first_seen in rows.groupby(["Cliente", "Producto", "Unidad"])["Fecha"].min().items()
        }
        self._products = np.array([product for _, product, _ in self._pairs], dtype=object)
        self._backtests: dict[tuple[int, date, int], BacktestReport] = {}
        self._sale_backtests: dict[tuple[int, int, date], dict[SeriesKey, dict]] = {}

    def _series_key(self, pair: tuple[str, str, str]) -> SeriesKey:
        return pair[0], pair[1], pair[2].strip().casefold()

    def _coverage_complete(self, pair: tuple[str, str, str], start: date, end: date) -> bool:
        intervals = self.data.coverage.get(self._series_key(pair), ())
        cursor = start
        for first, last in sorted(intervals):
            if last < cursor:
                continue
            if first > cursor:
                return False
            cursor = max(cursor, last + timedelta(days=1))
            if cursor > end:
                return True
        return cursor > end

    def _available_indices(self, cutoff: date) -> np.ndarray:
        return np.array([
            index for index, pair in enumerate(self._pairs)
            if (self._pair_first_dates.get(self._series_key(pair), date.max) <= cutoff
                or any(first <= cutoff for first, _ in self.data.coverage.get(
                    self._series_key(pair), ())))
        ], dtype=int)

    def available_series(self, cutoff: date) -> frozenset[SeriesKey]:
        """Series observadas o cubiertas al corte que se usa en los pronósticos."""
        return frozenset(self._series_key(self._pairs[index])
                         for index in self._available_indices(cutoff))

    @staticmethod
    def _closed_cutoff(data: SalesData, as_of: date) -> date:
        return min(data.last_date, as_of - timedelta(days=1))

    def _sum_days(self, start: date, days: int) -> np.ndarray | None:
        end = start + timedelta(days=days - 1)
        if start < self.data.first_date or end > self.data.last_date:
            return None
        rows = self._daily.loc[pd.Timestamp(start):pd.Timestamp(end)]
        if len(rows) != days:
            return None
        return rows.sum(axis=0).to_numpy(dtype=float)

    def _recent(self, cutoff: date, days: int) -> np.ndarray | None:
        return self._sum_days(cutoff - timedelta(days=days), days)

    def _sale_forecast(self, target: date, pair_index: int,
                       cutoff: date | None = None) -> float | None:
        """Mediana de los ocho días anteriores con el mismo día de semana."""
        cutoff = cutoff or target - timedelta(days=1)
        weeks_back = max(1, ((target - cutoff).days + 6) // 7)
        sample_dates = [pd.Timestamp(target - timedelta(days=7 * week))
                        for week in range(weeks_back, weeks_back + 8)]
        if sample_dates[-1].date() < self.data.first_date:
            return None
        values = self._daily.reindex(sample_dates).iloc[:, pair_index]
        if values.isna().any():
            return None
        return float(values.median())

    def _sale_audit(self, lead_days: int, weekday: int, cutoff: date) -> dict[SeriesKey, dict]:
        """Audita una fecha comercial con el mismo plazo y un corte histórico cerrado."""
        cache_key = (lead_days, weekday, cutoff)
        if cache_key in self._sale_backtests:
            return self._sale_backtests[cache_key]
        latest = cutoff - timedelta(days=(cutoff.weekday() - weekday) % 7)
        targets = [latest - timedelta(days=7 * offset) for offset in range(15, -1, -1)]
        if targets[0] - timedelta(days=70) < self.data.first_date:
            self._sale_backtests[cache_key] = {}
            return {}
        result = {}
        for index, pair in enumerate(self._pairs):
            key = self._series_key(pair)
            selection_actual, selection_daily, selection_base = [], [], []
            audit_actual, audit_daily, audit_base = [], [], []
            for position, target in enumerate(targets):
                origin = target - timedelta(days=lead_days)
                forecast_cutoff = origin - timedelta(days=1)
                daily = self._sale_forecast(target, index, forecast_cutoff)
                start_day = min(target - timedelta(days=70), origin - timedelta(days=28))
                if (daily is None or not self._coverage_complete(pair, start_day, target)
                        or forecast_cutoff < self.data.first_date):
                    continue
                start = pd.Timestamp(origin - timedelta(days=28))
                end = pd.Timestamp(origin - timedelta(days=1))
                baseline_rows = self._daily.loc[start:end, pair]
                if len(baseline_rows) != 28:
                    continue
                actual = float(self._daily.loc[pd.Timestamp(target), pair])
                values = (selection_actual, selection_daily, selection_base) if position < 8 else (
                    audit_actual, audit_daily, audit_base
                )
                values[0].append(actual)
                values[1].append(daily)
                values[2].append(float(baseline_rows.mean()))
            enough_selection = len(selection_actual) == 8
            enough_audit = len(audit_actual) == 8
            selected = self._score(np.array(selection_actual), np.array(selection_daily)) if enough_selection else {}
            selected_base = self._score(np.array(selection_actual), np.array(selection_base)) if enough_selection else {}
            audited = self._score(np.array(audit_actual), np.array(audit_daily)) if enough_audit else {}
            audited_base = self._score(np.array(audit_actual), np.array(audit_base)) if enough_audit else {}
            eligible = bool(
                enough_selection and enough_audit
                and selected["positive_periods"] >= 3
                and selected["mae"] <= selected_base["mae"]
                and selected["overforecast"] <= selected_base["overforecast"]
                and audited["positive_periods"] >= 3
                and audited["mae"] <= audited_base["mae"]
                and audited["overforecast"] <= audited_base["overforecast"]
            )
            result[key] = {
                "eligible": eligible,
                "selection_windows": len(selection_actual),
                "audit_windows": len(audit_actual),
                "audit_targets": len(audit_actual),
                "audit_coverage": len(audit_actual) / 8,
                "positive_audit_days": audited.get("positive_periods", 0),
                "mae": audited.get("mae"),
                "baseline_mae": audited_base.get("mae"),
                "overforecast": (audited["overforecast"] / len(audit_actual)
                                 if audited and audit_actual else None),
                "baseline_overforecast": (audited_base["overforecast"] / len(audit_actual)
                                          if audited_base and audit_actual else None),
            }
        self._sale_backtests[cache_key] = result
        return result

    def _event_sale_forecast(self, target: date, pair_index: int,
                             event_dates: tuple[date, ...], cutoff: date,
                             lead_days: int) -> tuple[float | None, dict]:
        pair = self._pairs[pair_index]
        observations = []
        for day in sorted(set(event_dates)):
            stamp = pd.Timestamp(day)
            if (self.data.first_date <= day < target and day <= cutoff
                    and stamp in self._daily.index
                    and self._coverage_complete(pair, day, day)):
                value = float(self._daily.loc[stamp].iloc[pair_index])
                observations.append((day, value))
        if len(observations) < 4:
            return None, {"count": len(observations), "validated": False}

        audit_errors, baseline_errors = [], []
        audit_over, baseline_over = [], []
        for index in range(2, len(observations)):
            actual_day, actual = observations[index]
            forecast_cutoff = actual_day - timedelta(days=lead_days + 1)
            known_events = [value for day, value in observations[:index] if day <= forecast_cutoff]
            if len(known_events) < 2 or not self._coverage_complete(
                pair, forecast_cutoff - timedelta(days=28), actual_day
            ):
                continue
            event_prediction = float(np.median(known_events))
            baseline = self._sale_forecast(actual_day, pair_index, forecast_cutoff)
            if baseline is None:
                continue
            audit_errors.append(abs(event_prediction - actual))
            baseline_errors.append(abs(baseline - actual))
            audit_over.append(max(0.0, event_prediction - actual))
            baseline_over.append(max(0.0, baseline - actual))
        validated = bool(
            len(audit_errors) >= 2
            and sum(value > 0 for _, value in observations[-len(audit_errors):]) >= 2
            and float(np.mean(audit_errors)) <= float(np.mean(baseline_errors))
            and float(np.mean(audit_over)) <= float(np.mean(baseline_over))
        )
        return (float(np.median([value for _, value in observations])), {
            "count": len(observations), "validated": validated,
            "audit_windows": len(audit_errors),
            "mae": float(np.mean(audit_errors)) if audit_errors else None,
            "baseline_mae": float(np.mean(baseline_errors)) if baseline_errors else None,
            "overforecast": float(np.mean(audit_over)) if audit_over else None,
            "baseline_overforecast": float(np.mean(baseline_over)) if baseline_over else None,
            "last_date": observations[-1][0], "last_qty": observations[-1][1],
        })

    def for_sale(self, delivery_date: date, source_verified: bool,
                 as_of: date | None = None, event: dict | None = None, *,
                 required_semantics: str = "sale_order",
                 target_kind: str = "commercial_sale_day") -> pd.DataFrame:
        """Pronóstico diario con anticipación equivalente a la recomendación actual."""
        as_of = as_of or date.today()
        if not isinstance(delivery_date, date) or not isinstance(as_of, date):
            raise ValueError("Las fechas deben ser fechas válidas.")
        lead_days = (delivery_date - as_of).days
        if not 0 <= lead_days <= 7:
            raise ValueError("La fecha objetivo debe estar entre hoy y los próximos siete días.")
        cutoff = min(self._closed_cutoff(self.data, as_of), delivery_date - timedelta(days=1))
        fresh = 0 <= (as_of - cutoff).days <= 7
        audit = self._sale_audit(lead_days, delivery_date.weekday(), cutoff)
        rows = []
        for index in self._available_indices(cutoff):
            pair = self._pairs[index]
            client, product, unit = pair
            key = self._series_key(pair)
            values = self._daily.loc[:pd.Timestamp(cutoff), pair]
            sold = values[values > 0]
            reference_date = sold.index[-1].date() if not sold.empty else None
            reference_qty = float(sold.iloc[-1]) if not sold.empty else None
            historical = self._sale_forecast(delivery_date, index, cutoff)
            evidence = audit.get(key, {})
            event_evidence = {}
            if event:
                historical, event_evidence = self._event_sale_forecast(
                    delivery_date, index, tuple(event.get("history_dates", ())), cutoff, lead_days
                )
                if event_evidence.get("last_date") is not None:
                    reference_date = event_evidence["last_date"]
                    reference_qty = event_evidence["last_qty"]
            history_start = min(delivery_date - timedelta(days=70), cutoff - timedelta(days=56))
            complete_history = self._coverage_complete(pair, history_start, cutoff)
            eligible = bool(
                self.data.date_semantics == required_semantics and self.data.provenance in {"real", "simulated"}
                and bool(unit.strip()) and complete_history
                and source_verified and fresh and historical is not None
                and historical > 0 and (
                    event_evidence.get("validated") if event else evidence.get("eligible")
                )
            )
            if self.data.date_semantics != required_semantics:
                reason = ("La fuente contiene fechas de entrega; no se usa para pronosticar la fecha comercial del pedido."
                          if required_semantics == "sale_order" else
                          "El archivo contiene fechas de venta o pedido, no de entrega realizada.")
            elif not unit.strip():
                reason = "La unidad histórica no está confirmada para esta serie."
            elif not complete_history:
                reason = "La cobertura histórica de esta serie está incompleta o sin revisar."
            elif not source_verified:
                reason = "Fuente sin verificación; usa los antecedentes solo como referencia."
            elif not fresh:
                reason = "Las ventas no están actualizadas; el pronóstico comercial queda manual."
            elif event and not event_evidence.get("validated"):
                reason = (f"Oportunidad para revisar: {event['title']} el "
                          f"{delivery_date:%d/%m/%Y}; hay {event_evidence.get('count', 0)} "
                          "fechas equivalentes y falta evidencia cronológica suficiente para proyectar ventas.")
            elif not evidence.get("eligible") and not event:
                reason = "La serie de este cliente y producto no supera la comparación temporal."
            elif historical is None or historical <= 0:
                reason = "No hay ventas suficientes para esta fecha comercial."
            else:
                if event:
                    reason = (f"En {event_evidence['last_date']:%d/%m/%Y} se registró una venta de "
                              f"{event_evidence['last_qty']:g}; mediana de {event_evidence['count']} "
                              f"celebraciones equivalentes, validada en {event_evidence['audit_windows']} "
                              "comparaciones cronológicas contra días equivalentes normales.")
                else:
                    reason = (f"Mediana de 8 días equivalentes; auditoría de {evidence['audit_windows']} "
                              f"semanas: error {evidence['mae']:.3g} frente a {evidence['baseline_mae']:.3g} "
                              "del promedio diario de 28 días.")
            audit_windows = int(event_evidence.get("audit_windows", 0) if event else
                                evidence.get("audit_windows", 0))
            audit_targets = (max(0, int(event_evidence.get("count", 0)) - 2) if event else 8)
            audit_mae = event_evidence.get("mae") if event else evidence.get("mae")
            baseline_mae = event_evidence.get("baseline_mae") if event else evidence.get("baseline_mae")
            audit_overforecast = event_evidence.get("overforecast") if event else evidence.get("overforecast")
            baseline_overforecast = (event_evidence.get("baseline_overforecast") if event
                                     else evidence.get("baseline_overforecast"))
            rows.append({
                "Cliente": client,
                "Producto": product,
                "Unidad": unit,
                "target_date": delivery_date,
                "delivery_date": delivery_date,
                "source_end": cutoff,
                "cutoff_date": cutoff,
                "reference_date": reference_date,
                "reference_qty": reference_qty,
                "forecast": round(historical, 3) if eligible else None,
                "commercial_forecast": round(historical, 3) if eligible and target_kind == "commercial_sale_day" else None,
                "quantity": round(historical, 3) if eligible and target_kind == "delivery_quantity" else None,
                "target_kind": target_kind,
                "evidence_status": "validated" if eligible else (
                    "incompatible_dates" if self.data.date_semantics != required_semantics else
                    "unknown_unit" if not unit.strip() else
                    "unknown_coverage" if not complete_history else
                    "unknown_provenance" if self.data.provenance not in {"real", "simulated"} else
                    "unverified" if not source_verified else "manual"
                ),
                "method": ("Mediana de celebraciones equivalentes" if eligible and event else
                           "Mediana de días equivalentes" if eligible else
                           "Pedido manual" if required_semantics == "sale_order" else "Entrega manual"),
                "evidence": reason,
                "suggestion_event": event["title"] if event else "",
                "eligible_for_auto": eligible,
                "audit_windows": audit_windows,
                "audit_targets": audit_targets,
                "audit_coverage": audit_windows / audit_targets if audit_targets else None,
                "audit_mae": audit_mae,
                "baseline_mae": baseline_mae,
                "audit_overforecast": audit_overforecast,
                "baseline_overforecast": baseline_overforecast,
            })
        return pd.DataFrame(rows)

    def _features(self, cutoff: date, lead_days: int,
                  pair_indices: np.ndarray | None = None) -> np.ndarray | None:
        recent = [self._recent(cutoff, days) for days in (7, 28, 56)]
        if any(values is None for values in recent):
            return None
        indices = (np.arange(len(self._pairs)) if pair_indices is None
                   else pair_indices)
        delivery = cutoff + timedelta(days=lead_days)
        calendar = np.tile(
            [delivery.month, delivery.weekday(), lead_days], (len(indices), 1)
        )
        return np.column_stack((calendar, recent[0][indices], recent[1][indices] / 4,
                                recent[2][indices] / 8)).astype(float)

    def _xgboost(self, cutoff: date, lead_days: int) -> np.ndarray | None:
        """Entrena solo con ventanas objetivo terminadas antes del corte."""
        first = self.data.first_date + timedelta(days=56)
        last = cutoff - timedelta(days=lead_days + 7)
        origins = pd.date_range(first, last, freq="7D")
        if len(origins) < 8:
            return None
        features, targets, training_products = [], [], []
        for origin in origins:
            day = origin.date()
            indices = self._available_indices(day)
            if not len(indices):
                continue
            x = self._features(day, lead_days, indices)
            y = self._sum_days(day + timedelta(days=lead_days), 7)
            if x is not None and y is not None:
                features.append(x)
                targets.append(y[indices])
                training_products.extend(self._products[indices])
        if len(features) < 8:
            return None
        live_indices = self._available_indices(cutoff)
        x_live = self._features(cutoff, lead_days, live_indices)
        if x_live is None:
            return None
        x_train = np.vstack(features)
        y_train = np.concatenate(targets)
        products = np.asarray(training_products, dtype=object)
        # ponytail: una escala por producto impide que el modelo global favorezca
        # solo a los productos grandes; con más años se puede evaluar cada serie.
        scales = {
            product: float(np.median(y_train[(products == product) & (y_train > 0)]))
            if np.any((products == product) & (y_train > 0)) else 1.0
            for product in np.unique(self._products)
        }
        train_scale = np.array([scales[product] for product in products])
        from xgboost import XGBRegressor

        model = XGBRegressor(
            objective="reg:squarederror", tree_method="hist", n_estimators=80,
            max_depth=3, learning_rate=0.05, n_jobs=1, random_state=73,
        )
        model.fit(x_train, y_train / train_scale)
        live_scale = np.array([scales.get(product, 1.0)
                               for product in self._products[live_indices]])
        prediction = np.full(len(self._pairs), np.nan, dtype=float)
        prediction[live_indices] = np.maximum(0, model.predict(x_live) * live_scale)
        return prediction

    def _candidates(self, cutoff: date, lead_days: int, *, model: bool) -> dict[str, np.ndarray]:
        recent = [self._recent(cutoff, days) for days in (7, 28, 56)]
        if any(values is None for values in recent):
            return {}
        result = dict(zip(METHODS[:3], (recent[0], recent[1] / 4, recent[2] / 8)))
        prior_start = (pd.Timestamp(cutoff + timedelta(days=lead_days))
                       - pd.DateOffset(years=1)).date()
        seasonal = self._sum_days(prior_start, 7)
        if seasonal is not None:
            result[METHODS[3]] = seasonal
        if model:
            prediction = self._xgboost(cutoff, lead_days)
            if prediction is not None:
                result["XGBoost"] = prediction
        return result

    @staticmethod
    def _score(actual: np.ndarray, predicted: np.ndarray) -> dict[str, float | int | None]:
        error = predicted - actual
        total = float(actual.sum())
        absolute_error = float(np.abs(error).sum())
        signed_error = float(error.sum())
        return {
            "periods": len(actual),
            "positive_periods": int(np.count_nonzero(actual > 0)),
            "actual_total": total,
            "absolute_error_total": absolute_error,
            "signed_error_total": signed_error,
            "mae": absolute_error / len(actual) if len(actual) else None,
            "wape": absolute_error / total if total > 0 else None,
            "signed_bias": signed_error / total if total > 0 else None,
            "overforecast": float(np.maximum(error, 0).sum()),
            "underforecast": float(np.maximum(-error, 0).sum()),
            "false_positive_periods": int(np.count_nonzero((actual == 0) & (predicted > 0))),
        }

    def backtest(self, lead_days: int, cutoff: date | None = None,
                 target_weekday: int | None = None) -> BacktestReport:
        """Compara series y unidades en 8 ventanas de selección y 8 de auditoría."""
        if not 0 <= lead_days <= 13:
            raise ValueError("El plazo de entrega supera la cobertura evaluable.")
        cutoff = min(cutoff or self.data.last_date, self.data.last_date)
        evaluation_cutoff = cutoff
        current_indices = self._available_indices(evaluation_cutoff)
        current_pairs = [self._pairs[index] for index in current_indices]
        target_weekday = (target_weekday if target_weekday is not None
                          else (cutoff + timedelta(days=1)).weekday())
        cache_key = (lead_days, cutoff, target_weekday)
        if cache_key in self._backtests:
            return self._backtests[cache_key]
        latest = cutoff - timedelta(days=lead_days + 6)
        origin_weekday = (target_weekday - lead_days) % 7
        latest -= timedelta(days=(latest.weekday() - origin_weekday) % 7)
        origins = [latest - timedelta(days=7 * index)
                   for index in range(MIN_BACKTEST_WINDOWS)][::-1]
        earliest = self.data.first_date + timedelta(days=56)
        if not origins or origins[0] < earliest:
            report = BacktestReport(pd.DataFrame(), {}, frozenset(), 0,
                                    len(current_pairs) * SELECTION_WINDOWS, 0)
            self._backtests[cache_key] = report
            return report

        observations = []
        for position, cutoff in enumerate(origins):
            available_at_origin = set(self._available_indices(cutoff))
            actual = self._sum_days(cutoff + timedelta(days=lead_days), 7)
            predictions = self._candidates(cutoff, lead_days, model=True)
            if actual is None or len(predictions) < 3:
                report = BacktestReport(pd.DataFrame(), {}, frozenset(), len(origins),
                                        len(current_pairs) * SELECTION_WINDOWS, 0)
                self._backtests[cache_key] = report
                return report
            for method, values in predictions.items():
                for index, pair in enumerate(self._pairs):
                    if index not in available_at_origin:
                        continue
                    key = self._series_key(pair)
                    target_start = cutoff + timedelta(days=lead_days)
                    if not self._coverage_complete(
                        pair, cutoff - timedelta(days=56), target_start + timedelta(days=6)
                    ):
                        continue
                    observations.append((
                        "selección" if position < SELECTION_WINDOWS else "auditoría",
                        cutoff, *key, method, actual[index], values[index],
                    ))
        frame = pd.DataFrame(
            observations, columns=["partition", "cutoff", "Cliente", "Producto", "Unidad",
                                  "method", "actual", "predicted"]
        )
        score_rows = []
        grouping = ["partition", "Cliente", "Producto", "Unidad", "method"]
        for (partition, client, product, unit, method), group in frame.groupby(
            grouping, sort=True
        ):
            # Un candidato se evalúa solo si cubre todas las mismas ventanas.
            if group["cutoff"].nunique() != SELECTION_WINDOWS:
                continue
            score_rows.append({"partition": partition, "Cliente": client,
                               "Producto": product, "Unidad": unit,
                               "method": method, **self._score(
                                   group["actual"].to_numpy(dtype=float),
                                   group["predicted"].to_numpy(dtype=float))})
        scores = pd.DataFrame(score_rows, columns=[
            "partition", "Cliente", "Producto", "Unidad", "method", "periods",
            "positive_periods", "actual_total", "absolute_error_total", "signed_error_total",
            "mae", "wape", "signed_bias", "overforecast", "underforecast",
            "false_positive_periods",
        ])
        winners: dict[str, str] = {}
        series_winners: dict[SeriesKey, str] = {}
        eligible: set[str] = set()
        eligible_series: set[SeriesKey] = set()
        series = sorted({self._series_key(pair) for pair in current_pairs})
        for key in series:
            client, product, unit = key
            series_scores = scores[
                (scores["Cliente"] == client) & (scores["Producto"] == product)
                & (scores["Unidad"] == unit)
            ] if not scores.empty else scores
            selection = series_scores[series_scores["partition"] == "selección"]
            audit = series_scores[series_scores["partition"] == "auditoría"]
            base_selection = selection[selection["method"] == METHODS[1]]
            base_audit = audit[audit["method"] == METHODS[1]]
            if base_selection.empty or base_audit.empty:
                continue
            # El menor exceso solo compite entre métodos que no empeoran el
            # error absoluto del promedio de 28 días. La auditoría queda aparte.
            base_mae = float(base_selection.iloc[0]["mae"])
            comparable = selection[selection["mae"] <= base_mae + 1e-9].sort_values(
                ["overforecast", "mae", "method"]
            )
            if comparable.empty:
                continue
            method = str(comparable.iloc[0]["method"])
            winner_audit = audit[audit["method"] == method]
            if winner_audit.empty:
                continue
            series_winners[key] = method
            measured = winner_audit.iloc[0]
            baseline = base_audit.iloc[0]
            if (measured["positive_periods"] >= MIN_POSITIVE_AUDIT_PERIODS
                    and measured["wape"] is not None
                    and not pd.isna(measured["wape"])
                    and measured["wape"] < 1
                    and measured["mae"] <= baseline["mae"] + 1e-9
                    and measured["overforecast"] <= baseline["overforecast"] + 1e-9):
                eligible_series.add(key)
                eligible.add(product)
            product_methods = {value for (owner, name, _), value in series_winners.items()
                               if name == product and owner == client}
            if len(product_methods) == 1:
                winners[product] = next(iter(product_methods))
        audit_windows = max(0, len(origins) - SELECTION_WINDOWS)
        audit_targets = len(current_pairs) * audit_windows
        eligible_audit_targets = len(eligible_series) * audit_windows
        report = BacktestReport(
            scores, winners, frozenset(eligible), len(origins),
            audit_targets, eligible_audit_targets, series_winners, frozenset(eligible_series),
        )
        self._backtests[cache_key] = report
        return report

    def _weekly_forecast(self, target_date: date, source_verified: bool, as_of: date,
                         required_semantics: str, target_kind: str) -> pd.DataFrame:
        cutoff = self._closed_cutoff(self.data, as_of)
        origin = cutoff + timedelta(days=1)
        lead = (target_date - origin).days
        if not 0 <= lead <= 13:
            lead = -1
        recent = {days: self._recent(origin, days) for days in (7, 28, 56)}
        fresh = 0 <= (as_of - cutoff).days <= 7
        report = None
        candidates: dict[str, np.ndarray] = {}
        if (self.data.date_semantics == required_semantics
                and self.data.provenance in {"real", "simulated"}
                and source_verified and fresh and 0 <= lead <= 13
                and recent[56] is not None):
            report = self.backtest(lead, cutoff, target_date.weekday())
            if report.eligible_series:
                candidates = self._candidates(origin, lead,
                                              model="XGBoost" in report.series_winners.values())
        rows = []
        for index in self._available_indices(cutoff):
            pair = self._pairs[index]
            client, product, unit = pair
            key = self._series_key(pair)
            method = report.series_winners.get(key) if report else None
            history_start = origin - timedelta(days=56)
            target_end = target_date + timedelta(days=6)
            complete_coverage = self._coverage_complete(pair, history_start, cutoff)
            qualified = bool(
                self.data.date_semantics == required_semantics
                and self.data.provenance in {"real", "simulated"}
                and unit.strip() and complete_coverage
                and source_verified and fresh and report
                and key in report.eligible_series and method in candidates
                and recent[56] is not None and recent[56][index] > 0
            )
            audit_score = baseline_score = None
            if report is not None and not report.scores.empty:
                score_rows = report.scores[
                    (report.scores["partition"] == "auditoría")
                    & (report.scores["Cliente"] == client)
                    & (report.scores["Producto"] == product)
                    & (report.scores["Unidad"] == unit)
                ]
                if method:
                    chosen = score_rows[score_rows["method"] == method]
                    audit_score = chosen.iloc[0] if not chosen.empty else None
                baseline = score_rows[score_rows["method"] == METHODS[1]]
                baseline_score = baseline.iloc[0] if not baseline.empty else None
            audit_windows = int(audit_score["periods"]) if audit_score is not None else 0
            forecast = round(float(candidates[method][index]), 3) if qualified else None
            if self.data.date_semantics != required_semantics:
                evidence, status = (
                    "La fecha del archivo es de venta o pedido; no representa una entrega física.",
                    "incompatible_dates",
                )
            elif not unit.strip():
                evidence, status = "La unidad de esta serie no está confirmada.", "unknown_unit"
            elif not complete_coverage:
                evidence, status = "La cobertura de esta serie no es completa para el período evaluado.", "unknown_coverage"
            elif self.data.provenance not in {"real", "simulated"}:
                evidence, status = "Falta confirmar si la serie es real o simulada.", "unknown_provenance"
            elif not source_verified:
                evidence, status = "La fuente requiere verificación administrativa.", "unverified"
            elif not fresh:
                evidence, status = "El último corte cerrado está desactualizado.", "stale"
            elif not qualified:
                evidence, status = "La serie no supera la evaluación cronológica independiente.", "manual"
            else:
                evidence = (
                    f"Corte {cutoff:%d/%m/%Y}; {method.lower()} evaluado con 8 ventanas de selección "
                    "y 8 de auditoría contra el promedio de 28 días."
                )
                status = "validated"
            values = self._daily.loc[:pd.Timestamp(cutoff), pair]
            sold = values[values > 0]
            reference_date = sold.index[-1].date() if not sold.empty else None
            reference_qty = float(sold.iloc[-1]) if not sold.empty else None
            rows.append({
                "Cliente": client, "Producto": product, "Unidad": unit,
                "target_date": target_date, "horizon_end": target_end,
                "cutoff_date": cutoff, "source_end": cutoff,
                "reference_date": reference_date, "reference_qty": reference_qty,
                "forecast": forecast,
                "quantity": forecast if target_kind == "delivery_quantity" else None,
                "commercial_forecast": forecast if target_kind == "commercial_week" else None,
                "method": method if qualified else "Revisión manual",
                "target_kind": target_kind, "evidence_status": status,
                "evidence": evidence, "explanation": evidence,
                "eligible_for_auto": qualified,
                "audit_windows": audit_windows,
                "audit_targets": 8,
                "audit_coverage": audit_windows / 8,
                "audit_mae": float(audit_score["mae"]) if audit_score is not None else None,
                "baseline_mae": float(baseline_score["mae"]) if baseline_score is not None else None,
                "audit_overforecast": float(audit_score["overforecast"]) if audit_score is not None else None,
                "baseline_overforecast": float(baseline_score["overforecast"]) if baseline_score is not None else None,
            })
        return pd.DataFrame(rows)

    def for_commercial_week(self, start_date: date, source_verified: bool,
                            as_of: date | None = None) -> pd.DataFrame:
        """Previsión semanal de ventas; no se interpreta como demanda física de entrega."""
        as_of = as_of or date.today()
        if not isinstance(start_date, date) or not isinstance(as_of, date):
            raise ValueError("Las fechas deben ser fechas válidas.")
        if not 0 <= (start_date - as_of).days <= 7:
            raise ValueError("La semana comercial debe iniciar entre hoy y los próximos siete días.")
        return self._weekly_forecast(start_date, source_verified, as_of,
                                     "sale_order", "commercial_week")

    def for_delivery(self, delivery_date: date, source_verified: bool,
                     as_of: date | None = None) -> pd.DataFrame:
        """Cantidad para una entrega específica, evaluada con el mismo día y anticipación."""
        as_of = as_of or date.today()
        if not isinstance(delivery_date, date) or not isinstance(as_of, date):
            raise ValueError("Las fechas deben ser fechas válidas.")
        if not 0 <= (delivery_date - as_of).days <= 7:
            raise ValueError("La entrega debe estar entre hoy y los próximos siete días.")
        return self.for_sale(
            delivery_date, source_verified, as_of, required_semantics="delivery",
            target_kind="delivery_quantity",
        )

    def for_delivery_week(self, start_date: date, source_verified: bool,
                          as_of: date | None = None) -> pd.DataFrame:
        """Total de entregas de un período compatible de siete días para reposición física."""
        as_of = as_of or date.today()
        if not isinstance(start_date, date) or not isinstance(as_of, date):
            raise ValueError("Las fechas deben ser fechas válidas.")
        if not 0 <= (start_date - as_of).days <= 7:
            raise ValueError("El período debe iniciar entre hoy y los próximos siete días.")
        return self._weekly_forecast(start_date, source_verified, as_of,
                                     "delivery", "delivery_period_total")
