"""User-facing messages for gateway policy blocks (no network, no Databricks)."""
import os, sys, importlib.util
here = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("gateway_client", os.path.join(here, "..", "core", "gateway_client.py"))
gc = importlib.util.module_from_spec(spec); spec.loader.exec_module(gc)
m = gc.user_message_for_block

HARM = "Your query has been blocked because it contains harmful content."
VIOL = "Your query has been blocked because it violates our usage policies."
cases = [
    ("block-unsafe-content", "pre_call", "intent_classifier", HARM),
    ("block-jailbreak",      "pre_call", "intent_classifier", VIOL),
    ("off-topic-restriction","pre_call", "intent_classifier", gc._MSG_OFF_TOPIC),
    ("spam-abuse-policy",    "pre_call", "response_generator", gc._MSG_SPAM),
    ("mask-personal-data",   "pre_call", "intent_classifier", gc._MSG_PII),
    ("some-new-policy",      "pre_call", "reranker",          VIOL),
    (None,                   None,       None,                VIOL),
    # the model's reply was blocked, or the blocked prompt was the app's own: never "your query"
    ("block-unsafe-content", "post_call", "response_generator", gc._MSG_RESPONSE),
    ("off-topic-restriction","pre_call",  "output_guardrail",    gc._MSG_RESPONSE),
]
bad = [(c[:3], m(*c[:3])) for c in cases if m(*c[:3]) != c[3]]
b = gc.GatewayPolicyBlock("p", "pre_call", "x")
assert b.calling_node is None
print("FAIL" if bad else "RESULT: all block messages ok", bad if bad else "")
sys.exit(1 if bad else 0)
