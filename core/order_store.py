"""
OrderStore — Delta-backed order history for the Shopping Assistant.

Table: shopping_assistant.user_data.orders
place_order() atomically copies the active cart to order rows and soft-deletes
the cart items in a single fire-and-forget thread.
"""

import os
import uuid
import logging
import threading
from typing import List, Optional

logger = logging.getLogger(__name__)

CATALOG   = os.getenv("AUDIT_CATALOG", "shopping_assistant")
ORDER_TBL = f"{CATALOG}.user_data.orders"
CART_TBL  = f"{CATALOG}.user_data.cart_items"

DDL = f"""
CREATE TABLE IF NOT EXISTS {ORDER_TBL} (
    order_id      STRING    NOT NULL,
    order_item_id STRING    NOT NULL,
    subject_ref   STRING    NOT NULL,
    app_id        STRING    NOT NULL,
    product_id    STRING,
    product_name  STRING,
    brand         STRING,
    price         DOUBLE,
    image_url     STRING,
    retailer_url  STRING,
    quantity      INT,
    unit_price    DOUBLE,
    line_total    DOUBLE,
    placed_at     TIMESTAMP,
    status        STRING
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
            logger.warning("[ORDER] Background write failed: %s", e)
    threading.Thread(target=_run, daemon=True).start()


def ensure_table():
    try:
        _exec(DDL)
        logger.info("[ORDER] Table ready: %s", ORDER_TBL)
    except Exception as e:
        logger.warning("[ORDER] Table creation failed (may already exist): %s", e)


class OrderStore:
    def __init__(self, app_id: str = None):
        self.app_id = app_id or os.getenv("AUDIT_APP_ID", "myre_app")

    def place_order(self, subject_ref: str, cart_items: List[dict]) -> Optional[str]:
        """
        Create order rows from cart_items and clear the cart.
        Returns order_id, or None if cart is empty.
        Fire-and-forget for all Delta writes.
        """
        if not cart_items:
            return None

        order_id = str(uuid.uuid4())

        from databricks.sdk.service.sql import StatementParameterListItem

        # Insert one order row per cart item
        for item in cart_items:
            item_id    = str(uuid.uuid4())
            quantity   = int(item.get("quantity", 1))
            unit_price = float(item.get("price", 0))
            line_total = round(quantity * unit_price, 2)

            sql = (
                f"INSERT INTO {ORDER_TBL} "
                f"(order_id, order_item_id, subject_ref, app_id, product_id, product_name, "
                f"brand, price, image_url, retailer_url, quantity, unit_price, line_total, placed_at, status) "
                f"VALUES (:order_id, :order_item_id, :subject_ref, :app_id, :product_id, :product_name, "
                f":brand, :price, :image_url, :retailer_url, :quantity, :unit_price, :line_total, "
                f"current_timestamp(), 'confirmed')"
            )
            params = [
                StatementParameterListItem(name="order_id",      value=order_id),
                StatementParameterListItem(name="order_item_id", value=item_id),
                StatementParameterListItem(name="subject_ref",   value=subject_ref),
                StatementParameterListItem(name="app_id",        value=self.app_id),
                StatementParameterListItem(name="product_id",    value=str(item.get("product_id", ""))),
                StatementParameterListItem(name="product_name",  value=str(item.get("product_name", ""))),
                StatementParameterListItem(name="brand",         value=str(item.get("brand", ""))),
                StatementParameterListItem(name="price",         value=str(unit_price)),
                StatementParameterListItem(name="image_url",     value=str(item.get("image_url", ""))),
                StatementParameterListItem(name="retailer_url",  value=str(item.get("retailer_url", ""))),
                StatementParameterListItem(name="quantity",      value=str(quantity)),
                StatementParameterListItem(name="unit_price",    value=str(unit_price)),
                StatementParameterListItem(name="line_total",    value=str(line_total)),
            ]
            _exec_bg(sql, params)

        # Soft-delete all active cart items for this user
        clear_sql = (
            f"UPDATE {CART_TBL} SET is_active = false "
            f"WHERE subject_ref = :subject_ref AND app_id = :app_id AND is_active = true"
        )
        clear_params = [
            StatementParameterListItem(name="subject_ref", value=subject_ref),
            StatementParameterListItem(name="app_id",      value=self.app_id),
        ]
        _exec_bg(clear_sql, clear_params)

        return order_id

    def fetch_orders(self, subject_ref: str, limit: int = 20) -> List[dict]:
        """Return order history grouped by order_id, most recent first."""
        from databricks.sdk.service.sql import StatementParameterListItem
        sql = (
            f"SELECT order_id, order_item_id, product_id, product_name, brand, "
            f"price, image_url, retailer_url, quantity, unit_price, line_total, placed_at, status "
            f"FROM {ORDER_TBL} "
            f"WHERE subject_ref = :subject_ref AND app_id = :app_id "
            f"ORDER BY placed_at DESC "
            f"LIMIT {int(limit)}"
        )
        params = [
            StatementParameterListItem(name="subject_ref", value=subject_ref),
            StatementParameterListItem(name="app_id",      value=self.app_id),
        ]
        try:
            result = _exec(sql, params)
            if not (result.result and result.result.data_array):
                return []
            cols = ["order_id", "order_item_id", "product_id", "product_name", "brand",
                    "price", "image_url", "retailer_url", "quantity", "unit_price",
                    "line_total", "placed_at", "status"]
            rows = []
            for row in result.result.data_array:
                d = dict(zip(cols, row))
                d["price"]      = float(d["price"])      if d["price"]      else 0.0
                d["unit_price"] = float(d["unit_price"]) if d["unit_price"] else 0.0
                d["line_total"] = float(d["line_total"]) if d["line_total"] else 0.0
                d["quantity"]   = int(d["quantity"])     if d["quantity"]   else 1
                rows.append(d)
            # Group into orders
            orders = {}
            for row in rows:
                oid = row["order_id"]
                if oid not in orders:
                    orders[oid] = {
                        "order_id":  oid,
                        "placed_at": row["placed_at"],
                        "status":    row["status"],
                        "items":     [],
                        "total":     0.0,
                    }
                orders[oid]["items"].append(row)
                orders[oid]["total"] = round(orders[oid]["total"] + row["line_total"], 2)
            return list(orders.values())
        except Exception as e:
            logger.warning("[ORDER] fetch_orders error: %s", e)
            return []
