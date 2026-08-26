# Vector Search Troubleshooting Guide

Databricks Apps + Databricks Vector Search (Standard Endpoint) — issues encountered and fixes applied on the `feat/wishlist` branch.

---

## Issue 1 — Workflow fails to initialise: `MissingSchema` on vector search

**Symptom**
```
[DATABRICKS ADAPTER] Initialization error: Invalid URL
'adb-4206962778623078.18.azuredatabricks.net/oidc/v1/token': No scheme supplied.
Perhaps you meant https://adb-4206962778623078.18.azuredatabricks.net/oidc/v1/token?
```
App starts but every `/api/search` returns **503**.

**Root cause**  
Databricks Apps automatically injects `DATABRICKS_HOST` into the environment **without** the `https://` scheme. The Vector Search client tries to construct an OIDC token URL from it and fails.

**Fix 1 — Defensive code in `core/vector_store/databricks_adapter.py`**
```python
raw_host = host or os.getenv("DATABRICKS_HOST", "")
if raw_host and not raw_host.startswith("http"):
    raw_host = f"https://{raw_host}"
self.host = raw_host or None
```

**Fix 2 — Explicit env var in `app.yaml`**
```yaml
- name: DATABRICKS_HOST
  value: "https://adb-4206962778623078.18.azuredatabricks.net"
```

---

## Issue 2 — Workflow falls back to Pinecone and fails: `[401] Invalid API key`

**Symptom**
```
pinecone.errors.exceptions.UnauthorizedError: [401] Invalid API key
ERROR: Failed to initialize workflow
```
Occurs immediately after Issue 1 — the code falls back to Pinecone when Databricks vector search fails.

**Root cause**  
`PINECONE_API_KEY` was commented out in `app.yaml`. The fallback path tried to initialise a real Pinecone client with no key.

**Fix — Set dummy values in `app.yaml`**
```yaml
- name: PINECONE_API_KEY
  value: "dummy"
- name: PINECONE_INDEX_NAME
  value: "dummy"
```
The app uses Databricks vector search exclusively. These dummy values prevent the Pinecone init from crashing.

---

## Issue 3 — Filter format rejected by Standard endpoint

**Symptom**
```
databricks.vector_search.exceptions.BadRequest: Filter string is not supported
for standard endpoints. If you are calling from the python client,
use filters={"column_name": "value"} instead.
```
Vector search connects successfully but every filtered search returns an error.

**Root cause**  
The original `_build_filter_clause()` produced a **SQL WHERE string** (e.g. `"category_clean = 'tote bags'"`). The Databricks Vector Search Python SDK requires a **dict** for Standard endpoints. SQL strings are only accepted by Storage-Optimized endpoints via the REST API.

**Fix — Add `_build_filter_dict()` in `databricks_adapter.py`**

The method converts MongoDB/Pinecone-style operators to Databricks SDK dict format:

| Pinecone/Mongo style | Databricks SDK dict format |
|---|---|
| `{"col": {"$in": [...]}}` | `{"col": [...]}` |
| `{"col": {"$nin": [...]}}` | `{"col NOT": [...]}` |
| `{"col": {"$ne": val}}` | `{"col NOT": val}` |
| `{"col": {"$gt": val}}` | `{"col >": val}` |
| `{"col": {"$gte": val}}` | `{"col >=": val}` |
| `{"col": {"$lt": val}}` | `{"col <": val}` |
| `{"col": {"$lte": val}}` | `{"col <=": val}` |
| `{"$and": [{...}, {...}]}` | Merged into a single flat dict |

Then in `search()`:
```python
filter_dict = self._build_filter_dict(filters) if filters else None
results = self.index.similarity_search(
    query_vector=vector,
    columns=columns_to_retrieve,
    num_results=top_k,
    filters=filter_dict   # dict, not a SQL string
)
```

---

## Issue 4 — Category filter always returns 0 results

**Symptom**  
Vector search connects, runs, returns 0 results for every category-based query ("tote bags", "shoulder bags", etc.). Constraint relaxer fires repeatedly but still 0 results.

**Root cause**  
The `category_clean` column in the index stores **full URL taxonomy paths**, not simple labels:
```
women/handbags/totes--1
women/handbags/totes--1/leather-tote-bags
travel-tech/bags-travel-accessories/weekend-overnight-bags
```
Sending an exact/IN filter like `{"category_clean": ["tote bags", "Tote Bags", "tote"]}` never matches any row because none of those values appear verbatim in the taxonomy paths.

**Fix — Remove category from index filters; use post-filter substring match instead**

**Step 1:** Remove the category `$in`/`$ne`/`$nin` conditions from `_build_pinecone_filters()` in `workflow.py`. Add this comment:
```python
# Category filter: NOT sent as an index-level filter. category_clean
# stores full taxonomy paths (e.g. "women/handbags/totes--1"), which
# can never match an exact/IN filter against a bare keyword like "tote".
# Category matching is applied as a substring post-filter via _category_matches().
```

**Step 2:** Add two helper methods to `ShoppingAssistantWorkflow`:
```python
@staticmethod
def _generate_category_variations(categories: List[str]) -> List[str]:
    variations = []
    for cat in categories:
        cat_lower = cat.lower()
        variations.append(cat_lower)
        if cat_lower.endswith('s'):
            variations.append(cat_lower[:-1])
        else:
            variations.append(cat_lower + 's')
        if 'tote' in cat_lower:
            variations.append('tote')
        elif 'shoulder' in cat_lower:
            variations.append('shoulder')
        elif 'crossbody' in cat_lower or 'cross-body' in cat_lower:
            variations.extend(['crossbody', 'cross-body'])
        elif 'backpack' in cat_lower:
            variations.append('backpack')
    return list(set(variations))

def _category_matches(self, category_clean: Optional[str], categories: List[str]) -> bool:
    if not category_clean or not categories:
        return False
    haystack = category_clean.lower()
    return any(kw in haystack for kw in self._generate_category_variations(categories))
```

**Step 3:** Apply the post-filter inside the product loop in `product_search_node()`, after price filtering:
```python
category_clean = metadata.get('category', '')
if preferences.categories and not self._category_matches(category_clean, preferences.categories):
    continue
if preferences.excluded_categories and self._category_matches(category_clean, preferences.excluded_categories):
    continue
```

---

## Issue 5 — Semantic cache poisons results after a failed search

**Symptom**  
After any of the above issues cause a 0-result search, subsequent queries for the same term return 0 results instantly (cache HIT, ~0.3ms) even after the underlying bug is fixed.

**Root cause**  
The `SemanticCache` caches zero-result responses the same as non-zero ones. Once a bad result is cached (in-memory or Delta), the fixed vector search path is never reached.

**Fix options**

- **In-memory cache** (default when `DATABRICKS_SQL_WAREHOUSE_ID` is unset): clears automatically on app restart/redeploy. Just redeploy after fixing the underlying bug.
- **Delta cache** (`sandbox.venkat.semantic_cache`): truncate the table after fixing:
  ```python
  from databricks.sdk import WorkspaceClient
  w = WorkspaceClient()
  w.statement_execution.execute_statement(
      warehouse_id="<warehouse_id>",
      statement="TRUNCATE TABLE sandbox.venkat.semantic_cache",
      wait_timeout="30s"
  )
  ```

---

## Issue 6 — Missing env vars causing audit trail and warehouse failures

**Symptom**  
```
[BUILD] error resolving resource audit-app-id for env AUDIT_APP_ID:
error getting secret audit-app-id for scope shopping_assistant
```
AuditWrapper raises at startup; `DATABRICKS_SQL_WAREHOUSE_ID` is empty causing all Delta writes to fail silently.

**Root cause**  
Several env vars were either missing from `app.yaml` or referencing wrong resource/secret names.

**Fix — Correct `app.yaml` entries**

| Env var | Wrong config | Correct config |
|---|---|---|
| `AUDIT_APP_ID` | `valueFrom: audit-app-id` (key does not exist in scope) | `value: "ecom-shop-assistant-dev"` |
| `DATABRICKS_SQL_WAREHOUSE_ID` | `valueFrom: sql-warehouse-id` (wrong resource name) | `valueFrom: databricks-sql-warehouse-id` |
| `DEFAULT_USER_COUNTRY` | missing | `value: "US"` |
| `GOOGLE_CLIENT_ID` | missing | `valueFrom: google-client-id` |
| `GOOGLE_CLIENT_SECRET` | missing | `valueFrom: google-client-secret` |

To verify which secret keys exist in a scope:
```bash
databricks secrets list-secrets <scope-name> --profile <profile>
```

---

## Quick Diagnostic Checklist

When search returns 0 results or 503, check the deployment logs in order:

1. **503 on `/api/search`** → workflow failed to init → check for `MissingSchema` or `[401]` errors → fix `DATABRICKS_HOST` scheme (Issue 1 & 2)
2. **`BadRequest: Filter string is not supported`** → filters sent as SQL string → use `_build_filter_dict()` (Issue 3)
3. **0 results, no errors** → category filter blocking all results → remove category from index filters, use post-filter (Issue 4)
4. **0 results, cache HIT, ~0ms** → stale zero cached → redeploy (in-memory) or truncate Delta cache (Issue 5)
5. **BUILD error on resource** → secret key name mismatch → run `databricks secrets list-secrets <scope>` and fix `app.yaml` (Issue 6)
