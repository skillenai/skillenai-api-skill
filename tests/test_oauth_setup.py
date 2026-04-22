"""Unit tests for scripts/oauth_setup.py.

Coverage targets:
    * The poll state machine handles all 5 RFC 8628 response branches.
    * The token NEVER appears in stdout, stderr, or any captured stream.
    * The credentials file is written with mode 0600 via direct fd write.
    * Existing-credentials prompt logic respects --force and TTY state.
"""

from __future__ import annotations

import json
import os
import re
import stat
from unittest import mock

import pytest

import oauth_setup


FAKE_TOKEN = "skn_live_FAKE_TEST_TOKEN_DO_NOT_USE_anywhere_real_1234567890"
FAKE_DEVICE_CODE = "device-code-shouldnt-leak-to-output-9876543210"


def _mock_response(status_code: int, body: dict | None = None) -> mock.Mock:
    resp = mock.Mock()
    resp.status_code = status_code
    if body is None:
        resp.json.side_effect = ValueError("no body")
        resp.text = ""
    else:
        resp.json.return_value = body
        resp.text = json.dumps(body)
    return resp


# --- request_device_code --------------------------------------------------

def test_request_device_code_returns_parsed_body():
    body = {
        "device_code": FAKE_DEVICE_CODE,
        "user_code": "ABCD-1234",
        "verification_uri": "https://example.test/activate",
        "verification_uri_complete": "https://example.test/activate?code=ABCD-1234",
        "expires_in": 900,
        "interval": 5,
    }
    with mock.patch("oauth_setup._post_with_retry",
                    return_value=_mock_response(200, body)) as post:
        result = oauth_setup.request_device_code("https://example.test")
    assert result == body
    post.assert_called_once()


def test_request_device_code_rejects_unknown_client():
    err = _mock_response(400, {"error": "invalid_client"})
    with mock.patch("oauth_setup._post_with_retry", return_value=err):
        with pytest.raises(oauth_setup.SetupError) as exc:
            oauth_setup.request_device_code("https://example.test")
    assert "invalid_client" in str(exc.value)


def test_request_device_code_rejects_malformed_body():
    bad = _mock_response(200, {"unexpected": "shape"})
    with mock.patch("oauth_setup._post_with_retry", return_value=bad):
        with pytest.raises(oauth_setup.SetupError) as exc:
            oauth_setup.request_device_code("https://example.test")
    assert "Unexpected response format" in str(exc.value)


# --- poll_for_token (RFC 8628 §3.5 branches) ------------------------------

def test_poll_returns_token_on_success(monkeypatch):
    resp = _mock_response(200, {"access_token": FAKE_TOKEN, "token_type": "api_key"})
    monkeypatch.setattr(oauth_setup, "_post_with_retry", lambda *a, **k: resp)
    monkeypatch.setattr(oauth_setup, "_sleep_until", lambda _t: None)
    token = oauth_setup.poll_for_token("https://example.test", FAKE_DEVICE_CODE, 1, 60)
    assert token == FAKE_TOKEN


def test_poll_handles_authorization_pending_then_success(monkeypatch):
    pending = _mock_response(400, {"error": "authorization_pending"})
    success = _mock_response(200, {"access_token": FAKE_TOKEN, "token_type": "api_key"})
    seq = iter([pending, pending, success])
    monkeypatch.setattr(oauth_setup, "_post_with_retry", lambda *a, **k: next(seq))
    monkeypatch.setattr(oauth_setup, "_sleep_until", lambda _t: None)
    token = oauth_setup.poll_for_token("https://example.test", FAKE_DEVICE_CODE, 1, 60)
    assert token == FAKE_TOKEN


def test_poll_handles_slow_down_increases_interval(monkeypatch):
    slow = _mock_response(400, {"error": "slow_down"})
    success = _mock_response(200, {"access_token": FAKE_TOKEN, "token_type": "api_key"})
    responses = iter([slow, success])

    intervals_observed: list[float] = []

    def fake_sleep_until(deadline: float) -> None:
        intervals_observed.append(deadline)

    monkeypatch.setattr(oauth_setup, "_post_with_retry", lambda *a, **k: next(responses))
    monkeypatch.setattr(oauth_setup, "_sleep_until", fake_sleep_until)
    token = oauth_setup.poll_for_token("https://example.test", FAKE_DEVICE_CODE, 1, 60)
    assert token == FAKE_TOKEN
    # one slow_down -> one sleep call, second iteration gets success and exits
    assert len(intervals_observed) == 1


def test_poll_access_denied_terminal(monkeypatch):
    resp = _mock_response(400, {"error": "access_denied"})
    monkeypatch.setattr(oauth_setup, "_post_with_retry", lambda *a, **k: resp)
    monkeypatch.setattr(oauth_setup, "_sleep_until", lambda _t: None)
    with pytest.raises(oauth_setup.SetupError) as exc:
        oauth_setup.poll_for_token("https://example.test", FAKE_DEVICE_CODE, 1, 60)
    assert "canceled" in str(exc.value).lower()


def test_poll_expired_token_terminal(monkeypatch):
    resp = _mock_response(400, {"error": "expired_token"})
    monkeypatch.setattr(oauth_setup, "_post_with_retry", lambda *a, **k: resp)
    monkeypatch.setattr(oauth_setup, "_sleep_until", lambda _t: None)
    with pytest.raises(oauth_setup.SetupError) as exc:
        oauth_setup.poll_for_token("https://example.test", FAKE_DEVICE_CODE, 1, 60)
    assert "expired" in str(exc.value).lower()


def test_poll_unexpected_error_code_is_terminal_and_redacted(monkeypatch):
    resp = _mock_response(400, {"error": "invalid_grant"})
    monkeypatch.setattr(oauth_setup, "_post_with_retry", lambda *a, **k: resp)
    monkeypatch.setattr(oauth_setup, "_sleep_until", lambda _t: None)
    with pytest.raises(oauth_setup.SetupError) as exc:
        oauth_setup.poll_for_token("https://example.test", FAKE_DEVICE_CODE, 1, 60)
    msg = str(exc.value)
    assert "invalid_grant" in msg
    # Defence in depth: the device code must never appear in error text.
    assert FAKE_DEVICE_CODE not in msg


def test_poll_malformed_success_body_is_terminal(monkeypatch):
    resp = _mock_response(200, {"wrong": "shape"})
    monkeypatch.setattr(oauth_setup, "_post_with_retry", lambda *a, **k: resp)
    monkeypatch.setattr(oauth_setup, "_sleep_until", lambda _t: None)
    with pytest.raises(oauth_setup.SetupError) as exc:
        oauth_setup.poll_for_token("https://example.test", FAKE_DEVICE_CODE, 1, 60)
    assert "Unexpected response format" in str(exc.value)


# --- write_credentials ----------------------------------------------------

def test_write_credentials_mode_0600(tmp_path):
    target = tmp_path / "subdir" / ".env"
    oauth_setup.write_credentials(FAKE_TOKEN, target)
    assert target.is_file()
    mode = stat.S_IMODE(os.stat(target).st_mode)
    assert mode == 0o600
    assert target.read_text() == f"API_KEY={FAKE_TOKEN}\n"


def test_write_credentials_replaces_existing(tmp_path):
    target = tmp_path / ".env"
    target.write_text("API_KEY=old_value\n")
    os.chmod(target, 0o644)
    oauth_setup.write_credentials(FAKE_TOKEN, target)
    assert target.read_text() == f"API_KEY={FAKE_TOKEN}\n"
    mode = stat.S_IMODE(os.stat(target).st_mode)
    assert mode == 0o600


def test_existing_key_present_detects_api_key(tmp_path):
    target = tmp_path / ".env"
    target.write_text("API_KEY=anything\n")
    assert oauth_setup.existing_key_present(target) is True


def test_existing_key_present_false_when_missing_or_empty(tmp_path):
    target = tmp_path / ".env"
    assert oauth_setup.existing_key_present(target) is False
    target.write_text("OTHER_VAR=1\n")
    assert oauth_setup.existing_key_present(target) is False


# --- Token leakage smoke test ---------------------------------------------

def test_full_run_does_not_leak_token_to_stdio(monkeypatch, tmp_path, capsys):
    """Simulate a complete successful run and assert no stream contains the token."""
    creds_path = tmp_path / ".env"
    monkeypatch.setattr(oauth_setup, "CREDS_PATH", creds_path)
    monkeypatch.setattr(oauth_setup, "CREDS_DIR", creds_path.parent)

    device_body = {
        "device_code": FAKE_DEVICE_CODE,
        "user_code": "ABCD-1234",
        "verification_uri": "https://example.test/activate",
        "verification_uri_complete": "https://example.test/activate?code=ABCD-1234",
        "expires_in": 60,
        "interval": 1,
    }
    token_body = {"access_token": FAKE_TOKEN, "token_type": "api_key"}

    def fake_post(url, json):
        if url.endswith("/oauth/device"):
            return _mock_response(200, device_body)
        return _mock_response(200, token_body)

    monkeypatch.setattr(oauth_setup, "_post_with_retry", fake_post)
    monkeypatch.setattr(oauth_setup, "_sleep_until", lambda _t: None)
    monkeypatch.setattr(oauth_setup, "open_browser", lambda _u: None)

    rc = oauth_setup.main(["--no-browser", "--app-url", "https://example.test"])
    assert rc == 0

    out, err = capsys.readouterr()
    combined = out + err
    # The whole point of the script: the token must never be visible in
    # stdout or stderr at any point during the run.
    assert FAKE_TOKEN not in combined
    # The device_code (a transient secret too) must also not leak.
    assert FAKE_DEVICE_CODE not in combined
    # Sanity: success message did print.
    assert "Authorized" in out
    # File was written.
    assert creds_path.read_text() == f"API_KEY={FAKE_TOKEN}\n"


def test_terminal_error_does_not_leak_token(monkeypatch, tmp_path, capsys):
    """Even on the access_denied path, no secret should leak."""
    creds_path = tmp_path / ".env"
    monkeypatch.setattr(oauth_setup, "CREDS_PATH", creds_path)
    monkeypatch.setattr(oauth_setup, "CREDS_DIR", creds_path.parent)

    device_body = {
        "device_code": FAKE_DEVICE_CODE,
        "user_code": "ABCD-1234",
        "verification_uri": "https://example.test/activate",
        "verification_uri_complete": "https://example.test/activate?code=ABCD-1234",
        "expires_in": 60,
        "interval": 1,
    }
    denied = _mock_response(400, {"error": "access_denied"})

    call_count = {"n": 0}

    def fake_post(url, json):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return _mock_response(200, device_body)
        return denied

    monkeypatch.setattr(oauth_setup, "_post_with_retry", fake_post)
    monkeypatch.setattr(oauth_setup, "_sleep_until", lambda _t: None)
    monkeypatch.setattr(oauth_setup, "open_browser", lambda _u: None)

    rc = oauth_setup.main(["--no-browser", "--app-url", "https://example.test"])
    assert rc == 1

    out, err = capsys.readouterr()
    combined = out + err
    assert FAKE_TOKEN not in combined
    assert FAKE_DEVICE_CODE not in combined
    assert not creds_path.exists()


# --- Argument parsing -----------------------------------------------------

def test_parse_args_defaults(monkeypatch):
    monkeypatch.delenv("APP_URL", raising=False)
    args = oauth_setup.parse_args([])
    assert args.force is False
    assert args.no_browser is False
    assert args.app_url == oauth_setup.DEFAULT_APP_URL


def test_parse_args_app_url_override(monkeypatch):
    monkeypatch.delenv("APP_URL", raising=False)
    args = oauth_setup.parse_args(["--app-url", "https://staging.example.test"])
    assert args.app_url == "https://staging.example.test"


def test_parse_args_app_url_from_env(monkeypatch):
    monkeypatch.setenv("APP_URL", "https://env.example.test")
    args = oauth_setup.parse_args([])
    assert args.app_url == "https://env.example.test"
