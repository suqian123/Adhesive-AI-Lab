"""Merge a backed-up project database without deleting destination history."""
from __future__ import annotations

import argparse
from datetime import datetime
from decimal import Decimal
import hashlib
import json
import math
from pathlib import Path
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
TABLE_KEYS = {
    "candidates": "candidate_id",
    "literature_candidates": "candidate_id",
    "literature_results": "id",
    "experimental_results": "id",
    "simulation_results": "id",
    "model_versions": "model_version",
}
JSON_COLUMNS = {"properties", "descriptors", "conditions", "metadata", "qchem", "md", "interface_data", "predictions"}


def decode_tagged(value):
    if isinstance(value, dict) and set(value) == {"__decimal__"}:
        return Decimal(value["__decimal__"])
    if isinstance(value, dict) and set(value) == {"__datetime__"}:
        return datetime.fromisoformat(value["__datetime__"])
    return value


def equal_json(expected, actual):
    """Allow only floating-point roundoff in MySQL's binary JSON conversion."""
    if isinstance(expected, dict) and isinstance(actual, dict):
        return expected.keys() == actual.keys() and all(equal_json(value, actual[key]) for key, value in expected.items())
    if isinstance(expected, list) and isinstance(actual, list):
        return len(expected) == len(actual) and all(equal_json(a, b) for a, b in zip(expected, actual))
    if isinstance(expected, float) and isinstance(actual, float):
        return math.isclose(expected, actual, rel_tol=2e-15, abs_tol=0.0)
    return expected == actual


def equal_row(expected, actual):
    for column, value in expected.items():
        other = actual.get(column)
        if column in JSON_COLUMNS:
            value = json.loads(value) if isinstance(value, str) else value
            other = json.loads(other) if isinstance(other, str) else other
            if not equal_json(value, other):
                return False
            continue
        elif isinstance(value, str) and isinstance(other, datetime):
            value = datetime.fromisoformat(value)
        if value != other:
            return False
    return True


def merge_snapshot(connection, snapshot, *, apply=False):
    """Check identities before applying all changes in one transaction."""
    unknown = set(snapshot["tables"]) - set(TABLE_KEYS)
    if unknown:
        raise ValueError(f"Unsupported tables: {sorted(unknown)}")
    cursor = connection.cursor(dictionary=True)
    report = {}
    try:
        connection.start_transaction()
        for table, key in TABLE_KEYS.items():
            source = snapshot["tables"].get(table, [])
            cursor.execute(f"SHOW COLUMNS FROM `{table}`")
            columns = {row["Field"] for row in cursor.fetchall()}
            cursor.execute(f"SELECT * FROM `{table}` FOR UPDATE")
            existing = {row[key]: row for row in cursor.fetchall()}
            actions = []
            seen = set()
            for row in source:
                if not set(row) <= columns:
                    raise ValueError(f"Unknown columns in {table}")
                identity = row[key]
                if identity in seen:
                    raise ValueError(f"Duplicate source identity in {table}: {identity}")
                seen.add(identity)
                previous = existing.get(identity)
                if previous is not None:
                    if equal_row(row, previous):
                        continue
                    if table != "candidates" or row.get("formulation_id") != previous.get("formulation_id"):
                        raise ValueError(f"Conflicting destination identity in {table}: {identity}")
                actions.append((row, previous is not None))
            report[table] = {"source": len(source), "before": len(existing),
                             "inserted": sum(not update for _, update in actions),
                             "updated": sum(update for _, update in actions),
                             "unchanged": len(source) - len(actions)}
            if apply:
                for row, update in actions:
                    fields = list(row)
                    if update:
                        fields.remove(key)
                        sql = f"UPDATE `{table}` SET " + ",".join(f"`{column}`=%s" for column in fields) + f" WHERE `{key}`=%s"
                        values = tuple(row[column] for column in fields) + (row[key],)
                    else:
                        sql = f"INSERT INTO `{table}` (" + ",".join(f"`{column}`" for column in fields) + ") VALUES (" + ",".join("%s" for _ in fields) + ")"
                        values = tuple(row[column] for column in fields)
                    cursor.execute(sql, values)
                cursor.execute(f"SELECT * FROM `{table}`")
                result = {row[key]: row for row in cursor.fetchall()}
                for row in source:
                    actual = result.get(row[key], {})
                    if not equal_row(row, actual):
                        differences = [column for column in row if not equal_row({column: row[column]}, actual)]
                        raise ValueError(f"Post-import content mismatch in {table}, identity={row[key]}, columns={differences}")
                report[table]["after"] = len(result)
        if apply:
            connection.commit()
        else:
            connection.rollback()
        return report
    except Exception:
        connection.rollback()
        raise
    finally:
        cursor.close()


def install_artifacts(package, manifest):
    """Install only hash-checked model files inside the project model directory."""
    for name, digest in manifest["files"].items():
        destination = (ROOT / name).resolve()
        if not destination.is_relative_to((ROOT / "work/models").resolve()) or destination.suffix != ".npz":
            raise ValueError("Unexpected model artifact path")
        content = package.read(name)
        if hashlib.sha256(content).hexdigest() != digest:
            raise ValueError("Model artifact hash mismatch")
        if destination.exists() and hashlib.sha256(destination.read_bytes()).hexdigest() != digest:
            raise ValueError(f"Existing model artifact conflict: {name}")
    for name in manifest["files"]:
        destination = ROOT / name
        destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if not destination.exists():
            destination.write_bytes(package.read(name))
            destination.chmod(0o600)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("package", type=Path)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--report", type=Path, required=True)
    arguments = parser.parse_args()
    sys.path.insert(0, str(ROOT / "src"))
    from adhesive_ai.database import config_from_env
    import mysql.connector
    with zipfile.ZipFile(arguments.package) as package:
        snapshot = json.loads(package.read("snapshot.json"), object_hook=decode_tagged)
        manifest = json.loads(package.read("manifest.json"))
        connection = mysql.connector.connect(**config_from_env())
        try:
            preflight = merge_snapshot(connection, snapshot)
            if arguments.apply:
                install_artifacts(package, manifest)
                report = merge_snapshot(connection, snapshot, apply=True)
            else:
                report = preflight
        finally:
            connection.close()
    result = {"applied": arguments.apply, "tables": report, "artifact_files": len(manifest["files"]),
              "missing_artifacts": manifest["missing_artifacts"]}
    arguments.report.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    arguments.report.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    arguments.report.chmod(0o600)
    print(json.dumps(result))


if __name__ == "__main__":
    main()
