"""
sync_gateway_policies.py

Copies the AI Gateway's policy decisions into the audit tables.

The gateway records every policy check (name, action, phase, reason) as `policy_evaluated`
events on its own trace table. The app never receives that detail in the HTTP response, so a
separate step has to copy it. This job does that, after the request, for every
`gateway_llm_call:*` row in node_executions_raw that has not been synced yet:

  1. match the audit row to its gateway span
       - exact, by gateway request id, when the audit row carries `gateway_request_id`
       - otherwise by start time (+/- tolerance) AND token counts (allowed calls) or
         blocking policy name (blocked calls), one gateway span per audit row
  2. write one row per policy and phase into guardrail_results_raw, with the detail
     (reason, handler, configured action, gateway request id, node_execution_id) in
     guardrail_metadata
  3. write child rows into node_executions_raw (parent_node_id = the gateway_llm_call row), so
     the step tree shows  node -> gateway call -> policies, judge calls, model call:
       - one row per policy (node_name "gateway_policy:<name>", node_type "guardrail"): the
         high-level result (blocked / success) with both phases summarised. The detail, one row
         per policy and phase, is in guardrail_results_raw under the same trace_id.
       - one row per Claude judge call  (node_name "gateway_policy_judge", node_type "guardrail"):
         model, tokens, time, outcome, and the phase (pre_call / post_call) worked out from
         whether it started before or after the model call. The gateway does not say which
         policy a judge call belongs to, so none is named.
       - one row for the model call itself (node_name "gateway_model_call", node_type "llm"):
         provider model, tokens, model-only time, and the gateway's own total time and time to
         first byte. Absent for a call blocked before it reached the model.

Nothing is updated or deleted. Rows are only inserted, in batches (several rows per statement).
A run is idempotent: calls that already have guardrail rows or child rows pointing at their
node_execution_id are skipped.

DRY RUN BY DEFAULT. Pass --write to insert.

    python scripts/sync_gateway_policies.py --profile <profile> --since 2026-10-04
    python scripts/sync_gateway_policies.py --profile <profile> --since 2026-10-04 --write
"""
import argparse
import json
import os
import re
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from audit_wrapper import _redact_pii, _ts_to_iso, _now_iso  # noqa: E402

GATEWAY_TABLE = "xponent_prod.`xponent-creds`.unity_gateway_otel_spans"
SCHEMA_VERSION = "1.0"

# ── reason masking ────────────────────────────────────────────────────────
# A gateway reason can quote the user's text ("Contains personal name 'Nupur' ...").
# _redact_pii only recognises phrases like "I am <Name>", so quoted tokens are masked first.
# An opening quote must not follow a letter, so apostrophes in "user's" are left alone.
_QUOTED = re.compile(r"""(?<![A-Za-z])(['"])(?=\S)(.{1,60}?)(?<=\S)\1(?![A-Za-z])""")


def mask_reason(text):
    if not text:
        return None
    masked = _QUOTED.sub(lambda m: f"{m.group(1)}[REDACTED]{m.group(1)}", str(text))
    return _redact_pii(masked)[:1000]


# ── parsing ───────────────────────────────────────────────────────────────
def parse_policy_events(events_json):
    """Return a list of dicts, one per policy_evaluated event."""
    out = []
    try:
        events = json.loads(events_json) if events_json else []
    except Exception:
        return out
    for ev in events or []:
        if ev.get("name") != "policy_evaluated":
            continue
        a = ev.get("attributes") or {}
        try:
            options = json.loads(a.get("policy.options") or "{}")
        except Exception:
            options = {}
        out.append({
            "name":       a.get("policy.name"),
            "action":     (a.get("policy.action") or "").upper(),
            "phase":      a.get("policy.phase"),
            "handler":    a.get("policy.handler"),
            "type":       a.get("policy.type"),
            "reason":     a.get("policy.reason") or None,
            "configured_action": options.get("action"),
            "dry_run":    options.get("dry_run"),
        })
    return [e for e in out if e["name"]]


def _int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def prepare_audit_call(row):
    """Normalise one node_executions_raw row (gateway_llm_call:*) for matching."""
    try:
        meta = json.loads(row.get("node_metadata") or "{}")
    except Exception:
        meta = {}
    created_ms = _int(row.get("created_ms"))
    latency = _int(row.get("latency_ms")) or 0
    return {
        "node_execution_id": row["node_execution_id"],
        "trace_id":          row["trace_id"],
        "app_id":            row.get("app_id"),
        "subject_ref":       row.get("subject_ref"),
        "status":            row.get("status"),
        "model_name":        row.get("model_name"),
        "est_start_ms":      (created_ms - latency) if created_ms is not None else None,
        "prompt_tokens":     _int(meta.get("prompt_tokens")),
        "completion_tokens": _int(meta.get("completion_tokens")),
        "blocked_by_policy": meta.get("blocked_by_policy"),
        "gateway_request_id": meta.get("gateway_request_id"),
    }


def prepare_gateway_span(row):
    return {
        "gw_request_id": row.get("gw_request_id"),
        "otel_trace_id": row.get("otel_trace_id"),
        "root_span_id":  row.get("root_span_id"),
        "start_ms":      _int(row.get("start_ms")),
        "endpoint":      row.get("endpoint"),
        "in_tokens":     _int(row.get("in_tokens")),
        "out_tokens":    _int(row.get("out_tokens")),
        "gw_latency_ms": _int(row.get("gw_latency_ms")),
        "ttfb_ms":       _int(row.get("ttfb_ms")),
        "blocked_policy": row.get("blocked_policy"),
        "action":        (row.get("action") or "").upper(),
        "events":        parse_policy_events(row.get("events_json")),
    }


def prepare_child_span(row):
    """One CLIENT span of a gateway call: a Claude judge call or the model call itself."""
    start_ns, end_ns = _int(row.get("start_ns")), _int(row.get("end_ns"))
    return {
        "otel_trace_id": row.get("otel_trace_id"),
        "span_id":       row.get("span_id"),
        "parent_span_id": row.get("parent_span_id"),
        "name":          row.get("name"),
        "model":         row.get("model"),
        "start_ns":      start_ns,
        "end_ns":        end_ns,
        "duration_ms":   round((end_ns - start_ns) / 1e6) if start_ns is not None and end_ns is not None else None,
        "in_tokens":     _int(row.get("in_tokens")),
        "out_tokens":    _int(row.get("out_tokens")),
        "http_status":   _int(row.get("http_status")),
        "outcome":       row.get("outcome"),
    }


# ── matching ──────────────────────────────────────────────────────────────
def _compatible(call, span):
    if call["status"] == "blocked":
        return span["action"] == "DENY" and span["blocked_policy"] == call["blocked_by_policy"]
    if call["status"] == "success":
        return (span["action"] != "DENY"
                and span["in_tokens"] == call["prompt_tokens"]
                and span["out_tokens"] == call["completion_tokens"])
    return False  # errored calls have no usable signature


def match_calls(calls, spans, tolerance_ms=5000):
    """
    One gateway span per audit call.
    Returns (matches, unmatched) where matches = [(call, span, method, gap_ms, n_candidates)].
    """
    by_id = {s["gw_request_id"]: s for s in spans if s["gw_request_id"]}
    matches, used_spans, used_calls = [], set(), set()

    # 1. exact: gateway request id recorded on the audit row
    for c in calls:
        gid = c.get("gateway_request_id")
        if gid and gid in by_id and gid not in used_spans:
            s = by_id[gid]
            gap = abs(c["est_start_ms"] - s["start_ms"]) if c["est_start_ms"] and s["start_ms"] else None
            matches.append((c, s, "request_id", gap, 1))
            used_spans.add(gid)
            used_calls.add(c["node_execution_id"])

    # 2. fallback: time + token counts / blocking policy, nearest first, one-to-one
    pairs, cand_count = [], {}
    for c in calls:
        if c["node_execution_id"] in used_calls or c["est_start_ms"] is None:
            continue
        n = 0
        for s in spans:
            if s["gw_request_id"] in used_spans or s["start_ms"] is None:
                continue
            if s["endpoint"] and c["model_name"] and s["endpoint"] != c["model_name"]:
                continue
            gap = abs(c["est_start_ms"] - s["start_ms"])
            if gap <= tolerance_ms and _compatible(c, s):
                pairs.append((gap, c["node_execution_id"], c, s))
                n += 1
        cand_count[c["node_execution_id"]] = n
    for gap, _, c, s in sorted(pairs, key=lambda p: (p[0], p[1])):
        if c["node_execution_id"] in used_calls or s["gw_request_id"] in used_spans:
            continue
        method = "time+policy" if c["status"] == "blocked" else "time+tokens"
        matches.append((c, s, method, gap, cand_count.get(c["node_execution_id"], 1)))
        used_calls.add(c["node_execution_id"])
        used_spans.add(s["gw_request_id"])

    unmatched = [c for c in calls if c["node_execution_id"] not in used_calls]
    return matches, unmatched


# ── row building ──────────────────────────────────────────────────────────
def display_string(policy_name, action, reason):
    status = "blocked" if action == "DENY" else "passed"
    return f"{policy_name} ({reason}) | {status}" if reason else f"{policy_name} | {status}"


_PHASE_LABEL = {"on_call": "before call", "on_result": "after call"}


def _norm(s):
    """Compare model names ignoring punctuation and a 'v' before a digit:
    endpoint 'meta-llama-3-1-8b-instruct' vs provider model 'meta_llama_v3_1_8b_instruct'."""
    return re.sub(r"v(?=\d)", "", re.sub(r"[^a-z0-9]", "", (s or "").lower()))


def _ns_iso(ns):
    return _ts_to_iso(ns / 1e9) if ns else None


def _is_model_call(child, span, call):
    """The model call is the CLIENT span hanging directly under the call's request span; every
    other CLIENT span in the trace is a Claude judge call. Falls back to the model name."""
    if span.get("root_span_id"):
        return child["parent_span_id"] == span["root_span_id"]
    tail = _norm((call.get("model_name") or "").split(".")[-1])
    return bool(tail) and tail == _norm(child["model"])


def _tokens(ch):
    if ch["in_tokens"] is None and ch["out_tokens"] is None:
        return None
    return (ch["in_tokens"] or 0) + (ch["out_tokens"] or 0)


def build_child_rows(call, span, children, base):
    """Return (node_rows, display_lines): one row per judge call and one for the model call,
    numbered in the order they ran, all children of the gateway_llm_call row."""
    model = next((c for c in children if _is_model_call(c, span, call)), None)
    ordered = sorted(children, key=lambda c: c["start_ns"] or 0)
    rows, lines, judge_n = [], [], 0
    for order, ch in enumerate(ordered, 1):
        is_model = ch is model
        ok = ch["outcome"] == "success" and ch["http_status"] in (None, 200)
        meta = {"source": "gateway_trace", "gateway_request_id": span["gw_request_id"],
                "otel_trace_id": span["otel_trace_id"], "span_id": ch["span_id"],
                "prompt_tokens": ch["in_tokens"], "completion_tokens": ch["out_tokens"],
                "outcome": ch["outcome"], "http_status": ch["http_status"]}
        if is_model:
            meta.update(role="model", provider_model=ch["model"],
                        gateway_latency_ms=span.get("gw_latency_ms"), time_to_first_byte_ms=span.get("ttfb_ms"))
            name, ntype = "gateway_model_call", "llm"
            lines.append(f"model call  {ch['model']}  {ch['duration_ms']} ms  {ch['in_tokens']} in / {ch['out_tokens']} out")
        else:
            judge_n += 1
            after = model is not None and (ch["start_ns"] or 0) >= (model["start_ns"] or 0)
            phase = "post_call" if after else "pre_call"
            meta.update(role="policy_judge", judge_order=judge_n, phase=phase,
                        note="the gateway does not name the policy this judge call belongs to")
            name, ntype = "gateway_policy_judge", "guardrail"
            lines.append(f"judge {judge_n} ({phase})  {ch['model']}  {ch['duration_ms']} ms  {ch['in_tokens']} in / {ch['out_tokens']} out")
        rows.append({
            **base,
            "node_execution_id": str(uuid.uuid4()),
            "node_name":      name,
            "node_type":      ntype,
            "node_order":     order,
            "parent_node_id": call["node_execution_id"],
            "status":         "success" if ok else "error",
            "error_message":  None if ok else f"outcome={ch['outcome']} http={ch['http_status']}",
            "model_name":     ch["model"],
            "tokens_used":    _tokens(ch),
            "latency_ms":     ch["duration_ms"],
            "started_at":     _ns_iso(ch["start_ns"]),
            "ended_at":       _ns_iso(ch["end_ns"]),
            "node_metadata":  json.dumps(meta),
        })
    return rows, lines


def build_rows(call, span, children, method, gap_ms, now_iso=None):
    """Return (guardrail_rows, node_rows, display_lines) for one matched call.
    guardrail_rows: one per policy and phase. node_rows: the judge calls and the model call."""
    now = now_iso or _now_iso()
    checked_at = _ts_to_iso(span["start_ms"] / 1000.0)
    base = {"trace_id": call["trace_id"], "app_id": call["app_id"], "subject_ref": call["subject_ref"],
            "is_erasure_flag": "false", "created_at": now, "schema_version": SCHEMA_VERSION}
    guardrail_rows, per_policy = [], {}
    for ev in span["events"]:
        reason = mask_reason(ev["reason"])
        meta = {
            "source": "gateway_trace",
            "gateway_request_id": span["gw_request_id"],
            "otel_trace_id": span["otel_trace_id"],
            "node_execution_id": call["node_execution_id"],
            "match_method": method,
            "match_gap_ms": gap_ms,
            "phase": ev["phase"], "handler": ev["handler"], "policy_type": ev["type"],
            "configured_action": ev["configured_action"], "dry_run": ev["dry_run"],
            "reason": reason,
        }
        guardrail_rows.append({
            **base,
            "guardrail_id":    str(uuid.uuid4()),
            "policy_name":     ev["name"],
            "result":          f"{ev['action'].lower()}_{ev['phase']}",
            "triggered_block": "true" if ev["action"] == "DENY" else "false",
            "checked_at":      checked_at,
            "guardrail_metadata": json.dumps(meta),
        })
        per_policy.setdefault(ev["name"], []).append((ev, reason))

    # High-level view in the step tree: one row per policy (both phases summarised). The detail,
    # one row per policy and phase, stays in guardrail_results_raw under the same trace_id.
    policy_rows, lines = [], []
    for name, evs in per_policy.items():
        denied = [(e, r) for e, r in evs if e["action"] == "DENY"]
        reason = denied[0][1] if denied else None
        action = "DENY" if denied else "ALLOW"
        policy_rows.append({
            **base,
            "node_execution_id": str(uuid.uuid4()),
            "node_name":      f"gateway_policy:{name}",
            "node_type":      "guardrail",
            "parent_node_id": call["node_execution_id"],
            "status":         "blocked" if denied else "success",
            "output_summary": "; ".join(f"{e['action']} {_PHASE_LABEL.get(e['phase'], e['phase'])}" for e, _ in evs),
            "error_message":  (f"Blocked by policy: {name}" + (f" ({reason})" if reason else "")) if denied else None,
            "started_at":     checked_at,
            "ended_at":       checked_at,
            "node_metadata":  json.dumps({
                "source": "gateway_trace",
                "gateway_request_id": span["gw_request_id"],
                "phases": {e["phase"]: e["action"] for e, _ in evs},
                "configured_action": evs[0][0]["configured_action"],
                "handler": evs[0][0]["handler"],
                "detail": "guardrail_results_raw, same trace_id, policy_name and phase",
            }),
        })
        lines.append(display_string(name, action, reason))

    child_rows, child_lines = build_child_rows(call, span, children or [], base)
    return guardrail_rows, policy_rows + child_rows, lines + child_lines


# ── SQL access ────────────────────────────────────────────────────────────
def run_sql(w, warehouse_id, statement, parameters=None):
    res = w.statement_execution.execute_statement(
        warehouse_id=warehouse_id, statement=statement, parameters=parameters, wait_timeout="50s")
    if res.status.error:
        raise RuntimeError(res.status.error.message)
    cols = [c.name for c in res.manifest.schema.columns] if res.manifest else []
    return [dict(zip(cols, r)) for r in (res.result.data_array or [])] if res.result else []


def fetch_audit_calls(w, wh, catalog, since):
    return run_sql(w, wh, f"""
        SELECT n.node_execution_id, n.trace_id, n.app_id, n.subject_ref, n.status, n.model_name,
               n.latency_ms, unix_millis(n.created_at) AS created_ms, n.node_metadata
        FROM {catalog}.raw_logs.node_executions_raw n
        WHERE n.node_name LIKE 'gateway_llm_call:%' AND n.created_at >= '{since}'
          AND NOT EXISTS (
              SELECT 1 FROM {catalog}.raw_logs.guardrail_results_raw g
              WHERE g.trace_id = n.trace_id
                AND g.guardrail_metadata LIKE concat('%', n.node_execution_id, '%'))
          AND NOT EXISTS (
              SELECT 1 FROM {catalog}.raw_logs.node_executions_raw c
              WHERE c.trace_id = n.trace_id AND c.parent_node_id = n.node_execution_id
                AND (c.node_name IN ('gateway_policy_judge', 'gateway_model_call')
                     OR c.node_name LIKE 'gateway_policy:%'))
        ORDER BY n.created_at""")


def fetch_gateway_spans(w, wh, gateway_table, lo_ms, hi_ms, endpoints, agent_id=None):
    ep = ", ".join("'" + e.replace("'", "''") + "'" for e in endpoints)
    agent = f"AND attributes:['gen_ai.agent.id']::string = '{agent_id}'" if agent_id else ""
    return run_sql(w, wh, f"""
        SELECT attributes:['databricks.request_id']::string AS gw_request_id, trace_id AS otel_trace_id,
               span_id AS root_span_id,
               unix_millis(time) AS start_ms, attributes:['databricks.endpoint_name']::string AS endpoint,
               attributes:['gen_ai.usage.input_tokens']::string AS in_tokens,
               attributes:['gen_ai.usage.output_tokens']::string AS out_tokens,
               attributes:['databricks.latency_ms']::string AS gw_latency_ms,
               attributes:['databricks.time_to_first_byte_ms']::string AS ttfb_ms,
               attributes:['databricks.policy.name']::string AS blocked_policy,
               attributes:['databricks.policy.action']::string AS action,
               to_json(events) AS events_json
        FROM {gateway_table}
        WHERE name LIKE 'ai-gateway%'
          AND unix_millis(time) BETWEEN {lo_ms} AND {hi_ms}
          AND attributes:['databricks.endpoint_name']::string IN ({ep}) {agent}""")


def fetch_gateway_children(w, wh, gateway_table, trace_ids, lo_ms, hi_ms):
    """CLIENT spans (Claude judge calls and the model call) of the given gateway traces."""
    ids = ", ".join("'" + t.replace("'", "''") + "'" for t in trace_ids)
    return run_sql(w, wh, f"""
        SELECT trace_id AS otel_trace_id, span_id, parent_span_id, name,
               start_time_unix_nano AS start_ns, end_time_unix_nano AS end_ns,
               attributes:['gen_ai.request.model']::string AS model,
               attributes:['gen_ai.usage.input_tokens']::string AS in_tokens,
               attributes:['gen_ai.usage.output_tokens']::string AS out_tokens,
               attributes:['http.response.status_code']::string AS http_status,
               attributes:['databricks.outcome']::string AS outcome
        FROM {gateway_table}
        WHERE kind = 'SPAN_KIND_CLIENT' AND trace_id IN ({ids})
          AND unix_millis(time) BETWEEN {lo_ms} AND {hi_ms}""")


MAX_PARAMS = 200   # one statement takes at most 256 parameters


def insert_rows(w, wh, table, rows, max_params=MAX_PARAMS):
    """Insert rows in batches: several rows per INSERT statement (rows with the same columns are
    grouped, and a statement never carries more than max_params values)."""
    from databricks.sdk.service.sql import StatementParameterListItem
    groups = {}
    for r in rows:
        cols = tuple(c for c, v in r.items() if v is not None and v != "")
        groups.setdefault(cols, []).append(r)
    statements = 0
    for cols, grp in groups.items():
        per = max(1, max_params // len(cols))
        for i in range(0, len(grp), per):
            chunk = grp[i:i + per]
            values = ", ".join("(" + ", ".join(f":{c}_{j}" for c in cols) + ")" for j in range(len(chunk)))
            sql = f"INSERT INTO {table} ({', '.join(cols)}) VALUES {values}"
            params = [StatementParameterListItem(name=f"{c}_{j}", value=str(row[c]))
                      for j, row in enumerate(chunk) for c in cols]
            run_sql(w, wh, sql, params)
            statements += 1
    return statements


# ── main ──────────────────────────────────────────────────────────────────
def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--profile", help="Databricks CLI profile (never defaulted)", required=True)
    ap.add_argument("--catalog", default=os.getenv("AUDIT_CATALOG", "shopping_assistant"))
    ap.add_argument("--warehouse-id", default=os.getenv("DATABRICKS_SQL_WAREHOUSE_ID"))
    ap.add_argument("--gateway-table", default=GATEWAY_TABLE)
    ap.add_argument("--agent-id", help="only gateway spans with this gen_ai.agent.id (the app's OAuth integration)")
    ap.add_argument("--since", help="ISO date/time; default: last 48 hours")
    ap.add_argument("--tolerance-ms", type=int, default=5000)
    ap.add_argument("--limit", type=int, default=500, help="max audit calls handled in one run")
    ap.add_argument("--write", action="store_true", help="insert rows (default is a dry run)")
    a = ap.parse_args(argv)
    if not a.warehouse_id:
        sys.exit("Set --warehouse-id or DATABRICKS_SQL_WAREHOUSE_ID")

    from databricks.sdk import WorkspaceClient
    w = WorkspaceClient(profile=a.profile)
    since = a.since or (datetime.now(timezone.utc) - timedelta(hours=48)).strftime("%Y-%m-%d %H:%M:%S")

    calls = [prepare_audit_call(r) for r in fetch_audit_calls(w, a.warehouse_id, a.catalog, since)][: a.limit]
    print(f"audit gateway_llm_call rows not yet synced since {since}: {len(calls)}")
    if not calls:
        return 0
    times = [c["est_start_ms"] for c in calls if c["est_start_ms"] is not None]
    lo, hi = min(times) - a.tolerance_ms - 60000, max(times) + a.tolerance_ms + 60000
    endpoints = sorted({c["model_name"] for c in calls if c["model_name"]})
    spans = [prepare_gateway_span(r) for r in fetch_gateway_spans(
        w, a.warehouse_id, a.gateway_table, lo, hi, endpoints, a.agent_id)]
    print(f"gateway spans fetched for matching: {len(spans)}")

    matches, unmatched = match_calls(calls, spans, a.tolerance_ms)
    print(f"matched: {len(matches)}   unmatched: {len(unmatched)}\n")

    children_by_trace = {}
    trace_ids = sorted({s["otel_trace_id"] for _, s, *_ in matches if s["otel_trace_id"]})
    if trace_ids:
        for r in fetch_gateway_children(w, a.warehouse_id, a.gateway_table, trace_ids, lo, hi):
            ch = prepare_child_span(r)
            children_by_trace.setdefault(ch["otel_trace_id"], []).append(ch)

    all_g, all_n = [], []
    for c, s, method, gap, ncand in sorted(matches, key=lambda m: m[0]["est_start_ms"] or 0):
        g_rows, n_rows, lines = build_rows(c, s, children_by_trace.get(s["otel_trace_id"], []), method, gap)
        all_g += g_rows; all_n += n_rows
        print(f"trace {c['trace_id'][:8]}  call {c['node_execution_id'][:8]}  {c['status']:8} "
              f"-> gateway {str(s['gw_request_id'])[:8]}  [{method}, {gap} ms apart, {ncand} candidate(s)]")
        for ln in lines:
            print(f"      {ln}")
    total_g, total_n = len(all_g), len(all_n)
    if a.write:
        stmts = insert_rows(w, a.warehouse_id, f"{a.catalog}.raw_logs.guardrail_results_raw", all_g)
        stmts += insert_rows(w, a.warehouse_id, f"{a.catalog}.raw_logs.node_executions_raw", all_n)
        print(f"\n{stmts} INSERT statements run")
    for c in unmatched:
        why = "errored call, no signature" if c["status"] == "error" else "no gateway span within tolerance"
        print(f"UNMATCHED call {c['node_execution_id'][:8]} trace {c['trace_id'][:8]} ({c['status']}): {why}")

    mode = "WROTE" if a.write else "DRY RUN, would write"
    print(f"\n{mode}: {total_g} rows to guardrail_results_raw, {total_n} rows to node_executions_raw")
    return 0


if __name__ == "__main__":
    sys.exit(main())
