"""
Shopping Assistant — app-level audit helpers.

Keeps shopping-specific event logging (wishlist, cart, orders) separate from
the generic AuditWrapper platform component. Calls AuditWrapper's internal
_fire() / _write_row() via the same fire-and-forget pattern.

Usage:
    from core.shopping_audit import log_wishlist_action, log_cart_action
    log_wishlist_action(audit_wrapper, trace_id="...", action="add", ...)
"""

import uuid
import logging
from typing import Optional

from audit_wrapper import _fire

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def log_wishlist_action(
    audit_wrapper,
    trace_id:    str,
    action:      str,
    product_id:  str,
    status:      str,
    subject_ref: Optional[str] = None,
) -> None:
    """
    Log a wishlist add / remove / view event to raw_logs.wishlist_events_raw.
    Fire-and-forget — never blocks the caller.

    action values: "add", "remove", "view"
    status values: "success", "already_exists", "not_found", "error"
    """
    try:
        now = _now_iso()
        row = {
            "event_id":        str(uuid.uuid4()),
            "trace_id":        trace_id    or None,
            "app_id":          audit_wrapper.app_id,
            "subject_ref":     subject_ref or None,
            "action":          action,
            "product_id":      product_id  or None,
            "status":          status,
            "is_erasure_flag": "false",
            "created_at":      now,
            "schema_version":  audit_wrapper.schema_version,
        }
        _fire(audit_wrapper._tbl("raw_logs.wishlist_events_raw"), row)
    except Exception as e:
        logger.warning("[SHOPPING AUDIT] log_wishlist_action failed: %s", e)


def log_cart_action(
    audit_wrapper,
    trace_id:    str,
    action:      str,
    product_id:  str,
    status:      str,
    quantity:    int = 1,
    subject_ref: Optional[str] = None,
) -> None:
    """
    Log a cart add / remove / update / view / checkout event to raw_logs.cart_events_raw.
    Fire-and-forget — never blocks the caller.

    action values: "add_to_cart", "remove_from_cart", "update_cart", "view", "order_placed"
    status values: "success", "not_found", "error"
    """
    try:
        now = _now_iso()
        row = {
            "event_id":        str(uuid.uuid4()),
            "trace_id":        trace_id    or None,
            "app_id":          audit_wrapper.app_id,
            "subject_ref":     subject_ref or None,
            "action":          action,
            "product_id":      product_id  or None,
            "quantity":        str(quantity),
            "status":          status,
            "is_erasure_flag": "false",
            "created_at":      now,
            "schema_version":  audit_wrapper.schema_version,
        }
        _fire(audit_wrapper._tbl("raw_logs.cart_events_raw"), row)
    except Exception as e:
        logger.warning("[SHOPPING AUDIT] log_cart_action failed: %s", e)


def log_inventory_check(
    audit_wrapper,
    trace_id:    str,
    product_id:  str,
    result:      Optional[dict],
    latency_ms:  Optional[float] = None,
    mode:        Optional[str] = None,
    error:       Optional[str] = None,
    subject_ref: Optional[str] = None,
    cache_hit:   Optional[bool] = None,
) -> None:
    """
    Log one "Check if the product is in stock" call to raw_logs.tool_calls_raw (SAGOI-16).
    Fire-and-forget and never raises, so a failed audit write cannot break the check.

    Stores the product id, the outcome and its source (mock or live). It never stores the
    user's email or name; the person is identified only by subject_ref.
    """
    try:
        if audit_wrapper is None:
            return
        result = result or {}
        status = result.get("status")
        ok = error is None and status in ("in_stock", "out_of_stock", "not_found")
        audit_wrapper.log_tool_call(
            trace_id=trace_id,
            tool_name="inventory_check",
            tool_inputs={"product_id": product_id, "mode": mode},
            tool_outputs={
                "status":     status,
                "source":     result.get("source"),
                "reason":     result.get("reason"),
                "request_id": result.get("request_id"),
                "cache_hit":  cache_hit,
            },
            status="success" if ok else "error",
            latency_ms=latency_ms,
            error_message=(error or result.get("reason")) if not ok else None,
            subject_ref=subject_ref,
        )
    except Exception as e:
        logger.warning("[SHOPPING AUDIT] log_inventory_check failed: %s", e)


def log_order_action(
    audit_wrapper,
    trace_id:   str,
    action:     str,
    order_id:   str,
    status:     str,
    item_count: int = 0,
    order_total: float = 0.0,
    subject_ref: Optional[str] = None,
) -> None:
    """Log a checkout / order event to raw_logs.order_events_raw."""
    try:
        now = _now_iso()
        row = {
            "event_id":        str(uuid.uuid4()),
            "app_id":          audit_wrapper.app_id,
            "trace_id":        trace_id,
            "subject_ref":     subject_ref or None,
            "action":          action,
            "order_id":        order_id,
            "item_count":      str(item_count),
            "order_total":     str(round(order_total, 2)),
            "status":          status,
            "is_erasure_flag": "false",
            "created_at":      now,
            "schema_version":  audit_wrapper.schema_version,
        }
        _fire(audit_wrapper._tbl("raw_logs.order_events_raw"), row)
    except Exception as e:
        logger.warning("[SHOPPING AUDIT] log_order_action failed: %s", e)
