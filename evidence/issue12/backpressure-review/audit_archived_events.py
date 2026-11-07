"""Read-only reanalysis of the archived PR9 CI upload benchmark, not a new load run."""
from collections import Counter, defaultdict
from datetime import datetime
import hashlib
import json
from pathlib import Path
import statistics

ROOT = Path(__file__).parent
ARCHIVE = Path('/workspace/g3a1-evidence/final-head-ci.zip')
BASE = ROOT / 'archived-ci/ltc-g3a1-evidence/shared-benchmark'

def distribution(values):
    values = sorted(values)
    def pct(q):
        if not values:
            return None
        position = (len(values)-1)*q
        lo = int(position)
        hi = min(len(values)-1, lo+1)
        return values[lo] + (values[hi]-values[lo])*(position-lo)
    return {'observations': len(values), 'p50': pct(.5), 'p95': pct(.95), 'p99': pct(.99)}

def main():
    summary = json.loads((BASE / 'summary.json').read_text())
    events = [json.loads(line) for line in (BASE/'events.jsonl').read_text().splitlines()]
    result = {
        'evidence_kind': 'REANALYSIS_OF_ARCHIVED_CI_NOT_NEW_LOAD_RUN',
        'source_sha': summary['runtime']['source_sha'],
        'source_worktree_clean': summary['runtime']['source_worktree_clean'],
        'archive_sha256': hashlib.sha256(ARCHIVE.read_bytes()).hexdigest(),
        'cases': [],
    }
    for case in summary['cases'][:2]:
        selected = [row for row in events if row['event']=='http_attempt' and row['case']==case['case']]
        operations = defaultdict(list)
        for row in selected:
            operations[row['operation']].append(row)
        for rows in operations.values():
            assert [row['attempt'] for row in rows] == list(range(1,len(rows)+1))
            assert len(rows) <= 16
            assert all(row['status']==503 for row in rows[:-1])
            assert rows[-1]['status'] != 503
            assert len(set(row['replica_pid'] for row in rows)) == 1
        assert len(selected)==case['http_attempts']
        assert len(operations)==case['logical_operations']
        assert dict(Counter(str(row['status']) for row in selected))==case['http_status_counts']
        assert dict(Counter(str(rows[0]['status']) for rows in operations.values()))==case['initial_status_counts']
        assert dict(Counter(str(rows[-1]['status']) for rows in operations.values()))==case['final_status_counts']
        assert sum(len(rows)-1 for rows in operations.values())==case['backpressure_retries']
        recalculated=distribution([row['elapsed_seconds'] for row in selected])
        assert recalculated==case['http_attempt_latency_seconds']
        by_status = {status: distribution([row['elapsed_seconds'] for row in selected if str(row['status'])==status])
                     for status in sorted(case['http_status_counts'])}
        by_replica = {}
        for pid in sorted(set(row['replica_pid'] for row in selected)):
            rows=[row for row in selected if row['replica_pid']==pid]
            op_rows=[rs for rs in operations.values() if rs[0]['replica_pid']==pid]
            by_replica[str(pid)] = {
                'logical_operations': len(op_rows),
                'physical_attempts': len(rows),
                'status_counts': dict(sorted(Counter(str(row['status']) for row in rows).items())),
                'max_attempts_for_one_operation': max(map(len,op_rows)),
            }
        result['cases'].append({
            **case,
            'event_counts_and_attempt_latency_match_summary': True,
            'attempt_count_histogram': dict(sorted(Counter(str(len(rows)) for rows in operations.values()).items())),
            'max_attempts_for_one_operation': max(map(len,operations.values())),
            'physical_to_logical_amplification': len(selected)/len(operations),
            'physical_503_fraction': sum(row['status']==503 for row in selected)/len(selected),
            'logical_initial_503_fraction': sum(rows[0]['status']==503 for rows in operations.values())/len(operations),
            'attempt_latency_by_status_seconds': by_status,
            'replicas': by_replica,
        })
    samples=[row for row in events if row['event']=='resource_sample']
    times=[datetime.fromisoformat(row['at']).timestamp() for row in samples]
    gaps=[b-a for a,b in zip(times,times[1:])]
    resource=summary['resources']
    result['resources']={**resource,
        'total_measured_client_and_reaped_child_cpu_seconds':sum(resource[k] for k in resource if k.endswith('_seconds') and k!='elapsed_seconds' and k!='sampling_interval_seconds'),
        'actual_sample_gap_seconds': {**distribution(gaps), 'max': max(gaps)},
        'postgres_cpu_and_memory_included': False,
        'per_cohort_or_auth_cpu_available': False,
    }
    output=ROOT/'archived-ci-reanalysis.json'
    output.write_text(json.dumps(result,indent=2,sort_keys=True)+'\n')
    print(json.dumps(result,indent=2,sort_keys=True))

if __name__ == '__main__':
    main()
