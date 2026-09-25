"""Pronóstico de demanda a siete días y evidencia para cada recomendación."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

import numpy as np
import pandas as pd
from xgboost import XGBRegressor

from .data import SalesData


@dataclass(frozen=True)
class ForecastReport:
    rows: pd.DataFrame
    as_of: date
    source_end: date
    metrics_by_product: pd.DataFrame
    feature_importance: pd.DataFrame
    validation_windows: int
    median_wape: float | None
    model_status: str


def _feature_frame(frame: pd.DataFrame, columns: list[str] | None = None) -> pd.DataFrame:
    dates = pd.to_datetime(frame["Fecha"])
    features = pd.DataFrame({
        "mes": dates.dt.month.astype(float),
        "dia_mes": dates.dt.day.astype(float),
        "dia_semana": dates.dt.dayofweek.astype(float),
        "semana_ano": dates.dt.isocalendar().week.astype(float),
        "ventas_7_dias_previos": frame["prior_7"].astype(float),
        "ventas_28_dias_previos": frame["prior_28"].astype(float),
        "mismo_periodo_ano_anterior": frame["previous_year_7"].astype(float),
    }, index=frame.index)
    categorical = pd.get_dummies(frame[["Cliente", "Producto"]], dtype=float)
    features = pd.concat([features, categorical], axis=1)
    return features if columns is None else features.reindex(columns=columns, fill_value=0)


def _training_frame(data: SalesData) -> pd.DataFrame:
    daily = data.daily()
    groups = daily.groupby(["Cliente", "Producto"], sort=False)["Cantidad_kg_unid"]
    daily["prior_7"] = groups.transform(lambda series: series.shift(1).rolling(7, min_periods=7).sum())
    daily["prior_28"] = groups.transform(lambda series: series.shift(1).rolling(28, min_periods=28).sum())
    daily["target_7"] = groups.transform(
        lambda series: series.iloc[::-1].rolling(7, min_periods=7).sum().iloc[::-1]
    )
    older = daily[["Cliente", "Producto", "Fecha", "target_7"]].rename(
        columns={"Fecha": "previous_date", "target_7": "previous_year_7"}
    )
    daily["previous_date"] = daily["Fecha"] - pd.DateOffset(years=1)
    return daily.merge(older, on=["Cliente", "Producto", "previous_date"], how="left")


def _metrics(actual: np.ndarray, predicted: np.ndarray) -> tuple[float | None, float]:
    error = np.abs(actual - predicted)
    total = float(np.abs(actual).sum())
    return (float(error.sum() / total * 100) if total else None, float(error.mean()))


class DemandForecaster:
    """Un modelo global; conserva los productos como categorías separadas."""

    def __init__(self) -> None:
        self.model: XGBRegressor | None = None
        self.feature_columns: list[str] = []

    @staticmethod
    def _new_model() -> XGBRegressor:
        # ponytail: un solo modelo global evita 80 modelos con apenas un año;
        # comparar modelos por serie cuando existan varios años de ventas.
        return XGBRegressor(
            objective="reg:squarederror", tree_method="hist", n_estimators=160,
            max_depth=4, learning_rate=0.05, subsample=0.85,
            colsample_bytree=0.85, n_jobs=2, random_state=73,
        )

    def run(self, data: SalesData, as_of: date) -> ForecastReport:
        frame = _training_frame(data)
        usable = frame.dropna(subset=["target_7"])
        if usable["Fecha"].nunique() < 90:
            raise ValueError("Se requieren al menos 96 días de cobertura para evaluar el modelo.")
        last_target_start = pd.Timestamp(data.last_date - timedelta(days=6))
        first_validation = last_target_start - timedelta(days=55)
        # La ventana objetivo de entrenamiento debe terminar antes de validar.
        train = usable[usable["Fecha"] + timedelta(days=6) < first_validation]
        validation = usable[
            (usable["Fecha"] >= first_validation)
            & (usable["Fecha"] <= last_target_start)
            & ((usable["Fecha"] - first_validation).dt.days % 7 == 0)
        ]
        if train.empty or validation.empty:
            raise ValueError("El histórico no alcanza para separar entrenamiento y validación.")
        self.feature_columns = _feature_frame(train).columns.tolist()
        self.model = self._new_model()
        self.model.fit(_feature_frame(train, self.feature_columns), train["target_7"])
        actual = validation["target_7"].to_numpy(dtype=float)
        model_pred = np.maximum(0, self.model.predict(_feature_frame(validation, self.feature_columns)))
        baseline_pred = validation["prior_28"].fillna(0).to_numpy(dtype=float) / 4
        measured = validation[["Producto", "target_7"]].copy()
        measured["estimacion_xgboost"] = model_pred
        measured["estimacion_promedio"] = baseline_pred
        metrics = []
        uncertainty_by_product: dict[str, float] = {}
        for product, group in measured.groupby("Producto", sort=True):
            observed = group["target_7"].to_numpy(dtype=float)
            model_wape, model_mae = _metrics(
                observed, group["estimacion_xgboost"].to_numpy(dtype=float)
            )
            baseline_wape, baseline_mae = _metrics(
                observed, group["estimacion_promedio"].to_numpy(dtype=float)
            )
            use_xgboost = (
                model_wape is not None and baseline_wape is not None
                and model_wape < baseline_wape
            )
            selected_prediction = (
                group["estimacion_xgboost"].to_numpy(dtype=float)
                if use_xgboost else group["estimacion_promedio"].to_numpy(dtype=float)
            )
            uncertainty_by_product[product] = float(
                np.quantile(np.abs(observed - selected_prediction), 0.80)
            )
            metrics.append({
                "Producto": product, "WAPE_XGBoost": model_wape,
                "WAPE_promedio": baseline_wape, "MAE_XGBoost": model_mae,
                "MAE_promedio": baseline_mae,
                "Metodo_ganador": "XGBoost" if use_xgboost else "Promedio 28 días",
                "Mejora_WAPE_pp": (
                    baseline_wape - model_wape
                    if model_wape is not None and baseline_wape is not None else np.nan
                ),
                "Error_P80_metodo": uncertainty_by_product[product],
            })
        metrics_by_product = pd.DataFrame(metrics)
        winner_by_product = {
            row["Producto"]: row["WAPE_XGBoost"] < row["WAPE_promedio"]
            for row in metrics if row["WAPE_XGBoost"] is not None
            and row["WAPE_promedio"] is not None
        }

        # Reentrenar con todo lo conocido después de medir el error fuera de muestra.
        self.model = self._new_model()
        self.model.fit(_feature_frame(usable, self.feature_columns), usable["target_7"])
        importance = pd.DataFrame({
            "Variable": self.feature_columns,
            "Importancia": self.model.feature_importances_.astype(float),
        })
        importance["Variable"] = importance["Variable"].map(self._feature_label)
        feature_importance = (
            importance.groupby("Variable", as_index=False)["Importancia"].sum()
            .sort_values("Importancia", ascending=False)
            .reset_index(drop=True)
        )
        importance_total = feature_importance["Importancia"].sum()
        if importance_total > 0:
            feature_importance["Importancia"] /= importance_total

        pairs = data.rows[["Cliente", "Producto"]].drop_duplicates().reset_index(drop=True)
        future = pairs.copy()
        future["Fecha"] = pd.Timestamp(as_of)
        daily = data.daily()
        previous_start = pd.Timestamp(as_of) - pd.DateOffset(years=1)
        previous_end = previous_start + timedelta(days=6)
        has_previous = (
            pd.Timestamp(data.first_date) <= previous_start
            and pd.Timestamp(data.last_date) >= previous_end
        )
        for span, name in ((7, "prior_7"), (28, "prior_28")):
            start = pd.Timestamp(as_of - timedelta(days=span))
            end = pd.Timestamp(as_of - timedelta(days=1))
            if pd.Timestamp(data.first_date) <= start and pd.Timestamp(data.last_date) >= end:
                values = daily[(daily["Fecha"] >= start) & (daily["Fecha"] <= end)]
                totals = values.groupby(["Cliente", "Producto"])["Cantidad_kg_unid"].sum()
                future[name] = pd.MultiIndex.from_frame(pairs).map(totals)
            else:
                future[name] = np.nan
        if has_previous:
            values = daily[(daily["Fecha"] >= previous_start) & (daily["Fecha"] <= previous_end)]
            totals = values.groupby(["Cliente", "Producto"])["Cantidad_kg_unid"].sum()
            future["previous_year_7"] = pd.MultiIndex.from_frame(pairs).map(totals)
        else:
            future["previous_year_7"] = np.nan

        month_start = previous_start.replace(day=1)
        month_end = month_start + pd.offsets.MonthEnd(1)
        if pd.Timestamp(data.first_date) <= month_start and pd.Timestamp(data.last_date) >= month_end:
            month_rows = daily[(daily["Fecha"] >= month_start) & (daily["Fecha"] <= month_end)]
            month_totals = month_rows.groupby(["Cliente", "Producto"])["Cantidad_kg_unid"].sum()
            future["previous_year_month_week"] = (
                pd.MultiIndex.from_frame(pairs).map(month_totals) * 7 / month_end.day
            )
        else:
            future["previous_year_month_week"] = np.nan

        future["XGBoost_7d"] = np.maximum(
            0, self.model.predict(_feature_frame(future, self.feature_columns))
        ).round(2)
        def choose(row: pd.Series) -> pd.Series:
            if pd.notna(row["prior_28"]):
                if winner_by_product.get(row["Producto"], False):
                    return pd.Series((row["XGBoost_7d"], "XGBoost: menor error del producto"))
                return pd.Series((row["prior_28"] / 4, "Promedio de 28 días"))
            if pd.notna(row["previous_year_month_week"]):
                # ponytail: con un solo año, usar el promedio semanal observado
                # del mismo mes; validar estacionalidad interanual al recibir más años.
                return pd.Series((row["previous_year_month_week"], "Mismo mes del año anterior"))
            return pd.Series((row["XGBoost_7d"], "XGBoost sin comparación reciente"))

        future[["Pronostico_7d", "Metodo"]] = future.apply(choose, axis=1)
        future["Pronostico_7d"] = future["Pronostico_7d"].astype(float).round(2)
        future["Margen_orientativo"] = future["Producto"].map(uncertainty_by_product).fillna(0)
        future["Rango_bajo_7d"] = np.maximum(
            0, future["Pronostico_7d"] - future["Margen_orientativo"]
        ).round(2)
        future["Rango_alto_7d"] = (
            future["Pronostico_7d"] + future["Margen_orientativo"]
        ).round(2)
        future["Mismo_periodo_ano_anterior"] = future["previous_year_7"]
        future["Promedio_semanal_mismo_mes"] = future["previous_year_month_week"].round(2)
        future["Ultimos_28_dias"] = future["prior_28"]
        future["Nivel_atencion"] = future.apply(self._attention_level, axis=1)
        future["Situacion"] = future.apply(self._explanation, axis=1)
        output = future[["Cliente", "Producto", "Nivel_atencion", "Pronostico_7d",
                         "Rango_bajo_7d", "Rango_alto_7d", "Metodo", "XGBoost_7d",
                         "Mismo_periodo_ano_anterior", "Promedio_semanal_mismo_mes",
                         "Ultimos_28_dias", "Situacion"]]
        status = (
            "El histórico termina antes de la fecha de análisis; el pronóstico requiere "
            "revisión comercial y datos operativos actuales."
            if data.last_date < as_of - timedelta(days=28)
            else "Histórico reciente disponible para apoyar la recomendación."
        )
        available_wape = metrics_by_product[["WAPE_XGBoost", "WAPE_promedio"]].min(axis=1)
        median_wape = float(available_wape.median()) if available_wape.notna().any() else None
        return ForecastReport(
            rows=output.sort_values(
                ["Cliente", "Pronostico_7d"], ascending=[True, False]
            ).reset_index(drop=True),
            as_of=as_of,
            source_end=data.last_date,
            metrics_by_product=metrics_by_product,
            feature_importance=feature_importance,
            validation_windows=validation["Fecha"].nunique(),
            median_wape=median_wape,
            model_status=status,
        )

    @staticmethod
    def _feature_label(value: str) -> str:
        labels = {
            "mes": "Mes",
            "dia_mes": "Día del mes",
            "dia_semana": "Día de la semana",
            "semana_ano": "Semana del año",
            "ventas_7_dias_previos": "Ventas de los 7 días previos",
            "ventas_28_dias_previos": "Ventas de los 28 días previos",
            "mismo_periodo_ano_anterior": "Mismo período del año anterior",
        }
        if value.startswith("Cliente_"):
            return "Identidad del cliente"
        if value.startswith("Producto_"):
            return "Identidad del producto"
        return labels.get(value, value.replace("_", " ").capitalize())

    @staticmethod
    def _attention_level(row: pd.Series) -> str:
        recent = row["prior_28"]
        if pd.isna(recent):
            return "Revisión necesaria"
        weekly_average = float(recent) / 4
        if weekly_average <= 0:
            return "Alta" if row["Pronostico_7d"] > 0 else "Baja"
        ratio = float(row["Pronostico_7d"]) / weekly_average
        if ratio >= 1.20:
            return "Alta"
        if ratio >= 0.80:
            return "Media"
        return "Baja"

    @staticmethod
    def _explanation(row: pd.Series) -> str:
        predicted = row["Pronostico_7d"]
        previous = row["previous_year_7"]
        recent = row["prior_28"]
        method = row["Metodo"]
        if pd.notna(previous):
            direction = "por encima" if predicted > previous else "por debajo" if predicted < previous else "igual"
            action = (
                "Revise disponibilidad para cubrir la demanda."
                if predicted > previous else
                "Evite aumentar el pedido sin verificar rotación e inventario."
            )
            return (
                f"Estimación por {method.lower()}, {direction} de los {previous:.1f} "
                "registrados en los mismos siete días del año anterior. "
                + action
            )
        if pd.notna(recent):
            return (
                f"Estimación por {method.lower()}; en los 28 días previos se "
                f"vendieron {recent:.1f} unidades de la "
                "medida original. Revise inventario antes de confirmar."
            )
        return f"Estimación por {method.lower()}; no hay comparación completa. Revise antes de pedir."
