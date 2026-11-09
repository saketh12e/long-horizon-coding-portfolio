"""Outcome relationships and expectations; readable only by score controller."""
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import shutil

from controller import FeatureFailure, canonical, run_uid, CANDIDATE_UID, BUILD

SCHEMA = [['key', 'int64'], ['value', 'string'], ['flag', 'bool']]
ID = '__delta_row_id'
CV = '__delta_row_commit_version'


@dataclass(frozen=True)
class Case:
    name: str
    classification: str
    contract: str
    run: object


def records(*keys):
    return [{'key': key, 'value': f'v{key}', 'flag': key % 2 == 0} for key in keys]


def write(h, p, rows, **kwargs):
    return h.operation(p, 'write', schema=SCHEMA, rows=rows, **kwargs)


def enabled(h, name, keys=(1, 2, 3, 4)):
    p = h.new(name)
    write(h, p, records(*keys))
    h.operation(p, 'enable')
    result, artifact = h.tracked(p)
    assert artifact['enabled']
    return p, result, artifact


def bykey(result):
    return {row['key']: row for row in result['rows']}


def unchanged(before, after, keys):
    a, b = bykey(before), bykey(after)
    for key in keys:
        assert a[key] == b[key], f'untouched row {key} changed identity/version/value'


def fresh(before, after, keys, version):
    old = {row[ID] for row in before['rows']}
    actual = bykey(after)
    for key in keys:
        assert actual[key][ID] not in old, 'insert reused a stable ID'
        assert actual[key][CV] == version, 'insert version differs from successful commit'


def ordinary_regression(h):
    p = h.new('ordinary')
    write(h, p, records(1, 2, 3))
    result = h.operation(p, 'read')
    assert canonical(result['rows']) == canonical(records(1, 2, 3))
    assert all(not f['name'].startswith('__delta_row_') for f in result['schema'])
    h.operation(p, 'update', updates={'value': "'edited'"}, predicate='key = 2')
    h.operation(p, 'delete', predicate='key = 1')
    write(h, p, records(4), mode='append')
    result = h.operation(p, 'read')
    expected = records(2, 3, 4)
    expected[0]['value'] = 'edited'
    assert canonical(result['rows']) == canonical(expected)
    h.operation(p, 'checkpoint')
    assert canonical(h.operation(p, 'read')['rows']) == canonical(expected)


def isolation(h):
    code = '''import os,json
denied=[]
for path in ['/tests/controller.py','/logs/verifier/reward.txt','/run/controller/private-canary']:
 try:
  with open(path,'wb') as f: f.write(b'bad')
 except PermissionError: denied.append(path)
print(json.dumps({'uid':os.getuid(),'denied':denied,'socket':os.path.exists('/var/run/docker.sock')}))
'''
    raw, _ = run_uid(CANDIDATE_UID, ['/opt/python/bin/python', '-I', '-c', code])
    reply = json.loads(raw)
    assert reply['uid'] == CANDIDATE_UID and len(reply['denied']) == 3 and not reply['socket'], 'candidate isolation/access denial failed'


def backfill(h):
    p = h.new('backfill')
    write(h, p, records(1, 2, 3))
    files = {q.relative_to(p).as_posix(): hashlib.sha256(q.read_bytes()).hexdigest() for q in p.rglob('*.parquet')}
    old_version = h.operation(p, 'read')['version']
    commit = h.operation(p, 'enable')['version']
    assert commit == old_version + 1, 'backfill must be one successful transition'
    actual, _ = h.tracked(p)
    assert len(actual['rows']) == 3
    assert all(r[CV] == commit for r in actual['rows'])
    after = {q.relative_to(p).as_posix(): hashlib.sha256(q.read_bytes()).hexdigest() for q in p.rglob('*.parquet')}
    assert files == after, 'enablement rewrote/moved data files'
    again = h.operation(p, 'enable')['version']
    assert again == commit
    assert canonical(h.tracked(p)[0]['rows']) == canonical(actual['rows'])


def empty_and_create(h):
    p = h.new('empty')
    write(h, p, [])
    h.operation(p, 'enable')
    value, artifact = h.tracked(p)
    assert value['rows'] == [] and artifact['enabled']
    p2 = h.new('create-enabled')
    write(h, p2, records(10, 20), configuration={'delta.enableRowTracking': 'true'})
    value, artifact = h.tracked(p2)
    assert len(value['rows']) == 2 and artifact['enabled']
    assert all(r[CV] == value['version'] for r in value['rows'])


def append_duplicate_values(h):
    p, before, _ = enabled(h, 'append-duplicate')
    # Duplicate user values are independent rows, not an identity hash.
    write(h, p, records(2, 2), mode='append')
    after, _ = h.tracked(p)
    assert len(after['rows']) == len(before['rows']) + 2
    assert len({r[ID] for r in after['rows']}) == len(after['rows'])
    same = [r for r in after['rows'] if r['key'] == 2]
    assert len(same) == 3 and sum(r[CV] == after['version'] for r in same) == 2


def update_and_noop_assignment(h):
    p, before, _ = enabled(h, 'update')
    h.operation(p, 'update', updates={'value': "'changed'"}, predicate='key IN (1,3)')
    after, _ = h.tracked(p)
    unchanged(before, after, [2, 4])
    for key in (1, 3):
        assert bykey(after)[key][ID] == bykey(before)[key][ID]
        assert bykey(after)[key][CV] == after['version']
        assert bykey(after)[key]['value'] == 'changed'
    h.operation(p, 'update', updates={'value': 'value'}, predicate='key = 2')
    same, _ = h.tracked(p)
    unchanged(after, same, [1, 3, 4])
    assert bykey(same)[2][ID] == bykey(after)[2][ID]
    assert bykey(same)[2][CV] == same['version']


def deletes(h):
    p, before, _ = enabled(h, 'delete')
    h.operation(p, 'delete', predicate='key IN (1,3)')
    after, _ = h.tracked(p)
    assert set(bykey(after)) == {2, 4}
    unchanged(before, after, [2, 4])
    write(h, p, records(10, 11), mode='append')
    mid, _ = h.tracked(p)
    h.operation(p, 'delete', predicate='key >= 10')
    final, _ = h.tracked(p)
    unchanged(mid, final, [2, 4])


def overwrite(h):
    p, before, history = enabled(h, 'overwrite')
    write(h, p, records(1, 2), mode='overwrite')
    after, state = h.tracked(p)
    assert set(bykey(after)) == {1, 2}
    fresh(before, after, [1, 2], after['version'])
    assert state['high_water'] >= history['high_water']


def partitions(h):
    p = h.new('partition')
    write(h, p, records(1, 2, 3), partition_by=['value'])
    h.operation(p, 'enable')
    before, _ = h.tracked(p)
    replacement = [{'key': 20, 'value': 'v2', 'flag': True}]
    write(h, p, replacement, mode='overwrite', predicate="value = 'v2'")
    after, _ = h.tracked(p)
    unchanged(before, after, [1, 3])
    fresh(before, after, [20], after['version'])
    assert set(bykey(after)) == {1, 3, 20}


def mixed_merge(h):
    p, before, _ = enabled(h, 'merge')
    h.operation(p, 'merge', schema=SCHEMA, rows=records(1, 2, 5), predicate='t.key = s.key', clauses=[
        {'method': 'when_matched_delete', 'args': {'predicate': 's.key = 2'}},
        {'method': 'when_matched_update', 'args': {'updates': {'value': "'merged'", 'flag': 's.flag'}}},
        {'method': 'when_not_matched_insert_all'},
    ])
    after, _ = h.tracked(p)
    assert set(bykey(after)) == {1, 3, 4, 5}
    unchanged(before, after, [3, 4])
    assert bykey(after)[1][ID] == bykey(before)[1][ID]
    assert bykey(after)[1][CV] == after['version']
    assert bykey(after)[1]['value'] == 'merged'
    fresh(before, after, [5], after['version'])


def compaction(h):
    p, _, _ = enabled(h, 'compact')
    for keys in ((5, 6), (7, 8), (9, 10)):
        write(h, p, records(*keys), mode='append')
    h.operation(p, 'update', updates={'flag': 'true'}, predicate='key = 3')
    before, _ = h.tracked(p)
    h.operation(p, 'compact')
    after, _ = h.tracked(p)
    assert canonical(after['rows']) == canonical(before['rows'])
    h.operation(p, 'zorder', columns=['key', 'value'])
    final, _ = h.tracked(p)
    assert canonical(final['rows']) == canonical(before['rows'])


def projection_nulls(h):
    p = h.new('nulls')
    write(h, p, [{'key': 1, 'value': None, 'flag': None}, {'key': 2, 'value': 'x', 'flag': True}, {'key': 3, 'value': 'x', 'flag': False}])
    h.operation(p, 'enable')
    full, _ = h.tracked(p)
    projected = h.operation(p, 'read', include_row_tracking=True, columns=['value'], filters='key >= 2')
    assert [f['name'] for f in projected['schema']] == ['value', ID, CV]
    expected = [{k: r[k] for k in ('value', ID, CV)} for r in full['rows'] if r['key'] >= 2]
    assert canonical(projected['rows']) == canonical(expected)
    ordinary = h.operation(p, 'read')
    assert all(ID not in r and CV not in r for r in ordinary['rows'])
    assert len(ordinary['rows']) == 3


def history_restore(h):
    p, original, state = enabled(h, 'restore')
    target = original['version']
    write(h, p, records(5, 6), mode='append')
    later, later_state = h.tracked(p)
    h.operation(p, 'update', updates={'value': "'future'"}, predicate='key <= 2')
    historical, _ = h.tracked(p, version=target)
    assert canonical(historical['rows']) == canonical(original['rows'])
    h.operation(p, 'restore', target=target)
    restored, restored_state = h.tracked(p)
    assert canonical(restored['rows']) == canonical(original['rows'])
    assert restored_state['high_water'] >= later_state['high_water']
    write(h, p, records(7), mode='append')
    final, _ = h.tracked(p)
    fresh(later, final, [7], final['version'])
    unchanged(original, final, [1, 2, 3, 4])


def checkpoint_relocation(h):
    p, before, _ = enabled(h, 'checkpoint')
    write(h, p, records(5, 6), mode='append')
    h.operation(p, 'update', updates={'value': "'checkpointed'"}, predicate='key = 1')
    before, _ = h.tracked(p)
    h.operation(p, 'checkpoint')
    # Delete earlier JSON logs only after a complete classic checkpoint;
    # reference retained data files remain available. Both readers must replay it.
    checkpoints = list((p / '_delta_log').glob('*.checkpoint.parquet'))
    assert checkpoints, 'classic checkpoint was not written'
    cv = max(int(q.name.split('.')[0]) for q in checkpoints)
    for q in (p / '_delta_log').glob('*.json'):
        if len(q.stem) == 20 and q.stem.isdigit() and int(q.stem) < cv:
            q.unlink()
    moved = p.parent / 'relocated'
    p.rename(moved)
    assert not p.exists()
    after, _ = h.tracked(moved)
    assert canonical(after['rows']) == canonical(before['rows'])


def native_parity(h):
    p, _, _ = enabled(h, 'native')
    h.operation(p, 'update', updates={'value': "'native'"}, predicate='key = 1')
    h.tracked(p, native=True)


def invalid_requests(h):
    p = h.new('invalid')
    write(h, p, records(1))
    before = h.operation(p, 'read')
    missing = h.command(p, 'read', include_row_tracking=True)
    assert missing['ok'] is False, 'unenabled metadata read silently succeeded'
    assert h.operation(p, 'read') == before
    h.operation(p, 'enable')
    enabled_version = h.operation(p, 'read')['version']
    reply = h.command(p, 'read', engine='pyarrow', include_row_tracking=True)
    assert not reply['ok'] and reply['error']['type'] == 'ValueError'
    for properties in ({'delta.enableRowTracking': 'false'}, {'delta.rowTrackingSuspended': 'true'}):
        reply = h.command(p, 'properties', properties=properties)
        assert not reply['ok'], 'excluded row-tracking transition was committed'
        assert h.operation(p, 'read')['version'] == enabled_version


def reserved_columns(h):
    for name in (ID, CV):
        p = h.new('reserved-' + name)
        schema = [['key', 'int64'], [name, 'int64']]
        h.operation(p, 'write', schema=schema, rows=[{'key': 1, name: 40}])
        before = h.operation(p, 'read')
        reply = h.command(p, 'enable')
        assert not reply['ok'], 'conflicting reserved schema accepted'
        assert reply['error']['type'] == 'DeltaProtocolError', 'missing enable API is not a reserved-schema rejection'
        assert h.operation(p, 'read') == before


def restore_untracked_rejected(h):
    p = h.new('restore-untracked')
    write(h, p, records(1))
    target = h.operation(p, 'read')['version']
    h.operation(p, 'enable')
    before, _ = h.tracked(p)
    reply = h.command(p, 'restore', target=target)
    assert not reply['ok']
    after, _ = h.tracked(p)
    assert after == before


CASES = [
    Case('isolation-access-denials', 'pass-to-pass', 'Isolation and source policy', isolation),
    Case('ordinary-untracked-regressions', 'pass-to-pass', 'Tables without rowTracking retain behavior', ordinary_regression),
    Case('backfill-idempotence-data-bytes', 'fail-to-pass', 'Enablement and physical representation', backfill),
    Case('empty-and-creation-enable', 'fail-to-pass', 'Empty enablement and existing writer initialization', empty_and_create),
    Case('duplicate-inserts-allocate', 'fail-to-pass', 'Equal values and append fresh identities', append_duplicate_values),
    Case('update-noop-assignment', 'fail-to-pass', 'UPDATE selected/unselected row semantics', update_and_noop_assignment),
    Case('delete-survivor-preservation', 'fail-to-pass', 'DELETE partial and whole-file semantics', deletes),
    Case('overwrite-fresh-identities', 'fail-to-pass', 'Overwrite inserts and allocation history', overwrite),
    Case('partition-replacement', 'fail-to-pass', 'String partitions and replacement', partitions),
    Case('mixed-merge', 'fail-to-pass', 'MERGE update/insert/delete/untouched', mixed_merge),
    Case('compaction-zorder', 'fail-to-pass', 'Rewrite metadata multiset preservation', compaction),
    Case('projection-filter-nulls', 'fail-to-pass', 'Read ordinals, projection/filter and NULL', projection_nulls),
    Case('historical-restore-allocation', 'fail-to-pass', 'Historical reads, restore, high water and later append', history_restore),
    Case('checkpoint-reopen-relocation', 'fail-to-pass', 'Checkpoint replay, fresh process and portability', checkpoint_relocation),
    Case('native-python-artifact-parity', 'fail-to-pass', 'Native LoadBuilder scan_table parity', native_parity),
    Case('invalid-and-excluded-requests', 'fail-to-pass', 'Explicit documented errors and excluded transitions', invalid_requests),
    Case('reserved-output-schema', 'fail-to-pass', 'Reserved user column rejection without commit', reserved_columns),
    Case('restore-untracked-rejected', 'fail-to-pass', 'Enabled-to-untracked restore rejection', restore_untracked_rejected),
]
