import hashlib
import json
from types import SimpleNamespace
from unittest.mock import Mock

import httpx2 as httpx
import pytest
from openai import OpenAI, APIConnectionError, APITimeoutError

from mth.editorial import (SAMPLE_TEXT, EditorialError, EditorialResponse,
                           live_review, recorded_review)


@pytest.fixture
def revision():
    return {"id": "fixture-revision", "text": SAMPLE_TEXT,
            "content_hash": hashlib.sha256(SAMPLE_TEXT.encode()).hexdigest()}


def result(revision):
    return {"revision_id": revision["id"], "content_hash": revision["content_hash"],
            "suggestions": recorded_review(revision)}


def test_recorded_is_explicit_sample_fixture(revision, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    edits = recorded_review(revision)
    assert len(edits) == 2
    assert edits[0]["original"] in SAMPLE_TEXT
    revision["text"] += "\r\n"
    revision["content_hash"] = hashlib.sha256(revision["text"].encode()).hexdigest()
    with pytest.raises(EditorialError, match="original sample only"):
        recorded_review(revision)


def test_missing_key_never_falls_back(revision, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(EditorialError, match="No model was called"):
        live_review(revision)


def test_real_sdk_parses_mock_http_response_and_binds_source(revision):
    requests = []
    def handler(request):
        body = json.loads(request.content)
        requests.append(body)
        return httpx.Response(200, json={
            "id": "resp_mock", "object": "response", "created_at": 0,
            "status": "completed", "model": "gpt-6-astra",
            "output": [{"id": "msg_mock", "type": "message", "role": "assistant",
                        "status": "completed", "content": [{"type": "output_text",
                        "text": json.dumps(result(revision)), "annotations": []}]}],
        })
    client = OpenAI(api_key="test-key-not-real", max_retries=0,
                    http_client=httpx.Client(transport=httpx.MockTransport(handler)))
    assert live_review(revision, client=client) == recorded_review(revision)
    body = requests[0]
    assert body["store"] is False
    assert body["text"]["format"]["type"] == "json_schema"
    assert json.loads(body["input"][1]["content"])["manuscript"] == SAMPLE_TEXT
    assert "tools" not in body


@pytest.mark.parametrize("status,match", [(401, "API key"), (429, "quota"), (500, "HTTP 500")])
def test_http_errors_are_actionable(revision, status, match):
    client = OpenAI(api_key="test-key-not-real", max_retries=0,
                    http_client=httpx.Client(transport=httpx.MockTransport(
                        lambda request: httpx.Response(status, json={"error": {"message": "failure"}}))))
    with pytest.raises(EditorialError, match=match):
        live_review(revision, client=client)


@pytest.mark.parametrize("kind", ["refusal", "incomplete", "wrong_revision", "wrong_hash", "malformed"])
def test_unusable_model_outputs_rejected(revision, kind):
    data = result(revision)
    if kind == "wrong_revision":
        data["revision_id"] = "other"
    if kind == "wrong_hash":
        data["content_hash"] = "0" * 64
    parsed = EditorialResponse.model_validate(data)
    if kind == "refusal":
        parsed = None
    if kind == "malformed":
        parsed = {"unknown": True}
    client = Mock()
    client.responses.parse.return_value = SimpleNamespace(
        status="incomplete" if kind == "incomplete" else "completed", output_parsed=parsed)
    with pytest.raises(EditorialError):
        live_review(revision, client=client)


@pytest.mark.parametrize("error", [APIConnectionError, APITimeoutError])
def test_network_failure_preserves_revision(revision, error):
    original = dict(revision)
    client = Mock()
    client.responses.parse.side_effect = error(request=httpx.Request("POST", "https://api.openai.com/v1/responses"))
    with pytest.raises(EditorialError, match="connection"):
        live_review(revision, client=client)
    assert revision == original


def test_inconsistent_source_hash_rejected_before_call(revision):
    revision["text"] += "changed"
    client = Mock()
    with pytest.raises(EditorialError, match="hash"):
        live_review(revision, client=client)
    client.responses.parse.assert_not_called()
