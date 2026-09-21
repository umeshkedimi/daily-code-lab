"""Test/demo harness: local HTTP servers plus a fake DNS + routing layer.

Everything here binds to 127.0.0.1 and only talks to servers this process
started. `FakeNetwork` lets an experiment say "attacker.test resolves to
127.0.0.1" or "rebind.test resolves to a public IP the first time and to
127.0.0.1 the second time", and lets a "public" IP such as 203.0.113.10
(TEST-NET-3, never routable on the real internet) be routed to a local
server -- so DNS-rebinding and redirect attacks are reproducible offline.
"""

from __future__ import annotations

import ipaddress
import socket
import threading
from collections import defaultdict
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable, Dict, List, Optional, Tuple, Union

_real_getaddrinfo = socket.getaddrinfo
_real_create_connection = socket.create_connection

Body = Union[bytes, Callable]  # bytes, or callable(wfile) that streams its own response body
Route = Callable[[str, Dict[str, str]], Tuple[int, Dict[str, str], Body]]


class LabServer:
    """A tiny local HTTP server. `route(path, headers) -> (status, headers, body)`.
    Every request is recorded in `.hits` as (path, Host header) so a test
    can assert a server was -- or, more importantly, was never -- contacted.
    """

    def __init__(self, route: Route, ssl_context=None):
        self.hits: List[Tuple[str, Optional[str]]] = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args):  # silence
                pass

            def do_GET(self):
                outer.hits.append((self.path, self.headers.get("Host")))
                status, headers, body = route(self.path, dict(self.headers))
                self.send_response(status)
                for k, v in headers.items():
                    self.send_header(k, v)
                if callable(body):
                    self.send_header("Connection", "close")
                    self.end_headers()
                    self.close_connection = True
                    try:
                        body(self.wfile)
                    except (BrokenPipeError, ConnectionResetError):
                        pass
                else:
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)

        self._httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        # A client that hangs up mid-request (a size/timeout test, a failed TLS
        # verification) is an expected event here, not something to print.
        self._httpd.handle_error = lambda *args: None
        if ssl_context is not None:
            self._httpd.socket = ssl_context.wrap_socket(self._httpd.socket, server_side=True)
        self.port = self._httpd.server_address[1]
        # serve_forever's default 0.5s poll interval makes shutdown() take up to 0.5s per server.
        self._thread = threading.Thread(target=lambda: self._httpd.serve_forever(poll_interval=0.02), daemon=True)

    def __enter__(self) -> "LabServer":
        self._thread.start()
        return self

    def __exit__(self, *exc) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()


class RawServer:
    """A bare TCP server for misbehavior an HTTP framework won't express,
    e.g. dripping response *headers* one byte at a time. `handler(conn)` is
    called on a thread per accepted connection."""

    def __init__(self, handler: Callable[[socket.socket], None]):
        self._sock = socket.socket()
        self._sock.bind(("127.0.0.1", 0))
        self._sock.listen(8)
        self.port = self._sock.getsockname()[1]
        self._handler = handler
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._serve, daemon=True)

    def _serve(self) -> None:
        self._sock.settimeout(0.2)
        while not self._stop.is_set():
            try:
                conn, _ = self._sock.accept()
            except (socket.timeout, OSError):
                continue
            threading.Thread(target=self._run, args=(conn,), daemon=True).start()

    def _run(self, conn: socket.socket) -> None:
        try:
            self._handler(conn)
        except OSError:
            pass
        finally:
            conn.close()

    def __enter__(self) -> "RawServer":
        self._thread.start()
        return self

    def __exit__(self, *exc) -> None:
        self._stop.set()
        self._sock.close()


def _is_ip_literal(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        return False


class FakeNetwork:
    """Fake DNS + routing.

    dns:    name -> list of answers; the Nth lookup of a name returns the Nth
            answer (the last one repeats). Names not listed fall through to
            the real resolver, so obfuscated forms like '2130706433' get the
            operating system's genuine interpretation.
    routes: ip -> (host, port) actual destination. An IP with no route is
            connected to as-is.
    """

    def __init__(self, dns: Dict[str, List[List[str]]], routes: Optional[Dict[str, Tuple[str, int]]] = None):
        self.dns = dns
        self.routes = routes or {}
        self.lookups: Dict[str, int] = defaultdict(int)
        self.connected_ips: List[str] = []  # every IP a connection was actually opened to

    def resolve(self, host: str, port=None) -> List[str]:
        if host in self.dns:
            answers = self.dns[host]
            n = self.lookups[host]
            self.lookups[host] += 1
            return list(answers[min(n, len(answers) - 1)])
        infos = _real_getaddrinfo(host, port or 0, type=socket.SOCK_STREAM)
        out: List[str] = []
        for info in infos:
            ip = info[4][0].split("%", 1)[0]
            if ip not in out:
                out.append(ip)
        return out

    def getaddrinfo(self, host, port, family=0, type=0, proto=0, flags=0):
        if host in self.dns:
            return [
                (socket.AF_INET6 if ":" in ip else socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port or 0))
                for ip in self.resolve(host, port)
            ]
        return _real_getaddrinfo(host, port, family, type, proto, flags)

    def create_connection(self, address, timeout=socket._GLOBAL_DEFAULT_TIMEOUT, source_address=None):
        host, port = address
        ip = host if _is_ip_literal(host) else self.resolve(host, port)[0]
        self.connected_ips.append(ip)
        real = self.routes.get(ip, (ip, port))
        return _real_create_connection(real, timeout, source_address)

    @contextmanager
    def patched(self):
        """Route the *process-wide* socket module through this fake network, so
        code that resolves and connects by itself (urllib) is subject to it."""
        socket.getaddrinfo = self.getaddrinfo
        socket.create_connection = self.create_connection
        try:
            yield self
        finally:
            socket.getaddrinfo = _real_getaddrinfo
            socket.create_connection = _real_create_connection
