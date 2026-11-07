"""Prepare only a fresh disposable loopback PostgreSQL database, through G3A.1."""
import os
from pathlib import Path
import psycopg
from psycopg.conninfo import conninfo_to_dict
from prepare_g3a_database import main as prepare_g3a


def main():
    info = conninfo_to_dict(os.environ["G1_TEST_DSN"])
    if (info.get("host") not in {"127.0.0.1", "localhost", "::1"}
            or not info.get("dbname", "").startswith("g1_")
            or info.get("service") or info.get("hostaddr")
            or os.environ.get("PGSERVICE") or os.environ.get("PGHOSTADDR")):
        raise SystemExit("Refusing non-laboratory or overridden database target")
    prepare_g3a()
    with psycopg.connect(os.environ["G1_TEST_DSN"]) as db:
        name = "supabase/migrations/20261107150813_shared_upload_authority.sql"
        db.execute((Path(__file__).resolve().parents[1] / name).read_text())
        print(f"Applied {name}", flush=True)


if __name__ == "__main__":
    main()
