"""
Unity AI Gateway client adapter — On-Behalf-Of (OBO) authentication.
"""

import requests
from flask import request as flask_request
from databricks.sdk import WorkspaceClient


class GatewayPolicyBlock(Exception):
    def __init__(self, policy: str, phase: str, content: str):
        self.policy = policy
        self.phase = phase
        self.content = content
        super().__init__(f"Blocked by policy: {policy}")


class _GatewayResponse:
    def __init__(self, content: str):
        self.content = content


class GatewayChatModel:
    def __init__(self, endpoint: str, temperature: float = 0.1, max_tokens: int = 500):
        self.endpoint = endpoint
        self.temperature = temperature
        self.max_tokens = max_tokens
        w = WorkspaceClient()
        self._host = w.config.host.rstrip("/")
        self._url = f"{self._host}/ai-gateway/mlflow/v1/chat/completions"

    def invoke(self, messages, max_tokens: int = None, temperature: float = None) -> _GatewayResponse:
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

        if response.status_code != 200:
            raise RuntimeError(
                f"Gateway call failed: status={response.status_code} "
                f"url={self._url} body={response.text[:1000]}"
            )

        data = response.json()
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