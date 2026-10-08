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

# 5 real blocked-response shape (captured 2026-10-07): databricks_service_policy object, HTTP 200
pre = {"id": "databricks-guardrail-block", "object": "chat.completion", "created": 0,
       "choices": [{"index": 0, "message": {"role": "assistant", "content": "This request was blocked by the 'block-jailbreak' service policy."},
                    "finish_reason": "content_filter"}],
       "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
       "databricks_service_policy": {"name": "block-jailbreak", "action": "deny", "phase": "pre_call",
                                     "reason": "Attempts direct instruction override with 'Ignore your instructions'."}}
row, meta = run(Resp(200, pre, {"x-request-id": "a934cb54-1a54-413a-a453-ef896be5150b"}))
check("service policy: blocked, policy, phase from object", row["status"] == "blocked" and meta["blocked_by_policy"] == "block-jailbreak" and meta["blocked_phase"] == "pre_call")
check("service policy: decision DENY and finish_reason recorded", meta["policy_decision"] == "DENY" and meta["finish_reason"] == "content_filter")
check("service policy: reason stored with quoted user text masked", meta["policy_reason"] == "Attempts direct instruction override with '[REDACTED]'.")

post = {"id": "databricks-guardrail-block", "object": "chat.completion", "created": 0,
        "choices": [{"index": 0, "message": {"role": "assistant", "content": "This response was blocked by the 'hallucination-guard' service policy."},
                     "finish_reason": "content_filter"}],
        "usage": {"prompt_tokens": 21, "completion_tokens": 60, "total_tokens": 81},
        "databricks_service_policy": {"name": "hallucination-guard", "action": "deny", "phase": "post_call",
                                      "reason": "The response presents a specific product with fabricated price."}}
row, meta = run(Resp(200, post, {"x-request-id": "2f33a5d9-170a-490c-b5db-3a010e0eb8c7"}))
check("post_call block: phase post_call, tokens still counted", meta["blocked_phase"] == "post_call" and row["tokens_used"] == "81")

# 6 a policy object alone (message text changed by Databricks) is still detected as a block
odd = {"choices": [{"message": {"content": "Sorry."}, "finish_reason": "stop"}], "usage": {},
       "databricks_service_policy": {"name": "off-topic-restriction", "action": "deny", "phase": "pre_call", "reason": "x"}}
row, meta = run(Resp(200, odd, {"x-request-id": "11111111-2222-4333-8444-555555555555"}))
check("block detected without the block message text", row["status"] == "blocked" and meta["blocked_by_policy"] == "off-topic-restriction")

# 7 real pass shape: no policy object, no block
okreal = {"id": "chatcmpl_x", "object": "chat.completion", "created": 1791346459,
          "choices": [{"index": 0, "message": {"role": "assistant", "content": "I can help with bags."}, "finish_reason": "length", "logprobs": None}],
          "usage": {"prompt_tokens": 22, "completion_tokens": 60, "total_tokens": 82}}
row, meta = run(Resp(200, okreal, {"x-request-id": "e6c8ebd4-6813-4ddc-8761-6add3a42b55d"}))
check("real pass: success, no policy fields, finish_reason kept",
      row["status"] == "success" and meta["blocked_by_policy"] is None and meta["policy_reason"] is None and meta["finish_reason"] == "length")

# 8 call-time fields: OTEL trace id, gateway timing, real start time, size and keyed hash of what was sent
w._hmac = lambda value: f"hmac-{len(value)}"
OTEL = "4bf92f3577b34da6a3ce929d0e0e4736"
hdrs = {"x-request-id": "822c258b-834d-428c-bfd0-959ccb212741",
        "traceresponse": f"00-{OTEL}-00f067aa0ba902b7-01", "server-timing": "total;dur=6749"}
row, meta = run(Resp(200, ok, hdrs))
check("OTEL trace id is read from the traceresponse header", meta["otel_trace_id"] == OTEL)
check("server-timing header is kept", meta["server_timing"] == "total;dur=6749")
check("started_at is the real start, earlier than the write time (ended_at)", row["started_at"] < row["ended_at"])
check("prompt size is recorded, text is not", meta["message_count"] == 1 and meta["prompt_chars"] == 2 and "hi" not in json.dumps(meta).replace("hmac", ""))
check("prompt and response are stored as keyed hashes", meta["prompt_hash"] == "hmac-2" and meta["response_hash"] == "hmac-5")
check("the response text is not stored anywhere in the row", "hello" not in json.dumps(row))
row, meta = run(Resp(200, ok, {"x-request-id": "e6c8ebd4-6813-4ddc-8761-6add3a42b55d", "traceresponse": "garbage"}))
check("a malformed traceresponse gives no OTEL id and no error", meta["otel_trace_id"] is None and row["status"] == "success")
w._hmac = lambda value: (_ for _ in ()).throw(RuntimeError("no key"))
row, meta = run(Resp(200, ok, {"x-request-id": "e6c8ebd4-6813-4ddc-8761-6add3a42b55d"}))
check("if the audit key is unavailable the row is still written, hashes empty", row["status"] == "success" and meta["prompt_hash"] is None and meta["response_hash"] is None)
w._hmac = lambda value: f"hmac-{len(value)}"

# 9 the call row points at the step that made it
aw._active_nodes[("trace-1", "intent_classifier")] = "step-id-1"
gc._calling_node = lambda: "intent_classifier"
row, meta = run(Resp(200, ok, {"x-request-id": "e6c8ebd4-6813-4ddc-8761-6add3a42b55d"}))
check("parent_node_id is the id of the running step", row["parent_node_id"] == "step-id-1")
gc._calling_node = lambda: "reranker"
row, meta = run(Resp(200, ok, {"x-request-id": "e6c8ebd4-6813-4ddc-8761-6add3a42b55d"}))
check("no running step of that name: parent_node_id stays empty", row.get("parent_node_id") is None)
gc._calling_node = lambda: None

# 10 the response object carries usage and request id for the app (e.g. tokens_used in model_outputs_raw)
gc.requests.post = lambda *a, **k: Resp(200, ok, {"x-request-id": "03366c49-e306-4849-bcaa-3d624dcd9461"})
out = gc.GatewayChatModel("system.ai.meta-llama-3-1-8b-instruct").invoke("hi")
check("response.usage and response.request_id are available", out.usage["total_tokens"] == 15 and out.request_id == "03366c49-e306-4849-bcaa-3d624dcd9461")
check("response.content is unchanged", out.content == "hello")
time.sleep(0.15)

print("\nRESULT:", "ALL PASSED" if not failures else f"FAILED: {failures}")

