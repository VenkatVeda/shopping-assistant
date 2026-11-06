"""Genie One MCP client for the inventory check (SAGOI-12).

Asks the Genie One MCP, as a dedicated service principal (SP), whether one product is
available in the v2 product table, and turns the answer into a stock status.

Flow for one check (all inside one MCP session):
  1. genie_ask               - send the fixed question for one product id
  2. genie_poll_response     - wait until Genie has finished
  3. genie_get_query_result  - read the returned rows (never the wording of Genie's reply)

Settings (environment variables):
  DATABRICKS_HOST                      workspace URL
  INVENTORY_SP_CLIENT_ID / _SECRET     the SP's OAuth client credentials (supplied by SAGOI-9)
  INVENTORY_SP_SCOPES                  OAuth scopes (default: ai-gateway genie sql model-serving-inference)
  INVENTORY_SOURCE_TABLE               product table (default: sandbox.venkat.bags_embeddings_v2)
  INVENTORY_CHECK_TIMEOUT_SECONDS      limit for the whole check, retry included (default 60)
  INVENTORY_POLL_INTERVAL_SECONDS      pause between polls (default 2)

This module only talks to Genie One. Mapping its errors to the endpoint's `unknown` reasons,
and switching live mode on, is SAGOI-13.
"""
import asyncio
import json
import os
import re
import threading
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Optional

import requests

MCP_SERVICE = "system.ai.genie_one_mcp"
DEFAULT_SOURCE_TABLE = "sandbox.venkat.bags_embeddings_v2"
DEFAULT_SCOPES = "ai-gateway genie sql model-serving-inference"
DEFAULT_TIMEOUT_SECONDS = 60.0

# The gateway sometimes fails a single call with this text; the next call succeeds.
TRANSIENT = "Unable to verify MCP service access"
_DENIED_MARKERS = ("does not exist, or you do not have access", "PERMISSION_DENIED",
                   "INSUFFICIENT_PERMISSIONS", "403 Forbidden", "HTTP 403")

# Same rule as core.inventory_service (kept here too so this module is safe on its own).
_PRODUCT_ID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
_TABLE_NAME = re.compile(r"^[A-Za-z0-9_]+\.[A-Za-z0-9_]+\.[A-Za-z0-9_]+$")

# The question is fixed. Only the validated product id is filled in; no user text reaches it.
QUESTION_TEMPLATE = (
    "From the table {table}, return product_id, is_available and last_updated for the product "
    "whose product_id is '{product_id}'. Return only that one row. "
    "If no product has that product_id, return no rows."
)


class GenieOneError(Exception):
    """The Genie One call failed (transport, gateway or an unexpected error)."""


class GenieOneNotConfigured(GenieOneError):
    """A required setting is missing, so no call was attempted."""


class GenieOneAccessDenied(GenieOneError):
    """The SP is not allowed to use the MCP, the warehouse or the table."""


class GenieOneTimeout(GenieOneError):
    """The whole check did not finish inside the time limit."""


@dataclass(frozen=True)
class StockLookup:
    """The outcome of one lookup. `reason` is set only when status is `unknown`."""
    status: str
    as_of: Optional[str] = None
    reason: Optional[str] = None


def _unknown(reason="upstream_error") -> StockLookup:
    return StockLookup(status="unknown", reason=reason)


# --------------------------------------------------------------------------- helpers

def _leaf(exc: BaseException) -> BaseException:
    """The innermost exception of an (anyio / task group) exception group."""
    while getattr(exc, "exceptions", None):
        exc = exc.exceptions[0]
    return exc


def _payload(result) -> dict:
    """Structured payload of an MCP tool result, best effort."""
    structured = getattr(result, "structuredContent", None)
    if structured:
        return structured
    for block in getattr(result, "content", None) or []:
        text = getattr(block, "text", None)
        if text:
            try:
                parsed = json.loads(text)
                return parsed if isinstance(parsed, dict) else {}
            except ValueError:
                return {"text": text}
    return {}


def _text(result) -> str:
    return "\n".join(getattr(b, "text", "") or "" for b in (getattr(result, "content", None) or []))


def _to_bool(value) -> Optional[bool]:
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.strip().lower() in ("true", "false"):
        return value.strip().lower() == "true"
    return None


def parse_stock_rows(product_id: str, result_payload: dict) -> StockLookup:
    """Turn a genie_get_query_result payload into a stock status. Anything unexpected is `unknown`."""
    if result_payload.get("statement_state") not in (None, "SUCCEEDED") or result_payload.get("truncated"):
        return _unknown()
    columns = [str(c.get("name", "")).lower() for c in (result_payload.get("columns") or [])]
    rows = result_payload.get("rows")
    if not columns or rows is None:
        return _unknown()
    if len(rows) == 0:
        return StockLookup(status="not_found")
    if len(rows) > 1:                      # v2 has one row per product, so this is unexpected
        return _unknown()
    row = dict(zip(columns, rows[0]))
    if str(row.get("product_id")) != product_id:
        return _unknown()                  # Genie answered about a different product
    available = _to_bool(row.get("is_available"))
    if available is None:
        return _unknown()
    as_of = row.get("last_updated")
    return StockLookup(status="in_stock" if available else "out_of_stock",
                       as_of=str(as_of) if as_of else None)


# --------------------------------------------------------------------------- settings

def _host() -> str:
    host = (os.getenv("DATABRICKS_HOST") or "").strip().rstrip("/")
    if not host:
        raise GenieOneNotConfigured("DATABRICKS_HOST is not set")
    return host if host.startswith("http") else f"https://{host}"


def _source_table() -> str:
    table = (os.getenv("INVENTORY_SOURCE_TABLE") or DEFAULT_SOURCE_TABLE).strip()
    if not _TABLE_NAME.match(table):
        raise GenieOneNotConfigured("INVENTORY_SOURCE_TABLE must look like catalog.schema.table")
    return table


class _SpTokenProvider:
    """OAuth client-credentials token for the SP, cached until shortly before it expires."""

    def __init__(self):
        self._lock = threading.Lock()
        self._token = None
        self._expires_at = 0.0

    def get_token(self) -> str:
        with self._lock:
            if self._token and time.time() < self._expires_at - 60:
                return self._token
            client_id = os.getenv("INVENTORY_SP_CLIENT_ID")
            client_secret = os.getenv("INVENTORY_SP_CLIENT_SECRET")
            if not client_id or not client_secret:
                raise GenieOneNotConfigured("INVENTORY_SP_CLIENT_ID and INVENTORY_SP_CLIENT_SECRET are not set")
            scopes = os.getenv("INVENTORY_SP_SCOPES") or DEFAULT_SCOPES
            try:
                resp = requests.post(
                    f"{_host()}/oidc/v1/token",
                    auth=(client_id, client_secret),
                    data={"grant_type": "client_credentials", "scope": scopes},
                    timeout=20.0,
                )
            except requests.RequestException as e:
                raise GenieOneError(f"token request failed: {type(e).__name__}") from None
            if resp.status_code != 200:
                raise GenieOneError(f"token request failed: HTTP {resp.status_code}")
            body = resp.json()
            self._token = body["access_token"]
            self._expires_at = time.time() + float(body.get("expires_in", 3600))
            return self._token


# --------------------------------------------------------------------------- client

class GenieOneClient:
    def __init__(self, session_factory=None, poll_interval_s: Optional[float] = None):
        """`session_factory` returns an async context manager that yields an object with
        `async call_tool(name, args)`. The default opens the real Genie One MCP; tests pass a fake."""
        self._tokens = _SpTokenProvider()
        self._session_factory = session_factory or self._open_mcp_session
        self._poll_interval_s = (poll_interval_s if poll_interval_s is not None
                                 else float(os.getenv("INVENTORY_POLL_INTERVAL_SECONDS", "2")))

    @asynccontextmanager
    async def _open_mcp_session(self):
        token = await asyncio.to_thread(self._tokens.get_token)
        url = f"{_host()}/ai-gateway/mcp-services/{MCP_SERVICE}"
        from mcp import ClientSession
        from mcp.client.streamable_http import streamablehttp_client

        async with streamablehttp_client(url, headers={"Authorization": f"Bearer {token}"}) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                yield session

    async def _call(self, session, name: str, args: dict):
        """One tool call, retried once when the gateway reports its occasional transient error."""
        for attempt in (0, 1):
            try:
                result = await session.call_tool(name, args)
                if getattr(result, "isError", False):
                    raise GenieOneError(_text(result)[:300] or "tool call failed")
            except Exception as e:  # noqa: BLE001 - classified below
                if attempt == 0 and TRANSIENT in str(_leaf(e)):
                    await asyncio.sleep(min(self._poll_interval_s, 2.0))
                    continue
                raise
            return result

    async def _lookup(self, product_id: str) -> StockLookup:
        question = QUESTION_TEMPLATE.format(table=_source_table(), product_id=product_id)
        async with self._session_factory() as session:
            started = _payload(await self._call(session, "genie_ask", {"question": question}))
            conversation_id, response_id = started.get("conversation_id"), started.get("response_id")
            if not (conversation_id and response_id):
                return _unknown()

            poll = {}
            while True:
                await asyncio.sleep(self._poll_interval_s)
                poll = _payload(await self._call(
                    session, "genie_poll_response",
                    {"conversation_id": conversation_id, "response_id": response_id}))
                if poll.get("status") != "in_progress":
                    break
            if poll.get("status") != "completed":
                return _unknown()

            items = [i for i in (poll.get("query_items") or []) if i.get("item_id")]
            if not items:                  # Genie answered without running a query: do not trust its prose
                return _unknown()
            result = _payload(await self._call(
                session, "genie_get_query_result",
                {"conversation_id": conversation_id, "response_id": response_id,
                 "item_id": items[0]["item_id"]}))
            return parse_stock_rows(product_id, result)

    def lookup(self, product_id: str, timeout_s: Optional[float] = None) -> StockLookup:
        """Look one product up. Returns a StockLookup (possibly `unknown`), or raises a GenieOneError."""
        if not isinstance(product_id, str) or not _PRODUCT_ID.match(product_id):
            raise ValueError("invalid product_id")
        limit = float(timeout_s if timeout_s is not None
                      else os.getenv("INVENTORY_CHECK_TIMEOUT_SECONDS", DEFAULT_TIMEOUT_SECONDS))
        try:
            return asyncio.run(asyncio.wait_for(self._lookup(product_id), limit))
        except asyncio.TimeoutError:
            raise GenieOneTimeout(f"check did not finish within {limit:g} seconds") from None
        except GenieOneError:
            raise
        except Exception as e:  # noqa: BLE001
            leaf = _leaf(e)
            message = str(leaf)
            if any(marker in message for marker in _DENIED_MARKERS):
                raise GenieOneAccessDenied(message[:300]) from None
            raise GenieOneError(f"{type(leaf).__name__}: {message[:300]}") from None


_client: Optional[GenieOneClient] = None
_client_lock = threading.Lock()


def get_client() -> GenieOneClient:
    """One shared client, so the SP token is cached across checks."""
    global _client
    with _client_lock:
        if _client is None:
            _client = GenieOneClient()
        return _client
