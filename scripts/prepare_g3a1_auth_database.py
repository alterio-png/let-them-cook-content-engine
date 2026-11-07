"""Add the shared upload migration only to an already prepared local Auth lab."""
import os
from pathlib import Path
import psycopg
from psycopg.conninfo import conninfo_to_dict


def main():
    dsn = os.environ["G3A_AUTH_ADMIN_DSN"]
    info = conninfo_to_dict(dsn)
    if (info.get("host") not in {"127.0.0.1", "localhost", "::1"}
            or not info.get("dbname", "").startswith("g3a_auth_")
            or info.get("service") or info.get("hostaddr")
            or os.environ.get("PGSERVICE") or os.environ.get("PGHOSTADDR")):
        raise SystemExit("Refusing non-laboratory Auth database")
    with psycopg.connect(dsn) as db:
        if not db.execute("SELECT to_regclass('auth.schema_migrations') IS NOT NULL AND to_regclass('public.render_jobs') IS NOT NULL").fetchone()[0]:
            raise SystemExit("Official Auth and historical engine must be initialized first")
        if db.execute("SELECT to_regnamespace('g3a1_private')").fetchone()[0]:
            raise SystemExit("Shared authority already exists; require a fresh database")
        path = Path(__file__).resolve().parents[1] / "supabase/migrations/20261107150813_shared_upload_authority.sql"
        db.execute(path.read_text())
        print("Applied shared authority after official Auth and unchanged engine migrations", flush=True)


if __name__ == "__main__":
    main()
