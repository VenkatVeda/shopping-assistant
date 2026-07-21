"""
AuditWrapper — writes AI interaction audit events to Delta tables
in the shopping_assistant catalog on Databricks.

Follows the same pattern as core/audit_logger.py:
  - pure Python, no PySpark, no spark globals
  - databricks.sdk WorkspaceClient + statement_execution for all writes
  - background threads (fire-and-forget) — zero latency impact on requests
  - instantiate once in ShoppingAssistantWorkflow.__init__(), not per request

Usage in workflow.py:
    # in __init__:
    from audit_wrapper import AuditWrapper
    self.audit_wrapper = AuditWrapper(
        catalog = os.getenv("AUDIT_CATALOG", "shopping_assistant"),
    )

    # in process_query, after final_state["trace_id"] = trace_id:
    self.audit_wrapper.log_interaction(
        user_email      = user_id,
        user_input      = query,
        model_output    = final_state.get("safe_response") or "",
        model_name      = os.getenv("DATABRICKS_CHAT_ENDPOINT", ""),
        status          = "success",
        session_id      = session_id,
        user_country    = _user_country,
        mlflow_trace_id = trace_id,
        final_state     = final_state,
    )

Required .env variables:
    AUDIT_APP_ID=myre_app
    AUDIT_CATALOG=shopping_assistant
    AUDIT_SECRET_SCOPE=audit_trail_secrets
    DATABRICKS_SQL_WAREHOUSE_ID=...
    DATABRICKS_HOST=https://...
    DATABRICKS_TOKEN=dapi...
"""

import os
import re
import json
import hmac as hmac_lib
import hashlib
import logging
import threading
import uuid
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

# ── jurisdiction mapper ────────────────────────────────────────────────────
_EU_COUNTRIES = {
    "AT","BE","BG","CY","CZ","DE","DK","EE","ES","FI",
    "FR","GR","HR","HU","IE","IT","LT","LU","LV","MT",
    "NL","PL","PT","RO","SE","SI","SK","GB"
}

def _determine_regulation(country: str = "", state: str = "", sector: str = "") -> str:
    country = (country or "").upper().strip()
    state   = (state   or "").upper().strip()
    sector  = (sector  or "").lower().strip()
    if sector == "health":                return "HIPAA"
    if country in _EU_COUNTRIES:          return "GDPR"
    if country == "US" and state == "CA": return "CCPA"
    if country == "IN":                   return "DPDP"
    return "INTERNAL_POLICY"

# ── PII redactor ───────────────────────────────────────────────────────────
_PII_PATTERNS = [
    ("EMAIL",   r'\b[\w\.-]+@[\w\.-]+\.\w{2,}\b'),
    ("PHONE",   r'\b(\+?\d{1,3}[\s-]?)?\d{10}\b'),
    ("AADHAAR", r'\b\d{4}\s?\d{4}\s?\d{4}\b'),
    ("PAN",     r'\b[A-Z]{5}\d{4}[A-Z]\b'),
    ("CARD",    r'\b\d{4}[\s-]?\d{4}[\s-]?\d{4}[\s-]?\d{4}\b'),
    ("SSN",     r'\b\d{3}-\d{2}-\d{4}\b'),
]
_NAME_TRIGGERS = [
    r'(?:i am|my name is|this is|hi,?\s+i.?m)\s+([A-Z][a-z]+(\s+[A-Z][a-z]+)?)',
]

def _redact_pii(text: str) -> str:
    if not text:
        return text
    result = text
    for label, pattern in _PII_PATTERNS:
        result = re.sub(pattern, f'[{label}]', result)
    for pattern in _NAME_TRIGGERS:
        def _rep(m):
            return m.group(0).replace(m.group(1), '[NAME]')
        result = re.sub(pattern, _rep, result, flags=re.IGNORECASE)
    return result

# ── low-level writer ───────────────────────────────────────────────────────
def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

def _write_row(table: str, row: dict) -> None:
    """
    Write one row to a Delta table via Databricks SQL Warehouse.
    Columns with None or empty string are omitted from the INSERT
    so Delta uses the column default (NULL) — this avoids CAST errors
    when trying to insert '' into DOUBLE or BIGINT columns.
    """
    try:
        from databricks.sdk import WorkspaceClient
        from databricks.sdk.service.sql import StatementParameterListItem

        warehouse_id = os.getenv("DATABRICKS_SQL_WAREHOUSE_ID")
        if not warehouse_id:
            logger.warning("[AUDIT] DATABRICKS_SQL_WAREHOUSE_ID not set — skipping write to %s", table)
            return

        # only include columns that have a real value
        # omitting empty strings prevents CAST errors on DOUBLE/BIGINT columns
        cols_to_insert = [
            c for c in row.keys()
            if row[c] is not None and row[c] != ""
        ]

        if not cols_to_insert:
            logger.warning("[AUDIT] No columns to insert for table %s", table)
            return

        col_list     = ", ".join(cols_to_insert)
        placeholders = ", ".join(f":{c}" for c in cols_to_insert)
        sql          = f"INSERT INTO {table} ({col_list}) VALUES ({placeholders})"

        params = [
            StatementParameterListItem(
                name  = c,
                value = str(row[c])
            )
            for c in cols_to_insert
        ]

        w = WorkspaceClient()
        result = w.statement_execution.execute_statement(
            warehouse_id = warehouse_id,
            statement    = sql,
            parameters   = params,
            wait_timeout = "10s",
        )

        if result.status.error:
            logger.warning("[AUDIT] Write failed for %s: %s", table, result.status.error.message)
        else:
            logger.debug("[AUDIT] Row written to %s", table)

    except Exception as exc:
        logger.warning("[AUDIT] Failed to write to %s: %s", table, exc)


def _fire(table: str, row: dict) -> None:
    """Fire-and-forget background write — never blocks the caller."""
    threading.Thread(target=_write_row, args=(table, row), daemon=True).start()


# ── AuditWrapper ───────────────────────────────────────────────────────────

class AuditWrapper:
    """
    Drop-in audit logging wrapper for any AI app on the platform.

    Instantiate ONCE in ShoppingAssistantWorkflow.__init__():
        self.audit_wrapper = AuditWrapper()

    Then call in process_query() after the graph completes:
        self.audit_wrapper.log_interaction(...)
    """

    def __init__(
        self,
        app_id:         str = None,
        catalog:        str = "shopping_assistant",
        schema_version: str = "1.0",
    ):
        # app_id: explicit value > env var > error (no silent default in production)
        self.app_id = app_id or os.getenv("AUDIT_APP_ID")
        if not self.app_id:
            raise ValueError(
                "AUDIT_APP_ID is not configured. "
                "Set it in .env or pass app_id explicitly to AuditWrapper()."
            )

        self.catalog        = catalog or os.getenv("AUDIT_CATALOG", "shopping_assistant")
        self.schema_version = schema_version
        self._key_cache: dict = {}

        # validate app is registered — runs once at startup, not per request
        self._validate_app()
        logger.info("[AUDIT] AuditWrapper ready. app_id=%s", self.app_id)

    # ── private helpers ───────────────────────────────────────────────────

    def _tbl(self, name: str) -> str:
        return f"{self.catalog}.{name}"

    def _get_key(self) -> bytes:
        """
        Retrieve HMAC key from Databricks secret scope. Cached after first call.
        Raises RuntimeError if key cannot be loaded — fail fast rather than
        producing incorrect subject_ref values that would break erasure lookups.
        """
        if self.app_id not in self._key_cache:
            scope    = os.getenv("AUDIT_SECRET_SCOPE", "audit_trail_secrets")
            key_name = f"hmac_key_{self.app_id}"
            try:
                from databricks.sdk import WorkspaceClient
                import base64
                w      = WorkspaceClient()
                secret = w.secrets.get_secret(scope=scope, key=key_name)
                try:
                    raw = base64.b64decode(secret.value).decode("utf-8")
                except Exception:
                    raw = secret.value
                self._key_cache[self.app_id] = raw.encode()
            except Exception as e:
                raise RuntimeError(
                    f"[AUDIT] Unable to load HMAC key '{key_name}' from scope '{scope}'. "
                    f"Check AUDIT_SECRET_SCOPE and Databricks Secrets access. "
                    f"Error: {e}"
                )
        return self._key_cache[self.app_id]

    def _hmac(self, value: str) -> str:
        return hmac_lib.new(
            self._get_key(),
            value.encode(),
            hashlib.sha256
        ).hexdigest()

    def _compute_refs(self, email: str) -> tuple:
        """
        Two-step pseudonymisation: email → subject_id → subject_ref.
        Raises ValueError if email is None or empty.
        """
        if not email:
            raise ValueError(
                "user_email is required and cannot be None or empty. "
                "Every audit record must be linked to a user."
            )
        subject_id  = self._hmac(email.lower().strip())
        subject_ref = self._hmac(subject_id)
        return subject_id, subject_ref

    def _validate_app(self) -> None:
        """Check app is registered and active in app_registry. Runs once at init."""
        try:
            from databricks.sdk import WorkspaceClient
            from databricks.sdk.service.sql import StatementState

            warehouse_id = os.getenv("DATABRICKS_SQL_WAREHOUSE_ID")
            if not warehouse_id:
                logger.warning("[AUDIT] No warehouse ID — skipping app_registry validation")
                return

            w      = WorkspaceClient()
            result = w.statement_execution.execute_statement(
                warehouse_id = warehouse_id,
                statement    = f"""
                    SELECT status FROM {self._tbl('raw_logs.app_registry')}
                    WHERE app_id = '{self.app_id}'
                    ORDER BY entry_created_at DESC
                    LIMIT 1
                """,
                wait_timeout = "10s",
            )

            if result.status.state != StatementState.SUCCEEDED:
                logger.warning("[AUDIT] app_registry check failed: %s", result.status.error)
                return

            if (
                not result.result
                or not result.result.data_array
                or len(result.result.data_array) == 0
            ):
                raise ValueError(
                    f"[AUDIT] app_id '{self.app_id}' not found in app_registry. "
                    f"Run the onboarding notebook first."
                )

            status = result.result.data_array[0][0]
            if status != "active":
                raise ValueError(
                    f"[AUDIT] app '{self.app_id}' status is '{status}', not 'active'."
                )

        except ValueError:
            raise
        except Exception as e:
            logger.warning("[AUDIT] app_registry validation error (non-blocking): %s", e)

    # ── public methods ────────────────────────────────────────────────────

    def start_session(
        self,
        user_email:  str,
        channel:     Optional[str] = None,
        device_type: Optional[str] = None,
    ) -> str:
        """Start a new session. Returns session_id. Call once per conversation."""
        session_id = str(uuid.uuid4())
        _, sref    = self._compute_refs(user_email)
        now        = _now_iso()
        row = {
            "session_id":        session_id,
            "app_id":            self.app_id,
            "subject_ref":       sref,
            "channel":           channel     or None,
            "device_type":       device_type or None,
            "started_at":        now,
            "is_erasure_flag":   "false",
            "created_at":        now,
            "schema_version":    self.schema_version,
        }
        _fire(self._tbl("raw_logs.sessions_raw"), row)
        return session_id

    # ── NEW: log_session ──────────────────────────────────────────────────
    def log_session(
        self,
        session_id:  str,
        user_email:  str,
        channel:     Optional[str] = None,
        device_type: Optional[str] = None,
    ) -> None:
        """
        Log an existing session_id to sessions_raw. Fire-and-forget.
        Use this when session_id is already generated by the app
        (e.g. passed in from process_query) rather than generating a new one.
        Call once per new conversation — not per query.
        """
        try:
            _, sref = self._compute_refs(user_email)
            now     = _now_iso()
            row = {
                "session_id":      session_id,
                "app_id":          self.app_id,
                "subject_ref":     sref,
                "channel":         channel     or None,
                "device_type":     device_type or None,
                "started_at":      now,
                "is_erasure_flag": "false",
                "created_at":      now,
                "schema_version":  self.schema_version,
            }
            _fire(self._tbl("raw_logs.sessions_raw"), row)
        except Exception as e:
            self._log_failure(session_id, "sessions_raw", e)

    # ── NEW: log_node_execution ───────────────────────────────────────────
    def log_node_execution(
        self,
        trace_id:       str,
        node_name:      str,
        status:         str             = "success",
        node_input:     Optional[str]   = None,
        node_output:    Optional[str]   = None,
        latency_ms:     Optional[float] = None,
        error_msg:      Optional[str]   = None,
        subject_ref:    Optional[str]   = None,
        node_type:      Optional[str]   = None,
        node_order:     Optional[int]   = None,
        parent_node_id: Optional[str]   = None,
        model_name:     Optional[str]   = None,
        model_version:  Optional[str]   = None,
        tokens_used:    Optional[int]   = None,
        retry_count:    Optional[int]   = None,
    ) -> None:
        """
        Log one LangGraph node execution to node_executions_raw. Fire-and-forget.
        Call from inside each node function in workflow.py after the node completes.
        node_input and node_output are PII-redacted, stored as input_summary /
        output_summary.

        node_type     : "guardrail" / "llm" / "tool" / "router" / "retriever"
        node_order    : integer position in pipeline (1, 2, 3 ...)
        parent_node_id: node_execution_id of triggering node (branching pipelines)
        model_name    : only for LLM nodes — which model was called
        tokens_used   : only for LLM nodes — token count
        """
        try:
            started    = _now_iso()
            input_san  = _redact_pii(str(node_input  or ""))
            output_san = _redact_pii(str(node_output or ""))
            ended      = _now_iso()
            row = {
                "node_execution_id": str(uuid.uuid4()),
                "trace_id":          trace_id,
                "app_id":            self.app_id,
                "node_name":         node_name,
                "status":            status,
                "input_summary":     input_san  or None,
                "output_summary":    output_san or None,
                "error_message":     error_msg  or None,
                "started_at":        started,
                "ended_at":          ended,
                "is_erasure_flag":   "false",
                "created_at":        ended,
                "schema_version":    self.schema_version,
                # optional identity columns
                "subject_ref":       subject_ref    or None,
                "node_type":         node_type      or None,
                "parent_node_id":    parent_node_id or None,
                "model_name":        model_name     or None,
                "model_version":     model_version  or None,
                # numeric — use `is not None` so 0 values are not dropped
                "latency_ms":        str(int(round(latency_ms))) if latency_ms is not None else None,
                "node_order":        str(node_order)  if node_order  is not None else None,
                "tokens_used":       str(tokens_used) if tokens_used is not None else None,
                "retry_count":       str(retry_count) if retry_count is not None else None,
            }
            _fire(self._tbl("raw_logs.node_executions_raw"), row)
        except Exception as e:
            self._log_failure(trace_id, "node_executions_raw", e)

    def log_interaction(
        self,
        user_email:             str,
        user_input:             str,
        model_output:           str,
        model_name:             str,
        status:                 str,
        session_id:             Optional[str]  = None,
        model_version:          Optional[str]  = None,
        user_country:           Optional[str]  = None,
        user_state:             Optional[str]  = None,
        system_prompt_version:  Optional[str]  = None,
        consent_version:        Optional[str]  = None,
        mlflow_trace_id:        Optional[str]  = None,
        final_state:            Optional[dict] = None,
    ) -> dict:
        """
        Core method. Call once per AI interaction after the graph completes.
        Fire-and-forget — never blocks the request.
        user_email must be a valid non-empty string.
        user_country must be passed by the caller from the user profile.
        Returns dict with trace_id and subject_ref for chaining.
        """
        trace_id = str(uuid.uuid4())
        result   = {"trace_id": trace_id, "status": "ok"}

        try:
            # guard — user_email must be provided
            if not user_email:
                raise ValueError(
                    "user_email is required for log_interaction. "
                    "Pass the authenticated user's email."
                )

            _, sref    = self._compute_refs(user_email)
            regulation = _determine_regulation(
                user_country or "",
                user_state   or "",
            )

            input_san  = _redact_pii(user_input  or "")
            output_san = _redact_pii(model_output or "")
            input_hash = self._hmac(user_input or "")

            result["subject_ref"] = sref
            result["regulation"]  = regulation

            intent       = (final_state or {}).get("intent")
            result_count = len((final_state or {}).get("reranked_results") or [])
            g_status     = (final_state or {}).get("guardrail_status")
            now          = _now_iso()

            # ── ai_interactions_raw ──────────────────────────────────────
            # None values are omitted from INSERT to avoid CAST errors
            # on DOUBLE/BIGINT columns (confidence_score, latency_ms)
            int_row = {
                "trace_id":              trace_id,
                "app_id":                self.app_id,
                "subject_ref":           sref,
                "request_timestamp":     now,
                "regulation_at_time":    regulation,
                "input_text_sanitised":  input_san,
                "input_hash":            input_hash,
                "system_prompt_version": system_prompt_version or "v1.0",
                "model_name":            model_name,
                "provider":              "databricks",
                "status":                status,
                "is_erasure_flag":       "false",
                "app_metadata":          json.dumps({
                    "intent":           intent,
                    "result_count":     result_count,
                    "guardrail_status": g_status,
                    "mlflow_trace_id":  mlflow_trace_id,
                }),
                "created_at":            now,
                "schema_version":        self.schema_version,
                # optional — only include if provided
                "session_id":            session_id     or None,
                "user_country":          user_country   or None,
                "user_state":            user_state     or None,
                "model_version":         model_version  or None,
                "run_id":                mlflow_trace_id or None,
                "consent_version":       consent_version or None,
                # numeric columns — None means omit from INSERT (no CAST error)
                "confidence_score":      None,
                "latency_ms":            None,
            }
            _fire(self._tbl("raw_logs.ai_interactions_raw"), int_row)

            # ── model_outputs_raw ────────────────────────────────────────
            out_row = {
                "output_id":             str(uuid.uuid4()),
                "trace_id":              trace_id,
                "app_id":                self.app_id,
                "subject_ref":           sref,
                "output_text_sanitised": output_san,
                "output_hash":           self._hmac(model_output or ""),
                "output_type":           "recommendation",
                "finish_reason":         "stop",
                "contains_pii_flag":     "false",
                "generated_at":          now,
                "is_erasure_flag":       "false",
                "created_at":            now,
                "schema_version":        self.schema_version,
                # numeric columns — omitted (None) to avoid CAST errors
                "confidence_score":      None,
                "tokens_used":           None,
            }
            _fire(self._tbl("raw_logs.model_outputs_raw"), out_row)

        except Exception as e:
            result["status"] = "logging_failed"
            result["error"]  = str(e)
            self._log_failure(trace_id, "ai_interactions_raw", e)

        return result

    def log_guardrail(
        self,
        trace_id:        str,
        policy_name:     str,
        score:           float,
        result:          str,
        triggered_block: bool,
        subject_ref:     Optional[str] = None,
    ) -> None:
        """Log one guardrail check. Fire-and-forget."""
        now = _now_iso()
        row = {
            "guardrail_id":    str(uuid.uuid4()),
            "trace_id":        trace_id,
            "app_id":          self.app_id,
            "policy_name":     policy_name,
            "result":          result,
            "triggered_block": str(triggered_block).lower(),
            "checked_at":      now,
            "is_erasure_flag": "false",
            "created_at":      now,
            "schema_version":  self.schema_version,
            # optional
            "subject_ref":     subject_ref or None,
            # numeric — omit if None to avoid CAST errors
            "score":           str(round(score, 4)) if score is not None else None,
        }
        _fire(self._tbl("raw_logs.guardrail_results_raw"), row)

    def log_tool_call(
        self,
        trace_id:      str,
        tool_name:     str,
        tool_inputs:   Optional[dict]  = None,
        tool_outputs:  Optional[dict]  = None,
        status:        str             = "success",
        latency_ms:    Optional[float] = None,
        error_message: Optional[str]   = None,
        subject_ref:   Optional[str]   = None,
    ) -> None:
        """Log one tool/API call. Fire-and-forget."""
        now         = _now_iso()
        inputs_san  = _redact_pii(json.dumps(tool_inputs  or {}))
        outputs_san = _redact_pii(json.dumps(tool_outputs or {}))
        row = {
            "tool_call_id":    str(uuid.uuid4()),
            "trace_id":        trace_id,
            "app_id":          self.app_id,
            "tool_name":       tool_name,
            "tool_inputs":     inputs_san,
            "tool_outputs":    outputs_san,
            "status":          status,
            "called_at":       now,
            "is_erasure_flag": "false",
            "created_at":      now,
            "schema_version":  self.schema_version,
            # optional
            "subject_ref":     subject_ref   or None,
            "error_message":   error_message or None,
            # numeric — omit if None
            "latency_ms":      str(round(latency_ms, 2)) if latency_ms is not None else None,
        }
        _fire(self._tbl("raw_logs.tool_calls_raw"), row)

    def register_customer(
        self,
        user_email:      str,
        full_name:       Optional[str] = None,
        user_country:    Optional[str] = None,
        user_state:      Optional[str] = None,
        consent_version: Optional[str] = "tnc_v2.1",
    ) -> None:
        """
        Register a customer in customer_pii on first login.
        Call from oauth_callback in app.py after Google login succeeds.
        Fire-and-forget — never blocks the login flow.

        Safe to call on EVERY login — not just first time. The table is
        append-only so repeat logins just add a new row with updated
        consent timestamp. Erasure deletes ALL rows for that subject_ref.

        PII is handled entirely inside this method — the caller never
        sees subject_id or subject_ref. Raw email is stored here by
        design (this IS the PII table — it's the only place email lives).
        """
        try:
            _, subject_ref = self._compute_refs(user_email)
            regulation = _determine_regulation(user_country or "", user_state or "")
            now = _now_iso()
            row = {
               
                "subject_ref":     subject_ref,
                "app_id":          self.app_id,
                "email":           user_email,
                "full_name":       full_name       or None,
                "user_country":    user_country    or None,
                "user_state":      user_state      or None,
                "regulation":      regulation,
                "consent_version": consent_version or "tnc_v2.1",
                "created_at":      now,
                "schema_version":  self.schema_version,
            }
            _fire(self._tbl("raw_logs.customer_pii"), row)
        except Exception as e:
            self._log_failure("register", "customer_pii", e)

    def _log_failure(self, trace_id: str, failed_table: str, error: Exception) -> None:
        """Log wrapper failures to logging_failures table. Never raises."""
        try:
            now = _now_iso()
            row = {
                "failure_id":    str(uuid.uuid4()),
                "app_id":        self.app_id,
                "trace_id":      trace_id      or None,
                "failed_table":  failed_table,
                "error_message": str(error)[:2000],
                "occurred_at":   now,
                "recovered":     "false",
                "created_at":    now,
                "schema_version":self.schema_version,
            }
            _fire(self._tbl("raw_logs.logging_failures"), row)
        except Exception:
            pass