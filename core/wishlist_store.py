"""
WishlistStore — Delta-backed wishlist persistence.

Follows the same fire-and-forget pattern as AuditWrapper:
  - pure Python, no PySpark
  - databricks.sdk WorkspaceClient + statement_execution for all reads/writes
  - background threads for writes — zero latency impact on requests

Table: shopping_assistant.user_data.wishlist_items
"""

import os
import uuid
import logging
import threading
from typing import Optional

logger = logging.getLogger(__name__)

CATALOG = os.getenv("AUDIT_CATALOG", "shopping_assistant")
TABLE   = f"{CATALOG}.user_data.wishlist_items"


# ── DDL ───────────────────────────────────────────────────────────────────────
DDL = f"""
CREATE TABLE IF NOT EXISTS {TABLE} (
    wishlist_id   STRING    NOT NULL,
    subject_ref   STRING    NOT NULL,
    app_id        STRING    NOT NULL,
    product_id    STRING    NOT NULL,
    product_name  STRING,
    brand         STRING,
    price         DOUBLE,
    image_url     STRING,
    retailer_url  STRING,
    added_at      TIMESTAMP,
    is_active     BOOLEAN   DEFAULT true
)
USING DELTA
"""


def _client():
    from databricks.sdk import WorkspaceClient
    return WorkspaceClient()


def _warehouse():
    wh = os.getenv("DATABRICKS_SQL_WAREHOUSE_ID")
    if not wh:
        raise RuntimeError("DATABRICKS_SQL_WAREHOUSE_ID not set")
    return wh


def _exec(sql: str, params: list = None):
    """Execute SQL synchronously and return result."""
    from databricks.sdk.service.sql import StatementParameterListItem
    w = _client()
    kwargs = dict(warehouse_id=_warehouse(), statement=sql, wait_timeout="10s")
    if params:
        kwargs["parameters"] = params
    return w.statement_execution.execute_statement(**kwargs)


def _exec_bg(sql: str, params: list = None):
    """Fire-and-forget background write."""
    def _run():
        try:
            _exec(sql, params)
        except Exception as e:
            logger.warning("[WISHLIST] Background write failed: %s", e)
    threading.Thread(target=_run, daemon=True).start()


def ensure_table():
    """Create the wishlist_items table if it does not exist."""
    try:
        _exec(DDL)
        logger.info("[WISHLIST] Table ready: %s", TABLE)
    except Exception as e:
        logger.warning("[WISHLIST] Table creation failed (may already exist): %s", e)


class WishlistStore:
    """
    Manages wishlist reads and writes for a single user (identified by subject_ref).

    Instantiate once in ShoppingAssistantWorkflow.__init__() alongside AuditWrapper.
    All write operations are fire-and-forget; reads are synchronous (called from API endpoints).
    """

    def __init__(self, app_id: str = None):
        self.app_id = app_id or os.getenv("AUDIT_APP_ID", "myre_app")

    # ── writes ────────────────────────────────────────────────────────────────

    def add(
        self,
        subject_ref:  str,
        product_id:   str,
        product_name: Optional[str] = None,
        brand:        Optional[str] = None,
        price:        Optional[float] = None,
        image_url:    Optional[str] = None,
        retailer_url: Optional[str] = None,
    ) -> str:
        """Add a product to the wishlist. Returns new wishlist_id. Fire-and-forget."""
        from databricks.sdk.service.sql import StatementParameterListItem
        wishlist_id = str(uuid.uuid4())

        cols   = ["wishlist_id", "subject_ref", "app_id", "product_id", "added_at", "is_active"]
        vals   = [wishlist_id, subject_ref, self.app_id, product_id, "current_timestamp()", "true"]
        params = []

        def _p(name, value):
            if value is not None and value != "":
                cols.append(name)
                vals.append(f":{name}")
                params.append(StatementParameterListItem(name=name, value=str(value)))

        _p("product_name", product_name)
        _p("brand",        brand)
        _p("price",        price)
        _p("image_url",    image_url)
        _p("retailer_url", retailer_url)

        literal_cols = ["wishlist_id", "subject_ref", "app_id", "product_id", "added_at", "is_active"]
        col_list  = ", ".join(cols)
        val_parts = []
        for c, v in zip(cols, vals):
            if c in literal_cols or v in ("current_timestamp()", "true"):
                val_parts.append(v)
            else:
                val_parts.append(v)
        val_str = ", ".join(val_parts)

        sql = f"INSERT INTO {TABLE} ({col_list}) VALUES ({val_str})"

        # Rebuild cleanly using parameterised approach
        param_cols  = ["wishlist_id", "subject_ref", "app_id", "product_id"]
        param_vals  = [wishlist_id, subject_ref, self.app_id, product_id]
        param_items = [
            StatementParameterListItem(name="wishlist_id",  value=wishlist_id),
            StatementParameterListItem(name="subject_ref",  value=subject_ref),
            StatementParameterListItem(name="app_id",       value=self.app_id),
            StatementParameterListItem(name="product_id",   value=product_id),
        ]

        for name, value in [
            ("product_name", product_name),
            ("brand",        brand),
            ("price",        str(price) if price is not None else None),
            ("image_url",    image_url),
            ("retailer_url", retailer_url),
        ]:
            if value is not None:
                param_cols.append(name)
                param_items.append(StatementParameterListItem(name=name, value=value))

        col_str  = ", ".join(param_cols) + ", added_at, is_active"
        ph_str   = ", ".join(f":{c}" for c in param_cols) + ", current_timestamp(), true"
        final_sql = f"INSERT INTO {TABLE} ({col_str}) VALUES ({ph_str})"

        _exec_bg(final_sql, param_items)
        return wishlist_id

    def remove(self, subject_ref: str, product_id: str) -> None:
        """Soft-delete: set is_active=false for this product. Fire-and-forget."""
        from databricks.sdk.service.sql import StatementParameterListItem
        sql = (
            f"UPDATE {TABLE} SET is_active = false "
            f"WHERE subject_ref = :subject_ref AND product_id = :product_id AND app_id = :app_id"
        )
        params = [
            StatementParameterListItem(name="subject_ref", value=subject_ref),
            StatementParameterListItem(name="product_id",  value=product_id),
            StatementParameterListItem(name="app_id",      value=self.app_id),
        ]
        _exec_bg(sql, params)

    # ── reads ─────────────────────────────────────────────────────────────────

    def fetch(self, subject_ref: str) -> list:
        """Return all active wishlist items for a user. Synchronous."""
        from databricks.sdk.service.sql import StatementParameterListItem
        sql = (
            f"SELECT wishlist_id, product_id, product_name, brand, price, image_url, retailer_url, added_at "
            f"FROM {TABLE} "
            f"WHERE subject_ref = :subject_ref AND app_id = :app_id AND is_active = true "
            f"ORDER BY added_at DESC"
        )
        params = [
            StatementParameterListItem(name="subject_ref", value=subject_ref),
            StatementParameterListItem(name="app_id",      value=self.app_id),
        ]
        try:
            result = _exec(sql, params)
            if result.status.error:
                logger.warning("[WISHLIST] Fetch failed: %s", result.status.error.message)
                return []
            if not result.result or not result.result.data_array:
                return []
            cols = [c.name for c in result.manifest.schema.columns]
            return [dict(zip(cols, row)) for row in result.result.data_array]
        except Exception as e:
            logger.warning("[WISHLIST] Fetch error: %s", e)
            return []

    def is_in_wishlist(self, subject_ref: str, product_id: str) -> bool:
        """Check if a product is already in the user's active wishlist."""
        from databricks.sdk.service.sql import StatementParameterListItem
        sql = (
            f"SELECT COUNT(*) FROM {TABLE} "
            f"WHERE subject_ref = :subject_ref AND product_id = :product_id "
            f"AND app_id = :app_id AND is_active = true"
        )
        params = [
            StatementParameterListItem(name="subject_ref", value=subject_ref),
            StatementParameterListItem(name="product_id",  value=product_id),
            StatementParameterListItem(name="app_id",      value=self.app_id),
        ]
        try:
            result = _exec(sql, params)
            if result.result and result.result.data_array:
                return int(result.result.data_array[0][0]) > 0
        except Exception as e:
            logger.warning("[WISHLIST] is_in_wishlist error: %s", e)
        return False
