"""MySQL persistence for virtual candidates, simulations and experimental feedback."""

from __future__ import annotations

import json
import os
import sqlite3
from decimal import Decimal
from pathlib import Path
from dataclasses import asdict, is_dataclass
from contextlib import contextmanager
from typing import Any, Iterator, Mapping
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
    "candidate_id VARCHAR(32) PRIMARY KEY, formulation_id VARCHAR(80) NOT NULL, candidate_library_version VARCHAR(64) NOT NULL, resin VARCHAR(32) NOT NULL, blend_resin VARCHAR(32) NULL, blend_fraction DECIMAL(5,3) NOT NULL, "
    "dynamic_unit VARCHAR(32) NOT NULL, cure_system VARCHAR(32) NOT NULL, catalyst VARCHAR(64) NULL, toughener_pct DECIMAL(6,3) NOT NULL, "
    "filler_pct DECIMAL(6,3) NOT NULL, crosslink_density DECIMAL(6,4) NOT NULL, properties JSON NOT NULL, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP, INDEX ix_candidate_formulation (formulation_id)"
    ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4",
    "CREATE TABLE IF NOT EXISTS simulation_results ("
    "id BIGINT AUTO_INCREMENT PRIMARY KEY, candidate_id VARCHAR(32) NOT NULL, formulation_id VARCHAR(80) NULL, candidate_library_version VARCHAR(64) NULL, model_version VARCHAR(32) NOT NULL, qchem JSON NOT NULL, md JSON NOT NULL, interface_data JSON NOT NULL, predictions JSON NOT NULL, multi_objective_score DECIMAL(8,3) NOT NULL, screening_class VARCHAR(32) NOT NULL, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, INDEX ix_result_candidate (candidate_id), INDEX ix_result_formulation (formulation_id)"
    ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4",
    "CREATE TABLE IF NOT EXISTS experimental_results ("
    "id BIGINT AUTO_INCREMENT PRIMARY KEY, candidate_id VARCHAR(32) NOT NULL, formulation_id VARCHAR(80) NULL, candidate_library_version VARCHAR(64) NULL, test_batch VARCHAR(64) NOT NULL, test_temperature_c DECIMAL(7,2) NULL, properties JSON NOT NULL, source VARCHAR(128) NULL, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, INDEX ix_experiment_candidate (candidate_id), INDEX ix_experiment_formulation (formulation_id)"
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
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, dict):
        return {str(key): jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [jsonable(item) for item in value]
    return value


def _json(value: Any) -> str:
    return json.dumps(jsonable(value), ensure_ascii=False)


def _sqlite_path() -> str:
    return os.getenv("ADHESIVE_SQLITE_PATH", "work/adhesive_ai_lab.sqlite3")


def _ensure_mysql_identity_columns(conn: Any) -> None:
    """Add identity columns to existing installations without deleting legacy history."""
    specifications = {
        "candidates": (
            ("formulation_id", "VARCHAR(80) NULL"),
            ("candidate_library_version", "VARCHAR(64) NULL"),
        ),
        "simulation_results": (
            ("formulation_id", "VARCHAR(80) NULL"),
            ("candidate_library_version", "VARCHAR(64) NULL"),
        ),
        "experimental_results": (
            ("formulation_id", "VARCHAR(80) NULL"),
            ("candidate_library_version", "VARCHAR(64) NULL"),
        ),
    }
    cursor = conn.cursor()
    try:
        for table, columns in specifications.items():
            cursor.execute(f"SHOW COLUMNS FROM {table}")
            existing = {str(row[0]) for row in cursor.fetchall()}
            for name, definition in columns:
                if name not in existing:
                    cursor.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")
            if table == "candidates":
                cursor.execute(
                    "UPDATE candidates SET formulation_id=JSON_UNQUOTE(JSON_EXTRACT(properties, '$.formulation_id')) "
                    "WHERE formulation_id IS NULL"
                )
                cursor.execute(
                    "UPDATE candidates SET candidate_library_version=JSON_UNQUOTE(JSON_EXTRACT(properties, '$.candidate_library_version')) "
                    "WHERE candidate_library_version IS NULL"
                )
                cursor.execute("SHOW INDEX FROM candidates WHERE Key_name='ix_candidate_formulation'")
                if not cursor.fetchall():
                    cursor.execute("CREATE INDEX ix_candidate_formulation ON candidates (formulation_id)")
    finally:
        cursor.close()


@contextmanager
def sqlite_connection() -> Iterator[sqlite3.Connection]:
    path = _sqlite_path()
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    try:
        conn.execute("CREATE TABLE IF NOT EXISTS experimental_results (id INTEGER PRIMARY KEY AUTOINCREMENT, candidate_id TEXT NOT NULL, formulation_id TEXT, candidate_library_version TEXT, test_batch TEXT NOT NULL, test_temperature_c REAL, properties TEXT NOT NULL, source TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP)")
        existing_columns = {row[1] for row in conn.execute("PRAGMA table_info(experimental_results)")}
        if "formulation_id" not in existing_columns:
            conn.execute("ALTER TABLE experimental_results ADD COLUMN formulation_id TEXT")
        if "candidate_library_version" not in existing_columns:
            conn.execute("ALTER TABLE experimental_results ADD COLUMN candidate_library_version TEXT")
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
        _ensure_mysql_identity_columns(conn)


def save_candidate(row: dict[str, Any]) -> None:
    """Upsert one candidate formulation."""
    save_candidates([row])


def save_candidates(rows: Any) -> int:
    """Upsert a candidate batch in one transaction and return its row count."""
    if isinstance(rows, pd.DataFrame):
        records = rows.to_dict("records")
    else:
        records = [dict(row) for row in rows]
    if not records:
        return 0
    formulation_keys = (
        "candidate_id",
        "formulation_id",
        "candidate_library_version",
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
    params = []
    for row in records:
        properties = {key: value for key, value in row.items() if key not in formulation_keys}
        params.append(tuple([row.get(key) for key in formulation_keys] + [_json(properties)]))
    sql = (
        "INSERT INTO candidates (candidate_id,formulation_id,candidate_library_version,resin,blend_resin,blend_fraction,dynamic_unit,cure_system,catalyst,toughener_pct,filler_pct,crosslink_density,properties) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
        "ON DUPLICATE KEY UPDATE formulation_id=VALUES(formulation_id), candidate_library_version=VALUES(candidate_library_version), resin=VALUES(resin), blend_resin=VALUES(blend_resin), blend_fraction=VALUES(blend_fraction), "
        "dynamic_unit=VALUES(dynamic_unit), cure_system=VALUES(cure_system), catalyst=VALUES(catalyst), toughener_pct=VALUES(toughener_pct), "
        "filler_pct=VALUES(filler_pct), crosslink_density=VALUES(crosslink_density), properties=VALUES(properties)"
    )
    with connection() as conn:
        _ensure_mysql_identity_columns(conn)
        cursor = conn.cursor()
        try:
            cursor.executemany(sql, params)
        finally:
            cursor.close()
    return len(records)


def load_candidates(candidate_ids: list[str] | None = None) -> pd.DataFrame:
    """Load candidate formulations with JSON properties expanded into columns."""
    query = (
        "SELECT candidate_id,formulation_id,candidate_library_version,resin,blend_resin,blend_fraction,dynamic_unit,cure_system,catalyst,"
        "toughener_pct,filler_pct,crosslink_density,properties FROM candidates"
    )
    params: tuple[Any, ...] = ()
    if candidate_ids:
        placeholders = ",".join(["%s"] * len(candidate_ids))
        query += f" WHERE candidate_id IN ({placeholders})"
        params = tuple(candidate_ids)
    query += " ORDER BY candidate_id"
    with connection() as conn:
        cursor = conn.cursor(dictionary=True)
        try:
            cursor.execute(query, params)
            rows = cursor.fetchall()
        finally:
            cursor.close()

    records: list[dict[str, Any]] = []
    for row in rows:
        record = dict(row)
        raw_properties = record.pop("properties", {})
        if isinstance(raw_properties, str):
            properties = json.loads(raw_properties)
        else:
            properties = dict(raw_properties or {})
        records.append({**record, **properties})
    return pd.DataFrame(records)


def save_simulation(
    row: dict[str, Any],
    qchem: dict[str, Any],
    md: dict[str, Any],
    interface: dict[str, Any],
    model_version: str = "proxy-v1",
) -> None:
    """Append a cumulative simulation snapshot for one candidate."""
    formulation_id = str(row.get("formulation_id") or "").strip()
    if not formulation_id:
        raise ValueError("Simulation snapshots require formulation_id")
    prediction_keys = ("wide_temp_adhesion_mpa", "healing_efficiency_pct", "atomic_oxygen_retention_pct", "uv_retention_pct", "am_feasibility")
    predictions = {
        key: row.get(key, row.get(f"predicted_{key}"))
        for key in prediction_keys
    }
    params = (
        row["candidate_id"],
        formulation_id,
        str(row.get("candidate_library_version") or "") or None,
        model_version,
        _json(qchem),
        _json(md),
        _json(interface),
        _json(predictions),
        row.get("multi_objective_score", row.get("predicted_multi_objective_score")),
        row.get("screening_class", row.get("predicted_screening_class")),
    )
    with connection() as conn:
        _ensure_mysql_identity_columns(conn)
        cursor = conn.cursor()
        try:
            cursor.execute(
                "INSERT INTO simulation_results (candidate_id,formulation_id,candidate_library_version,model_version,qchem,md,interface_data,predictions,multi_objective_score,screening_class) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                params,
            )
        finally:
            cursor.close()


def load_latest_simulation_results(
    candidate_ids: list[str] | None = None,
    *,
    formulation_ids: Mapping[str, str] | None = None,
) -> dict[str, dict[str, Any]]:
    """Return the latest cumulative external-calculation snapshot per candidate."""
    query = (
        "SELECT s.candidate_id,s.formulation_id,s.candidate_library_version,s.model_version,s.qchem,s.md,s.interface_data,s.predictions,"
        "s.multi_objective_score,s.screening_class,s.created_at "
        "FROM simulation_results s "
        "JOIN (SELECT candidate_id,formulation_id,MAX(id) AS latest_id FROM simulation_results GROUP BY candidate_id,formulation_id) latest "
        "ON latest.latest_id=s.id AND latest.candidate_id=s.candidate_id AND latest.formulation_id=s.formulation_id"
    )
    params: tuple[Any, ...] = ()
    if candidate_ids:
        placeholders = ",".join(["%s"] * len(candidate_ids))
        query += f" WHERE s.candidate_id IN ({placeholders})"
        params = tuple(candidate_ids)
    with connection() as conn:
        _ensure_mysql_identity_columns(conn)
        cursor = conn.cursor(dictionary=True)
        try:
            cursor.execute(query, params)
            rows = cursor.fetchall()
        finally:
            cursor.close()

    def decoded(value: Any) -> dict[str, Any]:
        if isinstance(value, str):
            return json.loads(value)
        return dict(value or {})

    return {
        str(row["candidate_id"]): {
            "formulation_id": row.get("formulation_id"),
            "candidate_library_version": row.get("candidate_library_version"),
            "model_version": row["model_version"],
            "dft": decoded(row["qchem"]),
            "md": decoded(row["md"]),
            "interface": decoded(row["interface_data"]),
            "predictions": decoded(row["predictions"]),
            "multi_objective_score": float(row["multi_objective_score"]),
            "screening_class": row["screening_class"],
            "created_at": row["created_at"],
        }
        for row in rows
        if not formulation_ids or row.get("formulation_id") == formulation_ids.get(str(row["candidate_id"]))
    }


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
    *,
    formulation_id: str,
    candidate_library_version: str,
) -> None:
    row = {
        "candidate_id": candidate_id,
        "formulation_id": formulation_id,
        "candidate_library_version": candidate_library_version,
        "test_batch": test_batch,
        "test_temperature_c": temperature_c,
        "source": source,
        **properties,
    }
    save_experiments(pd.DataFrame([row]))


def save_experiments(
    frame: pd.DataFrame,
    *,
    default_source: str | None = None,
    candidate_formulations: Mapping[str, str] | None = None,
    candidate_library_versions: Mapping[str, str] | None = None,
) -> int:
    """Append a validated batch of experimental feedback rows."""
    if frame is None or frame.empty:
        return 0
    records = frame.to_dict("records")
    metadata_keys = {
        "candidate_id", "formulation_id", "candidate_library_version",
        "test_batch", "test_temperature_c", "source", "created_at",
    }
    params = []
    for row in records:
        raw_candidate_id = row.get("candidate_id")
        candidate_id = "" if raw_candidate_id is None or pd.isna(raw_candidate_id) else str(raw_candidate_id).strip()
        if not candidate_id:
            raise ValueError("Experimental rows require candidate_id")
        raw_formulation_id = row.get("formulation_id")
        formulation_id = "" if raw_formulation_id is None or pd.isna(raw_formulation_id) else str(raw_formulation_id).strip()
        if not formulation_id:
            raise ValueError(f"实验记录缺少配方指纹：{candidate_id}")
        if candidate_formulations is not None:
            expected = candidate_formulations.get(candidate_id)
            if not expected:
                raise ValueError(f"实验记录候选编号不在当前候选库：{candidate_id}")
            if formulation_id != expected:
                raise ValueError(f"实验记录的配方指纹与候选编号不匹配：{candidate_id}")
        raw_library_version = row.get("candidate_library_version")
        library_version = (
            None if raw_library_version is None or pd.isna(raw_library_version)
            else str(raw_library_version).strip() or None
        )
        if not library_version:
            raise ValueError(f"实验记录缺少候选库版本：{candidate_id}")
        if candidate_library_versions is not None and library_version != candidate_library_versions.get(candidate_id):
            raise ValueError(f"实验记录的候选库版本与当前候选不匹配：{candidate_id}")
        raw_batch = row.get("test_batch")
        test_batch = "manual" if raw_batch is None or pd.isna(raw_batch) else str(raw_batch).strip() or "manual"
        temperature_c = row.get("test_temperature_c")
        if temperature_c is not None and pd.isna(temperature_c):
            temperature_c = None
        raw_source = row.get("source")
        source = default_source if raw_source is None or pd.isna(raw_source) else str(raw_source).strip() or default_source
        properties = {
            key: value for key, value in row.items()
            if key not in metadata_keys and not (isinstance(value, float) and np.isnan(value))
        }
        params.append((candidate_id, formulation_id, library_version, test_batch, temperature_c, _json(properties), source))
    try:
        with connection() as conn:
            _ensure_mysql_identity_columns(conn)
            cursor = conn.cursor()
            try:
                cursor.executemany(
                    "INSERT INTO experimental_results (candidate_id,formulation_id,candidate_library_version,test_batch,test_temperature_c,properties,source) VALUES (%s,%s,%s,%s,%s,%s,%s)",
                    params,
                )
            finally:
                cursor.close()
    except DatabaseError:
        with sqlite_connection() as conn:
            conn.executemany("INSERT INTO experimental_results (candidate_id,formulation_id,candidate_library_version,test_batch,test_temperature_c,properties,source) VALUES (?,?,?,?,?,?,?)", params)
    return len(params)


def load_experiments(
    candidate_ids: list[str] | None = None,
    *,
    formulation_ids: Mapping[str, str] | None = None,
) -> pd.DataFrame:
    """Load persisted experimental rows, preferring MySQL and falling back to SQLite."""
    try:
        with connection() as conn:
            _ensure_mysql_identity_columns(conn)
            query = "SELECT candidate_id,formulation_id,candidate_library_version,test_batch,test_temperature_c,properties,source,created_at FROM experimental_results"
            params: tuple[Any, ...] = ()
            if candidate_ids:
                placeholders = ",".join(["%s"] * len(candidate_ids))
                query += f" WHERE candidate_id IN ({placeholders})"
                params = tuple(candidate_ids)
            cursor = conn.cursor(dictionary=True)
            try:
                cursor.execute(query, params)
                frame = pd.DataFrame(cursor.fetchall())
            finally:
                cursor.close()
    except DatabaseError:
        with sqlite_connection() as conn:
            query = "SELECT candidate_id,formulation_id,candidate_library_version,test_batch,test_temperature_c,properties,source,created_at FROM experimental_results"
            params = []
            if candidate_ids:
                query += " WHERE candidate_id IN (" + ",".join(["?"] * len(candidate_ids)) + ")"
                params = candidate_ids
            frame = pd.read_sql_query(query, conn, params=params)
    if frame.empty:
        return frame
    if formulation_ids:
        frame = frame.loc[
            frame["candidate_id"].astype(str).map(formulation_ids).eq(frame["formulation_id"])
        ].copy()
    if frame.empty:
        return frame.drop(columns=["properties"])
    expanded = frame["properties"].apply(lambda value: json.loads(value) if isinstance(value, str) else {}).apply(pd.Series)
    return pd.concat([frame.drop(columns=["properties"]), expanded], axis=1)


def save_model_version(model: Any, artifact_path: str | None = None) -> None:
    """Persist model metadata so predictions can be traced to a training version."""
    metadata = {
        "feature_names": getattr(model, "feature_names", ()), "target_names": getattr(model, "target_names", ()),
        "training_rows": getattr(model, "training_rows", 0), "experimental_rows": getattr(model, "experimental_rows", 0),
        "validation_metrics": getattr(model, "validation_metrics", {}), "created_at": getattr(model, "created_at", ""),
        "data_provenance": getattr(model, "data_provenance", {}),
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
