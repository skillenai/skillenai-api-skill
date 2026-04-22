"""OAuth 2.0 Device Authorization Grant client (RFC 8628).

Drives the browser-based authorization flow for the Skillenai API:

    1. POST /oauth/device     - request a device_code + user_code
    2. open the browser to /activate?code=USER-CODE
    3. poll POST /oauth/token until the user clicks Allow
    4. write the issued API key to ~/.skillenai/.env (mode 0600)

Security posture:
    The access_token returned by the token endpoint is a long-lived API
    key. It MUST NOT enter stdout, stderr, logs, exception messages,
    subprocess argv, or any chat transcript at any point. The success
    confirmation prints only a fixed string. The file write goes through
    a direct file descriptor with mode 0600 so the key never passes
    through print() or a shell heredoc.

Usage:
    python oauth_setup.py
    python oauth_setup.py --force      # overwrite existing credentials
    python oauth_setup.py --app-url https://app.skillenai.com
"""

from __future__ import annotations

import argparse
import os
import platform
import shutil
import subprocess
import sys
import time
from pathlib import Path

import requests


CLIENT_ID = "skillenai-api-skill"
DEVICE_GRANT_TYPE = "urn:ietf:params:oauth:grant-type:device_code"
DEFAULT_APP_URL = "https://app.skillenai.com"
CREDS_DIR = Path.home() / ".skillenai"
CREDS_PATH = CREDS_DIR / ".env"

# Network retry budget for transient errors on either endpoint. The
# device flow itself has a 15-min ceiling; this is the inner budget for
# a single HTTP attempt.
NETWORK_RETRY_BUDGET_SECONDS = 60
NETWORK_RETRY_INITIAL_BACKOFF = 1.0


class SetupError(Exception):
    """Raised on a non-recoverable setup failure. Message is safe to print."""


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Authorize this machine for the Skillenai API via OAuth.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite existing ~/.skillenai/.env without prompting.",
    )
    parser.add_argument(
        "--app-url",
        default=os.environ.get("APP_URL", DEFAULT_APP_URL),
        help="Override the app base URL (default: %(default)s).",
    )
    parser.add_argument(
        "--no-browser",
        action="store_true",
        help="Print the verification URL but do not try to open a browser.",
    )
    return parser.parse_args(argv)


def existing_key_present(path: Path | None = None) -> bool:
    if path is None:
        path = CREDS_PATH
    if not path.is_file():
        return False
    try:
        with path.open("r", encoding="utf-8") as fh:
            for line in fh:
                if line.strip().startswith("API_KEY="):
                    return True
    except OSError:
        return False
    return False


def confirm_overwrite() -> bool:
    if not sys.stdin.isatty():
        # Non-interactive runs without --force should fail closed rather
        # than silently overwrite a key the user may still want.
        return False
    answer = input(
        f"Credentials already exist at {CREDS_PATH}. Overwrite? [y/N] "
    ).strip().lower()
    return answer in ("y", "yes")


def open_browser(url: str) -> None:
    """Best-effort browser launch. Failure is non-fatal; URL is on stdout."""
    system = platform.system()
    if system == "Darwin":
        cmd = ["open", url]
    elif system == "Windows":
        # Avoid shell=True with a URL we control but still want to keep
        # tidy; "start" is a cmd builtin so we route through cmd /c.
        cmd = ["cmd", "/c", "start", "", url]
    else:
        cmd = ["xdg-open", url]

    if shutil.which(cmd[0]) is None:
        return

    try:
        subprocess.run(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return


def request_device_code(app_url: str) -> dict:
    url = f"{app_url.rstrip('/')}/oauth/device"
    try:
        resp = _post_with_retry(url, json={"client_id": CLIENT_ID})
    except requests.RequestException as exc:
        raise SetupError(f"Could not reach {url}: {exc.__class__.__name__}")

    if resp.status_code != 200:
        # Device endpoint errors carry an "error" code per RFC 8628 §3.2
        # (deferring to RFC 6749 §5.2). Surface the code, never the body.
        error_code = _safe_error_code(resp)
        raise SetupError(f"Device authorization failed: {error_code}")

    try:
        body = resp.json()
    except ValueError:
        raise SetupError("Unexpected response format from device endpoint")

    required = ("device_code", "user_code", "verification_uri",
                "verification_uri_complete", "expires_in", "interval")
    if not all(k in body for k in required):
        raise SetupError("Unexpected response format from device endpoint")
    return body


def poll_for_token(app_url: str, device_code: str, interval: int,
                   expires_in: int) -> str:
    """Poll /oauth/token until success or terminal failure. Returns the raw token."""
    url = f"{app_url.rstrip('/')}/oauth/token"
    deadline = time.monotonic() + max(expires_in, 60)
    current_interval = max(interval, 1)
    payload = {
        "grant_type": DEVICE_GRANT_TYPE,
        "device_code": device_code,
        "client_id": CLIENT_ID,
    }

    while True:
        if time.monotonic() >= deadline:
            raise SetupError("Authorization code expired. Run setup again.")

        try:
            resp = _post_with_retry(url, json=payload)
        except requests.RequestException as exc:
            raise SetupError(f"Could not reach {url}: {exc.__class__.__name__}")

        if resp.status_code == 200:
            try:
                body = resp.json()
            except ValueError:
                raise SetupError("Unexpected response format from token endpoint")
            token = body.get("access_token")
            if not isinstance(token, str) or not token:
                raise SetupError("Unexpected response format from token endpoint")
            return token

        error_code = _safe_error_code(resp)
        if error_code == "authorization_pending":
            _sleep_until(min(deadline, time.monotonic() + current_interval))
            continue
        if error_code == "slow_down":
            current_interval += 5
            _sleep_until(min(deadline, time.monotonic() + current_interval))
            continue
        if error_code == "access_denied":
            raise SetupError("Authorization canceled.")
        if error_code == "expired_token":
            raise SetupError("Authorization code expired. Run setup again.")
        # invalid_grant, invalid_client, unsupported_grant_type, etc. —
        # all terminal. Surface only the code.
        raise SetupError(f"Authorization failed: {error_code}")


def write_credentials(token: str, path: Path | None = None) -> None:
    """Write API_KEY=<token> to `path` with mode 0600 via direct fd write.

    Routing through print() / logging / shell heredoc / subprocess argv
    would leak the token into stdout, transcripts, or `ps` output. The
    only acceptable path is a direct file-descriptor write.
    """
    if path is None:
        path = CREDS_PATH
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(
        str(path),
        os.O_WRONLY | os.O_CREAT | os.O_TRUNC,
        0o600,
    )
    try:
        os.write(fd, b"API_KEY=" + token.encode("utf-8") + b"\n")
    finally:
        os.close(fd)
    # Belt-and-suspenders: ensure mode is 0600 even if a prior file with
    # a permissive mode was truncated rather than replaced.
    os.chmod(path, 0o600)


def confirm_credentials(path: Path | None = None) -> bool:
    """Confirm the file contains an API_KEY line without revealing it."""
    if path is None:
        path = CREDS_PATH
    if not path.is_file():
        return False
    try:
        with path.open("r", encoding="utf-8") as fh:
            return any(line.startswith("API_KEY=") for line in fh)
    except OSError:
        return False


def run(args: argparse.Namespace) -> int:
    if existing_key_present() and not args.force:
        if not confirm_overwrite():
            print("Setup canceled. Existing credentials left in place.")
            return 0

    device = request_device_code(args.app_url)

    user_code = device["user_code"]
    verification_uri = device["verification_uri"]
    verification_uri_complete = device["verification_uri_complete"]

    print(f"Visit {verification_uri} and enter code: {user_code}")
    print(f"Or open this URL directly: {verification_uri_complete}")
    if not args.no_browser:
        print("Opening browser...")
        open_browser(verification_uri_complete)

    token = poll_for_token(
        args.app_url,
        device["device_code"],
        int(device["interval"]),
        int(device["expires_in"]),
    )

    write_credentials(token)
    # Drop the in-memory reference promptly — defence in depth against
    # the token surfacing in a later traceback frame variable.
    del token
    del device

    if not confirm_credentials():
        raise SetupError(
            f"Wrote credentials but {CREDS_PATH} did not contain API_KEY. "
            "Check filesystem permissions and try again."
        )

    print(f"✓ Authorized. Key saved to {CREDS_PATH}")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        return run(args)
    except KeyboardInterrupt:
        print("\nSetup canceled.", file=sys.stderr)
        return 130
    except SetupError as exc:
        print(str(exc), file=sys.stderr)
        return 1


def _safe_error_code(resp: requests.Response) -> str:
    """Pull the 'error' code from an RFC 8628 error body, never the body itself."""
    try:
        body = resp.json()
    except ValueError:
        return f"http_{resp.status_code}"
    code = body.get("error") if isinstance(body, dict) else None
    if isinstance(code, str) and code:
        return code
    return f"http_{resp.status_code}"


def _post_with_retry(url: str, *, json: dict) -> requests.Response:
    """POST with exponential backoff on connection-level errors only.

    HTTP responses (including 4xx/5xx) are returned directly; the caller
    decides which codes to retry. Only socket-level failures and
    timeouts trigger a retry, since those are the cases where a quick
    retry might actually succeed without the server having seen the
    request.
    """
    deadline = time.monotonic() + NETWORK_RETRY_BUDGET_SECONDS
    backoff = NETWORK_RETRY_INITIAL_BACKOFF
    last_exc: requests.RequestException | None = None
    while True:
        try:
            return requests.post(url, json=json, timeout=15)
        except (requests.ConnectionError, requests.Timeout) as exc:
            last_exc = exc
            if time.monotonic() + backoff >= deadline:
                raise
            time.sleep(backoff)
            backoff = min(backoff * 2, 10.0)
    # Unreachable; satisfy type checkers.
    raise last_exc if last_exc else requests.RequestException("retry loop exited")  # pragma: no cover


def _sleep_until(deadline: float) -> None:
    remaining = deadline - time.monotonic()
    if remaining > 0:
        time.sleep(remaining)


if __name__ == "__main__":
    sys.exit(main())
