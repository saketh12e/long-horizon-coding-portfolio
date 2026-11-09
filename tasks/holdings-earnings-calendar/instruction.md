# Holdings earnings calendar

The position-detail and weekly-review pages in a Flask analytics application omit upcoming company earnings announcements. Add the feature across the existing handlers and Jinja templates. This task assumes a matching application baseline; the baseline and calendar ingestion pipeline are outside this patch sample.

Each handler should add its earnings query to the page’s existing parallel data-fetch batch. For the active position symbol, select the next upcoming announcement within fourteen days and expose its report date, readable date and day countdown. When no row exists, omit the countdown rather than inventing a value.

For weekly review, restrict announcements to symbols still held by the selected account scope. Cover today through fourteen calendar days ahead and prepare separate lists for dates up to seven days away and dates eight through fourteen days away. Render the symbols and readable report dates in the review page. Preserve unrelated page behavior.

Use the neutral `analytics` dataset alias for the calendar, holdings and symbol-metadata tables. Regression coverage should exercise handlers and rendered templates with synthetic fixtures and mocked query results, without contacting external data services.
