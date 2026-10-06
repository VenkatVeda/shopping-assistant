"""
Unity AI Gateway client adapter — On-Behalf-Of (OBO) authentication.

Every call is also recorded in the audit trail (node_executions_raw, node_type="llm")
from inside invoke(). LangChain never sees these calls — this is a plain
requests.post, not a LangChain chat model — so the AuditTrailCallback cannot
record them; only this code sees the HTTP request and response.
"""

import threading
import time

import requests
from flask import request as flask_request
from databricks.sdk import WorkspaceClient


# AuditWrapper instance, registered once by ShoppingAssistantWorkflow.__init__.
_audit_sink = None


def set_audit_sink(audit_wrapper) -> None:
    """Register the AuditWrapper that receives one row per gateway call."""
    global _audit_sink
    _audit_sink = audit_wrapper


class GatewayPolicyBlock(Exception):
    def __init__(self, policy: str, phase: str, content: str):
        self.policy = policy
        self.phase = phase
        self.content = content
        super().__init__(f"Blocked by policy: {policy}")


class _GatewayResponse:
    def __init__(self, content: str):
        self.content = content


def _calling_node():
    """Name of the LangGraph node this call is running inside, if known."""
    try:
        from langchain_core.runnables.config import var_child_runnable_config
        cfg = var_child_runnable_config.get() or {}
        return (cfg.get("metadata") or {}).get("langgraph_node")
    except Exception:
        return None


# Header names a gateway or proxy commonly uses for its request id. The first call after
# deployment also records every response header NAME (response_header_keys), so the real
# name can be confirmed from the audit table without guessing.
_REQUEST_ID_HEADERS = (
    "x-request-id", "x-databricks-request-id", "x-ms-request-id",
    "apim-request-id", "request-id", "x-correlation-id",
)


def _find_request_id(headers):
    """Return (header_name, value) for the gateway's request id, or (None, None)."""
    try:
        for name in _REQUEST_ID_HEADERS:
            value = headers.get(name)
            if value:
                return name, value
        for name, value in headers.items():
            if "request-id" in name.lower() or "request_id" in name.lower():
                return name, value
    except Exception:
        pass
    return None, None


def _record_gateway_call(endpoint: str, status: str, t0: float, info: dict,
                         error_msg: str = None) -> None:
    """Write one audit row for this gateway call. Never raises."""
    if _audit_sink is None:
        return
    try:
        from audit_wrapper import _current_trace_id, _current_subject_ref

        usage = info.get("usage") or {}
        meta = {
            "calling_node":      info.get("calling_node"),
            "endpoint":          endpoint,
            "http_status":       info.get("http_status"),
            "uses_ai_gateway":   True,
            "policy_action":     "DENY" if status == "blocked" else "ALLOW",
            "blocked_by_policy": info.get("policy"),
            "blocked_phase":     info.get("phase"),
            "prompt_tokens":     usage.get("prompt_tokens"),
            "completion_tokens": usage.get("completion_tokens"),
            "response_chars":    info.get("response_chars"),
            # Join key to the gateway trace table (databricks.request_id), when the gateway returns it.
            "gateway_request_id":  info.get("gateway_request_id"),
            "request_id_header":   info.get("request_id_header"),
            "response_header_keys": info.get("response_header_keys"),
            "response_body_keys":   info.get("response_body_keys"),
        }
        kwargs = dict(
            trace_id      = _current_trace_id.get(),
            node_name     = f"gateway_llm_call:{info.get('calling_node') or 'unknown'}",
            status        = status,
            latency_ms    = (time.time() - t0) * 1000,
            error_msg     = error_msg,
            subject_ref   = _current_subject_ref.get() or None,
            node_type     = "llm",
            model_name    = endpoint,
            tokens_used   = usage.get("total_tokens"),
            node_metadata = meta,
        )
        threading.Thread(
            target=_audit_sink.log_node_execution, kwargs=kwargs, daemon=True
        ).start()
    except Exception:
        pass


class GatewayChatModel:
    def __init__(self, endpoint: str, temperature: float = 0.1, max_tokens: int = 500):
        self.endpoint = endpoint
        self.temperature = temperature
        self.max_tokens = max_tokens
        w = WorkspaceClient()
        self._host = w.config.host.rstrip("/")
        self._url = f"{self._host}/ai-gateway/mlflow/v1/chat/completions"

    def invoke(self, messages, max_tokens: int = None, temperature: float = None) -> _GatewayResponse:
        t0 = time.time()
        info = {"calling_node": _calling_node()}
        try:
            response = self._invoke(messages, max_tokens, temperature, info)
        except GatewayPolicyBlock as b:
            info.update(policy=b.policy, phase=b.phase)
            _record_gateway_call(self.endpoint, "blocked", t0, info, error_msg=str(b))
            raise
        except Exception as e:
            _record_gateway_call(self.endpoint, "error", t0, info, error_msg=str(e)[:500])
            raise
        info["response_chars"] = len(response.content or "")
        _record_gateway_call(self.endpoint, "success", t0, info)
        return response

    def _invoke(self, messages, max_tokens, temperature, info: dict) -> _GatewayResponse:
        user_token = None
        try:
            user_token = flask_request.headers.get("X-Forwarded-Access-Token")
        except RuntimeError:
            pass

        if not user_token:
            raise RuntimeError("No X-Forwarded-Access-Token available on this request.")

        headers = {"Authorization": f"Bearer {user_token}", "Content-Type": "application/json"}

        # Fix: ensure messages is always a list, never a plain string
        if isinstance(messages, str):
            messages = [{"role": "user", "content": messages}]

        payload = {
            "model": self.endpoint,
            "messages": messages,
            "max_tokens": max_tokens or self.max_tokens,
            "temperature": temperature if temperature is not None else self.temperature,
        }
        response = requests.post(self._url, headers=headers, json=payload, timeout=60)
        info["http_status"] = response.status_code
        info["response_header_keys"] = sorted(response.headers.keys())
        info["request_id_header"], info["gateway_request_id"] = _find_request_id(response.headers)

        if response.status_code != 200:
            raise RuntimeError(
                f"Gateway call failed: status={response.status_code} "
                f"url={self._url} body={response.text[:1000]}"
            )

        data = response.json()
        info["usage"] = data.get("usage") or {}
        info["response_body_keys"] = sorted(data.keys()) if isinstance(data, dict) else None
        # If the id is not in a header, accept it from the body only when it is a UUID-shaped
        # value under an explicit request-id key (never the chat completion "id").
        if not info.get("gateway_request_id") and isinstance(data, dict):
            for key in ("request_id", "databricks.request_id"):
                if data.get(key):
                    info["request_id_header"], info["gateway_request_id"] = f"body:{key}", data[key]
                    break
        content = data["choices"][0]["message"]["content"]

        # Extract gateway policy decision from response metadata
        policy_action = data.get("databricks.policy.action")
        policy_name = data.get("databricks.policy.name")

        # Detect block via policy action OR block message text
        is_blocked = policy_action == "DENY" or (
            isinstance(content, str)
            and content.startswith("This request was blocked by the '")
            and "service policy" in content
        )

        if is_blocked:
            # Extract policy name from text if not in metadata
            if not policy_name and "'" in content:
                try:
                    policy_name = content.split("'")[1]
                except IndexError:
                    policy_name = "unknown"
            phase = "post_call" if "response" in content.lower() else "pre_call"
            raise GatewayPolicyBlock(
                policy=policy_name or "unknown",
                phase=phase,
                content=content
            )

        return _GatewayResponse(content)
