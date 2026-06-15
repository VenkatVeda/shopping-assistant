"""
Per-node evaluators for the Shopping Assistant.

Each evaluator is independent and can be called:
  - inline (pass live state dicts from the workflow)
  - offline (pass saved state snapshots for batch eval)

Every evaluator returns an EvalResult dataclass containing:
  - score       (0.0–1.0 or raw metric)
  - passed      (bool)
  - details     (dict of sub-scores and diagnostics)
  - suggestions (list of improvement hints)
"""

from __future__ import annotations

import re
import json
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# EvalResult
# ---------------------------------------------------------------------------

@dataclass
class EvalResult:
    node: str
    score: float          # 0.0 – 1.0 normalised (or raw metric for latency)
    passed: bool
    details: Dict[str, Any] = field(default_factory=dict)
    suggestions: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict:
        return {
            "node": self.node,
            "score": round(self.score, 3),
            "passed": self.passed,
            "details": self.details,
            "suggestions": self.suggestions,
        }


# ---------------------------------------------------------------------------
# Base evaluator
# ---------------------------------------------------------------------------

class BaseEvaluator:
    name: str = "base"

    def evaluate(self, *args, **kwargs) -> EvalResult:
        raise NotImplementedError

    def _result(self, score: float, passed: bool, details: Dict, suggestions: List[str]) -> EvalResult:
        return EvalResult(
            node=self.name,
            score=score,
            passed=passed,
            details=details,
            suggestions=suggestions,
        )


# ---------------------------------------------------------------------------
# 1. Intent Node Evaluator
#    Checks: intent was classified, preferences extracted when expected
# ---------------------------------------------------------------------------

# Keyword sets used to assert expected intent without calling LLM
_SHOPPING_SIGNALS = [
    "bag", "wallet", "purse", "tote", "handbag", "clutch", "backpack",
    "leather", "canvas", "show me", "find", "looking for", "under $", "price",
    "brand", "color", "colour", "black", "brown", "red", "blue",
]
_CHAT_SIGNALS = ["weather", "joke", "hello", "hi", "how are you", "what is", "who is"]


class IntentNodeEval(BaseEvaluator):
    """
    Rule-based evaluator for intent_classifier_node output.

    Args:
        result_state: the dict returned by intent_classifier_node
        input_query:  the user query that was classified
    """

    name = "intent_classifier"

    def evaluate(self, result_state: Dict, input_query: str) -> EvalResult:
        intent = result_state.get("intent", "")
        preferences = result_state.get("preferences")
        query_lower = input_query.lower()

        details: Dict[str, Any] = {"intent": intent, "query_preview": input_query[:80]}
        suggestions: List[str] = []
        issues = 0

        # --- Intent correctness (rule-based heuristic) ---
        has_shopping = any(s in query_lower for s in _SHOPPING_SIGNALS)
        has_chat = any(s in query_lower for s in _CHAT_SIGNALS)

        if has_shopping and intent not in ("shopping", "preference_update"):
            suggestions.append(
                f"Query looks like shopping but intent='{intent}'. "
                "Check intent prompt — shopping signals present."
            )
            issues += 1

        if has_chat and not has_shopping and intent == "shopping":
            suggestions.append(
                f"Query looks like chat but intent='{intent}'. May be over-triggering shopping."
            )
            issues += 1

        details["expected_intent"] = "shopping" if has_shopping else ("chat" if has_chat else "ambiguous")

        # --- Preference extraction completeness ---
        if intent in ("shopping", "preference_update") and preferences:
            extracted_fields = []
            for attr in ("colors", "brands", "categories", "materials", "price_min", "price_max"):
                val = getattr(preferences, attr, None)
                if val:
                    extracted_fields.append(attr)
            details["extracted_preference_fields"] = extracted_fields

            if not extracted_fields:
                suggestions.append(
                    "Shopping intent but zero preference fields extracted. "
                    "Consider tuning the intent prompt to extract more structure."
                )
                issues += 1
        elif intent in ("shopping", "preference_update") and not preferences:
            suggestions.append("Shopping intent but preferences object is None.")
            issues += 1

        score = max(0.0, 1.0 - (issues * 0.4))
        return self._result(score, score >= 0.6, details, suggestions)


# ---------------------------------------------------------------------------
# 2. Search Node Evaluator
#    Checks: result counts, zero-result flag, filter application
# ---------------------------------------------------------------------------

class SearchNodeEval(BaseEvaluator):
    """
    Evaluates product_search_node output.

    Args:
        result_state:   dict returned by product_search_node
        preferences:    SearchPreferences that were used
    """

    name = "product_search"
    OPTIMAL_MIN = 3
    OPTIMAL_MAX = 30

    def evaluate(self, result_state: Dict, preferences=None) -> EvalResult:
        results = result_state.get("results", [])
        count = len(results)
        details: Dict[str, Any] = {"result_count": count}
        suggestions: List[str] = []
        issues = 0

        if count == 0:
            suggestions.append(
                "Zero results returned. "
                "Verify vector index is populated and embedding matches index dimensions. "
                "Consider widening the constraint relaxation levels."
            )
            issues += 2

        elif count > 50:
            suggestions.append(
                f"{count} results returned (> 50). "
                "Reranker will receive a large candidate pool — consider raising the top_k filter."
            )
            issues += 1

        # Deduplication check
        ids = [r.get("id") for r in results if r.get("id")]
        unique_ids = set(ids)
        if len(ids) != len(unique_ids):
            details["duplicate_count"] = len(ids) - len(unique_ids)
            suggestions.append("Duplicate product IDs detected — check deduplication logic.")
            issues += 1

        # Score distribution check
        scores = [r.get("score", 0) for r in results]
        if scores:
            details["avg_similarity_score"] = round(sum(scores) / len(scores), 4)
            details["max_similarity_score"] = round(max(scores), 4)
            if max(scores) < 0.3:
                suggestions.append(
                    "Max similarity score < 0.3 — results may be semantically unrelated. "
                    "Check embedding model and index alignment."
                )
                issues += 1

        # Preference filter stats
        if preferences:
            applied = []
            for attr in ("colors", "brands", "categories", "materials"):
                if getattr(preferences, attr, None):
                    applied.append(attr)
            details["filters_applied"] = applied

        score = max(0.0, 1.0 - (issues * 0.3))
        return self._result(score, count > 0, details, suggestions)


# ---------------------------------------------------------------------------
# 3. Reranker Node Evaluator
#    Checks: score ordering, threshold violations, diversity
# ---------------------------------------------------------------------------

class RerankerNodeEval(BaseEvaluator):
    """
    Evaluates reranker_node output.

    Args:
        result_state:  dict returned by reranker_node (contains reranked_results)
    """

    name = "reranker"
    MIN_ACCEPTABLE_SCORE = 40   # out of 100

    def evaluate(self, result_state: Dict) -> EvalResult:
        reranked = result_state.get("reranked_results", [])
        details: Dict[str, Any] = {"reranked_count": len(reranked)}
        suggestions: List[str] = []
        issues = 0

        if not reranked:
            return self._result(0.0, False, details, ["reranked_results is empty."])

        scores = [r.get("rerank_score", r.get("score", 0)) for r in reranked]
        details["top_score"] = round(scores[0], 1) if scores else 0
        details["avg_score"] = round(sum(scores) / len(scores), 1) if scores else 0
        details["min_score"] = round(min(scores), 1) if scores else 0

        # Ordering check
        is_ordered = all(scores[i] >= scores[i + 1] for i in range(len(scores) - 1))
        details["is_descending_order"] = is_ordered
        if not is_ordered:
            suggestions.append(
                "Reranked results are NOT in descending score order. "
                "Verify sort logic after LLM scoring."
            )
            issues += 1

        # Low-score check
        below_threshold = [s for s in scores if s < self.MIN_ACCEPTABLE_SCORE]
        if below_threshold:
            details["below_threshold_count"] = len(below_threshold)
            suggestions.append(
                f"{len(below_threshold)} products scored below {self.MIN_ACCEPTABLE_SCORE}/100. "
                "Consider raising the relevance cutoff or improving query representation."
            )
            issues += 1

        # Diversity check — same brand appearing too often
        brands = [r.get("brand", r.get("metadata", {}).get("brand", "")) for r in reranked[:5]]
        brand_counts: Dict[str, int] = {}
        for b in brands:
            if b:
                brand_counts[b] = brand_counts.get(b, 0) + 1
        dominant = {b: c for b, c in brand_counts.items() if c >= 4}
        if dominant:
            details["dominant_brands_top5"] = dominant
            suggestions.append(
                f"Top 5 results are dominated by {list(dominant.keys())}. "
                "Consider adding brand-diversity boosting to the reranker prompt."
            )

        score = max(0.0, 1.0 - (issues * 0.35))
        return self._result(score, score >= 0.6, details, suggestions)


# ---------------------------------------------------------------------------
# 4. Response Node Evaluator  (LLM-as-judge)
#    Checks: groundedness, relevance, helpfulness, length
# ---------------------------------------------------------------------------

_RESPONSE_EVAL_PROMPT = """You are a quality evaluator for a shopping assistant chatbot.
Assess the assistant's response using the criteria below. Return ONLY valid JSON.

User query: {query}
Assistant response: {response}
Products in context (top 3): {products_summary}

Evaluate on a scale 0-5 for each criterion:
- groundedness: Does the response only mention products that are in the context?
  5 = fully grounded, 0 = hallucinates product names or specs not in context.
- relevance: Does the response address what the user asked?
  5 = perfectly relevant, 0 = completely off-topic.
- helpfulness: Is the response actionable and useful for the user to make a decision?
  5 = very helpful with clear guidance, 0 = not useful.
- tone: Is the response friendly, concise, and professional?
  5 = excellent tone, 0 = inappropriate or robotic.

Also provide:
- issues: list of specific problems found (empty list if none)
- suggestions: list of improvement recommendations (empty list if none)

JSON format (no markdown, no explanation):
{{
  "groundedness": <0-5>,
  "relevance": <0-5>,
  "helpfulness": <0-5>,
  "tone": <0-5>,
  "issues": [...],
  "suggestions": [...]
}}"""


class ResponseNodeEval(BaseEvaluator):
    """
    LLM-as-judge evaluator for response_generator_node.

    Args:
        chat_model:     the ChatDatabricks (or any LangChain chat model) instance
        result_state:   dict returned by response_generator_node
        query:          original user query
    """

    name = "response_generator"
    PASS_THRESHOLD = 3.0   # average score out of 5

    def __init__(self, chat_model):
        self._model = chat_model

    def evaluate(self, result_state: Dict, query: str) -> EvalResult:
        response = result_state.get("generated_response") or result_state.get("safe_response", "")
        products = result_state.get("reranked_results") or result_state.get("results", [])
        details: Dict[str, Any] = {
            "response_length": len(response),
            "query_preview": query[:80],
        }
        suggestions: List[str] = []

        if not response:
            return self._result(0.0, False, details, ["generated_response is empty."])

        # Basic length checks (rule-based, no LLM needed)
        if len(response) < 40:
            suggestions.append("Response is very short (< 40 chars). May not be helpful.")
        if len(response) > 2000:
            suggestions.append("Response is very long (> 2000 chars). Consider trimming.")

        # Build product summary for the judge
        products_summary = "; ".join(
            f"{p.get('name', p.get('metadata', {}).get('name', 'Unknown'))} "
            f"by {p.get('brand', p.get('metadata', {}).get('brand', 'Unknown'))}"
            for p in products[:3]
        ) or "No products in context"

        # LLM-as-judge call
        judge_scores: Dict[str, float] = {}
        try:
            prompt = _RESPONSE_EVAL_PROMPT.format(
                query=query[:300],
                response=response[:800],
                products_summary=products_summary[:400],
            )
            llm_response = self._model.invoke(prompt)
            content = llm_response.content if hasattr(llm_response, "content") else str(llm_response)
            json_match = re.search(r"\{.*\}", content, re.DOTALL)
            if json_match:
                parsed = json.loads(json_match.group(0))
                for criterion in ("groundedness", "relevance", "helpfulness", "tone"):
                    judge_scores[criterion] = float(parsed.get(criterion, 0))
                suggestions.extend(parsed.get("issues", []))
                suggestions.extend(parsed.get("suggestions", []))
                details["judge_scores"] = judge_scores
                details["judge_issues"] = parsed.get("issues", [])
            else:
                suggestions.append("LLM judge returned unparseable response — check model output.")
        except Exception as exc:
            logger.warning(f"[RESPONSE EVAL] LLM judge failed: {exc}")
            suggestions.append(f"LLM judge unavailable: {exc}")

        # Compute aggregate score
        if judge_scores:
            avg = sum(judge_scores.values()) / len(judge_scores)
            normalised = avg / 5.0
        else:
            # fallback: pass if response is non-empty and reasonably sized
            normalised = 0.5 if 40 <= len(response) <= 2000 else 0.3

        details["normalised_score"] = round(normalised, 3)
        passed = normalised >= (self.PASS_THRESHOLD / 5.0)
        return self._result(normalised, passed, details, suggestions)


# ---------------------------------------------------------------------------
# 5. Guardrail Evaluator
#    Checks: input block rate, output pass/warn/fail distribution
# ---------------------------------------------------------------------------

class GuardrailNodeEval(BaseEvaluator):
    """
    Evaluates guardrail health from the MetricsStore counters.
    No live state needed — reads from the global metrics_store.
    """

    name = "guardrails"
    MAX_FAIL_RATE = 0.05      # alert if output guardrail fails > 5 % of the time
    MIN_INPUT_BLOCK_RATE = 0  # informational only

    def evaluate(self) -> EvalResult:
        from core.observability import metrics_store

        counters = metrics_store.all_counters()
        details: Dict[str, Any] = {}
        suggestions: List[str] = []
        issues = 0

        # Input guardrail
        passed_in = counters.get("guardrail_input.passed", 0)
        blocked_in = counters.get("guardrail_input.blocked", 0)
        total_in = passed_in + blocked_in
        details["input_guardrail"] = {
            "total": total_in,
            "passed": passed_in,
            "blocked": blocked_in,
            "block_rate_pct": round(blocked_in / total_in * 100, 1) if total_in else 0,
        }

        # Output guardrail
        out_pass = counters.get("guardrail_output.pass", 0)
        out_warn = counters.get("guardrail_output.warning", 0)
        out_fail = counters.get("guardrail_output.fail", 0)
        total_out = out_pass + out_warn + out_fail
        fail_rate = out_fail / total_out if total_out else 0
        details["output_guardrail"] = {
            "total": total_out,
            "pass": out_pass,
            "warning": out_warn,
            "fail": out_fail,
            "fail_rate_pct": round(fail_rate * 100, 1),
        }

        if fail_rate > self.MAX_FAIL_RATE:
            suggestions.append(
                f"Output guardrail fail rate is {fail_rate*100:.1f}% (threshold {self.MAX_FAIL_RATE*100:.0f}%). "
                "Investigate whether the response generator is hallucinating or the factual "
                "validation prompt is too strict."
            )
            issues += 1

        if out_warn > out_pass * 0.3:
            suggestions.append(
                "High output guardrail warning rate. "
                "Consider reviewing the content safety prompt thresholds."
            )

        score = max(0.0, 1.0 - (issues * 0.5))
        return self._result(score, score >= 0.5, details, suggestions)


# ---------------------------------------------------------------------------
# 6. Latency Evaluator
#    Checks: p95 per node against SLO thresholds
# ---------------------------------------------------------------------------

# SLO thresholds in milliseconds — tune per deployment
_NODE_SLO_MS: Dict[str, float] = {
    "input_guardrail":        500,
    "intent_classifier":     1500,
    "product_search":        2000,   # includes embedding + vector search
    "constraint_relaxer":     200,
    "reranker":              3000,
    "response_generator":    2500,
    "output_guardrail":      1000,
    "personalization":        800,
    "clarification":          500,
    "product_selection":      800,
    "product_detail_response": 2000,
    "request_total":         8000,   # end-to-end
}


class LatencyEval(BaseEvaluator):
    """
    Evaluates per-node latency against defined SLO thresholds.
    Reads from the global metrics_store — no live state required.
    """

    name = "latency"

    def evaluate(self) -> EvalResult:
        from core.observability import metrics_store

        all_stats = metrics_store.all_latency_stats()
        details: Dict[str, Any] = {}
        suggestions: List[str] = []
        violations = 0

        for node, slo_ms in _NODE_SLO_MS.items():
            stats = all_stats.get(node)
            if not stats or stats.get("count", 0) == 0:
                continue

            p95 = stats.get("p95", 0)
            p99 = stats.get("p99", 0)
            details[node] = {
                "count": stats["count"],
                "avg_ms": stats["avg"],
                "p50_ms": stats["p50"],
                "p95_ms": p95,
                "p99_ms": p99,
                "slo_ms": slo_ms,
                "slo_met": p95 <= slo_ms,
            }

            if p95 > slo_ms:
                violations += 1
                suggestions.append(
                    f"{node}: p95={p95:.0f}ms exceeds SLO of {slo_ms:.0f}ms. "
                    f"(p99={p99:.0f}ms, avg={stats['avg']:.0f}ms)"
                )

        total_nodes = len([n for n in _NODE_SLO_MS if all_stats.get(n, {}).get("count", 0) > 0])
        score = max(0.0, 1.0 - (violations / max(total_nodes, 1)))

        return self._result(score, violations == 0, details, suggestions)
