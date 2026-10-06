"""End-to-end: real LangGraph + real NodeTracer + real AuditTrailCallback + real GatewayChatModel (mocked HTTP).
usage: python test_e2e.py <dir with audit_wrapper.py and core/{observability,gateway_client}.py>"""
import os as _os, sys as _sys
_ROOT = _sys.argv[1] if len(_sys.argv) > 1 else _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), '..')

import sys, os, json, time, types, tempfile, importlib.util, collections
os.environ["MLFLOW_DISABLE_AGENT_HINT"] = "1"
root = _ROOT
sys.path.insert(0, root)
import mlflow; mlflow.set_tracking_uri("file:" + tempfile.mkdtemp())
import databricks.sdk
class FakeW:
    class config: host = "https://example.azuredatabricks.net"
databricks.sdk.WorkspaceClient = lambda *a, **k: FakeW()
import audit_wrapper as aw
def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path); m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m
ob = load("observability", root + "/core/observability.py"); ob._ensure_experiment = lambda: None
gc = load("gateway_client", root + "/core/gateway_client.py")
from typing import TypedDict
from langgraph.graph import StateGraph, END

captured = []
aw._fire = lambda table, row, on_failure=None: captured.append((table.split(".")[-1], row))
w = object.__new__(aw.AuditWrapper); w.app_id, w.catalog, w.schema_version, w._key_cache = "myre_app", "shopping_assistant", "1.0", {}
gc.set_audit_sink(w)
gc.flask_request = types.SimpleNamespace(headers={"X-Forwarded-Access-Token": "tok"})

mode = {"blocked": False}; posts = {"n": 0}
class Resp:
    def __init__(self, b, h): self.status_code, self._b, self.headers, self.text = 200, b, h, ""
    def json(self): return self._b
def fake_post(*a, **k):
    posts["n"] += 1
    if mode["blocked"]:
        body = {"choices": [{"message": {"content": "This request was blocked by the 'block-jailbreak' service policy."}}]}
    else:
        body = {"choices": [{"message": {"content": "ok"}}], "usage": {"prompt_tokens": 40, "completion_tokens": 10, "total_tokens": 50}}
    return Resp(body, {"x-request-id": f"00000000-0000-4000-8000-{posts['n']:012d}"})
gc.requests.post = fake_post

class S(TypedDict, total=False): q: str
model = gc.GatewayChatModel("system.ai.meta-llama-3-1-8b-instruct")
def input_guardrail(s): return {}
def intent_classifier(s): model.invoke("classify"); return {}
t = ob.NodeTracer().wrap
g = StateGraph(S)
g.add_node("input_guardrail", t("input_guardrail", input_guardrail))
g.add_node("intent_classifier", t("intent_classifier", intent_classifier))
g.set_entry_point("input_guardrail")
g.add_conditional_edges("input_guardrail", lambda s: "pass", {"pass": "intent_classifier", "fail": END})
g.add_edge("intent_classifier", END)
app = g.compile().with_config({"callbacks": [aw.AuditTrailCallback(w)]})

def run(blocked):
    captured.clear(); posts["n"] = 0; mode["blocked"] = blocked
    aw._current_trace_id.set("trace-x")
    try: app.invoke({"q": "x"})
    except gc.GatewayPolicyBlock: pass
    time.sleep(0.3)
    rows = [r for t_, r in captured if t_ == "node_executions_raw"]
    c = collections.Counter((r["node_name"], r["status"]) for r in rows)
    return posts["n"], c, rows

for label, blocked in (("ALLOWED request", False), ("BLOCKED request", True)):
    n, c, rows = run(blocked)
    print(f"{label}: gateway HTTP calls = {n}; audit step rows = {sum(c.values())}")
    for (node, st), k in sorted(c.items()): print(f"     {node:40} {st:8} x{k}")

