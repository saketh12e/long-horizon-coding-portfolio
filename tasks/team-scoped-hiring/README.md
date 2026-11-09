# Team-scoped hiring workflow

**Task type and codebase:** Feature implementation in a Python simulation service using SQLAlchemy and an HTTP API.

Connect persistent candidate records, team-specific candidate generation, deterministic voting and the eligible-target API. Candidates remain stable across repeated reads and are cleaned up after a hire.

**Testing:** Regression tests use deterministic randomness, SQLite-backed records and isolated teams. They cover candidate reuse, vote filtering, tie resolution, cleanup and API behavior.

Read `instruction.md` for the task, `solution.patch` for the implementation, and `tests/test_team_scoped_hiring.py` for the regression suite. `regression.patch` contains the same test source for application to a matching baseline.

This patch sample requires its corresponding source baseline and normal dependencies. Module names are neutralized consistently. In that baseline, apply the solution and regression patches, then run:

```sh
python -m pytest tests/test_team_scoped_hiring.py -q
```

Attribution and source-license information are retained under `notices/`.
