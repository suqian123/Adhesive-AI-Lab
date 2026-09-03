from adhesive_ai import build_candidate_library


def test_candidate_library_has_core_material_axes():
    frame = build_candidate_library(max_records=18, seed=3)
    assert len(frame) == 18
    assert {"resin", "resin_variant", "dynamic_unit", "curing_agent", "filler_type", "structure_params", "process_conditions", "performance_targets"}.issubset(frame.columns)
    assert {"thermal_resistance_index", "low_temp_toughness_index", "adhesion_strength_mpa", "self_healing_efficiency_pct", "space_environment_stability_index"}.issubset(frame.columns)
    assert set(frame["screening_class"]).issubset({"A", "B", "C", "D"})


def test_candidate_library_assigns_stable_formula_fingerprints():
    first = build_candidate_library(max_records=18, seed=3)
    second = build_candidate_library(max_records=18, seed=3)

    assert first["candidate_library_version"].eq("candidate-library-v3").all()
    assert first["formulation_id"].str.startswith("FMT-").all()
    assert first["formulation_id"].is_unique
    assert first["formulation_id"].tolist() == second["formulation_id"].tolist()
    assert first["formulation_contract"].map(type).eq(dict).all()
