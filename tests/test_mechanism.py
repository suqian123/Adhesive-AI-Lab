import pandas as pd

from adhesive_ai import build_candidate_library
from adhesive_ai.mechanism import fuse_candidate_mechanism, mechanism_provenance_frame


def test_candidate_mechanism_keeps_missing_values_explicitly_proxy():
    candidate = build_candidate_library(max_records=1, seed=3).iloc[0]

    result = fuse_candidate_mechanism(candidate)

    assert result["readiness"] == {"dft": False, "md": False, "interface": False, "experiment": False}
    assert result["provenance"]["quantum.oxygen_adsorption_ev"] == "physics-informed-proxy"
    assert result["provenance"]["performance.wide_temp_adhesion_mpa"] == "physics-informed-proxy"
    assert result["source_summary"]["external"] == 0


def test_candidate_mechanism_fuses_experiment_external_and_hybrid_profiles():
    candidate = build_candidate_library(max_records=1, seed=4).iloc[0]
    candidate_id = str(candidate.candidate_id)
    calculations = {
        "model_version": "external-v1",
        "dft": {
            "job_id": "dft-7", "facet": "(110)", "oxygen_vacancy_fraction": 0.15,
            "adsorption_energy_ev": -1.72, "reaction_barrier_ev": 0.43, "ce3_fraction": 0.31,
        },
        "md": {
            "job_id": "md-8", "glass_transition_c": 402.0,
            "free_volume_fraction": 0.115, "elastic_modulus_gpa": 4.2,
        },
        "interface": {
            "job_id": "interface-9", "binding_energy_mj_m2": 168.0,
            "adhesion_work_mj_m2": 142.0, "steps": [0, 100], "energy": [-1.0, -2.0],
        },
        "predictions": {"uv_retention_pct": 88.0},
    }
    experiments = pd.DataFrame([{
        "candidate_id": candidate_id, "test_batch": "batch-real", "measured_tg_c": 417.0,
        "wide_temp_adhesion_mpa": 31.4, "healing_efficiency_pct": 84.0,
    }])

    result = fuse_candidate_mechanism(candidate, calculations=calculations, experiments=experiments)
    provenance = mechanism_provenance_frame(result)

    assert result["readiness"] == {"dft": True, "md": True, "interface": True, "experiment": True}
    assert result["conditions"]["facet"] == "(110)"
    assert result["quantum"]["oxygen_adsorption_ev"] == -1.72
    assert result["md"]["glass_transition_c"] == 417.0
    assert result["provenance"]["md.glass_transition_c"] == "experiment:batch-real"
    assert result["provenance"]["md.free_volume_fraction"] == "external:md-8+proxy-temperature-shape"
    assert result["interface"]["binding_energy_mj_m2"] == 168.0
    assert result["performance"]["wide_temp_adhesion_mpa"] == 31.4
    assert result["provenance"]["performance.uv_retention_pct"] == "model:external-v1"
    assert result["trajectory"]["available"] is True
    assert {"section", "metric", "value", "source"}.issubset(provenance.columns)
