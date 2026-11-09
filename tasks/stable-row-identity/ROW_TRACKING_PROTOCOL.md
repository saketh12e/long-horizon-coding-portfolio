Source: delta-io/delta PROTOCOL.md, ab3326f1d5119de3779aecbe81b474083c4173f2. Apache-2.0.

# Row Tracking

Row Tracking is a feature that allows the tracking of rows across multiple versions of a Delta table.
It enables this by exposing two metadata columns: Row IDs, which uniquely identify a row across multiple versions of a table,
and Row Commit Versions, which make it possible to check whether two rows with the same ID in two different versions of the table represent the same version of the row.

Row Tracking is defined to be **supported** or **enabled** on a table as follows:
- When the feature `rowTracking` exists in the table `protocol`'s `writerFeatures`, then we say that Row Tracking is **supported**.
  In this situation, writers must assign Row IDs and Commit Versions as long as `delta.rowTrackingSuspended` table property is absent or set to false. However, they cannot yet be relied upon to be present in the table.
  When Row Tracking is supported but not yet enabled writers cannot preserve Row IDs and Commit Versions.
- When additionally the table property `delta.enableRowTracking` is set to `true`, then we say that Row Tracking is **enabled**.
  In this situation, Row IDs and Row Commit versions can be relied upon to be present in the table for all rows.
  When Row Tracking is enabled writers are expected to preserve Row IDs and Commit Versions.
- When the table property `delta.rowTrackingSuspended` is set to true, writers should suspend the assignment of Row IDs and Commit Versions.
  Table property `delta.rowTrackingSuspended` should not be enabled together with table property `delta.enableRowTracking`.

Enablement:
- The table must be on Writer Version 7.
- The feature `rowTracking` must exist in the table `protocol`'s `writerFeatures`. The feature `domainMetadata` is required in the table `protocol`'s `writerFeatures`.
- The table property `delta.enableRowTracking` must be set to `true`.
- The table property `delta.rowTrackingSuspended` should be absent or set to `false`.

## Row IDs

Delta provides Row IDs. Row IDs are integers that are used to uniquely identify rows within a table.
Every row has two Row IDs:

- A **fresh** or unstable **Row ID**.
  This ID uniquely identifies the row within one version of the table.
  The fresh ID of a row may change every time the table is updated, even for rows that are not modified. E.g. when a row is copied unchanged during an update operation, it will get a new fresh ID. Fresh IDs can be used to identify rows within one version of the table, e.g. for identifying matching rows in self joins.
- A **stable Row ID**.
  This ID uniquely identifies the row across versions of the table and across updates.
  When a row is inserted, it is assigned a new stable Row ID that is equal to the fresh Row ID.
  When a row is updated or copied, the stable Row ID for this row is preserved.
  When a row is restored (i.e. the table is restored to an earlier version), its stable Row ID is restored as well.

The fresh and stable Row IDs are not required to be equal.

Row IDs are stored in two ways:

- **Default generated Row IDs** use the `baseRowId` field stored in `add` and `remove` actions to generate fresh Row IDs.
  The default generated Row IDs for data files are calculated by adding the `baseRowId` of the file in which a row is contained to the (physical) position (index) of the row within the file.
  Default generated Row IDs require little storage overhead but are reassigned every time a row is updated or moved to a different file (for instance when a row is contained in a file that is compacted by OPTIMIZE).

- **Materialized Row IDs** are stored in a column in the data files.
  This column is hidden from readers and writers, i.e. it is not part of the `schemaString` in the table's `metaData`.
  Instead, the name of this column can be found in the value for the `delta.rowTracking.materializedRowIdColumnName` key in the `configuration` of the table's `metaData` action.
  This column may contain `null` values meaning that the corresponding row has no materialized Row ID. This column may be omitted if all its values are `null` in the file.
  Materialized Row IDs provide a mechanism for writers to preserve stable Row IDs for rows that are updated or copied.

The fresh Row ID of a row is equal to the default generated Row ID. The stable Row ID of a row is equal to the materialized Row ID of the row when that column is present and the value is not NULL, otherwise it is equal to the default generated Row ID.

When Row Tracking is enabled:
- Default generated Row IDs must be assigned to all existing rows.
  This means in particular that all files that are part of the table version that sets the table property `delta.enableRowTracking` to `true` must have `baseRowId` set.
  A backfill operation may be required to commit `add` and `remove` actions with the `baseRowId` field set for all data files before the table property `delta.enableRowTracking` can be set to `true`.

## Row Commit Versions

Row Commit Versions provide versioning of rows.

- **Fresh** or unstable **Row Commit Versions** can be used to identify the first commit version in which the `add` action containing the row was committed.
  The fresh Commit Version of a row may change every time the table is updated, even for rows that are not modified. E.g. when a row is copied unchanged during an update operation, it will get a new fresh Commit Version.
- **Stable Row Commit Versions** identify the last commit version in which the row (with the same ID) was either inserted or updated.
  When a row is inserted or updated, it is assigned the commit version number of the log entry containing the `add` entry with the new row.
  When a row is copied, the stable Row Commit Version for this row is preserved.
  When a row is restored (i.e. the table is restored to an earlier version), its stable Row Commit Version is restored as well.

The fresh and stable Row Commit Versions are not required to be equal.

Commit Versions are stored in two ways:

- **Default generated Row Commit Versions** use the `defaultRowCommitVersion` field in `add` and `remove` actions.
  Default generated Row Commit Versions require little storage overhead but are reassigned every time a row is updated or moved to a different file (for instance when a row is contained in a file that is compacted by OPTIMIZE).

- **Materialized Row Commit Versions** are stored in a column in the data files.
  This column is hidden from readers and writers, i.e. it is not part of the `schemaString` in the table's `metaData`.
  Instead, the name of this column can be found in the value for the `delta.rowTracking.materializedRowCommitVersionColumnName` key in the `configuration` of the table's `metaData` action.
  This column may contain `null` values meaning that the corresponding row has no materialized Row Commit Version. This column may be omitted if all its values are `null` in the file.
  Materialized Row Commit Versions provide a mechanism for writers to preserve Row Commit Versions for rows that are copied.

The fresh Row Commit Version of a row is equal to the default generated Row Commit version.
The stable Row Commit Version of a row is equal to the materialized Row Commit Version of the row when that column is present and the value is not NULL, otherwise it is equal to the default generated Commit Version.

## Reader Requirements for Row Tracking

When Row Tracking is enabled (when the table property `delta.enableRowTracking` is set to `true`), then:
- When Row IDs are requested, readers must reconstruct stable Row IDs as follows:
  1. Readers must use the materialized Row ID if the column determined by `delta.rowTracking.materializedRowIdColumnName` is present in the data file and the column contains a non `null` value for a row.
  2. Otherwise, readers must use the default generated Row ID of the `add` or `remove` action containing the row in all other cases.
     I.e. readers must add the index of the row in the file to the `baseRowId` of the `add` or `remove` action for the file containing the row.
- When Row Commit Versions are requested, readers must reconstruct them as follows:
  1. Readers must use the materialized Row Commit Versions if the column determined by `delta.rowTracking.materializedRowCommitVersionColumnName` is present in the data file and the column contains a non `null` value for a row.
  2. Otherwise, Readers must use the default generated Row Commit Versions of the `add` or `remove` action containing the row in all other cases.
     I.e. readers must use the `defaultRowCommitVersion` of the `add` or `remove` action for the file containing the row.
- Readers cannot read Row IDs and Row Commit Versions while reading change data files from `cdc` actions.

## Writer Requirements for Row Tracking

When Row Tracking is supported (when the `writerFeatures` field of a table's `protocol` action contains `rowTracking`) and Row Tracking is not suspended (when `delta.rowTrackingSuspended` table property is absent or set to false), then:
- Writers must assign unique fresh Row IDs to all rows that they commit.
  - Writers must set the `baseRowId` field in all `add` actions that they commit so that all default generated Row IDs are unique in the table version.
    Writers must never commit duplicate Row IDs in the table in any version.
  - Writers must set the `baseRowId` field in recommitted and checkpointed `add` actions and `remove` actions to the `baseRowId` value (if present) of the last committed `add` action with the same `path`.
  - Writers must track the high water mark, i.e. the highest fresh row id assigned.
    - The high water mark must be stored in a `domainMetadata` action with `delta.rowTracking` as the `domain`
      and a `configuration` containing a single key-value pair with `rowIdHighWaterMark` as the key and the highest assigned fresh row id as the value.
    - Writers must include a `domainMetadata` for `delta.rowTracking` whenever they assign new fresh Row IDs that are higher than `rowIdHighWaterMark` value of the current `domainMetadata` for `delta.rowTracking`.
      The `rowIdHighWaterMark` value in the `configuration` of this `domainMetadata` action must always be equal to or greater than the highest fresh Row ID committed so far.
      Writers can either commit this `domainMetadata` in the same commit, or they can reserve the fresh Row IDs in an earlier commit.
    - Writers must set the `baseRowId` field to a value that is higher than the row id high water mark.
- Writer must assign fresh Row Commit Versions to all rows that they commit.
  - Writers must set the `defaultRowCommitVersion` field in new `add` actions to the version number of the log enty containing the `add` action.
  - Writers must set the `defaultRowCommitVersion` field in recommitted and checkpointed `add` actions and `remove` actions to the `defaultRowCommitVersion` of the last committed `add` action with the same `path`.

On the other hand, when Row Tracking is supported but suspended (table property `delta.rowTrackingSuspended` is set to `true`), writers should not assign the `baseRowId` or the `defaultRowCommitVersion`.

Writers can enable Row Tracking by setting `delta.enableRowTracking` to `true` in the `configuration` of the table's `metaData`.
This is only allowed if the following requirements are satisfied:
- The feature `rowTracking` has been added to the `writerFeatures` field of a table's `protocol` action either in the same version of the table or in an earlier version of the table.
- The column name for the materialized Row IDs and Row Commit Versions have been assigned and added to the `configuration` in the table's `metaData` action using the keys `delta.rowTracking.materializedRowIdColumnName` and `delta.rowTracking.materializedRowCommitVersionColumnName` respectively.
  - The assigned column names must be unique. They must not be equal to the name of any other column in the table's schema.
    The assigned column names must remain unique in all future versions of the table.
    If [Column Mapping](#column-mapping) is enabled, then the assigned column name must be distinct from the physical column names of the table.
- The `baseRowId` and `defaultRowCommitVersion` fields are set for all active `add` actions in the version of the table in which `delta.enableRowTracking` is set to `true`.
- If the `baseRowId` and `defaultRowCommitVersion` fields are not set in some active `add` action in the table, then writers must first commit new `add` actions that set these fields to replace the `add` actions that do not have these fields set.
  This can be done in the commit that sets `delta.enableRowTracking` to `true` or in an earlier commit.
  The assigned `baseRowId` and `defaultRowCommitVersion` values must satisfy the same requirements as when assigning fresh Row IDs and fresh Row Commit Versions respectively.
Furthermore, writers should also verify table property `delta.rowTrackingSuspended` is absent or set to false before enabling Row Tracking.

When Row Tracking is enabled (when the table property `delta.enableRowTracking` is set to `true`), then:
- Writers must assign stable Row IDs to all rows.
  - Stable Row IDs must be unique within a version of the table and must not be equal to the fresh Row IDs of other rows in the same version of the table.
  - Writers should preserve the stable Row IDs of rows that are updated or copied using materialized Row IDs.
    - The preserved stable Row ID (i.e. a stable Row ID that is not equal to the fresh Row ID of the same physical row) should be equal to the stable Row ID of the same logical row before it was updated or copied.
    - Materialized Row IDs must be written to the column determined by `delta.rowTracking.materializedRowIdColumnName` in the `configuration` of the table's `metaData` action.
      The value in this column must be set to `NULL` for stable Row IDs that are not preserved.
- Writers must assign stable Row Commit Versions to all rows.
  - Writers should preserve the stable Row Commit Versions of rows that are copied (but not updated) using materialized Row Commit Versions.
    - The preserved stable Row Commit Version (i.e. a stable Row Commit Version that is not equal to the fresh Row Commit Version of the same physical row) should be equal to the stable Commit Version of the same logical row before it was copied.
    - Materialized Row Commit Versions must be written to the column determined by `delta.rowTracking.materializedRowCommitVersionColumnName` in the `configuration` of the table's `metaData` action.
      The value in this column must be set to `NULL` for stable Row Commit Versions that are not preserved (i.e. that are equal to the fresh Row Commit Version).
- Writers should set `delta.rowTracking.preserved` in the `tags` of the `commitInfo` action to `true` whenever all the stable Row IDs of rows that are updated or copied and all the stable Row Commit Versions of rows that are copied were preserved.
  In particular, writers should set `delta.rowTracking.preserved` in the `tags` of the `commitInfo` action to `true` if no rows are updated or copied.
  Writers should set that flag to false otherwise.
