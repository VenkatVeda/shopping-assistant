"""NodeTracer.wrap must run a node exactly once, whether it succeeds, raises, or MLflow itself misbehaves.
usage: python test_tracer_rerun.py <path to observability.py>"""
import os as _os, sys as _sys
_ROOT = _sys.argv[1] if len(_sys.argv) > 1 else _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), '..', 'core', 'observability.py')

import sys, os, importlib.util, tempfile
os.environ["MLFLOW_DISABLE_AGENT_HINT"] = "1"
import mlflow
mlflow.set_tracking_uri("file:" + tempfile.mkdtemp())
spec = importlib.util.spec_from_file_location("observability", _ROOT)
ob = importlib.util.module_from_spec(spec); spec.loader.exec_module(ob)
ob._ensure_experiment = lambda: None
assert ob._MLFLOW_AVAILABLE, "mlflow should be importable for this test"

class Block(Exception): pass
failures = []
def check(name, cond):
    print(("PASS " if cond else "FAIL ") + name)
    if not cond: failures.append(name)

def make(behaviour):
    calls = {"n": 0}
    def node(state):
        calls["n"] += 1
        if behaviour == "raise":
            raise Block("Blocked by policy: block-jailbreak")
        return {"intent": "shopping"}
    return calls, ob.NodeTracer().wrap("intent_classifier", node)

# 1. node succeeds
calls, f = make("ok"); out = f({"query": "q"})
check("normal node runs once and returns its result", calls["n"] == 1 and out == {"intent": "shopping"})

# 2. node raises (gateway block)
calls, f = make("raise")
try: f({"query": "q"}); raised = False
except Block: raised = True
check("a raising node still raises the same exception", raised)
check(f"a raising node runs exactly once (ran {calls['n']}x)", calls["n"] == 1)

# 3. MLflow fails before the node runs: node must still run, once
real_start_span = mlflow.start_span
def broken_start(*a, **k): raise RuntimeError("mlflow unavailable")
mlflow.start_span = broken_start
calls, f = make("ok"); out = f({"query": "q"})
check("MLflow down before the node: node runs once, result returned", calls["n"] == 1 and out == {"intent": "shopping"})
mlflow.start_span = real_start_span

# 4. MLflow fails after the node finished: node must NOT run again, result is kept
class Proxy:
    def __init__(self, span): self._s = span
    def __getattr__(self, name): return getattr(self._s, name)
    def set_outputs(self, *a, **k): raise RuntimeError("mlflow failed to record outputs")
class CM:
    def __init__(self, cm): self._cm = cm
    def __enter__(self): return Proxy(self._cm.__enter__())
    def __exit__(self, *a): return self._cm.__exit__(*a)
mlflow.start_span = lambda *a, **k: CM(real_start_span(*a, **k))
calls, f = make("ok"); out = f({"query": "q"})
check(f"MLflow fails after the node: node runs once (ran {calls['n']}x) and result is kept", calls["n"] == 1 and out == {"intent": "shopping"})
mlflow.start_span = real_start_span

print("\nRESULT:", "ALL PASSED" if not failures else f"FAILED: {failures}")


