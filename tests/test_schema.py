from __future__ import annotations

import tempfile
import unittest
import sqlite3
from pathlib import Path

from toolburn.schema import REQUIRED_TABLES, initialize_database, table_names


class SchemaTests(unittest.TestCase):
    def test_initialize_database_creates_required_tables(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "toolburn.sqlite"

            initialize_database(db_path)

            self.assertEqual(table_names(db_path), sorted(REQUIRED_TABLES))

    def test_initialize_database_migrates_existing_invocations_for_factual_bundles(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "toolburn.sqlite"
            with sqlite3.connect(db_path) as conn:
                conn.execute(
                    """
                    create table invocations(
                      invocation_id text primary key,
                      session_id text,
                      actor_id text,
                      tool_id text,
                      started_at text,
                      ended_at text,
                      output_bytes integer,
                      output_fingerprint text,
                      output_shape_json text
                    )
                    """
                )
            initialize_database(db_path)
            with sqlite3.connect(db_path) as conn:
                columns = {row[1] for row in conn.execute("pragma table_info(invocations)")}
            self.assertIn("bundle_json", columns)
            self.assertIn("bundle_fingerprint", columns)


if __name__ == "__main__":
    unittest.main()
