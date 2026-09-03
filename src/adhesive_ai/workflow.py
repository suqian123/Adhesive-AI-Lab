"""Connect candidate screening, experiments, and completed external jobs."""

from __future__ import annotations

from dataclasses import asdict, dataclass, is_dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

import numpy as np
import pandas as pd

from .database import (
    load_experiments,
    load_latest_simulation_results,
    save_model_version,
    save_simulation,
)
from .engines import compute_md_observables
from .jobs import JobRecord, get_job_status, parse_job_result, read_job_result_text, update_job_metadata
from .result_integration import apply_external_results, to_jsonable
from .result_arbitration import annotate_payload, build_provenance, merge_external_payloads
from .screening import predict_screening, save_model, screen_candidates
from .simulation import run_interface_simulation


@dataclass(frozen=True)
class IntegrationResult:
    job_id: str
    candidate_id: str
    candidates: pd.DataFrame
    shortlist: pd.DataFrame
    model: object
    payload: dict[str, dict[str, Any]]


def load_connected_state(
    candidates: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, dict[str, Any]]]:
    """Overlay persisted external results and load all matching experiments."""
    candidate_ids = candidates["candidate_id"].astype(str).tolist()
    formulations = dict(zip(candidate_ids, candidates["formulation_id"].astype(str)))
    payloads = load_latest_simulation_results(candidate_ids, formulation_ids=formulations)
    external = {
        candidate_id: {
            "dft": payload.get("dft", {}),
            "md": payload.get("md", {}),
            "interface": payload.get("interface", {}),
        }
        for candidate_id, payload in payloads.items()
    }
    connected = apply_external_results(candidates, external) if external else candidates.copy()
    return connected, load_experiments(candidate_ids, formulation_ids=formulations), payloads


def _require_matching_formulation(
    candidates: pd.DataFrame,
    candidate_id: str,
    recorded_formulation_id: object,
) -> tuple[str, str]:
    matched = candidates.loc[candidates["candidate_id"].astype(str) == candidate_id]
    if len(matched) != 1:
        raise ValueError(f"Candidate {candidate_id} is not present in the current candidate library")
    expected = str(matched.iloc[0].get("formulation_id") or "").strip()
    actual = str(recorded_formulation_id or "").strip()
    if not actual:
        raise ValueError(f"任务 {candidate_id} 缺少配方指纹，属于旧记录，不能自动回写。")
    if actual != expected:
        raise ValueError(f"任务配方指纹与当前候选不匹配：{candidate_id}。为避免错配，已拒绝自动回写。")
    return candidate_id, expected


def _case_insensitive(series: Mapping[str, Any], *names: str) -> Any:
    lookup = {str(key).lower(): value for key, value in series.items()}
    return next((lookup[name.lower()] for name in names if name.lower() in lookup), None)


def _bulk_md_payload(record: JobRecord, parsed: object) -> dict[str, Any]:
    if not isinstance(parsed, Mapping):
        x, y = parsed
        return {"time": to_jsonable(x), "potential_energy": to_jsonable(y), "engine": record.engine, "observables_available": False}
    raw = {str(key): to_jsonable(value) for key, value in parsed.items()}
    temperature = _case_insensitive(parsed, "Temp", "Temperature")
    volume = _case_insensitive(parsed, "Vol", "Volume")
    energy = _case_insensitive(parsed, "Pe", "PotEng", "E_pair", "Energy")
    if temperature is None or volume is None or energy is None:
        return {"raw_thermo": raw, "engine": record.engine, "observables_available": False}
    temperature_values = np.asarray(temperature, dtype=float)
    metadata = record.metadata or {}
    if str(metadata.get("temperature_unit", "K")).upper() == "K":
        temperature_values = temperature_values - 273.15
    energy_values = np.asarray(energy, dtype=float)
    if str(metadata.get("energy_unit", "kcal/mol")).lower().startswith("kcal"):
        energy_values = energy_values * 4.184
    try:
        observables = compute_md_observables(temperature_values, volume, energy_values)
    except ValueError as exc:
        return {
            "raw_thermo": raw,
            "engine": record.engine,
            "observables_available": False,
            "observables_error": str(exc),
        }
    payload = asdict(observables)
    payload.update(raw_thermo=raw, engine=record.engine, observables_available=True)
    return to_jsonable(payload)


def calculation_payload(
    record: JobRecord,
    parsed: object,
    *,
    root: str | Path = "work/jobs",
) -> dict[str, dict[str, Any]]:
    """Convert one engine parser result into the cumulative calculation schema."""
    engine = record.engine.lower().replace(" ", "")
    metadata = record.metadata or {}
    kind = str(metadata.get("calculation_kind") or ("dft" if engine in {"vasp", "quantumespresso", "qe", "cp2k"} else "bulk_md"))
    if kind == "dft":
        if not is_dataclass(parsed) and not isinstance(parsed, Mapping):
            raise TypeError("DFT parser result must be a dataclass or mapping")
        payload = dict(to_jsonable(asdict(parsed) if is_dataclass(parsed) else parsed))
        payload.update(
            job_id=record.job_id,
            facet=metadata.get("facet"),
            oxygen_vacancy_fraction=metadata.get("oxygen_vacancy_fraction"),
            hydroxyl_fraction=metadata.get("hydroxyl_fraction"),
        )
        return {"dft": payload}
    if kind == "bulk_md":
        payload = _bulk_md_payload(record, parsed)
        payload.update(job_id=record.job_id, calculation_temperature_c=metadata.get("temperature_c"))
        return {"md": payload}
    if kind == "interface_md":
        output = read_job_result_text(record.job_id, root=root)
        area_nm2 = float(metadata.get("area_nm2", 100.0))
        common = {
            "compatibility_index": float(metadata.get("compatibility_index", 0.5)),
            "polar_fraction": float(metadata.get("polar_fraction", 0.5)),
            "filler_ratio": float(metadata.get("filler_ratio", 0.1)),
            "temperature_c": float(metadata.get("temperature_c", 25.0)),
            "area_nm2": area_nm2,
        }
        simulation = (
            run_interface_simulation(**common, engine="lammps", thermo_output=output)
            if engine == "lammps"
            else run_interface_simulation(**common, engine="gromacs", energy_xvg=output)
        )
        return {"interface": {
            "binding_energy_mj_m2": simulation.interface_energy_mj_m2,
            "adhesion_work_mj_m2": simulation.adhesion_work_mj_m2,
            "stability_score": simulation.stability_score,
            "engine": simulation.engine,
            "area_nm2": area_nm2,
            "job_id": record.job_id,
            "temperature_c": common["temperature_c"],
            "steps": to_jsonable(simulation.steps),
            "energy": to_jsonable(simulation.energy),
            "coverage": to_jsonable(simulation.coverage),
            "final_positions": to_jsonable(simulation.final_positions),
        }}
    raise ValueError(f"Unsupported calculation kind: {kind}")


def integrate_completed_job(
    job_id: str,
    candidates: pd.DataFrame,
    *,
    experiments: pd.DataFrame | None = None,
    root: str | Path = "work/jobs",
    model_root: str | Path = "work/models",
    top_n: int = 12,
) -> IntegrationResult | None:
    """Persist one completed job, retrain the model, and mark it integrated."""
    record = get_job_status(job_id, root=root)
    metadata = record.metadata or {}
    if metadata.get("integrated_at"):
        return None
    if record.status != "completed":
        raise RuntimeError(f"Job {job_id} is {record.status}, not completed")
    candidate_id = str(metadata.get("candidate_id") or "")
    if not candidate_id:
        raise ValueError(f"Job {job_id} is not bound to a candidate")
    candidate_id, formulation_id = _require_matching_formulation(
        candidates, candidate_id, metadata.get("formulation_id"),
    )

    latest = load_latest_simulation_results(
        [candidate_id], formulation_ids={candidate_id: formulation_id},
    ).get(candidate_id, {})
    if any(
        isinstance(latest.get(component), Mapping) and latest[component].get("job_id") == job_id
        for component in ("dft", "md", "interface")
    ):
        update_job_metadata(job_id, {"integrated_at": datetime.now(timezone.utc).isoformat()}, root=root)
        return None

    parsed = parse_job_result(job_id, root=root)
    addition = annotate_payload(
        calculation_payload(record, parsed, root=root),
        build_provenance(
            metadata,
            source="campaign" if metadata.get("campaign_run_id") else "standalone",
            result_id=record.job_id,
            completed_at=record.finished_at,
        ),
    )
    cumulative = merge_external_payloads(latest, addition)
    updated_candidates = apply_external_results(candidates, {candidate_id: cumulative})
    formulations = dict(zip(
        updated_candidates["candidate_id"].astype(str), updated_candidates["formulation_id"].astype(str),
    ))
    history = experiments if experiments is not None else load_experiments(
        updated_candidates["candidate_id"].astype(str).tolist(), formulation_ids=formulations,
    )
    shortlist, model = screen_candidates(
        updated_candidates,
        experiments=history if history is not None and not history.empty else None,
        top_n=top_n,
        minimum_class="C",
        version="external-v1",
    )
    predictions = predict_screening(model, updated_candidates)
    row = predictions.loc[predictions["candidate_id"].astype(str) == candidate_id].iloc[0].to_dict()
    save_simulation(row, cumulative["dft"], cumulative["md"], cumulative["interface"], model.version)
    artifact = save_model(model, Path(model_root) / f"{model.version}.npz")
    save_model_version(model, str(artifact))
    integrated_at = datetime.now(timezone.utc).isoformat()
    update_job_metadata(
        job_id,
        {"integrated_at": integrated_at, "integrated_model_version": model.version},
        root=root,
    )
    return IntegrationResult(job_id, candidate_id, updated_candidates, shortlist, model, cumulative)
