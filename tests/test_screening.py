import pandas as pd

from adhesive_ai import build_candidate_library
from adhesive_ai.screening import OUTPUT_COLUMNS, _with_experiments, load_model, predict_screening, recommend_next_experiments, save_model, screen_candidates, train_screening_models, update_with_experiments


def test_regression_classification_and_experiment_update():
    candidates = build_candidate_library(max_records=36, seed=11)
    model = train_screening_models(candidates)
    assert len([name for name in model.feature_names if name.startswith("functional_group_")]) == 5
    assert model.data_provenance == {
        "candidate_rows": 36, "proxy_rows": 36, "external_rows": 0, "experimental_rows": 0, "literature_rows": 0,
    }
    predictions = predict_screening(model, candidates)
    assert set(f"predicted_{name}" for name in OUTPUT_COLUMNS).issubset(predictions.columns)
    assert predictions["predicted_screening_class"].isin(["A", "B", "C", "D"]).all()
    selected, _ = screen_candidates(candidates, top_n=5, minimum_class="C")
    assert len(selected) == 5
    measured = pd.DataFrame([{"candidate_id": candidates.iloc[0].candidate_id, "wide_temp_adhesion_mpa": 31.2, "healing_efficiency_pct": 88.0, "atomic_oxygen_retention_pct": 91.0, "uv_retention_pct": 86.0, "am_feasibility": 74.0}])
    updated = update_with_experiments(candidates, measured, previous_model=model)
    assert updated.experimental_rows == 1
    assert updated.version.endswith("+exp")


def test_experimental_feature_mapping_validation_persistence_and_recommendation(tmp_path):
    candidates = build_candidate_library(max_records=24, seed=4)
    experiment = pd.DataFrame([{
        "candidate_id": candidates.iloc[0].candidate_id, "measured_tg_c": 410.0,
        "measured_modulus_gpa": 3.5, "measured_cte_ppm_k": 48.0,
        "wide_temp_adhesion_mpa": 29.0, "healing_efficiency_pct": 82.0,
    }])
    model = train_screening_models(candidates, experiment)
    assert model.experimental_rows == 1
    assert "mean_rmse_scaled" in model.validation_metrics
    path = save_model(model, tmp_path / "models" / "screening.npz")
    restored = load_model(path)
    assert restored.version == model.version
    assert restored.data_provenance == model.data_provenance
    batch = recommend_next_experiments(restored, candidates, tested_ids=[candidates.iloc[0].candidate_id], batch_size=4)
    assert len(batch) == 4
    assert candidates.iloc[0].candidate_id not in set(batch.candidate_id)


def test_duplicate_experiment_rows_keep_latest_measurement_during_calibration():
    candidates = build_candidate_library(max_records=24, seed=9)
    candidate_id = candidates.iloc[0].candidate_id
    experiments = pd.DataFrame([
        {"candidate_id": candidate_id, "wide_temp_adhesion_mpa": 20.0},
        {"candidate_id": candidate_id, "wide_temp_adhesion_mpa": 32.0},
    ])

    selected, model = screen_candidates(candidates, experiments=experiments, top_n=5)

    assert len(selected) == 5
    assert model.experimental_rows == 1
    assert model.correction_bias is not None


def test_non_reference_conditioned_adhesion_does_not_replace_candidate_baseline():
    candidates = build_candidate_library(max_records=12, seed=10)
    candidate_id = candidates.iloc[0].candidate_id
    baseline = candidates.loc[candidates.candidate_id == candidate_id, "wide_temp_adhesion_mpa"].iloc[0]
    experiments = pd.DataFrame([{
        "candidate_id": candidate_id,
        "test_temperature_c": 150.0,
        "substrate_material": "铝合金",
        "substrate_grade": "6061-T6",
        "surface_condition": "阳极氧化",
        "adhesion_condition_record": True,
        "screening_reference_condition": False,
        "wide_temp_adhesion_mpa": 2.0,
    }])

    merged, experimental_rows, literature_rows = _with_experiments(candidates, experiments)

    assert experimental_rows == 1 and literature_rows == 0
    assert merged.loc[merged.candidate_id == candidate_id, "wide_temp_adhesion_mpa"].iloc[0] == baseline


def test_literature_rows_are_tracked_separately_from_experiments(tmp_path):
    candidates = build_candidate_library(max_records=24, seed=5)
    literature = pd.DataFrame([{
        "candidate_id": candidates.iloc[0].candidate_id, "wide_temp_adhesion_mpa": 33.0,
        "healing_efficiency_pct": 80.0, "atomic_oxygen_retention_pct": 88.0,
        "uv_retention_pct": 86.0, "am_feasibility": 75.0,
    }])
    model = train_screening_models(candidates, literature=literature, version="literature-v1")
    assert model.experimental_rows == 0
    assert model.literature_rows == 1
    assert model.data_provenance["literature_rows"] == 1
    assert load_model(save_model(model, tmp_path / "literature-v1.npz")).literature_rows == 1


def test_partial_literature_labels_do_not_block_other_targets():
    candidates = build_candidate_library(max_records=24, seed=6)
    literature_candidate = candidates.iloc[[0]].copy()
    literature_candidate["candidate_id"] = "LIT-EXAMPLE"
    literature_candidate.loc[:, list(OUTPUT_COLUMNS)] = float("nan")
    combined = pd.concat([candidates, literature_candidate], ignore_index=True)
    literature = pd.DataFrame([{"candidate_id": "LIT-EXAMPLE", "healing_efficiency_pct": 87.0}])

    model = train_screening_models(combined, literature=literature)

    assert model.literature_rows == 1
    assert predict_screening(model, combined)["predicted_healing_efficiency_pct"].notna().all()
