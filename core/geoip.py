"""
Country detection for Databricks Apps.

Two distinct uses — do not merge them:
  - get_country_from_request()    : per-request "where is this request coming
                                     from right now" signal. Header-only, no
                                     default fallback — an undetectable
                                     request country must stay empty, not
                                     silently become an environment default.
  - resolve_registration_country(): first-login-only "what country is this
                                     user registering from" signal, with a
                                     3-tier fallback chain (including
                                     DEFAULT_USER_COUNTRY) since it seeds the
                                     frozen governing regulation and needs a
                                     best-effort answer.

No external libraries required — avoids geoip2/MaxMind file dependency.
"""

import os


def get_country_from_request(request) -> str:
    """
    Return ISO-3166-1 alpha-2 country code (e.g. 'AU', 'US') from the incoming
    request's X-Databricks-Geo-Country header (set by the Databricks Apps edge).

    No fallback: returns '' if the header is absent. This value represents
    where THIS request actually came from — never substitute an environment
    default for it, or it stops meaning that.
    """
    return (request.headers.get("X-Databricks-Geo-Country") or "").strip().upper()


def parse_country_from_locale(locale: str) -> str:
    """Extract the region subtag from a BCP-47 locale (e.g. 'en-GB' -> 'GB').
    Returns '' if the locale has no region (e.g. bare 'en') — don't guess."""
    if not locale:
        return ""
    parts = locale.replace("_", "-").split("-")
    if len(parts) >= 2 and len(parts[-1]) == 2:
        return parts[-1].upper()
    return ""


def resolve_registration_country(request, locale: str = "") -> tuple:
    """
    Best-available country signal at first-registration time only.
    Used once, by oauth_callback, to seed customer_pii.user_country
    (and, from it, the frozen governing regulation). NOT used for
    per-request country — see get_country_from_request() for that.

    Resolution order (returns (country, source)):
      1. X-Databricks-Geo-Country header  -> source "geo_header"
      2. Google OAuth locale region       -> source "google_locale"
      3. DEFAULT_USER_COUNTRY env var     -> source "default_env"
      4. ("", "none")
    """
    header_country = (request.headers.get("X-Databricks-Geo-Country") or "").strip().upper()
    if header_country:
        return header_country, "geo_header"

    locale_country = parse_country_from_locale(locale)
    if locale_country:
        return locale_country, "google_locale"

    default_country = os.getenv("DEFAULT_USER_COUNTRY", "")
    if default_country:
        return default_country, "default_env"

    return "", "none"