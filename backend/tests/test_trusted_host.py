"""DNS rebinding: a request whose Host header is not allowed is refused with 400 before the Origin
check (which treats the Host as "our own origin") or any route runs."""

from fastapi.testclient import TestClient

from app import config
from app.main import app

client = TestClient(app)


def test_foreign_host_gets_400_on_a_read():
    r = client.get("/health", headers={"Host": "evil.example"})
    assert r.status_code == 400
    assert r.text == "Invalid host header"


def test_rebinding_post_is_refused_before_the_origin_check():
    # The attacker's page claims Origin = Host = evil.example. Without the host check the Origin
    # check would call that "own origin" and let it through to /auth/logout.
    r = client.post(
        "/auth/logout", headers={"Host": "evil.example:8000", "Origin": "http://evil.example:8000"}
    )
    assert r.status_code == 400


def test_localhost_and_loopback_ip_pass_with_a_port():
    for host in ("localhost:8000", "127.0.0.1:8000", "localhost"):
        r = client.get("/health", headers={"Host": host})
        assert r.status_code == 200, host


def test_a_look_alike_host_is_refused():
    for host in ("localhost.evil.example", "evil.example:8000@localhost", "127.0.0.1.evil.example"):
        r = client.get("/health", headers={"Host": host})
        assert r.status_code == 400, host


def test_wildcard_entry_is_ignored():
    assert config.allowed_host_list(" localhost, ,*, 127.0.0.1 ") == ["localhost", "127.0.0.1"]
    assert config.allowed_host_list("") == []
