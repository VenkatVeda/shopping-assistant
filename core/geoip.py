"""
Country detection for Databricks Apps.

Reads the X-Databricks-Geo-Country header injected by the Databricks Apps edge.
Falls back to DEFAULT_USER_COUNTRY env var, then empty string.

No external libraries required — avoids geoip2/MaxMind file dependency.
"""

import os


def get_country_from_request(request) -> str:
    """
    Return ISO-3166-1 alpha-2 country code (e.g. 'AU', 'US') from the incoming request.

    Resolution order:
      1. X-Databricks-Geo-Country header (set by Databricks Apps edge)
      2. DEFAULT_USER_COUNTRY env var
      3. Empty string (caller should handle)
    """
    country = (request.headers.get("X-Databricks-Geo-Country") or "").strip().upper()
    if country:
        return country
    return os.getenv("DEFAULT_USER_COUNTRY", "")