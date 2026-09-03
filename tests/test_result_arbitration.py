from adhesive_ai.result_arbitration import (
    ARBITRATION_KEY,
    HISTORY_KEY,
    annotate_payload,
    build_provenance,
    merge_external_payloads,
)


def _payload(*, result_id, rank, completed_at, conditions):
    tier = {1: "unapproved-manual", 2: "validated-input", 3: "approved-production"}[rank]
    return {
        "dft": {
            "job_id": result_id,
            "adsorption_energy_ev": -float(rank),
            ARBITRATION_KEY: {
                "tier": tier,
                "rank": rank,
                "source": "standalone",
                "result_id": result_id,
                "completed_at": completed_at,
                "conditions": conditions,
                "condition_signature": str(sorted(conditions.items())),
            },
        },
    }


def test_approved_result_beats_later_unapproved_manual_result():
    approved = _payload(
        result_id="approved", rank=3, completed_at="2026-01-01T00:00:00+00:00", conditions={"facet": "(111)"},
    )
    manual = _payload(
        result_id="manual", rank=1, completed_at="2026-02-01T00:00:00+00:00", conditions={"facet": "(111)"},
    )

    merged = merge_external_payloads(approved, manual)

    assert merged["dft"]["job_id"] == "approved"
    assert merged["dft"][HISTORY_KEY][0]["job_id"] == "manual"


def test_same_tier_and_same_conditions_uses_newer_completed_result():
    first = _payload(
        result_id="first", rank=2, completed_at="2026-01-01T00:00:00+00:00", conditions={"temperature_c": 25.0},
    )
    newer = _payload(
        result_id="newer", rank=2, completed_at="2026-01-02T00:00:00+00:00", conditions={"temperature_c": 25.0},
    )

    merged = merge_external_payloads(first, newer)

    assert merged["dft"]["job_id"] == "newer"
    assert merged["dft"][HISTORY_KEY][0]["job_id"] == "first"


def test_same_tier_different_conditions_stay_as_parallel_history():
    first = _payload(
        result_id="111", rank=2, completed_at="2026-01-01T00:00:00+00:00", conditions={"facet": "(111)"},
    )
    different_condition = _payload(
        result_id="110", rank=2, completed_at="2026-02-01T00:00:00+00:00", conditions={"facet": "(110)"},
    )

    merged = merge_external_payloads(first, different_condition)

    assert merged["dft"]["job_id"] == "111"
    assert merged["dft"][HISTORY_KEY][0]["job_id"] == "110"


def test_provenance_marks_static_input_as_validated_and_approved_input_as_production():
    validated = build_provenance(
        {"input_validation": "static-valid; pending-convergence-approval", "facet": "(111)"},
        source="standalone", result_id="validated", completed_at="2026-01-01T00:00:00+00:00",
    )
    approved = build_provenance(
        {"input_validation": "static-valid; convergence-approved", "facet": "(111)"},
        source="campaign", result_id="approved", completed_at="2026-01-01T00:00:00+00:00",
    )
    payload = annotate_payload({"dft": {"job_id": "approved"}}, approved)

    assert validated["rank"] == 2
    assert approved["rank"] == 3
    assert payload["dft"][ARBITRATION_KEY]["tier"] == "approved-production"
