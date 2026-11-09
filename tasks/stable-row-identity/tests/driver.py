"""Public primitive command adapter. No expectations, fixtures or scoring."""
import json
from pathlib import Path
import sys


def execute(req):
    import deltalake
    import deltalake._internal as native
    import pyarrow as pa
    from deltalake import DeltaTable, write_deltalake
    origin = str(Path(deltalake.__file__).resolve())
    extension = str(Path(native.__file__).resolve())
    if req['op'] == 'origin':
        return {'python': origin, 'extension': extension}
    path = req['path']
    op = req['op']
    if op in ('write', 'merge'):
        schema = pa.schema([(name, {'int64': pa.int64(), 'bool': pa.bool_(), 'string': pa.string()}[kind]) for name, kind in req['schema']])
        data = pa.Table.from_pylist(req['rows'], schema=schema)
    if op == 'write':
        kwargs = {k: req[k] for k in ('mode', 'partition_by', 'configuration', 'predicate', 'target_file_size') if k in req}
        write_deltalake(path, data, **kwargs)
    else:
        dt = DeltaTable(path, version=req.get('version'))
        if op == 'enable':
            dt.alter.enable_row_tracking()
        elif op == 'update':
            dt.update(updates=req['updates'], predicate=req.get('predicate'))
        elif op == 'delete':
            dt.delete(predicate=req.get('predicate'))
        elif op == 'merge':
            builder = dt.merge(data, req['predicate'], source_alias='s', target_alias='t')
            for clause in req['clauses']:
                builder = getattr(builder, clause['method'])(**clause.get('args', {}))
            builder.execute()
        elif op == 'compact':
            dt.optimize.compact(target_size=req.get('target_size', 1048576))
        elif op == 'zorder':
            dt.optimize.z_order(req['columns'], target_size=req.get('target_size', 1048576))
        elif op == 'checkpoint':
            dt.create_checkpoint()
        elif op == 'restore':
            dt.restore(req['target'])
        elif op == 'properties':
            dt.alter.set_table_properties(req['properties'])
        elif op == 'read':
            kwargs = {k: req[k] for k in ('columns', 'filters', 'engine', 'include_row_tracking') if k in req}
            kwargs.setdefault('engine', 'datafusion')
            table = dt.to_pyarrow_table(**kwargs)
            return {'version': dt.version(), 'rows': table.to_pylist(), 'schema': [{'name': f.name, 'type': str(f.type), 'nullable': f.nullable} for f in table.schema]}
        else:
            raise ValueError('unknown adapter operation')
    return {'version': DeltaTable(path).version()}


def main():
    req = json.loads(sys.stdin.buffer.read(1048577))
    try:
        result = {'ok': True, 'result': execute(req)}
    except Exception as error:
        result = {'ok': False, 'error': {'type': type(error).__name__, 'message': str(error)[:4096]}}
    print(json.dumps(result, allow_nan=False))


if __name__ == '__main__':
    main()
