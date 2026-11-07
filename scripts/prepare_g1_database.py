"""Initialize an EMPTY disposable local PostgreSQL database; never production.

G1_TEST_DSN must name a loopback database beginning g1_. No destructive reset.
The auth fixture models verified auth.uid() claims, not a Supabase deployment.
"""
import os
from pathlib import Path

import psycopg
from psycopg.conninfo import conninfo_to_dict


def main():
    dsn = os.environ["G1_TEST_DSN"]
    info = conninfo_to_dict(dsn)
    if info.get("host") not in {"127.0.0.1", "localhost", "::1"}:
        raise SystemExit("Refusing non-loopback test database")
    if not info.get("dbname", "").startswith("g1_"):
        raise SystemExit("Disposable test database name must begin g1_")
    root = Path(__file__).resolve().parents[1]
    with psycopg.connect(dsn) as db:
        if db.execute("SELECT to_regclass('public.organizations')").fetchone()[0]:
            raise SystemExit("Database already initialized; use a fresh disposable database")
        for name in ("integration/g1/bootstrap.sql", "sql/001_schema.sql",
                     "sql/002_g1_control_plane.sql"):
            db.execute((root / name).read_text())
            print(f"Applied {name}", flush=True)
        print(db.execute("SELECT version()").fetchone()[0])


if __name__ == "__main__":
    main()
