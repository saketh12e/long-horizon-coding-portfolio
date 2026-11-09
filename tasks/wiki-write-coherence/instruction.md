# Coherent wiki writes and page identity

A memory service maintains wiki pages on disk and exposes an in-process page list. After creating or updating pages, callers can still see an old list. Batch learning can also leave the wiki unchanged until a separate refresh is requested.

Make each mutating operation leave the next page-list read consistent with the current wiki. Cover manual creation, full and incremental updates, page refresh, cross-reference changes and batch learning. Reading, searching, memory links and backlinks must agree without requiring a later command.

Treat the markdown path as part of page identity. Two different titles that map to the same path must not overwrite existing content or create separate listed entities for one file. Preserve existing orphan markdown files. When learned memories resolve to an existing page, retain their links and backlinks on that page. Keep existing exact-duplicate and ordinary read/update behavior.
