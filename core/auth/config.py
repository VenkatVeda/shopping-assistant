import os

# JWT Configuration - use env vars in production
JWT_SECRET_KEY = os.environ.get("JWT_SECRET_KEY", "dev-jwt-secret-change-in-production")
JWT_ALGORITHM = "HS256"

ACCESS_TOKEN_EXPIRY = 15   # minutes
REFRESH_TOKEN_EXPIRY = 7   # days

# Google OAuth credentials - use env vars in production
GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_ID_REMOVED")
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET", "GOOGLE_CLIENT_SECRET_REMOVED")

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = "https://openidconnect.googleapis.com/v1/userinfo"
