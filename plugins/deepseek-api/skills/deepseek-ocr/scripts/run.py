#!/usr/bin/env python3
"""DeepSeek OCR script; Python 3.9 standard library only."""

import http.client
import io
import json
import os
import shutil
import signal
import stat
import sys
import tempfile
import urllib.error
import urllib.request
import uuid
from contextlib import contextmanager
from pathlib import Path

PLUGIN_ID = "deepseek-api"
API_URL = "https://api.deepseek.com/v1/tools/ocr"
REQUEST_TIMEOUT = 210
MAX_FILE_BYTES = 100 * 1024 * 1024


ARGUMENTS = {
    "ocr": {
        "file": {"type": "string", "required": True},
        "path": {"type": "string", "required": True},
        "purpose": {
            "type": "string",
            "choices": ["text_recognition", "rich_layout_recognition"],
            "default": "text_recognition",
        },
    }
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
        if not isinstance(value, str):
            raise ToolError("invalid_request", f"{name} must be string")
        if not value.strip():
            raise ToolError("invalid_request", f"{name} must not be blank")
        if "choices" in field and value not in field["choices"]:
            raise ToolError("invalid_request", f"Unsupported {name}")
    return result


def absolute_path(value, name):
    if "\x00" in value:
        raise ToolError("invalid_request", "File paths must not contain null bytes")
    try:
        path = Path(value).expanduser()
    except RuntimeError:
        raise ToolError("invalid_request", "Cannot expand file path") from None
    if not path.is_absolute():
        raise ToolError("invalid_request", f"{name} must be an absolute path")
    return path


def input_file(value):
    path = absolute_path(value, "file")
    try:
        file_stat = path.stat()
        if not stat.S_ISREG(file_stat.st_mode):
            raise ToolError(
                "file_error", "file must name a regular file", path=str(path)
            )
        if not 0 < file_stat.st_size <= MAX_FILE_BYTES:
            raise ToolError(
                "invalid_request",
                "file must be nonempty and at most 100 MiB",
                path=str(path),
            )
    except OSError:
        raise ToolError(
            "file_error", "Cannot read input file", path=str(path)
        ) from None
    return path


def prepare_output(path):
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryFile(dir=path.parent):
            pass
    except OSError:
        raise ToolError(
            "file_error", "Output directory is not writable", path=str(path.parent)
        ) from None


def publish_file(temporary, path):
    index = 0
    while True:
        target = path.with_name(f"{path.stem}-{index}{path.suffix}") if index else path
        index += 1
        try:
            os.link(temporary, target)
            return target
        except FileExistsError:
            continue


def save_json(result, path):
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", errors="backslashreplace", dir=path.parent
        ) as stream:
            json.dump(result, stream, ensure_ascii=False, indent=2)
            stream.flush()
            size = os.fstat(stream.fileno()).st_size
            target = publish_file(stream.name, path)
    except OSError:
        raise ToolError(
            "file_error", "Cannot save output JSON", path=str(path.parent)
        ) from None
    return {"path": str(target), "bytes": size}


@contextmanager
def multipart_body(path, purpose):
    boundary = uuid.uuid4().hex
    with path.open("rb") as source, tempfile.TemporaryFile() as body:
        # Format detection uses content, so the upload name can stay fixed.
        body.write(
            (
                f'--{boundary}\r\nContent-Disposition: form-data; name="purpose"\r\n\r\n{purpose}\r\n'
                f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="document"\r\n'
                "Content-Type: application/octet-stream\r\n\r\n"
            ).encode("ascii")
        )
        shutil.copyfileobj(source, body, length=65536)
        body.write(f"\r\n--{boundary}--\r\n".encode("ascii"))
        length = body.tell()
        body.seek(0)
        yield (
            body,
            {
                "Content-Type": f"multipart/form-data; boundary={boundary}",
                "Content-Length": str(length),
            },
        )


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def upstream_error(status, error=None):
    code, message = (
        "invalid_request",
        "Upstream rejected the request or could not process the file",
    )
    if status == 200:
        code, message = "ocr_failed", "OCR failed; check the file or try again later"
        if isinstance(error, dict) and error.get("code") == "gateway_timeout_error":
            message = "OCR timed out; use a smaller file"
    elif status == 401:
        code, message = "auth_failed", "Check the configured API key"
    elif status == 402:
        code, message = "billing_error", "Check the account balance"
    elif status == 408:
        code, message = "network_error", "File upload timed out"
    elif status == 413:
        message = "file must be at most 100 MiB"
    elif status == 429:
        code, message = "rate_limited", "Too many OCR requests are in progress"
    elif 500 <= status < 600:
        code, message = (
            "service_error",
            "OCR service failed; try later or use a smaller file",
        )
    return ToolError(code, message, http_status=status)


def post_ocr(key, data, headers):
    request = urllib.request.Request(
        API_URL,
        data=data,
        method="POST",
        headers={**headers, "Authorization": "Bearer " + key},
    )
    try:
        with urllib.request.build_opener(
            urllib.request.ProxyHandler({}), NoRedirect
        ).open(request, timeout=REQUEST_TIMEOUT) as res:
            result = json.load(res)
    except urllib.error.HTTPError as exc:
        status = exc.code
        exc.close()
        raise upstream_error(status) from None
    except (ValueError, RecursionError):
        raise ToolError("invalid_response", "Upstream returned invalid JSON") from None
    except (OSError, http.client.HTTPException):
        raise ToolError(
            "network_error",
            "Connection failed or timed out; OCR may still complete and be billed. Do not retry automatically.",
        ) from None
    if not isinstance(result, dict):
        raise ToolError("invalid_response", "Expected a JSON object from upstream")
    if "error" in result:
        raise upstream_error(200, result["error"])
    if not isinstance(result.get("pages"), list) or any(
        not isinstance(page, dict) or not isinstance(page.get("text"), str)
        for page in result["pages"]
    ):
        raise ToolError("invalid_response", "Expected OCR pages from upstream")
    return result


@request_deadline(
    REQUEST_TIMEOUT,
    "Request timed out; OCR may still complete and be billed. Do not retry automatically.",
)
def request_ocr(path, purpose, key):
    try:
        with multipart_body(path, purpose) as (data, headers):
            return post_ocr(key, data, headers)
    except OSError:
        raise ToolError(
            "file_error", "Cannot prepare file for upload", path=str(path)
        ) from None


def ocr(arguments):
    key = read_api_key()
    source = input_file(arguments["file"])
    path = absolute_path(arguments["path"], "path")
    if path.suffix.lower() != ".json":
        raise ToolError("invalid_request", "path must end in .json")
    prepare_output(path)
    response = request_ocr(source, arguments["purpose"], key)
    result = save_json(response, path)
    result["page_count"] = len(response["pages"])
    result["incomplete_page_count"] = sum(
        page.get("finish_reason") == "length" for page in response["pages"]
    )
    return result


HANDLERS = {"ocr": ocr}


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
