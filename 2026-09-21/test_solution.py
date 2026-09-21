"""SSRF defenses, tested against real local servers.

Design rule for these tests: for every attack, assert the *observable
security property* -- the internal service received no request at all
(`internal.hits == []`) and no connection was opened to a forbidden IP --
not merely that some exception was raised. An exception can be raised for
the wrong reason (a DNS typo, a closed port) while the defense is absent.
"""

import ipaddress
import socket
import ssl
import subprocess
from types import SimpleNamespace

import pytest

import solution
from labnet import FakeNetwork, LabServer, RawServer
from solution import (
    BlockedURLError,
    FetchError,
    SSRFPolicy,
    _nets,
    blocked_reason,
    check_then_fetch,
    parse_url,
    safe_fetch,
    substring_blocklist_fetch,
)

SECRET = b"AWS_SECRET_ACCESS_KEY=hunter2"
PUBLIC_PAGE = b"harmless public page"
PUBLIC_IP = "203.0.113.10"  # TEST-NET-3: stands in for "a public address", routed to a local server


# --- classification of addresses ----------------------------------------------------


@pytest.mark.parametrize(
    "ip",
    [
        "127.0.0.1", "127.255.255.254", "0.0.0.0", "10.0.0.1", "172.16.0.1", "172.31.255.255", "192.168.0.1",
        "169.254.169.254", "169.254.0.1", "100.64.0.1", "100.100.100.200", "224.0.0.1", "239.255.255.255",
        "240.0.0.1", "255.255.255.255", "192.0.0.8",
        "168.63.129.16",  # Azure platform address: is_global is True, must still be denied
        "::1", "::", "fe80::1", "fe80::1%eth0", "fc00::1", "fd00:ec2::254",
        "::ffff:127.0.0.1", "::ffff:7f00:1", "::ffff:10.0.0.1", "::ffff:169.254.169.254",
        "::127.0.0.1",  # IPv4-compatible: is_global is True
        "64:ff9b::7f00:1",  # NAT64 encoding of 127.0.0.1: is_global is True
        "2002:7f00:1::",  # 6to4 encoding of 127.0.0.1: is_global is True
        "2001::1",
        "not-an-ip",
    ],
)
def test_forbidden_addresses_are_blocked(ip):
    assert blocked_reason(ip) is not None


@pytest.mark.parametrize("ip", ["8.8.8.8", "1.1.1.1", "93.184.216.34", "2606:4700:4700::1111", "::ffff:8.8.8.8"])
def test_ordinary_public_addresses_are_allowed(ip):
    assert blocked_reason(ip) is None


def test_extra_allowed_networks_opens_internal_range_but_deny_list_still_wins():
    policy = SSRFPolicy(extra_allowed_networks=_nets("10.0.0.0/8", "169.254.0.0/16"))
    assert blocked_reason("10.1.2.3", policy) is None  # legitimate opt-in
    assert blocked_reason("192.168.1.1", policy) is not None  # not opted in
    assert blocked_reason("169.254.169.254", policy) is not None  # metadata denied even inside an allowed range


# --- URL parsing ----------------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd", "gopher://example.com/", "ftp://example.com/", "javascript:alert(1)",
        "data:text/plain,hi", "dict://example.com:80/", "//example.com/", "example.com/path", "",
        "http://user@example.com/", "http://user:pw@example.com/", "http://example.com@127.0.0.1/",
        "http://example.com\\@127.0.0.1/", "http://example.com/\r\nHost: evil", "http://exa mple.com/",
        "http://example.com/\x00", "http:///path", "http://example.com:22/", "http://example.com:8080/",
        "http://example.com:99999/", "http://example.com:abc/", "http://a%2eb.example/", "http://exa$mple.com/",
        "http://" + "a" * 3000 + ".com/",
    ],
)
def test_parse_rejects_dangerous_or_ambiguous_urls(url):
    with pytest.raises(BlockedURLError):
        parse_url(url)


def test_parse_rejects_non_string():
    with pytest.raises(BlockedURLError):
        parse_url(None)  # type: ignore[arg-type]


def test_parse_normalizes_valid_urls():
    t = parse_url("HTTP://Example.COM/a/b?q=1#frag")
    assert (t.scheme, t.host, t.port, t.request_target) == ("http", "example.com", 80, "/a/b?q=1")
    assert parse_url("https://example.com").port == 443
    assert parse_url("http://example.com").request_target == "/"
    assert parse_url("http://example.com/café").request_target == "/caf%C3%A9"
    assert parse_url("http://bücher.example/").host == "xn--bcher-kva.example"
    assert parse_url("http://[2606:4700:4700::1111]/").host == "2606:4700:4700::1111"


# --- lab fixture -------------------------------------------------------------------------


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.fixture
def lab():
    def internal_route(path, headers):
        return (200, {"Content-Type": "text/plain"}, SECRET) if path == "/secret" else (404, {}, b"nope")

    with LabServer(internal_route) as internal:

        def public_route(path, headers):
            if path == "/redir":
                return 302, {"Location": f"http://127.0.0.1:{internal.port}/secret"}, b""
            if path == "/redir-decimal":
                return 302, {"Location": f"http://2130706433:{internal.port}/secret"}, b""
            if path == "/redir-metadata":
                return 302, {"Location": "http://metadata.test/latest/meta-data/"}, b""
            if path == "/redir-file":
                return 302, {"Location": "file:///etc/passwd"}, b""
            if path == "/redir-port22":
                return 302, {"Location": "http://public.test:22/"}, b""
            if path == "/redir-rel":
                return 302, {"Location": "/landing"}, b""
            if path.startswith("/chain/"):
                n = int(path.rsplit("/", 1)[1])
                return (302, {"Location": f"/chain/{n - 1}"}, b"") if n > 0 else (200, {}, b"end of chain")
            if path == "/loop":
                return 302, {"Location": "/loop"}, b""
            if path == "/no-location":
                return 302, {}, b"redirect without a Location header"
            if path == "/big":
                return 200, {}, b"x" * 5000
            if path == "/big-undeclared":  # no Content-Length: body delimited by connection close

                def stream(wfile):
                    for _ in range(50):
                        wfile.write(b"y" * 100)

                return 200, {}, stream
            if path == "/slow":

                def drip(wfile):
                    import time

                    for _ in range(50):
                        wfile.write(b"z")
                        wfile.flush()
                        time.sleep(0.2)

                return 200, {"Content-Length": "50"}, drip
            return 200, {"Content-Type": "text/plain"}, PUBLIC_PAGE

        with LabServer(public_route) as public:
            policy = SSRFPolicy(
                # The lab servers' ports are allowed so that the IP checks -- not the port check --
                # are what stand between an attacker and the internal service.
                allowed_ports=frozenset({80, 443, internal.port, public.port}),
                extra_allowed_networks=_nets("203.0.113.0/24"),
                max_body_bytes=1000,
                max_redirects=3,
                total_timeout=1.0,
            )
            dns = {
                "public.test": [[PUBLIC_IP]],
                "metadata.test": [["169.254.169.254"]],
            }
            routes = {PUBLIC_IP: ("127.0.0.1", public.port)}

            def net(extra_dns=None, extra_routes=None):
                return FakeNetwork({**dns, **(extra_dns or {})}, {**routes, **(extra_routes or {})})

            def fetch(url, network, pol=policy, **kw):
                return safe_fetch(url, pol, resolver=network.resolve, connect=network.create_connection, **kw)

            yield SimpleNamespace(internal=internal, public=public, policy=policy, net=net, fetch=fetch)


def _url(lab, path="/", host="public.test"):
    return f"http://{host}:{lab.public.port}{path}"


# --- pinning: the connection goes to the IP we validated ------------------------------------


def test_fetch_connects_to_validated_ip_and_keeps_hostname_in_host_header(lab):
    """Also the canary for the private `http.client._create_connection` hook: if a
    future stdlib stopped honoring it, the request would bypass the pinned
    connector, `connected_ips` would be empty, and this fails."""
    net = lab.net()
    result = lab.fetch(_url(lab), net)
    assert result.body == PUBLIC_PAGE
    assert result.connected_ip == PUBLIC_IP
    assert net.connected_ips == [PUBLIC_IP]
    assert lab.public.hits[0][1].startswith("public.test:")  # Host is the name, not the IP


def test_dns_rebinding_cannot_redirect_the_connection(lab):
    # 1st answer: a public IP (passes validation). 2nd answer: loopback (the attack).
    net = lab.net({"rebind.test": [[PUBLIC_IP], ["127.0.0.1"]]})
    result = lab.fetch(f"http://rebind.test:{lab.internal.port}/secret", net)
    assert net.lookups["rebind.test"] == 1  # resolved exactly once: there is no "second answer" to be fooled by
    assert net.connected_ips == [PUBLIC_IP]
    assert result.body == PUBLIC_PAGE  # served by the validated (public) IP, not the internal service
    assert lab.internal.hits == []


def test_name_resolving_to_loopback_is_blocked_before_any_connection(lab):
    net = lab.net({"attacker.test": [["127.0.0.1"]]})
    with pytest.raises(BlockedURLError, match="127.0.0.1"):
        lab.fetch(f"http://attacker.test:{lab.internal.port}/secret", net)
    assert net.connected_ips == [] and lab.internal.hits == []


def test_one_bad_answer_among_public_ones_blocks_the_whole_name(lab):
    net = lab.net({"mixed.test": [[PUBLIC_IP, "127.0.0.1"]]})
    with pytest.raises(BlockedURLError):
        lab.fetch(f"http://mixed.test:{lab.public.port}/", net)
    assert net.connected_ips == []


@pytest.mark.parametrize("target_ip", ["169.254.169.254", "168.63.129.16", "100.100.100.200", "::1", "fd00:ec2::254"])
def test_cloud_metadata_and_internal_names_are_blocked(lab, target_ip):
    net = lab.net({"meta.test": [[target_ip]]})
    with pytest.raises(BlockedURLError):
        lab.fetch(f"http://meta.test:{lab.public.port}/latest/meta-data/", net)
    assert net.connected_ips == []


# Obfuscated IP forms are handed to the OS resolver, which is what real
# requesters do -- so the *resolved* address is what must be judged.
@pytest.mark.parametrize("host", ["2130706433", "127.1", "0"])
def test_obfuscated_loopback_forms_are_blocked_as_loopback(lab, host):
    net = lab.net()
    with pytest.raises(BlockedURLError):
        lab.fetch(f"http://{host}:{lab.internal.port}/secret", net)
    assert net.connected_ips == [] and lab.internal.hits == []


@pytest.mark.parametrize(
    "host",
    [
        "0x7f.1", "0x7f000001", "017700000001", "0177.0.0.1", "127.0.0.1.",
        "127。0。0。1",  # ideographic full stops, normalized to dots by IDNA
        "[::1]", "[::ffff:127.0.0.1]", "[::ffff:7f00:1]", "[::127.0.0.1]", "[64:ff9b::7f00:1]", "[2002:7f00:1::]",
    ],
)
def test_no_spelling_of_loopback_ever_reaches_the_internal_service(lab, host):
    """Some spellings resolve differently per OS (macOS reads 0177.0.0.1 as 177.0.0.1,
    glibc as 127.0.0.1) or not at all. The invariant that must hold everywhere:
    no connection is opened and the internal service is never contacted."""
    net = lab.net()
    with pytest.raises((BlockedURLError, FetchError)):
        lab.fetch(f"http://{host}:{lab.internal.port}/secret", net)
    assert lab.internal.hits == []
    for ip in net.connected_ips:
        assert blocked_reason(ip, lab.policy) is None  # anything we did dial was individually allowed


# --- redirects: every hop is re-validated -----------------------------------------------------


@pytest.mark.parametrize("path", ["/redir", "/redir-decimal", "/redir-metadata", "/redir-file", "/redir-port22"])
def test_redirect_to_a_forbidden_target_is_blocked_on_that_hop(lab, path):
    net = lab.net()
    with pytest.raises(BlockedURLError):
        lab.fetch(_url(lab, path), net)
    assert lab.internal.hits == []
    assert net.connected_ips == [PUBLIC_IP]  # the first (public) hop only; nothing after it


def test_relative_redirect_is_followed_and_recorded(lab):
    result = lab.fetch(_url(lab, "/redir-rel"), lab.net())
    assert result.body == PUBLIC_PAGE
    assert result.chain == [_url(lab, "/redir-rel"), _url(lab, "/landing")]


def test_redirect_chain_within_limit_succeeds_and_beyond_limit_fails(lab):
    assert lab.fetch(_url(lab, "/chain/3"), lab.net()).body == b"end of chain"  # exactly max_redirects hops
    with pytest.raises(FetchError, match="redirects"):
        lab.fetch(_url(lab, "/chain/4"), lab.net())


def test_redirect_loop_terminates(lab):
    with pytest.raises(FetchError, match="redirects"):
        lab.fetch(_url(lab, "/loop"), lab.net())


def test_redirect_status_without_location_is_returned_as_is(lab):
    result = lab.fetch(_url(lab, "/no-location"), lab.net())
    assert result.status == 302 and result.body == b"redirect without a Location header"


# --- resource limits -----------------------------------------------------------------------------


def test_declared_oversize_body_is_rejected_from_the_header_alone(lab):
    with pytest.raises(FetchError, match="Content-Length"):
        lab.fetch(_url(lab, "/big"), lab.net())


def test_undeclared_oversize_body_is_cut_off_at_the_cap(lab):
    with pytest.raises(FetchError, match="exceeds limit"):
        lab.fetch(_url(lab, "/big-undeclared"), lab.net())


def test_slow_drip_response_hits_the_total_time_limit(lab):
    import time

    start = time.monotonic()
    with pytest.raises(FetchError, match="time limit"):
        lab.fetch(_url(lab, "/slow"), lab.net())
    assert time.monotonic() - start < 4  # bounded by total_timeout (1s) plus one drip, not the 10s the server would take


def test_slow_drip_of_response_headers_hits_the_total_time_limit(lab):
    """The body loop's deadline check can't help while http.client is still
    blocked inside header parsing -- this is what the watchdog is for."""
    import time

    def drip_headers(conn):
        conn.recv(4096)
        conn.sendall(b"HTTP/1.1 200 OK\r\nX-Drip: ")
        for _ in range(100):  # would take ~30s to finish the header line
            conn.sendall(b"a")
            time.sleep(0.3)

    with RawServer(drip_headers) as raw:
        net = FakeNetwork({"public.test": [[PUBLIC_IP]]}, {PUBLIC_IP: ("127.0.0.1", raw.port)})
        policy = SSRFPolicy(allowed_ports=frozenset({raw.port}), extra_allowed_networks=_nets("203.0.113.0/24"), total_timeout=1.0)
        start = time.monotonic()
        with pytest.raises(FetchError, match="time limit"):
            safe_fetch(f"http://public.test:{raw.port}/", policy, resolver=net.resolve, connect=net.create_connection)
        assert time.monotonic() - start < 4


def test_body_shorter_than_declared_content_length_is_an_error_not_a_silent_success(lab):
    def cut_short(path, headers):
        def write_some(wfile):
            wfile.write(b"only ten b")  # declares 100, sends 10, then hangs up

        return 200, {"Content-Length": "100"}, write_some

    with LabServer(cut_short) as short:
        net = FakeNetwork({"public.test": [[PUBLIC_IP]]}, {PUBLIC_IP: ("127.0.0.1", short.port)})
        policy = SSRFPolicy(allowed_ports=frozenset({short.port}), extra_allowed_networks=_nets("203.0.113.0/24"))
        with pytest.raises(FetchError, match="truncated"):
            safe_fetch(f"http://public.test:{short.port}/", policy, resolver=net.resolve, connect=net.create_connection)


def test_falls_back_to_next_validated_ip_when_the_first_refuses(lab):
    dead = _free_port()  # nothing listening
    net = lab.net(
        {"multi.test": [[PUBLIC_IP, "203.0.113.11"]]},
        {PUBLIC_IP: ("127.0.0.1", dead), "203.0.113.11": ("127.0.0.1", lab.public.port)},
    )
    result = lab.fetch(f"http://multi.test:{lab.public.port}/", net)
    assert result.connected_ip == "203.0.113.11"
    assert net.connected_ips == [PUBLIC_IP, "203.0.113.11"]


def test_unresolvable_name_is_a_fetch_error_not_a_block(lab):
    net = lab.net()
    with pytest.raises(FetchError, match="resolve"):
        lab.fetch(f"http://does-not-exist.invalid:{lab.public.port}/", net)


# --- policy is genuinely policy (not "always blocks") --------------------------------------------


def test_explicit_opt_in_allows_a_specific_internal_service(lab):
    opted_in = SSRFPolicy(
        allowed_ports=lab.policy.allowed_ports, extra_allowed_networks=_nets("127.0.0.1/32"), max_body_bytes=1000
    )
    net = lab.net()
    result = safe_fetch(
        f"http://127.0.0.1:{lab.internal.port}/secret", opted_in, resolver=net.resolve, connect=net.create_connection
    )
    assert result.body == SECRET  # the same URL the default policy blocks, allowed only because we said so


# --- HTTPS: pinning must not weaken certificate verification --------------------------------------


def _make_cert(tmp_path, hostname):
    key, crt = tmp_path / "key.pem", tmp_path / "cert.pem"
    proc = subprocess.run(
        ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", str(key), "-out", str(crt),
         "-days", "1", "-subj", f"/CN={hostname}", "-addext", f"subjectAltName=DNS:{hostname}"],
        capture_output=True,
    )
    if proc.returncode != 0:
        pytest.skip("openssl unavailable or too old to generate a SAN certificate")
    return key, crt


def test_https_pinned_to_ip_still_verifies_certificate_against_the_hostname(tmp_path):
    key, crt = _make_cert(tmp_path, "public.test")
    server_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_ctx.load_cert_chain(str(crt), str(key))
    client_ctx = ssl.create_default_context(cafile=str(crt))  # trusts only our test cert; hostname checking ON

    with LabServer(lambda path, headers: (200, {}, b"secure page"), ssl_context=server_ctx) as tls:
        policy = SSRFPolicy(
            allowed_ports=frozenset({443, tls.port}), extra_allowed_networks=_nets("203.0.113.0/24")
        )
        routes = {PUBLIC_IP: ("127.0.0.1", tls.port)}

        good = FakeNetwork({"public.test": [[PUBLIC_IP]]}, routes)
        result = safe_fetch(
            f"https://public.test:{tls.port}/", policy, resolver=good.resolve,
            connect=good.create_connection, ssl_context=client_ctx,
        )
        assert result.body == b"secure page" and good.connected_ips == [PUBLIC_IP]

        # Same server, same pinned IP, but the URL names a host the certificate does not cover.
        wrong = FakeNetwork({"other.test": [[PUBLIC_IP]]}, routes)
        with pytest.raises(FetchError):
            safe_fetch(
                f"https://other.test:{tls.port}/", policy, resolver=wrong.resolve,
                connect=wrong.create_connection, ssl_context=client_ctx,
            )


# --- the baselines really are vulnerable (negative controls) ----------------------------------------
# If these stopped leaking, the tests above would prove nothing about safe_fetch.


def test_control_substring_blocklist_leaks_via_obfuscated_ip_and_file_scheme(lab, tmp_path):
    secret_file = tmp_path / "s.txt"
    secret_file.write_bytes(SECRET)
    with lab.net().patched():
        assert substring_blocklist_fetch(f"http://2130706433:{lab.internal.port}/secret") == SECRET
        assert substring_blocklist_fetch(f"http://0x7f.1:{lab.internal.port}/secret") == SECRET
        assert substring_blocklist_fetch(f"file://{secret_file}") == SECRET


def test_control_check_then_fetch_leaks_via_dns_rebinding(lab):
    net = lab.net({"rebind.test": [[PUBLIC_IP], ["127.0.0.1"]]})
    with net.patched():
        body = check_then_fetch(f"http://rebind.test:{lab.internal.port}/secret", lab.policy, net.resolve)
    assert body == SECRET and lab.internal.hits


def test_control_check_then_fetch_leaks_via_redirect(lab):
    net = lab.net()
    with net.patched():
        body = check_then_fetch(_url(lab, "/redir"), lab.policy, net.resolve)
    assert body == SECRET


def test_same_attacks_do_not_leak_through_safe_fetch(lab, tmp_path):
    """The demo's full attack list, replayed as a regression test."""
    secret_file = tmp_path / "s.txt"
    secret_file.write_bytes(SECRET)
    for name, url, attack_dns in solution._demo_attacks(lab.internal.port, lab.public.port, str(secret_file)):
        net = lab.net(attack_dns)
        try:
            body = lab.fetch(url, net).body
        except (BlockedURLError, FetchError):
            body = b""
        assert SECRET not in body, f"leaked via: {name}"
        assert lab.internal.hits == [], f"internal service was contacted via: {name}"
