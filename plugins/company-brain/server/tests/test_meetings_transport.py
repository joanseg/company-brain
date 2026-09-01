"""The Circleback transport: auth, JSON and SSE envelopes, error messages."""
import io
import json

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


def test_call_raises_the_json_rpc_error_message(monkeypatch):
    monkeypatch.setenv("CIRCLEBACK_API_KEY", "cb_test")
    body = json.dumps({"jsonrpc": "2.0", "id": 1,
                       "error": {"code": -32000, "message": "Permission denied."}})
    with pytest.raises(ValueError) as excinfo:
        meetings._call("SearchMeetings", {},
                       opener=lambda r, timeout=None: FakeResponse(body))
    assert "Permission denied." in str(excinfo.value)
