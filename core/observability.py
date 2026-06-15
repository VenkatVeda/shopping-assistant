"""
Observability layer for the Shopping Assistant.

Wraps every LangGraph node with:
  - MLflow span tracing (latency, inputs, outputs, errors)
  - In-memory rolling metrics buffer (p50 / p95 / p99 per node)
  - Structured stderr logging with emoji tags for easy log grep

Usage inside _build_graph:
    tracer = NodeTracer()
    workflow.add_node("intent_classifier", tracer.wrap("intent_classifier", self.intent_classifier_node))
"""

import os
import time
import logging
import functools
import statistics
from collections import defaultdict
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# MLflow setup — graceful degradation if not available / not configured
# ---------------------------------------------------------------------------

def _init_mlflow():
    """Attempt to configure MLflow. Returns True when tracing is available."""
    try:
        import mlflow
        experiment_name = os.getenv(
            "MLFLOW_EXPERIMENT_NAME", "/Shared/shopping-assistant-observability"
        )
        mlflow.set_experiment(experiment_name)
        logger.info(f"[OBSERVABILITY] MLflow experiment: {experiment_name}")
        return True
    except Exception as exc:
        logger.warning(f"[OBSERVABILITY] MLflow unavailable — tracing disabled ({exc})")
        return False


_MLFLOW_AVAILABLE = _init_mlflow()


# ---------------------------------------------------------------------------
# Rolling percentile buffer — thread-safe enough for single-worker Flask
# ---------------------------------------------------------------------------

class RollingBuffer:
    """Keeps the last N samples for percentile calculation."""

    def __init__(self, maxlen: int = 500):
        self._maxlen = maxlen
        self._data: List[float] = []

    def append(self, value: float):
        if len(self._data) >= self._maxlen:
            self._data.pop(0)
        self._data.append(value)

    def percentile(self, p: float) -> float:
        if not self._data:
            return 0.0
        sorted_data = sorted(self._data)
        idx = max(0, int(len(sorted_data) * p / 100) - 1)
        return sorted_data[idx]

    def stats(self) -> Dict[str, float]:
        if not self._data:
            return {"count": 0, "avg": 0, "p50": 0, "p95": 0, "p99": 0, "min": 0, "max": 0}
        return {
            "count": len(self._data),
            "avg": round(statistics.mean(self._data), 2),
            "p50": round(self.percentile(50), 2),
            "p95": round(self.percentile(95), 2),
            "p99": round(self.percentile(99), 2),
            "min": round(min(self._data), 2),
            "max": round(max(self._data), 2),
        }


# ---------------------------------------------------------------------------
# Global metrics store
# ---------------------------------------------------------------------------

class MetricsStore:
    """
    Single in-process store for all node and request-level metrics.
    Survives multiple requests; reset between deployments.
    """

    def __init__(self):
        # latency buffers keyed by node name (ms)
        self._latency: Dict[str, RollingBuffer] = defaultdict(lambda: RollingBuffer(500))
        # counters keyed by (node_name, event)
        self._counters: Dict[str, int] = defaultdict(int)
        # per-request summary list (last 200 requests)
        self._requests: List[Dict] = []
        self._request_maxlen = 200
        # zero-result tracking
        self._search_result_counts: RollingBuffer = RollingBuffer(500)

    # --- recording ---

    def record_latency(self, node: str, duration_ms: float):
        self._latency[node].append(duration_ms)

    def increment(self, key: str, amount: int = 1):
        self._counters[key] += amount

    def record_request(self, summary: Dict):
        if len(self._requests) >= self._request_maxlen:
            self._requests.pop(0)
        self._requests.append(summary)

    def record_search_count(self, count: int):
        self._search_result_counts.append(count)

    # --- retrieval ---

    def latency_stats(self, node: str) -> Dict[str, float]:
        return self._latency[node].stats()

    def all_latency_stats(self) -> Dict[str, Dict[str, float]]:
        return {node: buf.stats() for node, buf in self._latency.items()}

    def counter(self, key: str) -> int:
        return self._counters[key]

    def all_counters(self) -> Dict[str, int]:
        return dict(self._counters)

    def recent_requests(self, n: int = 20) -> List[Dict]:
        return self._requests[-n:]

    def search_stats(self) -> Dict[str, float]:
        stats = self._search_result_counts.stats()
        total = self._counters.get("search_total", 0)
        zero = self._counters.get("search_zero_results", 0)
        stats["zero_result_rate_pct"] = round((zero / total * 100) if total else 0, 1)
        return stats

    def snapshot(self) -> Dict:
        """Full snapshot for the /metrics endpoint."""
        return {
            "node_latency_ms": self.all_latency_stats(),
            "search_metrics": self.search_stats(),
            "counters": self.all_counters(),
            "recent_requests": self.recent_requests(20),
        }


# Singleton — imported everywhere
metrics_store = MetricsStore()


# ---------------------------------------------------------------------------
# NodeTracer — wraps LangGraph node functions
# ---------------------------------------------------------------------------

def _safe_extract(state: Dict, keys: List[str]) -> Dict:
    """Pull a handful of safe, serialisable values from state for span logging."""
    out = {}
    for k in keys:
        val = state.get(k)
        if val is None:
            continue
        if isinstance(val, (str, int, float, bool)):
            out[k] = val
        elif isinstance(val, list):
            out[k] = len(val)          # log list length, not full content
        elif isinstance(val, dict):
            out[k] = list(val.keys())  # log keys only
        else:
            out[k] = type(val).__name__
    return out


# Keys we want to capture per node (safe subset — no PII)
_NODE_INPUT_KEYS = {
    "intent_classifier":    ["query", "intent", "history"],
    "product_search":       ["query", "preferences"],
    "result_validator":     ["results"],
    "constraint_relaxer":   ["results", "relaxation_level"],
    "reranker":             ["results", "reranked_results"],
    "response_generator":   ["reranked_results", "generated_response"],
    "input_guardrail":      ["query"],
    "output_guardrail":     ["generated_response", "guardrail_status"],
    "personalization":      ["user_id", "personalization_context"],
    "clarification":        ["needs_clarification", "clarification_question"],
    "product_selection":    ["product_discussion_mode", "selected_product_id"],
    "product_detail_response": ["query", "product_discussion_mode"],
}


class NodeTracer:
    """
    Wraps node callables with MLflow span tracing + metrics recording.

    In _build_graph, replace:
        workflow.add_node("intent_classifier", self.intent_classifier_node)
    with:
        workflow.add_node("intent_classifier",
                          self.tracer.wrap("intent_classifier", self.intent_classifier_node))
    """

    def __init__(self):
        self._mlflow = _MLFLOW_AVAILABLE

    def wrap(self, node_name: str, fn: Callable) -> Callable:
        input_keys = _NODE_INPUT_KEYS.get(node_name, ["query"])

        @functools.wraps(fn)
        def wrapped(state: Dict) -> Dict:
            start_ms = time.time() * 1000
            error_msg: Optional[str] = None
            result: Dict = {}

            captured_inputs = _safe_extract(state, input_keys)

            try:
                if self._mlflow:
                    try:
                        import mlflow
                        with mlflow.start_span(name=node_name) as span:
                            span.set_inputs(captured_inputs)
                            result = fn(state)
                            captured_outputs = _safe_extract(result, input_keys)
                            span.set_outputs(captured_outputs)
                            elapsed = time.time() * 1000 - start_ms
                            span.set_attribute("latency_ms", round(elapsed, 2))
                            span.set_attribute("node", node_name)
                    except Exception as mlflow_err:
                        logger.debug(f"[OBSERVABILITY] MLflow span error: {mlflow_err}")
                        result = fn(state)
                else:
                    result = fn(state)

            except Exception as exc:
                error_msg = str(exc)
                metrics_store.increment(f"{node_name}.error")
                logger.error(f"[{node_name.upper()}] ❌ Error: {exc}")
                raise

            finally:
                elapsed = time.time() * 1000 - start_ms
                metrics_store.record_latency(node_name, elapsed)
                metrics_store.increment(f"{node_name}.calls")

                # Node-specific counter updates
                if node_name == "product_search" and result:
                    count = len(result.get("results", []))
                    metrics_store.record_search_count(count)
                    metrics_store.increment("search_total")
                    if count == 0:
                        metrics_store.increment("search_zero_results")

                if node_name == "input_guardrail":
                    if result and result.get("error"):
                        metrics_store.increment("guardrail_input.blocked")
                    else:
                        metrics_store.increment("guardrail_input.passed")

                if node_name == "output_guardrail" and result:
                    status = result.get("guardrail_status", "unknown")
                    metrics_store.increment(f"guardrail_output.{status}")

                if node_name == "intent_classifier" and result:
                    intent = result.get("intent", "unknown")
                    metrics_store.increment(f"intent.{intent}")

                logger.info(
                    f"⏱  [{node_name}] {elapsed:.1f}ms"
                    + (f" ❌ {error_msg}" if error_msg else "")
                )

            return result

        return wrapped


# ---------------------------------------------------------------------------
# Request-level span — call once per /api/search request
# ---------------------------------------------------------------------------

class RequestTrace:
    """Context manager that wraps an entire request in an MLflow run + span."""

    def __init__(self, query: str, user_id: Optional[str] = None):
        self._query = query[:120]
        self._user_id = user_id
        self._start = time.time()
        self._run = None

    def __enter__(self):
        metrics_store.increment("request.total")
        if _MLFLOW_AVAILABLE:
            try:
                import mlflow
                self._run = mlflow.start_run(
                    run_name=f"search:{self._query[:40]}",
                    tags={"source": "shopping_assistant", "user_id": self._user_id or "anon"},
                )
                self._run.__enter__()
            except Exception as exc:
                logger.debug(f"[OBSERVABILITY] MLflow run start failed: {exc}")
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        elapsed_ms = (time.time() - self._start) * 1000

        if exc_type is not None:
            metrics_store.increment("request.error")

        summary = {
            "query_preview": self._query,
            "duration_ms": round(elapsed_ms, 1),
            "error": str(exc_val) if exc_val else None,
        }
        metrics_store.record_request(summary)
        metrics_store.record_latency("request_total", elapsed_ms)

        if _MLFLOW_AVAILABLE and self._run:
            try:
                import mlflow
                mlflow.log_metric("request_duration_ms", elapsed_ms)
                if exc_type:
                    mlflow.set_tag("error", str(exc_val))
                self._run.__exit__(exc_type, exc_val, exc_tb)
            except Exception as exc:
                logger.debug(f"[OBSERVABILITY] MLflow run end failed: {exc}")

        logger.info(f"🔍 Request done in {elapsed_ms:.0f}ms — '{self._query[:60]}'")
        return False  # do not suppress exceptions
