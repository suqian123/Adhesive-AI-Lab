"""Closed-loop candidate regression, classification, and experiment updates."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Sequence

import numpy as np
import pandas as pd


FUNCTIONAL_GROUP_FEATURES = {
    "functional_group_cyanate_phenolic": "cyanate/phenolic",
    "functional_group_phthalonitrile": "phthalonitrile/nitrile-triazine",
    "functional_group_imide_amine": "imide/amine",
    "functional_group_siloxane": "siloxane",
    "functional_group_urethane": "urethane",
}
FEATURE_COLUMNS = (
    *FUNCTIONAL_GROUP_FEATURES,
    "crosslink_density", "glass_transition_c", "free_volume_fraction", "chain_mobility",
    "cohesive_energy_density_mj_m3", "elastic_modulus_gpa", "cte_ppm_k",
    "filler_oxygen_adsorption_ev", "filler_radical_capture_index",
    "interface_binding_energy_mj_m2", "interface_covalent_bond_count", "filler_pct",
)
OUTPUT_COLUMNS = (
    "wide_temp_adhesion_mpa", "healing_efficiency_pct", "atomic_oxygen_retention_pct",
    "uv_retention_pct", "am_feasibility",
)
EXPERIMENT_FEATURE_ALIASES = {
    "measured_tg_c": "glass_transition_c", "tg_c": "glass_transition_c", "glass_transition_temperature_c": "glass_transition_c",
    "measured_free_volume": "free_volume_fraction", "free_volume": "free_volume_fraction",
    "measured_chain_mobility": "chain_mobility", "measured_cohesive_energy_density": "cohesive_energy_density_mj_m3",
    "measured_modulus_gpa": "elastic_modulus_gpa", "elastic_modulus": "elastic_modulus_gpa",
    "measured_cte_ppm_k": "cte_ppm_k", "thermal_expansion_coefficient_ppm_k": "cte_ppm_k",
    "adhesion_strength_mpa": "wide_temp_adhesion_mpa", "self_healing_efficiency_pct": "healing_efficiency_pct",
}
CLASS_ORDER = ("D", "C", "B", "A")


@dataclass(frozen=True)
class ScreeningModel:
    feature_names: tuple[str, ...]
    target_names: tuple[str, ...]
    x_means: np.ndarray
    x_scales: np.ndarray
    y_means: np.ndarray
    y_scales: np.ndarray
    regression_weights: np.ndarray
    classifier_weights: np.ndarray
    classes: tuple[str, ...]
    training_rows: int
    experimental_rows: int
    version: str
    correction_bias: np.ndarray | None = None
    validation_metrics: dict[str, float] = field(default_factory=dict)
    created_at: str = ""
    data_provenance: dict[str, int] = field(default_factory=dict)


def save_model(model: ScreeningModel, path: str | Path) -> Path:
    """Persist a trained model, validation metrics, and calibration bias to NPZ."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(
        target, feature_names=np.asarray(model.feature_names), target_names=np.asarray(model.target_names),
        x_means=model.x_means, x_scales=model.x_scales, y_means=model.y_means, y_scales=model.y_scales,
        regression_weights=model.regression_weights, classifier_weights=model.classifier_weights,
        classes=np.asarray(model.classes), training_rows=model.training_rows, experimental_rows=model.experimental_rows,
        version=model.version, correction_bias=np.asarray(model.correction_bias if model.correction_bias is not None else []),
        validation_metrics=json.dumps(model.validation_metrics), created_at=model.created_at,
        data_provenance=json.dumps(model.data_provenance),
    )
    return target


def load_model(path: str | Path) -> ScreeningModel:
    """Load a model saved by :func:`save_model`."""
    with np.load(Path(path), allow_pickle=False) as payload:
        bias = payload["correction_bias"]
        return ScreeningModel(
            tuple(payload["feature_names"].tolist()), tuple(payload["target_names"].tolist()),
            payload["x_means"], payload["x_scales"], payload["y_means"], payload["y_scales"],
            payload["regression_weights"], payload["classifier_weights"], tuple(payload["classes"].tolist()),
            int(payload["training_rows"]), int(payload["experimental_rows"]), str(payload["version"]),
            bias if len(bias) else None, json.loads(str(payload["validation_metrics"])), str(payload["created_at"]),
            json.loads(str(payload["data_provenance"])) if "data_provenance" in payload.files else {},
        )


def _numeric(frame: pd.DataFrame, columns: tuple[str, ...]) -> np.ndarray:
    functional_groups = frame.get("functional_group_type", pd.Series("", index=frame.index)).astype(str).str.strip()
    values = pd.DataFrame(index=frame.index)
    for name in columns:
        if name in FUNCTIONAL_GROUP_FEATURES:
            values[name] = functional_groups.eq(FUNCTIONAL_GROUP_FEATURES[name]).astype(float)
        elif name in frame:
            values[name] = pd.to_numeric(frame[name], errors="coerce")
        else:
            values[name] = 0.0
    values = values.replace([np.inf, -np.inf], np.nan).fillna(0.0)
    return values.to_numpy(dtype=float)


def _class_from_score(score: np.ndarray) -> np.ndarray:
    return np.select([score >= 78, score >= 64, score >= 50], ["A", "B", "C"], default="D")


def _multi_objective_score(predictions: pd.DataFrame) -> np.ndarray:
    scaled = np.column_stack([
        np.clip(predictions["wide_temp_adhesion_mpa"] / 42.0, 0, 1),
        np.clip(predictions["healing_efficiency_pct"] / 100.0, 0, 1),
        np.clip(predictions["atomic_oxygen_retention_pct"] / 100.0, 0, 1),
        np.clip(predictions["uv_retention_pct"] / 100.0, 0, 1),
        np.clip(predictions["am_feasibility"] / 100.0, 0, 1),
    ])
    return 100 * scaled @ np.array([0.30, 0.18, 0.22, 0.18, 0.12])


def _with_experiments(candidates: pd.DataFrame, experiments: pd.DataFrame | None) -> tuple[pd.DataFrame, int]:
    frame = candidates.copy()
    if experiments is None or experiments.empty:
        return frame, 0
    exp = experiments.copy()
    if "properties" in exp.columns:
        expanded = exp["properties"].apply(lambda value: json.loads(value) if isinstance(value, str) else (value or {})).apply(pd.Series)
        exp = pd.concat([exp.drop(columns=["properties"]), expanded], axis=1)
    for source, target in EXPERIMENT_FEATURE_ALIASES.items():
        if source in exp.columns and target not in exp.columns:
            exp[target] = exp[source]
    if "candidate_id" not in exp.columns:
        raise ValueError("实验数据必须包含 candidate_id")
    exp = exp.drop_duplicates("candidate_id", keep="last").set_index("candidate_id")
    frame = frame.set_index("candidate_id")
    matched = frame.index.intersection(exp.index)
    if len(matched) == 0:
        raise ValueError("实验数据中的 candidate_id 未匹配候选库")
    for name in FEATURE_COLUMNS + OUTPUT_COLUMNS:
        if name in exp.columns:
            values = pd.to_numeric(exp.loc[matched, name], errors="coerce")
            frame.loc[matched, name] = values.combine_first(frame.loc[matched, name])
    return frame.reset_index(), len(matched)


def train_screening_models(
    candidates: pd.DataFrame, experiments: pd.DataFrame | None = None,
    *, alpha: float = 0.15, version: str = "proxy-v1",
) -> ScreeningModel:
    """Train multi-target regression and one-vs-rest classification models."""
    if not isinstance(candidates, pd.DataFrame):
        candidates = pd.DataFrame(candidates)
    if candidates.empty:
        raise ValueError("候选库不能为空")
    if experiments is not None and not isinstance(experiments, pd.DataFrame):
        experiments = pd.DataFrame(experiments)
    frame, experimental_rows = _with_experiments(candidates, experiments)
    x = _numeric(frame, FEATURE_COLUMNS)
    y_frame = frame.reindex(columns=OUTPUT_COLUMNS, fill_value=0).apply(pd.to_numeric, errors="coerce").fillna(0.0)
    y = y_frame.to_numpy(dtype=float)
    x_means, x_scales = x.mean(axis=0), x.std(axis=0)
    x_scales[x_scales < 1e-8] = 1.0
    y_means, y_scales = y.mean(axis=0), y.std(axis=0)
    y_scales[y_scales < 1e-8] = 1.0
    xs, ys = (x - x_means) / x_scales, (y - y_means) / y_scales
    design = np.column_stack([np.ones(len(xs)), xs])
    ridge = np.eye(design.shape[1]) * float(alpha)
    ridge[0, 0] = 0.0
    regression = np.linalg.solve(design.T @ design + ridge, design.T @ ys)
    score = _multi_objective_score(y_frame)
    labels = frame["screening_class"].astype(str).to_numpy() if "screening_class" in frame else _class_from_score(score)
    classes = tuple(name for name in CLASS_ORDER if name in set(labels)) or CLASS_ORDER
    class_targets = np.column_stack([(labels == name).astype(float) for name in classes])
    classifier = np.linalg.solve(design.T @ design + ridge, design.T @ class_targets)
    validation_metrics: dict[str, float] = {}
    if len(xs) >= 8:
        validation = np.arange(len(xs)) % 5 == 0
        training = ~validation
        train_design = design[training]
        train_ridge = np.eye(train_design.shape[1]) * float(alpha)
        train_ridge[0, 0] = 0.0
        validation_weights = np.linalg.solve(train_design.T @ train_design + train_ridge, train_design.T @ ys[training])
        validation_prediction = train_design if not validation.any() else design[validation] @ validation_weights
        actual = ys[validation]
        for index, name in enumerate(OUTPUT_COLUMNS):
            residual = validation_prediction[:, index] - actual[:, index]
            validation_metrics[f"{name}_rmse_scaled"] = float(np.sqrt(np.mean(residual ** 2)))
            denominator = float(np.sum((actual[:, index] - np.mean(actual[:, index])) ** 2))
            validation_metrics[f"{name}_r2"] = float(1 - np.sum(residual ** 2) / denominator) if denominator > 1e-12 else 0.0
        validation_logits = design[validation] @ classifier
        validation_labels = np.asarray(classes)[np.argmax(validation_logits, axis=1)]
        actual_labels = labels[validation]
        validation_metrics["classification_accuracy"] = float(np.mean(validation_labels == actual_labels))
        recalls = []
        for label in classes:
            positives = actual_labels == label
            if positives.any():
                recalls.append(float(np.mean(validation_labels[positives] == label)))
        validation_metrics["classification_macro_recall"] = float(np.mean(recalls)) if recalls else 0.0
        validation_metrics["validation_rows"] = float(np.sum(validation))
    validation_metrics["mean_rmse_scaled"] = float(np.mean([value for key, value in validation_metrics.items() if key.endswith("_rmse_scaled")])) if any(key.endswith("_rmse_scaled") for key in validation_metrics) else 0.0
    source_counts = {
        "candidate_rows": len(frame),
        "proxy_rows": int(len(frame) - frame.get("simulation_source", pd.Series(index=frame.index, dtype=str)).eq("external").sum()),
        "external_rows": int(frame.get("simulation_source", pd.Series(index=frame.index, dtype=str)).eq("external").sum()),
        "experimental_rows": experimental_rows,
    }
    return ScreeningModel(
        FEATURE_COLUMNS, OUTPUT_COLUMNS, x_means, x_scales, y_means, y_scales,
        regression, classifier, classes, len(frame), experimental_rows, version,
        None, validation_metrics, datetime.now(timezone.utc).isoformat(), source_counts,
    )


def predict_screening(model: ScreeningModel, candidates: pd.DataFrame) -> pd.DataFrame:
    """Predict all output metrics and screening class for candidate rows."""
    if not isinstance(candidates, pd.DataFrame):
        candidates = pd.DataFrame(candidates)
    x = (_numeric(candidates, model.feature_names) - model.x_means) / model.x_scales
    design = np.column_stack([np.ones(len(x)), x])
    pred = design @ model.regression_weights * model.y_scales + model.y_means
    if model.correction_bias is not None:
        pred = pred + model.correction_bias
    result = candidates.copy().reset_index(drop=True)
    for index, name in enumerate(model.target_names):
        result[f"predicted_{name}"] = pred[:, index]
    predicted = result[[f"predicted_{name}" for name in model.target_names]].rename(columns=lambda name: name.removeprefix("predicted_"))
    result["predicted_multi_objective_score"] = _multi_objective_score(predicted)
    distance = np.sqrt(np.mean(((x) ** 2), axis=1))
    result["prediction_uncertainty"] = model.validation_metrics.get("mean_rmse_scaled", 0.0) * (1.0 + np.clip(distance, 0, 4) / 4.0)
    logits = design @ model.classifier_weights
    result["predicted_screening_class"] = np.array(model.classes)[np.argmax(logits, axis=1)]
    result["model_version"] = model.version
    return result


def screen_candidates(
    candidates: pd.DataFrame, *, experiments: pd.DataFrame | None = None,
    top_n: int = 20, minimum_class: str = "D", version: str = "proxy-v1",
) -> tuple[pd.DataFrame, ScreeningModel]:
    """Train/update models and return the highest-scoring feasible candidates."""
    if not isinstance(candidates, pd.DataFrame):
        candidates = pd.DataFrame(candidates)
    baseline = train_screening_models(candidates, version=version)
    if experiments is not None and not experiments.empty:
        # Retrain on measured targets and calibrate the residual bias against
        # the pre-feedback model before ranking the untouched candidate pool.
        model = update_with_experiments(
            candidates, experiments, previous_model=baseline,
            version=f"{version}+exp-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}",
        )
    else:
        model = baseline
    predictions = predict_screening(model, candidates)
    allowed = CLASS_ORDER[CLASS_ORDER.index(minimum_class):] if minimum_class in CLASS_ORDER else CLASS_ORDER
    selected = predictions[predictions["predicted_screening_class"].isin(allowed)]
    if selected.empty:
        selected = predictions
    return selected.sort_values("predicted_multi_objective_score", ascending=False).head(max(1, int(top_n))).reset_index(drop=True), model


def update_with_experiments(
    candidates: pd.DataFrame, experiments: pd.DataFrame,
    *, previous_model: ScreeningModel | None = None, version: str | None = None,
) -> ScreeningModel:
    """Retrain the closed loop with measured thermal, mechanical, adhesion, and stability data."""
    next_version = version or (f"{previous_model.version}+exp" if previous_model else "exp-v1")
    updated = train_screening_models(candidates, experiments, version=next_version)
    if previous_model is None:
        return updated
    exp = experiments if isinstance(experiments, pd.DataFrame) else pd.DataFrame(experiments)
    if "properties" in exp.columns:
        expanded = exp["properties"].apply(lambda value: json.loads(value) if isinstance(value, str) else (value or {})).apply(pd.Series)
        exp = pd.concat([exp.drop(columns=["properties"]), expanded], axis=1)
    for source, target in EXPERIMENT_FEATURE_ALIASES.items():
        if source in exp.columns and target not in exp.columns:
            exp[target] = exp[source]
    if "candidate_id" not in exp.columns:
        raise ValueError("实验数据必须包含 candidate_id")
    exp = exp.drop_duplicates("candidate_id", keep="last")
    base = candidates if isinstance(candidates, pd.DataFrame) else pd.DataFrame(candidates)
    base = base.drop_duplicates("candidate_id", keep="last")
    base = base[base["candidate_id"].isin(exp["candidate_id"])]
    if base.empty:
        return updated
    baseline = predict_screening(previous_model, base)
    bias = np.zeros(len(OUTPUT_COLUMNS), dtype=float)
    for index, name in enumerate(OUTPUT_COLUMNS):
        if name in exp.columns:
            observed = exp.set_index("candidate_id")[name].reindex(base["candidate_id"]).to_numpy(dtype=float)
            predicted = baseline[f"predicted_{name}"].to_numpy(dtype=float)
            valid = np.isfinite(observed)
            if valid.any():
                bias[index] = float(np.mean(observed[valid] - predicted[valid]))
    return replace(updated, correction_bias=bias)


def recommend_next_experiments(
    model: ScreeningModel, candidates: pd.DataFrame, *, tested_ids: Sequence[str] = (), batch_size: int = 8,
    exploration_weight: float = 0.25,
) -> pd.DataFrame:
    """Select the next candidates by predicted performance plus feature-space exploration."""
    predictions = predict_screening(model, candidates)
    tested = set(tested_ids)
    if tested and "candidate_id" in predictions:
        predictions = predictions[~predictions["candidate_id"].isin(tested)]
    exploration = np.clip(predictions["prediction_uncertainty"].to_numpy(dtype=float), 0, 1)
    predictions["acquisition_score"] = predictions["predicted_multi_objective_score"] + 100 * float(exploration_weight) * exploration
    return predictions.sort_values("acquisition_score", ascending=False).head(max(1, int(batch_size))).reset_index(drop=True)


def closed_loop_screening(
    candidates: pd.DataFrame | None = None, experiments: pd.DataFrame | None = None,
    *, max_records: int = 720, top_n: int = 20, seed: int = 7,
) -> dict[str, object]:
    """Build, fit, rank, and optionally correct a candidate library in one call."""
    if candidates is None:
        from .candidate_library import build_candidate_library
        candidates = build_candidate_library(max_records=max_records, seed=seed)
    elif not isinstance(candidates, pd.DataFrame):
        candidates = pd.DataFrame(candidates)
    baseline = train_screening_models(candidates, version="proxy-v1")
    model = update_with_experiments(candidates, experiments, previous_model=baseline, version=f"proxy-v1+exp-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}") if experiments is not None and not experiments.empty else baseline
    predictions = predict_screening(model, candidates)
    shortlist = predictions.sort_values("predicted_multi_objective_score", ascending=False).head(max(1, int(top_n))).reset_index(drop=True)
    return {"candidates": candidates, "predictions": predictions, "shortlist": shortlist, "model": model}
