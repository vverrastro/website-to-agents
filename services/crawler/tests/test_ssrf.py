import httpcore
import pytest
from wta_crawler.ssrf import (
    SafeNetworkBackend,
    SSRFError,
    default_resolver,
    is_forbidden_ip,
    validate_url,
)


@pytest.mark.parametrize(
    "ip",
    [
        "127.0.0.1", "127.1.2.3", "10.0.0.5", "172.16.0.1", "172.31.255.255", "192.168.1.1",
        "169.254.169.254", "169.254.0.1", "100.64.0.1", "100.100.100.200", "0.0.0.0",
        "224.0.0.1", "240.0.0.1", "255.255.255.255", "192.0.2.1", "198.51.100.7",
        "::1", "::", "fe80::1", "fc00::1", "fd00:ec2::254", "ff02::1",
        "::ffff:127.0.0.1", "::ffff:169.254.169.254", "::ffff:10.1.1.1",
        "2002:7f00:0001::1",  # 6to4 embedding 127.0.0.1
        "64:ff9b::7f00:1",  # NAT64 embedding 127.0.0.1
        "2001:0:4136:e378:8000:63bf:3fff:fdd2",  # Teredo
    ],
)  # fmt: skip
def test_forbidden_ips(ip: str) -> None:
    assert is_forbidden_ip(ip)


@pytest.mark.parametrize("ip", ["8.8.8.8", "1.1.1.1", "93.184.216.34", "2606:4700:4700::1111"])
def test_public_ips_allowed(ip: str) -> None:
    assert not is_forbidden_ip(ip)


def resolver_for(mapping: dict[str, list[str]]):  # type: ignore[no-untyped-def]
    async def resolve(host: str, port: int) -> list[str]:
        return mapping.get(host, [])

    return resolve


@pytest.mark.parametrize(
    "url,reason",
    [
        ("file:///etc/passwd", "forbidden_scheme"),
        ("ftp://example.com/", "forbidden_scheme"),
        ("gopher://example.com/", "forbidden_scheme"),
        ("http://user:pw@example.com/", "credentials_in_url"),
        ("http:///nohost", "missing_host"),
        ("http://localhost/", "forbidden_hostname"),
        ("http://app.localhost/", "forbidden_hostname"),
        ("http://db.internal/", "forbidden_hostname"),
        ("http://127.0.0.1/", "forbidden_address"),
        ("http://[::1]:8000/", "forbidden_address"),
        ("http://169.254.169.254/latest/meta-data/", "forbidden_address"),
        ("http://10.0.0.1:6379/", "forbidden_address"),
        ("http://evil.test/", "forbidden_address"),
    ],
)
async def test_validate_url_rejects(url: str, reason: str) -> None:
    resolver = resolver_for({"evil.test": ["93.184.216.34", "127.0.0.1"]})
    with pytest.raises(SSRFError) as exc:
        await validate_url(url, resolver)
    assert exc.value.reason == reason


async def test_validate_url_rejects_when_any_answer_is_private() -> None:
    resolver = resolver_for({"mixed.test": ["8.8.8.8", "10.0.0.1"]})
    with pytest.raises(SSRFError):
        await validate_url("https://mixed.test/", resolver)


async def test_validate_url_accepts_public() -> None:
    resolver = resolver_for({"ok.test": ["93.184.216.34"]})
    assert await validate_url("https://ok.test/x", resolver) == ["93.184.216.34"]


async def test_dns_without_answers_rejected() -> None:
    with pytest.raises(SSRFError, match="dns_no_addresses"):
        await validate_url("https://nxdomain.test/", resolver_for({}))


@pytest.mark.parametrize("host", ["127.1", "2130706433", "0x7f000001", "017700000001", "0"])
async def test_obfuscated_loopback_forms_never_pass(host: str) -> None:
    # Whatever the OS resolver does with these legacy encodings, they must not validate.
    with pytest.raises(SSRFError):
        await validate_url(f"http://{host}/", default_resolver)


class RecordingBackend(httpcore.AsyncNetworkBackend):
    def __init__(self) -> None:
        self.connected: list[tuple[str, int]] = []

    async def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):  # type: ignore[no-untyped-def]  # noqa: ASYNC109
        self.connected.append((host, port))
        raise httpcore.ConnectError("test backend does not connect")

    async def connect_unix_socket(self, path, timeout=None, socket_options=None):  # type: ignore[no-untyped-def]  # noqa: ASYNC109
        raise AssertionError("unreachable")

    async def sleep(self, seconds: float) -> None:
        return None


async def test_connect_time_rebinding_is_blocked() -> None:
    """First answer is public (passes pre-flight), second is internal (used at connect)."""
    answers = iter([["93.184.216.34"], ["169.254.169.254"]])

    async def rebinding(host: str, port: int) -> list[str]:
        return next(answers)

    assert await validate_url("http://rebind.test/", rebinding) == ["93.184.216.34"]
    inner = RecordingBackend()
    backend = SafeNetworkBackend(rebinding, inner)
    with pytest.raises(SSRFError, match="forbidden_address"):
        await backend.connect_tcp("rebind.test", 80)
    assert inner.connected == []


async def test_backend_connects_to_validated_ip_literal() -> None:
    inner = RecordingBackend()
    backend = SafeNetworkBackend(resolver_for({"ok.test": ["93.184.216.34"]}), inner)
    with pytest.raises(httpcore.ConnectError):
        await backend.connect_tcp("ok.test", 443)
    assert inner.connected == [("93.184.216.34", 443)]


async def test_unix_sockets_forbidden() -> None:
    with pytest.raises(SSRFError):
        await SafeNetworkBackend().connect_unix_socket("/var/run/docker.sock")
