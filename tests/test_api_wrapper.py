"""Unit tests for scripts/api.py.

Coverage targets:
    * URL construction picks the right base host.
    * The X-API-Key header is set from the loaded env var.
    * The wrapper exits non-zero when no credentials are available.
    * The key is loaded only inside the wrapper process and never appears
      in stdout (the response body alone is printed).
"""

from __future__ import annotations

import json
from unittest import mock

import pytest

import api as api_wrapper


FAKE_KEY = "skn_live_FAKE_TEST_TOKEN_DO_NOT_USE_anywhere_real_1234567890"


@pytest.fixture(autouse=True)
def isolate_env(monkeypatch, tmp_path):
    """Clear API_KEY/APP_URL/API_URL/CLAUDE_PLUGIN_ROOT and chdir to tmp."""
    for var in ("API_KEY", "API_URL", "APP_URL", "CLAUDE_PLUGIN_ROOT"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path / "fake-home")
    yield


def test_load_credentials_reads_api_key_env(monkeypatch):
    monkeypatch.setenv("API_KEY", FAKE_KEY)
    key, api_url, app_url = api_wrapper.load_credentials()
    assert key == FAKE_KEY
    assert api_url == api_wrapper.DEFAULT_API_URL
    assert app_url == api_wrapper.DEFAULT_APP_URL


def test_load_credentials_exits_when_missing(capsys):
    with pytest.raises(SystemExit) as exc:
        api_wrapper.load_credentials()
    assert exc.value.code == 2
    err = capsys.readouterr().err
    assert "No API key found" in err
    assert "/skillenai:api setup" in err


def test_load_credentials_reads_dotenv_in_home(monkeypatch, tmp_path):
    home = tmp_path / "fake-home"
    skn = home / ".skillenai"
    skn.mkdir(parents=True)
    (skn / ".env").write_text(f"API_KEY={FAKE_KEY}\n")
    key, _api, _app = api_wrapper.load_credentials()
    assert key == FAKE_KEY


def test_build_url_api_host():
    url = api_wrapper.build_url(
        "https://api.example.test", "https://app.example.test",
        "api", "/v1/health",
    )
    assert url == "https://api.example.test/v1/health"


def test_build_url_app_host():
    url = api_wrapper.build_url(
        "https://api.example.test", "https://app.example.test",
        "app", "/alerts",
    )
    assert url == "https://app.example.test/alerts"


def test_build_url_adds_leading_slash():
    url = api_wrapper.build_url(
        "https://api.example.test", "https://app.example.test",
        "api", "v1/health",
    )
    assert url == "https://api.example.test/v1/health"


def test_load_body_inline_json():
    args = mock.Mock(body='{"q": "x"}', body_file=None)
    assert api_wrapper.load_body(args) == {"q": "x"}


def test_load_body_invalid_json_exits(capsys):
    args = mock.Mock(body="{not json}", body_file=None)
    with pytest.raises(SystemExit) as exc:
        api_wrapper.load_body(args)
    assert exc.value.code == 2
    assert "not valid JSON" in capsys.readouterr().err


def test_load_body_from_file(tmp_path):
    p = tmp_path / "body.json"
    p.write_text('{"q": "from-file"}')
    args = mock.Mock(body=None, body_file=p)
    assert api_wrapper.load_body(args) == {"q": "from-file"}


# --- main() integration: header set, response body printed verbatim ------

def test_main_get_request_sets_x_api_key_header(monkeypatch, capsys):
    monkeypatch.setenv("API_KEY", FAKE_KEY)
    monkeypatch.setenv("API_URL", "https://api.example.test")

    captured: dict[str, object] = {}

    def fake_request(method, url, headers=None, json=None, timeout=None):
        captured["method"] = method
        captured["url"] = url
        captured["headers"] = headers
        resp = mock.Mock()
        resp.text = '{"status": "ok"}'
        resp.status_code = 200
        return resp

    monkeypatch.setattr(api_wrapper.requests, "request", fake_request)
    rc = api_wrapper.main(["GET", "/v1/health"])
    assert rc == 0
    assert captured["method"] == "GET"
    assert captured["url"] == "https://api.example.test/v1/health"
    assert captured["headers"]["X-API-Key"] == FAKE_KEY
    out = capsys.readouterr().out
    assert '"status": "ok"' in out


def test_main_post_request_sets_content_type(monkeypatch, capsys):
    monkeypatch.setenv("API_KEY", FAKE_KEY)
    monkeypatch.setenv("API_URL", "https://api.example.test")

    captured: dict[str, object] = {}

    def fake_request(method, url, headers=None, json=None, timeout=None):
        captured["headers"] = headers
        captured["json"] = json
        resp = mock.Mock()
        resp.text = "[]"
        resp.status_code = 200
        return resp

    monkeypatch.setattr(api_wrapper.requests, "request", fake_request)
    rc = api_wrapper.main(["POST", "/v1/jobs/search", '{"query":"x"}'])
    assert rc == 0
    assert captured["headers"]["X-API-Key"] == FAKE_KEY
    assert captured["headers"]["Content-Type"] == "application/json"
    assert captured["json"] == {"query": "x"}


def test_main_propagates_non_2xx_with_body(monkeypatch, capsys):
    monkeypatch.setenv("API_KEY", FAKE_KEY)

    def fake_request(method, url, headers=None, json=None, timeout=None):
        resp = mock.Mock()
        resp.text = '{"detail": "Not found"}'
        resp.status_code = 404
        return resp

    monkeypatch.setattr(api_wrapper.requests, "request", fake_request)
    rc = api_wrapper.main(["GET", "/v1/missing"])
    assert rc == 1
    assert "Not found" in capsys.readouterr().out


def test_main_does_not_leak_key_to_stdout(monkeypatch, capsys):
    monkeypatch.setenv("API_KEY", FAKE_KEY)

    def fake_request(method, url, headers=None, json=None, timeout=None):
        resp = mock.Mock()
        resp.text = '{"ok": true}'
        resp.status_code = 200
        return resp

    monkeypatch.setattr(api_wrapper.requests, "request", fake_request)
    api_wrapper.main(["GET", "/v1/health"])
    captured = capsys.readouterr()
    # The wrapper's contract: only the response body goes to stdout.
    # The key must never appear in either stream.
    assert FAKE_KEY not in captured.out
    assert FAKE_KEY not in captured.err


def test_main_app_host_routes_to_app_url(monkeypatch):
    monkeypatch.setenv("API_KEY", FAKE_KEY)
    monkeypatch.setenv("APP_URL", "https://app.example.test")

    captured: dict[str, str] = {}

    def fake_request(method, url, headers=None, json=None, timeout=None):
        captured["url"] = url
        resp = mock.Mock()
        resp.text = "[]"
        resp.status_code = 200
        return resp

    monkeypatch.setattr(api_wrapper.requests, "request", fake_request)
    api_wrapper.main(["GET", "/alerts", "--host", "app"])
    assert captured["url"] == "https://app.example.test/alerts"
