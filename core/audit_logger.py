"""
Audit Logger — writes guardrail and tool-call events to Delta tables.

Target tables (create once in Unity Catalog before first use):

  CREATE TABLE IF NOT EXISTS shopping_assistant.audit.guardrail_results (
    event_time    TIMESTAMP,
    request_id    STRING,
    user_id       STRING,
    session_id    STRING,
    guardrail_type STRING,
    query_preview STRING,
    status        STRING,
    issues        STRING,
    corrections_made BOOLEAN,
    latency_ms    DOUBLE
  ) USING DELTA;

  CREATE TABLE IF NOT EXISTS shopping_assistant.audit.tool_calls (
    event_time    TIMESTAMP,
    request_id    STRING,
    user_id       STRING,
    session_id    STRING,
    tool_name     STRING,
    index_name    STRING,
    query_preview STRING,
    filter_summary STRING,
    top_k         INT,
    result_count  INT,
    latency_ms    DOUBLE,
    error         STRING
  ) USING DELTA;

All writes are fire-and-forget (background thread) — they never block the main request.
"""

import os
import json
import logging
import threading
from datetime import datetime, timezone
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

_GUARDRAIL_TABLE = os.getenv(
    "AUDIT_GUARDRAIL_TABLE", "shopping_assistant.audit.guardrail_results"
)
_TOOL_CALLS_TABLE = os.getenv(
    "AUDIT_TOOL_CALLS_TABLE", "shopping_assistant.audit.tool_calls"
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _write_row(table: str, row: Dict[str, Any]) -> None:
    """Write one audit row to a Delta table via the Databricks SQL Warehouse."""
    try:
        from databricks.sdk import WorkspaceClient
        from databricks.sdk.service.sql import StatementParameterListItem

        warehouse_id = os.getenv("DATABRICKS_SQL_WAREHOUSE_ID")
        if not warehouse_id:
            logger.debug("[AUDIT] DATABRICKS_SQL_WAREHOUSE_ID not set — skipping write to %s", table)
            return

        cols = list(row.keys())
        col_list = ", ".join(cols)
        placeholders = ", ".join(f":{c}" for c in cols)
        sql = f"INSERT INTO {table} ({col_list}) VALUES ({placeholders})"

        params = [
            StatementParameterListItem(name=c, value=str(row[c]) if row[c] is not None else "")
            for c in cols
        ]

        w = WorkspaceClient()
        w.statement_execution.execute_statement(
            warehouse_id=warehouse_id,
            statement=sql,
            parameters=params,
            wait_timeout="10s",
        )
        logger.debug("[AUDIT] Row written to %s", table)

    except Exception as exc:
        logger.warning("[AUDIT] Failed to write to %s: %s", table, exc)


def _fire(table: str, row: Dict[str, Any]) -> None:
    """Background thread — never blocks the caller."""
    threading.Thread(target=_write_row, args=(table, row), daemon=True).start()


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------

def log_guardrail(
    *,
    guardrail_type: str,
    status: str,
    issues: list,
    corrections_made: bool,
    latency_ms: float,
    query_preview: str = "",
    request_id: Optional[str] = None,
    user_id: Optional[str] = None,
    session_id: Optional[str] = None,
) -> None:
    """Log one guardrail check result (pass, warning, or fail)."""
    row = {
        "event_time":       _now_iso(),
        "request_id":       request_id or "",
        "user_id":          user_id or "anon",
        "session_id":       session_id or "",
        "guardrail_type":   guardrail_type,
        "query_preview":    query_preview[:120],
        "status":           status,
        "issues":           json.dumps(issues),
        "corrections_made": str(corrections_made),
        "latency_ms":       str(round(latency_ms, 2)),
    }
    _fire(_GUARDRAIL_TABLE, row)


def log_tool_call(
    *,
    tool_name: str,
    index_name: str = "",
    query_preview: str = "",
    filter_summary: str = "",
    top_k: int = 0,
    result_count: int = 0,
    latency_ms: float = 0.0,
    error: str = "",
    request_id: Optional[str] = None,
    user_id: Optional[str] = None,
    session_id: Optional[str] = None,
) -> None:
    """Log one vector-search (or external tool) invocation."""
    row = {
        "event_time":      _now_iso(),
        "request_id":      request_id or "",
        "user_id":         user_id or "anon",
        "session_id":      session_id or "",
        "tool_name":       tool_name,
        "index_name":      index_name,
        "query_preview":   query_preview[:120],
        "filter_summary":  filter_summary[:300],
        "top_k":           str(top_k),
        "result_count":    str(result_count),
        "latency_ms":      str(round(latency_ms, 2)),
        "error":           error[:500],
    }
    _fire(_TOOL_CALLS_TABLE, row)
