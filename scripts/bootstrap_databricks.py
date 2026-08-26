"""
One-time Databricks bootstrap for ecom-shop-assistant-dev.

Creates:
  - shopping_assistant.user_data schema
  - Delta tables: wishlist_items, cart_items, orders, erasure_requests
  - app_registry entry for ecom-shop-assistant-dev
  - HMAC secret: hmac_key_ecom-shop-assistant-dev (in both secret scopes)

Run once per environment (dev / prod). Safe to re-run — uses IF NOT EXISTS and
upsert logic where possible.

Usage:
    DATABRICKS_CONFIG_PROFILE=Venkat python scripts/bootstrap_databricks.py
"""

import os
import sys
import uuid
import secrets
import base64

from databricks.sdk import WorkspaceClient
from databricks.sdk.service.sql import StatementParameterListItem

APP_ID         = os.getenv("AUDIT_APP_ID",   "ecom-shop-assistant-dev")
APP_NAME       = "Ecom Shopping Assistant (Dev)"
APP_OWNER      = "venkat@xponent.ai"
CATALOG        = os.getenv("AUDIT_CATALOG",  "shopping_assistant")
SECRET_SCOPE   = os.getenv("AUDIT_SECRET_SCOPE", "shopping_assistant")
FALLBACK_SCOPE = "audit_trail_secrets"
WAREHOUSE_ID   = os.getenv("DATABRICKS_SQL_WAREHOUSE_ID", "")

w = WorkspaceClient()


# ── helpers ───────────────────────────────────────────────────────────────────

def sql(statement: str, params=None, label: str = ""):
    if not WAREHOUSE_ID:
        print(f"  SKIP (no DATABRICKS_SQL_WAREHOUSE_ID set): {label or statement[:60]}")
        return None
    kwargs = dict(warehouse_id=WAREHOUSE_ID, statement=statement, wait_timeout="50s")
    if params:
        kwargs["parameters"] = params
    r = w.statement_execution.execute_statement(**kwargs)
    state = r.status.state.value if r.status and r.status.state else "?"
    err   = r.status.error.message[:120] if r.status and r.status.error else ""
    tag   = label or statement.strip()[:60]
    print(f"  [{state}] {tag}" + (f"  ERR: {err}" if err else ""))
    return r


def put_secret(scope: str, key: str, value: str):
    try:
        w.secrets.put_secret(scope=scope, key=key, string_value=value)
        print(f"  Written secret [{scope}] {key}")
    except Exception as e:
        print(f"  FAIL secret [{scope}] {key}: {e}")


# ── 1. Schema ─────────────────────────────────────────────────────────────────

print("\n=== 1. Schema ===")
sql(f"CREATE SCHEMA IF NOT EXISTS {CATALOG}.user_data", label=f"CREATE SCHEMA {CATALOG}.user_data")

# ── 2. Tables ─────────────────────────────────────────────────────────────────

print("\n=== 2. Delta Tables ===")

sql(f"""
CREATE TABLE IF NOT EXISTS {CATALOG}.user_data.wishlist_items (
    wishlist_item_id STRING NOT NULL,
    subject_ref      STRING NOT NULL,
    app_id           STRING NOT NULL,
    product_id       STRING,
    product_name     STRING,
    brand            STRING,
    price            DOUBLE,
    image_url        STRING,
    retailer_url     STRING,
    added_at         TIMESTAMP,
    is_active        BOOLEAN
) USING DELTA
""", label="CREATE TABLE wishlist_items")

sql(f"""
CREATE TABLE IF NOT EXISTS {CATALOG}.user_data.cart_items (
    cart_item_id STRING NOT NULL,
    subject_ref  STRING NOT NULL,
    app_id       STRING NOT NULL,
    product_id   STRING,
    product_name STRING,
    brand        STRING,
    price        DOUBLE,
    image_url    STRING,
    retailer_url STRING,
    quantity     INT,
    added_at     TIMESTAMP,
    updated_at   TIMESTAMP,
    is_active    BOOLEAN
) USING DELTA
""", label="CREATE TABLE cart_items")

sql(f"""
CREATE TABLE IF NOT EXISTS {CATALOG}.user_data.orders (
    order_id      STRING NOT NULL,
    order_item_id STRING NOT NULL,
    subject_ref   STRING NOT NULL,
    app_id        STRING NOT NULL,
    product_id    STRING,
    product_name  STRING,
    brand         STRING,
    price         DOUBLE,
    image_url     STRING,
    retailer_url  STRING,
    quantity      INT,
    unit_price    DOUBLE,
    line_total    DOUBLE,
    placed_at     TIMESTAMP,
    status        STRING
) USING DELTA
""", label="CREATE TABLE orders")

sql(f"""
CREATE TABLE IF NOT EXISTS {CATALOG}.user_data.erasure_requests (
    request_id   STRING NOT NULL,
    subject_ref  STRING NOT NULL,
    app_id       STRING NOT NULL,
    regulation   STRING,
    status       STRING,
    requested_at TIMESTAMP,
    processed_at TIMESTAMP,
    notes        STRING
) USING DELTA
""", label="CREATE TABLE erasure_requests")

# ── 3. app_registry ───────────────────────────────────────────────────────────

print("\n=== 3. app_registry ===")

check = w.statement_execution.execute_statement(
    warehouse_id=WAREHOUSE_ID,
    statement=f"SELECT COUNT(*) FROM {CATALOG}.raw_logs.app_registry WHERE app_id = '{APP_ID}' AND status = 'active'",
    wait_timeout="50s",
) if WAREHOUSE_ID else None

already_registered = (
    check and check.result and check.result.data_array
    and int(check.result.data_array[0][0]) > 0
)

if already_registered:
    print(f"  SKIP — {APP_ID} already active in app_registry")
else:
    entry_id = str(uuid.uuid4())
    sql(
        f"""
        INSERT INTO {CATALOG}.raw_logs.app_registry
        (registry_entry_id, app_id, app_name, app_owner_email, business_domain,
         regulations, secret_key_name, status, onboarded_at, entry_created_at, schema_version)
        VALUES
        (:entry_id, :app_id, :app_name, :owner, :domain,
         :regs, :key_name, 'active', current_timestamp(), current_timestamp(), '1.0')
        """,
        params=[
            StatementParameterListItem(name="entry_id",  value=entry_id),
            StatementParameterListItem(name="app_id",    value=APP_ID),
            StatementParameterListItem(name="app_name",  value=APP_NAME),
            StatementParameterListItem(name="owner",     value=APP_OWNER),
            StatementParameterListItem(name="domain",    value="retail"),
            StatementParameterListItem(name="regs",      value='["GDPR","DPDP"]'),
            StatementParameterListItem(name="key_name",  value=f"hmac_key_{APP_ID}"),
        ],
        label=f"INSERT {APP_ID} into app_registry",
    )

# ── 4. HMAC secret ────────────────────────────────────────────────────────────

print("\n=== 4. HMAC secret ===")

secret_key = f"hmac_key_{APP_ID}"

# Check if secret already exists (get_secret returns value if it does)
existing_secret = None
try:
    existing_secret = w.secrets.get_secret(scope=SECRET_SCOPE, key=secret_key)
    print(f"  SKIP — {secret_key} already exists in [{SECRET_SCOPE}]")
except Exception:
    pass

if existing_secret is None:
    new_key = base64.b64encode(secrets.token_bytes(32)).decode("utf-8")
    put_secret(SECRET_SCOPE,   secret_key, new_key)
    put_secret(FALLBACK_SCOPE, secret_key, new_key)
    print(f"  Generated fresh 256-bit HMAC key for {APP_ID}")
else:
    # Ensure fallback scope also has it
    try:
        w.secrets.get_secret(scope=FALLBACK_SCOPE, key=secret_key)
    except Exception:
        put_secret(FALLBACK_SCOPE, secret_key, existing_secret.value)

# ── 5. Verification ───────────────────────────────────────────────────────────

print("\n=== 5. Verification ===")
for tbl in ["wishlist_items", "cart_items", "orders", "erasure_requests"]:
    r = w.statement_execution.execute_statement(
        warehouse_id=WAREHOUSE_ID,
        statement=f"SELECT COUNT(*) FROM {CATALOG}.user_data.{tbl}",
        wait_timeout="50s",
    ) if WAREHOUSE_ID else None
    if r and r.result and r.result.data_array:
        print(f"  OK    {CATALOG}.user_data.{tbl}  ({r.result.data_array[0][0]} rows)")
    else:
        err = r.status.error.message[:80] if r and r.status and r.status.error else "no warehouse"
        print(f"  FAIL  {CATALOG}.user_data.{tbl}: {err}")

print("\nBootstrap complete.")
