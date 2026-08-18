"""Synthetic-data surrogate model for the first screening loop."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from .features import feature_names, formulation_features


@dataclass
class ModelBundle:
    weights: np.ndarray
    means: np.ndarray
    scales: np.ndarray
    target_means: np.ndarray
    target_scales: np.ndarray
    target_names: tuple[str, ...]
    training_rows: int


def make_training_data(seed: int = 42, rows: int = 700) -> tuple[pd.DataFrame, pd.DataFrame]:
    rng = np.random.default_rng(seed)
    records, targets = [], []
    for _ in range(rows):
        ratios = rng.dirichlet([5.0, 2.2, 1.4])
        record = formulation_features(
            rng.choice(["CC(C)C", "CCO", "c1ccccc1", "CC(C)(C)C(=O)O"]),
            rng.choice(["CCOC(=O)C", "c1ccccc1O", "CC(C)C(C)O", "C1CCCCC1"]),
            rng.choice(["O=[Si](O)O", "CC", "C", "O=C=O"]),
            *ratios, float(rng.uniform(-20, 100)), float(rng.uniform(10, 90)),
        )
        c, p, f, t, h = (
            record["compatibility_index"], record["weighted_polar_fraction"],
            record["weighted_flexibility"], record["temperature_factor"],
            record["humidity_factor"],
        )
        filler = record["filler_ratio"]
        records.append(record)
        targets.append({
            "adhesion_work_mj_m2": max(10.0, 82 + 42*c + 27*p + 18*f + 13*t + 8*h - 24*filler + rng.normal(0, 3.2)),
            "interface_energy_mj_m2": max(3.0, 24 + 38*c + 19*p + 9*t - 11*filler + rng.normal(0, 1.7)),
            "density_g_cm3": max(0.5, 0.84 + 0.22*filler + 0.06*record["weighted_molecular_weight"]/100 + rng.normal(0, .012)),
        })
    return pd.DataFrame(records)[feature_names()], pd.DataFrame(targets)


def train_model(seed: int = 42, rows: int = 700) -> ModelBundle:
    features, targets = make_training_data(seed, rows)
    x = features.to_numpy(dtype=float)
    y = targets.to_numpy(dtype=float)
    means = x.mean(axis=0)
    scales = x.std(axis=0)
    scales[scales < 1e-8] = 1.0
    target_means = y.mean(axis=0)
    target_scales = y.std(axis=0)
    target_scales[target_scales < 1e-8] = 1.0
    x_scaled = (x - means) / scales
    y_scaled = (y - target_means) / target_scales

    # A small randomized ridge ensemble is enough for the offline MVP and
    # keeps the scientific core usable when binary ML wheels are unavailable.
    rng = np.random.default_rng(seed)
    ensemble = []
    for _ in range(24):
        rows_idx = rng.integers(0, len(x_scaled), len(x_scaled))
        mask = rng.random(x_scaled.shape[1]) > 0.15
        mask[:5] = True
        design = x_scaled[rows_idx][:, mask]
        design = np.column_stack([np.ones(len(design)), design])
        ridge = np.eye(design.shape[1]) * 0.08
        ridge[0, 0] = 0.0
        coefficient = np.linalg.solve(design.T @ design + ridge, design.T @ y_scaled[rows_idx])
        full = np.zeros((x_scaled.shape[1] + 1, y_scaled.shape[1]))
        full[0] = coefficient[0]
        full[1:][mask] = coefficient[1:]
        ensemble.append(full)
    return ModelBundle(
        weights=np.stack(ensemble),
        means=means,
        scales=scales,
        target_means=target_means,
        target_scales=target_scales,
        target_names=tuple(targets.columns),
        training_rows=len(features),
    )


def predict(bundle: ModelBundle, features: dict[str, float]) -> dict[str, float]:
    values = np.array([[features[n] for n in feature_names()]], dtype=float)
    values = (values - bundle.means) / bundle.scales
    design = np.column_stack([np.ones(len(values)), values])
    scaled_prediction = np.mean(design @ bundle.weights, axis=0)
    prediction = scaled_prediction * bundle.target_scales + bundle.target_means
    return {name: float(value) for name, value in zip(bundle.target_names, prediction[0])}


def feature_importance(bundle: ModelBundle) -> pd.DataFrame:
    return pd.DataFrame({
        "feature": feature_names(),
        "importance": np.mean(np.abs(bundle.weights[:, 1:]), axis=(0, 2)),
    }).sort_values("importance", ascending=False).head(8).reset_index(drop=True)
