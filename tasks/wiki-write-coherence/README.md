# Coherent wiki writes and page identity

**Task type and codebase:** Consistency repair in a Python memory service with a file-backed wiki.

Keep page listings, file ownership, memory links and backlinks consistent after manual writes and batch learning. Prevent normalized filename collisions from overwriting existing pages.

**Testing:** Regression tests use temporary directories and the in-process client. They cover immediate visibility, filename collisions, orphan files, grouped learning and existing read behavior.

Read `instruction.md` for the task, `solution.patch` for the implementation, and `tests/test_wiki_write_coherence.py` for the regression suite. `regression.patch` contains the same test source for application to a matching baseline.

This patch sample requires its corresponding source baseline and normal dependencies. Module names are neutralized consistently. In that baseline, apply the solution and regression patches, then run:

```sh
python -m pytest tests/test_wiki_write_coherence.py -q
```

Attribution and source-license information are retained under `notices/`.
