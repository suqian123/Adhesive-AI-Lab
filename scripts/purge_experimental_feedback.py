"""Back up and remove every persisted experimental-feedback row.

Only ``experimental_results`` is affected. Candidate definitions, simulation
results, and model-version metadata are intentionally retained. The default is
read-only; pass ``--apply`` to create backups and delete the rows.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3


ROOT = Path(__file__).resolve().parents[1]
SQLITE_DATABASES = (
    ROOT / "work" / "adhesive_ai_lab.sqlite3",
    ROOT / "outputs" / "experiments.sqlite",
)


def inspect_sqlite(path: Path) -> dict[str, object]:
    with sqlite3.connect(path) as connection:
        exists = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='experimental_results'"
        ).fetchone() is not None
        if not exists:
            return {"database": str(path), "table_exists": False, "row_count": 0, "samples": []}
        rows = connection.execute(
            "SELECT id,candidate_id,test_batch,source,created_at FROM experimental_results ORDER BY id LIMIT 20"
        ).fetchall()
        return {
            "database": str(path),
            "table_exists": True,
            "row_count": connection.execute("SELECT COUNT(*) FROM experimental_results").fetchone()[0],
            "samples": [dict(zip(("id", "candidate_id", "test_batch", "source", "created_at"), row)) for row in rows],
        }


def purge_sqlite(path: Path, backup_directory: Path, timestamp: str) -> dict[str, object]:
    report = inspect_sqlite(path)
    if not report["table_exists"] or not report["row_count"]:
        return {**report, "deleted": 0, "backup": None}
    backup_directory.mkdir(parents=True, exist_ok=True)
    backup = backup_directory / f"{path.stem}-before-experimental-purge-{timestamp}.sqlite"
    with sqlite3.connect(path) as source, sqlite3.connect(backup) as destination:
        source.backup(destination)
        cursor = source.execute("DELETE FROM experimental_results")
        source.commit()
        remaining = source.execute("SELECT COUNT(*) FROM experimental_results").fetchone()[0]
    if remaining:
        raise RuntimeError(f"Deletion verification failed for {path}: {remaining} rows remain")
    return {**report, "deleted": cursor.rowcount, "backup": str(backup)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Back up and delete every experimental_results row")
    arguments = parser.parse_args()
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_directory = ROOT / "work" / "experimental_backups"
    reports = []
    for path in SQLITE_DATABASES:
        if not path.is_file():
            reports.append({"database": str(path), "exists": False, "row_count": 0, "deleted": 0})
        elif arguments.apply:
            reports.append(purge_sqlite(path, backup_directory, timestamp))
        else:
            reports.append(inspect_sqlite(path))
    print(json.dumps({"applied": arguments.apply, "databases": reports}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
