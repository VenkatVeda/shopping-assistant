"""
Unity AI Gateway client adapter — On-Behalf-Of (OBO) authentication.

Every call is also recorded in the audit trail (node_executions_raw, node_type="llm")
from inside invoke(). LangChain never sees these calls — this is a plain
requests.post, not a LangChain chat model — so the AuditTrailCallback cannot
record them; only this code sees the HTTP request and response.
"""

import re
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
    def __init__(self, policy: str, phase: str, content: str, reason: str = None, action: str = None):
        self.policy = policy
        self.phase = phase
        self.content = content
        self.reason = reason
        self.action = action
        super().__init__(f"Blocked by policy: {policy}")


class _GatewayResponse:
    def __init__(self, content: str, usage: dict = None, request_id: str = None):
        self.content = content
        self.usage = usage or {}          # prompt_tokens / completion_tokens / total_tokens from the gateway
        self.request_id = request_id      # the gateway's x-request-id for this call


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


# W3C trace context: "00-<32 hex trace id>-<16 hex span id>-<2 hex flags>". The gateway answers with
# a `traceresponse` header in this shape; its trace id is the key of the call's spans in the gateway table.
_TRACE_CONTEXT = re.compile(r"^[0-9a-f]{2}-([0-9a-f]{32})-([0-9a-f]{16})-[0-9a-f]{2}$")


def _otel_trace_id(headers):
    """OpenTelemetry trace id from the traceresponse (or traceparent) header, or None."""
    try:
        for name in ("traceresponse", "traceparent"):
            m = _TRACE_CONTEXT.match((headers.get(name) or "").strip().lower())
            if m:
                return m.group(1)
    except Exception:
        pass
    return None


def _hash(text):
    """Keyed hash of text through the audit wrapper (same HMAC as the other audit hashes), or None."""
    if not text or _audit_sink is None:
        return None
    try:
        return _audit_sink._hmac(text)
    except Exception:
        return None


# The gateway's reason can quote the user's text ("Contains personal name 'Nupur' ...").
# Quoted text is masked before the usual PII patterns run (same rule as sync_gateway_policies.py).
_QUOTED = re.compile(r"""(?<![A-Za-z])(['"])(?=\S)(.{1,60}?)(?<=\S)\1(?![A-Za-z])""")


def _mask_reason(text):
    if not text:
        return None
    masked = _QUOTED.sub(lambda m: f"{m.group(1)}[REDACTED]{m.group(1)}", str(text))
    try:
        from audit_wrapper import _redact_pii
        masked = _redact_pii(masked)
    except Exception:
        pass
    return masked[:1000]


def _record_gateway_call(endpoint: str, status: str, t0: float, info: dict,
                         error_msg: str = None) -> None:
    """Write one audit row for this gateway call. Never raises."""
    if _audit_sink is None:
        return
    try:
        from audit_wrapper import (_current_trace_id, _current_subject_ref, _ts_to_iso,
                                   get_active_node_id)

        usage = info.get("usage") or {}
        trace_id = _current_trace_id.get()
        meta = {
            "calling_node":      info.get("calling_node"),
            "endpoint":          endpoint,
            "http_status":       info.get("http_status"),
            "uses_ai_gateway":   True,
            "policy_action":     "DENY" if status == "blocked" else "ALLOW",
            "blocked_by_policy": info.get("policy"),
            "blocked_phase":     info.get("phase"),
            # From the response body's databricks_service_policy object (blocked calls only).
            "policy_decision":   info.get("policy_decision"),
            "policy_reason":     _mask_reason(info.get("policy_reason")),
            "finish_reason":     info.get("finish_reason"),
            "prompt_tokens":    usage.get("prompt_tokens"),
            "completion_tokens": usage.get("completion_tokens"),
            "response_chars":    info.get("response_chars"),
            # Join key to the gateway trace table (databricks.request_id), when the gateway returns it.
            "gateway_request_id":  info.get("gateway_request_id"),
            "request_id_header":   info.get("request_id_header"),
            "response_header_keys": info.get("response_header_keys"),
            "response_body_keys":   info.get("response_body_keys"),
            # Joins every span of this call in the gateway table (its trace_id), and gateway-side timing.
            "otel_trace_id":     info.get("otel_trace_id"),
            "server_timing":     info.get("server_timing"),
            # Size of what was sent. The text itself is not stored; hashes are added below.
            "message_count":     info.get("message_count"),
            "prompt_chars":      info.get("prompt_chars"),
        }
        calling_node = info.get("calling_node")
        kwargs = dict(
            trace_id      = trace_id,
            node_name     = f"gateway_llm_call:{calling_node or 'unknown'}",
            status        = status,
            latency_ms    = (time.time() - t0) * 1000,
            error_msg     = error_msg,
            subject_ref   = _current_subject_ref.get() or None,
            node_type     = "llm",
            model_name    = endpoint,
            tokens_used   = usage.get("total_tokens"),
            node_metadata = meta,
            started_at    = _ts_to_iso(t0),     # real start; the row is written after the call ends
            # the step (node) row that made this call, when the audit callback is running it
            parent_node_id = get_active_node_id(trace_id, calling_node) if calling_node else None,
        )
        prompt_text, response_text = info.get("_prompt_text"), info.get("_response_text")

        def _write():
            # Hashing may need the audit key, so it runs here and never delays the request.
            meta["prompt_hash"] = _hash(prompt_text)
            meta["response_hash"] = _hash(response_text)
            _audit_sink.log_node_execution(**kwargs)

        threading.Thread(target=_write, daemon=True).start()
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
            info.update(policy=b.policy, phase=b.phase, policy_reason=b.reason,
                        policy_decision=(b.action or "").upper() or None)
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

        # Only the size and (in the background thread) a keyed hash of what is sent are audited.
        info["message_count"] = len(messages)
        info["_prompt_text"] = "\n".join(
            str(m.get("content", "")) for m in messages if isinstance(m, dict))
        info["prompt_chars"] = len(info["_prompt_text"])

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
        info["otel_trace_id"] = _otel_trace_id(response.headers)
        info["server_timing"] = (response.headers.get("server-timing") or None)
        if info["server_timing"]:
            info["server_timing"] = str(info["server_timing"])[:200]

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
        choice = data["choices"][0]
        content = choice["message"]["content"]
        info["finish_reason"] = choice.get("finish_reason")
        info["_response_text"] = content if isinstance(content, str) else ""

        # A blocked response carries databricks_service_policy = {name, action, phase, reason}
        # (HTTP status is still 200). Passed calls carry no such object.
        svc = data.get("databricks_service_policy")
        svc = svc if isinstance(svc, dict) else {}
        policy_name = svc.get("name")
        policy_action = (svc.get("action") or "").upper()

        # Detect block via the policy object, finish_reason, or the block message text
        is_blocked = (
            bool(svc)
            or policy_action == "DENY"
            or choice.get("finish_reason") == "content_filter"
            or (
                isinstance(content, str)
                and content.startswith(("This request was blocked by the '",
                                        "This response was blocked by the '"))
                and "service policy" in content
            )
        )

        if is_blocked:
            # Fall back to the message text if the policy object is missing
            if not policy_name and isinstance(content, str) and "'" in content:
                try:
                    policy_name = content.split("'")[1]
                except IndexError:
                    policy_name = "unknown"
            phase = svc.get("phase") or (
                "post_call" if isinstance(content, str) and content.startswith("This response") else "pre_call"
            )
            raise GatewayPolicyBlock(
                policy=policy_name or "unknown",
                phase=phase,
                content=content,
                reason=svc.get("reason"),
                action=svc.get("action"),
            )

        return _GatewayResponse(content, usage=info.get("usage"), request_id=info.get("gateway_request_id"))
