"""Concurrent appends through independent handles, checked from saved artifacts."""
import json

from cases import Case, enabled, unchanged, write, records
from controller import run_uid, CANDIDATE_UID, BUILD


def concurrent_appends(h):
    p, before, _ = enabled(h, 'concurrent-appends')
    # Each worker loads a snapshot before either starts its append. The barrier
    # coordinates ordinary public API calls and requires no implementation hook.
    script = r'''
import concurrent.futures, json, sys, threading
import pyarrow as pa
from deltalake import DeltaTable, write_deltalake
barrier = threading.Barrier(2, timeout=30)
def append(key):
    table = DeltaTable(sys.argv[1])
    barrier.wait()
    write_deltalake(table, pa.table({'key': pa.array([key], type=pa.int64()),
        'value': ['v'+str(key)], 'flag': [key % 2 == 0]}), mode='append')
    return key
with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
    print(json.dumps(sorted(pool.map(append, [20, 21]))))
'''
    raw, _ = run_uid(CANDIDATE_UID, [BUILD / 'venv/bin/python', '-I', '-c', script, p], timeout=120)
    assert json.loads(raw) == [20, 21], 'both concurrent appends must complete'
    after, artifact = h.tracked(p)
    unchanged(before, after, [1, 2, 3, 4])
    assert len(after['rows']) == 6
    inserted = [r for r in after['rows'] if r['key'] in (20, 21)]
    assert len(inserted) == 2
    assert len({r['__delta_row_id'] for r in after['rows']}) == 6
    assert {r['__delta_row_commit_version'] for r in inserted} == {before['version'] + 1, before['version'] + 2}
    assert artifact['high_water'] >= max(r['__delta_row_id'] for r in after['rows'])


CONCURRENT_CASES = [Case('concurrent-independent-appends', 'fail-to-pass',
    'Independent handles retry allocations and use successful commit versions', concurrent_appends)]
