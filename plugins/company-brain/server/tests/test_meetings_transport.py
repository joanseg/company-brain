"""The Circleback transport: auth, JSON and SSE envelopes, error messages."""
import io
import json
import urllib.error

import pytest

from company_brain import meetings


class FakeResponse(io.BytesIO):
    def __init__(self, body: str, content_type: str = "application/json"):
        super().__init__(body.encode())
        self.headers = {"Content-Type": content_type}


def _rpc(payload: dict) -> str:
    return json.dumps({"jsonrpc": "2.0", "id": 1, "result": payload})


def test_missing_api_key_names_the_settings_page(monkeypatch):
    monkeypatch.delenv("CIRCLEBACK_API_KEY", raising=False)
    with pytest.raises(ValueError) as excinfo:
        meetings._api_key()
    assert meetings.SETTINGS_URL in str(excinfo.value)


def test_call_skips_the_network_when_key_is_unset(monkeypatch):
    monkeypatch.delenv("CIRCLEBACK_API_KEY", raising=False)

    def tripwire(request, timeout=None):
        raise AssertionError("must not make a network call when the key is unset")

    with pytest.raises(ValueError) as excinfo:
        meetings._call("SearchMeetings", {}, opener=tripwire)
    assert meetings.SETTINGS_URL in str(excinfo.value)


def test_call_sends_bearer_token_and_intent(monkeypatch):
    monkeypatch.setenv("CIRCLEBACK_API_KEY", "cb_test")
    captured = {}

    def opener(request, timeout=None):
        captured["url"] = request.full_url
        captured["auth"] = request.get_header("Authorization")
        captured["body"] = json.loads(request.data)
        return FakeResponse(_rpc({"structuredContent": {"meetings": []}}))

    meetings._call("SearchMeetings", {"startDate": "2026-08-01"}, opener=opener)

    assert captured["url"] == meetings.API_URL
    assert captured["auth"] == "Bearer cb_test"
    assert captured["body"]["method"] == "tools/call"
    assert captured["body"]["params"]["name"] == "SearchMeetings"
    assert captured["body"]["params"]["arguments"]["intent"]


def test_call_unwraps_structured_content(monkeypatch):
    monkeypatch.setenv("CIRCLEBACK_API_KEY", "cb_test")
    body = _rpc({"structuredContent": {"meetings": [{"id": "m1"}]}})
    result = meetings._call("SearchMeetings", {}, opener=lambda r, timeout=None: FakeResponse(body))
    assert result == {"meetings": [{"id": "m1"}]}


def test_call_unwraps_json_inside_text_content(monkeypatch):
    monkeypatch.setenv("CIRCLEBACK_API_KEY", "cb_test")
    inner = json.dumps({"meetings": [{"id": "m2"}]})
    body = _rpc({"content": [{"type": "text", "text": inner}]})
    result = meetings._call("SearchMeetings", {}, opener=lambda r, timeout=None: FakeResponse(body))
    assert result == {"meetings": [{"id": "m2"}]}


def test_call_parses_a_server_sent_events_response(monkeypatch):
    monkeypatch.setenv("CIRCLEBACK_API_KEY", "cb_test")
    body = "event: message\ndata: %s\n\n" % _rpc({"structuredContent": {"meetings": []}})
    result = meetings._call(
        "SearchMeetings", {},
        opener=lambda r, timeout=None: FakeResponse(body, "text/event-stream"))
    assert result == {"meetings": []}


def test_call_parses_a_multi_line_sse_frame(monkeypatch):
    monkeypatch.setenv("CIRCLEBACK_API_KEY", "cb_test")
    payload = _rpc({"structuredContent": {"meetings": [{"id": "m3"}]}})
    # Split inside the ", " between two top-level members: not inside a quoted
    # string (a raw newline there would make the JSON itself invalid), but a
    # genuine mid-document cut -- neither half is complete JSON on its own,
    # so this only passes if `_body` actually joins the two data: lines.
    split_at = payload.index(', "result"') + 1
    first_half, second_half = payload[:split_at], payload[split_at:]
    with pytest.raises(json.JSONDecodeError):
        json.loads(first_half)
    with pytest.raises(json.JSONDecodeError):
        json.loads(second_half)

    body = "event: message\ndata: %s\ndata: %s\n\n" % (first_half, second_half)
    result = meetings._call(
        "SearchMeetings", {},
        opener=lambda r, timeout=None: FakeResponse(body, "text/event-stream"))
    assert result == {"meetings": [{"id": "m3"}]}


def test_call_stops_the_sse_frame_at_the_first_blank_line(monkeypatch):
    monkeypatch.setenv("CIRCLEBACK_API_KEY", "cb_test")
    first_event = _rpc({"structuredContent": {"meetings": []}})
    second_event = _rpc({"structuredContent": {"meetings": [{"id": "should-not-appear"}]}})
    body = (
        "event: message\ndata: %s\n\n"
        "event: message\ndata: %s\n\n" % (first_event, second_event)
    )
    result = meetings._call(
        "SearchMeetings", {},
        opener=lambda r, timeout=None: FakeResponse(body, "text/event-stream"))
    assert result == {"meetings": []}


def test_call_raises_the_json_rpc_error_message(monkeypatch):
    monkeypatch.setenv("CIRCLEBACK_API_KEY", "cb_test")
    body = json.dumps({"jsonrpc": "2.0", "id": 1,
                       "error": {"code": -32000, "message": "Permission denied."}})
    with pytest.raises(ValueError) as excinfo:
        meetings._call("SearchMeetings", {},
                       opener=lambda r, timeout=None: FakeResponse(body))
    assert "Permission denied." in str(excinfo.value)


def test_call_raises_value_error_for_a_malformed_body(monkeypatch):
    monkeypatch.setenv("CIRCLEBACK_API_KEY", "cb_test")
    with pytest.raises(ValueError) as excinfo:
        meetings._call("SearchMeetings", {},
                       opener=lambda r, timeout=None: FakeResponse("not json"))
    assert "unreadable response" in str(excinfo.value)


def test_call_raises_circlebacks_message_for_a_401(monkeypatch):
    monkeypatch.setenv("CIRCLEBACK_API_KEY", "cb_test")
    error_body = json.dumps({"error": "Invalid API key."})

    def opener(request, timeout=None):
        raise urllib.error.HTTPError(
            meetings.API_URL, 401, "Unauthorized", None,
            io.BytesIO(error_body.encode()))

    with pytest.raises(ValueError) as excinfo:
        meetings._call("SearchMeetings", {}, opener=opener)
    assert "Invalid API key." in str(excinfo.value)
    assert meetings.SETTINGS_URL in str(excinfo.value)
    assert "cb_test" not in str(excinfo.value)


def test_call_raises_for_a_429(monkeypatch):
    monkeypatch.setenv("CIRCLEBACK_API_KEY", "cb_test")

    def opener(request, timeout=None):
        raise urllib.error.HTTPError(
            meetings.API_URL, 429, "Too Many Requests", None,
            io.BytesIO(b"Rate limit exceeded."))

    with pytest.raises(ValueError) as excinfo:
        meetings._call("SearchMeetings", {}, opener=opener)
    assert "429" in str(excinfo.value)
    assert "retry" in str(excinfo.value).lower()


def test_call_raises_value_error_for_a_connection_failure(monkeypatch):
    monkeypatch.setenv("CIRCLEBACK_API_KEY", "cb_test")

    def opener(request, timeout=None):
        raise urllib.error.URLError("Name or service not known")

    with pytest.raises(ValueError):
        meetings._call("SearchMeetings", {}, opener=opener)
