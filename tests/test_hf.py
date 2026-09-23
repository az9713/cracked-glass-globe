import json

import httpx
import pytest

import hf


@pytest.fixture
def sdk():
    value = hf.SafeClient(api_key="test-id:test-secret", base_url=hf.PRODUCTION, timeout=1)
    yield value
    value.close()


def mock_http(sdk, handler):
    # Retain the real SDK's credentials and origin guard, replace only the wire.
    real = sdk._client
    sdk.__dict__["_client"] = httpx.Client(
        base_url=sdk.base_url, headers=real.headers, event_hooks=real.event_hooks,
        transport=httpx.MockTransport(handler), timeout=1,
    )
    real.close()


def test_existing_env_alias_and_separate_keys():
    assert hf.credentials({"HIGGSFIELD_API_KEY": "id:secret"}) == "id:secret"
    assert hf.credentials({"HF_API_KEY": "id", "HF_API_SECRET": "secret"}) == "id:secret"


@pytest.mark.parametrize("value", ["", "only-one-part", "id:", "id:secret:extra", "id:secret\nBAD"])
def test_bad_credentials_are_not_disclosed(value):
    with pytest.raises(hf.ConfigurationError) as exc:
        hf.credentials({"HF_KEY": value})
    assert "only-one-part" not in str(exc.value)
    assert "BAD" not in str(exc.value)


@pytest.mark.parametrize("value", ["https://evil.example/model", "../model", "//evil.example/x?key=x", "model#x"])
def test_rejects_non_model_paths(value):
    with pytest.raises(hf.ConfigurationError):
        hf.model_id(value)


def test_generic_submission_preserves_nested_inputs(sdk):
    calls = []
    arguments = {"prompt": "hello", "future_setting": {"images": ["https://example.test/a.png"], "weight": 0.3}}

    def respond(request):
        calls.append(request)
        return httpx.Response(200, json={"request_id": "job-123",
            "status_url": hf.PRODUCTION + "/requests/job-123/status",
            "cancel_url": hf.PRODUCTION + "/requests/job-123/cancel"})

    mock_http(sdk, respond)
    controller = sdk.submit("future-vendor/new-model/v9", arguments=arguments)
    assert controller.request_id == "job-123"
    assert calls[0].url.path == "/future-vendor/new-model/v9"
    assert json.loads(calls[0].content) == arguments
    assert calls[0].headers["Authorization"] == "Key test-id:test-secret"


@pytest.mark.parametrize("failure", ["500", "timeout"])
def test_submission_is_never_retried(sdk, failure):
    calls = []

    def respond(request):
        calls.append(request)
        if failure == "timeout":
            raise httpx.ReadTimeout("uncertain acceptance", request=request)
        return httpx.Response(500, json={"detail": "error"})

    mock_http(sdk, respond)
    with pytest.raises(Exception):
        sdk.submit("vendor/model", arguments={"prompt": "test"})
    assert len(calls) == 1


def test_wait_preserves_all_outputs_and_retries_only_reads(sdk, monkeypatch):
    monkeypatch.setattr(hf.time, "sleep", lambda _: None)
    result = {"status": "completed", "audio": {"url": "https://example.test/a.wav"}, "unknown_output": [1, 2]}
    replies = [httpx.Response(503), httpx.Response(200, json={"status": "queued"}), httpx.Response(200, json=result)]
    calls = []

    def respond(request):
        calls.append(request.method)
        return replies.pop(0)

    mock_http(sdk, respond)
    assert sdk.wait("job-1") == result
    assert calls == ["GET", "GET", "GET"]


@pytest.mark.parametrize("state", ["failed", "nsfw", "canceled", "cancelled"])
def test_wait_stops_on_failure(sdk, state):
    mock_http(sdk, lambda _: httpx.Response(200, json={"status": state}))
    assert sdk.wait("job-1")["status"] == state


def test_wait_deadline_does_not_cancel_or_resubmit(sdk, monkeypatch):
    clock = iter([0, 0, 0, 2])
    monkeypatch.setattr(hf.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(hf.time, "sleep", lambda _: None)
    calls = []

    def respond(request):
        calls.append(request.method)
        return httpx.Response(200, json={"status": "queued"})

    mock_http(sdk, respond)
    with pytest.raises(TimeoutError):
        sdk.wait("job-1", timeout=1)
    assert calls == ["GET"]


def test_origin_guard_protects_credentials(sdk):
    calls = []
    mock_http(sdk, lambda r: calls.append(r) or httpx.Response(200))
    with pytest.raises(hf.ConfigurationError):
        sdk._client.get("https://evil.example/status")
    assert calls == []


def test_resume_uses_configured_host():
    sdk = hf.SafeClient(base_url="https://preview.higgsfield.ai", api_key="id:secret")
    try:
        controller = sdk.get_request_controller("job-1")
        assert controller.status_url == "https://preview.higgsfield.ai/requests/job-1/status"
        assert controller.cancel_url == "https://preview.higgsfield.ai/requests/job-1/cancel"
    finally:
        sdk.close()


def test_upload_does_not_send_api_key_to_storage(sdk, tmp_path):
    mock_http(sdk, lambda _: httpx.Response(200, json={"public_url": "https://cdn.example/a.png",
        "upload_url": "https://storage.example/a.png"}))
    uploads = []
    sdk.__dict__["_upload_client"] = httpx.Client(transport=httpx.MockTransport(
        lambda r: uploads.append(r) or httpx.Response(200)))
    path = tmp_path / "a.png"
    path.write_bytes(b"test-image")
    assert sdk.upload_file(path) == "https://cdn.example/a.png"
    assert "authorization" not in uploads[0].headers
    assert uploads[0].content == b"test-image"


def test_redacts_combined_and_separate_secrets():
    env = {"HIGGSFIELD_API_KEY": "key-123:secret-456"}
    text = hf.redact({"message": "key-123:secret-456 key-123 secret-456"}, env)
    assert "key-123" not in text and "secret-456" not in text


def test_errors_do_not_print_server_body(sdk):
    mock_http(sdk, lambda _: httpx.Response(401, json={"detail": "test-id:test-secret"}))
    with pytest.raises(Exception) as exc:
        sdk.submit("vendor/model", arguments={"prompt": "hello"})
    text = hf.error_message(exc.value, {})
    assert "401" in text and "test-secret" not in text


def test_dry_run_has_no_network_or_credentials(monkeypatch, capsys):
    monkeypatch.setattr(hf, "environment", lambda: {})
    monkeypatch.setattr(hf, "client", lambda *_: pytest.fail("Dry run created a client"))
    assert hf.main(["run", "new/model", "--prompt", "hi", "--dry-run"]) == 0
    assert json.loads(capsys.readouterr().out)["dry_run"] is True


def test_input_keeps_unicode_and_custom_fields(tmp_path):
    path = tmp_path / "input.json"
    path.write_text('\ufeff{"prompt":"雨", "arbitrary":[1,true]}', encoding="utf-8")
    assert hf.input_arguments(path) == {"prompt": "雨", "arbitrary": [1, True]}


def test_catalog_discovery_deduplicates():
    parser = hf.CatalogParser()
    parser.feed('<a href="/models/vendor/model/playground">M</a><a href="/models/vendor/model/api-reference">Docs</a>')
    assert parser.models == {"vendor/model"}
