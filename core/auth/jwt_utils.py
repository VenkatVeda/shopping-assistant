import jwt
from datetime import datetime, timedelta
from .config import JWT_SECRET_KEY, JWT_ALGORITHM, ACCESS_TOKEN_EXPIRY, REFRESH_TOKEN_EXPIRY

def generate_access_token(user_id, email, name):
    payload = {
        "user_id": user_id,
        "email": email,
        "name": name,
        "type": "access",
        "iat": datetime.utcnow(),
        "exp": datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRY),
    }
    return jwt.encode(payload, JWT_SECRET_KEY, algorithm=JWT_ALGORITHM)

def generate_refresh_token(user_id, email="", name=""):
    """Generate a refresh token that carries enough identity to rebuild an access token.

    ``email`` and ``name`` are stored so the ``/api/refresh`` endpoint can issue
    a new access token without a round-trip to the identity provider.
    """
    payload = {
        "user_id": user_id,
        "email": email,
        "name": name,
        "type": "refresh",
        "iat": datetime.utcnow(),
        "exp": datetime.utcnow() + timedelta(days=REFRESH_TOKEN_EXPIRY),
    }
    return jwt.encode(payload, JWT_SECRET_KEY, algorithm=JWT_ALGORITHM)

def verify_token(token):
    try:
        return jwt.decode(token, JWT_SECRET_KEY, algorithms=[JWT_ALGORITHM])
    except jwt.ExpiredSignatureError:
        return {"error": "Token expired"}
    except jwt.InvalidTokenError:
        return {"error": "Invalid token"}
