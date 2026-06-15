import requests
import os
from urllib.parse import urlencode
from flask import request as flask_request
from .config import GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, GOOGLE_AUTH_URL, GOOGLE_TOKEN_URL, GOOGLE_USERINFO_URL

def get_base_url():
    """Get base URL - dynamically detects from request context or environment"""
    # Try to get from current request context (most reliable for Databricks Apps)
    try:
        if flask_request:
            # Databricks Apps provides X-Forwarded-Host header
            forwarded_host = flask_request.headers.get('X-Forwarded-Host')
            forwarded_proto = flask_request.headers.get('X-Forwarded-Proto', 'https')
            if forwarded_host:
                return f"{forwarded_proto}://{forwarded_host}"
    except RuntimeError:
        # Outside request context
        pass
    
    # Fallback to environment variable
    return os.environ.get('APP_BASE_URL', 'https://ecom-shop-assistant-prod-4206962778623078.18.azure.databricksapps.com')

def get_google_auth_url(state):
    base_url = get_base_url()
    redirect_uri = f"{base_url}/oauth/callback"

    params = {
        "client_id": GOOGLE_CLIENT_ID,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": "openid email profile",
        "state": state,
        "access_type": "offline",
        "prompt": "consent",
    }
    return f"{GOOGLE_AUTH_URL}?{urlencode(params)}"

def exchange_code_for_token(code):
    base_url = get_base_url()
    redirect_uri = f"{base_url}/oauth/callback"

    data = {
        "code": code,
        "client_id": GOOGLE_CLIENT_ID,
        "client_secret": GOOGLE_CLIENT_SECRET,
        "redirect_uri": redirect_uri,
        "grant_type": "authorization_code",
    }
    response = requests.post(GOOGLE_TOKEN_URL, data=data)
    response.raise_for_status()
    return response.json()

def get_google_user_info(access_token):
    headers = {"Authorization": f"Bearer {access_token}"}
    response = requests.get(GOOGLE_USERINFO_URL, headers=headers)
    response.raise_for_status()
    return response.json()
