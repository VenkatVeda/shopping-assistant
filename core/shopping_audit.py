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
        audit_wrapper._fire(audit_wrapper._tbl("raw_logs.wishlist_events_raw"), row)
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
        audit_wrapper._fire(audit_wrapper._tbl("raw_logs.cart_events_raw"), row)
    except Exception as e:
        logger.warning("[SHOPPING AUDIT] log_cart_action failed: %s", e)
