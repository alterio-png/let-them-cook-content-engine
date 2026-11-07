"""Supplemental independent SQL permission matrix, composite FK and quota checks."""
import concurrent.futures
import json
from pathlib import Path
import time
from uuid import uuid4
import psycopg
from psycopg import sql

from sql_adversarial_review import ADMIN, ORG, OTHER_ORG, USER, PEER, BODY, SHA, OUT, actor, rpc, query, expect_error, record, checks

def main():
    with psycopg.connect(ADMIN) as db:
        roles = ['anon', 'authenticated', 'engine_worker', 'g3a1_upload_reconciler']
        privileges = []
        for role in roles:
            for table in ['upload_limits', 'upload_reservations', 'upload_receipts', 'upload_events']:
                for privilege in ['SELECT', 'INSERT', 'UPDATE', 'DELETE', 'TRUNCATE', 'REFERENCES', 'TRIGGER']:
                    actual = db.execute('SELECT has_table_privilege(%s,%s,%s)', (role, 'g3a1_private.'+table, privilege)).fetchone()[0]
                    expected = role == 'authenticated' and table != 'upload_events' and privilege == 'SELECT'
                    assert actual == expected, (role, table, privilege, actual, expected)
                    privileges.append({'role':role,'table':table,'privilege':privilege,'granted':actual})
        (OUT/'privilege-matrix.json').write_text(json.dumps(privileges, indent=2)+'\n')
        record('all 112 table privilege cells match explicit read-only owner policy boundary')

        # Restore a synthetic revoked owner strictly to reach immutability constraints via trusted owner SQL.
        db.execute('INSERT INTO auth.users(id) VALUES(%s)', (USER,))
        db.execute("INSERT INTO public.memberships(organization_id,user_id,role) VALUES(%s,%s,'editor')", (ORG, USER))
        reservation = db.execute("SELECT id,object_key,size_bytes,sha256 FROM g3a1_private.upload_reservations WHERE organization_id=%s AND request_key='temp-shadow'", (ORG,)).fetchone()
        current_bytes, current_count = db.execute('SELECT used_bytes,used_count FROM g3a1_private.upload_limits WHERE organization_id=%s', (ORG,)).fetchone()
        db.execute('UPDATE g3a1_private.upload_limits SET max_bytes=%s,max_upload_bytes=%s,max_count=%s WHERE organization_id=%s', (current_bytes, current_bytes, current_count, ORG))
    expect_error('already uploaded+uncertain+reserved cumulative count prevents new admission', '54000', lambda: rpc('reserve_upload', ORG, 'cumulative-cap-denied', len(BODY), SHA, 120, user=PEER))

    rid, key, size, digest = reservation
    def mismatch(org=ORG, owner=USER, obj=key, amount=size, sha=digest):
        with psycopg.connect(ADMIN) as db:
            db.execute('INSERT INTO g3a1_private.upload_receipts(id,organization_id,owner_user_id,object_key,size_bytes,sha256) VALUES(%s,%s,%s,%s,%s,%s)', (rid,org,owner,obj,amount,sha))
    for name, kwargs in [('owner', {'owner':PEER}), ('size', {'amount':size+1}), ('SHA', {'sha':'f'*64}), ('org', {'org':OTHER_ORG,'obj':f'{OTHER_ORG}/uploads/{rid}/unvalidated.bin'})]:
        expect_error('composite receipt FK rejects mismatched '+name, '23503', lambda kwargs=kwargs: mismatch(**kwargs))

    # Distinct requests must share one authoritative count and byte cap.
    fresh_org = str(uuid4())
    with psycopg.connect(ADMIN) as db:
        db.execute("INSERT INTO public.organizations(id,name) VALUES(%s,'Issue12 quota contention synthetic')", (fresh_org,))
        db.execute("INSERT INTO public.memberships(organization_id,user_id,role) VALUES(%s,%s,'editor')", (fresh_org,PEER))
        db.execute('INSERT INTO g3a1_private.upload_limits(organization_id,max_bytes,max_count,max_upload_bytes,max_ttl_seconds,max_lease_seconds) VALUES(%s,100,3,100,120,20)', (fresh_org,))
    def distinct(n):
        try:
            grant=rpc('reserve_upload', fresh_org, 'distinct-'+str(n),len(BODY),SHA,120,user=PEER)
            return ('admitted',grant['id'])
        except psycopg.Error as error:
            assert error.sqlstate=='54000'
            return ('quota_denied',error.sqlstate)
    with concurrent.futures.ThreadPoolExecutor(max_workers=12) as pool:
        outcome=list(pool.map(distinct,range(12)))
    assert sum(x[0]=='admitted' for x in outcome)==3
    assert sum(x[0]=='quota_denied' for x in outcome)==9
    with psycopg.connect(ADMIN) as db:
        actual=db.execute('SELECT used_bytes,used_count FROM g3a1_private.upload_limits WHERE organization_id=%s',(fresh_org,)).fetchone()
    assert actual==(3*len(BODY),3),actual
    record('12 concurrent distinct SQL requests serialize on shared cap: 3 admitted, 9 denied, no byte/count overrun',committed_bytes=actual[0],committed_count=actual[1])

    # A separate one-operation quota makes accidental duplicate charging fail visibly.
    duplicate_org = str(uuid4())
    with psycopg.connect(ADMIN) as db:
        db.execute("INSERT INTO public.organizations(id,name) VALUES(%s,'Issue12 duplicate contention synthetic')",(duplicate_org,))
        db.execute("INSERT INTO public.memberships(organization_id,user_id,role) VALUES(%s,%s,'editor')",(duplicate_org,PEER))
        db.execute('INSERT INTO g3a1_private.upload_limits(organization_id,max_bytes,max_count,max_upload_bytes,max_ttl_seconds,max_lease_seconds) VALUES(%s,100,1,100,120,20)',(duplicate_org,))
    def duplicate(_):
        grant=rpc('reserve_upload',duplicate_org,'same-key',len(BODY),SHA,1,user=PEER)
        return {key:grant[key] for key in ['id','object_key','expires_at','fence','state']}
    with concurrent.futures.ThreadPoolExecutor(max_workers=12) as pool:
        duplicates=list(pool.map(duplicate,range(24)))
    assert all(item==duplicates[0] for item in duplicates)
    with psycopg.connect(ADMIN) as db:
        actual=db.execute('SELECT used_bytes,used_count,(SELECT count(*) FROM g3a1_private.upload_reservations WHERE organization_id=%s) FROM g3a1_private.upload_limits WHERE organization_id=%s',(duplicate_org,duplicate_org)).fetchone()
    assert actual==(len(BODY),1,1)
    record('24 concurrent identical SQL requests preserve one grant, original expiry, immutable key, one byte/count charge')
    claimed=rpc('claim_upload',duplicate_org,duplicates[0]['id'],1,user=PEER)
    rpc('mark_upload_uncertain',duplicate_org,claimed['id'],claimed['lease_token'],claimed['fence'],'SYNTHETIC_UNCERTAINTY',user=PEER)
    time.sleep(1.1)
    assert rpc('expire_unused_uploads',duplicate_org,1000,user=PEER)==0
    with psycopg.connect(ADMIN) as db:
        assert db.execute('SELECT used_bytes,used_count FROM g3a1_private.upload_limits WHERE organization_id=%s',(duplicate_org,)).fetchone()==(len(BODY),1)
    record('claimed uncertain operation remains charged after original TTL and lease; no unused expiry refund')
    (OUT/'constraint-quota-results.json').write_text(json.dumps({'checks':checks,'count':len(checks)},indent=2,default=str)+'\n')
    print('ALL SUPPLEMENTAL SQL CHECKS PASSED',flush=True)

if __name__=='__main__':
    main()
