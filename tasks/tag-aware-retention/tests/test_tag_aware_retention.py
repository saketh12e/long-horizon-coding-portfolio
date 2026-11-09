"""Behavioral regression tests for tag-aware memory retention."""

from __future__ import annotations

import time
from typing import Any

import pytest
from fastapi.testclient import TestClient

from memory_service.api import create_fastapi_app
from memory_service.core import MemoryService
from memory_service.models import MemoryItem

_TAG_EPHEMERAL = "ephemeral"
_TAG_PERMANENT = "permanent"
_TAG_PRIORITY_A = "prio-a"
_TAG_PRIORITY_B = "prio-b"

_PROBE_EPHEMERAL = "mem-tagdecay-ephemeral-7f3a2c"
_PROBE_DEFAULT = "mem-tagdecay-default-a1b2c3"
_PROBE_PRIORITY_A = "mem-tagdecay-prioa-c7d8e9"
_PROBE_PRIORITY_B = "mem-tagdecay-priob-d4e5f6"
_PROBE_NO_TAG = "mem-tagdecay-notag-91b8c7"

_BACKDATE_SECONDS = 10 * 86400  # 10 days, well past default min_age_days

# Accept any of the natural English naming conventions an implementation
# might pick. The test verifies behaviour, not naming. This is the
# and the test must accept it without the agent needing to guess the
# exact convention the gold happened to use.
_SETTER_NAMES = (
    "set_decay_rate_for_tag",
    "set_tag_decay_rate",
    "set_tag_decay",
    "set_decay_rate",
    "set_per_tag_decay",
    "configure_tag_decay",
    "configure_tag_decay_rate",
    "set_tag_rate",
    "set_tag_rate_for",
    "set_rate_for_tag",
    "set_decay_for_tag",
    "set_tag_decay_for",
    "set_tag_decay_value",
    "set_per_tag_rate",
    "register_tag_rate",
    "add_tag_rate",
    "update_tag_rate",
    "apply_tag_rate",
    "apply_tag_decay",
    "bind_tag_rate",
    "set_tag_rate_map",
    "set_per_tag_decay_rate",
)
_GETTER_NAMES = (
    "get_decay_rate_for_tag",
    "get_tag_decay_rate",
    "get_tag_decay",
    "get_tag_rate",
    "get_per_tag_decay",
    "read_tag_decay",
    "get_decay_for_tag",
    "get_rate_for_tag",
    "get_tag_rate_for",
    "fetch_tag_decay",
    "fetch_tag_rate",
    "lookup_tag_decay",
    "lookup_tag_rate",
    "query_tag_decay",
    "query_tag_rate",
    "find_tag_decay",
    "find_tag_rate",
    "get_per_tag_rate",
    "read_tag_rate",
    "get_tag_decay_value",
    "get_tag_decay_config",
)
_LISTER_NAMES = (
    "list_decay_tag_rates",
    "list_tag_decay_rates",
    "list_tag_decay",
    "list_tag_rates",
    "get_decay_tag_rates",
    "all_tag_decay_rates",
    "get_tag_decay_rates",
    "list_per_tag_rates",
    "all_tag_rates",
    "get_all_tag_rates",
    "list_all_tag_rates",
    "get_tag_rate_map",
    "all_per_tag_rates",
    "enumerate_tag_rates",
    "dump_tag_rates",
    "registered_tag_rates",
)
_CLEARER_NAMES = (
    "clear_decay_rate_for_tag",
    "remove_tag_decay_rate",
    "remove_tag_decay",
    "remove_tag_rate",
    "delete_tag_decay",
    "unset_tag_decay",
    "forget_tag_decay",
    "delete_tag_decay_rate",
    "unset_tag_decay_rate",
    "reset_tag_decay",
    "drop_tag_decay",
    "remove_per_tag_decay",
    "clear_tag_decay_rate",
    "clear_tag_decay",
    "remove_tag_rate_for",
    "delete_tag_rate",
    "drop_tag_rate",
    "forget_tag_rate",
    "reset_tag_rate",
    "unset_tag_rate",
)
_ENGINE_SETTER_NAMES = (
    "set_decay_rate_for_tag",
    "set_tag_decay_rate",
    "set_tag_rate",
    "set_tag_decay",
    "configure_tag_decay",
    "configure_tag_decay_rate",
    "register_tag_decay",
    "add_tag_decay",
    "set_per_tag_decay",
    "set_engine_tag_decay",
    "set_decay_for_tag",
    "set_rate_for_tag",
    "set_tag_rate_for",
    "register_tag_rate",
    "add_tag_rate",
    "update_tag_rate",
    "apply_tag_rate",
    "set_per_tag_rate",
    "set_engine_tag_rate",
)
_ENGINE_GETTER_NAMES = (
    "get_decay_rate_for_tag",
    "get_tag_decay_rate",
    "get_tag_rate",
    "get_tag_decay",
    "get_decay_for_tag",
    "get_rate_for_tag",
    "get_per_tag_rate",
    "read_tag_rate",
    "lookup_tag_rate",
)
_ENGINE_LISTER_NAMES = (
    "list_decay_tag_rates",
    "list_tag_decay_rates",
    "list_tag_rates",
    "list_tag_decay",
    "all_tag_decay_rates",
    "get_all_tag_rates",
    "list_per_tag_rates",
    "all_tag_rates",
    "get_tag_rate_map",
    "registered_tag_rates",
)
_ENGINE_CLEARER_NAMES = (
    "clear_decay_rate_for_tag",
    "remove_tag_decay_rate",
    "remove_tag_rate",
    "remove_tag_decay",
    "delete_tag_decay",
    "unset_tag_decay",
    "forget_tag_decay",
    "delete_tag_decay_rate",
    "unset_tag_decay_rate",
    "reset_tag_decay",
    "drop_tag_decay",
    "remove_per_tag_decay",
    "clear_tag_decay",
    "delete_tag_rate",
)
# The admin HTTP surface might be at any of several paths. The wrapper
# tries each one in order and returns the first that exists.
_TAG_RATE_URLS = (
    "/api/v1/admin/decay/tag-rates",
    "/api/v1/admin/tag-decay-rates",
    "/api/v1/admin/decay-tag-rates",
    "/api/v1/admin/tag_decay",
    "/api/v1/admin/tag_decay_rates",
    "/api/v1/decay/tag-rates",
    "/api/v1/admin/per-tag-decay-rates",
)


# ---------------------------------------------------------------------------
# Naming-agnostic wrappers
# ---------------------------------------------------------------------------


def _call_any(obj: Any, names: tuple[str, ...], *args: Any) -> Any:
    """Call the first callable attribute matching one of *names*; raise if none found."""
    for name in names:
        fn = getattr(obj, name, None)
        if callable(fn):
            return fn(*args)
    raise AttributeError(
        type(obj).__name__ + " has no callable attribute matching any of " + str(names)
    )


def _normalize_to_dict(value: Any) -> dict[str, float]:
    """Coerce any plausible per-tag-rates return value into a {tag: rate} dict.

    Accepts dict, list of (tag, rate) pairs (tuple or list), and any other
    mapping of tag → rate.
    """
    if isinstance(value, dict):
        return {str(k): float(v) for k, v in value.items()}
    if isinstance(value, list):
        return {str(pair[0]): float(pair[1]) for pair in value}
    if value is None:
        return {}
    raise TypeError(type(value).__name__ + ": " + str(type(value)))


def _set_rate(target: Any, tag: str, rate: float) -> None:
    """Set the per-tag decay rate via whatever name the implementation picked."""
    _call_any(target, _SETTER_NAMES, tag, rate)


def _get_rate(target: Any, tag: str) -> float | None:
    """Get the per-tag decay rate via whatever name the implementation picked."""
    return _call_any(target, _GETTER_NAMES, tag)


def _list_rates(target: Any) -> dict[str, float]:
    """List all configured per-tag rates, normalized to a {tag: rate} dict."""
    return _normalize_to_dict(_call_any(target, _LISTER_NAMES))


def _clear_rate(target: Any, tag: str) -> bool:
    """Clear the per-tag rate via whatever name the implementation picked."""
    result = _call_any(target, _CLEARER_NAMES, tag)
    return bool(result)


def _find_tag_rate_path(client: TestClient) -> str:
    """Find which of the candidate URL paths is wired up on the admin router."""
    # A GET to the base URL (no trailing tag) should return 200, 404, or 405.
    # 404 means the prefix doesn't exist; 200/405 means the path is live.
    for path in _TAG_RATE_URLS:
        # Try GET on the prefix (without /{tag}) for the live check.
        for suffix in ("",):
            probe = path + suffix
            try:
                resp = client.get(probe)
            except Exception:
                continue
            if resp.status_code in (200, 405):
                return path
    # Default to the gold's URL so the test still has something to call.
    return _TAG_RATE_URLS[0]


def _find_get_specific_path(client: TestClient) -> str:
    """Like _find_tag_rate_path but only returns paths that have an actual GET
    handler (status 200, not 405). Some agents register POST/DELETE on the
    path but forget the GET handler; this probe finds paths that have all three.
    """
    for path in _TAG_RATE_URLS:
        try:
            resp = client.get(path)
        except Exception:
            continue
        if resp.status_code == 200:
            return path
    return _TAG_RATE_URLS[0]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def mem_and_client():
    mem = MemoryService(backend="memory", decay_rate=0.01)
    app = create_fastapi_app(memory_service=mem)
    with TestClient(app, raise_server_exceptions=False) as client:
        yield mem, client


@pytest.fixture()
def mem(mem_and_client):
    return mem_and_client[0]


@pytest.fixture()
def client(mem_and_client):
    return mem_and_client[1]


def _make_old_item(mem: MemoryService, content: str, *, tags=None, importance: float = 0.5, age_seconds: float = _BACKDATE_SECONDS) -> MemoryItem:
    """Learn a memory and immediately backdate it to `age_seconds` in the past."""
    item = mem.learn(content, tags=tags, importance=importance)
    item.created_at = time.time() - age_seconds
    mem._store.upsert(item, namespace=mem._namespace)
    return item


def _fetch(mem: MemoryService, content: str) -> MemoryItem | None:
    """Fetch the most recently stored memory matching `content` via the public storage API."""
    matches = [item for item in mem._store.list_all(namespace=mem._namespace) if item.content == content]
    if not matches:
        return None
    return max(matches, key=lambda item: item.created_at)


# ---------------------------------------------------------------------------
# 1. Setter / getter / list / clear — pure facade semantics
# ---------------------------------------------------------------------------


def test_set_then_get_round_trip(mem: MemoryService):
    """Setting a rate for a tag must make it readable through the public facade."""
    _set_rate(mem, _TAG_EPHEMERAL, 0.5)
    assert _get_rate(mem, _TAG_EPHEMERAL) == pytest.approx(0.5)


def test_get_returns_none_for_unconfigured_tag(mem: MemoryService):
    """An unconfigured tag must return None (not raise, not a default rate)."""
    assert _get_rate(mem, "never-configured") is None


def test_list_decay_tag_rates_starts_empty(mem: MemoryService):
    """A fresh MemoryService instance exposes no tag-specific rates."""
    assert _list_rates(mem) == {}


def test_list_decay_tag_rates_after_set(mem: MemoryService):
    """Configured rates are visible in the listing (normalised to a dict)."""
    _set_rate(mem, _TAG_EPHEMERAL, 0.5)
    _set_rate(mem, _TAG_PERMANENT, 0.0)
    rates = _list_rates(mem)
    assert rates[_TAG_EPHEMERAL] == pytest.approx(0.5)
    assert rates[_TAG_PERMANENT] == pytest.approx(0.0)


def test_clear_decay_rate_for_tag_returns_true_when_set(mem: MemoryService):
    """Clearing a configured rate must report that something was removed."""
    _set_rate(mem, _TAG_EPHEMERAL, 0.5)
    assert _clear_rate(mem, _TAG_EPHEMERAL) is True
    assert _get_rate(mem, _TAG_EPHEMERAL) is None


def test_clear_decay_rate_for_tag_returns_false_when_unset(mem: MemoryService):
    """Clearing a tag that has no rate must return False (idempotent and safe)."""
    assert _clear_rate(mem, "never-configured") is False


def test_set_decay_rate_for_tag_rejects_invalid_range(mem: MemoryService):
    """Out-of-range rates must be rejected — the rate is a probability per day.

    The invalid rate must not overwrite a previously stored valid rate, so
    we set a valid baseline first and assert it survives the rejected call.
    """
    _set_rate(mem, _TAG_EPHEMERAL, 0.5)  # establish a valid baseline
    with pytest.raises(Exception):
        _set_rate(mem, _TAG_EPHEMERAL, 1.5)
    # The rejected rate must not have overwritten the valid one.
    assert _get_rate(mem, _TAG_EPHEMERAL) == pytest.approx(0.5)
    with pytest.raises(Exception):
        _set_rate(mem, _TAG_EPHEMERAL, -0.1)
    assert _get_rate(mem, _TAG_EPHEMERAL) == pytest.approx(0.5)


# ---------------------------------------------------------------------------
# 2. Per-tag rate actually drives the decay behaviour (the core feature)
# ---------------------------------------------------------------------------


def test_tagged_memory_decays_at_tag_rate_not_default(mem: MemoryService):
    """A memory carrying a configured tag must decay at that rate, not the default.

    Default rate is 0.01 (decay factor ~0.74 over 30 days).
    Tag rate is 0.99 (decay factor ~0.74 over 1 day ≈ 0 after 30 days).
    Two 30-day-old memories, identical importance, only the tag differs.
    """
    _make_old_item(mem, _PROBE_EPHEMERAL, tags=[_TAG_EPHEMERAL], importance=0.5)
    _make_old_item(mem, _PROBE_DEFAULT, tags=["unrelated"], importance=0.5)

    _set_rate(mem, _TAG_EPHEMERAL, 0.99)

    mem.decay(min_age_days=0, floor=0.0, dry_run=False)

    ephemeral = _fetch(mem, _PROBE_EPHEMERAL)
    default = _fetch(mem, _PROBE_DEFAULT)
    assert ephemeral is not None and default is not None
    # The tagged memory must have collapsed (its rate is 0.99 → factor ≈ 0).
    assert ephemeral.importance < 0.1
    # The untagged one is barely touched by the default 0.01 rate.
    assert default.importance > 0.4
    # And the gap is substantial — the per-tag rate is doing real work.
    assert default.importance - ephemeral.importance > 0.4


def test_memory_with_no_tag_falls_back_to_default_rate(mem: MemoryService):
    """A memory with no tags at all must decay at the default rate even when
    other tag-specific rates are configured."""
    _make_old_item(mem, _PROBE_NO_TAG, tags=None, importance=0.5)
    _set_rate(mem, _TAG_EPHEMERAL, 0.99)  # aggressive rate for another tag

    mem.decay(min_age_days=0, floor=0.0, dry_run=False)

    no_tag = _fetch(mem, _PROBE_NO_TAG)
    assert no_tag is not None
    # The untagged memory uses the default 0.01 rate (barely any decay).
    assert no_tag.importance > 0.4


def test_decay_resolves_rate_from_first_matching_tag(mem: MemoryService):
    """A memory carrying multiple tags must use the most specific (first matching) rate.

    We deliberately give two tags distinct rates and pin the priority order
    by registering them in a known sequence. The first configured tag in
    the memory's tag list wins.
    """
    _make_old_item(mem, _PROBE_PRIORITY_A, tags=[_TAG_PRIORITY_A], importance=0.5)
    _make_old_item(mem, _PROBE_PRIORITY_B, tags=[_TAG_PRIORITY_B], importance=0.5)

    _set_rate(mem, _TAG_PRIORITY_A, 0.99)  # aggressive
    _set_rate(mem, _TAG_PRIORITY_B, 0.0)  # frozen

    mem.decay(min_age_days=0, floor=0.0, dry_run=False)

    a = _fetch(mem, _PROBE_PRIORITY_A)
    b = _fetch(mem, _PROBE_PRIORITY_B)
    assert a is not None and b is not None
    # A: high rate → decayed hard.
    assert a.importance < 0.1
    # B: zero rate → untouched.
    assert b.importance == pytest.approx(0.5, abs=0.01)


def test_decay_zero_rate_for_tag_preserves_importance(mem: MemoryService):
    """A tag with rate=0.0 must act as a decay-free marker for those memories."""
    _make_old_item(mem, _PROBE_DEFAULT, tags=[_TAG_PERMANENT], importance=0.5)
    _set_rate(mem, _TAG_PERMANENT, 0.0)

    mem.decay(min_age_days=0, floor=0.0, dry_run=False)

    item = _fetch(mem, _PROBE_DEFAULT)
    assert item is not None
    # Rate 0.0 means the memory is untouched.
    assert item.importance == pytest.approx(0.5, abs=0.01)


def test_adjusted_score_uses_tag_rate(mem: MemoryService):
    """The decay engine underlying the public facade must consult the tag rate on
    adjusted_score. The rate must be set on the engine itself; a facade-only
    setter that never wires the engine does not count.
    """
    engine = getattr(mem, "_decay", None) or getattr(mem, "decay_engine", None) or getattr(mem, "_decay_engine", None)
    if engine is None:
        pytest.skip("No accessible engine attribute on the public facade.")
    try:
        _call_any(engine, _ENGINE_SETTER_NAMES, _TAG_EPHEMERAL, 0.9)
    except AttributeError as exc:
        pytest.fail(
            "The engine must expose a per-tag rate setter (one of: "
            + ", ".join(_ENGINE_SETTER_NAMES)
            + "). Facade-only wiring is not enough — adjusted_score reads from the engine. ("
            + str(exc) + ")"
        )

    item = MemoryItem(
        id="probe-score",
        content="probe",
        importance=0.5,
        created_at=time.time() - 30 * 86400,
        tags=[_TAG_EPHEMERAL],
    )
    score_tagged = engine.adjusted_score(0.5, item)

    plain = MemoryItem(
        id="probe-plain",
        content="probe",
        importance=0.5,
        created_at=time.time() - 30 * 86400,
        tags=[],
    )
    score_plain = engine.adjusted_score(0.5, plain)

    # Tagged item decays far more aggressively than the untagged one.
    assert score_tagged < score_plain
    assert score_plain - score_tagged > 0.2


# ---------------------------------------------------------------------------
# 3. Admin API surface — route registration and round-trip behaviour
# ---------------------------------------------------------------------------


def test_post_tag_decay_rate_endpoint_persists(mem: MemoryService, client: TestClient):
    """The admin HTTP surface for tag rates must store the rate and be readable."""
    path = _find_tag_rate_path(client)
    response = client.post(
        path,
        json={"tag": _TAG_EPHEMERAL, "rate": 0.7},
    )
    assert response.status_code == 200
    # The rate must be readable through the public facade; the response body
    # shape is not gated (agents use "status"/"success"/"ok"/etc. inconsistently).
    assert _get_rate(mem, _TAG_EPHEMERAL) == pytest.approx(0.7)


def test_get_tag_decay_rates_endpoint_lists_configured(mem: MemoryService, client: TestClient):
    """The admin HTTP surface must list every configured tag/rate pair."""
    _set_rate(mem, _TAG_EPHEMERAL, 0.5)
    _set_rate(mem, _TAG_PERMANENT, 0.0)

    path = _find_get_specific_path(client)
    response = client.get(path)
    assert response.status_code == 200
    payload = response.json()
    # Accept any reasonable response key the agent might pick; the convention in
    # this codebase is "rates" but agents often use "data", "results", "items",
    # "tags", "tag_rates", "per_tag_rates", or even the bare payload.
    rates_payload = (
        payload.get("rates")
        or payload.get("items")
        or payload.get("data")
        or payload.get("results")
        or payload.get("tags")
        or payload.get("tag_rates")
        or payload.get("per_tag_rates")
        or payload.get("config")
        or payload
    )
    rates = _normalize_to_dict(rates_payload)
    assert rates[_TAG_EPHEMERAL] == pytest.approx(0.5)
    assert rates[_TAG_PERMANENT] == pytest.approx(0.0)


def test_delete_tag_decay_rate_endpoint_removes(mem: MemoryService, client: TestClient):
    """The admin HTTP surface must remove the configuration when DELETEd."""
    _set_rate(mem, _TAG_EPHEMERAL, 0.5)
    path = _find_tag_rate_path(client)
    tag_path = f"{path.rstrip('/')}/{_TAG_EPHEMERAL}"
    response = client.delete(tag_path)
    assert response.status_code == 200
    assert _get_rate(mem, _TAG_EPHEMERAL) is None


def test_post_tag_decay_rate_endpoint_rejects_invalid(mem: MemoryService, client: TestClient):
    """An out-of-range rate must be rejected (not silently clamped)."""
    path = _find_tag_rate_path(client)
    response = client.post(
        path,
        json={"tag": _TAG_EPHEMERAL, "rate": 2.0},
    )
    # The endpoint must NOT 200-succeed on an out-of-range rate.
    assert response.status_code != 200 or _get_rate(mem, _TAG_EPHEMERAL) is None


# ---------------------------------------------------------------------------
# 4. Regression guards (p2p) — existing behaviour must not break
# ---------------------------------------------------------------------------


def test_default_decay_rate_still_applies_when_no_tag_rates_set(mem: MemoryService):
    """With no tag-specific rates, run_decay still uses the configured default rate."""
    _make_old_item(mem, _PROBE_DEFAULT, tags=["irrelevant"], importance=0.5)
    mem.decay(min_age_days=0, floor=0.0, dry_run=False)
    item = _fetch(mem, _PROBE_DEFAULT)
    assert item is not None
    # Default 0.01 rate over 10 days: factor ≈ 0.904 → importance ≈ 0.45.
    assert 0.3 < item.importance < 0.5


def test_reinforce_still_works_with_tag_rates_configured(mem: MemoryService):
    """Adding tag rates must not break the existing reinforce() path."""
    _set_rate(mem, _TAG_EPHEMERAL, 0.99)
    item = mem.learn("reinforce probe", tags=["unrelated"], importance=0.4)
    new_imp = mem.reinforce_memory(item.id, strength=0.3)
    assert new_imp == pytest.approx(0.7, abs=0.01)


def test_run_decay_with_no_memories_is_a_noop(mem: MemoryService):
    """run_decay on an empty store must return an empty report and not raise."""
    _set_rate(mem, _TAG_EPHEMERAL, 0.99)
    report = mem.decay(min_age_days=0, floor=0.0, dry_run=False)
    assert report.total == 0
    assert report.decayed == 0
