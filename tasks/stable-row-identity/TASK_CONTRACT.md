# Native stable row tracking, bounded profile v1

Stage this contract as /app/TASK_CONTRACT.md. The public Delta protocol excerpt at /app/ROW_TRACKING_PROTOCOL.md is pinned to delta-io/delta commit ab3326f1d5119de3779aecbe81b474083c4173f2. This contract defines the evaluated profile and explicitly strengthens identity preservation where the protocol says SHOULD. It does not require every Delta feature.

## Supported setting

Use the supplied delta-rs revision, Rust/Python toolchain and frozen dependencies on Linux. Tables use a local POSIX directory with normal Delta JSON logs and Parquet data. All writers use this implementation. The evaluator may prepare protocol-valid external fixtures. No cloud credentials, Spark installation or network are required.

Supported user columns are nullable/non-null int64, bool and UTF-8, including duplicate values. Supported combinations include unpartitioned tables, string partitions, name-mode column mapping and supplied deletion-vector fixtures. NULL handling and ordinary projections/filters retain existing behavior. Metadata output names __delta_row_id and __delta_row_commit_version are reserved by this profile; enablement rejects conflicting user schemas without committing changes. User columns with other ordinary names must work.

Actors, streaming source offset recovery, automatic cross-table transactions, arbitrary external side effects, identity columns, suspended row tracking, dropping/disabling row tracking, arbitrary protocol upgrades, and cloud stores are outside this profile. Attempts to request the excluded tracking transitions must fail explicitly before committing them. Tables without the rowTracking protocol feature keep their existing behavior.

The intermediate protocol state where rowTracking is supported but delta.enableRowTracking is not true is supported. Writes in that state must allocate fresh IDs, commit versions and the domain high-water mark under the pinned protocol, even though metadata reads through the new API still require enablement. Enablement preserves existing valid allocation fields and the high-water mark, backfilling only missing fields. Preservation of stable identities across rewrites becomes mandatory once enabled.

## Interfaces

Add DeltaTable.enable_row_tracking(self) in Rust, returning an EnableRowTrackingBuilder whose IntoFuture output is DeltaResult<DeltaTable>. Expose the builder through the normal operations API and support with_commit_properties(CommitProperties). The returned table reflects the committed state. Use the existing DeltaTable.scan_table() builder entry and add LoadBuilder.with_row_tracking(bool); true appends the two metadata columns to the ordinary projected user columns in its native Arrow stream. The existing load result shape remains unchanged.

In Python add dt.alter.enable_row_tracking(*, commit_properties=None, post_commithook_properties=None) -> None, updating that DeltaTable handle on success. Add keyword-only include_row_tracking: bool = False to DeltaTable.to_pyarrow_table. With engine="datafusion" and include_row_tracking=True, return the requested ordinary user columns followed by __delta_row_id and __delta_row_commit_version as non-null Arrow int64 columns. columns refers to user-column projection; filters retain the existing DataFusion-engine meaning. Row order is unspecified. Requesting tracking through engine="pyarrow" raises ValueError. Requesting tracking on a table where it is not enabled raises an explicit exception; ordinary reads remain usable.

Setting delta.enableRowTracking=true at new-table creation through the existing writer API must also initialize valid tracking state. An already enabled valid table can be enabled again without changing its version, identities or data. Enablement is an explicit request to raise the protocol to writer version 7 as needed and add the required rowTracking/domainMetadata features; it must preserve existing compatible features. Do not claim unrelated feature support by changing capability lists alone.

Expose ordinary error types consistent with the existing bindings. Invalid types/arguments raise TypeError/ValueError in Python; unsupported state, corruption, overflow and commit conflicts must be distinguishable failures rather than successful empty results. WORKSPACE.md specifies the exception mappings and public examples. Exact exception messages are not prescribed.

## Enablement and physical representation

Enablement works on an empty table and an existing nonempty table. Backfill the necessary metadata for existing active files in one successful table transition without changing user values, data-file bytes or their paths. Do not expose a committed enabled table with missing row metadata. If another writer changes the snapshot before the transition, retry/revalidate against the winning state or fail cleanly without publishing an invalid table.

Persist the protocol-defined baseRowId, defaultRowCommitVersion, delta.rowTracking domain high-water mark, and materialized-ID/version column names/values as appropriate. The physical names are implementation-selected and collision-free, including against name-mapped physical user columns. Generated columns are not exposed as ordinary user data. No side database, Python object identity, user-column hash, process memory or external lookup file may be required to reconstruct identity. An independent reader using only the Delta log, Parquet files and documented deletion vectors must recover the same results.

IDs are nonnegative signed 64-bit integers, unique among active logical rows in each version. Fresh row-ID ranges and the high-water mark follow the pinned protocol. Gaps are permitted. The evaluator does not prescribe a starting ID or exact allocation order. Fresh IDs must not reuse IDs allocated in committed history after deletion, compaction, rollback of logical contents or restore. A retry cannot reuse a range committed or reserved by another successful transaction. Tentative uncommitted allocations may be discarded and recalculated. The high-water mark never decreases across committed history. Overflow must fail before a wrapping or inconsistent commit.

For rows whose version field is newly backfilled by enablement, the initial row commit version is the successful enablement version. Existing valid version/allocation fields are preserved, including in a supported-but-not-enabled table. For newly committed data, defaultRowCommitVersion is the version of the actual successful log commit, including after a conflict changes that version. Recommitted/removal metadata and checkpoint actions preserve the protocol-defined historical fields. Materialized values take precedence over defaults when non-null; otherwise reconstruction uses physical file row ordinal plus baseRowId, and the file's defaultRowCommitVersion.

## Mutation semantics

Append and overwrite insert genuinely new logical rows with fresh identities and the successful commit version. Equal user values do not make two inserted rows the same logical row. Partition replacement treats its new rows as inserts; unaffected partitions retain identity/version.

UPDATE preserves each affected row's stable ID and sets its row commit version to the successful update version. All rows selected for the update count as updated, even if assigned values happen to compare equal. Unselected rows copied during a file rewrite preserve both identity and version. DELETE removes selected identities and preserves the identity/version of surviving rows, including when entire files are rewritten.

MERGE preserves IDs for matched updates, gives inserts fresh IDs, removes matched-deleted rows, and retains identity/version for untouched rows copied by the operation. Updated rows get the successful merge version. Existing duplicate-match rejection rules remain valid. No requirement is imposed to accept a source that the baseline correctly rejects.

Compaction and z-ordering may reorder and rewrite files but must preserve the multiset of user values, stable IDs and row commit versions. This applies across multiple input files and small batches. The feature must not depend on file order, process-local row order or a unique user business key.

Historical reads report identity/version from their chosen snapshot, unaffected by later writes. Restore to a historical tracked version restores that version's user rows, stable IDs and row commit versions, while preserving the current history's allocation high-water mark. A later insert cannot reuse an ID allocated after the target historical version. Restore to an untracked version from an enabled table is unsupported by this profile and fails before a new commit. Existing untracked-to-untracked restore behavior is unchanged.

## Reads, checkpoints and compatibility

Metadata reconstruction must use physical row ordinals before filtering, projection or deletion-vector removal. A deletion vector removing physical rows must not renumber later surviving rows. Metadata and user values must remain aligned under ordinary filters, projection, row groups, batches and name-mode mapping. Existing compatible deletion vectors may be read or rewritten; this contract does not mandate a particular mutation algorithm or force new vector generation.

The Rust load API and Python DataFusion read API must agree. Ordinary reads exclude tracking metadata by default. Metadata is not fabricated in Python after loading ordinary rows. Native writers/readers must persist and recover it correctly even when called without the Python interface.

After checkpoint creation, log replay through that checkpoint and fresh-process reopen recover the same state. Required domain state and action metadata survive checkpoint serialization/replay. The evaluator may copy a table to a different local directory and remove the original. No absolute authoring or prior table path may be necessary. Historical reads are tested while their required retained files/logs remain available; no promise is made after VACUUM has legitimately removed them. The task covers classic checkpoints; v2 checkpoint features are outside the profile.

## Concurrency, failures and bounded evaluation

Concurrent appends from independent table handles must either commit correctly or report the existing bounded retry failure. The evaluator includes a controlled two-writer conflict where at least the ordinary supported retry path must let both appends succeed, with unique IDs and correct winning commit versions. Public fixture support for that deterministic schedule must be supplied before freeze; do not grade a private helper name or rely on arbitrary sleeps.

Mutation retries must revalidate or rebase allocation/version metadata against intervening commits. Valid existing conflict detection for conflicting updates remains applicable; do not silently overwrite another writer's changes merely to return success. After a rejected or interrupted operation, the latest complete snapshot remains readable and internally valid. Unreferenced data/temp files are permitted as in normal Delta writes; a malformed or partially visible committed log entry is not.

A public local storage/failpoint adapter may be supplied for deterministic commit-boundary interruption and overflow/conflict fixtures. Its interface and supported events must be documented in WORKSPACE.md before the task is frozen. It must not expose reference algorithms or be required as an alternate implementation. Ordinary operations must work without the adapter. Killing a process before commit may leave no new version; killing it after a successful commit may leave that complete new version. Assertions respect this commit boundary rather than demanding rollback of an already committed operation. Tests simulate process crashes, not physical power loss.

Verification uses small tables, bounded process timeouts and coordinated concurrent writes. Correctness is checked through the native and Python APIs and independently reconstructed from committed artifacts. There are no hidden source-file quotas, wall-time speed thresholds or required private function names. Supported combinations listed here are acceptance requirements, not optional bonus work.
