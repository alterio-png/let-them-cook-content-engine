"""Prepare an empty disposable loopback PostgreSQL database for G3A contracts."""
import os
from pathlib import Path
import psycopg
from prepare_g2_database import prepare_g1


def main():
    prepare_g1()
    root = Path(__file__).resolve().parents[1]
    with psycopg.connect(os.environ["G1_TEST_DSN"]) as db:
        for name in ("sql/003_media_execution.sql",
                     "supabase/migrations/20261107123845_g3a_cloud_contracts.sql"):
            db.execute((root / name).read_text())
            print(f"Applied {name}", flush=True)


if __name__ == "__main__":
    main()
