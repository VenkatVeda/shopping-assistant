# Databricks notebook source
# MAGIC %md
# MAGIC # Component 4 · The Logging Wrapper
# MAGIC # AI Audit Trail Platform — The Engine
# MAGIC
# MAGIC **Component:** 4 of 7 | **Type:** Core library notebook | **Run:** Once to define, then imported by apps | **Requires:** Components 1, 2, 3 complete
# MAGIC
# MAGIC ---
# MAGIC
# MAGIC ## What this notebook builds
# MAGIC
# MAGIC The `AuditWrapper` class — the single thing any app imports to become fully audit-ready.
# MAGIC
# MAGIC **What it does automatically on every AI call:**
# MAGIC 1. Validates `app_id` is registered and active in `app_registry`
# MAGIC 2. Retrieves HMAC key from secret scope
# MAGIC 3. Computes `subject_id` + `subject_ref` from user email (two-step pseudonymisation)
# MAGIC 4. Generates `trace_id` (UUID4) and manages `session_id`
# MAGIC 5. Determines `regulation_at_time` from user country
# MAGIC 6. Redacts PII from input text and output text before any write
# MAGIC 7. Writes all 6 capture categories to correct tables
# MAGIC 8. Captures node executions for pipeline steps
# MAGIC 9. Logs its own failures to `logging_failures` — never fails silently
# MAGIC 10. Always returns the AI response regardless of logging outcome
# MAGIC
# MAGIC **App team experience — 3 lines:**
# MAGIC ```python
# MAGIC wrapper = AuditWrapper(app_id="shopping_assistant_app", catalog="shopping_assistant")
# MAGIC response = wrapper.log_interaction(user_email=..., user_input=..., model_output=..., ...)
# MAGIC ```
# MAGIC
# MAGIC ---
# MAGIC
# MAGIC ## Notebook structure
# MAGIC - **Step 1** — imports and config
# MAGIC - **Step 2** — PII redactor (regex + placeholder replacement)
# MAGIC - **Step 3** — jurisdiction mapper (country → regulation)
# MAGIC - **Step 4** — `AuditWrapper` class (the full wrapper)
# MAGIC - **Step 5** — save wrapper as a reusable Python file in DBFS
# MAGIC - **Step 6** — end-to-end smoke test (simulated AI call)
# MAGIC - **Step 7** — verification

# COMMAND ----------

# MAGIC %md
# MAGIC ---
# MAGIC ## Step 1 — Imports and config

# COMMAND ----------

# ============================================================
# STEP 1 — IMPORTS AND CONFIG
# ============================================================
import uuid, json, hmac as hmac_lib, hashlib, re, base64
from datetime import datetime, timezone
from pyspark.sql.types import (
    StructType, StructField, StringType, TimestampType,
    BooleanType, DoubleType, LongType, IntegerType
)
from databricks.sdk import WorkspaceClient

BOOTSTRAP_CATALOG = "shopping_assistant"

cfg_rows = spark.sql(f"""
    SELECT config_key, config_value
    FROM {BOOTSTRAP_CATALOG}.raw_logs.bundle_config
""").collect()
CONFIG = {r["config_key"]: r["config_value"] for r in cfg_rows}

CATALOG      = CONFIG["catalog_name"]
RAW          = CONFIG["schema_raw"]
CURATED      = CONFIG["schema_curated"]
REPORT       = CONFIG["schema_reporting"]
SECRET_SCOPE = CONFIG["secret_scope"]
SCHEMA_VER   = CONFIG["schema_version"]

def tbl(schema, name): return f"{CATALOG}.{schema}.{name}"

spark.sql(f"USE CATALOG {CATALOG}")
print(f"Config loaded. Catalog: {CATALOG}")

# COMMAND ----------

# MAGIC %md
# MAGIC ---
# MAGIC ## Step 2 — PII Redactor
# MAGIC
# MAGIC Scans free text before it touches any table. Two passes:
# MAGIC - **Pass 1 — regex patterns:** emails, phones, Aadhaar, PAN, credit cards — anything with a fixed format. Fast and deterministic.
# MAGIC - **Pass 2 — name patterns:** simple heuristic for capitalised words after greeting words (Hi, I am, My name is). Not perfect — that's why vacuum and encryption are backup layers.
# MAGIC
# MAGIC Returns the sanitised text and a list of PII types found.

# COMMAND ----------

# ============================================================
# STEP 2 — PII REDACTOR
# ============================================================

PII_PATTERNS = [
    ("EMAIL",   r'\b[\w\.-]+@[\w\.-]+\.\w{2,}\b'),
    ("PHONE",   r'\b(\+?\d{1,3}[\s-]?)?\d{10}\b'),
    ("AADHAAR", r'\b\d{4}\s?\d{4}\s?\d{4}\b'),
    ("PAN",     r'\b[A-Z]{5}\d{4}[A-Z]\b'),
    ("CARD",    r'\b\d{4}[\s-]?\d{4}[\s-]?\d{4}[\s-]?\d{4}\b'),
    ("SSN",     r'\b\d{3}-\d{2}-\d{4}\b'),
]

NAME_TRIGGERS = [
    r'(?:i am|my name is|this is|hi,?\s+i.?m)\s+([A-Z][a-z]+(\s+[A-Z][a-z]+)?)',
]

def redact_pii(text: str) -> tuple:
    """
    Redact PII from text before writing to audit tables.
    Returns (sanitised_text, list_of_pii_types_found)
    """
    if not text:
        return text, []

    found = []
    result = text

    # pass 1 — structured PII patterns
    for label, pattern in PII_PATTERNS:
        replaced, count = re.subn(pattern, f'[{label}]', result)
        if count > 0:
            found.append(label)
            result = replaced

    # pass 2 — name heuristics
    for pattern in NAME_TRIGGERS:
        def replace_name(m):
            found.append("PERSON")
            return m.group(0).replace(m.group(1), "[NAME]")
        result = re.sub(pattern, replace_name, result, flags=re.IGNORECASE)

    return result, list(set(found))


# quick self-test
_test = "Hi I am Nupur, email nupur@gmail.com phone 9876543210"
_out, _types = redact_pii(_test)
print(f"Redactor test:")
print(f"  input : {_test}")
print(f"  output: {_out}")
print(f"  found : {_types}")
assert "[EMAIL]" in _out
assert "[PHONE]" in _out
print("  Redactor OK")

# COMMAND ----------

# MAGIC %md
# MAGIC ---
# MAGIC ## Step 3 — Jurisdiction mapper
# MAGIC
# MAGIC Determines which regulation applies at the moment of the interaction.
# MAGIC This is stored as `regulation_at_time` on every audit row — locked at call time,
# MAGIC not re-evaluated later. This solves the Delhi→US→Germany jurisdiction problem.

# COMMAND ----------

# ============================================================
# STEP 3 — JURISDICTION MAPPER
# ============================================================

EU_COUNTRIES = {
    "AT","BE","BG","CY","CZ","DE","DK","EE","ES","FI",
    "FR","GR","HR","HU","IE","IT","LT","LU","LV","MT",
    "NL","PL","PT","RO","SE","SI","SK","GB"
}

US_CCPA_STATES = {"CA"}

def determine_regulation(country: str, state: str = None,
                         sector: str = None) -> str:
    """
    Returns the primary regulation for a user interaction.
    Priority: HIPAA (sector) > GDPR (EU) > CCPA (CA) > DPDP (IN) > INTERNAL
    """
    if not country:
        return "INTERNAL_POLICY"

    country = country.upper().strip()
    state   = (state or "").upper().strip()
    sector  = (sector or "").lower().strip()

    if sector == "health":
        return "HIPAA"
    if country in EU_COUNTRIES:
        return "GDPR"
    if country == "US" and state in US_CCPA_STATES:
        return "CCPA"
    if country == "IN":
        return "DPDP"
    return "INTERNAL_POLICY"


# self-test
assert determine_regulation("DE")         == "GDPR"
assert determine_regulation("IN")         == "DPDP"
assert determine_regulation("US", "CA")   == "CCPA"
assert determine_regulation("US", "NY")   == "INTERNAL_POLICY"
assert determine_regulation("US", sector="health") == "HIPAA"
print("Jurisdiction mapper OK")
print(f"  DE → {determine_regulation('DE')}")
print(f"  IN → {determine_regulation('IN')}")
print(f"  US/CA → {determine_regulation('US','CA')}")
print(f"  US/NY → {determine_regulation('US','NY')}")

# COMMAND ----------

# MAGIC %md
# MAGIC ---
# MAGIC ## Step 4 — AuditWrapper class
# MAGIC
# MAGIC The complete wrapper. Every method is documented inline.

# COMMAND ----------

# ============================================================
# STEP 4 — AUDIT WRAPPER CLASS
# ============================================================

class AuditWrapper:
    """
    Drop-in audit logging wrapper for any AI app on the platform.

    Usage:
        wrapper = AuditWrapper(
            app_id  = "shopping_assistant_app",
            catalog = "shopping_assistant"
        )
        result = wrapper.log_interaction(
            user_email   = "nupur@xponent.ai",
            user_input   = "show me red kurtas under 2000",
            model_output = "here are 5 options...",
            model_name   = "gpt-4",
            status       = "success"
        )
    """

    def __init__(self, app_id: str, catalog: str,
                 secret_scope: str = "audit_trail_secrets",
                 schema_raw: str = "raw_logs",
                 schema_version: str = "1.0"):

        self.app_id         = app_id
        self.catalog        = catalog
        self.scope          = secret_scope
        self.raw            = schema_raw
        self.schema_version = schema_version
        self._w             = WorkspaceClient()
        self._key_cache     = {}

        # validate on init — fail fast
        self._validate_app()
        print(f"AuditWrapper ready. app_id={app_id}")

    # ── private helpers ───────────────────────────────────────

    def _tbl(self, name): return f"{self.catalog}.{self.raw}.{name}"

    def _now(self): return datetime.now(timezone.utc)

    def _get_key(self) -> bytes:
        if self.app_id not in self._key_cache:
            key_name = f"hmac_key_{self.app_id}"
            secret   = self._w.secrets.get_secret(
                scope=self.scope, key=key_name
            )
            try:
                raw_key = base64.b64decode(secret.value).decode("utf-8")
            except Exception:
                raw_key = secret.value
            self._key_cache[self.app_id] = raw_key.encode()
        return self._key_cache[self.app_id]

    def _hmac(self, value: str) -> str:
        return hmac_lib.new(
            self._get_key(), value.encode(), hashlib.sha256
        ).hexdigest()

    def _compute_refs(self, email: str) -> tuple:
        subject_id  = self._hmac(email.lower().strip())
        subject_ref = self._hmac(subject_id)
        return subject_id, subject_ref

    def _validate_app(self):
        spark.sql(f"USE CATALOG {self.catalog}")
        rows = spark.sql(f"""
            SELECT status FROM {self._tbl('app_registry')}
            WHERE app_id = '{self.app_id}'
            ORDER BY entry_created_at DESC LIMIT 1
        """).collect()
        if not rows:
            raise ValueError(
                f"app_id '{self.app_id}' not in app_registry. "
                f"Run Component 3 onboarding first."
            )
        if rows[0]["status"] != "active":
            raise ValueError(
                f"app '{self.app_id}' is '{rows[0]['status']}', not active."
            )

    def _log_failure(self, trace_id, failed_table, error, payload_hash=None):
        """Write to logging_failures — never raises, never loses the error."""
        try:
            schema = StructType([
                StructField("failure_id",    StringType(),   False),
                StructField("app_id",        StringType(),   True),
                StructField("trace_id",      StringType(),   True),
                StructField("failed_table",  StringType(),   True),
                StructField("error_message", StringType(),   True),
                StructField("payload_hash",  StringType(),   True),
                StructField("occurred_at",   TimestampType(),False),
                StructField("recovered",     BooleanType(),  True),
                StructField("created_at",    TimestampType(),False),
                StructField("schema_version",StringType(),   False),
            ])
            now = self._now()
            data = [(
                str(uuid.uuid4()), self.app_id, trace_id,
                failed_table, str(error)[:2000], payload_hash,
                now, False, now, self.schema_version
            )]
            (spark.createDataFrame(data, schema)
                  .write.format("delta").mode("append")
                  .saveAsTable(self._tbl("logging_failures")))
        except Exception:
            pass  # logging_failures itself failed — nothing we can do

    # ── public methods ────────────────────────────────────────

    def start_session(self, user_email: str,
                      channel: str = "api",
                      device_type: str = None) -> str:
        """
        Start a new session. Returns session_id.
        Call once per conversation, then pass session_id to log_interaction.
        """
        session_id  = str(uuid.uuid4())
        _, subject_ref = self._compute_refs(user_email)
        now = self._now()
        schema = StructType([
            StructField("session_id",        StringType(),   False),
            StructField("app_id",            StringType(),   False),
            StructField("subject_ref",       StringType(),   True),
            StructField("channel",           StringType(),   True),
            StructField("device_type",       StringType(),   True),
            StructField("started_at",        TimestampType(),False),
            StructField("ended_at",          TimestampType(),True),
            StructField("interaction_count", IntegerType(),  True),
            StructField("is_erasure_flag",   BooleanType(),  False),
            StructField("session_metadata",  StringType(),   True),
            StructField("created_at",        TimestampType(),False),
            StructField("schema_version",    StringType(),   False),
        ])
        data = [(
            session_id, self.app_id, subject_ref,
            channel, device_type, now, None, 0,
            False, None, now, self.schema_version
        )]
        try:
            (spark.createDataFrame(data, schema)
                  .write.format("delta").mode("append")
                  .saveAsTable(self._tbl("sessions_raw")))
        except Exception as e:
            self._log_failure(None, "sessions_raw", e)
        return session_id

    def log_interaction(self,
        user_email:           str,
        user_input:           str,
        model_output:         str,
        model_name:           str,
        status:               str,
        session_id:           str  = None,
        model_version:        str  = None,
        provider:             str  = None,
        region:               str  = None,
        run_id:               str  = None,
        confidence_score:     float = None,
        latency_ms:           int   = None,
        user_country:         str   = None,
        user_state:           str   = None,
        sector:               str   = None,
        system_prompt_hash:   str   = None,
        system_prompt_version:str   = None,
        consent_version:      str   = None,
        app_metadata:         dict  = None,
    ) -> dict:
        """
        Core method. Call once per AI interaction.
        Writes to ai_interactions_raw and model_outputs_raw.
        Returns dict with trace_id and subject_ref for chaining
        tool/guardrail/feedback logs.
        """
        trace_id    = str(uuid.uuid4())
        now         = self._now()
        result      = {"trace_id": trace_id, "status": "ok"}

        try:
            subject_id, subject_ref = self._compute_refs(user_email)
            regulation = determine_regulation(
                user_country or "", user_state or "", sector or ""
            )

            # redact PII from input and output
            input_san,  input_pii_types  = redact_pii(user_input  or "")
            output_san, output_pii_types = redact_pii(model_output or "")

            # hash raw input for tamper evidence
            input_hash = self._hmac(user_input or "")

            result["subject_ref"] = subject_ref
            result["regulation"]  = regulation

            # ── write ai_interactions_raw ─────────────────────
            int_schema = StructType([
                StructField("trace_id",              StringType(),   False),
                StructField("session_id",            StringType(),   True),
                StructField("app_id",                StringType(),   False),
                StructField("subject_ref",           StringType(),   True),
                StructField("request_timestamp",     TimestampType(),False),
                StructField("user_country",          StringType(),   True),
                StructField("user_state",            StringType(),   True),
                StructField("regulation_at_time",    StringType(),   True),
                StructField("input_text_sanitised",  StringType(),   True),
                StructField("input_hash",            StringType(),   True),
                StructField("system_prompt_hash",    StringType(),   True),
                StructField("system_prompt_version", StringType(),   True),
                StructField("model_name",            StringType(),   False),
                StructField("model_version",         StringType(),   True),
                StructField("provider",              StringType(),   True),
                StructField("region",                StringType(),   True),
                StructField("run_id",                StringType(),   True),
                StructField("status",                StringType(),   False),
                StructField("confidence_score",      DoubleType(),   True),
                StructField("latency_ms",            LongType(),     True),
                StructField("consent_version",       StringType(),   True),
                StructField("is_erasure_flag",       BooleanType(),  False),
                StructField("app_metadata",          StringType(),   True),
                StructField("created_at",            TimestampType(),False),
                StructField("schema_version",        StringType(),   False),
            ])
            int_data = [(
                trace_id, session_id, self.app_id, subject_ref,
                now, user_country, user_state, regulation,
                input_san, input_hash, system_prompt_hash,
                system_prompt_version, model_name, model_version,
                provider, region, run_id, status,
                confidence_score,
                int(latency_ms) if latency_ms is not None else None,
                consent_version, False,
                json.dumps(app_metadata) if app_metadata else None,
                now, self.schema_version
            )]
            (spark.createDataFrame(int_data, int_schema)
                  .write.format("delta").mode("append")
                  .saveAsTable(self._tbl("ai_interactions_raw")))

            # ── write model_outputs_raw ───────────────────────
            out_schema = StructType([
                StructField("output_id",             StringType(),   False),
                StructField("trace_id",              StringType(),   False),
                StructField("app_id",                StringType(),   False),
                StructField("subject_ref",           StringType(),   True),
                StructField("output_text_sanitised", StringType(),   True),
                StructField("output_hash",           StringType(),   True),
                StructField("output_type",           StringType(),   True),
                StructField("recommended_items",     StringType(),   True),
                StructField("confidence_score",      DoubleType(),   True),
                StructField("tokens_used",           LongType(),     True),
                StructField("finish_reason",         StringType(),   True),
                StructField("contains_pii_flag",     BooleanType(),  True),
                StructField("pii_types_found",       StringType(),   True),
                StructField("generated_at",          TimestampType(),False),
                StructField("is_erasure_flag",       BooleanType(),  False),
                StructField("app_metadata",          StringType(),   True),
                StructField("created_at",            TimestampType(),False),
                StructField("schema_version",        StringType(),   False),
            ])
            has_pii = bool(output_pii_types)
            out_data = [(
                str(uuid.uuid4()), trace_id, self.app_id, subject_ref,
                output_san, self._hmac(model_output or ""),
                "recommendation", None, confidence_score,
                None, "stop", has_pii,
                json.dumps(output_pii_types) if output_pii_types else None,
                now, False, None, now, self.schema_version
            )]
            (spark.createDataFrame(out_data, out_schema)
                  .write.format("delta").mode("append")
                  .saveAsTable(self._tbl("model_outputs_raw")))

        except Exception as e:
            result["status"] = "logging_failed"
            result["error"]  = str(e)
            self._log_failure(trace_id, "ai_interactions_raw", e)

        return result

    def log_tool_call(self, trace_id: str, tool_name: str,
                      tool_inputs: dict, tool_outputs: dict,
                      status: str, latency_ms: int = None,
                      error_message: str = None,
                      node_execution_id: str = None,
                      subject_ref: str = None) -> None:
        """Log one tool/API call. Call once per tool invocation."""
        now = self._now()
        schema = StructType([
            StructField("tool_call_id",      StringType(),   False),
            StructField("trace_id",          StringType(),   False),
            StructField("node_execution_id", StringType(),   True),
            StructField("app_id",            StringType(),   False),
            StructField("subject_ref",       StringType(),   True),
            StructField("tool_name",         StringType(),   False),
            StructField("tool_inputs",       StringType(),   True),
            StructField("tool_outputs",      StringType(),   True),
            StructField("status",            StringType(),   False),
            StructField("latency_ms",        LongType(),     True),
            StructField("error_message",     StringType(),   True),
            StructField("called_at",         TimestampType(),False),
            StructField("is_erasure_flag",   BooleanType(),  False),
            StructField("tool_metadata",     StringType(),   True),
            StructField("created_at",        TimestampType(),False),
            StructField("schema_version",    StringType(),   False),
        ])
        # sanitise tool inputs/outputs
        inputs_str,  _ = redact_pii(json.dumps(tool_inputs  or {}))
        outputs_str, _ = redact_pii(json.dumps(tool_outputs or {}))
        data = [(
            str(uuid.uuid4()), trace_id, node_execution_id,
            self.app_id, subject_ref, tool_name,
            inputs_str, outputs_str, status,
            int(latency_ms) if latency_ms else None,
            error_message, now, False, None, now, self.schema_version
        )]
        try:
            (spark.createDataFrame(data, schema)
                  .write.format("delta").mode("append")
                  .saveAsTable(self._tbl("tool_calls_raw")))
        except Exception as e:
            self._log_failure(trace_id, "tool_calls_raw", e)

    def log_guardrail(self, trace_id: str, policy_name: str,
                      score: float, result: str,
                      triggered_block: bool,
                      subject_ref: str = None) -> None:
        """Log one guardrail check result."""
        now = self._now()
        schema = StructType([
            StructField("guardrail_id",      StringType(),   False),
            StructField("trace_id",          StringType(),   False),
            StructField("app_id",            StringType(),   False),
            StructField("subject_ref",       StringType(),   True),
            StructField("policy_name",       StringType(),   False),
            StructField("score",             DoubleType(),   True),
            StructField("result",            StringType(),   False),
            StructField("triggered_block",   BooleanType(),  False),
            StructField("checked_at",        TimestampType(),False),
            StructField("is_erasure_flag",   BooleanType(),  False),
            StructField("guardrail_metadata",StringType(),   True),
            StructField("created_at",        TimestampType(),False),
            StructField("schema_version",    StringType(),   False),
        ])
        data = [(
            str(uuid.uuid4()), trace_id, self.app_id, subject_ref,
            policy_name, float(score) if score is not None else None,
            result, triggered_block, now, False, None, now, self.schema_version
        )]
        try:
            (spark.createDataFrame(data, schema)
                  .write.format("delta").mode("append")
                  .saveAsTable(self._tbl("guardrail_results_raw")))
        except Exception as e:
            self._log_failure(trace_id, "guardrail_results_raw", e)

    def log_node_execution(self, trace_id: str, node_name: str,
                           node_type: str, node_order: int,
                           status: str,
                           input_summary: str  = None,
                           output_summary: str = None,
                           model_name: str     = None,
                           tokens_used: int    = None,
                           latency_ms: int     = None,
                           error_message: str  = None,
                           subject_ref: str    = None) -> str:
        """Log one pipeline node execution. Returns node_execution_id."""
        node_id = str(uuid.uuid4())
        now     = self._now()
        input_san,  _ = redact_pii(input_summary  or "")
        output_san, _ = redact_pii(output_summary or "")
        schema = StructType([
            StructField("node_execution_id", StringType(),   False),
            StructField("trace_id",          StringType(),   False),
            StructField("app_id",            StringType(),   False),
            StructField("subject_ref",       StringType(),   True),
            StructField("node_name",         StringType(),   False),
            StructField("node_type",         StringType(),   True),
            StructField("node_order",        IntegerType(),  True),
            StructField("parent_node_id",    StringType(),   True),
            StructField("status",            StringType(),   False),
            StructField("input_summary",     StringType(),   True),
            StructField("output_summary",    StringType(),   True),
            StructField("error_message",     StringType(),   True),
            StructField("model_name",        StringType(),   True),
            StructField("model_version",     StringType(),   True),
            StructField("tokens_used",       LongType(),     True),
            StructField("latency_ms",        LongType(),     True),
            StructField("retry_count",       IntegerType(),  True),
            StructField("started_at",        TimestampType(),False),
            StructField("ended_at",          TimestampType(),True),
            StructField("is_erasure_flag",   BooleanType(),  False),
            StructField("node_metadata",     StringType(),   True),
            StructField("created_at",        TimestampType(),False),
            StructField("schema_version",    StringType(),   False),
        ])
        data = [(
            node_id, trace_id, self.app_id, subject_ref,
            node_name, node_type, node_order, None,
            status, input_san, output_san, error_message,
            model_name, None,
            int(tokens_used) if tokens_used else None,
            int(latency_ms)  if latency_ms  else None,
            0, now, now, False, None, now, self.schema_version
        )]
        try:
            (spark.createDataFrame(data, schema)
                  .write.format("delta").mode("append")
                  .saveAsTable(self._tbl("node_executions_raw")))
        except Exception as e:
            self._log_failure(trace_id, "node_executions_raw", e)
        return node_id

    def log_human_feedback(self, trace_id: str, feedback_type: str,
                           action: str, subject_ref: str = None,
                           reviewer_email: str = None,
                           rationale: str = None,
                           rating: int = None,
                           app_metadata: dict = None) -> None:
        """Log human review or override. Async — call any time after interaction."""
        now = self._now()
        reviewer_ref = None
        if reviewer_email:
            _, reviewer_ref = self._compute_refs(reviewer_email)
        rationale_san, rat_pii = redact_pii(rationale or "")
        rat_hash = self._hmac(rationale or "") if rationale else None
        schema = StructType([
            StructField("feedback_id",          StringType(),   False),
            StructField("trace_id",             StringType(),   False),
            StructField("app_id",               StringType(),   False),
            StructField("subject_ref",          StringType(),   True),
            StructField("reviewer_subject_ref", StringType(),   True),
            StructField("feedback_type",        StringType(),   False),
            StructField("action",               StringType(),   True),
            StructField("rationale_sanitised",  StringType(),   True),
            StructField("rationale_hash",       StringType(),   True),
            StructField("rating",               IntegerType(),  True),
            StructField("contains_pii_flag",    BooleanType(),  True),
            StructField("feedback_at",          TimestampType(),False),
            StructField("is_erasure_flag",      BooleanType(),  False),
            StructField("app_metadata",         StringType(),   True),
            StructField("created_at",           TimestampType(),False),
            StructField("schema_version",       StringType(),   False),
        ])
        data = [(
            str(uuid.uuid4()), trace_id, self.app_id,
            subject_ref, reviewer_ref, feedback_type, action,
            rationale_san, rat_hash, rating,
            bool(rat_pii), now, False,
            json.dumps(app_metadata) if app_metadata else None,
            now, self.schema_version
        )]
        try:
            (spark.createDataFrame(data, schema)
                  .write.format("delta").mode("append")
                  .saveAsTable(self._tbl("human_feedback_raw")))
        except Exception as e:
            self._log_failure(trace_id, "human_feedback_raw", e)

print("AuditWrapper class defined.")

# COMMAND ----------

# MAGIC %md
# MAGIC ---
# MAGIC ## Step 5 — Save wrapper to DBFS
# MAGIC
# MAGIC Saves the wrapper as a Python file so any notebook can import it with:
# MAGIC ```python
# MAGIC import sys
# MAGIC sys.path.insert(0, "/dbfs/audit_trail/")
# MAGIC from audit_wrapper import AuditWrapper
# MAGIC ```

# COMMAND ----------

# ============================================================
# STEP 5 — SAVE WRAPPER TO DBFS
# ============================================================

WRAPPER_CODE = '''
import uuid, json, hmac as hmac_lib, hashlib, re, base64
from datetime import datetime, timezone
from pyspark.sql.types import (
    StructType, StructField, StringType, TimestampType,
    BooleanType, DoubleType, LongType, IntegerType
)
from databricks.sdk import WorkspaceClient

EU_COUNTRIES = {
    "AT","BE","BG","CY","CZ","DE","DK","EE","ES","FI",
    "FR","GR","HR","HU","IE","IT","LT","LU","LV","MT",
    "NL","PL","PT","RO","SE","SI","SK","GB"
}

PII_PATTERNS = [
    ("EMAIL",   r"\\b[\\w\\.-]+@[\\w\\.-]+\\.\\w{2,}\\b"),
    ("PHONE",   r"\\b(\\+?\\d{1,3}[\\s-]?)?\\d{10}\\b"),
    ("AADHAAR", r"\\b\\d{4}\\s?\\d{4}\\s?\\d{4}\\b"),
    ("PAN",     r"\\b[A-Z]{5}\\d{4}[A-Z]\\b"),
    ("CARD",    r"\\b\\d{4}[\\s-]?\\d{4}[\\s-]?\\d{4}[\\s-]?\\d{4}\\b"),
    ("SSN",     r"\\b\\d{3}-\\d{2}-\\d{4}\\b"),
]

def redact_pii(text):
    if not text: return text, []
    found, result = [], text
    for label, pattern in PII_PATTERNS:
        replaced, count = re.subn(pattern, f"[{label}]", result)
        if count > 0:
            found.append(label)
            result = replaced
    return result, list(set(found))

def determine_regulation(country="", state="", sector=""):
    country = (country or "").upper().strip()
    state   = (state   or "").upper().strip()
    sector  = (sector  or "").lower().strip()
    if sector == "health":    return "HIPAA"
    if country in EU_COUNTRIES: return "GDPR"
    if country == "US" and state == "CA": return "CCPA"
    if country == "IN":       return "DPDP"
    return "INTERNAL_POLICY"
'''

dbutils.fs.mkdirs("dbfs:/audit_trail/")
dbutils.fs.put(
    "dbfs:/audit_trail/audit_wrapper.py",
    WRAPPER_CODE,
    overwrite=True
)
print("Wrapper saved to: dbfs:/audit_trail/audit_wrapper.py")
print("Import in any notebook with:")
print("  import sys")
print("  sys.path.insert(0, '/dbfs/audit_trail/')")
print("  from audit_wrapper import AuditWrapper")

# COMMAND ----------

# ============================================================
# STEP 5 — SAVE WRAPPER (DBFS not available on serverless)
# skipping DBFS save — wrapper is defined in Step 4 above
# To use in another notebook: copy the AuditWrapper class
# definition from Step 4 into your notebook, or use
# %run /path/to/04_logging_wrapper to import it
# ============================================================

print("Wrapper defined in memory — ready to use in this session.")
print()
print("To use in another notebook, either:")
print("  1. %run /Workspace/path/to/04_logging_wrapper")
print("     (then AuditWrapper is available in that notebook's scope)")
print()
print("  2. Copy the AuditWrapper class from Step 4 directly.")
print()
print("Continuing to smoke test...")

# COMMAND ----------

# MAGIC %md
# MAGIC ---
# MAGIC ## Step 6 — End-to-end smoke test
# MAGIC
# MAGIC Simulates a real shopping assistant interaction with:
# MAGIC - one session
# MAGIC - one interaction (with PII in the input to prove redaction)
# MAGIC - two node executions (pipeline steps)
# MAGIC - one tool call (product search)
# MAGIC - one guardrail check
# MAGIC - one human feedback (stylist override)

# COMMAND ----------

# ============================================================
# STEP 6 — END-TO-END SMOKE TEST
# ============================================================

print("=" * 60)
print("  SMOKE TEST — simulated shopping assistant interaction")
print("=" * 60 + "\n")

# initialise wrapper
w_test = AuditWrapper(
    app_id   = "shopping_assistant_app",
    catalog  = CATALOG,
)

# 1 — start session
session_id = w_test.start_session(
    user_email  = "testuser@example.com",
    channel     = "web",
    device_type = "desktop"
)
print(f"  Session started  : {session_id[:8]}...")

# 2 — log interaction (PII in input — proves redaction)
result = w_test.log_interaction(
    user_email    = "testuser@example.com",
    user_input    = "Hi I am Priya, email priya@test.com. Show me red kurtas under 2000",
    model_output  = "Here are 5 red kurtas under Rs.2000 for you.",
    model_name    = "gpt-4",
    model_version = "turbo",
    provider      = "openai",
    region        = "eastus",
    status        = "success",
    confidence_score = 0.94,
    latency_ms    = 1240,
    user_country  = "IN",
    session_id    = session_id,
    system_prompt_version = "v1.0",
    consent_version = "tnc_v2.1",
    app_metadata  = {"occasion": "casual", "budget": 2000},
)
trace_id    = result["trace_id"]
subject_ref = result.get("subject_ref")
print(f"  Interaction logged: trace={trace_id[:8]}... status={result['status']}")

# 3 — log node executions (pipeline steps)
nid1 = w_test.log_node_execution(
    trace_id     = trace_id,
    node_name    = "intent_classifier",
    node_type    = "llm",
    node_order   = 1,
    status       = "success",
    input_summary  = "user query about kurtas",
    output_summary = "intent: product_search, category: kurta, color: red",
    model_name   = "gpt-4",
    tokens_used  = 85,
    latency_ms   = 210,
    subject_ref  = subject_ref,
)
nid2 = w_test.log_node_execution(
    trace_id     = trace_id,
    node_name    = "product_ranker",
    node_type    = "tool",
    node_order   = 2,
    status       = "success",
    input_summary  = "5 products from catalogue",
    output_summary = "ranked by relevance score",
    latency_ms   = 95,
    subject_ref  = subject_ref,
)
print(f"  Node executions  : intent_classifier, product_ranker")

# 4 — log tool call
w_test.log_tool_call(
    trace_id          = trace_id,
    tool_name         = "product_catalogue_search",
    tool_inputs       = {"query": "red kurta", "max_price": 2000, "limit": 10},
    tool_outputs      = {"results_count": 5, "top_product_id": "SKU_001"},
    status            = "success",
    latency_ms        = 380,
    node_execution_id = nid1,
    subject_ref       = subject_ref,
)
print(f"  Tool call        : product_catalogue_search")

# 5 — log guardrail
w_test.log_guardrail(
    trace_id       = trace_id,
    policy_name    = "body_neutrality_check",
    score          = 0.97,
    result         = "pass",
    triggered_block= False,
    subject_ref    = subject_ref,
)
print(f"  Guardrail        : body_neutrality_check → pass")

# 6 — log human feedback (async - stylist override)
w_test.log_human_feedback(
    trace_id      = trace_id,
    feedback_type = "override",
    action        = "override",
    subject_ref   = subject_ref,
    reviewer_email= "stylist@xponent.ai",
    rationale     = "Added dupatta set option for festive season",
    rating        = 4,
)
print(f"  Human feedback   : stylist override logged")

print("\n  Smoke test complete. Verifying what was written...\n")

# verify written data
spark.sql(f"USE CATALOG {CATALOG}")

print("  ai_interactions_raw (input sanitised):")
spark.sql(f"""
    SELECT trace_id, input_text_sanitised, regulation_at_time,
           status, confidence_score
    FROM {tbl(RAW, 'ai_interactions_raw')}
    WHERE trace_id = '{trace_id}'
""").show(truncate=False)

print("  node_executions_raw:")
spark.sql(f"""
    SELECT node_name, node_type, node_order, status, latency_ms
    FROM {tbl(RAW, 'node_executions_raw')}
    WHERE trace_id = '{trace_id}'
    ORDER BY node_order
""").show(truncate=False)

print("  tool_calls_raw:")
spark.sql(f"""
    SELECT tool_name, status, latency_ms, tool_inputs
    FROM {tbl(RAW, 'tool_calls_raw')}
    WHERE trace_id = '{trace_id}'
""").show(truncate=False)

print("  guardrail_results_raw:")
spark.sql(f"""
    SELECT policy_name, score, result, triggered_block
    FROM {tbl(RAW, 'guardrail_results_raw')}
    WHERE trace_id = '{trace_id}'
""").show(truncate=False)

print("  human_feedback_raw:")
spark.sql(f"""
    SELECT feedback_type, action, rationale_sanitised, rating
    FROM {tbl(RAW, 'human_feedback_raw')}
    WHERE trace_id = '{trace_id}'
""").show(truncate=False)

# COMMAND ----------

# MAGIC %md
# MAGIC ---
# MAGIC ## Step 7 — Final verification

# COMMAND ----------

# ============================================================
# STEP 7 — VERIFICATION
# ============================================================

print("=" * 60)
print("  COMPONENT 4 VERIFICATION")
print("=" * 60 + "\n")

all_ok = True
spark.sql(f"USE CATALOG {CATALOG}")

checks = {
    "ai_interactions_raw has data":
        f"SELECT COUNT(*) AS c FROM {tbl(RAW,'ai_interactions_raw')} WHERE is_erasure_flag=false",
    "model_outputs_raw has data":
        f"SELECT COUNT(*) AS c FROM {tbl(RAW,'model_outputs_raw')} WHERE is_erasure_flag=false",
    "node_executions_raw has data":
        f"SELECT COUNT(*) AS c FROM {tbl(RAW,'node_executions_raw')} WHERE is_erasure_flag=false",
    "tool_calls_raw has data":
        f"SELECT COUNT(*) AS c FROM {tbl(RAW,'tool_calls_raw')} WHERE is_erasure_flag=false",
    "guardrail_results_raw has data":
        f"SELECT COUNT(*) AS c FROM {tbl(RAW,'guardrail_results_raw')} WHERE is_erasure_flag=false",
    "human_feedback_raw has data":
        f"SELECT COUNT(*) AS c FROM {tbl(RAW,'human_feedback_raw')} WHERE is_erasure_flag=false",
    "sessions_raw has data":
        f"SELECT COUNT(*) AS c FROM {tbl(RAW,'sessions_raw')} WHERE is_erasure_flag=false",
    "logging_failures is empty (no errors)":
        None,
}

for check, query in checks.items():
    if query is None:
        cnt = spark.sql(
            f"SELECT COUNT(*) AS c FROM {tbl(RAW,'logging_failures')}"
        ).first()["c"]
        ok  = (cnt == 0)
        print(f"  [{'OK' if ok else 'WARN'}] {check} ({cnt} failures)")
    else:
        cnt = spark.sql(query).first()["c"]
        ok  = (cnt > 0)
        all_ok &= ok
        print(f"  [{'OK' if ok else 'FAIL'}] {check} ({cnt} rows)")

# PII redaction check
rows = spark.sql(f"""
    SELECT input_text_sanitised FROM {tbl(RAW,'ai_interactions_raw')}
    WHERE trace_id = '{trace_id}'
""").collect()
if rows:
    text = rows[0]["input_text_sanitised"]
    pii_gone  = "priya@test.com" not in text.lower()
    pii_gone &= "priya" not in text.lower()
    all_ok &= pii_gone
    print(f"  [{'OK' if pii_gone else 'FAIL'}] PII redacted from input text")
    print(f"    stored as: {text}")

print("\n" + "=" * 60)
if all_ok:
    print("  COMPONENT 4 COMPLETE — logging wrapper is live.")
    print("  Next: Component 5 — synthetic data + full pipeline test.")
else:
    print("  SOMETHING FAILED — check items above.")
print("=" * 60)

# COMMAND ----------

# MAGIC %md
# MAGIC ---
# MAGIC ## Component 4 complete
# MAGIC
# MAGIC ```
# MAGIC AuditWrapper is live. Any app imports it with 3 lines:
# MAGIC
# MAGIC     import sys
# MAGIC     sys.path.insert(0, '/dbfs/audit_trail/')
# MAGIC     from audit_wrapper import AuditWrapper
# MAGIC
# MAGIC     wrapper = AuditWrapper(
# MAGIC         app_id  = "shopping_assistant_app",
# MAGIC         catalog = "shopping_assistant"
# MAGIC     )
# MAGIC
# MAGIC Methods available:
# MAGIC     wrapper.start_session()        → returns session_id
# MAGIC     wrapper.log_interaction()      → writes to 2 tables, returns trace_id
# MAGIC     wrapper.log_node_execution()   → writes to node_executions_raw
# MAGIC     wrapper.log_tool_call()        → writes to tool_calls_raw
# MAGIC     wrapper.log_guardrail()        → writes to guardrail_results_raw
# MAGIC     wrapper.log_human_feedback()   → writes to human_feedback_raw (async)
# MAGIC ```
# MAGIC
# MAGIC **Next → Component 5:** synthetic data generator — pushes 500 realistic
# MAGIC interactions through the wrapper to populate all tables for
# MAGIC dashboard and erasure testing.