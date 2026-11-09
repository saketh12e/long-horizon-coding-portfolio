# Tag-aware memory retention

A memory service currently applies one decay rate to all stored entries. Add optional per-tag rates so temporary notes can fade quickly while durable facts retain importance.

Use the existing decay-rate scale. Reject empty tags and rates outside [0.0, 1.0] without replacing a previously valid setting. Expose setting, reading, listing and clearing policies through the existing maintenance facade and administrative HTTP surface. Provide create/update, list and delete operations at that surface.

During decay and adjusted-score calculation, use the first configured rate in the memory’s tag order. Fall back to the unchanged default when no tag matches. A zero rate must preserve importance over time. Existing reinforcement, untagged-memory and default-rate behavior must continue to work. Persistence across process restarts and a new command-line interface are outside this feature.
