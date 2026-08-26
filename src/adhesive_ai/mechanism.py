"""Candidate-level fusion of proxy, external calculation, and experiment data.

The fusion is deliberately field-wise: a completed calculation only replaces
the observables it actually produced. Missing values remain explicitly marked
as proxy estimates, and scalar MD observables are used as anchors for the
proxy temperature curve rather than being presented as a full real sweep.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any, Mapping, Sequence

import numpy as np
import pandas as pd

from .multiscale import calculate_interface_and_cg, calculate_quantum_surface, calculate_resin_md
from .result_integration import to_jsonable
from .screening import OUTPUT_COLUMNS


QUANTUM_EXTERNAL_MAP = {
    "adsorption_energy_ev": "oxygen_adsorption_ev",
    "oxygen_adsorption_ev": "oxygen_adsorption_ev",
    "reaction_energy_ev": "oxygen_reaction_energy_ev",
    "oxygen_reaction_energy_ev": "oxygen_reaction_energy_ev",
    "reaction_barrier_ev": "oxygen_reaction_barrier_ev",
    "oxygen_reaction_barrier_ev": "oxygen_reaction_barrier_ev",
    "ce3_fraction": "ce3_fraction",
    "reactive_oxygen_capture_index": "reactive_oxygen_capture_index",
    "pda_resin_hbond_ev": "pda_resin_hbond_ev",
    "pda_surface_coordination_ev": "pda_surface_coordination_ev",
    "pda_reaction_ev": "pda_reaction_ev",
}
MD_PROFILE_FIELDS = (
    "free_volume_fraction", "cohesive_energy_density_mj_m3", "elastic_modulus_gpa", "cte_ppm_k",
)
INTERFACE_EXTERNAL_MAP = {
    "binding_energy_mj_m2": "binding_energy_mj_m2",
    "interface_binding_energy_mj_m2": "binding_energy_mj_m2",
    "hydrogen_bond_density_nm2": "hydrogen_bond_density_nm2",
    "coordination_bond_density_nm2": "coordination_bond_density_nm2",
    "covalent_reaction_fraction": "covalent_reaction_fraction",
    "dispersion_index": "dispersion_index",
    "coarse_grained_reinforcement_index": "coarse_grained_reinforcement_index",
    "adhesion_work_mj_m2": "adhesion_work_mj_m2",
    "stability_score": "stability_score",
}
EXPERIMENT_FEATURE_MAP = {
    "measured_tg_c": "glass_transition_c",
    "measured_free_volume": "free_volume_fraction",
    "measured_chain_mobility": "chain_mobility",
    "measured_cohesive_energy_density": "cohesive_energy_density_mj_m3",
    "measured_modulus_gpa": "elastic_modulus_gpa",
    "measured_cte_ppm_k": "cte_ppm_k",
}


def _record(value: Mapping[str, Any] | pd.Series | None) -> dict[str, Any]:
    if value is None:
        return {}
    return value.to_dict() if isinstance(value, pd.Series) else dict(value)


def _finite(value: Any) -> bool:
    if value is None or isinstance(value, (Mapping, str, bytes)):
        return False
    try:
        array = np.asarray(value, dtype=float)
    except (TypeError, ValueError):
        return False
    return bool(array.size and np.isfinite(array).all())


def _source(payload: Mapping[str, Any], component: str) -> str:
    return f"external:{payload.get('job_id') or payload.get('engine') or component}"


def _latest_values(experiments: pd.DataFrame, candidate_id: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    if experiments is None or experiments.empty or "candidate_id" not in experiments:
        return {}, []
    rows = experiments.loc[experiments["candidate_id"].astype(str) == candidate_id].copy()
    if rows.empty:
        return {}, []
    if "created_at" in rows:
        rows = rows.sort_values("created_at", kind="stable")
    latest: dict[str, Any] = {}
    for name in (*OUTPUT_COLUMNS, *EXPERIMENT_FEATURE_MAP):
        if name not in rows:
            continue
        available = rows.loc[pd.to_numeric(rows[name], errors="coerce").notna()]
        if not available.empty:
            record = available.iloc[-1]
            latest[name] = {
                "value": float(record[name]),
                "source": f"experiment:{record.get('test_batch') or record.get('source') or 'record'}",
            }
    return latest, [to_jsonable(row) for row in rows.to_dict("records")]


def _anchor_profile(proxy: Sequence[float], real: Any, temperatures: np.ndarray) -> np.ndarray:
    proxy_values = np.asarray(proxy, dtype=float)
    real_values = np.asarray(real, dtype=float).reshape(-1)
    if real_values.size == proxy_values.size:
        return real_values
    if real_values.size != 1:
        raise ValueError("External MD profile must be scalar or match the temperature grid")
    reference_index = int(np.argmin(np.abs(temperatures - 25.0)))
    return proxy_values + float(real_values[0] - proxy_values[reference_index])


def fuse_candidate_mechanism(
    candidate: Mapping[str, Any] | pd.Series,
    *,
    calculations: Mapping[str, Any] | None = None,
    experiments: pd.DataFrame | None = None,
    facet: str = "(111)",
    oxygen_vacancy_fraction: float = 0.08,
    hydroxyl_fraction: float = 0.35,
    particle_size_nm: float = 35.0,
    temperatures_c: Sequence[float] = (-180.0, -120.0, -60.0, 25.0, 80.0, 150.0),
) -> dict[str, Any]:
    """Fuse one candidate using experiment > external calculation > proxy priority."""
    row = _record(candidate)
    candidate_id = str(row.get("candidate_id") or "").strip()
    if not candidate_id:
        raise ValueError("Candidate mechanism fusion requires candidate_id")
    temperatures = np.asarray(temperatures_c, dtype=float)
    calculation = dict(calculations or {})
    dft = _record(calculation.get("dft"))
    external_md = _record(calculation.get("md"))
    external_interface = _record(calculation.get("interface"))
    latest_experiment, experiment_records = _latest_values(experiments if experiments is not None else pd.DataFrame(), candidate_id)

    quantum_proxy = calculate_quantum_surface(
        facet=facet,
        oxygen_vacancy_fraction=oxygen_vacancy_fraction,
        hydroxyl_fraction=hydroxyl_fraction,
        resin_polarity=float(row.get("resin_polarity", 0.5)),
        dynamic_healing=float(row.get("dynamic_healing", 0.45)),
    )
    md_proxy = calculate_resin_md(
        crosslink_density=float(row.get("crosslink_density", 0.65)),
        resin_thermal=float(row.get("resin_thermal", 0.5)),
        resin_toughness=float(row.get("resin_toughness", row.get("low_temp_toughness_index", 0.5))),
        resin_polarity=float(row.get("resin_polarity", 0.5)),
        dynamic_healing=float(row.get("dynamic_healing", 0.45)),
        dynamic_mobility=float(row.get("dynamic_mobility", row.get("chain_mobility", 0.25))),
        filler_pct=float(row.get("filler_pct", 0.0)),
        temperatures_c=temperatures,
    )
    interface_proxy = calculate_interface_and_cg(
        quantum=quantum_proxy,
        md=md_proxy,
        filler_pct=float(row.get("filler_pct", 0.0)),
        crosslink_density=float(row.get("crosslink_density", 0.65)),
        resin_polarity=float(row.get("resin_polarity", 0.5)),
        particle_size_nm=particle_size_nm,
    )
    quantum = asdict(quantum_proxy)
    md = asdict(md_proxy)
    md["chain_mobility"] = float(row.get("chain_mobility", row.get("dynamic_mobility", 0.25)))
    interface = asdict(interface_proxy)
    provenance: dict[str, str] = {
        **{f"quantum.{name}": "physics-informed-proxy" for name in quantum},
        **{f"md.{name}": "physics-informed-proxy" for name in md if name != "temperatures_c"},
        **{f"interface.{name}": "physics-informed-proxy" for name in interface},
    }

    dft_source = _source(dft, "dft")
    for external_name, fused_name in QUANTUM_EXTERNAL_MAP.items():
        if _finite(dft.get(external_name)):
            quantum[fused_name] = float(dft[external_name])
            provenance[f"quantum.{fused_name}"] = dft_source

    md_source = _source(external_md, "md")
    if _finite(external_md.get("glass_transition_c")):
        md["glass_transition_c"] = float(external_md["glass_transition_c"])
        provenance["md.glass_transition_c"] = md_source
    for name in MD_PROFILE_FIELDS:
        if _finite(external_md.get(name)):
            values = np.asarray(external_md[name], dtype=float).reshape(-1)
            md[name] = _anchor_profile(md[name], values, temperatures)
            suffix = "" if values.size == temperatures.size else "+proxy-temperature-shape"
            provenance[f"md.{name}"] = md_source + suffix

    for experiment_name, fused_name in EXPERIMENT_FEATURE_MAP.items():
        if experiment_name not in latest_experiment:
            continue
        item = latest_experiment[experiment_name]
        if fused_name == "glass_transition_c":
            md[fused_name] = item["value"]
        elif fused_name in MD_PROFILE_FIELDS:
            md[fused_name] = _anchor_profile(md[fused_name], item["value"], temperatures)
        else:
            md[fused_name] = item["value"]
        provenance[f"md.{fused_name}"] = item["source"] + ("+proxy-temperature-shape" if fused_name in MD_PROFILE_FIELDS else "")

    interface_source = _source(external_interface, "interface")
    for external_name, fused_name in INTERFACE_EXTERNAL_MAP.items():
        if _finite(external_interface.get(external_name)):
            interface[fused_name] = float(external_interface[external_name])
            provenance[f"interface.{fused_name}"] = interface_source

    performance: dict[str, float] = {}
    stored_predictions = _record(calculation.get("predictions"))
    model_version = calculation.get("model_version")
    for name in OUTPUT_COLUMNS:
        if name in latest_experiment:
            performance[name] = latest_experiment[name]["value"]
            provenance[f"performance.{name}"] = latest_experiment[name]["source"]
        elif _finite(stored_predictions.get(name)):
            performance[name] = float(stored_predictions[name])
            provenance[f"performance.{name}"] = f"model:{model_version or 'persisted'}"
        else:
            performance[name] = float(row.get(name, 0.0))
            provenance[f"performance.{name}"] = "physics-informed-proxy"

    trajectory = {
        "available": False,
        "steps": [],
        "energy": [],
        "coverage": [],
        "final_positions": [],
        "source": "not-persisted",
    }
    steps, energy = external_interface.get("steps"), external_interface.get("energy")
    step_values = np.asarray(steps, dtype=float).reshape(-1) if _finite(steps) else np.asarray([])
    energy_values = np.asarray(energy, dtype=float).reshape(-1) if _finite(energy) else np.asarray([])
    if step_values.size and step_values.size == energy_values.size:
        trajectory = {
            "available": True,
            "steps": to_jsonable(steps),
            "energy": to_jsonable(energy),
            "coverage": to_jsonable(external_interface.get("coverage", [])),
            "final_positions": to_jsonable(external_interface.get("final_positions", [])),
            "source": interface_source,
        }

    source_values = list(provenance.values())
    source_summary = {
        "experimental": sum(value.startswith("experiment:") for value in source_values),
        "external": sum(value.startswith("external:") and "+proxy" not in value for value in source_values),
        "hybrid": sum("+proxy" in value for value in source_values),
        "model": sum(value.startswith("model:") for value in source_values),
        "proxy": sum(value == "physics-informed-proxy" for value in source_values),
    }
    return {
        "candidate_id": candidate_id,
        "candidate": to_jsonable(row),
        "conditions": {
            "facet": dft.get("facet") or facet,
            "oxygen_vacancy_fraction": dft.get("oxygen_vacancy_fraction") if dft.get("oxygen_vacancy_fraction") is not None else oxygen_vacancy_fraction,
            "hydroxyl_fraction": dft.get("hydroxyl_fraction") if dft.get("hydroxyl_fraction") is not None else hydroxyl_fraction,
            "temperatures_c": temperatures.tolist(),
        },
        "quantum": to_jsonable(quantum),
        "md": to_jsonable(md),
        "interface": to_jsonable(interface),
        "performance": performance,
        "trajectory": trajectory,
        "provenance": provenance,
        "source_summary": source_summary,
        "experiment_records": experiment_records,
        "readiness": {
            "dft": bool(dft),
            "md": bool(external_md),
            "interface": bool(external_interface),
            "experiment": bool(experiment_records),
        },
    }


def mechanism_provenance_frame(result: Mapping[str, Any]) -> pd.DataFrame:
    """Return field/value/source rows for UI inspection and export."""
    rows: list[dict[str, Any]] = []
    provenance = dict(result.get("provenance", {}))
    for path, source in provenance.items():
        section, name = path.split(".", 1)
        value = _record(result.get(section)).get(name)
        if isinstance(value, list):
            value = f"{len(value)}-point profile"
        rows.append({"section": section, "metric": name, "value": value, "source": source})
    return pd.DataFrame(rows)
