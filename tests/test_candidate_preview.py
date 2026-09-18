import pandas as pd

from adhesive_ai.candidate_preview import (
    ADHESION_SORT_ASCENDING,
    ADHESION_SORT_DESCENDING,
    CANDIDATE_ID_SORT,
    order_candidate_preview,
)


def test_candidate_preview_orders_combined_display_values_by_numeric_strength():
    frame = pd.DataFrame(
        {
            "candidate_id": ["CL-3", "CL-1", "CL-2"],
            "adhesion_reference_strength_mpa": [17.2, 9.86, 11.4],
            "adhesion_reference_summary": [
                "17.20｜铝合金 6061-T6",
                "9.86｜铝合金 6061-T6",
                "11.40｜铝合金 6061-T6",
            ],
        }
    )

    assert order_candidate_preview(frame, ADHESION_SORT_ASCENDING)["candidate_id"].tolist() == ["CL-1", "CL-2", "CL-3"]
    assert order_candidate_preview(frame, ADHESION_SORT_DESCENDING)["candidate_id"].tolist() == ["CL-3", "CL-2", "CL-1"]
    assert order_candidate_preview(frame, CANDIDATE_ID_SORT)["candidate_id"].tolist() == ["CL-1", "CL-2", "CL-3"]
