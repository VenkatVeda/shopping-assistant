"""
Test suite for AuditWrapper.
Run: python -m pytest tests/test_audit_wrapper.py -v

Prerequisites:
    .env must have:
        AUDIT_APP_ID=myre_app
        DATABRICKS_SQL_WAREHOUSE_ID=...
        AUDIT_SECRET_SCOPE=audit_trail_secrets
"""
import os
from dotenv import load_dotenv

# explicitly load .env from project root
load_dotenv(os.path.join(os.path.dirname(__file__), '..', '.env'), override=True)
import os
import pytest
from dotenv import load_dotenv

load_dotenv()

# ── helpers ───────────────────────────────────────────────────────────────

def get_warehouse():
    from databricks.sdk import WorkspaceClient
    from databricks.sdk.service.sql import StatementState
    return WorkspaceClient(), os.getenv("DATABRICKS_SQL_WAREHOUSE_ID")


def query(sql: str) -> list:
    """Run a SQL query and return rows."""
    from databricks.sdk.service.sql import StatementState
    w, wh = get_warehouse()
    result = w.statement_execution.execute_statement(
        warehouse_id=wh, statement=sql, wait_timeout="15s"
    )
    assert result.status.state == StatementState.SUCCEEDED, \
        f"Query failed: {result.status.error}"
    return result.result.data_array or []


# ── test 1: AUDIT_APP_ID not set raises ValueError ────────────────────────

def test_missing_app_id_raises():
    """Wrapper must crash loudly if AUDIT_APP_ID not configured."""
    original = os.environ.pop("AUDIT_APP_ID", None)
    try:
        from audit_wrapper import AuditWrapper
        with pytest.raises(ValueError, match="AUDIT_APP_ID is not configured"):
            AuditWrapper()
    finally:
        if original:
            os.environ["AUDIT_APP_ID"] = original


# ── test 2: unregistered app_id raises ValueError ─────────────────────────

def test_unregistered_app_raises():
    """Wrapper must reject app_ids not in app_registry."""
    from audit_wrapper import AuditWrapper
    with pytest.raises(ValueError, match="not found in app_registry"):
        AuditWrapper(app_id="nonexistent_app_xyz")


# ── test 3: missing HMAC key raises RuntimeError ──────────────────────────

def test_missing_hmac_key_raises():
    """Wrapper must fail fast if HMAC key not in secret scope."""
    # temporarily point to wrong scope
    original = os.environ.get("AUDIT_SECRET_SCOPE")
    os.environ["AUDIT_SECRET_SCOPE"] = "nonexistent_scope_xyz"
    try:
        from audit_wrapper import AuditWrapper
        import importlib
        import audit_wrapper as aw_module
        importlib.reload(aw_module)

        wrapper = aw_module.AuditWrapper.__new__(aw_module.AuditWrapper)
        wrapper.app_id         = os.getenv("AUDIT_APP_ID", "myre_app")
        wrapper.catalog        = "shopping_assistant"
        wrapper.schema_version = "1.0"
        wrapper._key_cache     = {}

        with pytest.raises(RuntimeError, match="Unable to load HMAC key"):
            wrapper._get_key()
    finally:
        if original:
            os.environ["AUDIT_SECRET_SCOPE"] = original
        else:
            os.environ.pop("AUDIT_SECRET_SCOPE", None)


# ── test 4: successful write ───────────────────────────────────────────────

def test_successful_write():
    """
    Log a real interaction and confirm the row lands in Delta.
    This is the core smoke test.
    """
    from audit_wrapper import AuditWrapper
    import time

    wrapper = AuditWrapper()

    result = wrapper.log_interaction(
        user_email            = "pytest_test@myre.com",
        user_input            = "show me red bags under 500",
        model_output          = "Here are some red bags",
        model_name            = "databricks-meta-llama-3-1-8b-instruct",
        status                = "success",
        user_country          = "IN",
        system_prompt_version = "v1.0",
        final_state           = {
            "intent":           "shopping",
            "reranked_results": [1, 2, 3],
            "guardrail_status": "pass"
        }
    )

    assert result["status"] == "ok"
    assert "trace_id" in result
    assert "subject_ref" in result
    assert result["regulation"] == "DPDP"

    trace_id = result["trace_id"]

    # wait for background thread to complete
    time.sleep(8)

    # confirm row in Delta
    rows = query(f"""
        SELECT trace_id, app_id, regulation_at_time, status
        FROM shopping_assistant.raw_logs.ai_interactions_raw
        WHERE trace_id = '{trace_id}'
    """)

    assert len(rows) == 1, f"Expected 1 row, got {len(rows)}"
    assert rows[0][1] == os.getenv("AUDIT_APP_ID", "myre_app")
    assert rows[0][2] == "DPDP"
    assert rows[0][3] == "success"
    print(f"\n  trace_id confirmed in Delta: {trace_id}")


# ── test 5: PII is redacted before storing ────────────────────────────────

def test_pii_redacted():
    """Input containing email and phone must be sanitised before writing."""
    from audit_wrapper import AuditWrapper
    import time

    wrapper = AuditWrapper()

    result = wrapper.log_interaction(
        user_email   = "pytest_pii@myre.com",
        user_input   = "Hi I am Priya, email priya@test.com phone 9876543210",
        model_output = "Here are some options",
        model_name   = "databricks-meta-llama-3-1-8b-instruct",
        status       = "success",
        user_country = "IN",
    )

    trace_id = result["trace_id"]
    time.sleep(15)

    rows = query(f"""
        SELECT input_text_sanitised
        FROM shopping_assistant.raw_logs.ai_interactions_raw
        WHERE trace_id = '{trace_id}'
    """)

    assert len(rows) == 1
    stored_input = rows[0][0]
    assert "priya@test.com" not in stored_input, "Email not redacted"
    assert "9876543210"     not in stored_input, "Phone not redacted"
    assert "[EMAIL]"        in stored_input
    assert "[PHONE]"        in stored_input
    print(f"\n  PII redacted. Stored: {stored_input}")


# ── test 6: GDPR regulation applied for EU user ───────────────────────────

def test_gdpr_regulation():
    """EU user must get GDPR regulation, not DPDP."""
    from audit_wrapper import AuditWrapper
    import time

    wrapper = AuditWrapper()

    result = wrapper.log_interaction(
        user_email   = "pytest_eu@myre.com",
        user_input   = "show me bags",
        model_output = "Here are some bags",
        model_name   = "databricks-meta-llama-3-1-8b-instruct",
        status       = "success",
        user_country = "DE",   # Germany → GDPR
    )

    assert result["regulation"] == "GDPR"

    trace_id = result["trace_id"]
    time.sleep(15)

    rows = query(f"""
        SELECT regulation_at_time
        FROM shopping_assistant.raw_logs.ai_interactions_raw
        WHERE trace_id = '{trace_id}'
    """)
    assert rows[0][0] == "GDPR"
    print(f"\n  DE → GDPR confirmed in Delta")


# ── test 7: guardrail logging ─────────────────────────────────────────────

def test_guardrail_write():
    """Guardrail result must land in guardrail_results_raw."""
    from audit_wrapper import AuditWrapper
    import time

    wrapper  = AuditWrapper()
    trace_id = "pytest-guardrail-" + __import__('uuid').uuid4().hex[:8]

    wrapper.log_guardrail(
        trace_id        = trace_id,
        policy_name     = "pytest_body_neutrality_check",
        score           = 0.95,
        result          = "pass",
        triggered_block = False,
        subject_ref     = "test_ref",
    )

    time.sleep(15)

    rows = query(f"""
        SELECT policy_name, result, triggered_block
        FROM shopping_assistant.raw_logs.guardrail_results_raw
        WHERE trace_id = '{trace_id}'
    """)

    assert len(rows) == 1
    assert rows[0][0] == "pytest_body_neutrality_check"
    assert rows[0][1] == "pass"
    assert rows[0][2] == "false"
    print(f"\n  Guardrail row confirmed in Delta")


# ── test 8: logging failure path ──────────────────────────────────────────

def test_logging_failure_path():
    """
    If log_interaction fails internally, it must:
    - return status = logging_failed (not raise)
    - write to logging_failures table
    """
    from audit_wrapper import AuditWrapper
    import time

    wrapper = AuditWrapper()

    # pass None as user_email to force an internal error
    result = wrapper.log_interaction(
        user_email   = None,
        user_input   = "test",
        model_output = "test",
        model_name   = "test-model",
        status       = "success",
    )

    # must not raise — returns error status
    assert result["status"] == "logging_failed"
    assert "error" in result
    print(f"\n  Failure handled gracefully: {result['error'][:80]}")


# ── test 9: subject_ref is deterministic ─────────────────────────────────

def test_subject_ref_deterministic():
    """Same email must always produce same subject_ref."""
    from audit_wrapper import AuditWrapper

    wrapper = AuditWrapper()
    email   = "determinism_test@myre.com"

    _, ref1 = wrapper._compute_refs(email)
    _, ref2 = wrapper._compute_refs(email)
    _, ref3 = wrapper._compute_refs(email)

    assert ref1 == ref2 == ref3, "subject_ref is not deterministic"
    print(f"\n  subject_ref stable: {ref1[:16]}...")


# ── test 10: different emails give different refs ─────────────────────────

def test_subject_ref_unique():
    """Different emails must produce different subject_refs."""
    from audit_wrapper import AuditWrapper

    wrapper = AuditWrapper()
    _, ref1 = wrapper._compute_refs("user_a@myre.com")
    _, ref2 = wrapper._compute_refs("user_b@myre.com")

    assert ref1 != ref2, "Different emails gave same subject_ref"
    print(f"\n  user_a: {ref1[:16]}...")
    print(f"  user_b: {ref2[:16]}...")