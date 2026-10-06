# audit_fix

Proposed changes for the audit trail. **Nothing here has been applied to your app, committed to the
workspace repo, deployed, or run against the audit tables with `--write`.** Your original files are untouched.
The folder mirrors the app layout, so a file here replaces the file at the same path in the app.

## What changed

| File | Change | Why |
|---|---|---|
| `audit_wrapper.py` | `AuditTrailCallback` writes one row per real node execution. | LangGraph tags every run inside a node (the tracer wrapper, the routing function of a conditional edge) with the node's name, so one execution fired 2 to 3 callback events. A request wrote 49 step rows for 19 executions. |
| `audit_wrapper.py` | `started_at` is the real start time. `log_node_execution` takes a new optional `started_at`. | The row used to be stamped when it was written, so `started_at` was about equal to `ended_at`, and the duplicate rows carried latencies of 0 ms. |
| `audit_wrapper.py` | `_redact_pii_keep_ids`, used for `node_metadata`. | `_redact_pii` turns a UUID whose last segment is all digits into `[PHONE]` or `[AADHAAR]`. That would corrupt the gateway request id used as a join key. |
| `core/observability.py` | `NodeTracer.wrap` never runs a node twice. | The `except Exception` around the MLflow span re-ran the node when the node itself raised, so a gateway block hit the gateway twice. It also re-ran a node that had finished when MLflow failed afterwards. |
| `core/gateway_client.py` | Records `gateway_request_id`, the header it came from, all response header names and body keys in the `gateway_llm_call` row. | The audit rows and the gateway trace table have no shared id. The first deployed call shows which header carries it. Until then the sync job matches by time and tokens. Base file: the app's repo copy, not your older local file. |
| `scripts/sync_gateway_policies.py` | New. Copies the gateway's policy decisions into `guardrail_results_raw` and `node_executions_raw`. | The app never receives the gateway's policy detail, so it has to be copied afterwards. |

`CHANGES.diff` holds the exact line changes.

## The sync job

For every `gateway_llm_call:*` row not yet synced it:
1. finds the gateway span: by `gateway_request_id` when the audit row has one, otherwise by start time
   (within 5 s) and the same token counts (allowed calls) or the same blocking policy (blocked calls), one span per row;
2. writes one `guardrail_results_raw` row per policy and phase: `policy_name`, `result` (`allow_on_call`,
   `deny_on_call`, `allow_on_result`), `triggered_block`, `checked_at` (the gateway span time), and a
   `guardrail_metadata` JSON with the reason, handler, configured action, gateway request id and the
   `node_execution_id` of the gateway call. No `score` is written, because the gateway returns none per policy;
3. writes one child row per policy into `node_executions_raw`: `node_name = gateway_policy:<policy>`,
   `node_type = guardrail`, `parent_node_id` = the gateway call row, `status = success | blocked`,
   `error_message = "Blocked by policy: <policy> (<reason>)"` for a block.

Rows are only inserted, never updated or deleted. A run skips calls that already have rows. The reason is
masked first: text in quotes becomes `[REDACTED]`, then the usual PII patterns apply.

```
python scripts/sync_gateway_policies.py --profile <profile> --since 2026-10-04 --warehouse-id <id> [--agent-id <oauth integration id>]
python scripts/sync_gateway_policies.py ... --write      # inserts; the default is a dry run
```

Per session: `ai_interactions_raw.session_id` -> `trace_id` -> `guardrail_results_raw`.

## Tests

`python tests/run_all_tests.py` (needs `langgraph==0.2.62`, `langchain-core==0.3.29`, `flask`, `databricks-sdk`,
`requests`, `mlflow-skinny`). They use the pinned LangGraph version and make no network calls.

## Known limits

* **Old rows stay as they are.** Existing duplicate step rows are not touched, and nothing backfills old requests.
* **Matching without a request id is a best guess.** It was exact on all 8 calls of the 4 Oct tests, but those
  ran one at a time. Concurrent calls with identical token counts could be paired in the wrong order.
  The request id removes this.
* **Blocked requests have a partial policy list.** The gateway stops at the first DENY.
* **No per-policy timing.** All policy events of a call share one timestamp.
* **The gateway table is shared** with other projects. The job reads it through the app's endpoint name,
  and optionally `--agent-id`. The identity that runs it needs read access to that table.
* **`pii-redaction-policy` shows ALLOW even when it changed the text.** Its configured action
  (`transform`) is stored in the metadata.
* **A request with no audit row cannot be synced.** The 5 Oct `Name-Redaction` denial has none.
