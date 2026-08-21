import logging
from flask import Request

logger = logging.getLogger(__name__)


def get_country_from_request(request: Request) -> str:
    """
    Country is resolved by Databricks Apps' own edge/ingress layer and passed
    on every request — no IP parsing or proxy-hop counting needed.
    """
    header_country = request.headers.get("X-Databricks-Geo-Country")
    if header_country:
        return header_country.upper()
    logger.warning("[GEOIP] X-Databricks-Geo-Country header missing on request")
    return ""
