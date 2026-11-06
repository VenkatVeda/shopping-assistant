"""Live mode of the inventory check (SAGOI-13): the endpoint service calls the Genie One client and
turns every outcome, including every failure, into one of the four statuses. No network, no credentials."""
import logging

import pytest

from core import inventory_service as svc
from core.genie_one_client import (
    GenieOneAccessDenied, GenieOneError, GenieOneNotConfigured, GenieOneTimeout, StockLookup,
)
from tests.test_genie_one_client import FakeSession, client_for, query_result

PID = "998643070"
RESPONSE_KEYS = {"product_id", "status", "as_of", "source", "request_id"}


class FakeClient:
    """Stands in for the Genie One client: returns a StockLookup or raises what it was given."""

    def __init__(self, outcome):
        self.outcome = outcome
        self.calls = []

    def lookup(self, product_id, timeout_s=None):
        self.calls.append(product_id)
        if isinstance(self.outcome, BaseException):
            raise self.outcome
        return self.outcome


@pytest.fixture
def live(monkeypatch):
    monkeypatch.setenv("INVENTORY_CHECK_MODE", "live")

    def use(outcome):
        fake = FakeClient(outcome)
        monkeypatch.setattr(svc, "get_client", lambda: fake)
        return fake
    return use


# ------------------------------------------------------------------ successful lookups

@pytest.mark.parametrize("status", ["in_stock", "out_of_stock", "not_found"])
def test_live_returns_the_clients_status(live, status):
    as_of = "2026-06-08T06:01:26.513Z" if status != "not_found" else None
    fake = live(StockLookup(status=status, as_of=as_of))
    result = svc.check_inventory(PID)
    assert fake.calls == [PID]
    assert result["status"] == status and result["as_of"] == as_of
    assert result["source"] == "genie_one_mcp" and result["product_id"] == PID
    assert RESPONSE_KEYS <= set(result) and "reason" not in result


def test_live_and_mock_responses_have_the_same_shape(live, monkeypatch):
    live(StockLookup(status="in_stock", as_of="t"))
    live_keys = set(svc.check_inventory(PID))
    monkeypatch.setenv("INVENTORY_CHECK_MODE", "mock")
    monkeypatch.setenv("INVENTORY_MOCK_DELAY_SECONDS", "0")
    assert live_keys == set(svc.check_inventory(PID, mock_status="in_stock"))


def test_mock_status_is_ignored_in_live_mode(live):
    live(StockLookup(status="out_of_stock", as_of="t"))
    assert svc.check_inventory(PID, mock_status="in_stock")["status"] == "out_of_stock"


# ------------------------------------------------------------------ failures always end as unknown

@pytest.mark.parametrize("error, reason", [
    (GenieOneAccessDenied("MCP service does not exist, or you do not have access to it."), "denied"),
    (GenieOneTimeout("check did not finish within 60 seconds"), "timeout"),
    (GenieOneError("something went wrong"), "upstream_error"),
    (GenieOneNotConfigured("INVENTORY_SP_CLIENT_ID is not set"), "upstream_error"),
    (RuntimeError("unexpected"), "upstream_error"),
    (ValueError("unexpected"), "upstream_error"),
])
def test_every_failure_is_unknown_with_a_reason(live, error, reason):
    live(error)
    result = svc.check_inventory(PID)
    assert result["status"] == "unknown" and result["reason"] == reason
    assert result["as_of"] is None and result["source"] == "genie_one_mcp"
    assert RESPONSE_KEYS <= set(result)


@pytest.mark.parametrize("lookup", [
    StockLookup(status="unknown", reason="upstream_error"),
    StockLookup(status="unknown"),
    StockLookup(status="something_else"),
])
def test_an_unusable_lookup_is_unknown(live, lookup):
    live(lookup)
    result = svc.check_inventory(PID)
    assert result["status"] == "unknown" and result["reason"] == "upstream_error"


def test_failures_are_logged_without_secrets(live, caplog):
    live(GenieOneError("token request failed: HTTP 401"))
    with caplog.at_level(logging.WARNING, logger=svc.logger.name):
        svc.check_inventory(PID)
    text = " ".join(r.getMessage() for r in caplog.records)
    assert "upstream_error" in text and "GenieOneError" in text and "HTTP 401" in text


# ------------------------------------------------------------------ the mode setting

def test_mock_is_the_default_when_the_setting_is_absent(monkeypatch):
    monkeypatch.delenv("INVENTORY_CHECK_MODE", raising=False)
    assert svc.get_mode() == "mock"


@pytest.mark.parametrize("value, expected", [("live", "live"), (" LIVE ", "live"), ("mock", "mock"),
                                             ("lve", "live"), ("", "live"), ("production", "live")])
def test_an_unknown_mode_is_live_never_mock(monkeypatch, value, expected):
    monkeypatch.setenv("INVENTORY_CHECK_MODE", value)
    assert svc.get_mode() == expected


def test_a_typo_in_the_mode_cannot_serve_a_fake_answer(live, monkeypatch):
    fake = live(StockLookup(status="out_of_stock", as_of="t"))
    monkeypatch.setenv("INVENTORY_CHECK_MODE", "lvie")
    result = svc.check_inventory(PID, mock_status="in_stock")
    assert result["status"] == "out_of_stock" and fake.calls == [PID]


# ------------------------------------------------------------------ the real client end to end (fake MCP session)

def test_end_to_end_with_the_real_client_and_a_fake_mcp(live, monkeypatch):
    monkeypatch.setattr(svc, "get_client",
                        lambda: client_for(FakeSession(rows=[query_result([[PID, "false", "2026-06-08T06:01:26.513Z"]])])))
    result = svc.check_inventory(PID)
    assert result["status"] == "out_of_stock" and result["as_of"] == "2026-06-08T06:01:26.513Z"


def test_end_to_end_a_slow_check_times_out_as_unknown(live, monkeypatch):
    monkeypatch.setenv("INVENTORY_CHECK_TIMEOUT_SECONDS", "0.2")
    monkeypatch.setattr(svc, "get_client", lambda: client_for(FakeSession(delay=5)))
    result = svc.check_inventory(PID)
    assert result["status"] == "unknown" and result["reason"] == "timeout"


def test_end_to_end_a_missing_grant_is_denied(live, monkeypatch):
    denied = RuntimeError("MCP service 'system.ai.genie_one_mcp' does not exist, or you do not have access to it.")
    monkeypatch.setattr(svc, "get_client", lambda: client_for(FakeSession(ask=[denied])))
    result = svc.check_inventory(PID)
    assert result["status"] == "unknown" and result["reason"] == "denied"


def test_end_to_end_one_transient_failure_is_retried_inside_the_limit(live, monkeypatch):
    transient = RuntimeError("Unable to verify MCP service access")
    session = FakeSession(ask=[transient, FakeSession().ask[0]])
    monkeypatch.setattr(svc, "get_client", lambda: client_for(session))
    assert svc.check_inventory(PID)["status"] == "in_stock"
    assert [c[0] for c in session.calls].count("genie_ask") == 2


# ------------------------------------------------------------------ through the route

@pytest.fixture(scope="module")
def client():
    import core

    def _skip_workflow(*args, **kwargs):
        raise RuntimeError("workflow disabled in unit tests")

    core.ShoppingAssistantWorkflow = _skip_workflow
    from app import app
    app.config["TESTING"] = True
    return app.test_client()


@pytest.fixture
def auth_headers():
    from core.auth import generate_access_token
    return {"Authorization": f"Bearer {generate_access_token('u1', 'tester@example.com', 'Tester')}"}


def test_route_in_live_mode_returns_the_lookup(live, client, auth_headers):
    live(StockLookup(status="in_stock", as_of="2026-06-08T06:01:26.513Z"))
    resp = client.post("/api/inventory/check", json={"product_id": PID}, headers=auth_headers)
    assert resp.status_code == 200
    assert resp.get_json()["status"] == "in_stock" and resp.get_json()["source"] == "genie_one_mcp"


def test_route_in_live_mode_answers_200_unknown_on_failure(live, client, auth_headers):
    live(GenieOneTimeout("check did not finish within 60 seconds"))
    resp = client.post("/api/inventory/check", json={"product_id": PID}, headers=auth_headers)
    assert resp.status_code == 200
    assert resp.get_json()["status"] == "unknown" and resp.get_json()["reason"] == "timeout"


def test_route_in_live_mode_still_requires_login_and_a_valid_id(live, client, auth_headers):
    fake = live(StockLookup(status="in_stock", as_of="t"))
    assert client.post("/api/inventory/check", json={"product_id": PID}).status_code == 401
    assert client.post("/api/inventory/check", json={"product_id": "bad id"}, headers=auth_headers).status_code == 400
    assert fake.calls == []
