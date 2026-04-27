"""Thin HTTP wrapper for the Skillenai API that keeps the key isolated.

The skill's bash flows call this instead of inlining
``curl -H "X-API-Key: $API_KEY"`` so the API key never enters the
agent-visible shell, the curl process argv, or any other place it
could be read or logged. The key is loaded into this script's process
only, used to sign one request, and dropped on exit.

Usage:
    api.py GET /v1/analytics/counts
    api.py POST /v1/jobs/search '{"query":"ML","size":20}'
    api.py POST /alerts '{"name":"…","source_query":{…}}' --host app
    api.py GET /v1/jobs/search?seniority=senior --query-file params.json

The response body is written verbatim to stdout (no key, no debug
headers). Non-2xx responses still print the body and exit non-zero so
the caller sees the error.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import requests
from dotenv import load_dotenv


DEFAULT_API_URL = "https://api.skillenai.com"
DEFAULT_APP_URL = "https://app.skillenai.com/api/backend"


def load_credentials() -> tuple[str, str, str]:
    """Resolve API key + base URLs.

    Precedence matches scripts/load_env.sh:
        1. existing API_KEY env var
        2. ~/.skillenai/.env
        3. $CLAUDE_PLUGIN_ROOT/.env
        4. ./.env
    """
    if not os.environ.get("API_KEY"):
        load_dotenv(Path.home() / ".skillenai" / ".env")
    if not os.environ.get("API_KEY"):
        plugin_root = os.environ.get("CLAUDE_PLUGIN_ROOT")
        if plugin_root:
            load_dotenv(Path(plugin_root) / ".env")
    if not os.environ.get("API_KEY"):
        load_dotenv()

    key = os.environ.get("API_KEY", "")
    if not key:
        sys.stderr.write(
            "No API key found. Run `/skillenai:api setup` to authorize.\n"
        )
        sys.exit(2)

    api_url = os.environ.get("API_URL", DEFAULT_API_URL).rstrip("/")
    app_url = os.environ.get("APP_URL", DEFAULT_APP_URL).rstrip("/")
    return key, api_url, app_url


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Send an authenticated request to the Skillenai API.",
    )
    parser.add_argument("method", help="HTTP method (GET, POST, PATCH, DELETE).")
    parser.add_argument("path", help="Request path (e.g. /v1/analytics/counts).")
    parser.add_argument(
        "body",
        nargs="?",
        default=None,
        help="JSON request body as a string (POST/PATCH).",
    )
    parser.add_argument(
        "--host",
        choices=("api", "app"),
        default="api",
        help="Which host to call (default: api).",
    )
    parser.add_argument(
        "--body-file",
        type=Path,
        help="Read JSON body from a file instead of inline.",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=60.0,
        help="Request timeout in seconds (default: 60).",
    )
    return parser.parse_args(argv)


def build_url(base_api: str, base_app: str, host: str, path: str) -> str:
    if not path.startswith("/"):
        path = "/" + path
    base = base_api if host == "api" else base_app
    return f"{base}{path}"


def load_body(args: argparse.Namespace) -> dict | None:
    if args.body_file is not None:
        with args.body_file.open("r", encoding="utf-8") as fh:
            return json.load(fh)
    if args.body is None:
        return None
    try:
        return json.loads(args.body)
    except json.JSONDecodeError as exc:
        sys.stderr.write(f"Body is not valid JSON: {exc.msg}\n")
        sys.exit(2)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    key, api_url, app_url = load_credentials()
    url = build_url(api_url, app_url, args.host, args.path)
    body = load_body(args)

    method = args.method.upper()
    if method not in {"GET", "POST", "PATCH", "PUT", "DELETE", "HEAD"}:
        sys.stderr.write(f"Unsupported method: {args.method}\n")
        return 2

    headers = {"X-API-Key": key}
    if body is not None:
        headers["Content-Type"] = "application/json"

    try:
        resp = requests.request(
            method, url, headers=headers, json=body, timeout=args.timeout,
        )
    except requests.RequestException as exc:
        # Sanitize: surface only the exception type, never the URL/headers
        # (URL is fine, but uniform handling keeps the message terse).
        sys.stderr.write(f"Request failed: {exc.__class__.__name__}\n")
        return 3

    text = resp.text
    if text:
        sys.stdout.write(text)
        if not text.endswith("\n"):
            sys.stdout.write("\n")

    if resp.status_code >= 400:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
