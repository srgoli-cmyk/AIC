"""Keeps tests off the network. The error is a BaseException, so that the code under test cannot
catch it with `except Exception` and turn it into an ordinary error response."""

import ipaddress
import socket

import httpx


class NetworkBlocked(BaseException):  # noqa: N818
    pass


def blocked_request(self, request):
    raise NetworkBlocked(f"a test tried a real HTTP request to {request.url.host}")


def _is_local(host) -> bool:
    if host in ("localhost", "", None):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _guard_connect(original):
    def connect(self, address, *args, **kwargs):
        # Unix sockets have a path as the address; TCP has (host, port, ...).
        if isinstance(address, tuple) and not _is_local(address[0]):
            raise NetworkBlocked(f"a test tried to connect to {address[0]}")
        return original(self, address, *args, **kwargs)

    return connect


def _guard_getaddrinfo(original):
    def getaddrinfo(host, *args, **kwargs):
        if not _is_local(host):
            raise NetworkBlocked(f"a test tried to look up {host}")
        return original(host, *args, **kwargs)

    return getaddrinfo


def block_network(monkeypatch) -> None:
    """Block httpx, and sockets and DNS for any non-loopback address. Local Postgres still works
    (its driver does not use these Python sockets, and loopback is allowed anyway)."""
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", blocked_request)
    monkeypatch.setattr(socket.socket, "connect", _guard_connect(socket.socket.connect))
    monkeypatch.setattr(socket, "getaddrinfo", _guard_getaddrinfo(socket.getaddrinfo))
