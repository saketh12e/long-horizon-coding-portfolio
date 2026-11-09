"""Small external fixtures and outcome checks for the trusted controller.

Only trusted pyarrow writes fixtures here; candidate operations always go through
the existing Harness subprocess boundary.  This module exports ADVANCED_CASES
without changing the base registry or the controller.
"""
import hashlib
import json
import struct

from cases import Case, SCHEMA, ID, CV, bykey, records, write
from controller import CANDIDATE_UID, canonical, own


I64_MAX = (1 << 63) - 1
RID = '_fixture_stable_id'
RCV = '_fixture_stable_version'
ID_PROPERTY = 'delta.rowTracking.materializedRowIdColumnName'
CV_PROPERTY = 'delta.rowTracking.materializedRowCommitVersionColumnName'
PHYSICAL = {
    'key': 'col-00000000-0000-0000-0000-000000000001',
    'value': 'col-00000000-0000-0000-0000-000000000002',
    'flag': 'col-00000000-0000-0000-0000-000000000003',
}
Z85 = '0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ.-:+=^!/*?&<>()[]{}@%$#'
STAMP = 1700000000000


def _new_fixture(h, name):
    path = h.new('advanced-' + name)
    path.mkdir(mode=0o700)
    (path / '_delta_log').mkdir(mode=0o700)
    return path


def _protocol(*, supported=True, mapped=False, dv=False):
    if not supported:
        return {'protocol': {'minReaderVersion': 1, 'minWriterVersion': 2}}
    writers = ['rowTracking', 'domainMetadata']
    readers = []
    if mapped:
        writers.append('columnMapping')
        readers.append('columnMapping')
    if dv:
        writers.append('deletionVectors')
        readers.append('deletionVectors')
    protocol = {'minReaderVersion': 3 if readers else 1,
                'minWriterVersion': 7, 'writerFeatures': writers}
    if readers:
        protocol['readerFeatures'] = readers
    return {'protocol': protocol}


def _metadata(*, enabled=True, supported=True, mapped=False, dv=False):
    fields = []
    for index, (name, kind) in enumerate(SCHEMA, 1):
        mapping = {'delta.columnMapping.id': index,
                   'delta.columnMapping.physicalName': PHYSICAL[name]} if mapped else {}
        fields.append({'name': name, 'type': {'int64': 'long', 'bool': 'boolean',
                                             'string': 'string'}[kind],
                       'nullable': True, 'metadata': mapping})
    config = {}
    if supported:
        config.update({'delta.enableRowTracking': 'true' if enabled else 'false',
                       ID_PROPERTY: RID, CV_PROPERTY: RCV})
    if mapped:
        config.update({'delta.columnMapping.mode': 'name',
                       'delta.columnMapping.maxColumnId': '3'})
    if dv:
        config['delta.enableDeletionVectors'] = 'true'
    return {'metaData': {
        'id': '00000000-0000-0000-0000-000000000042',
        'format': {'provider': 'parquet', 'options': {}},
        'schemaString': json.dumps({'type': 'struct', 'fields': fields}),
        'partitionColumns': [], 'configuration': config, 'createdTime': STAMP,
    }}


def _domain(high_water):
    return {'domainMetadata': {'domain': 'delta.rowTracking', 'removed': False,
                              'configuration': json.dumps({'rowIdHighWaterMark': high_water})}}


def _commit(path, version, actions):
    (path / '_delta_log' / f'{version:020d}.json').write_text(
        ''.join(json.dumps(action, allow_nan=False) + '\n' for action in actions))


def _add_file(path, name, rows, *, base=None, version=None, mapped=False, dv=None):
    # These imports resolve in the trusted controller, never in the candidate
    # venv.  Two-row groups expose accidental ordinal resets cheaply.
    import pyarrow as pa
    import pyarrow.parquet as pq

    fields, physical_rows = [], []
    types = {'int64': pa.int64(), 'bool': pa.bool_(), 'string': pa.string()}
    for index, (logical, kind) in enumerate(SCHEMA, 1):
        physical = PHYSICAL[logical] if mapped else logical
        field_metadata = {b'PARQUET:field_id': str(index).encode()} if mapped else None
        fields.append(pa.field(physical, types[kind], metadata=field_metadata))
    hidden = [name for name in (RID, RCV) if any(name in row for row in rows)]
    fields.extend(pa.field(name, pa.int64()) for name in hidden)
    for row in rows:
        physical_rows.append({**{PHYSICAL[k] if mapped else k: row[k] for k, _ in SCHEMA},
                              **{k: row.get(k) for k in hidden}})
    target = path / name
    pq.write_table(pa.Table.from_pylist(physical_rows, schema=pa.schema(fields)),
                   target, row_group_size=2, compression='NONE')
    add = {'path': name, 'partitionValues': {}, 'size': target.stat().st_size,
           'modificationTime': STAMP, 'dataChange': True,
           'stats': json.dumps({'numRecords': len(rows), 'tightBounds': dv is None})}
    if base is not None:
        add.update({'baseRowId': base, 'defaultRowCommitVersion': version})
    if dv is not None:
        add['deletionVector'] = dv
    return {'add': add}


def _remove(add, *, data_change=True):
    return {'remove': {**{key: value for key, value in add['add'].items()
                         if key in ('path', 'partitionValues', 'size', 'deletionVector',
                                    'baseRowId', 'defaultRowCommitVersion', 'tags')},
                       'deletionTimestamp': STAMP + 1,
                       'extendedFileMetadata': True, 'dataChange': data_change}}


def _publish(path):
    # h.new owns only its parent; root-created table/log/Parquet entries also
    # need candidate ownership under the controller's restrictive umask.
    own(path, CANDIDATE_UID)


def _fingerprints(path, pattern):
    return {p.relative_to(path).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in path.rglob(pattern)}


def _expect_rows(actual, expected, message):
    assert canonical(actual['rows']) == canonical(expected), message


def _ordinary(h, path, expected):
    result = h.operation(path, 'read')
    assert [(f['name'], f['type']) for f in result['schema']] == [tuple(f) for f in SCHEMA], \
        'ordinary read leaked hidden columns or lost logical names/types'
    _expect_rows(result, [{k: row[k] for k, _ in SCHEMA} for row in expected],
                 'ordinary user values differ from fixture/independent reconstruction')
    return result


def _projection(h, path, artifact, columns, predicate, selected):
    projected = h.operation(path, 'read', include_row_tracking=True,
                            columns=columns, filters=predicate)
    assert [f['name'] for f in projected['schema']] == columns + [ID, CV]
    assert all(f['type'] == 'int64' and f['nullable'] is False
               for f in projected['schema'][-2:]), 'projected metadata lost its Arrow contract'
    expected = [{k: row[k] for k in columns + [ID, CV]}
                for row in artifact['rows'] if selected(row)]
    _expect_rows(projected, expected, 'projection/filter changed physical identity alignment')


def _reject_unchanged(h, path, op, before, *, expected_error=None, **kwargs):
    logs = _fingerprints(path / '_delta_log', '*.json')
    reply = h.command(path, op, **kwargs)
    assert reply['ok'] is False, f'{op} unexpectedly accepted invalid tracking state/allocation'
    if expected_error is not None:
        assert reply['error']['type'] == expected_error, \
            f'{op} raised {reply["error"]["type"]}, expected {expected_error}'
    assert _fingerprints(path / '_delta_log', '*.json') == logs, \
        'rejected request published or changed a transaction log'
    actual = _ordinary(h, path, before['rows'])
    assert actual['version'] == before['version'], 'rejected request advanced the version'


def name_mapping_materialized(h):
    path = _new_fixture(h, 'name-mapping')
    original = _add_file(path, 'old.parquet', records(11, 12), base=17, version=0, mapped=True)
    _commit(path, 0, [_protocol(mapped=True), _metadata(mapped=True), _domain(18), original])
    # A real rewrite history: row 12 is copied, row 11 is updated, and four
    # rows are inserted. Null materialized values must fall back independently.
    rows = records(12, 13, 11, 14, 15, 16)
    rows[0].update({RID: 18, RCV: 0})
    rows[2].update({RID: 17, RCV: None, 'value': 'already-updated'})
    rows[3].update({'value': None, 'flag': None})
    rewritten = _add_file(path, 'mapped.parquet', rows, base=101, version=1, mapped=True)
    _commit(path, 1, [_remove(original), rewritten, _domain(106)])
    _publish(path)
    expected = [{**{k: row[k] for k, _ in SCHEMA},
                 ID: row.get(RID) if row.get(RID) is not None else 101 + ordinal,
                 CV: row.get(RCV) if row.get(RCV) is not None else 1}
                for ordinal, row in enumerate(rows)]
    before, artifact = h.tracked(path)
    _expect_rows(artifact, expected, 'mapped fixture reconstruction')
    _ordinary(h, path, expected)
    _projection(h, path, artifact, ['flag', 'value'], 'key >= 13', lambda r: r['key'] >= 13)
    data = _fingerprints(path, '*.parquet')
    assert h.operation(path, 'enable')['version'] == before['version'], 'enabled mapped table changed'
    assert _fingerprints(path, '*.parquet') == data, 'idempotent enable rewrote mapped data'
    h.operation(path, 'update', updates={'value': "'mapped-update'"}, predicate='key = 13')
    after, state = h.tracked(path)
    assert after['version'] > before['version']
    expected = [{**r, **({'value': 'mapped-update', CV: after['version']}
                         if r['key'] == 13 else {})} for r in expected]
    _expect_rows(after, expected, 'mapped update lost stable IDs, versions, or NULL user values')
    assert state['configuration']['delta.columnMapping.mode'] == 'name'
    _ordinary(h, path, expected)


def _inline_dv(ordinals):
    # Portable RoaringBitmapArray: one high-32 bucket, one no-run array
    # container. Its offset is relative to the start of the roaring bitmap.
    values = sorted(set(ordinals))
    assert values and 0 <= values[0] <= values[-1] < 65536
    payload = (struct.pack('<IQI', 1681511377, 1, 0)
               + struct.pack('<IIHHI', 12346, 1, 0, len(values) - 1, 16)
               + struct.pack('<' + 'H' * len(values), *values))
    padded = payload + b'\x00' * (-len(payload) % 4)
    encoded = []
    for offset in range(0, len(padded), 4):
        value = int.from_bytes(padded[offset:offset + 4], 'big')
        block = []
        for _ in range(5):
            value, digit = divmod(value, 85)
            block.append(Z85[digit])
        encoded.append(''.join(reversed(block)))
    return {'storageType': 'i', 'pathOrInlineDv': ''.join(encoded),
            'sizeInBytes': len(payload), 'cardinality': len(values)}


def inline_dv_physical_ordinals(h):
    path = _new_fixture(h, 'inline-dv')
    rows = records(40, 10, 70, 20, 60, 30, 80, 50)
    deleted = {0, 2, 5}  # Leading hole, interior holes, and multiple row groups.
    add = _add_file(path, 'dv.parquet', rows, base=503, version=0, dv=_inline_dv(deleted))
    _commit(path, 0, [_protocol(dv=True), _metadata(dv=True), _domain(510), add])
    _publish(path)
    expected = [{**row, ID: 503 + ordinal, CV: 0}
                for ordinal, row in enumerate(rows) if ordinal not in deleted]
    before, artifact = h.tracked(path)
    _expect_rows(artifact, expected, 'DV fixture must retain physical ordinals')
    _ordinary(h, path, expected)
    _projection(h, path, artifact, ['value'], 'key >= 50', lambda r: r['key'] >= 50)
    h.operation(path, 'update', updates={'value': "'dv-update'"}, predicate='key = 60')
    after, _ = h.tracked(path)
    assert after['version'] > before['version']
    expected = [{**r, **({'value': 'dv-update', CV: after['version']}
                         if r['key'] == 60 else {})} for r in expected]
    _expect_rows(after, expected, 'DV rewrite renumbered/resurrected rows or changed untouched versions')
    _ordinary(h, path, expected)


def supported_writes_enable_preservation(h):
    path = _new_fixture(h, 'supported-writes')
    add = _add_file(path, 'supported.parquet', records(1, 2, 3), base=31, version=0)
    _commit(path, 0, [_protocol(), _metadata(enabled=False), _domain(80), add])
    _publish(path)
    initial = h.inspect(path)
    assert initial['supported'] and not initial['enabled']
    _expect_rows(initial, [{**r, ID: 31 + i, CV: 0} for i, r in enumerate(records(1, 2, 3))],
                 'supported fixture allocation')
    _reject_unchanged(h, path, 'read', initial, include_row_tracking=True)
    appended_version = write(h, path, records(4, 5), mode='append')['version']
    appended = h.inspect(path)
    assert appended_version > initial['version'] and not appended['enabled']
    _ordinary(h, path, records(1, 2, 3, 4, 5))
    for key in (1, 2, 3):
        assert bykey(appended)[key] == bykey(initial)[key], 'append changed existing allocation'
    assert set(bykey(appended)) == {1, 2, 3, 4, 5}
    for key in (4, 5):
        row = bykey(appended)[key]
        assert row[ID] > initial['high_water'] and row[CV] == appended_version
    overwritten_version = write(h, path, records(6, 7), mode='overwrite')['version']
    before = h.inspect(path)
    assert overwritten_version > appended_version and not before['enabled']
    assert set(bykey(before)) == {6, 7}
    for row in before['rows']:
        assert row[ID] > appended['high_water'] and row[CV] == overwritten_version
    _ordinary(h, path, records(6, 7))
    data = _fingerprints(path, '*.parquet')
    enabled_version = h.operation(path, 'enable')['version']
    after, state = h.tracked(path)
    assert enabled_version == before['version'] + 1 and after['version'] == enabled_version
    _expect_rows(after, before['rows'], 'enablement replaced valid supported-state IDs/versions')
    assert state['enabled'] and state['high_water'] == before['high_water']
    assert canonical(state['files']) == canonical(before['files']), 'enablement replaced valid Add defaults'
    for key in (ID_PROPERTY, CV_PROPERTY):
        assert state['configuration'][key] == before['configuration'][key]
    assert _fingerprints(path, '*.parquet') == data, 'enablement changed data paths/bytes'
    assert h.operation(path, 'enable')['version'] == enabled_version
    _expect_rows(h.tracked(path)[0], before['rows'], 'repeat enablement changed supported allocations')


def partial_backfill_preserves_allocations(h):
    path = _new_fixture(h, 'partial-backfill')
    first = _add_file(path, 'allocated.parquet', records(1, 2))
    missing = _add_file(path, 'missing.parquet', records(3, 4))
    _commit(path, 0, [_protocol(supported=False), _metadata(supported=False), first, missing])
    # The files predate feature support. A valid intermediate backfill assigns
    # one file but leaves the other without defaults; no new data is inserted.
    allocated = {'add': {**first['add'], 'baseRowId': 100,
                         'defaultRowCommitVersion': 1, 'dataChange': False}}
    # Metadata-only backfill replaces the Add defaults for the same file.
    # A Remove for that same path in this commit would retire the file.
    _commit(path, 1, [_protocol(), _metadata(enabled=False), _domain(150), allocated])
    _publish(path)
    _expect_rows(h.inspect(path, version=0), records(1, 2, 3, 4), 'pre-feature fixture values')
    before = _ordinary(h, path, records(1, 2, 3, 4))
    assert before['version'] == 1
    _reject_unchanged(h, path, 'read', before, include_row_tracking=True)
    data = _fingerprints(path, '*.parquet')
    version = h.operation(path, 'enable')['version']
    after, artifact = h.tracked(path)
    assert version == 2 and after['version'] == version
    assert set(bykey(after)) == {1, 2, 3, 4}
    for key in (1, 2):
        assert bykey(after)[key] == {**records(key)[0], ID: 99 + key, CV: 1}, \
            'backfill replaced an already valid allocation/version'
    for key in (3, 4):
        row = bykey(after)[key]
        assert row[ID] > 150 and row[CV] == version, 'missing fields were not freshly backfilled'
    assert artifact['high_water'] >= max(r[ID] for r in after['rows'])
    files = {f['path']: f for f in artifact['files']}
    assert files['allocated.parquet']['base'] == 100 and files['allocated.parquet']['default'] == 1
    assert _fingerprints(path, '*.parquet') == data, 'partial backfill rewrote/moved Parquet'
    _ordinary(h, path, records(1, 2, 3, 4))


def allocation_overflow_atomicity(h):
    path = _new_fixture(h, 'overflow')
    add = _add_file(path, 'near-limit.parquet', records(1), base=I64_MAX - 1, version=0)
    _commit(path, 0, [_protocol(), _metadata(), _domain(I64_MAX - 1), add])
    _publish(path)
    before, artifact = h.tracked(path)
    _expect_rows(artifact, [{**records(1)[0], ID: I64_MAX - 1, CV: 0}], 'near-limit fixture')
    # The first ID would fit, but the second would wrap. Failure must leave the
    # complete snapshot intact; orphan, unreferenced data files are permitted.
    _reject_unchanged(h, path, 'write', before, expected_error='DeltaProtocolError',
                      schema=SCHEMA, rows=records(2, 3), mode='append')
    after, state = h.tracked(path)
    _expect_rows(after, before['rows'], 'overflow changed the visible snapshot')
    assert state['high_water'] == artifact['high_water']
    version = write(h, path, records(2), mode='append')['version']
    full, maximum = h.tracked(path)
    assert version > before['version'] and full['version'] == version
    _expect_rows(full, before['rows'] + [{**records(2)[0], ID: I64_MAX, CV: version}],
                 'the final representable signed int64 ID must remain usable')
    assert maximum['high_water'] == I64_MAX
    _reject_unchanged(h, path, 'write', full, expected_error='DeltaProtocolError',
                      schema=SCHEMA, rows=records(3), mode='append')
    final, state = h.tracked(path)
    _expect_rows(final, full['rows'], 'exhausted allocator changed stable identities')
    assert state['high_water'] == I64_MAX


ADVANCED_CASES = [
    Case('advanced-name-mapping-materialized', 'fail-to-pass',
         'Name-mode physical mapping, materialized precedence, NULL fallback, projection and update',
         name_mapping_materialized),
    Case('advanced-inline-dv-physical-ordinals', 'fail-to-pass',
         'Inline DV holes retain physical file ordinals across row groups, filters and rewrites',
         inline_dv_physical_ordinals),
    Case('advanced-supported-writes-enable-preservation', 'fail-to-pass',
         'Supported-not-enabled writes allocate, metadata reads reject, enable preserves valid state',
         supported_writes_enable_preservation),
    Case('advanced-partial-backfill-preservation', 'fail-to-pass',
         'Enablement backfills only missing Add fields without replacing valid allocations/data',
         partial_backfill_preserves_allocations),
    Case('advanced-allocation-overflow-atomicity', 'fail-to-pass',
         'Overflow rejects without a commit; final signed int64 ID remains usable',
         allocation_overflow_atomicity),
]
