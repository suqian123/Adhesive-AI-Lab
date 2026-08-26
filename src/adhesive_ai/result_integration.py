"""Map completed DFT, MD, and interface calculations back into candidates."""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from typing import Any, Mapping

import numpy as np
import pandas as pd

from .engines import DFTResult, MDObservables


def to_jsonable(value: Any) -> Any:
    """Convert dataclasses and NumPy values to JSON-compatible builtins."""
    if is_dataclass(value):
        return to_jsonable(asdict(value))
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, Mapping):
        return {str(key): to_jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_jsonable(item) for item in value]
    return value


def _record(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, Mapping):
        return dict(value)
    raise TypeError("Calculation results must be a dataclass or mapping")


def update_candidate_with_external_results(
    candidates: pd.DataFrame, candidate_id: str, *, dft: DFTResult | Mapping[str, Any] | None = None,
    md: MDObservables | Mapping[str, Any] | None = None, interface: Mapping[str, Any] | Any | None = None,
) -> pd.DataFrame:
    """Return candidates with external calculation values replacing proxy features."""
    frame = candidates.copy()
    matched = frame.index[frame["candidate_id"] == candidate_id]
    if len(matched) != 1:
        raise ValueError(f"Expected exactly one candidate_id match for {candidate_id}")
    row = matched[0]
    for name in ("simulation_source", "external_dft", "external_md", "external_interface", "feature_provenance"):
        if name not in frame:
            frame[name] = [None] * len(frame)
    dft_record, md_record, interface_record = _record(dft), _record(md), _record(interface)
    dft_map = {
        "adsorption_energy_ev": "filler_oxygen_adsorption_ev",
        "reactive_oxygen_capture_index": "filler_radical_capture_index",
        "radical_capture_capability": "filler_radical_capture_index",
    }
    md_map = {
        "glass_transition_c": "glass_transition_c", "free_volume_fraction": "free_volume_fraction",
        "cohesive_energy_density_mj_m3": "cohesive_energy_density_mj_m3",
        "elastic_modulus_gpa": "elastic_modulus_gpa", "cte_ppm_k": "cte_ppm_k",
    }
    interface_map = {
        "binding_energy_mj_m2": "interface_binding_energy_mj_m2",
        "interface_binding_energy_mj_m2": "interface_binding_energy_mj_m2",
        "covalent_bond_count": "interface_covalent_bond_count",
        "interface_covalent_bond_count": "interface_covalent_bond_count",
    }
    provenance = dict(frame.at[row, "feature_provenance"] or {})
    for source, target in dft_map.items():
        if dft_record.get(source) is not None:
            frame.loc[row, target] = float(dft_record[source])
            provenance[target] = f"external:{dft_record.get('job_id', 'dft')}"
    for source, target in md_map.items():
        if md_record.get(source) is not None:
            frame.loc[row, target] = float(md_record[source])
            provenance[target] = f"external:{md_record.get('job_id', 'md')}"
    for source, target in interface_map.items():
        if interface_record.get(source) is not None:
            frame.loc[row, target] = float(interface_record[source])
            provenance[target] = f"external:{interface_record.get('job_id', 'interface')}"
    frame.at[row, "simulation_source"] = "external"
    frame.at[row, "data_source"] = "hybrid-external"
    frame.at[row, "feature_provenance"] = provenance
    frame.at[row, "external_dft"] = to_jsonable(dft_record)
    frame.at[row, "external_md"] = to_jsonable(md_record)
    frame.at[row, "external_interface"] = to_jsonable(interface_record)
    return frame


def apply_external_results(candidates: pd.DataFrame, results: Mapping[str, Mapping[str, Any]]) -> pd.DataFrame:
    """Apply completed jobs keyed by candidate ID to the candidate database."""
    frame = candidates.copy()
    for candidate_id, payload in results.items():
        frame = update_candidate_with_external_results(
            frame, candidate_id, dft=payload.get("dft"), md=payload.get("md"), interface=payload.get("interface"),
        )
    return frame


def closed_loop_with_external_results(
    candidates: pd.DataFrame, results: Mapping[str, Mapping[str, Any]], *, experiments: pd.DataFrame | None = None,
    top_n: int = 20,
) -> dict[str, Any]:
    """Update candidates from completed calculations, retrain, and re-rank them."""
    from .screening import screen_candidates

    updated = apply_external_results(candidates, results)
    version = "external+exp-v1" if experiments is not None else "external-v1"
    shortlist, model = screen_candidates(updated, experiments=experiments, top_n=top_n, version=version)
    return {"candidates": updated, "shortlist": shortlist, "model": model}
