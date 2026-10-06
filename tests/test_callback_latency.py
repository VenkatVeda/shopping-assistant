"""A 300 ms node must be logged with its real latency and a started_at that is 300 ms before ended_at."""
import os as _os, sys as _sys
_ROOT = _sys.argv[1] if len(_sys.argv) > 1 else _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), '..')
import sys, time
from typing import TypedDict
from datetime import datetime
sys.path.insert(0, _ROOT)
import audit_wrapper as aw
from langgraph.graph import StateGraph, END
cap = []
aw._fire = lambda t, r, on_failure=None: cap.append(r)
w = object.__new__(aw.AuditWrapper); w.app_id, w.catalog, w.schema_version, w._key_cache = "a", "c", "1.0", {}
class S(TypedDict, total=False): q: str
def slow(s): time.sleep(0.3); return {}
g = StateGraph(S); g.add_node("slow_node", slow); g.set_entry_point("slow_node"); g.add_edge("slow_node", END)
app = g.compile().with_config({"callbacks": [aw.AuditTrailCallback(w)]})
aw._current_trace_id.set("t"); app.invoke({"q": "x"}); time.sleep(0.05)
f = "%Y-%m-%d %H:%M:%S.%f"
r = [x for x in cap if x["node_name"] == "slow_node"][0]
gap = (datetime.strptime(r["ended_at"], f) - datetime.strptime(r["started_at"], f)).total_seconds() * 1000
print(f"slow node (300 ms): latency_ms={r['latency_ms']}  started_at->ended_at gap={gap:.0f} ms")
print('FAIL: latency or start time is wrong' if not (int(r['latency_ms']) >= 250 and gap >= 250) else 'PASS: real latency and real start time')


