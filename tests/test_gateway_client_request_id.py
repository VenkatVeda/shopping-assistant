"""Mocked-gateway tests for the audit_fix gateway_client.py + audit_wrapper.py"""
import os as _os, sys as _sys
_ROOT = _sys.argv[1] if len(_sys.argv) > 1 else _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), '..')

import sys, json, time, types
root = _ROOT
sys.path.insert(0, root); sys.path.insert(0, root + "/core")
import audit_wrapper as aw
import importlib.util
spec = importlib.util.spec_from_file_location("gateway_client", root + "/core/gateway_client.py")
gc = importlib.util.module_from_spec(spec)
class FakeW:
    class config: host = "https://example.azuredatabricks.net"
gc_src = open(root + "/core/gateway_client.py", encoding="utf-8").read()
import databricks.sdk
databricks.sdk.WorkspaceClient = lambda *a, **k: FakeW()
spec.loader.exec_module(gc)

captured = []
aw._fire = lambda table, row, on_failure=None: captured.append((table, row))
w = object.__new__(aw.AuditWrapper); w.app_id, w.catalog, w.schema_version, w._key_cache = "myre_app", "shopping_assistant", "1.0", {}
gc.set_audit_sink(w)
aw._current_trace_id.set("trace-1")
gc.flask_request = types.SimpleNamespace(headers={"X-Forwarded-Access-Token": "tok"})

class Resp:
    def __init__(self, status, body, headers):
        self.status_code, self._b, self.headers, self.text = status, body, headers, json.dumps(body)
    def json(self): return self._b

def run(resp):
    captured.clear()
    gc.requests.post = lambda *a, **k: resp
    try:
        gc.GatewayChatModel("system.ai.meta-llama-3-1-8b-instruct").invoke("hi")
    except gc.GatewayPolicyBlock:
        pass
    time.sleep(0.15)
    rows = [r for t, r in captured if t.endswith("node_executions_raw")]
    assert len(rows) == 1, rows
    return rows[0], json.loads(rows[0]["node_metadata"])

ok = {"choices": [{"message": {"content": "hello"}}], "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}}
blocked = {"choices": [{"message": {"content": "This request was blocked by the 'block-jailbreak' service policy."}}], "usage": {}}
failures = []
def check(name, cond):
    print(("PASS " if cond else "FAIL ") + name)
    if not cond: failures.append(name)

# 1 allowed call, id in header
row, meta = run(Resp(200, ok, {"x-request-id": "03366c49-e306-4849-bcaa-3d624dcd9461", "content-type": "application/json"}))
check("allowed: gateway_request_id captured from x-request-id", meta["gateway_request_id"] == "03366c49-e306-4849-bcaa-3d624dcd9461")
check("allowed: header name recorded", meta["request_id_header"] == "x-request-id")
check("allowed: policy_action ALLOW, tokens 15", meta["policy_action"] == "ALLOW" and row["tokens_used"] == "15")
check("allowed: response header names recorded", "content-type" in meta["response_header_keys"])

# 2 blocked call
row, meta = run(Resp(200, blocked, {"x-request-id": "7212a026-c3bf-4738-a530-eb4e2a6c6147"}))
check("blocked: status blocked, DENY, policy and phase", row["status"] == "blocked" and meta["policy_action"] == "DENY"
      and meta["blocked_by_policy"] == "block-jailbreak" and meta["blocked_phase"] == "pre_call")
check("blocked: gateway_request_id kept", meta["gateway_request_id"] == "7212a026-c3bf-4738-a530-eb4e2a6c6147")

# 3 no id anywhere: header names are still recorded so the real name can be found
row, meta = run(Resp(200, ok, {"content-type": "application/json", "x-trace-thing": "abc"}))
check("no id: gateway_request_id is null", meta["gateway_request_id"] is None)
check("no id: header names recorded for discovery", meta["response_header_keys"] == ["content-type", "x-trace-thing"])
check("no id: body keys recorded", meta["response_body_keys"] == ["choices", "usage"])

# 4 UUID whose last segment is all digits must survive redaction (the phone/Aadhaar patterns would eat it)
tricky = "aaaaaaaa-bbbb-4ccc-8ddd-123456789012"
row, meta = run(Resp(200, ok, {"x-request-id": tricky}))
check("all-digit UUID tail survives node_metadata redaction", meta["gateway_request_id"] == tricky)
check("(control) plain _redact_pii would have corrupted it", tricky not in aw._redact_pii(tricky))
check("PII is still redacted next to a UUID", "[EMAIL]" in aw._redact_pii_keep_ids(f"{tricky} mail a@b.com") and tricky in aw._redact_pii_keep_ids(f"{tricky} mail a@b.com"))

print("\nRESULT:", "ALL PASSED" if not failures else f"FAILED: {failures}")

