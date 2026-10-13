# Databricks notebook source
# MAGIC %md
# MAGIC # 09 - Erasure job (weekly)
# MAGIC Thin launcher. All logic lives in `audit_wrapper.py` (`ErasureProcessor`, `run_erasure_cli`).
# MAGIC
# MAGIC | Widget | Meaning |
# MAGIC |---|---|
# MAGIC | `command` | `process-erasures` (delete personal data + flag audit rows) or `vacuum-erasures` (VACUUM customer_pii / feedback_pii once the recovery window has passed) |
# MAGIC | `app_id` | App whose requests are processed, e.g. `myre_app` |
# MAGIC | `warehouse_id` | SQL warehouse used for all statements |
# MAGIC | `execute` | `false` = dry run (reads only, prints the plan). `true` = really delete / write |
# MAGIC | `code_path` | Folder that holds `audit_wrapper.py` |
# MAGIC
# MAGIC Scope: `shopping_assistant` catalog only. Never touches cart, wishlist, orders, the `*_events_raw` tables or any other catalog.

# COMMAND ----------

dbutils.widgets.dropdown("command", "process-erasures", ["process-erasures", "vacuum-erasures"])
dbutils.widgets.text("app_id", "myre_app")
dbutils.widgets.text("warehouse_id", "8eba55b5eb828697")
dbutils.widgets.dropdown("execute", "false", ["false", "true"])
dbutils.widgets.text("code_path", "/Workspace/Users/nupur@xponent.ai/Audit-trail-main")

# COMMAND ----------

import sys, importlib

code_path = dbutils.widgets.get("code_path")
if code_path not in sys.path:
    sys.path.insert(0, code_path)
import audit_wrapper
importlib.reload(audit_wrapper)          # always run the version currently on disk

args = [
    dbutils.widgets.get("command"),
    "--app-id", dbutils.widgets.get("app_id"),
    "--warehouse-id", dbutils.widgets.get("warehouse_id"),
]
if dbutils.widgets.get("execute") == "true":
    args.append("--execute")
print("running:", " ".join(args))

rc = audit_wrapper.run_erasure_cli(args)
if rc != 0:
    raise Exception("Erasure job reported a failure - see the output above. Failed requests are retried on the next run.")

