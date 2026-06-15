"""
core.evals — node-level and end-to-end evaluation suite for the Shopping Assistant.

Entry points:
    from core.evals import EvalRunner
    runner = EvalRunner(chat_model)
    results = runner.run_all()
"""

from .runner import EvalRunner
from .evaluators import (
    IntentNodeEval,
    SearchNodeEval,
    RerankerNodeEval,
    ResponseNodeEval,
    GuardrailNodeEval,
    LatencyEval,
)

__all__ = [
    "EvalRunner",
    "IntentNodeEval",
    "SearchNodeEval",
    "RerankerNodeEval",
    "ResponseNodeEval",
    "GuardrailNodeEval",
    "LatencyEval",
]
