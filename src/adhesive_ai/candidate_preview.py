from __future__ import annotations

import pandas as pd


ADHESION_SORT_ASCENDING = "按 25 °C 基准黏附强度升序"
ADHESION_SORT_DESCENDING = "按 25 °C 基准黏附强度降序"
CANDIDATE_ID_SORT = "按候选编号"


def order_candidate_preview(frame: pd.DataFrame, order: str) -> pd.DataFrame:
    """Order the candidate preview using the numeric adhesion value, not display text."""
    ordered = frame.copy()
    if order == CANDIDATE_ID_SORT:
        return ordered.sort_values("candidate_id", kind="stable").reset_index(drop=True)

    strength = pd.to_numeric(ordered.get("adhesion_reference_strength_mpa"), errors="coerce")
    ordered = ordered.assign(_adhesion_numeric_sort_key=strength)
    ascending = order != ADHESION_SORT_DESCENDING
    return (
        ordered.sort_values(
            ["_adhesion_numeric_sort_key", "candidate_id"],
            ascending=[ascending, True],
            na_position="last",
            kind="stable",
        )
        .drop(columns="_adhesion_numeric_sort_key")
        .reset_index(drop=True)
    )
