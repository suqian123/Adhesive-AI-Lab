import json

from adhesive_ai import build_candidate_library
from adhesive_ai.campaign import (
    build_multiscale_campaign,
    campaign_task_frame,
    validate_candidate_contract,
    write_multiscale_campaign,
)


def test_candidate_contract_and_multiscale_task_matrix_are_complete():
    candidates = build_candidate_library(max_records=12, seed=5)
    report = validate_candidate_contract(candidates)
    campaign = build_multiscale_campaign(candidates.iloc[0])
    tasks = campaign_task_frame(campaign)

    assert report == {
        "valid": True,
        "rows": 12,
        "missing_columns": [],
        "null_counts": {},
        "feature_count": 12,
        "output_count": 5,
    }
    assert len(campaign.tasks) == 26
    assert set(tasks.scale) == {"quantum", "atomistic-md", "coarse-grained"}
    assert any(task.conditions.get("temperatures_c", ())[0] == -180 for task in campaign.tasks if task.scale == "atomistic-md")
    assert candidates.iloc[0].data_source == "physics-informed-proxy"


def test_campaign_package_contains_manifests_and_runnable_cg_starting_model(tmp_path):
    candidate = build_candidate_library(max_records=1, seed=8).iloc[0]
    campaign = build_multiscale_campaign(candidate)

    paths = write_multiscale_campaign(campaign, tmp_path / "campaigns")
    manifest = json.loads(paths["campaign"].read_text(encoding="utf-8"))

    assert paths["cg_data"].exists() and paths["cg_input"].exists()
    assert manifest["candidate_id"] == candidate.candidate_id
    assert len(manifest["tasks"]) == 26
    assert "requires-calibration" in next(task["readiness"] for task in manifest["tasks"] if task["scale"] == "coarse-grained")

