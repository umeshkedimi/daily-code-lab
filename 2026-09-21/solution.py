"""SSRF-safe URL fetching.

Server-side request forgery: an attacker supplies a URL and your server
fetches it *from inside your network*, reaching things the attacker can't
(cloud metadata at 169.254.169.254, internal admin panels, localhost
services). Any feature that fetches a caller-supplied URL -- webhooks, link
previews, image proxies, PDF/HTML renderers, LLM-agent "fetch this page"
tools -- has this problem.

The defense implemented here is layered, and each layer exists because a
specific bypass defeats the layers below it:

  1. Parse the URL ourselves, strictly (scheme/port allowlists, no userinfo,
     no backslashes or control characters) -- one parser, no differentials.
  2. Resolve the hostname ourselves and judge the *resolved IP addresses*,
     never the hostname text: decimal/hex/octal/short IP forms, attacker-
     controlled DNS names, and IPv6 wrappers all collapse to an IP.
  3. Connect to the exact IP we validated (pinning) while keeping the
     original hostname for the Host header and TLS verification -- so a
     second DNS answer (DNS rebinding) can never redirect the connection.
  4. Follow redirects manually and re-run 1-3 on every hop.
  5. Cap response size, redirect count, and total time.
"""

from __future__ import annotations

import http.client
import ipaddress
import re
import socket
import ssl
import threading
import time
import urllib.request
from dataclasses import dataclass, field
from typing import Callable, Dict, FrozenSet, List, Optional, Sequence, Tuple
from urllib.parse import quote, urljoin, urlsplit

IPNetwork = ipaddress._BaseNetwork  # type: ignore[attr-defined]


class BlockedURLError(Exception):
    """The URL (or a redirect hop, or an address it resolved to) violated policy."""


class FetchError(Exception):
    """The fetch failed for a non-policy reason: resolution/connection failure,
    too many redirects, or a size/time limit."""


def _nets(*cidrs: str) -> Tuple[IPNetwork, ...]:
    return tuple(ipaddress.ip_network(c) for c in cidrs)


# `ipaddress.is_global` is NOT sufficient on its own (verified on Python 3.9):
# it says True for the cloud-metadata address 168.63.129.16, for multicast,
# for the IPv4-compatible form ::127.0.0.1, and for the NAT64 / 6to4
# encodings of 127.0.0.1. Those are denied explicitly, and deny always wins.
DEFAULT_DENIED_NETWORKS = _nets(
    "169.254.169.254/32",  # AWS/GCP/Azure/DO instance metadata (also link-local)
    "fd00:ec2::254/128",  # AWS IMDS over IPv6
    "168.63.129.16/32",  # Azure WireServer / platform endpoint -- a *public-looking* address
    "100.100.100.200/32",  # Alibaba Cloud metadata
    "192.0.0.0/24",  # IETF protocol assignments
    "224.0.0.0/4",  # multicast
    "::/96",  # deprecated IPv4-compatible IPv6 (::127.0.0.1)
    "64:ff9b::/96",  # NAT64: embeds an IPv4 address
    "64:ff9b:1::/48",  # local-use NAT64
    "2002::/16",  # 6to4: embeds an IPv4 address
    "2001::/32",  # Teredo
)


@dataclass(frozen=True)
class SSRFPolicy:
    allowed_schemes: FrozenSet[str] = frozenset({"http", "https"})
    allowed_ports: FrozenSet[int] = frozenset({80, 443})
    # Explicit, narrow opt-in for legitimate internal targets. Note: denied
    # networks are checked first, so opening 10.0.0.0/8 still can't reach metadata.
    extra_allowed_networks: Tuple[IPNetwork, ...] = ()
    denied_networks: Tuple[IPNetwork, ...] = DEFAULT_DENIED_NETWORKS
    max_redirects: int = 3
    max_body_bytes: int = 1_000_000
    timeout: float = 5.0  # per socket operation
    total_timeout: float = 10.0  # whole fetch, all hops


DEFAULT_POLICY = SSRFPolicy()


# --- Layer 2: judge resolved addresses ---------------------------------------


def blocked_reason(ip_text: str, policy: SSRFPolicy = DEFAULT_POLICY) -> Optional[str]:
    """None if the address may be contacted, otherwise a human-readable reason."""
    try:
        ip = ipaddress.ip_address(ip_text.split("%", 1)[0])  # drop any IPv6 zone id
    except ValueError:
        return "unparseable address"

    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        # ::ffff:127.0.0.1 is just 127.0.0.1. Unwrap explicitly: how the
        # stdlib classifies mapped addresses has changed between versions.
        inner = blocked_reason(str(ip.ipv4_mapped), policy)
        return f"IPv4-mapped {ip.ipv4_mapped} ({inner})" if inner else None

    for net in policy.denied_networks:
        if ip in net:
            return f"in denied range {net}"
    for net in policy.extra_allowed_networks:
        if ip in net:
            return None
    if not ip.is_global:
        return "non-global address (private, loopback, link-local, reserved, or unspecified)"
    return None


def default_resolver(host: str, port: int) -> List[str]:
    infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    out: List[str] = []
    for info in infos:
        ip = info[4][0].split("%", 1)[0]
        if ip not in out:
            out.append(ip)
    return out


# --- Layer 1: strict single-parser URL handling ---------------------------------

_HOST_RE = re.compile(r"[a-z0-9_.-]+")


@dataclass(frozen=True)
class Target:
    scheme: str
    host: str
    port: int
    request_target: str  # path + query, percent-encoded


def parse_url(url: str, policy: SSRFPolicy = DEFAULT_POLICY) -> Target:
    if not isinstance(url, str) or not url or len(url) > 2048:
        raise BlockedURLError("URL missing or too long")
    # Whitespace/control characters enable header injection and parser
    # differentials; backslashes are treated as '/' by browsers but not by
    # RFC 3986 parsers -- reject rather than guess which one the target uses.
    if any(c <= " " or c == "\x7f" for c in url) or "\\" in url:
        raise BlockedURLError("URL contains whitespace, control characters, or a backslash")

    parts = urlsplit(url)
    scheme = parts.scheme.lower()
    if scheme not in policy.allowed_schemes:
        raise BlockedURLError(f"scheme {scheme!r} not allowed")
    if "@" in parts.netloc:
        raise BlockedURLError("userinfo (user:pass@) in URL is not allowed")  # 'good.com@evil.com' confusion
    if not parts.hostname:
        raise BlockedURLError("URL has no host")
    try:
        port = parts.port or {"http": 80, "https": 443}[scheme]
    except ValueError:
        raise BlockedURLError("invalid port")
    if port not in policy.allowed_ports:
        raise BlockedURLError(f"port {port} not allowed")

    host = parts.hostname  # urlsplit lowercases and strips [] from IPv6 literals
    if ":" in host:
        try:
            ipaddress.IPv6Address(host.split("%", 1)[0])
        except ValueError:
            raise BlockedURLError("invalid IPv6 literal")
    else:
        try:
            host = host.encode("idna").decode("ascii")  # normalizes unicode dots etc.
        except UnicodeError:
            raise BlockedURLError("invalid hostname")
        if not _HOST_RE.fullmatch(host):
            raise BlockedURLError("hostname contains disallowed characters")

    path = quote(parts.path or "/", safe="/%:@!$&'()*+,;=-._~")
    if parts.query:
        path += "?" + quote(parts.query, safe="/%:@!$&'()*+,;=-._~?")
    return Target(scheme, host, port, path)


def resolve_and_validate(
    target: Target, policy: SSRFPolicy, resolver: Callable[[str, int], List[str]]
) -> List[str]:
    try:
        ips = resolver(target.host, target.port)
    except OSError as exc:
        raise FetchError(f"could not resolve {target.host!r}: {exc}") from exc
    if not ips:
        raise FetchError(f"{target.host!r} resolved to no addresses")
    # EVERY answer must pass, not just the one we'd connect to: a name that
    # mixes public and internal records is either misconfigured or hostile.
    for ip in ips:
        reason = blocked_reason(ip, policy)
        if reason:
            raise BlockedURLError(f"{target.host!r} resolves to {ip}: {reason}")
    return ips


# --- Layer 3: connect to the validated IP, keep the hostname for Host/TLS --------


def _open_connection(
    target: Target,
    ips: Sequence[str],
    policy: SSRFPolicy,
    connect: Callable,
    ssl_context: Optional[ssl.SSLContext],
    deadline: float,
) -> Tuple[http.client.HTTPConnection, str]:
    if target.scheme == "https":
        conn: http.client.HTTPConnection = http.client.HTTPSConnection(
            target.host, target.port, timeout=policy.timeout, context=ssl_context or ssl.create_default_context()
        )
    else:
        conn = http.client.HTTPConnection(target.host, target.port, timeout=policy.timeout)

    last_error: Optional[Exception] = None
    for ip in ips:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise FetchError("total time limit exceeded while connecting")
        conn.timeout = min(policy.timeout, remaining)  # one slow address can't eat the whole budget
        # http.client would call create_connection((hostname, port)) and so
        # re-resolve the name. Substitute a connector that ignores the
        # hostname and dials the IP we already validated. The hostname is
        # still what http.client sends as Host and (for https) as the TLS
        # SNI/verification name. (`_create_connection` is a private
        # attribute -- see notes.md; the test-suite is the canary.)
        def pinned(address, tmo, source_address=None, _ip=ip):
            return connect((_ip, address[1]), tmo, source_address)

        conn._create_connection = pinned  # type: ignore[attr-defined]
        try:
            conn.connect()
            return conn, ip
        except (OSError, ssl.SSLError) as exc:
            last_error = exc
            conn.close()
    raise FetchError(f"could not connect to {target.host!r} at any validated address: {last_error}")


# --- Layers 4 + 5: manual redirects, capped response ----------------------------

_REDIRECT_CODES = {301, 302, 303, 307, 308}


@dataclass
class FetchResult:
    url: str
    status: int
    headers: Dict[str, str]
    body: bytes
    chain: List[str] = field(default_factory=list)  # every URL visited, in order
    connected_ip: str = ""


def _read_capped(
    resp: http.client.HTTPResponse, policy: SSRFPolicy, deadline: float, timed_out: threading.Event
) -> bytes:
    declared = resp.getheader("Content-Length")
    if declared and declared.isdigit() and int(declared) > policy.max_body_bytes:
        raise FetchError(f"declared Content-Length {declared} exceeds limit {policy.max_body_bytes}")
    chunks, total = [], 0
    while True:
        if timed_out.is_set() or time.monotonic() > deadline:
            raise FetchError("total time limit exceeded while reading body")
        # read1 returns as soon as *any* data is available. read(n) would block
        # until n bytes or EOF, so a server dripping one byte every few
        # hundred ms would never trip a per-recv timeout nor reach this loop's
        # deadline check -- a slowloris against the fetcher itself.
        chunk = resp.read1(min(8192, policy.max_body_bytes + 1 - total))
        if not chunk:
            if timed_out.is_set():  # the watchdog's shutdown looks exactly like a clean EOF
                raise FetchError("total time limit exceeded while reading body")
            if resp.length:  # http.client does not raise when a Content-Length body is cut short
                raise FetchError(f"response truncated: {resp.length} declared bytes never arrived")
            return b"".join(chunks)
        total += len(chunk)
        if total > policy.max_body_bytes:  # a server can lie about, or omit, Content-Length
            raise FetchError(f"body exceeds limit {policy.max_body_bytes}")
        chunks.append(chunk)


def safe_fetch(
    url: str,
    policy: SSRFPolicy = DEFAULT_POLICY,
    *,
    resolver: Callable[[str, int], List[str]] = default_resolver,
    connect: Callable = socket.create_connection,
    ssl_context: Optional[ssl.SSLContext] = None,
) -> FetchResult:
    """GET `url`, refusing to be turned against internal resources.

    `resolver` and `connect` are seams for testing with a fake network;
    production code uses the defaults.
    """
    deadline = time.monotonic() + policy.total_timeout
    chain: List[str] = []
    current = url

    for _ in range(policy.max_redirects + 1):
        chain.append(current)
        target = parse_url(current, policy)
        ips = resolve_and_validate(target, policy, resolver)

        conn, connected_ip = _open_connection(target, ips, policy, connect, ssl_context, deadline)

        # Watchdog: per-recv timeouts don't bound *total* time -- a peer that
        # sends one header byte every few seconds resets them forever. At the
        # deadline, shut the socket down so any blocked read (status line,
        # headers, body, TLS) returns immediately.
        timed_out = threading.Event()

        def _abort(_conn=conn):
            timed_out.set()
            try:
                _conn.sock.shutdown(socket.SHUT_RDWR)
            except (OSError, AttributeError):
                pass

        watchdog = threading.Timer(max(0.0, deadline - time.monotonic()), _abort)
        watchdog.daemon = True
        watchdog.start()
        try:
            conn.request(
                "GET",
                target.request_target,
                headers={"User-Agent": "safe-fetch/1.0", "Accept": "*/*", "Connection": "close"},
            )
            resp = conn.getresponse()
            location = resp.getheader("Location")
            if resp.status in _REDIRECT_CODES and location:
                current = urljoin(current, location)  # re-validated from scratch on the next loop
                continue
            body = _read_capped(resp, policy, deadline, timed_out)
            return FetchResult(
                url=current,
                status=resp.status,
                headers={k.lower(): v for k, v in resp.getheaders()},
                body=body,
                chain=chain,
                connected_ip=connected_ip,
            )
        except (OSError, http.client.HTTPException) as exc:
            if timed_out.is_set():
                raise FetchError("total time limit exceeded") from exc
            raise FetchError(f"request to {target.host!r} failed: {exc}") from exc
        finally:
            watchdog.cancel()
            conn.close()

    raise FetchError(f"more than {policy.max_redirects} redirects")


# --- Deliberately flawed baselines (what people actually ship) ---------------------

_NAIVE_BLOCKLIST = ("localhost", "127.0.0.1", "169.254.169.254", "0.0.0.0", "::1")


def substring_blocklist_fetch(url: str, timeout: float = 5.0) -> bytes:
    """Flaw: judges the URL *text*. Beaten by 2130706433, 0x7f.1, 127.1, an
    attacker DNS name that points at 127.0.0.1, redirects, and file://."""
    if any(bad in url.lower() for bad in _NAIVE_BLOCKLIST):
        raise BlockedURLError("blocked by substring blocklist")
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        return resp.read()


def check_then_fetch(
    url: str,
    policy: SSRFPolicy = DEFAULT_POLICY,
    resolver: Callable[[str, int], List[str]] = default_resolver,
    timeout: float = 5.0,
) -> bytes:
    """Flaw: a *good* check (same parser and IP validation as safe_fetch) on
    the first URL only, followed by a fetch that re-resolves the name and
    follows redirects on its own. Beaten by DNS rebinding (check sees a
    public IP, connect sees 127.0.0.1) and by redirects to internal targets."""
    target = parse_url(url, policy)
    resolve_and_validate(target, policy, resolver)
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        return resp.read()


# --- Demo ------------------------------------------------------------------------------

_SECRET = b"AWS_SECRET_ACCESS_KEY=hunter2 (pretend instance-metadata credentials)"


def _demo_attacks(internal_port: int, public_port: int, secret_file: str):
    """(name, url, fake-dns-answers). Names not in fake DNS use the real OS resolver."""
    p = internal_port
    return [
        ("direct 127.0.0.1", f"http://127.0.0.1:{p}/secret", {}),
        ("decimal IP 2130706433", f"http://2130706433:{p}/secret", {}),
        ("hex/short IP 0x7f.1", f"http://0x7f.1:{p}/secret", {}),
        ("short IP 127.1", f"http://127.1:{p}/secret", {}),
        ("unspecified addr 0", f"http://0:{p}/secret", {}),
        ("IPv4-mapped [::ffff:127.0.0.1]", f"http://[::ffff:127.0.0.1]:{p}/secret", {}),
        ("IPv4-mapped hex [::ffff:7f00:1]", f"http://[::ffff:7f00:1]:{p}/secret", {}),
        ("attacker DNS -> 127.0.0.1", f"http://attacker.test:{p}/secret", {"attacker.test": [["127.0.0.1"]]}),
        (
            "DNS rebinding (public, then loopback)",
            f"http://rebind.test:{p}/secret",
            {"rebind.test": [["203.0.113.10"], ["127.0.0.1"]]},
        ),
        (
            "open redirect -> internal",
            f"http://public.test:{public_port}/redir",
            {"public.test": [["203.0.113.10"]]},
        ),
        ("file:// scheme", f"file://{secret_file}", {}),
        ("userinfo confusion (parser-dependent)", f"http://public.test@2130706433:{p}/secret", {}),
    ]


def demo() -> None:
    import os
    import tempfile

    from labnet import FakeNetwork, LabServer

    with tempfile.NamedTemporaryFile("wb", suffix=".txt", delete=False) as f:
        f.write(_SECRET)
        secret_file = f.name

    def internal_route(path, headers):
        return (200, {"Content-Type": "text/plain"}, _SECRET) if path == "/secret" else (404, {}, b"no")

    with LabServer(internal_route) as internal:

        def public_route(path, headers):
            if path == "/redir":
                return (302, {"Location": f"http://127.0.0.1:{internal.port}/secret"}, b"")
            return (200, {"Content-Type": "text/plain"}, b"harmless public page")

        with LabServer(public_route) as public:
            routes = {"203.0.113.10": ("127.0.0.1", public.port)}  # the "public internet"
            allow_lab = SSRFPolicy(
                allowed_ports=frozenset({80, 443, internal.port, public.port}),  # so ONLY IP validation is under test
                extra_allowed_networks=_nets("203.0.113.0/24"),  # TEST-NET-3 stands in for "a public IP"
            )
            attacks = _demo_attacks(internal.port, public.port, secret_file)

            print("Attacker wants the 'internal service' secret. Each cell: what the fetcher did.\n")
            print(f"{'attack':40} {'substring blocklist':>20} {'check-then-fetch':>18} {'safe_fetch':>14}")

            summary = {"substring blocklist": 0, "check-then-fetch": 0, "safe_fetch": 0}
            safe_internal_contacts = 0
            for name, url, dns in attacks:
                cells = []
                for label, runner in (
                    ("substring blocklist", lambda net, u=url: substring_blocklist_fetch(u)),
                    ("check-then-fetch", lambda net, u=url: check_then_fetch(u, allow_lab, net.resolve)),
                    (
                        "safe_fetch",
                        lambda net, u=url: safe_fetch(u, allow_lab, resolver=net.resolve, connect=net.create_connection).body,
                    ),
                ):
                    net = FakeNetwork(dns, routes)
                    before = len(internal.hits)
                    try:
                        with net.patched():
                            body = runner(net)
                        outcome = "LEAKED" if _SECRET in body else "no leak"
                    except BlockedURLError:
                        outcome = "blocked"  # the policy itself refused
                    except Exception:
                        outcome = "error (no leak)"  # failed for some other reason -- NOT a defense win
                    if outcome == "LEAKED":
                        summary[label] += 1
                    if label == "safe_fetch":
                        safe_internal_contacts += len(internal.hits) - before
                    cells.append(outcome)
                print(f"{name:40} {cells[0]:>20} {cells[1]:>18} {cells[2]:>14}")

            print(f"\nsecrets leaked out of {len(attacks)} attacks: {summary}")
            print(f"times safe_fetch even opened a connection to the internal service: {safe_internal_contacts}")

    os.unlink(secret_file)


if __name__ == "__main__":
    demo()
