"""Tests for the Genie One client (SAGOI-12). They use a fake MCP session: no network, no credentials."""
import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from core import genie_one_client as g
from core.genie_one_client import (
    GenieOneAccessDenied, GenieOneClient, GenieOneError, GenieOneNotConfigured, GenieOneTimeout,
    QUESTION_TEMPLATE, StockLookup, parse_stock_rows,
)

PID = "998643070"
COLUMNS = [{"name": "product_id"}, {"name": "is_available"}, {"name": "last_updated"}]


def result(payload=None, is_error=False, text=""):
    content = [SimpleNamespace(text=text)] if text else []
    return SimpleNamespace(structuredContent=payload, content=content, isError=is_error)


def query_result(rows, **extra):
    body = {"statement_state": "SUCCEEDED", "ready": True, "columns": COLUMNS, "rows": rows,
            "total_row_count": len(rows), "truncated": False}
    body.update(extra)
    return result(body)


class FakeSession:
    """Plays back scripted answers per tool name and records every call."""

    def __init__(self, ask=None, poll=None, rows=None, delay=0.0):
        self.calls = []
        self.delay = delay
        self.ask = ask if ask is not None else [result({"conversation_id": "c1", "response_id": "r1"})]
        self.poll = poll if poll is not None else [
            result({"status": "completed", "query_items": [{"item_id": "pub_1_output", "sql": "SELECT 1"}]})]
        self.rows = rows if rows is not None else [query_result([[PID, "true", "2026-06-08T06:01:26.513Z"]])]

    async def call_tool(self, name, args):
        self.calls.append((name, args))
        if self.delay:
            await asyncio.sleep(self.delay)
        script = {"genie_ask": self.ask, "genie_poll_response": self.poll, "genie_get_query_result": self.rows}[name]
        item = script.pop(0) if len(script) > 1 else script[0]
        if isinstance(item, Exception):
            raise item
        return item


def client_for(session):
    @asynccontextmanager
    async def factory():
        yield session
    return GenieOneClient(session_factory=factory, poll_interval_s=0)


# ------------------------------------------------------------------ status mapping

def test_available_row_is_in_stock():
    s = FakeSession()
    out = client_for(s).lookup(PID)
    assert out == StockLookup(status="in_stock", as_of="2026-06-08T06:01:26.513Z")
    assert [c[0] for c in s.calls] == ["genie_ask", "genie_poll_response", "genie_get_query_result"]


def test_unavailable_row_is_out_of_stock():
    s = FakeSession(rows=[query_result([[PID, "false", "2026-06-08T06:01:26.513Z"]])])
    assert client_for(s).lookup(PID).status == "out_of_stock"


def test_boolean_values_are_accepted_too():
    s = FakeSession(rows=[query_result([[PID, True, "2026-06-08T06:01:26.513Z"]])])
    assert client_for(s).lookup(PID).status == "in_stock"


def test_no_row_is_not_found():
    out = client_for(FakeSession(rows=[query_result([])])).lookup(PID)
    assert out == StockLookup(status="not_found")


# ------------------------------------------------------------------ unreadable answers are never a stock status

@pytest.mark.parametrize("rows", [
    [query_result([[PID, "true", "t"], [PID, "false", "t"]])],      # more than one row
    [query_result([["111111111", "true", "t"]])],                   # a different product
    [query_result([[PID, "maybe", "t"]])],                          # not a boolean
    [query_result([[PID, "true", "t"]], truncated=True)],           # cut off
    [query_result([[PID, "true", "t"]], statement_state="FAILED")], # the SQL failed
    [result({})],                                                   # empty payload
])
def test_unexpected_results_are_unknown(rows):
    out = client_for(FakeSession(rows=rows)).lookup(PID)
    assert out.status == "unknown" and out.reason == "upstream_error"


def test_missing_conversation_ids_are_unknown():
    out = client_for(FakeSession(ask=[result({})])).lookup(PID)
    assert out.status == "unknown"


def test_failed_genie_response_is_unknown():
    out = client_for(FakeSession(poll=[result({"status": "failed"})])).lookup(PID)
    assert out.status == "unknown"


def test_answer_without_a_query_is_not_trusted():
    s = FakeSession(poll=[result({"status": "completed", "final_answer": "Yes, it is available", "query_items": []})])
    out = client_for(s).lookup(PID)
    assert out.status == "unknown"
    assert [c[0] for c in s.calls] == ["genie_ask", "genie_poll_response"]


def test_polls_until_genie_is_done():
    s = FakeSession(poll=[result({"status": "in_progress"}), result({"status": "in_progress"}),
                          result({"status": "completed", "query_items": [{"item_id": "pub_1_output"}]})])
    assert client_for(s).lookup(PID).status == "in_stock"
    assert [c[0] for c in s.calls].count("genie_poll_response") == 3


def test_parse_stock_rows_directly():
    assert parse_stock_rows(PID, {"columns": COLUMNS, "rows": [[PID, "true", None]]}) == \
        StockLookup(status="in_stock", as_of=None)


# ------------------------------------------------------------------ the question

def test_the_question_is_fixed_and_names_the_table_and_the_id(monkeypatch):
    monkeypatch.delenv("INVENTORY_SOURCE_TABLE", raising=False)
    s = FakeSession()
    client_for(s).lookup(PID)
    name, args = s.calls[0]
    assert name == "genie_ask"
    assert args["question"] == QUESTION_TEMPLATE.format(table="sandbox.venkat.bags_embeddings_v2", product_id=PID)
    assert "sandbox.venkat.bags_embeddings_v2" in args["question"] and f"'{PID}'" in args["question"]


@pytest.mark.parametrize("bad", ["", "a b", "x" * 65, "1' OR '1'='1", None, 123])
def test_bad_product_ids_never_reach_genie(bad):
    s = FakeSession()
    with pytest.raises(ValueError):
        client_for(s).lookup(bad)
    assert s.calls == []


def test_a_malformed_table_setting_is_refused(monkeypatch):
    monkeypatch.setenv("INVENTORY_SOURCE_TABLE", "sandbox.venkat.bags; DROP TABLE x")
    s = FakeSession()
    with pytest.raises(GenieOneNotConfigured):
        client_for(s).lookup(PID)
    assert s.calls == []


# ------------------------------------------------------------------ failures and the one retry

def test_one_transient_failure_is_retried():
    s = FakeSession(ask=[RuntimeError("Unable to verify MCP service access"),
                         result({"conversation_id": "c1", "response_id": "r1"})])
    assert client_for(s).lookup(PID).status == "in_stock"
    assert [c[0] for c in s.calls].count("genie_ask") == 2


def test_a_second_transient_failure_is_an_error():
    s = FakeSession(ask=[RuntimeError("Unable to verify MCP service access"),
                         RuntimeError("Unable to verify MCP service access")])
    with pytest.raises(GenieOneError):
        client_for(s).lookup(PID)
    assert [c[0] for c in s.calls].count("genie_ask") == 2


def test_other_errors_are_not_retried():
    s = FakeSession(ask=[RuntimeError("something else broke")])
    with pytest.raises(GenieOneError):
        client_for(s).lookup(PID)
    assert len(s.calls) == 1


def test_a_tool_error_result_is_an_error():
    s = FakeSession(ask=[result(None, is_error=True, text="Genie is unavailable")])
    with pytest.raises(GenieOneError):
        client_for(s).lookup(PID)


@pytest.mark.parametrize("message", [
    "MCP service 'system.ai.genie_one_mcp' does not exist, or you do not have access to it.",
    "[INSUFFICIENT_PERMISSIONS] Principal does not have SELECT on Table",
    "Client error '403 Forbidden' for url 'https://example'",
])
def test_access_problems_are_reported_as_access_denied(message):
    with pytest.raises(GenieOneAccessDenied):
        client_for(FakeSession(ask=[RuntimeError(message)])).lookup(PID)


def test_errors_inside_an_exception_group_are_unwrapped():
    inner = RuntimeError("MCP service 'x' does not exist, or you do not have access to it.")
    group = ExceptionGroup("unhandled errors in a TaskGroup", [inner])
    with pytest.raises(GenieOneAccessDenied):
        client_for(FakeSession(ask=[group])).lookup(PID)


def test_the_whole_check_has_a_time_limit():
    with pytest.raises(GenieOneTimeout):
        client_for(FakeSession(delay=5)).lookup(PID, timeout_s=0.2)


# ------------------------------------------------------------------ settings and the SP token

def test_missing_credentials_are_reported(monkeypatch):
    for name in ("INVENTORY_SP_CLIENT_ID", "INVENTORY_SP_CLIENT_SECRET"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("DATABRICKS_HOST", "adb-1.azuredatabricks.net")
    with pytest.raises(GenieOneNotConfigured):
        g._SpTokenProvider().get_token()


def test_token_request_uses_client_credentials_and_scopes_and_is_cached(monkeypatch):
    monkeypatch.setenv("DATABRICKS_HOST", "adb-1.azuredatabricks.net")
    monkeypatch.setenv("INVENTORY_SP_CLIENT_ID", "the-id")
    monkeypatch.setenv("INVENTORY_SP_CLIENT_SECRET", "the-secret")
    monkeypatch.delenv("INVENTORY_SP_SCOPES", raising=False)
    seen = []

    def fake_post(url, auth=None, data=None, timeout=None):
        seen.append((url, auth, data))
        return SimpleNamespace(status_code=200, json=lambda: {"access_token": "tok", "expires_in": 3600})

    monkeypatch.setattr(g.requests, "post", fake_post)
    provider = g._SpTokenProvider()
    assert provider.get_token() == "tok"
    assert provider.get_token() == "tok"
    assert len(seen) == 1                                    # second call came from the cache
    url, auth, data = seen[0]
    assert url == "https://adb-1.azuredatabricks.net/oidc/v1/token"
    assert auth == ("the-id", "the-secret")
    assert data == {"grant_type": "client_credentials", "scope": g.DEFAULT_SCOPES}


def test_a_failed_token_request_never_leaks_the_secret(monkeypatch):
    monkeypatch.setenv("DATABRICKS_HOST", "https://adb-1.azuredatabricks.net")
    monkeypatch.setenv("INVENTORY_SP_CLIENT_ID", "the-id")
    monkeypatch.setenv("INVENTORY_SP_CLIENT_SECRET", "super-secret-value")
    monkeypatch.setattr(g.requests, "post", lambda *a, **k: SimpleNamespace(status_code=401, json=lambda: {}))
    with pytest.raises(GenieOneError) as err:
        g._SpTokenProvider().get_token()
    assert "super-secret-value" not in str(err.value) and "401" in str(err.value)
