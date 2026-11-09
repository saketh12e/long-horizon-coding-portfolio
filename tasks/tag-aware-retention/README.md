# Tag-aware memory retention

**Task type and codebase:** Feature implementation in a Python memory service.

Add per-tag decay policies across scoring, the maintenance facade, input validation and administrative HTTP routes. Preserve default behavior and support a zero rate for durable memories.

**Testing:** Regression tests cover policy round trips, invalid updates, tag precedence, adjusted scores, reinforcement and HTTP administration.

Read `instruction.md` for the task, `solution.patch` for the implementation, and `tests/test_tag_aware_retention.py` for the regression suite. `regression.patch` contains the same test source for application to a matching baseline.

This patch sample requires its corresponding source baseline and normal dependencies. Module names are neutralized consistently. In that baseline, apply the solution and regression patches, then run:

```sh
python -m pytest tests/test_tag_aware_retention.py -q
```

Attribution and source-license information are retained under `notices/`.
