"""
ErasureStore — Delta-backed queue for Right-to-be-Forgotten requests.

Table: shopping_assistant.user_data.erasure_requests
Ops team processes requests from this table within the regulatory deadline
(30 days GDPR / 45 days CCPA). Actual data deletion is a separate ops step.
"""

import os
import uuid
import logging
import threading
from typing import Optional

logger = logging.getLogger(__name__)

CATALOG = os.getenv("AUDIT_CATALOG", "shopping_assistant")
TABLE   = f"{CATALOG}.user_data.erasure_requests"

DDL = f"""
CREATE TABLE IF NOT EXISTS {TABLE} (
    request_id    STRING    NOT NULL,
    subject_ref   STRING    NOT NULL,
    app_id        STRING    NOT NULL,
    regulation    STRING,
    status        STRING    DEFAULT 'pending',
    requested_at  TIMESTAMP,
    processed_at  TIMESTAMP,
    notes         STRING
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
            logger.warning("[ERASURE] Background write failed: %s", e)
    threading.Thread(target=_run, daemon=True).start()


def ensure_table():
    try:
        _exec(DDL)
        logger.info("[ERASURE] Table ready: %s", TABLE)
    except Exception as e:
        logger.warning("[ERASURE] Table creation failed (may already exist): %s", e)


class ErasureStore:
    def __init__(self, app_id: str = None):
        self.app_id = app_id or os.getenv("AUDIT_APP_ID", "myre_app")

    def raise_request(
        self,
        subject_ref: str,
        regulation:  Optional[str] = None,
    ) -> str:
        """Queue an erasure request. Returns request_id. Fire-and-forget."""
        from databricks.sdk.service.sql import StatementParameterListItem
        request_id = str(uuid.uuid4())
        sql = (
            f"INSERT INTO {TABLE} "
            f"(request_id, subject_ref, app_id, regulation, status, requested_at) "
            f"VALUES (:request_id, :subject_ref, :app_id, :regulation, 'pending', current_timestamp())"
        )
        params = [
            StatementParameterListItem(name="request_id",  value=request_id),
            StatementParameterListItem(name="subject_ref", value=subject_ref),
            StatementParameterListItem(name="app_id",      value=self.app_id),
            StatementParameterListItem(name="regulation",  value=regulation or "UNKNOWN"),
        ]
        _exec_bg(sql, params)
        return request_id

    def has_pending_request(self, subject_ref: str) -> bool:
        """Check if an active erasure request already exists for this user."""
        from databricks.sdk.service.sql import StatementParameterListItem
        sql = (
            f"SELECT COUNT(*) FROM {TABLE} "
            f"WHERE subject_ref = :subject_ref AND app_id = :app_id AND status = 'pending'"
        )
        params = [
            StatementParameterListItem(name="subject_ref", value=subject_ref),
            StatementParameterListItem(name="app_id",      value=self.app_id),
        ]
        try:
            result = _exec(sql, params)
            if result.result and result.result.data_array:
                return int(result.result.data_array[0][0]) > 0
        except Exception as e:
            logger.warning("[ERASURE] has_pending_request error: %s", e)
        return False
