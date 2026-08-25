"""Initialize and verify the configured MySQL database."""

from __future__ import annotations

import sys
from pathlib import Path
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from adhesive_ai.database import config_from_env, connection, initialize_schema


def main() -> None:
    initialize_schema()
    config = config_from_env()
    with connection() as conn:
        cursor = conn.cursor()
        try:
            cursor.execute("SHOW TABLES")
            tables = sorted(str(row[0]) for row in cursor.fetchall())
            cursor.execute("SELECT 1")
            read_test = cursor.fetchone()[0]
            probe_key = f"connection-test-{uuid4().hex}"
            cursor.execute(
                "INSERT INTO model_versions (model_version, metadata) VALUES (%s, %s)",
                (probe_key, "{}"),
            )
            cursor.execute(
                "SELECT COUNT(*) FROM model_versions WHERE model_version = %s",
                (probe_key,),
            )
            write_test = cursor.fetchone()[0]
            cursor.execute(
                "DELETE FROM model_versions WHERE model_version = %s",
                (probe_key,),
            )
        finally:
            cursor.close()

    print("MySQL connection verified")
    print(f"Server: {config['host']}:{config['port']}")
    print(f"Database: {config['database']}")
    print(f"Tables: {', '.join(tables)}")
    print(f"Read test: {read_test}")
    print(f"Write test: {write_test}")


if __name__ == "__main__":
    main()
