"""
CartStore — Delta-backed shopping cart persistence.

Follows the same fire-and-forget pattern as WishlistStore:
  - pure Python, no PySpark
  - databricks.sdk WorkspaceClient + statement_execution for all reads/writes
  - background threads for writes — zero latency impact on requests

Table: shopping_assistant.user_data.cart_items
"""

import os
import uuid
import logging
import threading
from typing import Optional

logger = logging.getLogger(__name__)

CATALOG = os.getenv("AUDIT_CATALOG", "shopping_assistant")
TABLE   = f"{CATALOG}.user_data.cart_items"

DDL = f"""
CREATE TABLE IF NOT EXISTS {TABLE} (
    cart_item_id  STRING    NOT NULL,
    subject_ref   STRING    NOT NULL,
    app_id        STRING    NOT NULL,
    product_id    STRING    NOT NULL,
    product_name  STRING,
    brand         STRING,
    price         DOUBLE,
    image_url     STRING,
    retailer_url  STRING,
    quantity      INT       DEFAULT 1,
    added_at      TIMESTAMP,
    updated_at    TIMESTAMP,
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
    from databricks.sdk.service.sql import StatementParameterListItem
    w = _client()
    kwargs = dict(warehouse_id=_warehouse(), statement=sql, wait_timeout="10s")
    if params:
        kwargs["parameters"] = params
    return w.statement_execution.execute_statement(**kwargs)


def _exec_bg(sql: str, params: list = None):
    def _run():
        try:
            _exec(sql, params)
        except Exception as e:
            logger.warning("[CART] Background write failed: %s", e)
    threading.Thread(target=_run, daemon=True).start()


def ensure_table():
    try:
        _exec(DDL)
        logger.info("[CART] Table ready: %s", TABLE)
    except Exception as e:
        logger.warning("[CART] Table creation failed (may already exist): %s", e)


class CartStore:
    """
    Manages cart reads and writes for a single user (identified by subject_ref).

    Instantiate once in ShoppingAssistantWorkflow.__init__() alongside WishlistStore.
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
        quantity:     int = 1,
    ) -> str:
        """
        Add a product to the cart. If already active, increments quantity by `quantity`.
        Returns new cart_item_id (or existing, for increment case). Fire-and-forget.
        """
        from databricks.sdk.service.sql import StatementParameterListItem

        existing = self._get_item(subject_ref, product_id)
        if existing:
            new_qty = int(existing.get("quantity", 1)) + quantity
            self._update_quantity(subject_ref, product_id, new_qty)
            return existing["cart_item_id"]

        cart_item_id = str(uuid.uuid4())
        param_cols  = ["cart_item_id", "subject_ref", "app_id", "product_id", "quantity"]
        param_items = [
            StatementParameterListItem(name="cart_item_id", value=cart_item_id),
            StatementParameterListItem(name="subject_ref",  value=subject_ref),
            StatementParameterListItem(name="app_id",       value=self.app_id),
            StatementParameterListItem(name="product_id",   value=product_id),
            StatementParameterListItem(name="quantity",     value=str(quantity)),
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

        col_str = ", ".join(param_cols) + ", added_at, updated_at, is_active"
        ph_str  = ", ".join(f":{c}" for c in param_cols) + ", current_timestamp(), current_timestamp(), true"
        sql     = f"INSERT INTO {TABLE} ({col_str}) VALUES ({ph_str})"

        _exec_bg(sql, param_items)
        return cart_item_id

    def remove(self, subject_ref: str, product_id: str) -> None:
        """Soft-delete: set is_active=false. Fire-and-forget."""
        from databricks.sdk.service.sql import StatementParameterListItem
        sql = (
            f"UPDATE {TABLE} SET is_active = false, updated_at = current_timestamp() "
            f"WHERE subject_ref = :subject_ref AND product_id = :product_id AND app_id = :app_id"
        )
        params = [
            StatementParameterListItem(name="subject_ref", value=subject_ref),
            StatementParameterListItem(name="product_id",  value=product_id),
            StatementParameterListItem(name="app_id",      value=self.app_id),
        ]
        _exec_bg(sql, params)

    def update_quantity(self, subject_ref: str, product_id: str, quantity: int) -> None:
        """Update quantity for an active cart item. quantity <= 0 removes the item."""
        if quantity <= 0:
            self.remove(subject_ref, product_id)
        else:
            self._update_quantity(subject_ref, product_id, quantity)

    def _update_quantity(self, subject_ref: str, product_id: str, quantity: int) -> None:
        from databricks.sdk.service.sql import StatementParameterListItem
        sql = (
            f"UPDATE {TABLE} SET quantity = :quantity, updated_at = current_timestamp() "
            f"WHERE subject_ref = :subject_ref AND product_id = :product_id "
            f"AND app_id = :app_id AND is_active = true"
        )
        params = [
            StatementParameterListItem(name="quantity",    value=str(quantity)),
            StatementParameterListItem(name="subject_ref", value=subject_ref),
            StatementParameterListItem(name="product_id",  value=product_id),
            StatementParameterListItem(name="app_id",      value=self.app_id),
        ]
        _exec_bg(sql, params)

    # ── reads ─────────────────────────────────────────────────────────────────

    def fetch(self, subject_ref: str) -> list:
        """Return all active cart items for a user. Synchronous."""
        from databricks.sdk.service.sql import StatementParameterListItem
        sql = (
            f"SELECT cart_item_id, product_id, product_name, brand, price, "
            f"image_url, retailer_url, quantity, added_at, updated_at "
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
                logger.warning("[CART] Fetch failed: %s", result.status.error.message)
                return []
            if not result.result or not result.result.data_array:
                return []
            cols = [c.name for c in result.manifest.schema.columns]
            rows = [dict(zip(cols, row)) for row in result.result.data_array]
            # coerce numeric types for JSON serialisation
            for r in rows:
                if r.get("price") is not None:
                    try:
                        r["price"] = float(r["price"])
                    except (ValueError, TypeError):
                        pass
                if r.get("quantity") is not None:
                    try:
                        r["quantity"] = int(r["quantity"])
                    except (ValueError, TypeError):
                        r["quantity"] = 1
            return rows
        except Exception as e:
            logger.warning("[CART] Fetch error: %s", e)
            return []

    def is_in_cart(self, subject_ref: str, product_id: str) -> bool:
        """Check if a product is already in the user's active cart."""
        return self._get_item(subject_ref, product_id) is not None

    def _get_item(self, subject_ref: str, product_id: str) -> Optional[dict]:
        from databricks.sdk.service.sql import StatementParameterListItem
        sql = (
            f"SELECT cart_item_id, quantity FROM {TABLE} "
            f"WHERE subject_ref = :subject_ref AND product_id = :product_id "
            f"AND app_id = :app_id AND is_active = true LIMIT 1"
        )
        params = [
            StatementParameterListItem(name="subject_ref", value=subject_ref),
            StatementParameterListItem(name="product_id",  value=product_id),
            StatementParameterListItem(name="app_id",      value=self.app_id),
        ]
        try:
            result = _exec(sql, params)
            if result.result and result.result.data_array:
                cols = [c.name for c in result.manifest.schema.columns]
                return dict(zip(cols, result.result.data_array[0]))
        except Exception as e:
            logger.warning("[CART] _get_item error: %s", e)
        return None
