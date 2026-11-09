"""Trusted bounded capture. Never import code from captured source/artifacts."""
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import stat

MAX_FILE = 32 * 1024 * 1024
MAX_TOTAL = 256 * 1024 * 1024
MAX_FILES = 10000


class UnsafeArtifact(ValueError):
    pass


def relative_path(value):
    if not isinstance(value, str) or not value or '\\' in value or '\x00' in value:
        raise UnsafeArtifact('invalid artifact path')
    parts = value.split('/')
    if any(p in ('', '.', '..') for p in parts) or PurePosixPath(value).is_absolute():
        raise UnsafeArtifact('noncanonical artifact path')
    return parts


def production_path(path):
    parts = relative_path(path)
    if len(parts) >= 4 and parts[:3] == ['crates', 'core', 'src']:
        return path.endswith('.rs')
    if len(parts) >= 3 and parts[:2] == ['python', 'src']:
        return path.endswith('.rs')
    if len(parts) >= 3 and parts[:2] == ['python', 'deltalake']:
        return path.endswith(('.py', '.pyi'))
    return False


def _copy_tree_fd(src_fd, dest, predicate, prefix, budget, records):
    # Each opened descriptor pins the directory/file being inspected. No path
    # is reopened after hashing; content is copied from that same descriptor.
    for name in sorted(os.listdir(src_fd)):
        relative_path(name)
        rel = f'{prefix}/{name}' if prefix else name
        st = os.stat(name, dir_fd=src_fd, follow_symlinks=False)
        if stat.S_ISLNK(st.st_mode) or not (stat.S_ISDIR(st.st_mode) or stat.S_ISREG(st.st_mode)):
            raise UnsafeArtifact('link or special file: ' + rel)
        if stat.S_ISDIR(st.st_mode):
            if name in ('__pycache__', '.git', '.venv', 'target'):
                continue
            fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=src_fd)
            try:
                _copy_tree_fd(fd, dest, predicate, rel, budget, records)
            finally:
                os.close(fd)
        elif predicate(rel):
            fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=src_fd)
            try:
                opened = os.fstat(fd)
                if not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1:
                    raise UnsafeArtifact('nonregular or multiply linked file: ' + rel)
                if opened.st_size > MAX_FILE or len(records) >= MAX_FILES:
                    raise UnsafeArtifact('artifact exceeds file budget')
                out = dest / rel
                out.parent.mkdir(parents=True, exist_ok=True)
                digest = hashlib.sha256()
                size = 0
                with out.open('xb') as output:
                    while True:
                        block = os.read(fd, 65536)
                        if not block:
                            break
                        size += len(block)
                        budget[0] += len(block)
                        if size > MAX_FILE or budget[0] > MAX_TOTAL:
                            raise UnsafeArtifact('artifact exceeds byte budget')
                        digest.update(block)
                        output.write(block)
                after = os.fstat(fd)
                if (opened.st_size, opened.st_mtime_ns, opened.st_ctime_ns) != (after.st_size, after.st_mtime_ns, after.st_ctime_ns):
                    raise UnsafeArtifact('artifact changed during capture')
                out.chmod(0o400)
                records.append({'path': rel, 'size': size, 'sha256': digest.hexdigest()})
            finally:
                os.close(fd)


def capture(source, dest, predicate=lambda _: True):
    dest = Path(dest)
    dest.mkdir(mode=0o700, parents=True, exist_ok=False)
    fd = os.open(source, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    records = []
    try:
        _copy_tree_fd(fd, dest, predicate, '', [0], records)
    finally:
        os.close(fd)
    return records


def primitive_json(raw, limit=4 * 1024 * 1024):
    if len(raw) > limit:
        raise UnsafeArtifact('JSON reply too large')
    def pairs(entries):
        result = {}
        for key, val in entries:
            if key in result:
                raise UnsafeArtifact('duplicate JSON key')
            result[key] = val
        return result
    value = json.loads(raw, parse_constant=lambda _: (_ for _ in ()).throw(UnsafeArtifact('nonfinite JSON')), object_pairs_hook=pairs)
    def check(v, depth=0):
        if depth > 32:
            raise UnsafeArtifact('JSON nesting too deep')
        if type(v) in (str, int, bool, type(None)):
            return
        if type(v) is list:
            if len(v) > 100000:
                raise UnsafeArtifact('too many JSON elements')
            for item in v:
                check(item, depth + 1)
        elif type(v) is dict:
            for item in v.values():
                check(item, depth + 1)
        else:
            raise UnsafeArtifact('unexpected JSON primitive')
    check(value)
    return value
