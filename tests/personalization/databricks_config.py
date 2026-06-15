"""
Databricks LLM configuration for testing.
Sets up the Databricks Foundation Models API client
using Databricks-native authentication.
"""

from typing import Optional


class DatabricksLLMClient:
    """
    Wrapper for Databricks Foundation Models API.
    Uses Databricks-native authentication (no PAT, no .env).
    """

    def __init__(self):
        """
        Initialize Databricks client using native workspace authentication.
        This works only when running inside a Databricks workspace.
        """

        try:
            from databricks.sdk import WorkspaceClient
            from databricks.sdk.service.serving import ChatMessage, ChatMessageRole

            # Native authentication (workspace identity)
            self.client = WorkspaceClient()

            self.ChatMessage = ChatMessage
            self.ChatMessageRole = ChatMessageRole

            print("[DATABRICKS] ✓ Using Databricks-native authentication")

        except ImportError:
            raise ImportError(
                "❌ Databricks SDK not installed.\n"
                "Install with: pip install databricks-sdk"
            )
        except Exception as e:
            raise Exception(
                "❌ Failed to initialize Databricks WorkspaceClient.\n"
                "Ensure this code is running inside a Databricks workspace.\n"
                f"Error: {e}"
            )

    def predict(self, endpoint: str, inputs: dict) -> dict:
        """
        Call Databricks Foundation Model endpoint.

        Args:
            endpoint: Model endpoint name
            inputs: {
                "messages": [
                    {"role": "system", "content": "..."},
                    {"role": "user", "content": "..."}
                ],
                "max_tokens": 500,
                "temperature": 0.1
            }

        Returns:
            Response dictionary compatible with OpenAI-style format
        """

        try:
            messages = [
                self.ChatMessage(
                    role=self.ChatMessageRole(msg["role"]),
                    content=msg["content"]
                )
                for msg in inputs.get("messages", [])
            ]

            response = self.client.serving_endpoints.query(
                name=endpoint,
                messages=messages,
                max_tokens=inputs.get("max_tokens", 500),
                temperature=inputs.get("temperature", 0.1)
            )

            return {
                "choices": [
                    {
                        "message": {
                            "content": response.choices[0].message.content
                        }
                    }
                ]
            }

        except Exception as e:
            print(f"[DATABRICKS] API Error: {e}")
            raise


def create_databricks_client() -> DatabricksLLMClient:
    """
    Convenience factory for Databricks LLM client.

    Usage:
        llm_client = create_databricks_client()
    """
    return DatabricksLLMClient()