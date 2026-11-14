#!/usr/bin/env python3
"""DeepSeek Web script; Python 3.9 standard library only."""

import http.client
import io
import json
import signal
import sys
import urllib.error
import urllib.parse
import urllib.request
from contextlib import contextmanager
from pathlib import Path

PLUGIN_ID = "deepseek-api"
API_BASE_URL = "https://api.deepseek.com"
REQUEST_TIMEOUT = 45


ARGUMENTS = {
    "search": {
        "query": {"type": "string", "required": True, "length": [1, 128]},
        "count": {"type": "integer", "range": [1, 50], "default": 10},
        "need_content": {"type": "boolean", "default": False},
        "ori_question": {"type": "string", "length": [1, 128]},
    },
    "fetch": {
        "url": {"type": "string", "required": True, "length": [1, 8192]},
    },
}


class ToolError(Exception):
    def __init__(self, code, message, **details):
        super().__init__(message)
        self.details = {"code": code, "message": message, **details}


@contextmanager
def request_deadline(timeout, message):
    """Bound request preparation and network waiting on the main thread."""

    def expire(signum, frame):
        raise ToolError("network_error", message)

    previous_handler = signal.signal(signal.SIGALRM, expire)
    try:
        signal.setitimer(signal.ITIMER_REAL, timeout)
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous_handler)


def read_api_key():
    path = Path.home() / ".config/agents/config.json"
    details = {"path": str(path)}
    try:
        config = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, RecursionError):
        raise ToolError(
            "config_error", "Cannot read configuration; expected UTF-8 JSON", **details
        ) from None
    section = config.get(PLUGIN_ID) if isinstance(config, dict) else None
    if not isinstance(section, dict):
        raise ToolError(
            "config_error", f"Expected an object for {PLUGIN_ID}", **details
        )
    key = section.get("api_key")
    key = key.strip() if isinstance(key, str) else ""
    if not key:
        raise ToolError(
            "config_error", f"{PLUGIN_ID}.api_key must be a nonempty string", **details
        )
    if any(not 33 <= ord(char) <= 126 for char in key):
        raise ToolError("config_error", f"Invalid {PLUGIN_ID}.api_key", **details)
    return key


def validate_arguments(arguments, fields):
    """Validate flat arguments and apply defaults."""
    if not isinstance(arguments, dict):
        raise ToolError("invalid_request", "arguments must be an object")
    if set(arguments) - fields.keys():
        raise ToolError("invalid_request", "Unknown argument")
    result = dict(arguments)
    for name, field in fields.items():
        if name not in result:
            if field.get("required"):
                raise ToolError("invalid_request", f"Missing required argument: {name}")
            if "default" not in field:
                continue
            result[name] = field["default"]
        value = result[name]
        kind = field["type"]
        valid_type = (
            (kind == "string" and isinstance(value, str))
            or (kind == "integer" and type(value) is int)
            or (kind == "boolean" and type(value) is bool)
        )
        if not valid_type:
            raise ToolError("invalid_request", f"{name} must be {kind}")
        if isinstance(value, str):
            low, high = field["length"]
            if not value.strip() or not low <= len(value) <= high:
                raise ToolError("invalid_request", f"{name} has an invalid length")
        elif type(value) is int:
            low, high = field["range"]
            if not low <= value <= high:
                raise ToolError(
                    "invalid_request", f"{name} is outside its allowed range"
                )
    return result


def validate_url(value):
    try:
        parsed = urllib.parse.urlsplit(value)
        valid = (
            parsed.scheme in ("http", "https")
            and parsed.hostname
            and not parsed.username
            and not parsed.password
        )
        _ = parsed.port
    except ValueError:
        valid = False
    if not valid or any(ord(char) <= 32 for char in value):
        raise ToolError(
            "invalid_request", "URL must be HTTP(S), without username or password"
        )


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def upstream_error(status):
    code, message = "invalid_request", "Upstream rejected the request"
    if status == 401:
        code, message = "auth_failed", "Check the configured API key"
    elif status == 402:
        code, message = (
            "billing_error",
            "Check the account balance",
        )
    elif status == 403:
        code, message = (
            "fetch_failed",
            "Fetch was rejected by security policy; use another source",
        )
    elif status == 404:
        code, message = (
            "fetch_failed",
            "Fetch returned no usable page content; use another source",
        )
    elif status == 408:
        code, message = "network_error", "Upstream request timed out"
    elif status == 429:
        code, message = "rate_limited", "Upstream rate limit reached"
    elif 500 <= status < 600:
        code, message = "service_error", "Upstream service failed"
    return ToolError(code, message, http_status=status)


@request_deadline(REQUEST_TIMEOUT, "Request timed out")
def post_json(path, payload):
    key = read_api_key()
    try:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    except UnicodeError:
        raise ToolError(
            "invalid_request", "Request text must contain valid UTF-8 characters"
        ) from None
    request = urllib.request.Request(
        API_BASE_URL + path,
        data=data,
        method="POST",
        headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"},
    )
    try:
        with urllib.request.build_opener(
            urllib.request.ProxyHandler({}), NoRedirect
        ).open(request, timeout=REQUEST_TIMEOUT) as res:
            result = json.load(res)
        if not isinstance(result, dict):
            raise ToolError("invalid_response", "Expected a JSON object from upstream")
        return result
    except urllib.error.HTTPError as exc:
        status = exc.code
        exc.close()
        raise upstream_error(status) from None
    except (ValueError, RecursionError):
        raise ToolError("invalid_response", "Upstream returned invalid JSON") from None
    except (OSError, http.client.HTTPException):
        raise ToolError("network_error", "Connection failed or timed out") from None


def search(arguments):
    return post_json("/v1/tools/search", arguments)


def fetch(arguments):
    validate_url(arguments["url"])
    return post_json("/v1/tools/fetch", arguments)


HANDLERS = {"search": search, "fetch": fetch}


def read_arguments():
    """Read one JSON object from standard input."""
    try:
        return json.load(sys.stdin)
    except (OSError, ValueError, RecursionError):
        raise ToolError("invalid_request", "Expected UTF-8 JSON on stdin") from None


def main():
    if sys.platform != "darwin":
        print("This plugin supports macOS only", file=sys.stderr, flush=True)
        return 1
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        if isinstance(stream, io.TextIOWrapper):
            stream.reconfigure(encoding="utf-8")
    try:
        if ARGUMENTS.keys() != HANDLERS.keys():
            raise ValueError("Operation arguments and handlers do not match")
        if sys.argv[1:] == ["--help"]:
            result = {"operations": ARGUMENTS}
        else:
            if len(sys.argv) != 2 or sys.argv[1] not in HANDLERS:
                raise ToolError(
                    "invalid_request",
                    "Expected one operation; use --help to list operations",
                )
            name = sys.argv[1]
            arguments = validate_arguments(read_arguments(), ARGUMENTS[name])
            result = HANDLERS[name](arguments)
        status = 0
    except ToolError as exc:
        result, status = exc.details, 1
    except Exception as exc:  # noqa: BLE001
        print(f"Script failure: {type(exc).__name__}", file=sys.stderr, flush=True)
        result = {"code": "internal_error", "message": "Plugin failed unexpectedly"}
        status = 1
    print(json.dumps(result), flush=True)
    return status


if __name__ == "__main__":
    raise SystemExit(main())
