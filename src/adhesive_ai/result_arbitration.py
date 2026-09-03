"""Deterministic selection of concurrent external-calculation results."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import json
from typing import Any, Mapping


COMPONENTS = ("dft", "md", "interface")
ARBITRATION_KEY = "_result_arbitration"
HISTORY_KEY = "_result_history"
CONDITION_KEYS = (
    "facet",
    "oxygen_vacancy_fraction",
    "hydroxyl_fraction",
    "temperature_c",
    "temperatures_c",
    "area_nm2",
    "pressure",
    "ensemble",
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _conditions(metadata: Mapping[str, Any]) -> dict[str, Any]:
    return {
        name: metadata[name]
        for name in CONDITION_KEYS
        if metadata.get(name) is not None
    }


def _tier(metadata: Mapping[str, Any]) -> tuple[int, str]:
    validation = str(metadata.get("input_validation") or "").lower()
    approved = bool(metadata.get("production_approved")) or any(
        marker in validation
        for marker in ("convergence-approved", "md-production-approved", "production-approved")
    )
    if approved:
        return 3, "approved-production"
    if validation.startswith("static-valid") or bool(metadata.get("input_static_validated")):
        return 2, "validated-input"
    return 1, "unapproved-manual"


def build_provenance(
    metadata: Mapping[str, Any],
    *,
    source: str,
    result_id: str,
    completed_at: str | None,
) -> dict[str, Any]:
    """Describe the evidence and comparison key for one result payload."""
    rank, tier = _tier(metadata)
    conditions = _conditions(metadata)
    return {
        "tier": tier,
        "rank": rank,
        "source": source,
        "result_id": result_id,
        "completed_at": completed_at or _now(),
        "conditions": conditions,
        "condition_signature": json.dumps(conditions, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
    }


def annotate_payload(
    payload: Mapping[str, Mapping[str, Any]],
    provenance: Mapping[str, Any],
) -> dict[str, dict[str, Any]]:
    """Attach one immutable provenance record to each populated result component."""
    annotated: dict[str, dict[str, Any]] = {}
    for component in COMPONENTS:
        value = payload.get(component)
        if not isinstance(value, Mapping) or not value:
            annotated[component] = {}
            continue
        annotated[component] = {
            **deepcopy(dict(value)),
            ARBITRATION_KEY: deepcopy(dict(provenance)),
        }
    return annotated


def _without_history(value: Mapping[str, Any]) -> dict[str, Any]:
    item = deepcopy(dict(value))
    item.pop(HISTORY_KEY, None)
    return item


def _identity(value: Mapping[str, Any]) -> tuple[str, str]:
    provenance = value.get(ARBITRATION_KEY)
    if not isinstance(provenance, Mapping):
        return "", ""
    return str(provenance.get("result_id") or ""), str(provenance.get("completed_at") or "")


def _history(value: Mapping[str, Any]) -> list[dict[str, Any]]:
    recorded = value.get(HISTORY_KEY)
    return [deepcopy(dict(item)) for item in recorded if isinstance(item, Mapping)] if isinstance(recorded, list) else []


def _append_unique(history: list[dict[str, Any]], value: Mapping[str, Any]) -> list[dict[str, Any]]:
    entry = _without_history(value)
    identity = _identity(entry)
    if identity != ("", "") and any(_identity(item) == identity for item in history):
        return history
    return [*history, entry]


def _incoming_wins(current: Mapping[str, Any], incoming: Mapping[str, Any]) -> bool:
    current_meta = current.get(ARBITRATION_KEY)
    incoming_meta = incoming.get(ARBITRATION_KEY)
    if not isinstance(incoming_meta, Mapping):
        return False
    if not isinstance(current_meta, Mapping):
        return True
    current_rank = int(current_meta.get("rank") or 0)
    incoming_rank = int(incoming_meta.get("rank") or 0)
    if incoming_rank != current_rank:
        return incoming_rank > current_rank

    # Different conditions are parallel observations, not interchangeable data.
    if incoming_meta.get("condition_signature") != current_meta.get("condition_signature"):
        return False
    return str(incoming_meta.get("completed_at") or "") >= str(current_meta.get("completed_at") or "")


def merge_external_payloads(
    current: Mapping[str, Mapping[str, Any]] | None,
    incoming: Mapping[str, Mapping[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Merge a completed result without letting completion order override evidence quality."""
    merged: dict[str, dict[str, Any]] = {}
    current = current or {}
    for component in COMPONENTS:
        existing = current.get(component)
        candidate = incoming.get(component)
        if not isinstance(candidate, Mapping) or not candidate:
            merged[component] = deepcopy(dict(existing)) if isinstance(existing, Mapping) else {}
            continue
        if not isinstance(existing, Mapping) or not existing:
            merged[component] = deepcopy(dict(candidate))
            continue

        history = _history(existing)
        if _incoming_wins(existing, candidate):
            history = _append_unique(history, existing)
            selected = deepcopy(dict(candidate))
        else:
            history = _append_unique(history, candidate)
            selected = deepcopy(dict(existing))
        if history:
            selected[HISTORY_KEY] = history
        merged[component] = selected
    return merged
