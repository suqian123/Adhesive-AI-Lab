"""End-to-end AI prediction plus simulation orchestration."""

from __future__ import annotations

from dataclasses import asdict

from .features import formulation_features, molecule_features
from .model import feature_importance, predict, train_model
from .simulation import run_interface_simulation


def run_screening(
    *, resin_smiles: str, tackifier_smiles: str, filler_smiles: str,
    resin_ratio: float, tackifier_ratio: float, filler_ratio: float,
    temperature_c: float, humidity_pct: float, simulation_steps: int = 650,
    seed: int = 7,
) -> dict:
    features = formulation_features(
        resin_smiles, tackifier_smiles, filler_smiles,
        resin_ratio, tackifier_ratio, filler_ratio, temperature_c, humidity_pct,
    )
    bundle = train_model(seed=seed)
    ai = predict(bundle, features)
    simulation = run_interface_simulation(
        compatibility_index=features["compatibility_index"],
        polar_fraction=features["weighted_polar_fraction"],
        filler_ratio=features["filler_ratio"],
        temperature_c=temperature_c, steps=simulation_steps, seed=seed,
    )
    combined = {
        "adhesion_work_mj_m2": round(ai["adhesion_work_mj_m2"]*.62 + simulation.adhesion_work_mj_m2*.38, 2),
        "interface_energy_mj_m2": round(ai["interface_energy_mj_m2"]*.58 + simulation.interface_energy_mj_m2*.42, 2),
        "density_g_cm3": round(ai["density_g_cm3"], 3),
        "surface_coverage": round(float(simulation.coverage[-1]), 3),
        "stability_score": round(simulation.stability_score, 3),
        "compatibility_index": round(features["compatibility_index"], 3),
    }
    return {
        "features": features,
        "molecules": {
            "resin": asdict(molecule_features(resin_smiles)),
            "tackifier": asdict(molecule_features(tackifier_smiles)),
            "filler": asdict(molecule_features(filler_smiles)),
        },
        "ai_prediction": ai, "combined": combined,
        "importance": feature_importance(bundle), "simulation": simulation,
    }
