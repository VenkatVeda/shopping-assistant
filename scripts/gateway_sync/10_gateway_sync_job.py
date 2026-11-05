# Databricks notebook source
# MAGIC %md
# MAGIC # 10 - Gateway policy and final-text sync (every 15 minutes)
# MAGIC Thin launcher. All logic lives in `scripts/sync_gateway_policies.py`.
# MAGIC
# MAGIC After each request the AI Gateway writes its own trace. This job copies, into `raw_logs`:
# MAGIC the policy decisions (`guardrail_results_raw`) and, for allowed calls, the final user message and reply
# MAGIC as the gateway sent them after its policies (`node_executions_raw`, `gateway_final_io`).
# MAGIC Calls whose gateway trace has not landed yet are left alone and picked up by the next run.
# MAGIC Insert only; a call that is already synced is skipped.
# MAGIC
# MAGIC | Widget | Meaning |
# MAGIC |---|---|
# MAGIC | `warehouse_id` | SQL warehouse used for all statements |
# MAGIC | `since_hours` | How far back to look for calls not yet synced |
# MAGIC | `write` | `false` = dry run (reads only, prints the plan). `true` = insert rows |
# MAGIC | `code_path` | Folder that holds `audit_wrapper.py` and `scripts/` |
# MAGIC
# MAGIC The identity that runs this job needs `SELECT` on the gateway trace table
# MAGIC (`xponent_prod.xponent-creds.unity_gateway_otel_spans`) and write access to `shopping_assistant.raw_logs`.

# COMMAND ----------

dbutils.widgets.text("warehouse_id", "8eba55b5eb828697")
dbutils.widgets.text("since_hours", "48")
dbutils.widgets.dropdown("write", "false", ["false", "true"])
dbutils.widgets.text("code_path", "/Workspace/Users/nupur@xponent.ai/Audit-trail-main")

# COMMAND ----------

import os, sys
from datetime import datetime, timedelta, timezone

code_path = dbutils.widgets.get("code_path")
for p in (code_path, os.path.join(code_path, "scripts")):
    if p not in sys.path:
        sys.path.insert(0, p)
import importlib, audit_wrapper, sync_gateway_policies
importlib.reload(audit_wrapper)              # always run the versions currently on disk
importlib.reload(sync_gateway_policies)

since = (datetime.now(timezone.utc) - timedelta(hours=int(dbutils.widgets.get("since_hours")))).strftime("%Y-%m-%d %H:%M:%S")
args = ["--default-auth", "--warehouse-id", dbutils.widgets.get("warehouse_id"), "--since", since]
if dbutils.widgets.get("write") == "true":
    args.append("--write")
print("running:", " ".join(args))

rc = sync_gateway_policies.main(args)
if rc != 0:
    raise Exception("Gateway sync reported a failure - see the output above. Unsynced calls are retried on the next run.")
