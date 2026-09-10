from urllib.parse import urlparse

from app.core.config import settings


def validate_allowed_endpoint(endpoint_url: str) -> str:
    endpoint = endpoint_url.strip()
    parsed = urlparse(endpoint)
    if parsed.scheme != "opc.tcp" or not parsed.hostname or parsed.port is None:
        raise ValueError("OPC UA endpoint must use opc.tcp://host:port")
    if parsed.username or parsed.password:
        raise ValueError("Credentials must not be embedded in the endpoint URL")
    if parsed.hostname.lower() not in settings.opcua_allowed_hosts:
        allowed = ", ".join(sorted(settings.opcua_allowed_hosts))
        raise ValueError(
            f"OPC UA endpoint host is not allowed; allowed hosts: {allowed}"
        )
    return endpoint
