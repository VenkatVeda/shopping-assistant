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
    from audit_wrapper import AuditWrapper, AuditTrailCallback
    from audit_wrapper import _current_trace_id, _current_subject_ref

    self.audit_wrapper  = AuditWrapper(catalog=os.getenv("AUDIT_CATALOG", "shopping_assistant"))
    self.audit_callback = AuditTrailCallback(self.audit_wrapper)

    # in _build_graph, after compile:
    app = workflow.compile(checkpointer=self.memory)
    if self.audit_callback:
        app = app.with_config({"callbacks": [self.audit_callback]})

    # in process_query, after the graph completes:
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
import time
import hmac as hmac_lib
import hashlib
import logging
import threading
import uuid
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Optional

try:
    from langchain_core.callbacks import BaseCallbackHandler
except ImportError:
    BaseCallbackHandler = object  # graceful fallback if langchain_core not installed

logger = logging.getLogger(__name__)

# ── shared ContextVars — set once per request in process_query ────────────
# AuditTrailCallback reads these so every auto-logged node row
# gets the same trace_id and subject_ref as the manual rows.
# Import these in workflow.py instead of redefining them there:
#   from audit_wrapper import _current_trace_id, _current_subject_ref
_current_trace_id:    ContextVar[str] = ContextVar('_current_trace_id',    default='')
_current_subject_ref: ContextVar[str] = ContextVar('_current_subject_ref', default='')

# (trace_id, node_name) -> node_execution_id of the node run in progress.
# AuditTrailCallback fills it when a node starts and clears it when the node ends;
# core/gateway_client.py reads it so a gateway_llm_call row can point at the step
# that made the call (parent_node_id). A plain dict, because ContextVars set inside
# a callback do not reliably reach the node's own thread.
_active_nodes: dict = {}

def get_active_node_id(trace_id: str, node_name: str) -> Optional[str]:
    """node_execution_id of the step named node_name that is running in this request, or None."""
    return _active_nodes.get((trace_id, node_name))

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
    if country == "AU":                   return "AUS"
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
            name = m.group(1)
            if name is None:
                return m.group(0)
            return m.group(0).replace(name, '[NAME]')
        result = re.sub(pattern, _rep, result, flags=re.IGNORECASE)
    return result

_UUID_RE = re.compile(
    r'\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b'
)

def _redact_pii_keep_ids(text: str) -> str:
    """
    _redact_pii, but UUIDs (trace ids, gateway request ids) are left intact.
    The phone / Aadhaar patterns match any 10-12 digit run, so a UUID whose last
    segment happens to be all digits would otherwise be replaced by [PHONE] or
    [AADHAAR] and silently lose its value as a join key.
    """
    if not text:
        return text
    shielded: list = []
    def _stash(m):
        shielded.append(m.group(0))
        return f"\x00UUID{len(shielded) - 1}\x00"
    result = _redact_pii(_UUID_RE.sub(_stash, text))
    for i, value in enumerate(shielded):
        result = result.replace(f"\x00UUID{i}\x00", value)
    return result

def _detect_pii_types(text: str) -> list:
    """Return the list of PII pattern labels found in text (for pii_types_found)."""
    if not text:
        return []
    return [label for label, pattern in _PII_PATTERNS if re.search(pattern, text)]

# ── low-level writer ───────────────────────────────────────────────────────
def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")

def _ts_to_iso(ts: float) -> str:
    """Epoch seconds -> the same UTC string format as _now_iso()."""
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")

def _guardrail_meta_json(meta) -> Optional[str]:
    """guardrail_metadata as masked JSON text (None when empty or not serialisable)."""
    if not meta:
        return None
    try:
        text = meta if isinstance(meta, str) else json.dumps(meta, default=str)
        return _redact_pii_keep_ids(text)[:4000]
    except Exception as e:
        logger.warning("[AUDIT] guardrail_metadata not serialisable: %s", e)
        return None

def _write_row(table: str, row: dict, on_failure=None) -> None:
    """
    Write one row to a Delta table via Databricks SQL Warehouse.
    Columns with None or empty string are omitted from the INSERT
    so Delta uses the column default (NULL) — this avoids CAST errors
    when trying to insert '' into DOUBLE or BIGINT columns.

    on_failure: optional callback(table, error_message) invoked when the
    write fails, so callers can route the failure into logging_failures.
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
            if on_failure:
                on_failure(table, result.status.error.message)
        else:
            logger.debug("[AUDIT] Row written to %s", table)

    except Exception as exc:
        logger.warning("[AUDIT] Failed to write to %s: %s", table, exc)
        if on_failure:
            on_failure(table, str(exc))


def _fire(table: str, row: dict, on_failure=None) -> None:
    """Fire-and-forget background write — never blocks the caller."""
    threading.Thread(target=_write_row, args=(table, row, on_failure), daemon=True).start()


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
                # Try primary scope first, then fallback to audit_trail_secrets
                secret = None
                scopes_to_try = [scope]
                if scope != "audit_trail_secrets":
                    scopes_to_try.append("audit_trail_secrets")
                last_error = None
                for try_scope in scopes_to_try:
                    try:
                        secret = w.secrets.get_secret(scope=try_scope, key=key_name)
                        scope = try_scope  # remember which worked
                        break
                    except Exception as se:
                        last_error = se
                if secret is None:
                    raise RuntimeError(f"Key not found in any scope: {last_error}")
                try:
                    raw = base64.b64decode(secret.value).decode("utf-8")
                except Exception:
                    raw = secret.value
                self._key_cache[self.app_id] = raw.encode()
            except RuntimeError:
                raise
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
            (value or "").encode(),
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
        node_metadata:  Optional[dict]  = None,
        started_at:     Optional[str]   = None,
        node_execution_id: Optional[str] = None,
    ) -> Optional[str]:
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
        node_metadata : dict of agent-level detail, stored as JSON in the
                        node_metadata column. Use these keys consistently
                        across every app so the column stays queryable:
                          agent_id       - stable id of the agent that ran this step
                          agent_role     - "researcher" / "recommender" / "broker"
                          handoff_from   - agent_id that handed control to this step
                          handoff_reason - why control moved
                          reasoning      - the model's stated reason for its decision
                        PII-redacted before write. Never put raw email or name here.

        started_at    : real start time of the step (same format as _now_iso()).
                        When omitted, the write time is used, which is only
                        correct for rows written the moment the step starts.
        node_execution_id: id to use for this row. When omitted a new one is generated.
                        AuditTrailCallback passes the id it handed out when the step
                        started, so rows written while the step ran (gateway calls) can
                        already point at it through parent_node_id.

        Returns the node_execution_id used, or None if logging failed.
        """
        try:
            node_execution_id = node_execution_id or str(uuid.uuid4())
            started    = started_at or _now_iso()
            input_san  = _redact_pii(str(node_input  or ""))
            output_san = _redact_pii(str(node_output or ""))

            # node_metadata → JSON string, PII-redacted.
            # default=str so non-serialisable values degrade instead of raising.
            meta_json = None
            if node_metadata:
                try:
                    meta_json = _redact_pii_keep_ids(json.dumps(node_metadata, default=str))
                except Exception as _me:
                    logger.warning("[AUDIT] node_metadata not serialisable: %s", _me)
                    meta_json = None

            ended = _now_iso()
            row = {
                "node_execution_id": node_execution_id,
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
                "subject_ref":       subject_ref    or None,
                "node_type":         node_type      or None,
                "parent_node_id":    parent_node_id or None,
                "model_name":        model_name     or None,
                "model_version":     model_version  or None,
                "latency_ms":        str(int(round(latency_ms))) if latency_ms is not None else None,
                "node_order":        str(node_order)  if node_order  is not None else None,
                "tokens_used":       str(tokens_used) if tokens_used is not None else None,
                "retry_count":       str(retry_count) if retry_count is not None else None,
                "node_metadata":     meta_json,
            }
            _fire(self._tbl("raw_logs.node_executions_raw"), row)
            return node_execution_id
        except Exception as e:
            self._log_failure(trace_id, "node_executions_raw", e)
            return None

    def log_interaction(
        self,
        user_email:             str,
        user_input:             str,
        model_output:           str,
        model_name:             str,
        status:                 str,
        trace_id:               Optional[str]   = None,
        session_id:             Optional[str]   = None,
        model_version:          Optional[str]   = None,
        user_country:           Optional[str]   = None,
        user_state:             Optional[str]   = None,
        system_prompt_version:  Optional[str]   = None,
        consent_version:        Optional[str]   = None,
        mlflow_trace_id:        Optional[str]   = None,
        latency_ms:             Optional[float] = None,
        final_state:            Optional[dict]  = None,
    ) -> dict:
        """
        Core method. Call once per AI interaction after the graph completes.
        Fire-and-forget — never blocks the request.
        user_email must be a valid non-empty string.
        user_country is the CURRENT/REQUEST country for this interaction
        (e.g. from get_country_from_request()). regulation_at_time is the
        regulation APPLICABLE TO THIS INTERACTION, recomputed from that
        country every call — it intentionally does NOT read the user's
        registered country/regulation in customer_pii (that stays a
        separate, frozen, account-level record — see register_customer()).
        trace_id should be the caller's pre-generated request trace_id — only a
        fallback UUID is minted when trace_id is None, so every audit row for
        one request shares the same trace_id.
        Writes to ai_interactions_raw. Model output is logged separately via
        log_model_output(), except for a request blocked by a gateway policy
        (final_state["blocked_by_policy"] set): the refusal text is then written
        to model_outputs_raw here, with output_type="blocked_refusal".
        Returns dict with trace_id and subject_ref for chaining.
        """
        trace_id = trace_id or str(uuid.uuid4())
        result   = {"trace_id": trace_id, "status": "ok"}

        try:
            # guard — user_email must be provided
            if not user_email:
                raise ValueError(
                    "user_email is required for log_interaction. "
                    "Pass the authenticated user's email."
                )

            _, sref    = self._compute_refs(user_email)
            regulation = _determine_regulation(user_country or "", user_state or "")

            input_san  = _redact_pii(user_input  or "")
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
                    "intent":            intent,
                    "result_count":      result_count,
                    "guardrail_status":  g_status,
                    "blocked_by_policy": (final_state or {}).get("blocked_by_policy"),
                    "blocked_phase":     (final_state or {}).get("blocked_phase"),
                    "guardrail_issues":  [
                        _redact_pii(str(i))[:200]
                        for i in ((final_state or {}).get("guardrail_issues") or [])[:10]
                    ],
                    # names and results only (detail stays in guardrail_results_raw) of the
                    # output checks that did not pass, when the app puts them in final_state
                    "failed_checks":     [
                        {"policy_name": c.get("policy_name") or c.get("name"), "result": c.get("result")}
                        for c in ((final_state or {}).get("guardrail_checks") or [])
                        if isinstance(c, dict) and c.get("result") not in (None, "pass", "skipped")
                    ][:10],
                    "mlflow_trace_id":   mlflow_trace_id,
                }),
                "created_at":            now,
                "schema_version":        self.schema_version,
                # optional — only include if provided
                "session_id":            session_id      or None,
                "user_country":          user_country    or None,
                "user_state":            user_state      or None,
                "model_version":         model_version   or None,
                "run_id":                mlflow_trace_id or None,
                "consent_version":       consent_version or None,
                # numeric columns — None means omit from INSERT (no CAST error)
                "confidence_score":      None,
                "latency_ms":            str(int(round(latency_ms))) if latency_ms is not None else None,
            }
            _fire(self._tbl("raw_logs.ai_interactions_raw"), int_row)

            # A request stopped by a gateway policy never reaches the response generator, so no
            # model output is logged for it. Keep what the shopper was actually shown, built from
            # the model_output and final_state the app already passes in.
            if (final_state or {}).get("blocked_by_policy"):
                self.log_model_output(
                    trace_id      = trace_id,
                    output_text   = model_output,
                    subject_ref   = sref,
                    output_type   = "blocked_refusal",
                    finish_reason = "content_filter",
                )

        except Exception as e:
            result["status"] = "logging_failed"
            result["error"]  = str(e)
            self._log_failure(trace_id, "ai_interactions_raw", e)

        return result

    def log_model_output(
        self,
        trace_id:           str,
        output_text:        str,
        recommended_items:  Optional[list] = None,
        subject_ref:        Optional[str]  = None,
        output_type:        str            = "recommendation",
        finish_reason:      str            = "stop",
        tokens_used:        Optional[int]  = None,
    ) -> None:
        """
        Log one generated model output to model_outputs_raw. Fire-and-forget.
        Call once from response_generator_node after the response text is produced.

        output_text is PII-redacted before storage; the raw text is HMAC-hashed
        (output_hash) for tamper evidence but never stored unredacted.
        recommended_items must contain product IDs only — never product names.
        tokens_used: optional total tokens of the call that produced this output;
        stored as NULL when not given.
        """
        try:
            now         = _now_iso()
            output_san  = _redact_pii(output_text or "")
            pii_types   = _detect_pii_types(output_text or "")
            row = {
                "output_id":             str(uuid.uuid4()),
                "trace_id":              trace_id,
                "app_id":                self.app_id,
                "subject_ref":           subject_ref or None,
                "output_text_sanitised": output_san,
                "output_hash":           self._hmac(output_text or ""),
                "output_type":           output_type,
                "finish_reason":         finish_reason,
                "recommended_items":     json.dumps(recommended_items or []),
                "contains_pii_flag":     "true" if pii_types else "false",
                "pii_types_found":       json.dumps(pii_types) if pii_types else None,
                "generated_at":          now,
                "is_erasure_flag":       "false",
                "created_at":            now,
                "schema_version":        self.schema_version,
                # numeric columns — omitted (None) to avoid CAST errors
                "confidence_score":      None,
                "tokens_used":           str(int(tokens_used)) if tokens_used is not None else None,
            }
            _fire(self._tbl("raw_logs.model_outputs_raw"), row)
        except Exception as e:
            self._log_failure(trace_id, "model_outputs_raw", e)

    def log_guardrail(
        self,
        trace_id:           str,
        policy_name:        str,
        score:              Optional[float],
        result:             str,
        triggered_block:    bool,
        subject_ref:        Optional[str]  = None,
        guardrail_metadata: Optional[dict] = None,
        checked_at:         Optional[str]  = None,
    ) -> None:
        """
        Log one guardrail check. Fire-and-forget.

        policy_name        : the real name of the check that ran (e.g. "query_length_limit"),
                             never a generic label.
        score              : pass None when the check has no real score. Do not invent 1.0 / 0.0.
        guardrail_metadata : optional dict with the detail of the decision (issues found, limits,
                             counts ...). Stored as JSON in guardrail_metadata, PII-masked first.
                             Never put raw user text in it.
        checked_at         : optional real check time (same format as _now_iso()); the write time
                             is used when omitted.
        """
        now = _now_iso()
        row = {
            "guardrail_id":    str(uuid.uuid4()),
            "trace_id":        trace_id,
            "app_id":          self.app_id,
            "policy_name":     policy_name,
            "result":          result,
            "triggered_block": str(triggered_block).lower(),
            "checked_at":      checked_at or now,
            "is_erasure_flag": "false",
            "created_at":      now,
            "schema_version":  self.schema_version,
            # optional
            "subject_ref":     subject_ref or None,
            "guardrail_metadata": _guardrail_meta_json(guardrail_metadata),
            # numeric — omit if None to avoid CAST errors
            "score":           str(round(score, 4)) if score is not None else None,
        }
        _fire(self._tbl("raw_logs.guardrail_results_raw"), row)

    def log_guardrail_checks(
        self,
        trace_id:    str,
        checks:      list,
        subject_ref: Optional[str] = None,
        node_name:   Optional[str] = None,
    ) -> None:
        """
        Log several guardrail checks in one call (one guardrail_results_raw row each).
        The wrapper only writes what the app hands it, so the app builds this list.

        checks: list of dicts, one per check that ran:
            policy_name     (or name) : real name of the check            -- required
            result                    : "pass" | "fail" | "warning" | "skipped"
                                        (default: "fail" if triggered_block else "pass")
            triggered_block           : True when the check stopped or replaced the answer (default False)
            score                     : optional real score, otherwise leave out (stored as NULL)
            metadata (or details)     : optional dict with the detail, e.g. {"issues": [...], "limit": 500}
            checked_at                : optional real check time
            node                      : optional name of the graph node that ran the check
        node_name: optional default for "node" when an entry has none, e.g. "output_guardrail".
            It is stored as "node" inside guardrail_metadata so a check can be tied to its step
            row in node_executions_raw (same trace_id, same node name).
        A malformed entry is skipped and never stops the others or the request.
        """
        for c in checks or []:
            try:
                name = c.get("policy_name") or c.get("name")
                if not name:
                    continue
                blocked = bool(c.get("triggered_block", False))
                meta = dict(c.get("metadata") or c.get("details") or {})
                node = c.get("node") or node_name
                if node:
                    meta.setdefault("node", node)
                self.log_guardrail(
                    trace_id           = trace_id,
                    policy_name        = name,
                    score              = c.get("score"),
                    result             = c.get("result") or ("fail" if blocked else "pass"),
                    triggered_block    = blocked,
                    subject_ref        = subject_ref,
                    guardrail_metadata = meta or None,
                    checked_at         = c.get("checked_at"),
                )
            except Exception as e:
                logger.warning("[AUDIT] Skipped one guardrail check entry: %s", e)

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
        try:
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
                "latency_ms":      str(int(round(latency_ms))) if latency_ms is not None else None,
            }
            _fire(
                self._tbl("raw_logs.tool_calls_raw"),
                row,
                on_failure=lambda tbl, err: self._log_failure(trace_id, tbl, Exception(err)),
            )
        except Exception as e:
            self._log_failure(trace_id, "tool_calls_raw", e)

    def register_customer(
        self,
        user_email:      str,
        full_name:       Optional[str] = None,
        user_country:    Optional[str] = None,
        user_state:      Optional[str] = None,
        consent_version: Optional[str] = "tnc_v2.1",
    ) -> None:
        """
        Register a customer in customer_pii on first login ONLY.
        Call from oauth_callback in app.py after Google login succeeds.
        Fire-and-forget — never blocks the login flow.

        Safe to call on EVERY login. If a row already exists for this
        subject_id, the call is a no-op — repeat logins do NOT create
        duplicate rows. Erasure deletes the row for that subject_ref.

        PII is handled entirely inside this method — the caller never
        sees subject_id or subject_ref. Raw email is stored here by
        design (this IS the PII table — it's the only place email lives).
        """
        try:
            subject_id, subject_ref = self._compute_refs(user_email)

            # ── check if this customer already exists ──────────────────────
            from databricks.sdk import WorkspaceClient
            from databricks.sdk.service.sql import StatementState

            warehouse_id = os.getenv("DATABRICKS_SQL_WAREHOUSE_ID")
            existing = 0
            if warehouse_id:
                w = WorkspaceClient()
                result = w.statement_execution.execute_statement(
                    warehouse_id = warehouse_id,
                    statement    = f"""
                        SELECT COUNT(*) AS c
                        FROM {self._tbl('raw_logs.customer_pii')}
                        WHERE subject_id = '{subject_id}'
                    """,
                    wait_timeout = "10s",
                )
                if result.status.state == StatementState.SUCCEEDED and result.result and result.result.data_array:
                    existing = int(result.result.data_array[0][0])

            if existing > 0:
                return  # already registered — skip, no duplicate row

            if not user_country:
                logger.warning(
                    "[AUDIT] Registering customer with unknown country (email redacted) — "
                    "regulation will default to INTERNAL_POLICY. subject_id=%s", subject_id
                )

            regulation = _determine_regulation(user_country or "", user_state or "")
            now = _now_iso()
            row = {
                "subject_id":      subject_id,
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
                "failure_id":     str(uuid.uuid4()),
                "app_id":         self.app_id,
                "trace_id":       trace_id     or None,
                "failed_table":   failed_table,
                "error_message":  str(error)[:2000],
                "occurred_at":    now,
                "recovered":      "false",
                "created_at":     now,
                "schema_version": self.schema_version,
            }
            _fire(self._tbl("raw_logs.logging_failures"), row)
        except Exception:
            pass


# ── AuditTrailCallback ────────────────────────────────────────────────────
# Separate top-level class — NOT inside AuditWrapper.
#
# Pass one instance to graph.compile().with_config() and every node,
# LLM call, and tool call logs itself automatically to Delta tables.
# Failures are handled inside AuditWrapper._log_failure() — not here.
#
# Usage in workflow.py __init__:
#     from audit_wrapper import AuditWrapper, AuditTrailCallback
#     self.audit_wrapper  = AuditWrapper(...)
#     self.audit_callback = AuditTrailCallback(self.audit_wrapper)
#
# Usage in workflow.py _build_graph:
#     app = workflow.compile(checkpointer=self.memory)
#     if self.audit_callback:
#         app = app.with_config({"callbacks": [self.audit_callback]})

class AuditTrailCallback(BaseCallbackHandler):
    """
    LangGraph callback adapter for AuditWrapper.
    Replaces all manual log_node_execution threading calls in workflow.py.
    Every node, LLM call, and tool call is captured automatically.
    Works with any LangGraph app — not just Shopping Assistant.
    """

    def __init__(self, audit_wrapper: "AuditWrapper"):
        self.audit_wrapper = audit_wrapper
        # key: run_id (UUID from LangGraph) → (start_time, node_name, is_node_run)
        # is_node_run is False for runs nested inside another run of the SAME node.
        self._runs: dict = {}
        # key: run_id of an outermost node run → (node_execution_id, (trace_id, node_name))
        self._node_ids: dict = {}

    def _finish_node(self, run_id: str):
        """Return the node_execution_id handed out at start and clear it from the registry."""
        nid, key = self._node_ids.pop(run_id, (None, None))
        if key is not None and _active_nodes.get(key) == nid:
            _active_nodes.pop(key, None)
        return nid

    def _extract_name(self, serialized, kwargs) -> str:
        """
        LangGraph 0.2.x passes the registered node name in
        kwargs["metadata"]["langgraph_node"] — not in run_name or serialized.
        """
        metadata = kwargs.get("metadata") or {}
        return (
            metadata.get("langgraph_node")          # ← LangGraph 0.2.x node name
            or kwargs.get("run_name")               # fallback: explicit run_name
            or (serialized.get("name") if serialized else None)
            or (serialized.get("id", ["unknown"])[-1] if serialized else None)
            or "unknown"
        )

    def on_chain_start(self, serialized, inputs, **kwargs):
        """
        Called by LangGraph BEFORE every run inside a node. Records start time.

        LangGraph tags every run INSIDE a node (the node wrapper, the routing
        function of a conditional edge) with the same `langgraph_node` name, so
        one node execution produces 2-3 callback events. Only the outermost run
        of a node is the node execution: a run whose parent is another run of
        the same node is nested and must not be logged again.
        """
        run_id    = str(kwargs.get("run_id", id(inputs)))
        node_name = self._extract_name(serialized, kwargs)
        parent_id = kwargs.get("parent_run_id")
        parent    = self._runs.get(str(parent_id)) if parent_id else None
        is_node_run = not (parent and parent[1] == node_name)
        self._runs[run_id] = (time.time(), node_name, is_node_run)
        if is_node_run and node_name and node_name != "unknown":
            # Hand out this step's row id now, so a gateway call made while the step runs
            # can already name its parent (see get_active_node_id).
            nid = str(uuid.uuid4())
            key = (_current_trace_id.get(), node_name)
            self._node_ids[run_id] = (nid, key)
            _active_nodes[key] = nid

    def on_chain_end(self, outputs, **kwargs):
        """Called by LangGraph AFTER every run inside a node completes."""
        run_id = str(kwargs.get("run_id", ""))
        t0, node_name, is_node_run = self._runs.pop(run_id, (time.time(), None, False))
        # skip graph runner / unnamed runs, and runs nested inside the same node
        if not node_name or node_name == "unknown" or not is_node_run:
            return
        # High-level view of the checks a guardrail node ran, when the node returns them in its
        # output (names and results only; the detail is in guardrail_results_raw, same trace_id).
        checks = outputs.get("guardrail_checks") if isinstance(outputs, dict) else None
        checks_meta = None
        if isinstance(checks, list) and checks:
            checks_meta = {"checks": [
                {"policy_name": c.get("policy_name") or c.get("name"), "result": c.get("result")}
                for c in checks if isinstance(c, dict)][:20]}
        self.audit_wrapper.log_node_execution(
            trace_id    = _current_trace_id.get(),
            node_name   = node_name,
            status      = "success",
            node_output = str(outputs)[:500],
            latency_ms  = (time.time() - t0) * 1000,
            subject_ref = _current_subject_ref.get() or None,
            node_metadata = checks_meta,
            started_at  = _ts_to_iso(t0),
            node_execution_id = self._finish_node(run_id),
        )

    def on_chain_error(self, error, **kwargs):
        """Called by LangGraph when a node raises an exception."""
        run_id = str(kwargs.get("run_id", ""))
        t0, node_name, is_node_run = self._runs.pop(run_id, (time.time(), None, False))
        # skip graph runner / unnamed runs, and runs nested inside the same node
        if not node_name or node_name == "unknown" or not is_node_run:
            return
        # A gateway policy block is a decision, not a failure of the step: log it as "blocked"
        # (the same status the gateway_llm_call row already gets) with the policy name and phase.
        blocked = self._is_policy_block(error)
        self.audit_wrapper.log_node_execution(
            trace_id      = _current_trace_id.get(),
            node_name     = node_name,
            status        = "blocked" if blocked else "error",
            error_msg     = str(error)[:500],
            latency_ms    = (time.time() - t0) * 1000,
            subject_ref   = _current_subject_ref.get() or None,
            node_metadata = ({"blocked_by_policy": getattr(error, "policy", None),
                              "blocked_phase":     getattr(error, "phase", None)} if blocked else None),
            started_at    = _ts_to_iso(t0),
            node_execution_id = self._finish_node(run_id),
        )

    @staticmethod
    def _is_policy_block(error) -> bool:
        """True for core.gateway_client.GatewayPolicyBlock, matched by class name so this
        module does not have to import the app's gateway client."""
        return any(c.__name__ == "GatewayPolicyBlock" for c in type(error).__mro__)

    def on_tool_end(self, output, **kwargs):
        """Called by LangGraph after every tool call completes."""
        self.audit_wrapper.log_tool_call(
            trace_id     = _current_trace_id.get(),
            tool_name    = kwargs.get("run_name") or "unknown_tool",
            tool_outputs = {"result": str(output)[:500]},
            status       = "success",
            subject_ref  = _current_subject_ref.get() or None,
        )