# Build and inspect

The upstream baseline is public delta-io/delta-rs commit `65e92263a083cd67b5328e9caaf52f1a8467594e`. Use Linux Docker with at least four CPUs and 8 GiB memory. Image construction fetches the pinned upstream source and registry dependencies. Test execution uses no network. The container is disposable; it owns the fixed /app, /opt, /work and /logs paths used by tests/controller.py.

```sh
./run.sh
```

This builds the unchanged baseline dependency cache, applies solution.patch to a separate implementation checkout, then runs the focused checks. The controller rebuilds the changed production source and verifies the imported extension's origin. Inspect the emitted per-case output and the command's exit status. The native probe and inspector are support programs for the harness, not standalone test runners. Container results are generated during execution, not supplied as sample history.

To inspect or apply the patch without Docker, use a clean upstream checkout:

```sh
git clone https://github.com/delta-io/delta-rs.git delta-rs
cd delta-rs
git checkout --detach 65e92263a083cd67b5328e9caaf52f1a8467594e
git apply --check /absolute/path/to/stable-row-identity/solution.patch
git apply /absolute/path/to/stable-row-identity/solution.patch
cp /absolute/path/to/stable-row-identity/Cargo.lock Cargo.lock
cargo test --locked -p deltalake-core --features datafusion --lib row_tracking
cargo test --locked -p deltalake-core --features datafusion --test row_tracking_api
```

The pinned upstream Rust toolchain is 1.95.0. Python package versions for the container are declared in Dockerfile. Cargo.lock pins registry dependencies; a direct build may fetch them before any offline run. The container harness installs and tests the resulting Python extension as well as Rust behavior.

Python argument errors use TypeError or ValueError. Invalid native tracking data, reserved user-column conflicts and allocation overflow use deltalake.exceptions.DeltaProtocolError; exhausted retries use CommitFailedError. Unsupported native operations use DeltaError. Storage failures retain their FileNotFoundError/OSError mapping. Failed validation must leave the latest committed version unchanged.

An optional deterministic commit-boundary adapter uses DELTALAKE_ROW_TRACKING_FAILPOINT_DIR. Before and after a tracked commit attempt, it creates <phase>-<version:020>-<attempt:02>.ready and waits for the matching .release file; phases are before and after, and attempts start at one. Waiting is bounded to 60 seconds. Ordinary operations work without this variable. This supports reproducible concurrency investigations without changing the public feature semantics.
