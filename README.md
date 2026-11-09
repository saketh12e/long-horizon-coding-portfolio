# Long-Horizon Coding Samples

Five software engineering tasks in existing codebases, with task briefs, reference implementations and regression tests.

| Task | Codebase | Change |
| --- | --- | --- |
| [Stable row identity](tasks/stable-row-identity/) | Rust table engine with Python bindings | Preserve row identity through concurrent commits, mutations, file rewrites and recovery. |
| [Team-scoped hiring](tasks/team-scoped-hiring/) | Python simulation service | Connect persistent candidate records, team-specific selection, voting and API behavior. |
| [Tag-aware retention](tasks/tag-aware-retention/) | Python memory service | Apply per-tag decay policies consistently through scoring, maintenance and administration. |
| [Wiki write coherence](tasks/wiki-write-coherence/) | Python memory service | Keep page identity, file ownership, links and cached listings consistent after writes. |
| [Holdings earnings calendar](tasks/holdings-earnings-calendar/) | Python web application | Add holdings-scoped earnings dates across query handlers and rendered views. |

Start with each task's `README.md`, then read its brief, implementation patch and tests. The Rust sample targets the public delta-rs codebase. The other samples use neutral module names and require their corresponding source baselines to execute.

Task-specific attribution and source licenses are retained in each folder.
