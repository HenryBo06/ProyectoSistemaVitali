"""Recomendaciones de ventas para los siete días posteriores a una entrega.

Las cifras son ventas observadas, no demanda perdida por falta de existencias.
Una referencia histórica sin validación nunca se convierte en pedido automático.
"""

from __future__ import annotations

from dataclasses import dataclass
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


@dataclass(frozen=True)
class BacktestReport:
    """Comparación por producto, con selección y auditoría en semanas distintas."""

    scores: pd.DataFrame
    winners: dict[str, str]
    eligible_products: frozenset[str]
    windows: int


class RecommendationEngine:
    """Conserva las cantidades separadas por producto y cliente."""

    def __init__(self, data: SalesData) -> None:
        self.data = data
        self._daily = data.daily().pivot(
            index="Fecha", columns=["Cliente", "Producto"], values="Cantidad_kg_unid"
        ).sort_index(axis=1)
        self._pairs = list(self._daily.columns)
        self._products = np.array([product for _, product in self._pairs], dtype=object)
        pair_frame = pd.DataFrame(self._pairs, columns=["Cliente", "Producto"])
        self._identities = pd.get_dummies(pair_frame, dtype=float).to_numpy(dtype=float)
        self._backtests: dict[int, BacktestReport] = {}

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

    def _features(self, cutoff: date, lead_days: int) -> np.ndarray | None:
        recent = [self._recent(cutoff, days) for days in (7, 28, 56)]
        if any(values is None for values in recent):
            return None
        delivery = cutoff + timedelta(days=lead_days)
        count = len(self._pairs)
        calendar = np.tile(
            [delivery.month, delivery.weekday(), lead_days], (count, 1)
        )
        return np.column_stack((calendar, recent[0], recent[1] / 4,
                                recent[2] / 8, self._identities)).astype(float)

    def _xgboost(self, cutoff: date, lead_days: int) -> np.ndarray | None:
        """Entrena solo con ventanas objetivo terminadas antes del corte."""
        first = self.data.first_date + timedelta(days=56)
        last = cutoff - timedelta(days=lead_days + 7)
        origins = pd.date_range(first, last, freq="7D")
        if len(origins) < 8:
            return None
        features, targets = [], []
        for origin in origins:
            day = origin.date()
            x = self._features(day, lead_days)
            y = self._sum_days(day + timedelta(days=lead_days), 7)
            if x is not None and y is not None:
                features.append(x)
                targets.append(y)
        if len(features) < 8:
            return None
        x_live = self._features(cutoff, lead_days)
        if x_live is None:
            return None
        x_train = np.vstack(features)
        y_train = np.concatenate(targets)
        products = np.tile(self._products, len(targets))
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
        live_scale = np.array([scales[product] for product in self._products])
        return np.maximum(0, model.predict(x_live) * live_scale)

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
        return {
            "periods": len(actual),
            "positive_periods": int(np.count_nonzero(actual > 0)),
            "mae": float(np.abs(error).mean()),
            "wape": float(np.abs(error).sum() / total) if total > 0 else None,
            "overforecast": float(np.maximum(error, 0).sum()),
            "underforecast": float(np.maximum(-error, 0).sum()),
            "false_positive_periods": int(np.count_nonzero((actual == 0) & (predicted > 0))),
        }

    def backtest(self, lead_days: int) -> BacktestReport:
        """Compara métodos en las mismas 16 semanas sin anticipar ventas futuras."""
        if not 0 <= lead_days <= 13:
            raise ValueError("El plazo de entrega supera la cobertura evaluable.")
        if lead_days in self._backtests:
            return self._backtests[lead_days]
        earliest = self.data.first_date + timedelta(days=140)
        latest = self.data.last_date - timedelta(days=lead_days + 6)
        weekday = (self.data.last_date + timedelta(days=1)).weekday()
        latest -= timedelta(days=(latest.weekday() - weekday) % 7)
        origins = [latest - timedelta(days=7 * index)
                   for index in range(MIN_BACKTEST_WINDOWS)][::-1]
        if not origins or origins[0] < earliest:
            report = BacktestReport(pd.DataFrame(), {}, frozenset(), 0)
            self._backtests[lead_days] = report
            return report

        observations = []
        for position, cutoff in enumerate(origins):
            actual = self._sum_days(cutoff + timedelta(days=lead_days), 7)
            predictions = self._candidates(cutoff, lead_days, model=True)
            if actual is None or len(predictions) < 3:
                report = BacktestReport(pd.DataFrame(), {}, frozenset(), 0)
                self._backtests[lead_days] = report
                return report
            for method, values in predictions.items():
                for index, (_, product) in enumerate(self._pairs):
                    observations.append(("selección" if position < 8 else "auditoría",
                                         cutoff, product, method, actual[index], values[index]))
        frame = pd.DataFrame(
            observations, columns=["partition", "cutoff", "Producto", "method", "actual", "predicted"]
        )
        score_rows = []
        for (partition, product, method), group in frame.groupby(
            ["partition", "Producto", "method"], sort=True
        ):
            # Un candidato se evalúa solo si cubre todas las mismas ventanas.
            if group["cutoff"].nunique() != 8:
                continue
            score_rows.append({"partition": partition, "Producto": product,
                               "method": method, **self._score(
                                   group["actual"].to_numpy(dtype=float),
                                   group["predicted"].to_numpy(dtype=float))})
        scores = pd.DataFrame(score_rows)
        winners: dict[str, str] = {}
        eligible: set[str] = set()
        for product in np.unique(self._products):
            product_scores = scores[scores["Producto"] == product]
            selection = product_scores[product_scores["partition"] == "selección"]
            audit = product_scores[product_scores["partition"] == "auditoría"]
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
            winners[product] = method
            measured = winner_audit.iloc[0]
            baseline = base_audit.iloc[0]
            if (measured["positive_periods"] >= MIN_POSITIVE_AUDIT_PERIODS
                    and measured["wape"] is not None
                    and not pd.isna(measured["wape"])
                    and measured["wape"] < 1
                    and measured["mae"] <= baseline["mae"] + 1e-9
                    and measured["overforecast"] <= baseline["overforecast"] + 1e-9):
                eligible.add(product)
        report = BacktestReport(scores, winners, frozenset(eligible), len(origins))
        self._backtests[lead_days] = report
        return report

    def for_delivery(
        self, delivery_date: date, source_verified: bool, as_of: date | None = None
    ) -> pd.DataFrame:
        """Devuelve una fila por cliente/producto para entrega en los próximos 7 días."""
        as_of = as_of or date.today()
        if not isinstance(delivery_date, date) or not isinstance(as_of, date):
            raise ValueError("Las fechas deben ser fechas válidas.")
        if not 0 <= (delivery_date - as_of).days <= 7:
            raise ValueError("La entrega debe estar entre hoy y los próximos siete días.")
        prior_start = (pd.Timestamp(delivery_date) - pd.DateOffset(years=1)).date()
        reference = self._sum_days(prior_start, 7)
        cutoff = self.data.last_date + timedelta(days=1)
        recent = {days: self._recent(cutoff, days) for days in (7, 28, 56)}
        fresh = 0 <= (as_of - self.data.last_date).days <= 7
        lead = (delivery_date - cutoff).days
        candidates: dict[str, np.ndarray] = {}
        report: BacktestReport | None = None
        if source_verified and fresh and 0 <= lead <= 13 and recent[56] is not None:
            report = self.backtest(lead)
            if report.eligible_products:
                candidates = self._candidates(cutoff, lead, model="XGBoost" in report.winners.values())
        rows = []
        for index, (client, product) in enumerate(self._pairs):
            method = report.winners.get(product) if report else None
            qualified = bool(
                report and product in report.eligible_products
                and method in candidates and recent[56] is not None
                and recent[56][index] > 0
            )
            forecast = round(float(candidates[method][index]), 2) if qualified else None
            if not source_verified:
                reason = "Fuente sin verificación; use el histórico solo como referencia."
            elif not fresh:
                reason = "Ventas no actualizadas para la fecha de hoy; indique un pedido manual."
            elif not 0 <= lead <= 13:
                reason = "La entrega queda fuera del horizonte respaldado por los datos."
            elif not qualified:
                reason = "La evidencia de esta serie no permite sugerir una cantidad automática."
            else:
                reason = (f"Ventas conocidas hasta {self.data.last_date:%d/%m/%Y}; "
                          f"{method.lower()} evaluado en {report.windows} semanas comparables.")
            previous = round(float(reference[index]), 2) if reference is not None else None
            explanation = (
                f"En los mismos siete días del año anterior se registraron {previous:g} "
                "en la medida original del Excel. Confirme unidad y existencias."
                if previous is not None else
                "No existe un periodo completo del año anterior para comparar."
            )
            rows.append({
                "Cliente": client, "Producto": product,
                "delivery_date": delivery_date, "horizon_end": delivery_date + timedelta(days=6),
                "source_end": self.data.last_date,
                "reference_previous_year_7d": previous,
                "recent_7": round(float(recent[7][index]), 2) if recent[7] is not None else None,
                "recent_28": round(float(recent[28][index]), 2) if recent[28] is not None else None,
                "recent_56": round(float(recent[56][index]), 2) if recent[56] is not None else None,
                "forecast": forecast, "method": method if qualified else "Pedido manual",
                "evidence": reason, "explanation": explanation,
                "eligible_for_auto": qualified,
            })
        return pd.DataFrame(rows)
