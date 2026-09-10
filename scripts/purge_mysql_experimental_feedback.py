"""Back up and remove all MySQL experimental-feedback rows.

The default is read-only. ``--apply`` writes a JSON backup under
``work/experimental_backups`` and clears only ``experimental_results``.
Candidates, simulation_results, and model_versions are never modified.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from adhesive_ai.database import config_from_env, connection


def inspect() -> dict[str, object]:
    config = config_from_env()
    with connection() as database:
        cursor = database.cursor(dictionary=True)
        try:
            cursor.execute("SELECT id,candidate_id,formulation_id,candidate_library_version,test_batch,test_temperature_c,properties,source,created_at FROM experimental_results ORDER BY id")
            rows = cursor.fetchall()
        finally:
            cursor.close()
    return {
        "database": {"host": config["host"], "port": config["port"], "name": config["database"], "user": config["user"]},
        "row_count": len(rows),
        "rows": rows,
    }


def purge() -> dict[str, object]:
    report = inspect()
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_directory = ROOT / "work" / "experimental_backups"
    backup_directory.mkdir(parents=True, exist_ok=True)
    backup = backup_directory / f"mysql-experimental-results-before-purge-{timestamp}.json"
    backup.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    with connection() as database:
        cursor = database.cursor()
        try:
            cursor.execute("DELETE FROM experimental_results")
            deleted = cursor.rowcount
            cursor.execute("SELECT COUNT(*) FROM experimental_results")
            remaining = int(cursor.fetchone()[0])
        finally:
            cursor.close()
    if remaining:
        raise RuntimeError(f"Deletion verification failed: {remaining} experimental rows remain")
    return {"database": report["database"], "deleted": deleted, "remaining": remaining, "backup": str(backup)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Back up and delete all MySQL experimental-results rows")
    arguments = parser.parse_args()
    result = purge() if arguments.apply else inspect()
    print(json.dumps({"applied": arguments.apply, **result}, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
