import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location("database_migration", Path(__file__).resolve().parents[1] / "scripts/migrate_database_snapshot.py")
migration = importlib.util.module_from_spec(spec)
spec.loader.exec_module(migration)


def test_mysql_json_roundoff_preserves_model_metrics():
    source = {"metadata": '{"validation_metrics":{"r2":0.9284861232644855}}'}
    stored = {"metadata": '{"validation_metrics":{"r2":0.9284861232644857}}'}
    assert migration.equal_row(source, stored)
    changed = {"metadata": '{"validation_metrics":{"r2":0.928486123265}}'}
    assert not migration.equal_row(source, changed)


def test_json_comparison_rejects_missing_or_changed_identity_data():
    source = {"properties": '{"formulation":"FMT-001","values":[1,2]}' }
    assert not migration.equal_row(source, {"properties": '{"formulation":"FMT-002","values":[1,2]}'})
    assert not migration.equal_row(source, {"properties": '{"formulation":"FMT-001"}'})
