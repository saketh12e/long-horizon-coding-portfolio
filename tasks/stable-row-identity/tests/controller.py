"""Trusted controller. Candidate imports/build hooks never run in this process."""
import ctypes
import json
import os
from pathlib import Path
import resource
import shutil
import signal
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parent))

from security import capture, primitive_json, production_path

ROOT = Path('/work')
LOG = Path('/logs/verifier')
BUILD = ROOT / 'candidate'
SOURCE = BUILD / 'source'
CASES = ROOT / 'cases'
HELPERS = Path('/opt/helpers')
CANDIDATE_UID = 2000
INSPECTOR_UID = 2001


class InfrastructureError(RuntimeError):
    pass


class FeatureFailure(AssertionError):
    pass


def own(path, uid):
    for parent, directories, files in os.walk(path):
        os.chown(parent, uid, uid)
        for name in files:
            os.chown(Path(parent) / name, uid, uid, follow_symlinks=False)


def stop_uid(uid):
    subprocess.run(['pkill', '-KILL', '-u', str(uid)], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def run_uid(uid, command, *, request=None, timeout=120, cwd=None, build=False):
    env = {
        'PATH': '/opt/python/bin:/usr/local/cargo/bin:/usr/bin:/bin',
        'HOME': str(BUILD if uid == CANDIDATE_UID else ROOT / 'inspect-home'),
        'LANG': 'C.UTF-8', 'PYTHONDONTWRITEBYTECODE': '1',
        'CARGO_HOME': '/usr/local/cargo', 'RUSTUP_HOME': '/usr/local/rustup',
        'CARGO_TARGET_DIR': str(BUILD / 'target'), 'CARGO_NET_OFFLINE': 'true',
        'CARGO_BUILD_JOBS': '2', 'CARGO_PROFILE_DEV_DEBUG': '0',
        'CARGO_PROFILE_TEST_DEBUG': '0', 'TMPDIR': str(BUILD / 'tmp' if uid == CANDIDATE_UID else ROOT / 'inspect-home'),
        'RUSTFLAGS': '-C linker=clang -C link-arg=-fuse-ld=lld',
    }
    argv = ['setpriv', '--reuid', str(uid), '--regid', str(uid), '--clear-groups', '--bounding-set=-all', '--inh-caps=-all', '--ambient-caps=-all', '--no-new-privs', '--'] + list(map(str, command))
    def limits():
        os.umask(0o077)
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
        resource.setrlimit(resource.RLIMIT_NOFILE, (1024, 1024))
        resource.setrlimit(resource.RLIMIT_NPROC, (1024, 1024))
        if uid == INSPECTOR_UID:
            resource.setrlimit(resource.RLIMIT_AS, (3 * 1024**3, 3 * 1024**3))
        bound = 2 * 1024**3 if build else 64 * 1024**2
        resource.setrlimit(resource.RLIMIT_FSIZE, (bound, bound))
    with tempfile.TemporaryFile(dir=LOG) as output, tempfile.TemporaryFile(dir=LOG) as errors:
        process = subprocess.Popen(argv, stdin=subprocess.PIPE if request is not None else subprocess.DEVNULL, stdout=output, stderr=errors, cwd=cwd or ROOT, env=env, close_fds=True, start_new_session=True, preexec_fn=limits)
        try:
            process.communicate(json.dumps(request).encode() if request is not None else None, timeout=timeout)
        except subprocess.TimeoutExpired:
            stop_uid(uid)
            process.wait(timeout=10)
            raise InfrastructureError(f'bounded child timeout ({timeout}s)')
        finally:
            stop_uid(uid)
        output.seek(0)
        errors.seek(0)
        out = output.read(4 * 1024 * 1024 + 1)
        err = errors.read(4 * 1024 * 1024 + 1)
    if len(out) > 4 * 1024 * 1024 or len(err) > 4 * 1024 * 1024:
        raise InfrastructureError('child output limit exceeded')
    if process.returncode:
        raise InfrastructureError(f'child exit {process.returncode}: {err[-6000:].decode(errors="replace")}')
    return out, err


class Harness:
    def __init__(self):
        self.counter = 0
        self.native_ready = False

    def command(self, path, op, **kwargs):
        raw, _ = run_uid(CANDIDATE_UID, [BUILD / 'venv/bin/python', '-I', HELPERS / 'driver.py'], request={'path': str(path), 'op': op, **kwargs})
        reply = primitive_json(raw)
        if not isinstance(reply, dict) or type(reply.get('ok')) is not bool:
            raise InfrastructureError('malformed adapter envelope')
        if not reply['ok']:
            error = reply.get('error')
            if not isinstance(error, dict) or not isinstance(error.get('type'), str) or not isinstance(error.get('message'), str):
                raise InfrastructureError('malformed typed exception')
        elif not isinstance(reply.get('result'), dict):
            raise InfrastructureError('malformed operation result')
        return reply

    def operation(self, path, op, **kwargs):
        reply = self.command(path, op, **kwargs)
        if not reply['ok']:
            raise FeatureFailure(f'{op}: {reply["error"]}')
        return reply['result']

    def new(self, name):
        path = CASES / name
        path.mkdir(mode=0o700)
        own(path, CANDIDATE_UID)
        return path / 'table'

    def snapshot(self, path):
        stop_uid(CANDIDATE_UID)
        self.counter += 1
        dest = ROOT / 'snapshots' / str(self.counter)
        manifest = capture(path, dest)
        (LOG / f'snapshot-{self.counter}.json').write_text(json.dumps(manifest))
        own(dest, INSPECTOR_UID)
        for parent, dirs, files in os.walk(dest):
            Path(parent).chmod(0o500)
        return dest

    def inspect(self, path, version=None):
        snapshot = self.snapshot(path)
        raw, _ = run_uid(INSPECTOR_UID, ['/opt/python/bin/python', '-I', HELPERS / 'inspect_artifacts.py'], request={'path': str(snapshot), 'version': version})
        value = primitive_json(raw)
        if not value.get('ok'):
            raise FeatureFailure(f'independent artifact decoder: {value.get("error")}')
        return value['result']

    def tracked(self, path, version=None, native=False):
        result = self.operation(path, 'read', include_row_tracking=True, version=version)
        if not isinstance(result.get('rows'), list) or not isinstance(result.get('schema'), list):
            raise InfrastructureError('malformed read result')
        names = [f['name'] for f in result['schema']]
        if names[-2:] != ['__delta_row_id', '__delta_row_commit_version']:
            raise FeatureFailure('metadata output order/names')
        for field in result['schema'][-2:]:
            if field['type'] != 'int64' or field['nullable'] is not False:
                raise FeatureFailure('metadata must be non-null Arrow int64')
        artifact = self.inspect(path, version)
        if canonical(result['rows']) != canonical(artifact['rows']):
            raise FeatureFailure('Python metadata differs from persisted independent reconstruction')
        if native:
            self.native_compare(path, result['rows'], version)
        return result, artifact

    def native_compare(self, path, rows, version=None):
        if not self.native_ready:
            helper = SOURCE / 'crates/core/examples/row_tracking_probe.rs'
            helper.parent.mkdir(exist_ok=True)
            helper.parent.chmod(0o755)
            os.chown(helper.parent, CANDIDATE_UID, CANDIDATE_UID)
            shutil.copyfile(HELPERS / 'native_probe.rs', helper)
            os.chown(helper, CANDIDATE_UID, CANDIDATE_UID)
            run_uid(CANDIDATE_UID, ['cargo', 'build', '--locked', '--offline', '-p', 'deltalake-core', '--features', 'datafusion', '--example', 'row_tracking_probe'], timeout=3600, cwd=SOURCE, build=True)
            self.native_ready = True
        args = [BUILD / 'target/debug/examples/row_tracking_probe', 'read', path]
        if version is not None:
            args.append(str(version))
        raw, _ = run_uid(CANDIDATE_UID, args)
        actual = primitive_json(raw)
        if canonical(actual.get('rows', [])) != canonical(rows):
            raise FeatureFailure('native Rust load metadata differs from Python/artifacts')


def canonical(rows):
    return sorted(json.dumps(row, sort_keys=True, separators=(',', ':'), allow_nan=False) for row in rows)


def prepare():
    if os.getuid() != 0:
        raise InfrastructureError('controller must own isolated root verifier identity')
    ctypes.CDLL(None).prctl(4, 0, 0, 0, 0)  # PR_SET_DUMPABLE=0
    control = Path('/run/controller')
    control.mkdir(exist_ok=True)
    control.chmod(0o700)
    ROOT.mkdir(exist_ok=True)
    ROOT.chmod(0o711)
    for name in ('candidate', 'cases', 'snapshots', 'inspect-home'):
        (ROOT / name).mkdir(mode=0o700)
    os.chmod(ROOT / 'snapshots', 0o711)
    own(ROOT / 'inspect-home', INSPECTOR_UID)
    shutil.copytree('/opt/baseline', SOURCE)
    # The image cache contains only the pristine baseline. Cargo still checks
    # fingerprints and recompiles candidate production changes independently.
    if Path('/opt/baseline-target').is_dir():
        subprocess.run(['cp', '-a', '--reflink=auto', '/opt/baseline-target', BUILD / 'target'], check=True)
    # Artifact roots are exactly the paths configured in task.toml. Harbor's
    # separate environment places configured artifacts at their original paths.
    transferred = Path('/app/delta-rs')
    overlay = ROOT / 'overlay'
    manifest = capture(transferred, overlay, production_path)
    if not manifest:
        raise InfrastructureError('empty/missing candidate production export')
    present = {entry['path'] for entry in manifest}
    for path in list(SOURCE.rglob('*')):
        if path.is_file() and production_path(path.relative_to(SOURCE).as_posix()) and path.relative_to(SOURCE).as_posix() not in present:
            path.unlink()
    for entry in manifest:
        target = SOURCE / entry['path']
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(overlay / entry['path'], target)
        target.chmod(0o600)
    (LOG / 'source-overlay.json').write_text(json.dumps(manifest, indent=2))
    (BUILD / 'tmp').mkdir()
    own(BUILD, CANDIDATE_UID)
    own(CASES, CANDIDATE_UID)
    HELPERS.mkdir(exist_ok=True)
    # test.sh sets umask 077; the candidate and inspector UIDs must be able to
    # traverse this directory to reach the read-only helpers inside it.
    HELPERS.chmod(0o755)
    for name in ('driver.py', 'inspect_artifacts.py', 'native_probe.rs'):
        source = Path('/tests') / name
        shutil.copyfile(source, HELPERS / name)
        (HELPERS / name).chmod(0o444)
    run_uid(CANDIDATE_UID, ['/opt/python/bin/python', '-m', 'venv', '--system-site-packages', BUILD / 'venv'], timeout=120)
    # venvs do not chain: --system-site-packages exposes the base interpreter,
    # not /opt/python itself. Link the trusted /opt/python packages (pyarrow,
    # maturin helpers) into the candidate venv via a .pth entry.
    site_packages = next((BUILD / 'venv' / 'lib').glob('python3.*/site-packages'))
    site_packages.mkdir(parents=True, exist_ok=True)
    trusted = next(Path('/opt/python/lib').glob('python3.*/site-packages'))
    pth = site_packages / 'trusted-opt-python.pth'
    pth.write_text(str(trusted) + '\n')
    # controller umask is 077; the candidate must be able to read this file or
    # site.py silently skips it and trusted packages vanish from the venv.
    pth.chmod(0o444)
    os.chown(pth, CANDIDATE_UID, CANDIDATE_UID)
    started = time.monotonic()
    out, err = run_uid(CANDIDATE_UID, ['maturin', 'build', '--manifest-path', SOURCE / 'python/Cargo.toml', '--locked', '--offline', '--out', BUILD / 'wheels', '--interpreter', BUILD / 'venv/bin/python'], timeout=7200, cwd=SOURCE, build=True)
    (LOG / 'candidate-build.log').write_bytes(out + err)
    wheels = sorted((BUILD / 'wheels').glob('*.whl'))
    if len(wheels) != 1:
        raise InfrastructureError('expected exactly one candidate wheel')
    # build=True: unpacking the candidate extension needs the large-file
    # allowance; the 64 MiB non-build cap aborts pip with EFBIG.
    run_uid(CANDIDATE_UID, [BUILD / 'venv/bin/python', '-m', 'pip', 'install', '--no-index', '--no-deps', '--force-reinstall', wheels[0]], timeout=600, build=True)
    harness = Harness()
    origin = harness.operation('/', 'origin')
    expected = str(BUILD / 'venv') + '/'
    if not all(str(origin.get(key, '')).startswith(expected) for key in ('python', 'extension')):
        raise InfrastructureError(f'candidate origin check failed: {origin}')
    (LOG / 'build-origin.json').write_text(json.dumps({'seconds': time.monotonic() - started, **origin}, indent=2))
    return harness


def main():
    from cases import CASES
    from advanced_cases import ADVANCED_CASES
    from concurrent_cases import CONCURRENT_CASES
    registry = CASES + ADVANCED_CASES + CONCURRENT_CASES
    LOG.mkdir(exist_ok=True)
    LOG.chmod(0o700)
    for name in ('reward.txt', 'reward.json'):
        (LOG / name).unlink(missing_ok=True)
    results = []
    try:
        harness = prepare()
        if not registry or len({case.name for case in registry}) != len(registry):
            raise InfrastructureError('empty or duplicate case collection')
        for case in registry:
            started = time.monotonic()
            try:
                case.run(harness)
            except FeatureFailure as error:
                status, detail = 'fail', str(error)
            except AssertionError as error:
                status, detail = 'fail', str(error) or 'outcome assertion failed'
            except Exception as error:
                status, detail = 'infrastructure-error', f'{type(error).__name__}: {error}'
            else:
                status, detail = 'pass', ''
            results.append({'name': case.name, 'classification': case.classification, 'contract': case.contract, 'status': status, 'detail': detail, 'seconds': time.monotonic() - started})
            (LOG / 'cases.json').write_text(json.dumps(results, indent=2))
            print(f'{case.name}: {status}: {detail}', flush=True)
        if any(r['status'] == 'infrastructure-error' for r in results):
            raise InfrastructureError('one or more cases did not execute correctly; no valid reward')
        if any(r['status'] != 'pass' and r['classification'] == 'pass-to-pass' for r in results):
            raise InfrastructureError('baseline behavior/environment health failed; no valid reward')
        reward = int(all(r['status'] == 'pass' for r in results))
        tmp = LOG / 'reward.pending'
        tmp.write_text(str(reward) + '\n')
        tmp.replace(LOG / 'reward.txt')
    except Exception as error:
        (LOG / 'infrastructure-error.json').write_text(json.dumps({'type': type(error).__name__, 'detail': str(error)}, indent=2))
        print(f'VERIFIER INFRASTRUCTURE ERROR: {error}', file=sys.stderr)
        return 2
    finally:
        stop_uid(CANDIDATE_UID)
        stop_uid(INSPECTOR_UID)
    return 0


if __name__ == '__main__':
    sys.exit(main())
