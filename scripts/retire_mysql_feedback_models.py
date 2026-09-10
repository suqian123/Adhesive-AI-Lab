"""Retire MySQL model versions trained on experimental-feedback rows.

The default is read-only.  ``--apply`` backs up the affected database records,
moves their local artifacts to a timestamped recovery directory, and deletes
only model-version rows whose metadata records ``experimental_rows > 0``.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from adhesive_ai.database import connection


def affected_versions() -> list[dict[str, object]]:
    with connection() as database:
        cursor = database.cursor(dictionary=True)
        try:
            cursor.execute("SELECT model_version, metadata, artifact_path, created_at FROM model_versions ORDER BY model_version")
            records = cursor.fetchall()
        finally:
            cursor.close()
    affected: list[dict[str, object]] = []
    for record in records:
        metadata = json.loads(record["metadata"])
        if int(metadata.get("experimental_rows", 0)) > 0:
            affected.append(record)
    return affected


def artifact_source(record: dict[str, object]) -> Path | None:
    value = record.get("artifact_path")
    if not value:
        return None
    source = (ROOT / str(value)).resolve()
    models_root = (ROOT / "work" / "models").resolve()
    if source != models_root and models_root not in source.parents:
        raise RuntimeError(f"Refusing to move artifact outside work/models: {source}")
    return source


def inspect() -> dict[str, object]:
    records = affected_versions()
    return {
        "affected_count": len(records),
        "versions": [
            {
                "model_version": record["model_version"],
                "artifact_path": record["artifact_path"],
                "artifact_exists": bool((source := artifact_source(record)) and source.is_file()),
                "created_at": record["created_at"],
            }
            for record in records
        ],
    }


def retire() -> dict[str, object]:
    records = affected_versions()
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_root = ROOT / "work" / "experimental_backups" / f"mysql-feedback-models-{timestamp}"
    backup_root.mkdir(parents=True, exist_ok=False)
    record_backup = backup_root / "model_versions.json"
    record_backup.write_text(json.dumps(records, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")

    moved: list[str] = []
    artifacts_root = backup_root / "artifacts"
    for record in records:
        source = artifact_source(record)
        if source and source.is_file():
            artifacts_root.mkdir(exist_ok=True)
            destination = artifacts_root / source.name
            if destination.exists():
                raise RuntimeError(f"Backup destination already exists: {destination}")
            shutil.move(str(source), str(destination))
            moved.append(str(destination))

    with connection() as database:
        cursor = database.cursor()
        try:
            for record in records:
                cursor.execute("DELETE FROM model_versions WHERE model_version = %s", (record["model_version"],))
            deleted = cursor.rowcount if len(records) == 1 else len(records)
            cursor.execute("SELECT model_version, metadata FROM model_versions")
            remaining = [
                row[0] for row in cursor.fetchall()
                if int(json.loads(row[1]).get("experimental_rows", 0)) > 0
            ]
        finally:
            cursor.close()
    if remaining:
        raise RuntimeError(f"Retirement verification failed; affected versions remain: {remaining}")
    return {"deleted": deleted, "remaining_affected": remaining, "backup": str(backup_root), "moved_artifacts": moved}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Back up and retire feedback-trained model versions")
    arguments = parser.parse_args()
    print(json.dumps({"applied": arguments.apply, **(retire() if arguments.apply else inspect())}, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
