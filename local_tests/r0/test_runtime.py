"""Runtime file safety and actual PostgreSQL principal/budget backstops."""
import json
import fcntl
import os
from pathlib import Path
import tempfile
import time
import unittest

import psycopg
from psycopg.rows import dict_row

from engine.local_runtime import event, local_dsn, private_json, sample_resources


class RuntimeFileSafetyTests(unittest.TestCase):
    def test_configuration_rejects_public_symlink_and_fifo_without_blocking(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / "config.json"
            config.write_text('{"safe":true}')
            config.chmod(0o600)
            self.assertEqual(private_json(config), {"safe": True})
            config.chmod(0o644)
            with self.assertRaises(ValueError):
                private_json(config)
            config.chmod(0o600)
            link = root / "link.json"
            link.symlink_to(config)
            with self.assertRaises(OSError):
                private_json(link)
            fifo = root / "fifo"
            os.mkfifo(fifo, 0o600)
            with self.assertRaises(ValueError):
                private_json(fifo)
            self.assertFalse(event(fifo, "resources", "sample"))

    def test_events_reject_public_file_and_never_exceed_disk_cap(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "events.jsonl"
            path.write_text("")
            path.chmod(0o644)
            self.assertFalse(event(path, "worker", "completed"))
            self.assertEqual(path.stat().st_size, 0)
            path.chmod(0o600)
            event(path, "worker", "completed", fence=2)
            self.assertEqual(json.loads(path.read_text())["fence"], 2)
            with path.open("a") as locked:
                fcntl.flock(locked.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                before = path.stat().st_size
                self.assertFalse(event(path, "worker", "completed"))
                self.assertEqual(path.stat().st_size, before)
            with path.open("r+b") as handle:
                handle.truncate(32 * 1024 * 1024 - 1)
            event(path, "worker", "completed")
            self.assertEqual(path.stat().st_size, 32 * 1024 * 1024 - 1)

    def test_runtime_refuses_remote_or_wrong_database_configuration(self):
        for dsn in ("host=example.invalid dbname=g3a_auth_lab", "host=127.0.0.1 dbname=production",
                    "host=127.0.0.1 dbname=g3a_auth_lab hostaddr=127.0.0.1"):
            with self.assertRaises(ValueError):
                local_dsn(dsn)


class RuntimePostgresSafetyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Missing real laboratory is an error, never a skipped/mocked PASS.
        cls.config = private_json(os.environ["G3BR0_TEST_CONFIG"])

    def test_serving_processes_do_not_inherit_setup_credentials(self):
        forbidden = {"G3A_AUTH_ADMIN_DSN", "G3A_AUTH_DB_PASSWORD", "G3A_API_PASSWORD",
                     "G3A_AUTH_JWT_SECRET", "G3A_API_DSN", "G1_TEST_DSN", "G3BR0_TEST_CONFIG"}
        for key in ("api_pid", "worker_pid", "monitor_pid"):
            raw = Path(f"/proc/{self.config[key]}/environ").read_bytes()
            names = {entry.split(b"=", 1)[0].decode() for entry in raw.split(b"\0") if entry}
            self.assertFalse(names & forbidden, (key, names & forbidden))

    def test_real_cluster_and_component_role_limits(self):
        expected = {"supabase_auth_admin": 4, "g3a_api_login": 6, "g3br0_worker_login": 3,
                    "g3br0_ingest_login": 2, "g3br0_monitor_login": 1}
        with psycopg.connect(local_dsn(self.config["admin_dsn"]), row_factory=dict_row) as db:
            self.assertEqual(db.execute("SHOW max_connections").fetchone()["max_connections"], "30")
            rows = db.execute("SELECT rolname,rolconnlimit,rolsuper,rolbypassrls,rolinherit,rolcreatedb,rolcreaterole "
                              "FROM pg_roles WHERE rolname=ANY(%s)", (list(expected),)).fetchall()
        self.assertEqual({row["rolname"]: row["rolconnlimit"] for row in rows}, expected)
        for row in rows:
            for flag in ("rolsuper", "rolbypassrls", "rolcreatedb", "rolcreaterole"):
                self.assertFalse(row[flag], (row["rolname"], flag))
            self.assertFalse(row["rolinherit"])

    def test_monitor_observes_known_real_api_connection(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "samples.jsonl"
            config = {"monitor_dsn": self.config["monitor_dsn"], "events_path": str(path),
                      "parent_pid": os.getpid()}
            # A known simultaneously open component connection is the witness;
            # monitoring only its own connection must fail this test.
            with psycopg.connect(local_dsn(self.config["api_dsn"])) as probe:
                probe.execute("SELECT 1")
                for attempt in range(5):
                    sample_resources(config, once=True)
                    samples = [json.loads(line) for line in path.read_text().splitlines()]
                    valid = [row for row in samples if row["outcome"] == "sample"]
                    if valid:
                        break
                    # The dedicated monitor login has a global cap of one;
                    # the continuously running sampler can briefly occupy it.
                    time.sleep(0.05)
            self.assertTrue(valid, samples)
            visible = {row["usename"]: row["connections"] for row in valid[-1]["postgres_clients"]}
            self.assertGreaterEqual(visible.get("g3a_api_login", 0), 1)
            self.assertGreaterEqual(valid[-1]["postgres_client_total"], 2)

    def test_api_login_cannot_assume_internal_worker_or_ingester(self):
        for role in ("engine_worker", "g3br0_ingester", "g3br0_monitor_login"):
            with psycopg.connect(local_dsn(self.config["api_dsn"])) as db:
                with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                    db.execute("SET ROLE " + role)
                db.rollback()

    def test_ingester_has_execute_authority_without_ledger_or_asset_dml(self):
        with psycopg.connect(local_dsn(self.config["admin_dsn"])) as admin:
            tables = [(table, admin.execute("SELECT %s::regclass::oid", (table,)).fetchone()[0]) for table in
                      ("public.assets", "g3br0_private.ingest_requests", "g3a1_private.upload_receipts")]
        with psycopg.connect(local_dsn(self.config["ingest_dsn"])) as db:
            db.execute("SET LOCAL ROLE g3br0_ingester")
            for table, oid in tables:
                for privilege in ("INSERT", "UPDATE", "DELETE"):
                    self.assertFalse(db.execute("SELECT has_table_privilege(current_user,%s::oid,%s)",
                                                (oid, privilege)).fetchone()[0], (table, privilege))


if __name__ == "__main__":
    unittest.main()
