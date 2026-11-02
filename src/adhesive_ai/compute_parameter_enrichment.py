"""Evidence-scoped candidate metadata; missing solver parameters remain null."""
from __future__ import annotations

from copy import deepcopy
from typing import Any

BATCH_ID = "compute-parameter-research-20261029-v1"
UNKNOWN_FIELDS = (
    "resin_monomers", "dynamic_component", "toughener_grade", "toughener_Mn_g_mol",
    "toughener_acrylonitrile_wt_pct", "toughener_average_functionality",
    "catalyst_loading", "curing_agent_structure", "molecular_weight_distribution",
    "reaction_conversion", "topology_file", "forcefield_file", "charge_model",
    "partial_charges", "timestep_fs", "nonbonded_cutoff_A", "pressure_atm",
    "equilibration_ps", "production_ps", "exchange_activation_energy_kJ_mol",
    "exchange_rate_per_s", "filler_particle_size_nm", "PDA_coating_thickness_nm",
    "interface_cross_parameters", "dft_functional", "dft_cutoff_eV", "dft_U_eV",
    "dft_kmesh", "validated_density_g_cm3", "validated_Tg_K",
)


def enrich(row: dict[str, Any], sources: dict[str, Any], specific_model: dict[str, Any]) -> dict[str, Any]:
    params = dict.fromkeys(UNKNOWN_FIELDS)
    refs = []
    for label in (row.get("resin"), row.get("dynamic_unit")):
        if label in sources:
            refs.append({"source_key": label, "applicability": "family-reference-only", **sources[label]})
    provenance: dict[str, Any] = {}
    model = None
    if (row.get("candidate_id") == specific_model["candidate_id"]
            and row.get("formulation_id") == specific_model["formulation_id"]
            and row.get("resin") == "CE" and row.get("dynamic_unit") == "Disulfide"):
        model = deepcopy(specific_model)
        model["identity_scope"] = "proposed specialization; existing formulation fingerprint does not encode these newly supplied structures"
        params.update(
            resin_monomers=deepcopy(model["monomers"]),
            dynamic_component="4,4'-dicyanatodiphenyl disulfide",
            toughener_grade="Hypro 1300X13 CTBN",
            toughener_Mn_g_mol=3150,
            toughener_acrylonitrile_wt_pct=26.0,
            toughener_average_functionality=1.8,
            reaction_conversion=[0.798, 0.948, 1.0],
        )
        for name in params:
            if params[name] is not None:
                provenance[name] = {
                    "scope": "proposed-model-only; not validated production input",
                    "source": "user specification in this project, 2026-10-29",
                }
        for name in ("toughener_Mn_g_mol", "toughener_average_functionality"):
            provenance[name].update(source=sources["CTBN1300X13"]["url"], scope="supplier-typical; batch CoA pending")
    return {
        "schema_version": "candidate-compute-parameters-v1", "batch_id": BATCH_ID,
        "candidate_id": row["candidate_id"], "formulation_id": row["formulation_id"],
        "candidate_library_version": row["candidate_library_version"],
        "resin": row["resin"], "dynamic_unit": row["dynamic_unit"],
        "readiness": "incomplete", "production_approved": False,
        "parameters": params, "field_provenance": provenance,
        "reference_routes": refs,
        "solver_documentation": [sources["LAMMPS_UNITS"], sources["LAMMPS_REACTION"]],
        "proposed_model": model,
        "missing_fields": [key for key, value in params.items() if value is None],
        "existing_proxy_values_promoted_to_validated_parameters": False,
    }
