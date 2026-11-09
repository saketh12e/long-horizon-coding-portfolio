# Holdings earnings calendar

**Task type and codebase:** feature implementation in a Python application using Flask, pandas, Jinja and BigQuery.

Add a position-page earnings countdown and a holdings-scoped, fourteen-day calendar in weekly review. Connect each page’s existing parallel query batch to its handler context and rendered template.

**Testing:** regression tests cover query dispatch, date boundaries, missing data, near/far buckets, handler context and rendered HTML. External queries and authentication use synthetic fixtures.

Read `instruction.md` for the task, `solution.patch` for the implementation, and `tests/test_holdings_earnings_calendar.py` for the regression suite. `regression.patch` contains the same test source.

This patch sample requires its corresponding application baseline and dependencies. The neutralized baseline must use the `analytics` dataset alias and honor `APP_SKIP_DB_INIT=1` during test initialization. The calendar data pipeline is outside the task. Apply both patches in that baseline, then run:

```sh
python -m pytest tests/test_holdings_earnings_calendar.py -q
```

Source attribution and the MIT license are retained in `NOTICE.md` and `LICENSE`.
