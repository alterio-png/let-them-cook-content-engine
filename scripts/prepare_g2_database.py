"""Fresh local G0/G1 database plus additive G2 migration. Never production."""
import os
from pathlib import Path
import psycopg
from prepare_g1_database import main as prepare_g1


if __name__ == "__main__":
    prepare_g1()
    with psycopg.connect(os.environ["G1_TEST_DSN"]) as db:
        db.execute((Path(__file__).resolve().parents[1] / "sql/003_media_execution.sql").read_text())
        print("Applied sql/003_media_execution.sql", flush=True)
