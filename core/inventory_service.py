"""Inventory check behind the "Check if the product is in stock" button (SAGOI-11).

Mock mode returns a chosen status after a short delay so the UI can be built
before the live Genie One lookup exists. Live mode is added in SAGOI-12 and 13.
"""
import os
import re
import time
import uuid
from datetime import datetime, timezone

STATUSES = ("in_stock", "out_of_stock", "not_found", "unknown")

# v2 product ids are plain numbers (up to 19 characters); allow a safe superset.
_PRODUCT_ID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


class InvalidProductId(ValueError):
    """The product id is missing or has unexpected characters."""


def get_mode() -> str:
    mode = os.getenv("INVENTORY_CHECK_MODE", "mock").strip().lower()
    return mode if mode in ("mock", "live") else "mock"


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


def check_inventory(product_id, mock_status=None) -> dict:
    """Return the stock status for one product. Raises InvalidProductId for a bad id."""
    product_id = validate_product_id(product_id)
    if get_mode() == "mock":
        return _mock_check(product_id, mock_status)
    # Live mode arrives with SAGOI-12 and 13. Until then it must never claim a stock status,
    # and mock_status is deliberately ignored so no caller can fake a result.
    return unknown_result(product_id, reason="upstream_error")
