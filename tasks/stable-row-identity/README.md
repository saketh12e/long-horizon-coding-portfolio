# Stable row identity

## Task type

Implement persistent row identity and last-change metadata across a transactional table lifecycle.

## Codebase

Rust data-systems code with Python bindings, based on [delta-rs](https://github.com/delta-io/delta-rs) at commit `65e92263a083cd67b5328e9caaf52f1a8467594e`. The sample contains a source patch and focused verification code; upstream source is fetched at the pinned revision. Apache-2.0 attribution and notices are preserved in [ATTRIBUTION.md](ATTRIBUTION.md), [LICENSE.txt](LICENSE.txt) and [NOTICE](NOTICE).

## Change scope

Protocol enablement and backfill, commit-time ID allocation, native scans, Python APIs, updates/deletes/merges, compaction, restore, checkpoints and concurrent writers. Identity must survive changes to row values and physical file layout. The exact supported behavior is in [TASK_CONTRACT.md](TASK_CONTRACT.md) and the attributed [protocol excerpt](ROW_TRACKING_PROTOCOL.md).

## Testing approach

Focused synthetic table operations check API behavior, mutation identity, allocation, recovery and ordinary-table regressions. A separate inspector reconstructs identities from committed Delta logs, checkpoints and Parquet files without importing the implementation under test. A native Rust probe checks parity with Python reads. The Linux harness separates the tested implementation and artifact inspector by process identity.

Run `./run.sh` to build the pinned source and execute the checks in Docker. See [WORKSPACE.md](WORKSPACE.md) for prerequisites, patch application and individual native checks. The patch includes focused upstream Rust and Python test additions. The compact sample does not embed an upstream checkout.
