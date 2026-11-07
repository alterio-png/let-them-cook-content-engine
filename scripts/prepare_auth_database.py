"""Bootstrap only a fresh loopback G3A Auth database; never hosted Supabase.

The official GoTrue image, not this script, creates auth.users/auth.sessions.
Run bootstrap before GoTrue, engine after the official migrations complete.
"""
import argparse
import os
from pathlib import Path

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("stage", choices=("bootstrap", "engine"))
    stage = parser.parse_args().stage
    dsn = os.environ["G3A_AUTH_ADMIN_DSN"]
    info = conninfo_to_dict(dsn)
    if info.get("host") not in {"127.0.0.1", "localhost", "::1"}:
        raise SystemExit("Refusing non-loopback Auth test database")
    if not info.get("dbname", "").startswith("g3a_auth_"):
        raise SystemExit("Disposable Auth database must begin g3a_auth_")
    root = Path(__file__).resolve().parents[1]
    with psycopg.connect(dsn) as db:
        if stage == "bootstrap":
            if db.execute("SELECT to_regnamespace('auth')").fetchone()[0]:
                raise SystemExit("Auth schema exists; require fresh disposable database")
            password = os.environ["G3A_AUTH_DB_PASSWORD"]
            db.execute(sql.SQL("CREATE ROLE supabase_auth_admin LOGIN NOINHERIT "
                              "NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION "
                              "NOBYPASSRLS PASSWORD {}").format(sql.Literal(password)))
            db.execute("CREATE SCHEMA auth AUTHORIZATION supabase_auth_admin")
            # GoTrue's Pop migrator stores its migration ledger in the login's
            # search_path; do not grant CREATE on public to work around this.
            db.execute("ALTER ROLE supabase_auth_admin SET search_path = auth")
            db.execute("CREATE ROLE anon NOLOGIN NOSUPERUSER NOBYPASSRLS")
            db.execute("CREATE ROLE authenticated NOLOGIN NOSUPERUSER NOBYPASSRLS")
            print("Bootstrapped official Auth owner/roles only; no auth table fixture", flush=True)
            return
        if not db.execute("SELECT to_regclass('auth.sessions') IS NOT NULL "
                          "AND to_regclass('auth.schema_migrations') IS NOT NULL").fetchone()[0]:
            raise SystemExit("Official GoTrue migrations must finish first")
        if db.execute("SELECT to_regclass('public.organizations')").fetchone()[0]:
            raise SystemExit("Engine exists; use fresh disposable database")
        # auth.uid() is also supplied by the official migrations; preserve it.
        db.execute("""
            REVOKE ALL ON SCHEMA auth FROM PUBLIC;
            REVOKE ALL ON FUNCTION auth.uid() FROM PUBLIC;
            GRANT USAGE ON SCHEMA auth TO authenticated;
            GRANT EXECUTE ON FUNCTION auth.uid() TO authenticated;
        """)
        for name in ("sql/001_schema.sql", "sql/002_g1_control_plane.sql", "sql/003_media_execution.sql"):
            db.execute((root / name).read_text())
            print(f"Applied unchanged {name}", flush=True)
        db.execute("""
            CREATE ROLE g3a_session_reader NOLOGIN NOINHERIT NOSUPERUSER
              NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
            GRANT USAGE ON SCHEMA auth TO g3a_session_reader;
            GRANT EXECUTE ON FUNCTION auth.uid() TO g3a_session_reader;
            GRANT SELECT(id, user_id, not_after) ON auth.sessions TO g3a_session_reader;
            CREATE POLICY g3a_own_session_lookup ON auth.sessions FOR SELECT
              TO g3a_session_reader USING (user_id = (SELECT auth.uid()));
        """)
        db.execute(sql.SQL("CREATE ROLE g3a_api_login LOGIN NOINHERIT NOSUPERUSER "
                          "NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS "
                          "PASSWORD {}").format(sql.Literal(os.environ["G3A_API_PASSWORD"])))
        db.execute("GRANT authenticated, g3a_session_reader TO g3a_api_login")
        print("Installed narrow session grant and non-bypass API login", flush=True)


if __name__ == "__main__":
    main()
