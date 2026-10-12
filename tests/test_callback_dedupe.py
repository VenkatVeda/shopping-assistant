"""
Reproduce the duplicate node_executions_raw rows with the app's graph topology
(same node names, same conditional edges, same node wrapper pattern) and the
real AuditTrailCallback. A fake AuditWrapper records every log_node_execution call.

usage: python test_dupes.py <directory containing audit_wrapper.py>
"""
import os as _os, sys as _sys
_ROOT = _sys.argv[1] if len(_sys.argv) > 1 else _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), '..')

import sys, functools, collections, inspect
from typing import TypedDict, Optional

sys.path.insert(0, _ROOT)
import audit_wrapper as aw
from langgraph.graph import StateGraph, END
from langgraph.checkpoint.memory import MemorySaver


class FakeAudit:
    def __init__(self):
        self.rows = []
    def log_node_execution(self, **kw):
        self.rows.append(kw)
    def log_tool_call(self, **kw):
        pass


class S(TypedDict, total=False):
    query: str
    relax: int
    result_count_status: str
    intent: str
    guardrail_status: str


def wrap(name, fn):
    """Same shape as NodeTracer.wrap with mlflow unavailable: functools.wraps + try/finally."""
    @functools.wraps(fn)
    def wrapped(state):
        try:
            return fn(state)
        finally:
            pass
    return wrapped


def input_guardrail(s):      return {"guardrail_status": "pass"}
def intent_classifier(s):    return {"intent": "shopping"}
def personalization(s):      return {}
def product_search(s):       return {}
def result_validator(s):     return {"result_count_status": "zero" if (s.get("relax") or 0) < 4 else "optimal"}
def constraint_relaxer(s):   return {"relax": (s.get("relax") or 0) + 1}
def reranker(s):             return {}
def response_generator(s):   return {}
def output_guardrail(s):     return {}

g = StateGraph(S)
for n, f in [("input_guardrail", input_guardrail), ("intent_classifier", intent_classifier),
             ("personalization", personalization), ("product_search", product_search),
             ("result_validator", result_validator), ("constraint_relaxer", constraint_relaxer),
             ("reranker", reranker), ("response_generator", response_generator),
             ("output_guardrail", output_guardrail)]:
    g.add_node(n, wrap(n, f))
g.set_entry_point("input_guardrail")
g.add_conditional_edges("input_guardrail", lambda s: "pass", {"pass": "intent_classifier", "fail": END})
g.add_conditional_edges("intent_classifier", lambda s: "shopping",
                        {"shopping": "personalization", "chat": "response_generator"})
g.add_edge("personalization", "product_search")
g.add_edge("product_search", "result_validator")
g.add_conditional_edges("result_validator",
                        lambda s: s.get("result_count_status"),
                        {"zero": "constraint_relaxer", "optimal": "reranker"})
g.add_conditional_edges("constraint_relaxer", lambda s: "continue" if (s.get("relax") or 0) < 4 else "done",
                        {"continue": "product_search", "done": "reranker"})
g.add_edge("reranker", "response_generator")
g.add_edge("response_generator", "output_guardrail")
g.add_edge("output_guardrail", END)

audit = FakeAudit()
cb = aw.AuditTrailCallback(audit)
app = g.compile(checkpointer=MemorySaver()).with_config({"callbacks": [cb]})
aw._current_trace_id.set("test-trace")

app.invoke({"query": "show me black bags"}, config={"configurable": {"thread_id": "t1"}})

counts = collections.Counter(r["node_name"] for r in audit.rows)
expected = {"input_guardrail": 1, "intent_classifier": 1, "personalization": 1, "product_search": 4,
            "result_validator": 4, "constraint_relaxer": 4, "reranker": 1, "response_generator": 1,
            "output_guardrail": 1}
print(f"{'node':22} {'rows written':>12} {'real executions':>16}")
for n in ["__start__"] + list(expected):
    print(f"{n:22} {counts.get(n, 0):>12} {expected.get(n, 1):>16}")
print("total rows:", sum(counts.values()), " real executions:", sum(expected.values()) + 1)

# start-time check: does the row carry the real start time?
sig = inspect.signature(aw.AuditWrapper.log_node_execution)
print("log_node_execution accepts started_at:", "started_at" in sig.parameters)
print("callback passed started_at on first row:", "started_at" in audit.rows[0] if audit.rows else None)
bad = {n: (counts.get(n, 0), expected.get(n, 1)) for n in expected if counts.get(n, 0) != expected[n]}
print("RESULT:", "OK, one row per execution" if not bad and counts.get("__start__", 0) <= 1 else f"DUPLICATES {bad}")

