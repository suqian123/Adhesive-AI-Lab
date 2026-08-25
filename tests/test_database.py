import pytest
import numpy as np

import adhesive_ai.database as database
from adhesive_ai.engines import MDObservables


def test_database_module_imports_without_mysql_driver():
    assert hasattr(database, "config_from_env")


def test_connection_errors_when_mysql_driver_missing(monkeypatch):
    monkeypatch.setattr(database, "mysql_connector", None)
    with pytest.raises(database.DatabaseError):
        with database.connection():
            pass


def test_database_jsonable_handles_md_dataclasses_and_numpy():
    value = database.jsonable({"md": MDObservables(300, .1, 500, 3, 45, (0, 25), (100, 101)), "array": np.array([1, 2])})
    assert value["md"]["glass_transition_c"] == 300
    assert value["array"] == [1, 2]


def test_experiment_and_model_persistence_fall_back_to_sqlite(monkeypatch, tmp_path):
    monkeypatch.setattr(database, "mysql_connector", None)
    monkeypatch.setenv("ADHESIVE_SQLITE_PATH", str(tmp_path / "lab.sqlite3"))
    database.save_experiment("CL-00001", {"wide_temp_adhesion_mpa": 28.5}, test_batch="batch-1", temperature_c=25)
    stored = database.load_experiments(["CL-00001"])
    assert len(stored) == 1 and stored.iloc[0].wide_temp_adhesion_mpa == 28.5
