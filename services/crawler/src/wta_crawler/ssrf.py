"""SSRF protection for every outbound connection the crawler makes.

Defence layers:
1. ``validate_url``: scheme/host/credentials checks plus DNS resolution with every resolved
   address validated (pre-flight, used before each request and redirect hop).
2. ``SafeNetworkBackend``: re-resolves and re-validates at *connect time* and connects to the
   validated IP literal, so a DNS answer that changes between check and use (DNS rebinding)
   can never reach a forbidden address.
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from collections.abc import Awaitable, Callable, Iterable
from urllib.parse import urlsplit

import httpcore

Resolver = Callable[[str, int], Awaitable[list[str]]]
SocketOption = (
    tuple[int, int, int] | tuple[int, int, bytes | bytearray] | tuple[int, int, None, int]
)

_FORBIDDEN_HOST_SUFFIXES = (".localhost", ".local", ".internal", ".localdomain", ".home.arpa")
_NAT64 = ipaddress.ip_network("64:ff9b::/96")


class SSRFError(Exception):
    """A destination was rejected. ``reason`` is a short stable code safe to store/log."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def is_forbidden_ip(value: str | ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """True for anything that is not a normal public unicast address."""
    ip = ipaddress.ip_address(value) if isinstance(value, str) else value
    if isinstance(ip, ipaddress.IPv6Address):
        if ip.ipv4_mapped is not None:
            return is_forbidden_ip(ip.ipv4_mapped)
        if ip.sixtofour is not None:
            return is_forbidden_ip(ip.sixtofour)
        if ip.teredo is not None:
            return True
        if ip in _NAT64:
            return is_forbidden_ip(ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF))
    return (
        not ip.is_global
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_unspecified
        or ip.is_private
    )


async def default_resolver(host: str, port: int) -> list[str]:
    """Resolve with the system resolver. IP literals are returned unchanged."""
    try:
        ipaddress.ip_address(host)
        return [host]
    except ValueError:
        pass
    loop = asyncio.get_running_loop()
    try:
        infos = await loop.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise SSRFError("dns_failure") from exc
    seen: list[str] = []
    for info in infos:
        addr = str(info[4][0])
        if addr not in seen:
            seen.append(addr)
    return seen


async def resolve_public(host: str, port: int, resolver: Resolver) -> list[str]:
    """Resolve ``host`` and require that *all* answers are public addresses."""
    lowered = host.lower().rstrip(".")
    if lowered == "localhost" or lowered.endswith(_FORBIDDEN_HOST_SUFFIXES):
        raise SSRFError("forbidden_hostname")
    try:
        # IP literals are validated directly and never depend on the resolver.
        addrs = [str(ipaddress.ip_address(host.strip("[]")))]
    except ValueError:
        addrs = await resolver(host, port)
    if not addrs:
        raise SSRFError("dns_no_addresses")
    for addr in addrs:
        try:
            forbidden = is_forbidden_ip(addr)
        except ValueError as exc:
            raise SSRFError("invalid_address") from exc
        if forbidden:
            raise SSRFError("forbidden_address")
    return addrs


async def validate_url(url: str, resolver: Resolver = default_resolver) -> list[str]:
    """Pre-flight check of a URL. Returns the validated addresses."""
    try:
        parts = urlsplit(url)
        host = parts.hostname
        port = parts.port
    except ValueError as exc:
        raise SSRFError("invalid_url") from exc
    if parts.scheme not in ("http", "https"):
        raise SSRFError("forbidden_scheme")
    if not host:
        raise SSRFError("missing_host")
    if parts.username is not None or parts.password is not None:
        raise SSRFError("credentials_in_url")
    return await resolve_public(host, port or (443 if parts.scheme == "https" else 80), resolver)


class SafeNetworkBackend(httpcore.AsyncNetworkBackend):
    """httpcore backend that validates the destination at the moment of connecting."""

    def __init__(
        self,
        resolver: Resolver = default_resolver,
        inner: httpcore.AsyncNetworkBackend | None = None,
    ) -> None:
        self._resolver = resolver
        self._inner = inner or httpcore.AnyIOBackend()

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,  # noqa: ASYNC109 (httpcore interface)
        local_address: str | None = None,
        socket_options: Iterable[SocketOption] | None = None,
    ) -> httpcore.AsyncNetworkStream:
        addrs = await resolve_public(host, port, self._resolver)
        last_exc: Exception | None = None
        for addr in addrs:
            try:
                # Connect to the validated literal; TLS still verifies against the hostname.
                return await self._inner.connect_tcp(
                    addr,
                    port,
                    timeout=timeout,
                    local_address=local_address,
                    socket_options=socket_options,
                )
            except httpcore.ConnectError as exc:
                last_exc = exc
        raise last_exc or httpcore.ConnectError("no usable address")

    async def connect_unix_socket(
        self,
        path: str,
        timeout: float | None = None,  # noqa: ASYNC109 (httpcore interface)
        socket_options: Iterable[SocketOption] | None = None,
    ) -> httpcore.AsyncNetworkStream:
        raise SSRFError("unix_sockets_forbidden")

    async def sleep(self, seconds: float) -> None:
        await self._inner.sleep(seconds)


__all__ = [
    "Resolver",
    "SSRFError",
    "SafeNetworkBackend",
    "default_resolver",
    "is_forbidden_ip",
    "resolve_public",
    "validate_url",
]
