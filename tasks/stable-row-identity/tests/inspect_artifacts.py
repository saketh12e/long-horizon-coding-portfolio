"""Independent Delta/Parquet decoder, run as inspector, never as scorer.

No delta-rs imports. Input is a controller-owned immutable snapshot. Implemented
from the pinned public protocol; materialized column names come from metadata.
"""
import json
from pathlib import Path
import struct
import sys
from urllib.parse import unquote, urlparse
import uuid
import zlib

I64_MAX = (1 << 63) - 1
Z85 = '0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ.-:+=^!/*?&<>()[]{}@%$#'


def z85_decode(text):
    if len(text) % 5 or len(text) > 8 * 1024 * 1024:
        raise ValueError('invalid Z85 length')
    result = bytearray()
    for offset in range(0, len(text), 5):
        value = 0
        for char in text[offset:offset + 5]:
            value = value * 85 + Z85.index(char)
        result.extend(value.to_bytes(4, 'big'))
    return bytes(result)


def bitmap(data):
    pos = 0
    def read(fmt):
        nonlocal pos
        size = struct.calcsize(fmt)
        value = struct.unpack_from(fmt, data, pos)
        pos += size
        return value[0] if len(value) == 1 else value
    if read('<I') != 1681511377:
        raise ValueError('unsupported deletion-vector magic')
    buckets = read('<Q')
    if buckets > 1024:
        raise ValueError('too many DV buckets')
    result = set()
    for _ in range(buckets):
        high = read('<I')
        if high > 0x7fffffff:
            raise ValueError('negative DV bucket')
        cookie = read('<I')
        has_run = cookie & 0xffff == 12347
        if has_run:
            count = (cookie >> 16) + 1
            runs = data[pos:pos + (count + 7) // 8]
            pos += len(runs)
        elif cookie == 12346:
            count = read('<I')
            runs = b''
        else:
            raise ValueError('invalid roaring cookie')
        if count > 65536:
            raise ValueError('too many roaring containers')
        headers = [read('<HH') for _ in range(count)]
        if not has_run or count >= 4:
            for _ in headers:
                read('<I')
        for index, (key, cardinality_minus_one) in enumerate(headers):
            cardinality = cardinality_minus_one + 1
            values = []
            if has_run and runs[index // 8] & (1 << (index % 8)):
                for _ in range(read('<H')):
                    start, length = read('<HH')
                    values.extend(range(start, start + length + 1))
            elif cardinality <= 4096:
                values = [read('<H') for _ in range(cardinality)]
            else:
                for word in range(1024):
                    bits = read('<Q')
                    while bits:
                        bit = (bits & -bits).bit_length() - 1
                        values.append(word * 64 + bit)
                        bits &= bits - 1
            if len(set(values)) != cardinality:
                raise ValueError('invalid roaring cardinality')
            result.update((high << 32) | (key << 16) | low for low in values)
            if len(result) > 100000:
                raise ValueError('DV is too large for bounded fixtures')
    if pos != len(data):
        raise ValueError('trailing DV bytes')
    return result


def local_path(root, text):
    parsed = urlparse(text)
    if parsed.scheme or parsed.netloc:
        raise ValueError('nonrelative artifact path')
    rel = unquote(text)
    if not rel or rel.startswith('/') or any(p in ('', '.', '..') for p in rel.split('/')) or '\\' in rel:
        raise ValueError('unsafe relative artifact path')
    path = root / rel
    # All snapshot entries were captured with O_NOFOLLOW; retain a second
    # boundary check before opening paths derived from untrusted metadata.
    if not path.resolve().is_relative_to(root.resolve()):
        raise ValueError('artifact path escapes snapshot')
    return path


def deleted_rows(root, descriptor):
    if not descriptor:
        return set()
    kind = descriptor['storageType']
    size = descriptor['sizeInBytes']
    if not isinstance(size, int) or not 0 <= size <= 4 * 1024 * 1024:
        raise ValueError('DV size limit')
    if kind == 'i':
        data = z85_decode(descriptor['pathOrInlineDv'])[:size]
    elif kind == 'u':
        encoded = descriptor['pathOrInlineDv']
        prefix, identity = encoded[:-20], encoded[-20:]
        name = 'deletion_vector_' + str(uuid.UUID(bytes=z85_decode(identity))) + '.bin'
        path = local_path(root, (prefix + '/' if prefix else '') + name)
        content = path.read_bytes()
        offset = descriptor.get('offset', 0)
        if content[0] != 1 or offset < 1 or offset + size + 8 > len(content):
            raise ValueError('invalid DV record bounds')
        length = struct.unpack_from('>I', content, offset)[0]
        data = content[offset + 4:offset + 4 + length]
        crc = struct.unpack_from('>I', content, offset + 4 + length)[0]
        if length != size or zlib.crc32(data) != crc:
            raise ValueError('DV size/checksum mismatch')
    else:
        raise ValueError('absolute/external DV path outside portable local fixture profile')
    result = bitmap(data)
    if len(result) != descriptor['cardinality']:
        raise ValueError('DV cardinality mismatch')
    return result


def parse_json(text):
    def pairs(items):
        out = {}
        for key, value in items:
            if key in out:
                raise ValueError('duplicate JSON key')
            out[key] = value
        return out
    return json.loads(text, object_pairs_hook=pairs, parse_constant=lambda _: (_ for _ in ()).throw(ValueError('nonfinite JSON')))


def dv_key(action):
    d = action.get('deletionVector')
    return (action['path'], json.dumps(d, sort_keys=True) if d else '')


def decode(root, version=None):
    import pyarrow.parquet as pq
    root = Path(root)
    log = root / '_delta_log'
    commits = sorted((int(p.stem), p) for p in log.glob('*.json') if len(p.stem) == 20 and p.stem.isdigit())
    if not commits:
        raise ValueError('no JSON transaction log')
    latest = max(v for v, _ in commits) if version is None else version
    active = {}
    metadata = protocol = None
    high_water = None
    checkpoint_version = -1
    # A checkpoint is used when old JSON commits are absent, so checkpoint
    # correctness is observable after intentional log truncation in a case.
    if commits[0][0] != 0:
        checkpoints = []
        for path in log.glob('*.checkpoint.parquet'):
            try:
                cv = int(path.name.split('.')[0])
            except ValueError:
                continue
            if cv <= latest:
                checkpoints.append((cv, path))
        if not checkpoints:
            raise ValueError('missing initial log and checkpoint')
        checkpoint_version, checkpoint = max(checkpoints)
        initial = pq.ParquetFile(checkpoint).read().to_pylist()
    else:
        initial = []
    history = []
    assigned = {}
    last_high = -1
    batches = [(checkpoint_version, initial)] if initial else []
    for v, path in commits:
        if checkpoint_version < v <= latest:
            actions = [parse_json(line) for line in path.read_text().splitlines() if line.strip()]
            batches.append((v, actions))
    for v, actions in batches:
        for action in actions:
            if action.get('protocol') is not None:
                protocol = action['protocol']
            if action.get('metaData') is not None:
                metadata = action['metaData']
            domain = action.get('domainMetadata')
            if domain and domain['domain'] == 'delta.rowTracking' and not domain.get('removed', False):
                high_water = parse_json(domain['configuration'])['rowIdHighWaterMark']
                if type(high_water) is not int or not -1 <= high_water <= I64_MAX or high_water < last_high:
                    raise ValueError('invalid/decreasing allocation high water mark')
        for action in actions:
            if action.get('remove') is not None:
                removed = action['remove']
                previous = active.pop(dv_key(removed), None)
                if previous is not None:
                    for field in ('baseRowId', 'defaultRowCommitVersion'):
                        if previous.get(field) is not None and removed.get(field) != previous[field]:
                            raise ValueError('removal lost historical tracking field: ' + field)
        new_ranges = []
        for action in actions:
            add = action.get('add')
            if add is None:
                continue
            key = dv_key(add)
            base, default = add.get('baseRowId'), add.get('defaultRowCommitVersion')
            if base is not None:
                if type(base) is not int or base < 0 or type(default) is not int or not 0 <= default <= v:
                    raise ValueError('invalid default tracking metadata')
                count = pq.ParquetFile(local_path(root, add['path'])).metadata.num_rows
                end = base + count - 1
                if end > I64_MAX or high_water is None or high_water < end:
                    raise ValueError('allocation overflows/exceeds high water mark')
                old = assigned.get(add['path'])
                if old is not None and old != (base, default):
                    raise ValueError('recommitted file changed default tracking metadata')
                if old is None and v != checkpoint_version:
                    if base <= last_high or default != v:
                        raise ValueError('fresh allocation reuses history or has stale commit version')
                    if count:
                        new_ranges.append((base, end))
                assigned[add['path']] = (base, default)
            active[key] = add
        new_ranges.sort()
        if any(b[0] <= a[1] for a, b in zip(new_ranges, new_ranges[1:])):
            raise ValueError('overlapping fresh allocation ranges')
        if high_water is not None:
            last_high = high_water
        history.append({'version': v, 'high_water': high_water})
    if metadata is None or protocol is None:
        raise ValueError('incomplete Delta metadata/protocol')
    config = metadata.get('configuration') or {}
    if isinstance(config, list):
        config = dict(config)
    enabled = config.get('delta.enableRowTracking') == 'true'
    supported = 'rowTracking' in (protocol.get('writerFeatures') or [])
    if enabled:
        if protocol['minWriterVersion'] < 7 or not {'rowTracking', 'domainMetadata'} <= set(protocol.get('writerFeatures') or []):
            raise ValueError('missing row-tracking protocol dependencies')
        for name in ('materializedRowIdColumnName', 'materializedRowCommitVersionColumnName'):
            if not config.get('delta.rowTracking.' + name):
                raise ValueError('missing materialized metadata column name')
    schema = parse_json(metadata['schemaString'])['fields']
    logical = [field['name'] for field in schema]
    physical = [field.get('metadata', {}).get('delta.columnMapping.physicalName', field['name']) for field in schema]
    id_col = config.get('delta.rowTracking.materializedRowIdColumnName')
    version_col = config.get('delta.rowTracking.materializedRowCommitVersionColumnName')
    if enabled and (id_col == version_col or id_col in logical + physical or version_col in logical + physical):
        raise ValueError('tracking metadata columns collide with user schema')
    rows = []
    files = []
    for add in active.values():
        path = local_path(root, add['path'])
        parquet = pq.ParquetFile(path)
        if parquet.metadata.num_rows > 100000:
            raise ValueError('too many fixture rows')
        content = parquet.read().to_pylist()
        partitions = add.get('partitionValues') or {}
        if isinstance(partitions, list):
            partitions = dict(partitions)
        deleted = deleted_rows(root, add.get('deletionVector'))
        if any(i >= len(content) for i in deleted):
            raise ValueError('DV ordinal outside file')
        files.append({'path': add['path'], 'rows': len(content), 'base': add.get('baseRowId'), 'default': add.get('defaultRowCommitVersion')})
        for ordinal, raw in enumerate(content):
            if ordinal in deleted:
                continue
            row = {name: raw.get(phys, partitions.get(phys, partitions.get(name))) for name, phys in zip(logical, physical)}
            if supported:
                if add.get('baseRowId') is None or add.get('defaultRowCommitVersion') is None:
                    raise ValueError('active tracked file lacks defaults')
                rid = raw.get(id_col)
                commit = raw.get(version_col)
                row['__delta_row_id'] = rid if rid is not None else add['baseRowId'] + ordinal
                row['__delta_row_commit_version'] = commit if commit is not None else add['defaultRowCommitVersion']
                if any(type(row[k]) is not int or not 0 <= row[k] <= I64_MAX for k in ('__delta_row_id', '__delta_row_commit_version')):
                    raise ValueError('invalid reconstructed metadata')
                if row['__delta_row_commit_version'] > latest:
                    raise ValueError('row commit version from future')
            rows.append(row)
    if supported and len({r['__delta_row_id'] for r in rows}) != len(rows):
        raise ValueError('duplicate active stable row IDs')
    return {'version': latest, 'enabled': enabled, 'supported': supported, 'rows': rows, 'files': files, 'high_water': high_water, 'history': history, 'configuration': config}


if __name__ == '__main__':
    request = json.loads(sys.stdin.buffer.read(1048577))
    try:
        value = {'ok': True, 'result': decode(request['path'], request.get('version'))}
    except Exception as error:
        value = {'ok': False, 'error': {'type': type(error).__name__, 'message': str(error)[:4096]}}
    print(json.dumps(value, allow_nan=False))
