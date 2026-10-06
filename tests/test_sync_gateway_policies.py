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
sp = {"gw_request_id": "gw-1", "otel_trace_id": "o-1", "start_ms": 1790000000123, "events": events}
c = call(9, 0, "blocked", policy="block-unsafe-content")
g_rows, n_rows, lines = sg.build_rows(c, sp, "time+policy", 219, now_iso="2026-10-06 00:00:00.000000")
check("12 events -> 12 guardrail rows", len(g_rows) == 12)
check("12 events -> 6 node rows (one per policy)", len(n_rows) == 6)
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
check("blocked node row: parent link, type, message with masked reason",
      blocked_node["parent_node_id"] == "call-9" and blocked_node["node_type"] == "guardrail"
      and blocked_node["node_name"] == "gateway_policy:block-unsafe-content"
      and "Nupur" not in blocked_node["error_message"] and blocked_node["error_message"].startswith("Blocked by policy: block-unsafe-content ("))
check("5 passed policies have status success and no error message",
      sum(1 for r in n_rows if r["status"] == "success" and r["error_message"] is None) == 5)
check("display string matches the requested 'policy (reason) | status' form",
      lines[0].startswith("block-unsafe-content (") and lines[0].endswith(") | blocked") and "pii-redaction-policy | passed" in lines)

print("\nRESULT:", "ALL PASSED" if not failures else f"FAILED: {failures}")
sys.exit(1 if failures else 0)
