"""Inventory check behind the "Check if the product is in stock" button (SAGOI-11, SAGOI-13).

Mock mode returns a chosen status after a short delay so the UI can be built
without the live lookup. Live mode asks the Genie One MCP (core/genie_one_client.py)
and maps every failure to `unknown` with a reason: timeout, denied or upstream_error.
It never answers in_stock or out_of_stock unless the lookup returned that status.
"""
import logging
import os
import re
import time
import uuid
from datetime import datetime, timezone

from core.genie_one_client import GenieOneAccessDenied, GenieOneTimeout, get_client

logger = logging.getLogger(__name__)

STATUSES = ("in_stock", "out_of_stock", "not_found", "unknown")
LIVE_SOURCE = "genie_one_mcp"

# v2 product ids are plain numbers (up to 19 characters); allow a safe superset.
_PRODUCT_ID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


class InvalidProductId(ValueError):
    """The product id is missing or has unexpected characters."""


def get_mode() -> str:
    """`mock` when the setting is absent (development). Any value other than mock or live is treated
    as live, so a typo can never make a deployed app serve fake answers."""
    mode = os.getenv("INVENTORY_CHECK_MODE", "mock").strip().lower()
    if mode in ("mock", "live"):
        return mode
    logger.warning("INVENTORY_CHECK_MODE=%r is not 'mock' or 'live'; using live", mode)
    return "live"


def validate_product_id(product_id) -> str:
    if not isinstance(product_id, str) or not _PRODUCT_ID.match(product_id):
        raise InvalidProductId("product_id must be 1 to 64 letters, digits, dots, dashes or underscores")
    return product_id


def _result(product_id, status, source, as_of=None, reason=None) -> dict:
    result = {
        "product_id": product_id,
        "status": status,
        "as_of": as_of,
        "source": source,
        "request_id": uuid.uuid4().hex,
    }
    if reason:
        result["reason"] = reason
    return result


def unknown_result(product_id, reason="upstream_error", source="inventory_check") -> dict:
    """The safe answer when a check cannot be completed. Never claims a stock status."""
    return _result(product_id, "unknown", source, reason=reason)


def _mock_check(product_id, mock_status) -> dict:
    time.sleep(float(os.getenv("INVENTORY_MOCK_DELAY_SECONDS", "2")))
    status = mock_status if mock_status in STATUSES else "in_stock"
    if status == "unknown":
        return unknown_result(product_id, reason="upstream_error", source="mock")
    as_of = datetime.now(timezone.utc).isoformat() if status in ("in_stock", "out_of_stock") else None
    return _result(product_id, status, "mock", as_of=as_of)


def _failed(product_id, reason, error) -> dict:
    """A check that could not be completed. Logs the error type and message (the client keeps secrets out
    of its messages) and answers `unknown`, never a stock status."""
    logger.warning("inventory check failed: reason=%s error=%s: %s", reason, type(error).__name__, str(error)[:200])
    return unknown_result(product_id, reason=reason, source=LIVE_SOURCE)


def _live_check(product_id) -> dict:
    try:
        lookup = get_client().lookup(product_id)       # the 60-second limit covers the whole check, retry included
    except GenieOneAccessDenied as e:
        return _failed(product_id, "denied", e)
    except GenieOneTimeout as e:
        return _failed(product_id, "timeout", e)
    except Exception as e:  # noqa: BLE001 - any other client error, or anything unexpected
        return _failed(product_id, "upstream_error", e)
    if lookup.status not in STATUSES or lookup.status == "unknown":
        return unknown_result(product_id, reason=lookup.reason or "upstream_error", source=LIVE_SOURCE)
    return _result(product_id, lookup.status, LIVE_SOURCE, as_of=lookup.as_of)


def check_inventory(product_id, mock_status=None) -> dict:
    """Return the stock status for one product. Raises InvalidProductId for a bad id.

    mock_status is honoured in mock mode only, so nobody can fake a result against the live lookup."""
    product_id = validate_product_id(product_id)
    if get_mode() == "mock":
        return _mock_check(product_id, mock_status)
    return _live_check(product_id)
