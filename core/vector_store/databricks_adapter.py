"""
Databricks Vector Search Adapter
Implements VectorStoreInterface for Databricks Vector Search
"""

import os
import sys
import time
from typing import List, Dict, Any, Optional
from databricks.vector_search.client import VectorSearchClient
from .base import VectorStoreInterface
from ..audit_logger import log_tool_call


class DatabricksAdapter(VectorStoreInterface):
    """Databricks Vector Search implementation of VectorStoreInterface"""

    def __init__(
        self,
        host: Optional[str] = None,
        token: Optional[str] = None,
        index_name: Optional[str] = None,
        endpoint_name: Optional[str] = None
    ):
        """
        Initialize Databricks Vector Search adapter

        Args:
            host: Databricks workspace URL (defaults to env var DATABRICKS_HOST)
            token: Databricks access token (defaults to env var DATABRICKS_TOKEN)
            index_name: Vector search index name (defaults to env var DATABRICKS_VECTOR_INDEX)
            endpoint_name: Vector search endpoint (defaults to env var DATABRICKS_VECTOR_ENDPOINT)
        """
        self.host = host or os.getenv("DATABRICKS_HOST")
        if self.host and not self.host.startswith("http"):
            self.host = f"https://{self.host}"
        self.token = token or os.getenv("DATABRICKS_TOKEN")
        self.index_name = index_name or os.getenv("DATABRICKS_VECTOR_INDEX", "bags_embeddings_index")
        self.endpoint_name = endpoint_name or os.getenv("DATABRICKS_VECTOR_ENDPOINT")

        # DEBUG: Print what we're getting
        print(f"[DATABRICKS ADAPTER DEBUG] host param: {host}", file=sys.stderr)
        print(f"[DATABRICKS ADAPTER DEBUG] token param: {'***' + token[-4:] if token else None}", file=sys.stderr)
        print(f"[DATABRICKS ADAPTER DEBUG] DATABRICKS_HOST env: {os.getenv('DATABRICKS_HOST')}", file=sys.stderr)
        print(f"[DATABRICKS ADAPTER DEBUG] DATABRICKS_TOKEN env: {'***' + os.getenv('DATABRICKS_TOKEN')[-4:] if os.getenv('DATABRICKS_TOKEN') else None}", file=sys.stderr)
        print(f"[DATABRICKS ADAPTER DEBUG] self.host: {self.host}", file=sys.stderr)
        print(f"[DATABRICKS ADAPTER DEBUG] self.token: {'***' + self.token[-4:] if self.token else None}", file=sys.stderr)
        print(f"[DATABRICKS ADAPTER DEBUG] self.endpoint_name: {self.endpoint_name}", file=sys.stderr)
        print(f"[DATABRICKS ADAPTER DEBUG] self.index_name: {self.index_name}", file=sys.stderr)

        if not self.endpoint_name:
            raise ValueError("DATABRICKS_VECTOR_ENDPOINT is required")

        # Initialize Databricks Vector Search client
        try:
            # In Databricks Apps, credentials are automatically available via the runtime
            # VectorSearchClient will use the app's service principal or default credentials
            print(f"[DATABRICKS ADAPTER] Initializing Vector Search client...", file=sys.stderr)

            # Check if we have explicit credentials (for local development)
            sp_client_id = os.getenv("DATABRICKS_CLIENT_ID")
            sp_client_secret = os.getenv("DATABRICKS_CLIENT_SECRET")
            if self.host and self.token:
                print(f"[DATABRICKS ADAPTER] Using PAT credentials for {self.host}", file=sys.stderr)
                self.client = VectorSearchClient(
                    workspace_url=self.host,
                    personal_access_token=self.token,
                    disable_notice=True
                )
            elif self.host and sp_client_id and sp_client_secret:
                # Databricks Apps injects service principal credentials as env vars
                print(f"[DATABRICKS ADAPTER] Using service principal credentials for {self.host}", file=sys.stderr)
                self.client = VectorSearchClient(
                    workspace_url=self.host,
                    service_principal_client_id=sp_client_id,
                    service_principal_client_secret=sp_client_secret,
                    disable_notice=True
                )
            else:
                # Databricks notebooks/clusters use default auth
                print(f"[DATABRICKS ADAPTER] Using default workspace credentials", file=sys.stderr)
                self.client = VectorSearchClient(disable_notice=True)

            # Get the index
            self.index = self.client.get_index(
                endpoint_name=self.endpoint_name,
                index_name=self.index_name
            )

            print(f"[DATABRICKS ADAPTER] Connected to index: {self.index_name}", file=sys.stderr)
            print(f"[DATABRICKS ADAPTER] Using endpoint: {self.endpoint_name}", file=sys.stderr)

        except Exception as e:
            print(f"[DATABRICKS ADAPTER] Initialization error: {e}", file=sys.stderr)
            raise

    def search(
        self,
        vector: List[float],
        top_k: int = 10,
        filters: Optional[Dict[str, Any]] = None,
        include_metadata: bool = True,
        audit_context: Optional[Dict[str, str]] = None,
    ) -> List[Dict[str, Any]]:
        """
        Search Databricks Vector Search index for similar vectors

        Args:
            vector: Query embedding
            top_k: Number of results
            filters: Metadata filters (Databricks SQL-style filters)
            include_metadata: Include metadata in response
            audit_context: Optional dict with request_id, user_id, session_id for audit logging

        Returns:
            List of matches with standardized format
        """
        _t0 = time.time()
        _error = ""
        try:
            # Convert filters to Databricks Vector Search filter dict if provided
            # (STANDARD endpoints reject SQL WHERE-clause strings — they require
            # the {"column[ op]": value} dict format)
            filter_clause = None
            if filters:
                filter_clause = self._build_filter_dict(filters)

            # Columns available in sandbox.venkat.bags_embeddings table
            columns_to_retrieve = [
                "product_id",
                "name_clean",
                "brand_clean",
                "category_clean",
                "gender",
                "price_from",
                "price_tier",
                "primary_color",
                "material_type",
                "is_available",
                "on_sale",
                "primary_image_url",
                "url_full",  # Myer product page URL
                "embedding_text"
            ]

            # Execute vector search
            results = self.index.similarity_search(
                query_vector=vector,
                columns=columns_to_retrieve,
                num_results=top_k,
                filters=filter_clause
            )

            # Debug: Print raw response
            print(f"[DATABRICKS ADAPTER DEBUG] Raw results type: {type(results)}", file=sys.stderr)
            if isinstance(results, dict):
                print(f"[DATABRICKS ADAPTER DEBUG] Dict keys: {results.keys()}", file=sys.stderr)
                print(f"[DATABRICKS ADAPTER DEBUG] Dict content: {results}", file=sys.stderr)
            print(f"[DATABRICKS ADAPTER DEBUG] Has data_array: {hasattr(results, 'data_array')}", file=sys.stderr)
            if hasattr(results, 'data_array'):
                print(f"[DATABRICKS ADAPTER DEBUG] data_array length: {len(results.data_array) if results.data_array else 0}", file=sys.stderr)
                if results.data_array and len(results.data_array) > 0:
                    print(f"[DATABRICKS ADAPTER DEBUG] First row sample: {results.data_array[0]}", file=sys.stderr)
            print(f"[DATABRICKS ADAPTER DEBUG] Results dir: {dir(results)}", file=sys.stderr)

            # Standardize format to match Pinecone adapter
            standardized_results = []

            # Handle dict response format (from VectorSearchClient)
            if isinstance(results, dict) and 'result' in results and 'data_array' in results['result']:
                data_array = results['result']['data_array']
                for row in data_array:
                    # Row format: [product_id, name_clean, brand_clean, category_clean, gender,
                    #              price_from, price_tier, primary_color, material_type,
                    #              is_available, on_sale, primary_image_url, embedding_text, score]

                    score = float(row[-1]) if len(row) > 0 and row[-1] is not None else 0.0

                    # Build metadata dict from available columns
                    metadata = {}
                    if len(row) > 0:
                        metadata['product_id'] = str(row[0])
                    if len(row) > 1:
                        metadata['name'] = str(row[1]) if row[1] is not None else ""
                        metadata['product_name'] = str(row[1]) if row[1] is not None else ""
                    if len(row) > 2:
                        metadata['brand'] = str(row[2]) if row[2] is not None else ""
                    if len(row) > 3:
                        metadata['category'] = str(row[3]) if row[3] is not None else ""
                    if len(row) > 4:
                        metadata['gender'] = str(row[4]) if row[4] is not None else ""
                    if len(row) > 5:
                        metadata['price'] = float(row[5]) if row[5] is not None else 0.0
                    if len(row) > 6:
                        metadata['price_tier'] = str(row[6]) if row[6] is not None else ""
                    if len(row) > 7:
                        metadata['color'] = str(row[7]) if row[7] is not None else ""
                    if len(row) > 8:
                        metadata['material'] = str(row[8]) if row[8] is not None else ""
                    if len(row) > 9:
                        metadata['available'] = bool(row[9]) if row[9] is not None else True
                    if len(row) > 10:
                        metadata['on_sale'] = bool(row[10]) if row[10] is not None else False
                    if len(row) > 11:
                        metadata['image_url'] = str(row[11]) if row[11] is not None else ""
                    if len(row) > 12:
                        metadata['url_full'] = str(row[12]) if row[12] is not None else ""
                    if len(row) > 13:
                        metadata['description'] = str(row[13]) if row[13] is not None else ""

                    standardized_results.append({
                        'id': str(row[0]) if len(row) > 0 and row[0] is not None else "",
                        'score': score,
                        'metadata': metadata
                    })
            # Fallback: Handle object with data_array attribute (legacy)
            elif hasattr(results, 'data_array'):
                for row in results.data_array:
                    score = float(row[-1]) if len(row) > 0 and row[-1] is not None else 0.0

                    # Build metadata dict from available columns
                    metadata = {}
                    if len(row) > 0:
                        metadata['product_id'] = str(row[0])
                    if len(row) > 1:
                        metadata['name'] = str(row[1]) if row[1] is not None else ""
                        metadata['product_name'] = str(row[1]) if row[1] is not None else ""
                    if len(row) > 2:
                        metadata['brand'] = str(row[2]) if row[2] is not None else ""
                    if len(row) > 3:
                        metadata['category'] = str(row[3]) if row[3] is not None else ""
                    if len(row) > 4:
                        metadata['gender'] = str(row[4]) if row[4] is not None else ""
                    if len(row) > 5:
                        metadata['price'] = float(row[5]) if row[5] is not None else 0.0
                    if len(row) > 6:
                        metadata['price_tier'] = str(row[6]) if row[6] is not None else ""
                    if len(row) > 7:
                        metadata['color'] = str(row[7]) if row[7] is not None else ""
                    if len(row) > 8:
                        metadata['material'] = str(row[8]) if row[8] is not None else ""
                    if len(row) > 9:
                        metadata['available'] = bool(row[9]) if row[9] is not None else True
                    if len(row) > 10:
                        metadata['on_sale'] = bool(row[10]) if row[10] is not None else False
                    if len(row) > 11:
                        metadata['image_url'] = str(row[11]) if row[11] is not None else ""
                    if len(row) > 12:
                        metadata['url_full'] = str(row[12]) if row[12] is not None else ""
                    if len(row) > 13:
                        metadata['description'] = str(row[13]) if row[13] is not None else ""

                    standardized_results.append({
                        'id': str(row[0]) if row[0] is not None else "",
                        'score': score,
                        'metadata': metadata
                    })

            print(f"[DATABRICKS ADAPTER] Found {len(standardized_results)} results", file=sys.stderr)
            log_tool_call(
                tool_name="databricks_vector_search",
                index_name=self.index_name,
                filter_summary=str(filters)[:300] if filters else "",
                top_k=top_k,
                result_count=len(standardized_results),
                latency_ms=(time.time() - _t0) * 1000,
                **(audit_context or {}),
            )
            return standardized_results

        except Exception as e:
            _error = str(e)
            print(f"[DATABRICKS ADAPTER] Search error: {e}", file=sys.stderr)
            import traceback
            traceback.print_exc(file=sys.stderr)
            log_tool_call(
                tool_name="databricks_vector_search",
                index_name=self.index_name,
                filter_summary=str(filters)[:300] if filters else "",
                top_k=top_k,
                result_count=0,
                latency_ms=(time.time() - _t0) * 1000,
                error=_error,
                **(audit_context or {}),
            )
            return []

    def fetch_by_id(self, vector_id: str) -> Optional[Dict[str, Any]]:
        """
        Fetch a single product from the Silver Delta table by product_id.

        Uses a parameterised SQL statement via the Databricks SQL Warehouse so that
        the lookup is both correct (exact match on product_id) and safe (no SQL
        injection risk).  The Silver table name and warehouse ID are read from the
        same environment variables used by the app-layer fetch helper.

        Falls back to None and logs a warning if the lookup fails, so callers can
        degrade gracefully.
        """
        if not vector_id:
            return None
        try:
            import os as _os
            from databricks.sdk import WorkspaceClient
            from databricks.sdk.service.sql import StatementState, StatementParameterListItem

            silver_table   = _os.getenv("DATABRICKS_SILVER_TABLE", "sandbox.venkat.product_data_silver_bags")
            warehouse_id   = _os.getenv("DATABRICKS_SQL_WAREHOUSE_ID")
            if not warehouse_id:
                print(
                    "[DATABRICKS ADAPTER] DATABRICKS_SQL_WAREHOUSE_ID not set — cannot fetch by ID",
                    file=sys.stderr,
                )
                return None

            w      = WorkspaceClient()
            result = w.statement_execution.execute_statement(
                warehouse_id=warehouse_id,
                statement=f"SELECT * FROM {silver_table} WHERE product_id = :product_id LIMIT 1",
                parameters=[StatementParameterListItem(name="product_id", value=str(vector_id))],
                wait_timeout="20s",
            )

            if result.status.state != StatementState.SUCCEEDED:
                print(
                    f"[DATABRICKS ADAPTER] fetch_by_id SQL failed for {vector_id}: "
                    f"{result.status.error}",
                    file=sys.stderr,
                )
                return None

            if (
                not result.result
                or not result.result.data_array
                or len(result.result.data_array) == 0
            ):
                print(f"[DATABRICKS ADAPTER] Product {vector_id} not found in Silver table", file=sys.stderr)
                return None

            columns = [col.name for col in result.manifest.schema.columns]
            row_dict = dict(zip(columns, result.result.data_array[0]))

            price = row_dict.get("price_from") or row_dict.get("price_to") or 0

            metadata = {
                "product_id":    str(row_dict.get("product_id", vector_id)),
                "name":          str(row_dict.get("name_clean") or row_dict.get("name", "")),
                "product_name":  str(row_dict.get("name_clean") or row_dict.get("name", "")),
                "brand":         str(row_dict.get("brand_clean") or row_dict.get("brand", "")),
                "category":      str(row_dict.get("category_clean") or row_dict.get("category", "")),
                "gender":        str(row_dict.get("gender", "")),
                "price":         float(price) if price else 0.0,
                "price_tier":    str(row_dict.get("price_tier", "")),
                "color":         str(row_dict.get("primary_color", "")),
                "material":      str(row_dict.get("material_type") or row_dict.get("material_raw", "")),
                "available":     bool(row_dict.get("is_available", True)),
                "on_sale":       bool(row_dict.get("on_sale", False)),
                "image_url":     str(row_dict.get("primary_image_url", "")),
                "url_full":      str(row_dict.get("url_full", "")),
                "description":   str(row_dict.get("long_description_clean") or row_dict.get("embedding_text", "")),
            }

            print(
                f"[DATABRICKS ADAPTER] fetch_by_id → {vector_id}: {metadata['name']}",
                file=sys.stderr,
            )
            return {"id": metadata["product_id"], "metadata": metadata}

        except Exception as e:
            print(f"[DATABRICKS ADAPTER] fetch_by_id error for {vector_id}: {e}", file=sys.stderr)
            import traceback
            traceback.print_exc(file=sys.stderr)
            return None

    def get_index_stats(self) -> Dict[str, Any]:
        """
        Get Databricks Vector Search index statistics

        Returns:
            Dictionary with index stats
        """
        try:
            # Get index description
            index_info = self.index.describe()

            stats = {
                'index_name': self.index_name,
                'endpoint_name': self.endpoint_name,
                'type': 'databricks_vector_search'
            }

            # Extract status info
            if isinstance(index_info, dict):
                if 'status' in index_info:
                    stats['status'] = index_info['status'].get('state', 'unknown')
                if 'delta_sync_index_spec' in index_info:
                    spec = index_info['delta_sync_index_spec']
                    stats['source_table'] = spec.get('source_table', 'unknown')
                    if 'embedding_vector_columns' in spec and len(spec['embedding_vector_columns']) > 0:
                        stats['dimension'] = spec['embedding_vector_columns'][0].get('embedding_dimension', 'unknown')

            print(f"[DATABRICKS ADAPTER] Stats retrieved: {stats}", file=sys.stderr)
            return stats

        except Exception as e:
            print(f"[DATABRICKS ADAPTER] Stats error: {e}", file=sys.stderr)
            return {
                'error': str(e),
                'index_name': self.index_name,
                'endpoint_name': self.endpoint_name,
                'type': 'databricks_vector_search'
            }

    def upsert(self, vectors: List[Dict[str, Any]]) -> None:
        """
        Upload vectors to Databricks Vector Search
        Note: Databricks uses Delta Sync, so direct upsert is not typical

        Args:
            vectors: List of vectors with id, values, and metadata
        """
        print(f"[DATABRICKS ADAPTER] Upsert not supported - use Delta table updates", file=sys.stderr)
        raise NotImplementedError("Databricks Vector Search uses Delta Sync - update source table instead")

    def delete(self, ids: List[str]) -> None:
        """
        Delete vectors from Databricks Vector Search
        Note: Databricks uses Delta Sync, so delete from source table

        Args:
            ids: List of vector IDs to delete
        """
        print(f"[DATABRICKS ADAPTER] Delete not supported - update Delta table instead", file=sys.stderr)
        raise NotImplementedError("Databricks Vector Search uses Delta Sync - delete from source table instead")

    def get_stats(self) -> Dict[str, Any]:
        """
        Get Databricks Vector Search index statistics
        Alias for get_index_stats for interface compatibility

        Returns:
            Dictionary with index stats
        """
        return self.get_index_stats()

    def _build_filter_dict(self, filters: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        Convert a MongoDB-style filters dict to the Databricks Vector Search
        filter dict format required by STANDARD endpoints: {"column[ op]": value}.
        (STANDARD endpoints reject SQL WHERE-clause strings; only the newer
        STORAGE_OPTIMIZED endpoint type accepts those.)

        Supported operators, mapped to Databricks filter-key suffixes:
          - Scalar equality  : {"field": "value"}            -> {"field": "value"}
          - $gt / $gte       : {"field": {"$gt": 100}}        -> {"field >": 100}
          - $lt / $lte       : {"field": {"$lt": 200}}        -> {"field <": 200}
          - $ne              : {"field": {"$ne": "black"}}    -> {"field NOT": "black"}
          - $in              : {"field": {"$in": [...]}}      -> {"field": [...]}
          - $nin             : {"field": {"$nin": [...]}}     -> {"field NOT": [...]}
          - $and             : {"$and": [{"field1": …}, …]}   -> merged into one dict

        Unknown operators are skipped and logged. Conflicting keys across an
        $and (e.g. two clauses targeting the same field+operator) overwrite
        each other — callers should keep such filters on distinct fields.
        """
        def _merge(target: Dict[str, Any], key: str, value: Any) -> None:
            if key in target:
                print(
                    f"[DATABRICKS ADAPTER] Duplicate filter key '{key}' — "
                    f"overwriting previous value",
                    file=sys.stderr,
                )
            target[key] = value

        def _parse_one(target: Dict[str, Any], key: str, value: Any) -> None:
            if isinstance(value, (str, int, float, bool)):
                _merge(target, key, value)
            elif isinstance(value, list):
                # Bare list treated as $in
                if value:
                    _merge(target, key, value)
            elif isinstance(value, dict):
                for op, val in value.items():
                    if op == "$gt":
                        _merge(target, f"{key} >", val)
                    elif op == "$gte":
                        _merge(target, f"{key} >=", val)
                    elif op == "$lt":
                        _merge(target, f"{key} <", val)
                    elif op == "$lte":
                        _merge(target, f"{key} <=", val)
                    elif op == "$ne":
                        _merge(target, f"{key} NOT", val)
                    elif op == "$in":
                        if val:
                            _merge(target, key, val)
                    elif op == "$nin":
                        if val:
                            _merge(target, f"{key} NOT", val)
                    else:
                        print(
                            f"[DATABRICKS ADAPTER] Unknown filter operator '{op}' "
                            f"for key '{key}' — skipped",
                            file=sys.stderr,
                        )

        result: Dict[str, Any] = {}
        for key, value in filters.items():
            if key == "$and":
                # {"$and": [{"field1": …}, {"field2": …}]}
                if isinstance(value, list):
                    for sub_filter in value:
                        if isinstance(sub_filter, dict):
                            sub = self._build_filter_dict(sub_filter)
                            if sub:
                                for sub_key, sub_val in sub.items():
                                    _merge(result, sub_key, sub_val)
            else:
                _parse_one(result, key, value)

        return result if result else None
