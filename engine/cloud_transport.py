"""Bounded authenticated HTTPS transport for provider REST contracts.

Access tokens are injected only by a trusted server-side provider. Responses,
exceptions and redirects never expose tokens or resumable session URLs.
"""
from __future__ import annotations

from dataclasses import dataclass
import http.client
import ipaddress
import socket
from typing import Callable, Mapping, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


class CloudTransportError(RuntimeError):
    """Sanitized transport failure; acceptance by the provider can be uncertain."""


class CloudResponseLimit(CloudTransportError):
    pass


@dataclass(frozen=True)
class CloudResponse:
    status: int
    headers: Mapping[str, str]
    body: bytes


class CloudTransport(Protocol):
    def request(self, method: str, url: str, headers: Mapping[str, str],
                body: bytes | None, timeout: float, *,
                max_response_bytes: int = 1024 * 1024) -> CloudResponse: ...


_GOOGLE_HOSTS = frozenset({"storage.googleapis.com", "cloudtasks.googleapis.com",
                           "run.googleapis.com", "oauth2.googleapis.com"})


def validate_cloud_url(url: str, *, allowed_hosts, allow_loopback_http=False):
    """No credentials, fragments, redirects or arbitrary token destinations."""
    try:
        parsed = urlsplit(url)
        port = parsed.port
        if not isinstance(url, str) or any(c in url for c in "\r\n\t"):
            raise ValueError
        if parsed.username or parsed.password or parsed.fragment:
            raise ValueError
        if parsed.scheme == "https" and parsed.hostname in allowed_hosts and port in {None, 443}:
            return parsed
        if allow_loopback_http and parsed.scheme == "http" and parsed.hostname:
            # Numeric loopback only: a DNS alias must never redirect bearer tokens.
            if ipaddress.ip_address(parsed.hostname).is_loopback and port:
                return parsed
    except (ValueError, TypeError, AttributeError):
        pass
    raise CloudTransportError("Cloud URL is outside the configured trusted endpoint")


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class UrllibCloudTransport:
    """Real stdlib HTTPS adapter. Local HTTP is an explicit test-only capability.

    No environment proxy is used: it could forward bearer credentials outside the
    allowlist. Token acquisition/refresh is a caller-owned server responsibility.
    """
    def __init__(self, token_provider: Callable[[], str], *,
                 allowed_hosts=("storage.googleapis.com",), allow_loopback_http=False,
                 max_timeout=60.0, max_response_bytes=512 * 1024 * 1024):
        from urllib.request import ProxyHandler
        self.allowed_hosts = frozenset(allowed_hosts)
        if not self.allowed_hosts or not self.allowed_hosts <= _GOOGLE_HOSTS:
            raise ValueError("Only supported Google REST hosts may receive credentials")
        if not callable(token_provider):
            raise ValueError("A server-side token provider is required")
        if type(allow_loopback_http) is not bool:
            raise ValueError("allow_loopback_http must be explicit")
        if not 0 < max_timeout <= 300 or type(max_response_bytes) is not int or max_response_bytes <= 0:
            raise ValueError("Invalid transport bounds")
        self.token_provider = token_provider
        self.allow_loopback_http = allow_loopback_http
        self.max_timeout = max_timeout
        self.max_response_bytes = max_response_bytes
        self._opener = build_opener(ProxyHandler({}), _NoRedirect())

    def request(self, method, url, headers=None, body=b"", timeout=15.0, *,
                max_response_bytes=1024 * 1024):
        validate_cloud_url(url, allowed_hosts=self.allowed_hosts,
                           allow_loopback_http=self.allow_loopback_http)
        if method not in {"GET", "POST", "PUT", "DELETE", "PATCH"}:
            raise CloudTransportError("Unsupported cloud request method")
        if not 0 < timeout <= self.max_timeout:
            raise ValueError("Request timeout exceeds transport bound")
        if type(max_response_bytes) is not int or not 0 < max_response_bytes <= self.max_response_bytes:
            raise ValueError("Invalid response byte bound")
        outgoing = dict(headers or {})
        if any(k.lower() in {"authorization", "proxy-authorization", "host"} for k in outgoing):
            raise CloudTransportError("Caller cannot override authentication or endpoint")
        if any("\r" in str(v) or "\n" in str(v) for v in outgoing.values()):
            raise CloudTransportError("Unsafe request header")
        try:
            token = self.token_provider()
        except Exception:
            raise CloudTransportError("Cloud access token is unavailable") from None
        if not isinstance(token, str) or not token or any(c.isspace() for c in token):
            raise CloudTransportError("Cloud access token is invalid")
        outgoing["Authorization"] = "Bearer " + token
        request = Request(url, data=body if method not in {"GET", "DELETE"} else None,
                          headers=outgoing, method=method)
        try:
            try:
                response = self._opener.open(request, timeout=timeout)
            except HTTPError as error:
                response = error
            with response:
                declared = response.headers.get("Content-Length")
                if declared is not None:
                    try:
                        if int(declared) < 0 or int(declared) > max_response_bytes:
                            raise CloudResponseLimit("Cloud response exceeds byte bound")
                    except ValueError:
                        raise CloudTransportError("Invalid cloud response length") from None
                data = response.read(max_response_bytes + 1)
                if len(data) > max_response_bytes:
                    raise CloudResponseLimit("Cloud response exceeds byte bound")
                if declared is not None and len(data) != int(declared):
                    raise CloudTransportError("Cloud response interrupted; acceptance requires reconciliation")
                return CloudResponse(response.status,
                                     {k.lower(): v for k, v in response.headers.items()}, data)
        except (TimeoutError, socket.timeout):
            raise TimeoutError("Cloud request timed out; acceptance requires reconciliation") from None
        except (URLError, OSError, http.client.HTTPException):
            raise CloudTransportError("Cloud request interrupted; acceptance requires reconciliation") from None
