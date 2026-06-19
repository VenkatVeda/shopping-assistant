import os

def _get_config(env_var, scope="shopping-assistant", secret_key=None):
    """Read from env var first, fall back to Databricks secret scope."""
    val = os.environ.get(env_var, "")
    if not val and secret_key:
        try:
            from databricks.sdk import WorkspaceClient
            import base64
            raw = WorkspaceClient().secrets.get_secret(scope=scope, key=secret_key).value
            # SDK returns base64-encoded bytes — decode to string
            val = base64.b64decode(raw).decode("utf-8")
        except Exception:
            pass
    return val

# JWT Configuration
JWT_SECRET_KEY = _get_config("JWT_SECRET_KEY", secret_key="jwt-secret-key")
JWT_ALGORITHM = "HS256"

ACCESS_TOKEN_EXPIRY = 15   # minutes
REFRESH_TOKEN_EXPIRY = 7   # days

# Google OAuth credentials
GOOGLE_CLIENT_ID     = _get_config("GOOGLE_CLIENT_ID",     scope="shopping_assistant", secret_key="google-client-id")
GOOGLE_CLIENT_SECRET = _get_config("GOOGLE_CLIENT_SECRET", scope="shopping_assistant", secret_key="google-client-secret")

GOOGLE_AUTH_URL     = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL    = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = "https://openidconnect.googleapis.com/v1/userinfo"
