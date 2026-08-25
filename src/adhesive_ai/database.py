"""MySQL persistence for virtual candidates, simulations and experimental feedback."""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from dataclasses import asdict, is_dataclass
from contextlib import contextmanager
from typing import Any, Iterator
from urllib.parse import parse_qs, unquote, urlparse

import pandas as pd
import numpy as np
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[2] / ".env")

try:
    import mysql.connector as mysql_connector
except ModuleNotFoundError:  # pragma: no cover - optional dependency
    mysql_connector = None

# Candidate-id indexes keep lookups fast without requiring the MySQL REFERENCES
# privilege, which is commonly withheld from application service accounts.
SCHEMA = (
    "CREATE TABLE IF NOT EXISTS candidates ("
    "candidate_id VARCHAR(32) PRIMARY KEY, resin VARCHAR(32) NOT NULL, blend_resin VARCHAR(32) NULL, blend_fraction DECIMAL(5,3) NOT NULL, "
    "dynamic_unit VARCHAR(32) NOT NULL, cure_system VARCHAR(32) NOT NULL, catalyst VARCHAR(64) NULL, toughener_pct DECIMAL(6,3) NOT NULL, "
    "filler_pct DECIMAL(6,3) NOT NULL, crosslink_density DECIMAL(6,4) NOT NULL, properties JSON NOT NULL, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP"
    ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4",
    "CREATE TABLE IF NOT EXISTS simulation_results ("
    "id BIGINT AUTO_INCREMENT PRIMARY KEY, candidate_id VARCHAR(32) NOT NULL, model_version VARCHAR(32) NOT NULL, qchem JSON NOT NULL, md JSON NOT NULL, interface_data JSON NOT NULL, predictions JSON NOT NULL, multi_objective_score DECIMAL(8,3) NOT NULL, screening_class VARCHAR(32) NOT NULL, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, INDEX ix_result_candidate (candidate_id)"
    ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4",
    "CREATE TABLE IF NOT EXISTS experimental_results ("
    "id BIGINT AUTO_INCREMENT PRIMARY KEY, candidate_id VARCHAR(32) NOT NULL, test_batch VARCHAR(64) NOT NULL, test_temperature_c DECIMAL(7,2) NULL, properties JSON NOT NULL, source VARCHAR(128) NULL, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, INDEX ix_experiment_candidate (candidate_id)"
    ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4",
    "CREATE TABLE IF NOT EXISTS model_versions ("
    "model_version VARCHAR(96) PRIMARY KEY, metadata JSON NOT NULL, artifact_path VARCHAR(512) NULL, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP"
    ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4",
)


class DatabaseError(RuntimeError):
    pass


def jsonable(value: Any) -> Any:
    """Convert dataclass and NumPy calculation results into database JSON values."""
    if is_dataclass(value):
        return jsonable(asdict(value))
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, dict):
        return {str(key): jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [jsonable(item) for item in value]
    return value


def _json(value: Any) -> str:
    return json.dumps(jsonable(value), ensure_ascii=False)


def _sqlite_path() -> str:
    return os.getenv("ADHESIVE_SQLITE_PATH", "work/adhesive_ai_lab.sqlite3")


@contextmanager
def sqlite_connection() -> Iterator[sqlite3.Connection]:
    path = _sqlite_path()
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    try:
        conn.execute("CREATE TABLE IF NOT EXISTS experimental_results (id INTEGER PRIMARY KEY AUTOINCREMENT, candidate_id TEXT NOT NULL, test_batch TEXT NOT NULL, test_temperature_c REAL, properties TEXT NOT NULL, source TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP)")
        conn.execute("CREATE TABLE IF NOT EXISTS model_versions (model_version TEXT PRIMARY KEY, metadata TEXT NOT NULL, artifact_path TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP)")
        yield conn
        conn.commit()
    finally:
        conn.close()


def config_from_env() -> dict[str, Any]:
    """Build mysql-connector settings from DATABASE_URL or MYSQL_* variables."""
    config: dict[str, Any] = {
        "host": "127.0.0.1",
        "port": 3306,
        "user": "root",
        "password": "",
        "database": "adhesive_ai_lab",
        "connection_timeout": int(os.getenv("MYSQL_CONNECT_TIMEOUT", "5")),
    }
    database_url = os.getenv("DATABASE_URL", "").strip()
    if database_url:
        parsed = urlparse(database_url)
        if not parsed.scheme.startswith("mysql"):
            raise DatabaseError("DATABASE_URL must use a MySQL scheme")
        query = parse_qs(parsed.query)
        config.update(
            host=parsed.hostname or config["host"],
            port=parsed.port or config["port"],
            user=unquote(parsed.username or config["user"]),
            password=unquote(parsed.password or ""),
            database=unquote(parsed.path.lstrip("/")) or config["database"],
        )
        if query.get("charset"):
            config["charset"] = query["charset"][-1]

    overrides = {
        "host": os.getenv("MYSQL_HOST"),
        "port": os.getenv("MYSQL_PORT"),
        "user": os.getenv("MYSQL_USER"),
        "password": os.getenv("MYSQL_PASSWORD"),
        "database": os.getenv("MYSQL_DATABASE"),
    }
    for key, value in overrides.items():
        if value is not None:
            config[key] = int(value) if key == "port" else value
    return config


def _connector() -> Any:
    if mysql_connector is None:
        raise DatabaseError("mysql-connector-python is not installed; MySQL persistence is unavailable")
    return mysql_connector


@contextmanager
def connection() -> Iterator[Any]:
    connector = _connector()
    try:
        conn = connector.connect(**config_from_env())
    except connector.Error as exc:
        raise DatabaseError(f"无法连接 MySQL: {exc}") from exc
    try:
        try:
            yield conn
            conn.commit()
        except connector.Error as exc:
            conn.rollback()
            raise DatabaseError(f"MySQL 操作失败: {exc}") from exc
    finally:
        conn.close()


def initialize_schema() -> None:
    with connection() as conn:
        cursor = conn.cursor()
        try:
            for statement in SCHEMA:
                cursor.execute(statement)
        finally:
            cursor.close()


def save_candidate(row: dict[str, Any]) -> None:
    formulation_keys = (
        "candidate_id",
        "resin",
        "blend_resin",
        "blend_fraction",
        "dynamic_unit",
        "cure_system",
        "catalyst",
        "toughener_pct",
        "filler_pct",
        "crosslink_density",
    )
    properties = {key: value for key, value in row.items() if key not in formulation_keys and key not in {"resin_name", "dynamic_name", "cure_name"}}
    params = [row.get(key) for key in formulation_keys] + [_json(properties)]
    sql = (
        "INSERT INTO candidates (candidate_id,resin,blend_resin,blend_fraction,dynamic_unit,cure_system,catalyst,toughener_pct,filler_pct,crosslink_density,properties) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
        "ON DUPLICATE KEY UPDATE resin=VALUES(resin), blend_resin=VALUES(blend_resin), blend_fraction=VALUES(blend_fraction), "
        "dynamic_unit=VALUES(dynamic_unit), cure_system=VALUES(cure_system), catalyst=VALUES(catalyst), toughener_pct=VALUES(toughener_pct), "
        "filler_pct=VALUES(filler_pct), crosslink_density=VALUES(crosslink_density), properties=VALUES(properties)"
    )
    with connection() as conn:
        cursor = conn.cursor()
        try:
            cursor.execute(sql, params)
        finally:
            cursor.close()


def save_simulation(
    row: dict[str, Any],
    qchem: dict[str, Any],
    md: dict[str, Any],
    interface: dict[str, Any],
    model_version: str = "proxy-v1",
) -> None:
    prediction_keys = ("wide_temp_adhesion_mpa", "healing_efficiency_pct", "atomic_oxygen_retention_pct", "uv_retention_pct", "am_feasibility")
    params = (
        row["candidate_id"],
        model_version,
        _json(qchem),
        _json(md),
        _json(interface),
        _json({key: row[key] for key in prediction_keys}),
        row["multi_objective_score"],
        row["screening_class"],
    )
    with connection() as conn:
        cursor = conn.cursor()
        try:
            cursor.execute(
                "INSERT INTO simulation_results (candidate_id,model_version,qchem,md,interface_data,predictions,multi_objective_score,screening_class) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                params,
            )
        finally:
            cursor.close()


def load_candidate_results(limit: int = 300) -> pd.DataFrame:
    query = (
        "SELECT c.candidate_id,c.resin,c.blend_resin,c.blend_fraction,c.dynamic_unit,c.cure_system,c.catalyst,c.toughener_pct,c.filler_pct,"
        "c.crosslink_density,c.properties,s.predictions,s.multi_objective_score,s.screening_class,s.created_at "
        "FROM simulation_results s "
        "JOIN candidates c ON c.candidate_id=s.candidate_id "
        "JOIN (SELECT candidate_id,MAX(id) AS latest_id FROM simulation_results GROUP BY candidate_id) latest ON latest.latest_id=s.id "
        "ORDER BY s.multi_objective_score DESC LIMIT %s"
    )
    with connection() as conn:
        return pd.read_sql(query, conn, params=(limit,))


def save_experiment(
    candidate_id: str,
    properties: dict[str, Any],
    test_batch: str = "manual",
    temperature_c: float | None = None,
    source: str | None = None,
) -> None:
    try:
        with connection() as conn:
            cursor = conn.cursor()
            try:
                cursor.execute(
                    "INSERT INTO experimental_results (candidate_id,test_batch,test_temperature_c,properties,source) VALUES (%s,%s,%s,%s,%s)",
                    (candidate_id, test_batch, temperature_c, _json(properties), source),
                )
            finally:
                cursor.close()
    except DatabaseError:
        with sqlite_connection() as conn:
            conn.execute("INSERT INTO experimental_results (candidate_id,test_batch,test_temperature_c,properties,source) VALUES (?,?,?,?,?)", (candidate_id, test_batch, temperature_c, _json(properties), source))


def load_experiments(candidate_ids: list[str] | None = None) -> pd.DataFrame:
    """Load persisted experimental rows, preferring MySQL and falling back to SQLite."""
    try:
        with connection() as conn:
            query = "SELECT candidate_id,test_batch,test_temperature_c,properties,source,created_at FROM experimental_results"
            params: tuple[Any, ...] = ()
            if candidate_ids:
                placeholders = ",".join(["%s"] * len(candidate_ids))
                query += f" WHERE candidate_id IN ({placeholders})"
                params = tuple(candidate_ids)
            frame = pd.read_sql(query, conn, params=params)
    except DatabaseError:
        with sqlite_connection() as conn:
            query = "SELECT candidate_id,test_batch,test_temperature_c,properties,source,created_at FROM experimental_results"
            params = []
            if candidate_ids:
                query += " WHERE candidate_id IN (" + ",".join(["?"] * len(candidate_ids)) + ")"
                params = candidate_ids
            frame = pd.read_sql_query(query, conn, params=params)
    if frame.empty:
        return frame
    expanded = frame["properties"].apply(lambda value: json.loads(value) if isinstance(value, str) else {}).apply(pd.Series)
    return pd.concat([frame.drop(columns=["properties"]), expanded], axis=1)


def save_model_version(model: Any, artifact_path: str | None = None) -> None:
    """Persist model metadata so predictions can be traced to a training version."""
    metadata = {
        "feature_names": getattr(model, "feature_names", ()), "target_names": getattr(model, "target_names", ()),
        "training_rows": getattr(model, "training_rows", 0), "experimental_rows": getattr(model, "experimental_rows", 0),
        "validation_metrics": getattr(model, "validation_metrics", {}), "created_at": getattr(model, "created_at", ""),
    }
    try:
        with connection() as conn:
            cursor = conn.cursor()
            try:
                cursor.execute(
                    "INSERT INTO model_versions (model_version,metadata,artifact_path) VALUES (%s,%s,%s) ON DUPLICATE KEY UPDATE metadata=VALUES(metadata), artifact_path=VALUES(artifact_path)",
                    (getattr(model, "version", "unknown"), _json(metadata), artifact_path),
                )
            finally:
                cursor.close()
    except DatabaseError:
        with sqlite_connection() as conn:
            conn.execute("INSERT INTO model_versions (model_version,metadata,artifact_path) VALUES (?,?,?) ON CONFLICT(model_version) DO UPDATE SET metadata=excluded.metadata, artifact_path=excluded.artifact_path", (getattr(model, "version", "unknown"), _json(metadata), artifact_path))
