"""Regression coverage: earnings calendar on position detail and weekly review.

Behavior-only. Exercises production handlers with mocked BigQuery batches and
Flask template renders. Outcome-graded: assertions check that the earnings
calendar is wired into both surfaces, not that any specific variable name,
dictionary shape, or CSS class was chosen by the implementer.
"""
from __future__ import annotations

import importlib
import importlib.util
import os
import sys
import types
from datetime import date, timedelta
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

os.environ.setdefault("SECRET_KEY", "test-only-not-for-deployment")
os.environ.setdefault("APP_SKIP_DB_INIT", "1")

if importlib.util.find_spec("google.genai") is None:
    import google as _google_pkg
    from unittest.mock import MagicMock as _MM

    _genai_stub = types.ModuleType("genai")
    _genai_stub.types = _MM()
    _google_pkg.genai = _genai_stub
    sys.modules["google.genai"] = _genai_stub
    sys.modules["google.genai.types"] = _genai_stub.types


# ───────────────────────── module loaders ─────────────────────────


def _routes_module():
    return importlib.import_module("app.routes")


def _weekly_review_module():
    return importlib.import_module("app.weekly_review")


def _auth_user():
    return types.SimpleNamespace(id=42, username="synthetic-user", is_authenticated=True)


def _empty_df():
    return pd.DataFrame()


# ───────────────────────── probe dataframes ─────────────────────────


def _earnings_probe_df(*, days_until: int = 5) -> pd.DataFrame:
    report = date.today() + timedelta(days=days_until)
    return pd.DataFrame(
        [
            {
                "symbol": "PROBE",
                "next_earnings_date": pd.Timestamp(report),
                "earnings_window_start": pd.Timestamp(report - timedelta(days=2)),
                "earnings_window_end": pd.Timestamp(report + timedelta(days=1)),
                "days_until": days_until,
            }
        ]
    )


def _weekly_earnings_probe_df() -> pd.DataFrame:
    near = date.today() + timedelta(days=3)
    far = date.today() + timedelta(days=11)
    return pd.DataFrame(
        [
            {
                "symbol": "NEAR",
                "next_earnings_date": pd.Timestamp(near),
                "earnings_window_start": pd.Timestamp(near - timedelta(days=2)),
                "earnings_window_end": pd.Timestamp(near + timedelta(days=1)),
                "days_until": 3,
                "long_name": "Example Near",
                "sector": "Tech",
                "subsector": "Software",
            },
            {
                "symbol": "FAR",
                "next_earnings_date": pd.Timestamp(far),
                "earnings_window_start": pd.Timestamp(far - timedelta(days=2)),
                "earnings_window_end": pd.Timestamp(far + timedelta(days=1)),
                "days_until": 11,
                "long_name": "Example Far",
                "sector": "Health",
                "subsector": "Devices",
            },
        ]
    )


def _summary_row() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "account": "synthetic-account",
                "strategy": "Hold",
                "status": "Open",
                "total_return": 120.0,
                "total_pnl": 120.0,
                "realized_pnl": 80.0,
                "unrealized_pnl": 40.0,
                "total_premium_received": 0.0,
                "total_premium_paid": 0.0,
                "num_winners": 1,
                "num_losers": 0,
                "win_rate": 1.0,
                "avg_pnl_per_trade": 80.0,
                "avg_days_in_trade": 5.0,
                "total_dividend_income": 0.0,
                "dividend_count": 0,
                "num_trade_groups": 1,
                "num_individual_trades": 1,
                "first_trade_date": "2026-01-01",
                "last_trade_date": "2026-01-02",
            }
        ]
    )


def _chart_df() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "date": "2026-01-01",
                "account": "synthetic-account",
                "symbol": "PROBE",
                "cumulative_equity_pnl": 50.0,
                "cumulative_options_pnl": 0.0,
                "cumulative_dividends_pnl": 0.0,
                "cumulative_other_pnl": 0.0,
                "open_options_unrealized_pnl": 0.0,
            },
            {
                "date": "2026-01-02",
                "account": "synthetic-account",
                "symbol": "PROBE",
                "cumulative_equity_pnl": 120.0,
                "cumulative_options_pnl": 0.0,
                "cumulative_dividends_pnl": 0.0,
                "cumulative_other_pnl": 0.0,
                "open_options_unrealized_pnl": 0.0,
            },
        ]
    )


# ───────────────────────── shape-tolerant helpers ─────────────────────────


def _query_text(entry) -> str:
    """Batch entries are either a SQL string or a (sql, query_config) tuple."""
    if isinstance(entry, tuple):
        return str(entry[0])
    return str(entry)


def _find_earnings_query(queries: dict):
    """Return the lowercased SQL of the earnings calendar query in the batch.

    Matches any key whose SQL references stg_earnings_calendar — the implementation may
    name the batch entry "earnings", "next_earnings", "calendar", "earnings_cal",
    "upcoming_earnings", or anything else.
    """
    for _key, entry in queries.items():
        text = _query_text(entry).lower()
        if "stg_earnings_calendar" in text:
            return text
    return None


def _ctx_keys_lower(ctx: dict):
    return [str(k).lower() for k in ctx.keys()]


def _find_days_in_ctx(ctx: dict, *, equals=None):
    """Search the render context for a days-until-earnings value.

    Accepts any of these implementer choices:
      • scalar:    ctx["earnings_days"] = 5
      • scalar:    ctx["next_earnings_days"] = 5
      • dict:      ctx["symbol_next_earnings"] = {"days_until": 5, ...}
      • dict:      ctx["next_earnings"]        = {"days": 5, ...}

    If `equals` is provided, only return values equal to it.
    """

    def _ok(v):
        if equals is None:
            return v is not None
        return v == equals

    for k, v in ctx.items():
        kl = str(k).lower()
        if "earning" not in kl and "report" not in kl:
            continue

        # scalar form on a *days*-flavored key
        if isinstance(v, int) and not isinstance(v, bool):
            if "day" in kl and _ok(v):
                return v

        # dict form: any sub-key that names a countdown
        if isinstance(v, dict):
            for sub in (
                "days_until",
                "days",
                "earnings_days",
                "days_until_earnings",
                "countdown",
                "in_days",
            ):
                val = v.get(sub)
                if isinstance(val, int) and not isinstance(val, bool) and _ok(val):
                    return val
    return None


def _collect_upcoming_buckets(ctx: dict):
    """Find the (this-week, next-week) earnings symbol buckets in any shape.

    Recognized shapes:
      • two top-level lists (any earnings-flavored key with this/near vs next/far)
          ctx["upcoming_earnings_this_week"], ctx["upcoming_earnings_next_week"]
      • single grouped dict
          ctx["upcoming_earnings"] = {"this_week": [...], "next_week": [...]}
      • single combined list — bucket by row's days_until
          ctx["upcoming_earnings"] = [...]  /  ctx["earnings_upcoming"] = [...]
    """
    near, far = set(), set()

    def _row_days(row):
        for k in ("days_until", "days", "days_until_earnings", "in_days"):
            v = row.get(k)
            if isinstance(v, int) and not isinstance(v, bool):
                return v
            try:
                if v is not None and not isinstance(v, bool):
                    return int(v)
            except (TypeError, ValueError):
                pass
        return None

    def _add_from_list(items, target_when_grouped):
        for row in items or []:
            if not isinstance(row, dict):
                continue
            sym = str(row.get("symbol") or row.get("ticker") or "").upper().strip()
            if not sym:
                continue
            d = _row_days(row)
            if target_when_grouped is not None:
                target_when_grouped.add(sym)
            elif d is not None:
                (near if d <= 7 else far).add(sym)

    for k, v in ctx.items():
        kl = str(k).lower()
        if "earning" not in kl and "report" not in kl:
            continue

        # explicit bucket lists
        if isinstance(v, list):
            if any(
                tag in kl
                for tag in (
                    "this_week",
                    "this-week",
                    "near",
                    "current_week",
                    "week_of",
                    "within_7",
                    "within7",
                    "week_1",
                )
            ):
                _add_from_list(v, near)
            elif any(
                tag in kl
                for tag in (
                    "next_week",
                    "next-week",
                    "far",
                    "following",
                    "upcoming_next",
                    "8_to_14",
                    "8-14",
                    "days_8",
                    "week_2",
                )
            ):
                _add_from_list(v, far)
            else:
                # combined list — bucket by days_until
                _add_from_list(v, None)

        # grouped dict
        elif isinstance(v, dict):
            for sub_key, val in v.items():
                if not isinstance(val, list):
                    continue
                skl = str(sub_key).lower()
                if any(tag in skl for tag in ("this_week", "this-week", "near", "current_week", "week_of")):
                    _add_from_list(val, near)
                elif any(tag in skl for tag in ("next_week", "next-week", "far", "following", "upcoming_next")):
                    _add_from_list(val, far)
                else:
                    _add_from_list(val, None)

    return near, far


# ───────────────────────── route drivers ─────────────────────────


def _drive_position_detail(mod, *, days_until: int = 5, empty_earnings: bool = False):
    """Run position_detail once; return parallel query map + render_template kwargs."""
    from app import app

    user = _auth_user()
    empty = _empty_df()
    summary = _summary_row()
    earnings = _empty_df() if empty_earnings else _earnings_probe_df(days_until=days_until)
    chart_df = _chart_df()
    captured_queries: dict = {}
    render_ctx: dict = {}

    def fake_bq_parallel(_client, queries):
        captured_queries.update(dict(queries))
        out = {}
        for key, entry in queries.items():
            sql = _query_text(entry).lower()
            if "stg_earnings_calendar" in sql:
                out[key] = earnings.copy()
            elif key == "summary":
                out[key] = summary.copy()
            else:
                out[key] = empty.copy()
        return out

    class _QueryJob:
        def to_dataframe(self):
            return chart_df.copy()

    client = MagicMock()
    client.query = lambda *_a, **_kw: _QueryJob()

    def capture_render(_template, **ctx):
        render_ctx.update(ctx)
        return ""

    patches = [
        patch.object(mod, "get_bigquery_client", return_value=client),
        patch.object(mod, "_redirect_if_no_accounts", return_value=None),
        patch.object(mod, "_user_account_list", return_value=["synthetic-account"]),
        patch.object(mod, "current_user", user),
        patch("flask_login.utils._get_user", return_value=user),
        patch.object(mod, "is_admin", return_value=False),
        patch.object(
            mod,
            "_compute_breakdown_by_type",
            return_value=[{"type": "Equity", "total": 120.0, "realized": 80.0, "unrealized": 40.0, "count": 1, "count_label": "trades", "count_open": 0}],
        ),
        patch.object(mod, "_bq_parallel", fake_bq_parallel),
        patch.object(mod, "_fetch_closed_option_legs_from_classification", return_value=empty),
        patch.object(mod, "_fetch_int_strategy_classification_by_symbol", return_value=empty),
        patch.object(mod, "_narrow_mart_daily_pnl_chart_df_to_summary_tenant", side_effect=lambda df, _s: df),
        patch.object(mod, "render_template", side_effect=capture_render),
    ]

    for p in patches:
        p.start()
    try:
        with app.test_request_context("/position/PROBE"):
            mod.position_detail("PROBE")
    finally:
        for p in patches:
            p.stop()

    return captured_queries, render_ctx


def _drive_weekly_review(mod):
    """Run weekly_review once; return parallel query map + render_template kwargs."""
    from app import app

    user = _auth_user()
    earn_df = _weekly_earnings_probe_df()
    captured_queries: dict = {}
    render_ctx: dict = {}

    def fake_bq_parallel(_client, queries):
        captured_queries.update(dict(queries))
        out = {}
        for key, entry in queries.items():
            sql = _query_text(entry).lower()
            if "stg_earnings_calendar" in sql:
                out[key] = earn_df.copy()
            else:
                out[key] = _empty_df()
        return out

    class _QueryJob:
        def to_dataframe(self):
            return _empty_df()

    client = MagicMock()
    client.query = lambda *_a, **_kw: _QueryJob()

    def capture_render(_template, **ctx):
        render_ctx.update(ctx)
        return "OK"

    patches = [
        patch.object(mod, "get_bigquery_client", return_value=client),
        patch.object(mod, "_user_account_list", return_value=["synthetic-account"]),
        patch.object(mod, "get_user_profile", return_value={"timezone": "America/New_York"}),
        patch.object(mod, "current_user", user),
        patch("flask_login.utils._get_user", return_value=user),
        patch("app.routes._redirect_if_no_accounts", return_value=None),
        patch.object(mod, "bump_review_visit", return_value={"last_visit_at": None}),
        patch.object(mod, "_bq_parallel", fake_bq_parallel),
        patch.object(mod, "_since_last_looked", return_value=None),
        patch.object(mod, "render_template", side_effect=capture_render),
    ]

    for p in patches:
        p.start()
    try:
        with app.test_request_context("/weekly-review?mode=friday"):
            mod.weekly_review()
    finally:
        for p in patches:
            p.stop()

    return captured_queries, render_ctx


# ───────────────────────── multi-shape template fixtures ─────────────────────────


def _hero_payload():
    """A canonical earnings hero payload covering the popular variable shapes."""
    d = date.today() + timedelta(days=5)
    return {
        "date": d.strftime("%Y-%m-%d"),
        "display": d.strftime("%a %b %-d") if hasattr(d, "strftime") else str(d),
        "days_until": 5,
        "days": 5,
    }


def _render_position_detail(**overrides) -> str:
    """Pre-populate every reasonable hero variable so any template choice surfaces.

    Implementers may read `symbol_next_earnings`, `next_earnings`, scalar
    `earnings_days`/`earnings_date`, or any combination — the rendered HTML
    should still expose "Earnings" and the 5-day countdown.
    """
    from flask import render_template

    from app import app

    hero = _hero_payload()
    ctx = {
        "symbol": "PROBE",
        "kpis": types.SimpleNamespace(
            total_return=1.0,
            total_trades=1,
            first_trade="2026-01-01",
            last_trade="2026-01-02",
            unrealized_pnl=0.0,
            realized_pnl=1.0,
            dividend_income=0.0,
            premium_collected=0.0,
            num_winners=0,
            num_losers=0,
            avg_days=1.0,
            win_rate=0.0,
        ),
        "overall_status": "Open",
        "strategy_rows": [],
        "breakdown_rows": [],
        "trades": [],
        "trade_outcomes": [],
        "current_positions": [],
        "option_matrices": [],
        "sessions": [],
        "selected_legs": [],
        "leg_param": "",
        "chart_data_json": '{"dates":[],"equity":[],"options":[],"dividends":[],"total":[]}',
        "has_underlying_price": False,
        "prices_through_date": "",
        "accounts": [],
        "selected_account": "",
        "symbol_sector": "",
        "symbol_subsector": "",
        "symbol_company": "",
        # multi-shape hero payloads — implementers may consume any of these
        "symbol_next_earnings": hero,
        "next_earnings": hero,
        "earnings": hero,
        "earnings_days": 5,
        "earnings_date": hero["date"],
        "earnings_date_display": hero["display"],
        "next_earnings_days": 5,
        "next_earnings_date": hero["date"],
        "next_earnings_display": hero["display"],
        "invariant_warning": None,
        "viewer_is_admin": False,
        "tabs": [],
        "active_symbol": "PROBE",
        "tab_href_base": "/position/",
        "tab_href_suffix": "",
        "mode": "navigate",
    }
    ctx.update(overrides)
    with app.test_request_context("/position/PROBE"):
        return render_template("position_detail.html", **ctx)


def _render_weekly_review(**overrides) -> str:
    """Pre-populate every reasonable bucket shape so any template choice surfaces."""
    from flask import render_template

    from app import app

    near_items = [
        {
            "symbol": "NEAR",
            "ticker": "NEAR",
            "company": "Example Near",
            "long_name": "Example Near",
            "sector": "Tech",
            "subsector": "Software",
            "earnings_date": "2026-06-05",
            "earnings_date_display": "Thu Jun 5",
            "next_earnings_date": "2026-06-05",
            "display": "Thu Jun 5",
            "days_until": 3,
            "days": 3,
        }
    ]
    far_items = [
        {
            "symbol": "FAR",
            "ticker": "FAR",
            "company": "Example Far",
            "long_name": "Example Far",
            "sector": "Health",
            "subsector": "Devices",
            "earnings_date": "2026-06-13",
            "earnings_date_display": "Thu Jun 13",
            "next_earnings_date": "2026-06-13",
            "display": "Thu Jun 13",
            "days_until": 11,
            "days": 11,
        }
    ]
    grouped = {"this_week": near_items, "next_week": far_items}
    ctx = {
        "title": "Friday Review",
        "mode": "friday",
        # Shape A — separate top-level keys
        "upcoming_earnings_this_week": near_items,
        "upcoming_earnings_next_week": far_items,
        # Shape B — single grouped dict
        "upcoming_earnings": grouped,
        "earnings_upcoming": grouped,
        # Shape C — single combined list (rows carry days_until)
        "earnings_list": near_items + far_items,
        "upcoming_announcements": near_items + far_items,
        # Common probe-style aliases (template-only tests must still render)
        "earnings_within_7": near_items,
        "earnings_8_to_14": far_items,
        "earnings_days_8_to_14": far_items,
    }
    ctx.update(overrides)
    with app.test_request_context("/weekly-review"):
        return render_template("weekly_review.html", **ctx)


# ───────────────────────── tests ─────────────────────────


class TestPositionDetailEarningsPath:
    def test_parallel_fetch_includes_symbol_calendar_query(self):
        mod = _routes_module()
        queries, _ = _drive_position_detail(mod)
        sql = _find_earnings_query(queries)
        assert sql is not None, (
            "Symbol workspace must add an earnings calendar query to the parallel "
            f"BigQuery batch. Saw keys: {list(queries)}"
        )
        assert "stg_earnings_calendar" in sql and "limit 1" in sql, (
            "Symbol calendar fetch must read a single upcoming row for the active ticker"
        )

    def test_hero_context_carries_countdown_payload(self):
        mod = _routes_module()
        _, ctx = _drive_position_detail(mod, days_until=5)
        days = _find_days_in_ctx(ctx, equals=5)
        assert days == 5, (
            "Symbol workspace must surface a 5-day countdown to the template. "
            "Looked for an earnings-flavored scalar (e.g. earnings_days=5) or a "
            "dict (e.g. symbol_next_earnings.days_until=5). "
            f"Earnings-related ctx keys: {[k for k in _ctx_keys_lower(ctx) if 'earning' in k]}"
        )

    def test_hero_omitted_when_no_upcoming_row(self):
        mod = _routes_module()
        queries, ctx = _drive_position_detail(mod, empty_earnings=True)
        sql = _find_earnings_query(queries)
        assert sql is not None, (
            "Symbol workspace must still register a calendar fetch before suppressing "
            "an empty hero"
        )
        leaked = _find_days_in_ctx(ctx, equals=None)
        assert leaked is None, (
            "When the calendar returns no upcoming row the symbol header must not "
            f"fabricate a countdown; ctx still carries days_until={leaked}"
        )


class TestWeeklyReviewEarningsPath:
    def test_parallel_fetch_includes_holdings_calendar_query(self):
        mod = _weekly_review_module()
        queries, _ = _drive_weekly_review(mod)
        sql = _find_earnings_query(queries)
        assert sql is not None, (
            "Weekly review must add a held-symbol announcement query to the parallel "
            f"batch. Saw keys: {list(queries)}"
        )
        assert "stg_earnings_calendar" in sql, (
            "Holdings calendar fetch must read from the analytics earnings calendar mart"
        )
        assert "int_enriched_current" in sql or "holdings" in sql, (
            "Holdings calendar fetch must be scoped to symbols the user still carries"
        )

    def test_weekly_query_spans_fourteen_calendar_days(self):
        mod = _weekly_review_module()
        queries, _ = _drive_weekly_review(mod)
        sql = _find_earnings_query(queries)
        assert sql is not None
        assert (
            "interval 14 day" in sql
            or "interval 14" in sql
            or ("date_add" in sql and "14" in sql)
            or ("date_sub" in sql and "14" in sql)
        ), "Weekly review must cover a fourteen-calendar-day announcement horizon"

    def test_buckets_near_and_far_announcements(self):
        mod = _weekly_review_module()
        _, ctx = _drive_weekly_review(mod)
        near, far = _collect_upcoming_buckets(ctx)
        earnings_keys = [k for k in _ctx_keys_lower(ctx) if "earning" in k]
        assert "NEAR" in near, (
            "Announcements within the next seven days must land in the this-week "
            f"bucket. Earnings-related ctx keys: {earnings_keys}; near={near}; far={far}"
        )
        assert "FAR" in far, (
            "Announcements eight to fourteen days out must land in the next-week "
            f"bucket. Earnings-related ctx keys: {earnings_keys}; near={near}; far={far}"
        )
        assert not (near & far), "Each symbol must appear in only one bucket"


class TestTemplateSurfaces:
    def test_position_hero_template_renders_countdown(self):
        html = _render_position_detail()
        assert "Earnings" in html, "Symbol header must render an Earnings label"
        assert any(
            tok in html for tok in ("(5d)", "5d", "5 days", "in 5", "5 day")
        ), "Symbol header must render a 5-day countdown next to the Earnings label"

    def test_weekly_review_template_lists_upcoming_holdings(self):
        html = _render_weekly_review()
        lower = html.lower()
        assert "earning" in lower, (
            "Weekly review must render an earnings-related section heading or label"
        )
        assert "NEAR" in html, "Held symbol with near earnings must render"
        assert "FAR" in html, "Held symbol with far earnings must render"


class TestRegressionGuards:
    def test_routes_module_imports(self):
        mod = _routes_module()
        assert hasattr(mod, "position_detail")

    def test_weekly_review_route_registered(self):
        from app import app

        rules = {rule.rule for rule in app.url_map.iter_rules()}
        assert "/weekly-review" in rules
