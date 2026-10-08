"""
Pure-function tests for scripts/sync_gateway_policies.py (no network, no Databricks).
Timings and token counts in the matching tests are the real 4 Oct 2026 values.

    python tests/test_sync_gateway_policies.py
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
sys.path.insert(0, os.path.join(HERE, "..", "scripts"))
import sync_gateway_policies as sg  # noqa: E402

failures = []


def check(name, cond):
    print(("PASS " if cond else "FAIL ") + name)
    if not cond:
        failures.append(name)


# ── mask_reason ───────────────────────────────────────────────────────────
r = sg.mask_reason("Contains personal name 'Nupur' which should be replaced with [NAME].")
check("quoted personal name is masked", "Nupur" not in r and "[REDACTED]" in r)
weap = "Request for weapon-making instructions combined with explicit stated intent to kill someone - violence/weapons content."
check("a reason without personal data is unchanged", sg.mask_reason(weap) == weap)
apos = "Attempt to extract the user's prompt and the developer's notes from the system."
check("apostrophes inside words are not treated as quotes", sg.mask_reason(apos) == apos)
check("an email in a reason is masked", "[EMAIL]" in sg.mask_reason("User shared a@b.com in the message"))
check("empty reason stays None", sg.mask_reason("") is None and sg.mask_reason(None) is None)

# ── parse_policy_events (real event shape) ────────────────────────────────
ev_json = json.dumps([
    {"time_unix_nano": 1, "name": "policy_evaluated", "attributes": {
        "policy.action": "DENY", "policy.handler": "system.ai.block_unsafe_content", "policy.name": "block-unsafe-content",
        "policy.options": json.dumps({"phases": "pre_call,post_call", "dry_run": "false", "max_turns": "1"}),
        "policy.phase": "on_call", "policy.type": "BUILTIN", "policy.reason": weap}},
    {"time_unix_nano": 1, "name": "policy_evaluated", "attributes": {
        "policy.action": "ALLOW", "policy.handler": "system.ai.detect_sensitive_data", "policy.name": "pii-redaction-policy",
        "policy.options": json.dumps({"action": "transform", "dry_run": "false"}),
        "policy.phase": "on_call", "policy.type": "BUILTIN"}},
    {"time_unix_nano": 1, "name": "some_other_event", "attributes": {"policy.name": "ignored"}},
])
evs = sg.parse_policy_events(ev_json)
check("only policy_evaluated events are kept", [e["name"] for e in evs] == ["block-unsafe-content", "pii-redaction-policy"])
check("configured action is read from the policy options", evs[1]["configured_action"] == "transform")
check("bad / empty events do not raise", sg.parse_policy_events("not json") == [] and sg.parse_policy_events(None) == [])

# ── matching: the real weapons pair (two blocked calls 2.1 s apart, same policy) ──
T = 1_000_000  # ms, arbitrary base standing for 10:05:00
ENDPOINT = "system.ai.meta-llama-3-1-8b-instruct"


def call(i, est_offset_ms, status, pt=None, ct=None, policy=None, gid=None):
    return {"node_execution_id": f"call-{i}", "trace_id": "tr", "app_id": "myre_app", "subject_ref": "s",
            "status": status, "model_name": ENDPOINT, "est_start_ms": T + est_offset_ms,
            "prompt_tokens": pt, "completion_tokens": ct, "blocked_by_policy": policy, "gateway_request_id": gid}


def span(gid, offset_ms, action="", policy=None, tin=None, tout=None):
    return {"gw_request_id": gid, "otel_trace_id": "o-" + gid, "start_ms": T + offset_ms, "endpoint": ENDPOINT,
            "in_tokens": tin, "out_tokens": tout, "blocked_policy": policy, "action": action, "events": evs}


calls = [call(1, 10676, "blocked", policy="block-unsafe-content"), call(2, 12946, "blocked", policy="block-unsafe-content")]
spans = [span("03366c49", 10895, "DENY", "block-unsafe-content"), span("a4794b6f", 13039, "DENY", "block-unsafe-content")]
m, un = sg.match_calls(calls, spans, 5000)
pairs = {c["node_execution_id"]: s["gw_request_id"] for c, s, *_ in m}
check("weapons pair: each blocked call gets its own gateway span (nearest in time)", pairs == {"call-1": "03366c49", "call-2": "a4794b6f"})
check("weapons pair: method is time+policy and nothing is unmatched", {x[2] for x in m} == {"time+policy"} and not un)
gaps = sorted(x[3] for x in m)
check("weapons pair: gaps are the real 93 and 219 ms", gaps == [93, 219])

# allowed calls: tokens must match exactly (real black-bags values)
calls = [call(3, 1000, "success", 6566, 90), call(4, 11500, "success", 481, 100)]
spans = [span("ee05b187", 1277, "", None, 6566, 90), span("49143e6c", 11601, "", None, 481, 100)]
m, un = sg.match_calls(calls, spans, 5000)
check("allowed calls match on time + exact tokens", {c["node_execution_id"]: s["gw_request_id"] for c, s, *_ in m} == {"call-3": "ee05b187", "call-4": "49143e6c"})
spans_bad = [span("ee05b187", 1277, "", None, 6566, 91)]
m, un = sg.match_calls([call(3, 1000, "success", 6566, 90)], spans_bad, 5000)
check("a token mismatch is NOT matched", not m and len(un) == 1)
m, un = sg.match_calls([call(3, 1000, "success", 6566, 90)], [span("x", 60000, "", None, 6566, 90)], 5000)
check("outside the time tolerance is NOT matched", not m and len(un) == 1)
m, un = sg.match_calls([call(5, 1000, "error")], [span("x", 1100, "", None, None, None)], 5000)
check("an errored call is left unmatched", not m and len(un) == 1)

# identical token counts for two calls close together: still one-to-one by time
calls = [call(6, 0, "success", 100, 10), call(7, 800, "success", 100, 10)]
spans = [span("a", 120, "", None, 100, 10), span("b", 930, "", None, 100, 10)]
m, un = sg.match_calls(calls, spans, 5000)
check("identical signatures resolve one-to-one by nearest time", {c["node_execution_id"]: s["gw_request_id"] for c, s, *_ in m} == {"call-6": "a", "call-7": "b"})

# exact join wins over time
calls = [call(8, 0, "success", 100, 10, gid="exact-id")]
spans = [span("near", 50, "", None, 100, 10), span("exact-id", 3000, "", None, 100, 10)]
m, un = sg.match_calls(calls, spans, 5000)
check("gateway_request_id on the audit row is used when present", m[0][1]["gw_request_id"] == "exact-id" and m[0][2] == "request_id")

# ── build_rows: 6 policies x 2 phases, one DENY ───────────────────────────
def ev(name, action, phase, reason=None, cfg=None):
    return {"name": name, "action": action, "phase": phase, "handler": "h", "type": "BUILTIN",
            "reason": reason, "configured_action": cfg, "dry_run": "false"}


policies = ["block-unsafe-content", "pii-redaction-policy", "hallucination-guard", "block-jailbreak", "off-topic-restriction", "spam-abuse-detection"]
events = [ev(p, "ALLOW", ph, cfg=("transform" if p == "pii-redaction-policy" else None)) for ph in ("on_call", "on_result") for p in policies]
events[0] = ev("block-unsafe-content", "DENY", "on_call", "Contains personal name 'Nupur' which should be replaced with [NAME].")
sp = {"gw_request_id": "gw-1", "otel_trace_id": "o-1", "root_span_id": "root", "start_ms": 1790000000123,
      "gw_latency_ms": 6800, "ttfb_ms": 6700, "events": events}
c = call(9, 0, "blocked", policy="block-unsafe-content")
g_rows, n_rows, lines = sg.build_rows(c, sp, [], "time+policy", 219, now_iso="2026-10-06 00:00:00.000000")
check("12 events -> 12 guardrail rows", len(g_rows) == 12)
check("12 events -> 6 policy node rows (one per policy, both phases summarised)",
      len(n_rows) == 6 and all(r["node_name"].startswith("gateway_policy:") for r in n_rows))
deny = next(r for r in g_rows if r["triggered_block"] == "true")
check("DENY row: name, result, triggered_block", deny["policy_name"] == "block-unsafe-content" and deny["result"] == "deny_on_call")
check("no fake score is written", all("score" not in r for r in g_rows))
meta = json.loads(deny["guardrail_metadata"])
check("metadata links to the gateway call and request", meta["node_execution_id"] == "call-9" and meta["gateway_request_id"] == "gw-1")
check("reason in metadata is masked", "Nupur" not in deny["guardrail_metadata"] and "[REDACTED]" in meta["reason"])
check("checked_at is the gateway span time, created_at is the job time",
      deny["checked_at"].startswith("2026-09-") or deny["checked_at"].startswith("2026-10-")
      and deny["created_at"] == "2026-10-06 00:00:00.000000")
pii = next(r for r in g_rows if r["policy_name"] == "pii-redaction-policy")
check("configured action (transform) is kept for the PII policy", json.loads(pii["guardrail_metadata"])["configured_action"] == "transform")
blocked_node = next(r for r in n_rows if r["status"] == "blocked")
check("blocked policy row: parent link, type, summary, message with masked reason",
      blocked_node["parent_node_id"] == "call-9" and blocked_node["node_type"] == "guardrail"
      and blocked_node["node_name"] == "gateway_policy:block-unsafe-content"
      and blocked_node["output_summary"] == "DENY before call; ALLOW after call"
      and "Nupur" not in blocked_node["error_message"]
      and blocked_node["error_message"].startswith("Blocked by policy: block-unsafe-content ("))
passed_nodes = [r for r in n_rows if r["status"] == "success"]
check("5 passed policies are success, no error message, and show both phases",
      len(passed_nodes) == 5 and all(r["error_message"] is None and r["output_summary"] == "ALLOW before call; ALLOW after call" for r in passed_nodes))
pmeta = json.loads(passed_nodes[0]["node_metadata"])
check("policy node row points at the detail rows and keeps request id and configured action",
      "guardrail_results_raw" in pmeta["detail"] and pmeta["gateway_request_id"] == "gw-1" and set(pmeta["phases"]) == {"on_call", "on_result"})
check("display string matches the requested 'policy (reason) | status' form",
      lines[0].startswith("block-unsafe-content (") and lines[0].endswith(") | blocked") and "pii-redaction-policy | passed" in lines)

# ── judge calls and the model call become child node rows ─────────────────
S = 1_790_000_000_000_000_000   # ns, arbitrary base
BASE = {"trace_id": "t", "app_id": "a", "subject_ref": "s", "is_erasure_flag": "false", "created_at": "x", "schema_version": "1.0"}


def child(span_id, parent, model, start_s, dur_s, tin, tout, outcome="success", http=200):
    return sg.prepare_child_span({
        "otel_trace_id": "o-1", "span_id": span_id, "parent_span_id": parent, "name": "system.ai." + model,
        "model": model, "start_ns": str(S + int(start_s * 1e9)), "end_ns": str(S + int((start_s + dur_s) * 1e9)),
        "in_tokens": str(tin), "out_tokens": str(tout), "http_status": str(http), "outcome": outcome})


# real shape of an allowed call: 4 judges before the model, 3 after (judge spans hang under their own request spans)
kids = ([child(f"j{i}", f"jr{i}", "claude-sonnet-5", i * 1.2, 1.1, 700 + i, 20) for i in range(4)]
        + [child("m", "root", "meta_llama_v3_1_8b_instruct", 5.0, 6.2, 22, 60)]
        + [child(f"p{i}", f"pr{i}", "claude-sonnet-5", 11.5 + i * 1.2, 1.1, 650 + i, 25) for i in range(3)])
allowed = {"gw_request_id": "gw-2", "otel_trace_id": "o-1", "root_span_id": "root", "start_ms": 1790000000123,
           "gw_latency_ms": 16800, "ttfb_ms": 16700, "events": events}
c = call(10, 0, "success", 22, 60)
g_rows, n_rows, lines = sg.build_rows(c, allowed, kids, "request_id", 40, now_iso="2026-10-06 00:00:00.000000")
check("allowed call: 12 guardrail rows + 14 node rows (6 policies, 7 judges, 1 model)", len(g_rows) == 12 and len(n_rows) == 14)
model_rows = [r for r in n_rows if r["node_name"] == "gateway_model_call"]
judge_rows = [r for r in n_rows if r["node_name"] == "gateway_policy_judge"]
check("exactly one model row and seven judge rows", len(model_rows) == 1 and len(judge_rows) == 7)
check("every child points at the gateway_llm_call row", all(r["parent_node_id"] == "call-10" for r in n_rows))
mrow = model_rows[0]
mmeta = json.loads(mrow["node_metadata"])
check("model row: type llm, provider model, tokens, model-only time",
      mrow["node_type"] == "llm" and mrow["model_name"] == "meta_llama_v3_1_8b_instruct"
      and mrow["tokens_used"] == 82 and mrow["latency_ms"] == 6200)
check("model row keeps the gateway's own total time and time to first byte",
      mmeta["gateway_latency_ms"] == 16800 and mmeta["time_to_first_byte_ms"] == 16700 and mmeta["role"] == "model")
phases = [json.loads(r["node_metadata"])["phase"] for r in sorted(judge_rows, key=lambda r: r["node_order"])]
check("judges split 4 before and 3 after the model call, in run order", phases == ["pre_call"] * 4 + ["post_call"] * 3)
check("node_order numbers the judge and model rows 1..8 in the order they ran",
      sorted(r["node_order"] for r in model_rows + judge_rows) == list(range(1, 9)) and model_rows[0]["node_order"] == 5)
check("judge rows: type guardrail, claude model, tokens, and no policy is named",
      all(r["node_type"] == "guardrail" and r["model_name"] == "claude-sonnet-5" and r["tokens_used"] > 0 for r in judge_rows)
      and all("policy_name" not in json.loads(r["node_metadata"]) for r in judge_rows))
check("judge and model rows carry the request id and OTEL trace id for joining",
      all(json.loads(r["node_metadata"])["gateway_request_id"] == "gw-2" and json.loads(r["node_metadata"])["otel_trace_id"] == "o-1" for r in model_rows + judge_rows))
failed_judge, _ = sg.build_child_rows(c, allowed, [child("j", "jr", "claude-sonnet-5", 0, 1, 10, 1, outcome="error", http=500)], BASE)
check("a failed judge call is logged as error with its outcome", failed_judge[0]["status"] == "error" and "outcome=error" in failed_judge[0]["error_message"])
check("lines list the policies, then the model call and the 7 judge calls",
      any(l.startswith("model call") for l in lines) and sum(l.startswith("judge") for l in lines) == 7)

# a call blocked before it reached the model: judges only, no model row
pre_block = [child("j0", "jr0", "claude-sonnet-5", 0, 1.0, 700, 20), child("j1", "jr1", "claude-sonnet-5", 1.2, 1.0, 701, 20)]
_, n_rows, _ = sg.build_rows(call(11, 0, "blocked", policy="block-jailbreak"), sp, pre_block, "request_id", 5)
kids_only = [r for r in n_rows if not r["node_name"].startswith("gateway_policy:")]
check("blocked pre-call: judge rows only (all pre_call) next to the policy rows, no model row",
      len(kids_only) == 2 and all(r["node_name"] == "gateway_policy_judge" and json.loads(r["node_metadata"])["phase"] == "pre_call" for r in kids_only)
      and len(n_rows) == 8)

# model call found by name when the root span id is not available
nr = {"gw_request_id": "gw-3", "otel_trace_id": "o-1", "start_ms": 1, "events": []}
_, n_rows, _ = sg.build_rows(call(12, 0, "success", 22, 60), nr, kids, "request_id", 0)
check("without root_span_id the model call is found by model name", sum(r["node_name"] == "gateway_model_call" for r in n_rows) == 1)

# ── batched inserts ───────────────────────────────────────────────────────
sent = []
sg.run_sql = lambda w, wh, statement, parameters=None: sent.append((statement, parameters))
rows = [{"guardrail_id": f"g{i}", "trace_id": "t", "policy_name": "p", "score": None, "guardrail_metadata": "{}"} for i in range(5)]
n = sg.insert_rows(None, "wh", "cat.raw_logs.guardrail_results_raw", rows)
check("5 rows with the same columns go in one statement", n == 1 and len(sent) == 1)
stmt, params = sent[0]
check("one VALUES group per row, parameters named per row, NULL columns left out",
      stmt.count("(:") == 5 and "score" not in stmt and params[0].name == "guardrail_id_0"
      and params[-1].name == "guardrail_metadata_4" and len(params) == 20)
sent.clear()
many = [{"a": str(i), "b": "x", "c": "y", "d": "z"} for i in range(120)]
n = sg.insert_rows(None, "wh", "t", many, max_params=200)
check("a statement never carries more than 200 values (120 rows x 4 columns -> 3 statements)",
      n == 3 and all(len(p) <= 200 for _, p in sent) and sum(len(p) for _, p in sent) == 480)
sent.clear()
sg.insert_rows(None, "wh", "t", [{"a": "1", "b": "2"}, {"a": "1"}])
check("rows with different columns are never mixed in one statement", len(sent) == 2)

# ── idempotency query also looks at child rows ────────────────────────────
sent.clear()
sg.fetch_audit_calls(None, "wh", "shopping_assistant", "2026-10-01")
check("already-synced calls are skipped when guardrail rows OR child rows exist",
      "guardrail_results_raw" in sent[0][0] and "gateway_policy_judge" in sent[0][0]
      and "gateway_model_call" in sent[0][0] and "gateway_policy:%" in sent[0][0])
sent.clear()
sg.fetch_gateway_children(None, "wh", "xponent_prod.`xponent-creds`.unity_gateway_otel_spans", ["o-1", "o-2"], 1, 2)
check("children are read only for the matched traces, CLIENT spans only",
      "SPAN_KIND_CLIENT" in sent[0][0] and "'o-1', 'o-2'" in sent[0][0])

print("\nRESULT:", "ALL PASSED" if not failures else f"FAILED: {failures}")
sys.exit(1 if failures else 0)
