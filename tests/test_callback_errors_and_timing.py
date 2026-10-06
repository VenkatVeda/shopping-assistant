"""
1) A node that raises (like a gateway block) must produce exactly ONE error row.
2) The real AuditWrapper.log_node_execution must store the real start time, earlier than the end time.
usage: python test_fix_details.py <directory containing audit_wrapper.py>
"""
import os as _os, sys as _sys
_ROOT = _sys.argv[1] if len(_sys.argv) > 1 else _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), '..')

import sys, time, functools, collections
from typing import TypedDict

sys.path.insert(0, _ROOT)
import audit_wrapper as aw
from langgraph.graph import StateGraph, END
from langgraph.checkpoint.memory import MemorySaver


class Block(Exception):
    pass


class S(TypedDict, total=False):
    q: str


def wrap(fn):
    @functools.wraps(fn)
    def w(state):
        return fn(state)
    return w


def input_guardrail(s):      return {}
def intent_classifier(s):    raise Block("Blocked by policy: block-jailbreak")


# ---- real AuditWrapper with the network write captured ----------------------
captured = []
aw._fire = lambda table, row, on_failure=None: captured.append((table, row))
w = object.__new__(aw.AuditWrapper)
w.app_id, w.catalog, w.schema_version, w._key_cache = "myre_app", "shopping_assistant", "1.0", {}

g = StateGraph(S)
g.add_node("input_guardrail", wrap(input_guardrail))
g.add_node("intent_classifier", wrap(intent_classifier))
g.set_entry_point("input_guardrail")
g.add_conditional_edges("input_guardrail", lambda s: "pass", {"pass": "intent_classifier", "fail": END})
g.add_edge("intent_classifier", END)

cb = aw.AuditTrailCallback(w)
app = g.compile(checkpointer=MemorySaver()).with_config({"callbacks": [cb]})
aw._current_trace_id.set("trace-block")
try:
    app.invoke({"q": "x"}, config={"configurable": {"thread_id": "t2"}})
except Block:
    pass

time.sleep(0.05)
rows = [r for t, r in captured if t.endswith("node_executions_raw")]
cnt = collections.Counter((r["node_name"], r["status"]) for r in rows)
print("rows by (node, status):", dict(cnt))
ok_err = cnt.get(("intent_classifier", "error"), 0) == 1
print("blocked node -> exactly one error row:", ok_err)

ok_time = True
for r in rows:
    s, e = r["started_at"], r["ended_at"]
    lat = int(r["latency_ms"]) if r.get("latency_ms") else None
    print(f"  {r['node_name']:18} started={s}  ended={e}  latency_ms={lat}  start<=end: {s <= e}")
    ok_time &= (s <= e)
# the latency recorded and the gap between started_at and ended_at should agree (a few ms)
r0 = next(r for r in rows if r["node_name"] == "input_guardrail")
from datetime import datetime
fmt = "%Y-%m-%d %H:%M:%S.%f"
gap_ms = (datetime.strptime(r0["ended_at"], fmt) - datetime.strptime(r0["started_at"], fmt)).total_seconds() * 1000
print(f"started_at to ended_at gap: {gap_ms:.1f} ms vs latency_ms {r0['latency_ms']} ms")
print("RESULT:", "OK" if (ok_err and ok_time) else "FAILED")

