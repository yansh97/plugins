#!/usr/bin/env python3
"""DeepSeek Search MCP server; Python 3.9 standard library only."""

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

SERVER_INFO = {"name": "deepseek-search", "version": "0.1.2"}
API_BASE_URL = "https://api.deepseek.com"
PROTOCOL_VERSIONS = ("2024-11-05", "2025-03-26", "2025-06-18")
REQUEST_TIMEOUT = 45
MAX_RESPONSE_BYTES = 16 * 1024 * 1024


TOOLS = [
    {
        "name": "web_search",
        "description": "搜索网页，返回链接、摘要及相关性等信息。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "搜索词，支持 site:、lang:、after: 等操作符。",
                    "minLength": 1,
                    "maxLength": 128,
                },
                "count": {
                    "type": "integer",
                    "description": "返回条数。",
                    "minimum": 1,
                    "maximum": 50,
                    "default": 10,
                },
                "need_content": {
                    "type": "boolean",
                    "description": "是否获取每条结果的网页正文。",
                    "default": False,
                },
                "ori_question": {
                    "type": "string",
                    "description": "用户原始问题，用于理解搜索意图。",
                    "minLength": 1,
                    "maxLength": 128,
                },
            },
            "required": ["query"],
            "additionalProperties": False,
        },
    },
    {
        "name": "web_fetch",
        "description": "抓取单个网页，返回标题和 Markdown 正文。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "HTTP(S) 绝对地址，不得携带凭据。",
                    "minLength": 1,
                    "maxLength": 8192,
                }
            },
            "required": ["url"],
            "additionalProperties": False,
        },
    },
]


TOOLS_BY_NAME = {tool["name"]: tool for tool in TOOLS}


class ToolError(Exception):
    def __init__(self, code, message, **details):
        super().__init__(message)
        self.details = {"code": code, "message": message, **details}


@contextmanager
def request_deadline(message):
    """Bound a request on the service's main thread on macOS."""

    def expire(signum, frame):
        raise ToolError("network_error", message)

    previous_handler = signal.signal(signal.SIGALRM, expire)
    try:
        signal.setitimer(signal.ITIMER_REAL, REQUEST_TIMEOUT)
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous_handler)


def read_api_key():
    path = Path.home() / ".config/agents/config.json"
    details = {"path": str(path)}
    try:
        config = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise ToolError(
            "config_error", "Cannot read configuration; expected UTF-8 JSON", **details
        ) from None
    name = SERVER_INFO["name"]
    section = config.get(name) if isinstance(config, dict) else None
    if not isinstance(section, dict):
        raise ToolError("config_error", f"Expected an object for {name}", **details)
    key = section.get("api_key")
    if not isinstance(key, str) or not key.strip():
        raise ToolError(
            "config_error", f"{name}.api_key must be a nonempty string", **details
        )
    key = key.strip()
    if any(not 33 <= ord(char) <= 126 for char in key):
        raise ToolError("config_error", f"Invalid {name}.api_key", **details)
    return key


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def validate_arguments(arguments, schema):
    """Validate arguments and apply schema defaults."""
    if not isinstance(arguments, dict):
        raise ToolError("invalid_request", "arguments must be an object")
    properties = schema["properties"]
    if set(arguments) - properties.keys():
        raise ToolError("invalid_request", "Unknown argument")
    for name in schema["required"]:
        if name not in arguments:
            raise ToolError("invalid_request", f"Missing required argument: {name}")
    result = dict(arguments)
    for name, field in properties.items():
        if name not in result and "default" in field:
            result[name] = field["default"]
        if name not in result:
            continue
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
            if (
                not value.strip()
                or not field["minLength"] <= len(value) <= field["maxLength"]
            ):
                raise ToolError("invalid_request", f"{name} has an invalid length")
        elif type(value) is int and not field["minimum"] <= value <= field["maximum"]:
            raise ToolError("invalid_request", f"{name} is outside its allowed range")
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
        raise ToolError("invalid_request", "URL must be HTTP(S), without credentials")
    if parsed.query:
        names = urllib.parse.parse_qs(parsed.query)
        credentials = {
            "key",
            "api_key",
            "apikey",
            "token",
            "access_token",
            "secret",
            "password",
            "authorization",
            "signature",
            "x-amz-signature",
            "x-goog-signature",
        }
        if any(name.lower() in credentials for name in names):
            raise ToolError("invalid_request", "URL must not contain credentials")


def upstream_error(exc):
    status = exc.code
    exc.close()
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


@request_deadline("Request timed out")
def api(path, payload):
    key = read_api_key()
    request = urllib.request.Request(
        API_BASE_URL + path,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        method="POST",
        headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"},
    )
    try:
        with urllib.request.build_opener(
            urllib.request.ProxyHandler({}), NoRedirect
        ).open(request, timeout=REQUEST_TIMEOUT) as res:
            body = res.read(MAX_RESPONSE_BYTES + 1)
        if len(body) > MAX_RESPONSE_BYTES:
            raise ToolError("invalid_response", "Upstream response is too large")
        text = body.decode("utf-8")
        result = json.loads(text)
        if not isinstance(result, dict):
            raise ToolError("invalid_response", "Expected a JSON object from upstream")
        if result.get("error"):
            raise ToolError("invalid_response", "Upstream returned an error as success")
        return text
    except urllib.error.HTTPError as exc:
        raise upstream_error(exc) from None
    except (ValueError, RecursionError):
        raise ToolError("invalid_response", "Upstream returned invalid JSON") from None
    except (OSError, http.client.HTTPException):
        raise ToolError("network_error", "Connection failed or timed out") from None


def web_search(arguments):
    return api("/v1/tools/search", arguments)


def web_fetch(arguments):
    validate_url(arguments["url"])
    return api("/v1/tools/fetch", arguments)


HANDLERS = {"web_search": web_search, "web_fetch": web_fetch}


def tool_result(value, is_error=False):
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    result = {"content": [{"type": "text", "text": text}]}
    if is_error:
        result["isError"] = True
    return result


def call_tool(params):
    name = params.get("name")
    if not isinstance(name, str) or name not in HANDLERS:
        return tool_result({"code": "invalid_request", "message": "Unknown tool"}, True)
    try:
        schema = TOOLS_BY_NAME[name]["inputSchema"]
        arguments = validate_arguments(params.get("arguments", {}), schema)
        return tool_result(HANDLERS[name](arguments))
    except ToolError as exc:
        return tool_result(exc.details, True)
    except Exception as exc:  # noqa: BLE001
        print(f"Tool failure: {type(exc).__name__}", file=sys.stderr, flush=True)
        return tool_result(
            {"code": "internal_error", "message": "Plugin failed unexpectedly"}, True
        )


def rpc_error(rid, code, message):
    return {"jsonrpc": "2.0", "id": rid, "error": {"code": code, "message": message}}


def handle(request):
    if not isinstance(request, dict):
        return rpc_error(None, -32600, "Invalid request")
    rid = request.get("id")
    if (
        request.get("jsonrpc") != "2.0"
        or not isinstance(request.get("method"), str)
        or ("id" in request and (type(rid) not in (str, int)))
    ):
        return rpc_error(None, -32600, "Invalid request")
    if "id" not in request:
        return None
    params = request.get("params", {})
    if not isinstance(params, dict):
        return rpc_error(rid, -32602, "params must be an object")
    method = request["method"]
    if method == "initialize":
        protocol = params.get("protocolVersion")
        if protocol not in PROTOCOL_VERSIONS:
            protocol = PROTOCOL_VERSIONS[-1]
        result = {
            "protocolVersion": protocol,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": SERVER_INFO,
        }
    elif method == "tools/list":
        result = {"tools": TOOLS}
    elif method == "tools/call":
        result = call_tool(params)
    elif method == "ping":
        result = {}
    elif method == "resources/list":
        result = {"resources": []}
    elif method == "prompts/list":
        result = {"prompts": []}
    else:
        return rpc_error(rid, -32601, "Method not found")
    return {"jsonrpc": "2.0", "id": rid, "result": result}


def serve():
    for line in sys.stdin:
        if not line.strip():
            continue
        try:
            response = handle(json.loads(line))
        except ValueError:
            response = rpc_error(None, -32700, "Parse error")
        except Exception as exc:  # noqa: BLE001
            print(
                f"Protocol failure: {type(exc).__name__}", file=sys.stderr, flush=True
            )
            response = rpc_error(None, -32603, "Internal error")
        if response is not None:
            print(json.dumps(response, ensure_ascii=False), flush=True)


def main():
    if sys.platform != "darwin":
        print("This plugin supports macOS only", file=sys.stderr, flush=True)
        raise SystemExit(1)
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        if isinstance(stream, io.TextIOWrapper):
            stream.reconfigure(encoding="utf-8")
    if len(TOOLS_BY_NAME) != len(TOOLS) or TOOLS_BY_NAME.keys() != HANDLERS.keys():
        raise ValueError("Tool definitions and handlers do not match")
    serve()


if __name__ == "__main__":
    main()
