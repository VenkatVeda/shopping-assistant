"""Result cache and per-user rate limit for the inventory check (SAGOI-14). No network, no credentials."""
import logging

import pytest

from core import inventory_limits as limits
from core import inventory_service as svc
from core.genie_one_client import GenieOneError, GenieOneTimeout, StockLookup
from core.inventory_limits import RateLimiter, TtlCache
from tests.test_inventory_live import FakeClient

PID = "998643070"
OTHER = "995766940"


class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


@pytest.fixture(autouse=True)
def _fresh_limits():
    limits.reset()
    yield
    limits.reset()


# ------------------------------------------------------------------ the cache

def test_a_cached_value_is_returned_until_it_expires():
    clock = FakeClock()
    cache = TtlCache(clock=clock)
    cache.set("a", {"v": 1}, ttl_seconds=30)
    assert cache.get("a") == {"v": 1}
    clock.advance(29.9)
    assert cache.get("a") == {"v": 1}
    clock.advance(0.2)
    assert cache.get("a") is None and len(cache) == 0


def test_a_zero_ttl_turns_the_cache_off():
    cache = TtlCache()
    cache.set("a", {"v": 1}, ttl_seconds=0)
    assert cache.get("a") is None and len(cache) == 0


def test_the_cache_never_grows_past_its_limit_and_drops_the_oldest():
    clock = FakeClock()
    cache = TtlCache(max_entries=3, clock=clock)
    for i in range(5):
        cache.set(f"k{i}", {"v": i}, ttl_seconds=30)
    assert len(cache) == 3
    assert cache.get("k0") is None and cache.get("k1") is None and cache.get("k4") == {"v": 4}


def test_expired_entries_are_dropped_before_live_ones():
    clock = FakeClock()
    cache = TtlCache(max_entries=2, clock=clock)
    cache.set("old", {"v": 1}, ttl_seconds=5)
    cache.set("keep", {"v": 2}, ttl_seconds=60)
    clock.advance(10)
    cache.set("new", {"v": 3}, ttl_seconds=60)
    assert cache.get("keep") == {"v": 2} and cache.get("new") == {"v": 3} and cache.get("old") is None


# ------------------------------------------------------------------ the rate limiter

def test_twenty_calls_a_minute_are_allowed_and_the_21st_is_refused():
    limiter = RateLimiter(clock=FakeClock())
    assert all(limiter.allow("u1", limit=20)[0] for _ in range(20))
    allowed, retry_after = limiter.allow("u1", limit=20)
    assert allowed is False and 1 <= retry_after <= 60


def test_the_limit_is_per_user():
    limiter = RateLimiter(clock=FakeClock())
    for _ in range(20):
        limiter.allow("u1", limit=20)
    assert limiter.allow("u1", limit=20)[0] is False
    assert limiter.allow("u2", limit=20)[0] is True


def test_the_window_slides_so_calls_are_allowed_again():
    clock = FakeClock()
    limiter = RateLimiter(clock=clock)
    for _ in range(3):
        limiter.allow("u1", limit=3)
    assert limiter.allow("u1", limit=3)[0] is False
    clock.advance(60)
    assert limiter.allow("u1", limit=3)[0] is True


def test_a_refused_call_is_not_counted_and_retry_after_counts_down():
    clock = FakeClock()
    limiter = RateLimiter(clock=clock)
    for _ in range(2):
        limiter.allow("u1", limit=2)
    first = limiter.allow("u1", limit=2)[1]
    clock.advance(30)
    second = limiter.allow("u1", limit=2)[1]
    assert second < first
    clock.advance(30)
    assert limiter.allow("u1", limit=2)[0] is True


def test_a_limit_of_zero_means_no_limit():
    limiter = RateLimiter(clock=FakeClock())
    assert all(limiter.allow("u1", limit=0)[0] for _ in range(500))


def test_the_limit_comes_from_the_setting(monkeypatch):
    monkeypatch.delenv("INVENTORY_RATE_LIMIT_PER_MINUTE", raising=False)
    assert limits.rate_limit_per_minute() == 20
    monkeypatch.setenv("INVENTORY_RATE_LIMIT_PER_MINUTE", "5")
    assert limits.rate_limit_per_minute() == 5
    monkeypatch.setenv("INVENTORY_RATE_LIMIT_PER_MINUTE", "not a number")
    assert limits.rate_limit_per_minute() == 20


def test_the_cache_time_comes_from_the_setting(monkeypatch):
    monkeypatch.delenv("INVENTORY_CACHE_SECONDS", raising=False)
    assert limits.cache_seconds() == 30
    monkeypatch.setenv("INVENTORY_CACHE_SECONDS", "10")
    assert limits.cache_seconds() == 10
    monkeypatch.setenv("INVENTORY_CACHE_SECONDS", "-5")
    assert limits.cache_seconds() == 0


# ------------------------------------------------------------------ the cache inside the service

@pytest.fixture
def live(monkeypatch):
    monkeypatch.setenv("INVENTORY_CHECK_MODE", "live")
    monkeypatch.delenv("INVENTORY_CACHE_SECONDS", raising=False)

    def use(*outcomes):
        """A client that answers with each outcome in turn (the last one repeats)."""
        queue = list(outcomes)

        class Sequence(FakeClient):
            def lookup(self, product_id, timeout_s=None):
                self.calls.append(product_id)
                item = queue.pop(0) if len(queue) > 1 else queue[0]
                if isinstance(item, BaseException):
                    raise item
                return item

        fake = Sequence(None)
        monkeypatch.setattr(svc, "get_client", lambda: fake)
        return fake
    return use


def test_a_repeat_check_inside_the_cache_time_does_not_call_genie(live):
    fake = live(StockLookup(status="in_stock", as_of="2026-06-08T06:01:26.513Z"))
    first = svc.run_check(PID)
    second = svc.run_check(PID)
    assert fake.calls == [PID]                                   # Genie was asked once
    assert first.cache_hit is False and second.cache_hit is True
    assert second.result["status"] == "in_stock" and second.result["as_of"] == first.result["as_of"]
    assert second.result["request_id"] != first.result["request_id"]
    assert set(second.result) == set(first.result)                # the public shape does not change


def test_the_cache_expires_after_its_time(live, monkeypatch):
    clock = FakeClock()
    monkeypatch.setattr(svc, "result_cache", TtlCache(clock=clock))
    fake = live(StockLookup(status="in_stock", as_of="t"))
    svc.run_check(PID)
    clock.advance(31)
    again = svc.run_check(PID)
    assert fake.calls == [PID, PID] and again.cache_hit is False


@pytest.mark.parametrize("status", ["in_stock", "out_of_stock", "not_found"])
def test_every_real_answer_is_cached(live, status):
    fake = live(StockLookup(status=status, as_of=None if status == "not_found" else "t"))
    svc.run_check(PID)
    assert svc.run_check(PID).cache_hit is True and len(fake.calls) == 1


@pytest.mark.parametrize("failure", [GenieOneTimeout("too slow"), GenieOneError("broke"),
                                     StockLookup(status="unknown", reason="upstream_error")])
def test_an_unknown_result_is_never_cached(live, failure):
    fake = live(failure, StockLookup(status="in_stock", as_of="t"))
    first = svc.run_check(PID)
    second = svc.run_check(PID)
    assert first.result["status"] == "unknown" and first.cache_hit is False
    assert second.result["status"] == "in_stock" and second.cache_hit is False
    assert len(fake.calls) == 2


def test_each_product_has_its_own_cache_entry(live):
    fake = live(StockLookup(status="in_stock", as_of="t"))
    svc.run_check(PID)
    svc.run_check(OTHER)
    assert fake.calls == [PID, OTHER]
    assert svc.run_check(PID).cache_hit and svc.run_check(OTHER).cache_hit


def test_a_cache_time_of_zero_turns_caching_off(live, monkeypatch):
    monkeypatch.setenv("INVENTORY_CACHE_SECONDS", "0")
    fake = live(StockLookup(status="in_stock", as_of="t"))
    svc.run_check(PID)
    svc.run_check(PID)
    assert len(fake.calls) == 2


def test_mock_answers_are_never_cached(monkeypatch):
    monkeypatch.setenv("INVENTORY_CHECK_MODE", "mock")
    monkeypatch.setenv("INVENTORY_MOCK_DELAY_SECONDS", "0")
    first = svc.run_check(PID, mock_status="in_stock")
    second = svc.run_check(PID, mock_status="out_of_stock")
    assert (first.result["status"], second.result["status"]) == ("in_stock", "out_of_stock")
    assert not first.cache_hit and not second.cache_hit and len(limits.result_cache) == 0


def test_a_cache_hit_is_logged(live, caplog):
    live(StockLookup(status="in_stock", as_of="t"))
    svc.run_check(PID)
    with caplog.at_level(logging.INFO, logger=svc.logger.name):
        svc.run_check(PID)
    assert any("served from cache" in r.getMessage() and PID in r.getMessage() for r in caplog.records)


def test_check_inventory_still_returns_only_the_response(live):
    live(StockLookup(status="in_stock", as_of="t"))
    result = svc.check_inventory(PID)
    assert isinstance(result, dict) and result["status"] == "in_stock"


# ------------------------------------------------------------------ the route: 429 and the order of checks

@pytest.fixture(scope="module")
def client():
    import core

    def _skip_workflow(*args, **kwargs):
        raise RuntimeError("workflow disabled in unit tests")

    core.ShoppingAssistantWorkflow = _skip_workflow
    from app import app
    app.config["TESTING"] = True
    return app.test_client()


def headers_for(user_id):
    from core.auth import generate_access_token
    return {"Authorization": f"Bearer {generate_access_token(user_id, f'{user_id}@example.com', user_id)}"}


@pytest.fixture
def mock_mode(monkeypatch):
    monkeypatch.setenv("INVENTORY_CHECK_MODE", "mock")
    monkeypatch.setenv("INVENTORY_MOCK_DELAY_SECONDS", "0")


def post(client, headers, product_id=PID, **extra):
    return client.post("/api/inventory/check", json={"product_id": product_id, **extra}, headers=headers)


def test_the_21st_check_in_a_minute_gets_a_429(client, mock_mode, monkeypatch):
    monkeypatch.delenv("INVENTORY_RATE_LIMIT_PER_MINUTE", raising=False)
    h = headers_for("u1")
    assert all(post(client, h).status_code == 200 for _ in range(20))
    resp = post(client, h)
    assert resp.status_code == 429
    body = resp.get_json()
    assert body["error"] == "Too many requests" and "try again" in body["message"].lower()
    assert 1 <= int(resp.headers["Retry-After"]) <= 60


def test_one_users_limit_does_not_affect_another(client, mock_mode, monkeypatch):
    monkeypatch.setenv("INVENTORY_RATE_LIMIT_PER_MINUTE", "2")
    h1, h2 = headers_for("u1"), headers_for("u2")
    assert [post(client, h1).status_code for _ in range(3)] == [200, 200, 429]
    assert post(client, h2).status_code == 200


def test_the_limit_is_per_user_not_per_product(client, mock_mode, monkeypatch):
    monkeypatch.setenv("INVENTORY_RATE_LIMIT_PER_MINUTE", "2")
    h = headers_for("u1")
    assert [post(client, h, pid).status_code for pid in (PID, OTHER, "111111111")] == [200, 200, 429]


def test_bad_ids_and_missing_logins_do_not_use_up_the_limit(client, mock_mode, monkeypatch):
    monkeypatch.setenv("INVENTORY_RATE_LIMIT_PER_MINUTE", "2")
    h = headers_for("u1")
    assert all(post(client, h, "bad id").status_code == 400 for _ in range(5))
    assert all(client.post("/api/inventory/check", json={"product_id": PID}).status_code == 401 for _ in range(5))
    assert [post(client, h).status_code for _ in range(3)] == [200, 200, 429]


def test_a_limit_of_zero_means_no_limit_on_the_route(client, mock_mode, monkeypatch):
    monkeypatch.setenv("INVENTORY_RATE_LIMIT_PER_MINUTE", "0")
    h = headers_for("u1")
    assert all(post(client, h).status_code == 200 for _ in range(30))


def test_the_route_serves_a_repeat_check_from_the_cache(client, live):
    fake = live(StockLookup(status="in_stock", as_of="2026-06-08T06:01:26.513Z"))
    h = headers_for("u1")
    first, second = post(client, h), post(client, h)
    assert first.status_code == second.status_code == 200
    assert first.get_json()["status"] == second.get_json()["status"] == "in_stock"
    assert first.get_json()["request_id"] != second.get_json()["request_id"]
    assert fake.calls == [PID]


def test_cached_answers_still_count_towards_the_limit(client, live, monkeypatch):
    monkeypatch.setenv("INVENTORY_RATE_LIMIT_PER_MINUTE", "2")
    live(StockLookup(status="in_stock", as_of="t"))
    h = headers_for("u1")
    assert [post(client, h).status_code for _ in range(3)] == [200, 200, 429]


# ------------------------------------------------------------------ together with the audit trail (SAGOI-16)

class _FakeAudit:
    def __init__(self):
        self.calls = []

    def _compute_refs(self, user_id):
        return "subject-id", "ref-" + user_id

    def log_tool_call(self, **kwargs):
        self.calls.append(kwargs)


class _FakeWorkflow:
    def __init__(self):
        self.audit_wrapper = _FakeAudit()


@pytest.fixture
def audited(client, monkeypatch):
    import app as app_module
    fake = _FakeWorkflow()
    monkeypatch.setattr(app_module, "workflow", fake)
    return fake.audit_wrapper


def test_the_audit_row_records_whether_the_cache_answered(client, live, audited):
    live(StockLookup(status="in_stock", as_of="2026-06-08T06:01:26.513Z"))
    h = headers_for("u1")
    post(client, h)
    post(client, h)
    first, second = audited.calls
    assert first["tool_outputs"]["cache_hit"] is False and first["status"] == "success"
    assert second["tool_outputs"]["cache_hit"] is True and second["status"] == "success"
    assert second["tool_outputs"]["status"] == "in_stock" and second["tool_outputs"]["source"] == "genie_one_mcp"
    assert first["tool_outputs"]["request_id"] != second["tool_outputs"]["request_id"]


def test_a_rate_limited_request_writes_no_audit_row(client, mock_mode, monkeypatch, audited):
    monkeypatch.setenv("INVENTORY_RATE_LIMIT_PER_MINUTE", "2")
    h = headers_for("u1")
    assert [post(client, h).status_code for _ in range(3)] == [200, 200, 429]
    assert len(audited.calls) == 2                                # the refused third request is not a check


def test_a_failed_live_check_is_still_audited_as_an_error(client, live, audited):
    live(GenieOneTimeout("check did not finish within 60 seconds"))
    resp = post(client, headers_for("u1"))
    assert resp.get_json()["reason"] == "timeout"
    (row,) = audited.calls
    assert row["status"] == "error" and row["error_message"] == "timeout"
    assert row["tool_outputs"]["cache_hit"] is False
