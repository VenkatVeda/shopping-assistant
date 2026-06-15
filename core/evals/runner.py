"""
EvalRunner — orchestrates the full evaluation suite.

Usage:
    from core.evals import EvalRunner

    # during request processing (inline eval after each search)
    runner = EvalRunner(chat_model=workflow.chat_model)
    result = runner.eval_search_node(result_state=state, preferences=prefs)

    # on-demand full suite via /evals/run endpoint
    runner = EvalRunner(chat_model=workflow.chat_model)
    report = runner.run_all(sample_traces=recent_request_snapshots)
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional

from .evaluators import (
    EvalResult,
    GuardrailNodeEval,
    IntentNodeEval,
    LatencyEval,
    RerankerNodeEval,
    ResponseNodeEval,
    SearchNodeEval,
)

logger = logging.getLogger(__name__)


class EvalRunner:
    """
    Runs individual or full-suite evaluations and optionally logs results
    to MLflow as run metrics.

    Args:
        chat_model: a LangChain-compatible chat model for LLM-as-judge evals.
                    Pass None to skip ResponseNodeEval (rule-based only).
    """

    def __init__(self, chat_model=None):
        self._model = chat_model
        self._intent_eval = IntentNodeEval()
        self._search_eval = SearchNodeEval()
        self._reranker_eval = RerankerNodeEval()
        self._response_eval = ResponseNodeEval(chat_model) if chat_model else None
        self._guardrail_eval = GuardrailNodeEval()
        self._latency_eval = LatencyEval()

    # ------------------------------------------------------------------
    # Per-node convenience wrappers
    # ------------------------------------------------------------------

    def eval_intent_node(self, result_state: Dict, input_query: str) -> EvalResult:
        return self._intent_eval.evaluate(result_state, input_query)

    def eval_search_node(self, result_state: Dict, preferences=None) -> EvalResult:
        return self._search_eval.evaluate(result_state, preferences)

    def eval_reranker_node(self, result_state: Dict) -> EvalResult:
        return self._reranker_eval.evaluate(result_state)

    def eval_response_node(self, result_state: Dict, query: str) -> EvalResult:
        if self._response_eval is None:
            return EvalResult(
                node="response_generator",
                score=0.0,
                passed=False,
                details={"reason": "chat_model not provided to EvalRunner"},
                suggestions=["Initialise EvalRunner with a chat_model to enable LLM-as-judge."],
            )
        return self._response_eval.evaluate(result_state, query)

    def eval_guardrails(self) -> EvalResult:
        return self._guardrail_eval.evaluate()

    def eval_latency(self) -> EvalResult:
        return self._latency_eval.evaluate()

    # ------------------------------------------------------------------
    # Full suite
    # ------------------------------------------------------------------

    def run_all(self, sample_traces: Optional[List[Dict]] = None) -> Dict[str, Any]:
        """
        Run every evaluator and return a consolidated report.

        Args:
            sample_traces: optional list of recent request state snapshots
                           (each dict contains query, result_state, etc.)
                           When None, only metric-based evaluators run.

        Returns:
            {
                "timestamp": "...",
                "overall_pass": bool,
                "overall_score": float,
                "node_results": { node_name: EvalResult.to_dict(), ... },
                "failed_nodes": [...],
                "suggestions": [...],
            }
        """
        started = time.time()
        node_results: Dict[str, Dict] = {}
        all_suggestions: List[str] = []
        failed_nodes: List[str] = []

        # --- Metric-based evaluators (no sample traces required) ---
        for ev in (self._guardrail_eval, self._latency_eval):
            try:
                r = ev.evaluate()
                node_results[r.node] = r.to_dict()
                if not r.passed:
                    failed_nodes.append(r.node)
                all_suggestions.extend(r.suggestions)
            except Exception as exc:
                logger.error(f"[EVAL RUNNER] {ev.name} failed: {exc}")
                node_results[ev.name] = {
                    "node": ev.name, "score": 0, "passed": False,
                    "details": {"error": str(exc)}, "suggestions": [],
                }

        # --- Trace-based evaluators ---
        if sample_traces:
            intent_results: List[EvalResult] = []
            search_results: List[EvalResult] = []
            reranker_results: List[EvalResult] = []
            response_results: List[EvalResult] = []

            for trace in sample_traces[:20]:  # cap at 20 to avoid LLM cost explosion
                query = trace.get("query", "")
                state = trace.get("state", {})

                # intent
                try:
                    intent_results.append(self._intent_eval.evaluate(state, query))
                except Exception as exc:
                    logger.warning(f"[EVAL RUNNER] IntentEval trace error: {exc}")

                # search
                try:
                    search_results.append(self._search_eval.evaluate(state))
                except Exception as exc:
                    logger.warning(f"[EVAL RUNNER] SearchEval trace error: {exc}")

                # reranker
                try:
                    reranker_results.append(self._reranker_eval.evaluate(state))
                except Exception as exc:
                    logger.warning(f"[EVAL RUNNER] RerankerEval trace error: {exc}")

                # response (LLM judge — expensive, sample only first 5)
                if self._response_eval and len(response_results) < 5:
                    try:
                        response_results.append(self._response_eval.evaluate(state, query))
                    except Exception as exc:
                        logger.warning(f"[EVAL RUNNER] ResponseEval trace error: {exc}")

            def _aggregate(results: List[EvalResult], name: str) -> Dict:
                if not results:
                    return {"node": name, "score": None, "passed": None,
                            "details": {"count": 0}, "suggestions": []}
                scores = [r.score for r in results]
                avg_score = sum(scores) / len(scores)
                passed = sum(1 for r in results if r.passed)
                all_sug = list({s for r in results for s in r.suggestions})
                return {
                    "node": name,
                    "score": round(avg_score, 3),
                    "passed": passed >= len(results) * 0.7,  # 70% pass rate
                    "details": {
                        "sample_count": len(results),
                        "pass_count": passed,
                        "avg_score": round(avg_score, 3),
                        "min_score": round(min(scores), 3),
                        "max_score": round(max(scores), 3),
                    },
                    "suggestions": all_sug,
                }

            for evname, evresults in [
                ("intent_classifier", intent_results),
                ("product_search", search_results),
                ("reranker", reranker_results),
                ("response_generator", response_results),
            ]:
                agg = _aggregate(evresults, evname)
                node_results[evname] = agg
                if agg["passed"] is False:
                    failed_nodes.append(evname)
                all_suggestions.extend(agg.get("suggestions", []))

        # --- Log to MLflow ---
        try:
            import mlflow
            with mlflow.start_run(run_name="eval_suite"):
                for node, res in node_results.items():
                    if res.get("score") is not None:
                        mlflow.log_metric(f"eval/{node}/score", res["score"])
                    mlflow.log_metric(f"eval/{node}/passed", int(bool(res.get("passed"))))
                mlflow.log_metric("eval/failed_node_count", len(failed_nodes))
                mlflow.log_metric("eval/overall_pass", int(len(failed_nodes) == 0))
        except Exception as exc:
            logger.debug(f"[EVAL RUNNER] MLflow logging skipped: {exc}")

        # --- Build overall score ---
        scored = [r["score"] for r in node_results.values() if r.get("score") is not None]
        overall_score = round(sum(scored) / len(scored), 3) if scored else 0.0

        elapsed = round((time.time() - started) * 1000, 1)
        from datetime import datetime

        return {
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "eval_duration_ms": elapsed,
            "overall_pass": len(failed_nodes) == 0,
            "overall_score": overall_score,
            "node_results": node_results,
            "failed_nodes": failed_nodes,
            "suggestions": list(dict.fromkeys(all_suggestions)),  # dedup, preserve order
        }
