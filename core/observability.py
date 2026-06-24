"""
Observability layer for the Shopping Assistant.

Wraps every LangGraph node with:
  - MLflow span tracing (latency, inputs, outputs, errors, status)
  - In-memory rolling metrics buffer (p50 / p95 / p99 per node)
  - Structured stderr logging with emoji tags for easy log grep

Usage inside _build_graph:
    tracer = NodeTracer()
    workflow.add_node("intent_classifier", tracer.wrap("intent_classifier", self.intent_classifier_node))

Usage in process_query:
    with RequestTrace(query, user_id, session_id) as rt:
        result = self.app.invoke(...)
        rt.set_outputs(result)
"""

import os
import time
import hashlib
import json
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
        self._latency: Dict[str, RollingBuffer] = defaultdict(lambda: RollingBuffer(500))
        self._counters: Dict[str, int] = defaultdict(int)
        self._requests: List[Dict] = []
        self._request_maxlen = 200
        self._search_result_counts: RollingBuffer = RollingBuffer(500)

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
        return {
            "node_latency_ms": self.all_latency_stats(),
            "search_metrics": self.search_stats(),
            "counters": self.all_counters(),
            "recent_requests": self.recent_requests(20),
        }


metrics_store = MetricsStore()


# ---------------------------------------------------------------------------
# State hashing — stable fingerprint for DAG replay
# ---------------------------------------------------------------------------

def _state_hash(state: Dict) -> str:
    """
    Deterministic 8-char hex hash of the graph state dict.

    Only scalar + list-length values are hashed (no full product blobs)
    so the hash is fast and stable across Python runs.
    """
    try:
        fingerprint = {
            "query":               state.get("query", ""),
            "intent":              state.get("intent", ""),
            "history_len":         len(state.get("history", [])),
            "results_len":         len(state.get("results") or []),
            "reranked_len":        len(state.get("reranked_results") or []),
            "relaxation_level":    state.get("relaxation_level", 0),
            "result_count_status": state.get("result_count_status", ""),
            "needs_clarification": state.get("needs_clarification", False),
            "guardrail_status":    state.get("guardrail_status", ""),
            "error":               state.get("error", ""),
            "product_discussion":  state.get("product_discussion_mode", False),
        }
        raw = json.dumps(fingerprint, sort_keys=True, default=str)
        return hashlib.md5(raw.encode()).hexdigest()[:8]
    except Exception:
        return "--------"


# ---------------------------------------------------------------------------
# Rich input/output extractors per node
# ---------------------------------------------------------------------------

def _prefs_summary(prefs) -> Dict:
    """Serialise SearchPreferences into a loggable dict."""
    if prefs is None:
        return {}
    return {
        "colors":     getattr(prefs, "colors", []),
        "categories": getattr(prefs, "categories", []),
        "brands":     getattr(prefs, "brands", []),
        "materials":  getattr(prefs, "materials", []),
        "price_min":  getattr(prefs, "price_min", None),
        "price_max":  getattr(prefs, "price_max", None),
    }


def _extract_inputs(node_name: str, state: Dict) -> Dict:
    """Return a rich, safe, serialisable inputs dict for the given node."""
    q = state.get("query", "")
    prefs = state.get("preferences")

    if node_name == "input_guardrail":
        return {
            "query":         q[:200],
            "query_length":  len(q),
            "history_turns": len(state.get("history", [])),
            "has_summary":   bool(state.get("summary")),
        }

    if node_name == "intent_classifier":
        return {
            "query":              q[:200],
            "has_prev_prefs":     prefs is not None,
            "clarification_last": state.get("clarification_asked_last_turn", False),
            "discussion_mode":    state.get("product_discussion_mode", False),
        }

    if node_name == "product_search":
        p = _prefs_summary(prefs)
        filter_count = sum(1 for v in [p.get("colors"), p.get("categories"),
                                        p.get("brands"), p.get("materials")] if v)
        return {
            "query":            q[:200],
            "filter_count":     filter_count,
            "has_price_filter": bool(p.get("price_min") or p.get("price_max")),
            "preferences":      p,
        }

    if node_name == "result_validator":
        return {"result_count": len(state.get("results", []))}

    if node_name == "constraint_relaxer":
        return {
            "relaxation_level": state.get("relaxation_level", 0),
            "result_count":     len(state.get("results", [])),
            "preferences":      _prefs_summary(prefs),
        }

    if node_name == "reranker":
        return {"result_count": len(state.get("results", []))}

    if node_name == "response_generator":
        return {
            "intent":        state.get("intent", ""),
            "result_count":  len(state.get("reranked_results") or state.get("results", [])),
            "discussion_mode": state.get("product_discussion_mode", False),
        }

    if node_name == "output_guardrail":
        resp = state.get("generated_response", "") or ""
        return {"response_length": len(resp)}

    if node_name == "personalization":
        return {
            "user_id":    state.get("user_id"),
            "has_history": bool(state.get("history")),
        }

    if node_name == "clarification":
        return {
            "result_count_status": state.get("result_count_status"),
            "has_prev_prefs":      state.get("previous_preferences") is not None,
        }

    # fallback
    return {"query": q[:200]}


def _extract_outputs(node_name: str, result: Dict) -> Dict:
    """Return a rich, safe, serialisable outputs dict for the given node."""
    if not result:
        return {}

    if node_name == "input_guardrail":
        return {
            "passed":         not bool(result.get("error")),
            "blocked_reason": result.get("error", "")[:120] if result.get("error") else None,
        }

    if node_name == "intent_classifier":
        prefs = result.get("preferences")
        return {
            "intent":     result.get("intent", ""),
            "preferences": _prefs_summary(prefs),
        }

    if node_name == "product_search":
        results = result.get("results", [])
        return {
            "result_count": len(results),
            "has_error":    bool(result.get("error")),
        }

    if node_name == "result_validator":
        return {
            "status":       result.get("result_count_status", ""),
            "result_count": len(result.get("results", result.get("reranked_results", []) or [])),
        }

    if node_name == "constraint_relaxer":
        return {
            "new_relaxation_level": result.get("relaxation_level", 0),
            "relaxation_message":   (result.get("relaxation_message") or "")[:120],
        }

    if node_name == "reranker":
        return {"reranked_count": len(result.get("reranked_results", []))}

    if node_name == "response_generator":
        resp = result.get("generated_response", "") or ""
        return {"response_length": len(resp)}

    if node_name == "output_guardrail":
        issues = result.get("guardrail_issues", []) or []
        return {
            "guardrail_status":  result.get("guardrail_status", ""),
            "issues_count":      len(issues),
            "issues":            issues[:5],
            "was_corrected":     result.get("guardrail_status") in ("warning", "fail"),
        }

    if node_name == "personalization":
        ctx = result.get("personalization_context") or ""
        return {"context_length": len(ctx)}

    if node_name == "clarification":
        return {
            "needs_clarification": result.get("needs_clarification", False),
            "question_preview":    (result.get("clarification_question") or "")[:120],
        }

    return {}


# ---------------------------------------------------------------------------
# NodeTracer — wraps LangGraph node functions
# ---------------------------------------------------------------------------

class NodeTracer:
    """
    Wraps node callables with MLflow span tracing + metrics recording.

    Every node gets a child span under the active RequestTrace root span.
    Span inputs/outputs are structured per-node for meaningful observability.
    """

    def __init__(self):
        self._mlflow = _MLFLOW_AVAILABLE

    def wrap(self, node_name: str, fn: Callable) -> Callable:

        @functools.wraps(fn)
        def wrapped(state: Dict) -> Dict:
            start_ms = time.time() * 1000
            error_msg: Optional[str] = None
            result: Dict = {}

            inputs = _extract_inputs(node_name, state)
            input_hash = _state_hash(state)

            try:
                if self._mlflow:
                    try:
                        import mlflow
                        from mlflow.entities import SpanStatusCode
                        with mlflow.start_span(name=node_name) as span:
                            # structured inputs + input state fingerprint
                            span.set_inputs({**inputs, "input_state_hash": input_hash})
                            result = fn(state)
                            elapsed = time.time() * 1000 - start_ms
                            # merge output state into result for hashing
                            merged = {**state, **result}
                            output_hash = _state_hash(merged)
                            outputs = _extract_outputs(node_name, result)
                            outputs["latency_ms"]        = round(elapsed, 2)
                            outputs["output_state_hash"] = output_hash
                            span.set_outputs(outputs)
                            span.set_attribute("node",              node_name)
                            span.set_attribute("input_state_hash",  input_hash)
                            span.set_attribute("output_state_hash", output_hash)
                            span.set_attribute("latency_ms",        round(elapsed, 2))
                            span.set_status(SpanStatusCode.OK)
                    except ImportError:
                        result = fn(state)
                    except Exception as mlflow_err:
                        logger.debug(f"[OBSERVABILITY] span error in {node_name}: {mlflow_err}")
                        result = fn(state)
                else:
                    result = fn(state)

            except Exception as exc:
                error_msg = str(exc)
                metrics_store.increment(f"{node_name}.error")
                logger.error(f"[{node_name.upper()}] ❌ {exc}")
                # mark span failed if still in context
                if self._mlflow:
                    try:
                        import mlflow
                        from mlflow.entities import SpanStatusCode
                        active = mlflow.get_current_active_span()
                        if active:
                            active.set_status(SpanStatusCode.ERROR, str(exc))
                    except Exception:
                        pass
                raise

            finally:
                elapsed = time.time() * 1000 - start_ms
                metrics_store.record_latency(node_name, elapsed)
                metrics_store.increment(f"{node_name}.calls")

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
# RequestTrace — root MLflow trace per request
# ---------------------------------------------------------------------------

class RequestTrace:
    """
    Context manager that opens a root MLflow trace for one complete request.

    Usage in process_query():
        with RequestTrace(query, user_id, session_id) as rt:
            final_state = self.app.invoke(initial_state, config=config)
            rt.set_result(final_state)
        trace_id = rt.trace_id
    """

    def __init__(self, query: str, user_id: Optional[str] = None, session_id: Optional[str] = None):
        self._query = (query or "")[:200]
        self._user_id = user_id or "anon"
        self._session_id = session_id or ""
        self._start = time.time()
        self._span = None
        self._ctx = None
        self.trace_id: Optional[str] = None

    def __enter__(self):
        metrics_store.increment("request.total")
        if _MLFLOW_AVAILABLE:
            try:
                import mlflow
                self._ctx = mlflow.start_span(
                    name="shopping_assistant_request",
                )
                self._span = self._ctx.__enter__()
                self._span.set_inputs({
                    "query":      self._query,
                    "user_id":    self._user_id,
                    "session_id": self._session_id,
                })
                self._span.set_attribute("source", "shopping_assistant")
            except Exception as exc:
                logger.debug(f"[OBSERVABILITY] Root trace start failed: {exc}")
        return self

    def set_result(self, final_state: Dict):
        """Call after invoke() completes to attach outputs to the root span."""
        if not self._span:
            return
        try:
            elapsed_ms = round((time.time() - self._start) * 1000, 1)
            resp = final_state.get("safe_response") or final_state.get("generated_response") or ""
            results = final_state.get("reranked_results") or final_state.get("results") or []
            self._span.set_outputs({
                "intent":          final_state.get("intent", ""),
                "result_count":    len(results),
                "response_length": len(resp),
                "guardrail_status": final_state.get("guardrail_status", ""),
                "total_ms":        elapsed_ms,
                "error":           final_state.get("error", ""),
            })
            self._span.set_attribute("total_ms", elapsed_ms)
            self._span.set_attribute("intent", final_state.get("intent", ""))
            self._span.set_attribute("result_count", len(results))
        except Exception as exc:
            logger.debug(f"[OBSERVABILITY] set_result failed: {exc}")

    def __exit__(self, exc_type, exc_val, exc_tb):
        elapsed_ms = (time.time() - self._start) * 1000

        if exc_type is not None:
            metrics_store.increment("request.error")

        summary = {
            "query_preview": self._query,
            "duration_ms":   round(elapsed_ms, 1),
            "error":         str(exc_val) if exc_val else None,
        }
        metrics_store.record_request(summary)
        metrics_store.record_latency("request_total", elapsed_ms)

        if _MLFLOW_AVAILABLE and self._ctx:
            try:
                import mlflow
                from mlflow.entities import SpanStatusCode
                if self._span:
                    status = SpanStatusCode.ERROR if exc_type else SpanStatusCode.OK
                    self._span.set_status(status, str(exc_val) if exc_val else "")
                    try:
                        self.trace_id = str(self._span.request_id)
                    except Exception:
                        self.trace_id = None
                self._ctx.__exit__(exc_type, exc_val, exc_tb)
            except Exception as exc:
                logger.debug(f"[OBSERVABILITY] Root trace close failed: {exc}")

        logger.info(f"🔍 Request done in {elapsed_ms:.0f}ms — '{self._query[:60]}'")
        return False
