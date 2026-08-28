import socket
import pytest
from company_brain import webui


@pytest.fixture(autouse=True)
def _stop_started_server():
    """`webui.start()` leaves a daemon thread and listening socket alive for
    the rest of the process otherwise; every test that starts one via
    `webui.start` gets it torn down here."""
    yield
    webui.stop()


def test_binds_loopback_only():
    server = webui.bind(0)
    try:
        assert server.server_address[0] == "127.0.0.1"
    finally:
        server.server_close()


def test_ladder_falls_forward_when_preferred_is_taken():
    """Occupies preferred AND preferred+1 so only walking the ladder lands on
    preferred+2 — a stub that ignores `preferred` and always binds ephemeral
    would not reliably produce this exact value."""
    first = socket.socket()
    first.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    first.bind(("127.0.0.1", 0))
    preferred = first.getsockname()[1]
    first.listen(1)

    second = socket.socket()
    second.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    second.bind(("127.0.0.1", preferred + 1))
    second.listen(1)
    try:
        server = webui.bind(preferred)
        try:
            assert server.server_address[1] == preferred + 2
        finally:
            server.server_close()
    finally:
        first.close()
        second.close()


@pytest.mark.parametrize("header,ok", [
    ("127.0.0.1:4717", True),
    ("localhost:4717", True),
    ("evil.example.com:4717", False),
    ("127.0.0.1:9999", False),
    (None, False),
    ("", False),
    # Adversarial cases: nothing but the exact allowlisted strings may pass.
    ("LOCALHOST:4717", False),          # case
    ("localhost.:4717", False),         # trailing dot
    ("[::1]:4717", False),              # IPv6 loopback
    ("x@127.0.0.1:4717", False),        # userinfo
    (" 127.0.0.1:4717", False),         # leading space
    ("127.0.0.1", False),               # no port
    ("127.0.0.1:4717.evil.com", False),  # suffix
])
def test_host_header_guard(header, ok):
    assert webui.host_allowed(header, 4717) is ok


def test_host_header_guard_rejects_blacklist_stub():
    """A blacklist stub (`header not in {known-bad values}`) passes every case
    above except this one, so this pins down that the implementation is an
    allowlist, not a blacklist."""
    assert webui.host_allowed("attacker.com:4717", 4717) is False


@pytest.mark.parametrize("raw,expected", [
    ("-1", webui.DEFAULT_PORT),
    ("999999999", webui.DEFAULT_PORT),
    ("not-a-number", webui.DEFAULT_PORT),
    ("", webui.DEFAULT_PORT),
    ("0", webui.DEFAULT_PORT),
    ("65536", webui.DEFAULT_PORT),
    ("4717", 4717),
    ("65535", 65535),
])
def test_configured_port_rejects_out_of_range_values(monkeypatch, raw, expected):
    monkeypatch.setenv("COMPANY_BRAIN_VIEW_PORT", raw)
    assert webui.configured_port() == expected


def test_configured_port_falls_back_when_env_unset(monkeypatch):
    monkeypatch.delenv("COMPANY_BRAIN_VIEW_PORT", raising=False)
    assert webui.configured_port() == webui.DEFAULT_PORT


def test_bind_falls_through_ladder_on_negative_preferred():
    """A bad explicit argument (as `configured_port` could in principle still
    hand it, or a caller bypassing that guard) must not raise OverflowError
    out of `bind` — it should fall through the ladder to an ephemeral port."""
    server = webui.bind(-1)
    try:
        assert server.server_address[0] == "127.0.0.1"
        assert server.server_address[1] > 0
    finally:
        server.server_close()


def test_bind_falls_through_ladder_on_absurd_preferred():
    server = webui.bind(999999999)
    try:
        assert server.server_address[0] == "127.0.0.1"
        assert server.server_address[1] > 0
    finally:
        server.server_close()


import json
import urllib.error
import urllib.request


def _get(url, host=None):
    request = urllib.request.Request(url)
    if host:
        request.add_header("Host", host)
    return urllib.request.urlopen(request, timeout=5)


def test_serves_api_and_rejects_foreign_host(tmp_path, monkeypatch):
    monkeypatch.setenv("COMPANY_BRAIN_PROJECT_DIR", str(tmp_path))
    from company_brain import db
    db.connect().close()

    base = webui.start(0)
    assert base.startswith("http://127.0.0.1:")

    with _get(base + "/api/overview") as response:
        assert response.status == 200
        assert json.loads(response.read())["empty"] is True

    with _get(base + "/") as response:
        assert response.status == 200
        assert b"<" in response.read()

    with pytest.raises(urllib.error.HTTPError) as caught:
        _get(base + "/api/overview", host="evil.example.com")
    assert caught.value.code == 403

    with pytest.raises(urllib.error.HTTPError) as caught:
        _get(base + "/nope")
    assert caught.value.code == 404


def test_no_cors_header_is_emitted(tmp_path, monkeypatch):
    monkeypatch.setenv("COMPANY_BRAIN_PROJECT_DIR", str(tmp_path))
    from company_brain import db
    db.connect().close()
    base = webui.start(0)
    with _get(base + "/api/overview") as response:
        assert response.headers.get("Access-Control-Allow-Origin") is None


def test_graph_malformed_limit_falls_back_instead_of_500(tmp_path, monkeypatch):
    monkeypatch.setenv("COMPANY_BRAIN_PROJECT_DIR", str(tmp_path))
    from company_brain import db
    db.connect().close()
    base = webui.start(0)
    with _get(base + "/api/graph?limit=abc") as response:
        assert response.status == 200
        payload = json.loads(response.read())
        assert payload["empty"] is True


def test_raising_builder_returns_500_json_and_thread_survives(tmp_path, monkeypatch):
    monkeypatch.setenv("COMPANY_BRAIN_PROJECT_DIR", str(tmp_path))
    from company_brain import db, views
    db.connect().close()

    def boom(conn):
        raise RuntimeError("boom")

    monkeypatch.setattr(views, "overview", boom)
    base = webui.start(0)

    with pytest.raises(urllib.error.HTTPError) as caught:
        _get(base + "/api/overview")
    assert caught.value.code == 500
    body = json.loads(caught.value.read())
    assert body["error"] == "internal"
    assert body["detail"] == "RuntimeError"
    assert "boom" not in json.dumps(body)

    # The server thread must have survived the raise.
    with _get(base + "/api/corpus") as response:
        assert response.status == 200
        assert json.loads(response.read())["empty"] is True
