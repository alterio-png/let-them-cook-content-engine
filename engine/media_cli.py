"""Run a synthetic G2 demonstration on a disposable loopback database only."""
import argparse
import json
import os
from pathlib import Path
from uuid import uuid4

import psycopg
from psycopg.conninfo import conninfo_to_dict
from psycopg.rows import dict_row

from .cli import demo
from .handoff import Dispatcher
from .local_queue import LocalQueue
from .media_ingest import ingest_media
from .media_pipeline import MediaWorker
from .object_store import LocalObjectStore


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    dsn = os.environ["G1_TEST_DSN"]
    info = conninfo_to_dict(dsn)
    if info.get("host") not in {"127.0.0.1", "localhost", "::1"} or not info.get("dbname", "").startswith("g1_"):
        raise SystemExit("Synthetic demo requires disposable loopback g1_ database")
    root = args.output.absolute()
    if root.exists():
        raise SystemExit("Choose a new output directory for private demo evidence")
    root.mkdir(mode=0o700, parents=True)
    media = demo(root / "synthetic", "demo")
    org_id, user_id = uuid4(), uuid4()
    store = LocalObjectStore(root / "objects")
    with psycopg.connect(dsn, row_factory=dict_row) as db:
        db.execute("INSERT INTO auth.users(id) VALUES(%s)", (user_id,))
        db.execute("INSERT INTO public.organizations(id,name) VALUES(%s,'G2 synthetic demo')", (org_id,))
        db.execute("INSERT INTO public.memberships(organization_id,user_id,role) VALUES(%s,%s,'editor')", (org_id, user_id))
        db.execute("INSERT INTO public.org_quotas(organization_id) VALUES(%s)", (org_id,))
        asset = ingest_media(db, store, org_id, user_id, Path(media["video"]),
                             Path(media["caption"]), rights_confirmed=True)
    with psycopg.connect(dsn, row_factory=dict_row) as db:
        db.execute("SET LOCAL ROLE authenticated")
        db.execute("SELECT set_config('request.jwt.claim.sub',%s,true)", (str(user_id),))
        job = db.execute("SELECT * FROM public.submit_media_render(%s,'synthetic-evidence',%s,1200,'smoke')",
                         (org_id, asset["id"])).fetchone()
    queue = LocalQueue(root / "queue" / "tasks.sqlite3")
    dispatcher = Dispatcher(dsn)
    dispatched = None
    for _ in range(1000):
        dispatched = dispatcher.dispatch_one(queue)
        tasks = [task for task in queue.pending(1000) if task["job_id"] == str(job["id"])]
        if tasks:
            break
        if dispatched is None:
            raise RuntimeError("Synthetic demo outbox was not available")
    else:
        raise RuntimeError("Synthetic demo outbox scan exceeded its bound")
    worker = MediaWorker(dsn, store, root / "scratch")
    completed = worker.run(tasks[0]["job_id"])
    if completed["state"] != "completed":
        raise RuntimeError("Synthetic demo did not complete")
    queue.ack(tasks[0]["event_id"])
    manifest = completed["output_manifest"]
    summary = {"job_id": str(job["id"]), "organization_id": str(org_id),
               "state": completed["state"], "dispatch": dispatched,
               "manifest": manifest,
               "output_files": [str(root / "objects" / item["key"]) for item in manifest["outputs"]]}
    output = root / "summary.json"
    output.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
    output.chmod(0o600)
    print(json.dumps(summary, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
