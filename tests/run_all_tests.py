"""Run the audit-fix tests in this folder (not the older repo tests, which need a live Databricks workspace) with the current Python. Needs: langgraph==0.2.62, langchain-core==0.3.29,
flask, databricks-sdk, requests, mlflow-skinny. No network, no Databricks access, no writes.

    python tests/run_all_tests.py
"""
import os, subprocess, sys
here = os.path.dirname(os.path.abspath(__file__))
env = dict(os.environ, MLFLOW_DISABLE_AGENT_HINT="1", PYTHONIOENCODING="utf-8")
bad = []
MY_TESTS = ['test_callback_dedupe.py', 'test_callback_errors_and_timing.py', 'test_callback_latency.py', 'test_gateway_client_request_id.py', 'test_sync_gateway_policies.py', 'test_tracer_runs_node_once.py', 'test_e2e_blocked_request.py']
for name in MY_TESTS:
    r = subprocess.run([sys.executable, os.path.join(here, name)], capture_output=True, text=True, env=env, encoding="utf-8")
    out = r.stdout
    # tests print RESULT lines; the e2e test prints a table instead and is judged by its exit code and content
    verdict = "ok" if r.returncode == 0 and ("FAIL" not in out) and ("DUPLICATES" not in out) else "FAILED"
    if name == "test_e2e_blocked_request.py":
        verdict = "ok" if ("BLOCKED request: gateway HTTP calls = 1; audit step rows = 4" in out
                           and "ALLOWED request: gateway HTTP calls = 1; audit step rows = 4" in out) else "FAILED"
    print(f"{verdict:7} {name}")
    if verdict != "ok":
        bad.append(name); print(out[-1500:], r.stderr[-1500:])
print("\nALL TESTS PASSED" if not bad else f"\nFAILED: {bad}")
sys.exit(1 if bad else 0)

