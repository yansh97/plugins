#!/usr/bin/env python3
"""DeepSeek Audio script; Python 3.9 standard library only."""

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
import wave
from contextlib import contextmanager
from pathlib import Path

PLUGIN_ID = "deepseek-api"
API_URL = "https://api.deepseek.com/v1/tools"
REQUEST_TIMEOUTS = {"asr": 660, "tts": 1800}
MAX_FILE_BYTES = 8 * 1024 * 1024
MAX_REQUEST_BYTES = 64 * 1024


ARGUMENTS = {
    "asr": {
        "file": {"type": "string", "required": True},
        "path": {"type": "string", "required": True},
    },
    "tts": {
        "text": {"type": "string", "required": True},
        "path": {"type": "string", "required": True},
        "voice_id": {
            "type": "string",
            "choices": ["stella", "tide"],
            "default": "stella",
        },
        "format": {"type": "string", "choices": ["wav", "pcm"]},
        "filter_markdown": {"type": "boolean", "default": True},
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
        valid_type = (kind == "string" and isinstance(value, str)) or (
            kind == "boolean" and type(value) is bool
        )
        if not valid_type:
            raise ToolError("invalid_request", f"{name} must be {kind}")
        if isinstance(value, str) and not value.strip():
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
                "file must be nonempty and at most 8 MiB",
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


@contextmanager
def multipart_body(path):
    boundary = uuid.uuid4().hex
    with path.open("rb") as source, tempfile.TemporaryFile() as body:
        # The service detects the audio format from its content.
        body.write(
            (
                f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="audio"\r\n'
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


def prepare_request(arguments):
    path = absolute_path(arguments["path"], "path")
    output_format = {".wav": "wav", ".pcm": "pcm"}.get(path.suffix.lower())
    if output_format is None:
        raise ToolError("invalid_request", "path must end in .wav or .pcm")
    if arguments.get("format", output_format) != output_format:
        raise ToolError("invalid_request", "format must match the path extension")
    payload = {
        name: arguments[name] for name in ("text", "voice_id", "filter_markdown")
    }
    payload["format"] = output_format
    prepare_output(path)
    return payload, path


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def upstream_error(operation, status):
    code, message = "invalid_request", "Upstream rejected the request or input"
    if status == 401:
        code, message = "auth_failed", "Check the configured API key"
    elif status == 402:
        code, message = "billing_error", "Check the account balance"
    elif operation == "asr" and status == 408:
        code, message = "network_error", "Audio upload timed out"
    elif operation == "asr" and status == 413:
        message = "file must be at most 8 MiB"
    elif status == 429:
        code, message = (
            "rate_limited",
            f"Too many {operation.upper()} requests are in progress",
        )
    elif 500 <= status < 600:
        code, message = "service_error", "Audio service failed"
    return ToolError(code, message, http_status=status)


@contextmanager
def open_response(operation, key, data, headers):
    request = urllib.request.Request(
        f"{API_URL}/{operation}",
        data=data,
        method="POST",
        headers={**headers, "Authorization": "Bearer " + key},
    )
    try:
        with urllib.request.build_opener(
            urllib.request.ProxyHandler({}), NoRedirect
        ).open(request, timeout=REQUEST_TIMEOUTS[operation]) as res:
            yield res
    except urllib.error.HTTPError as exc:
        status = exc.code
        exc.close()
        raise upstream_error(operation, status) from None
    except (OSError, http.client.HTTPException):
        raise ToolError(
            "network_error",
            "Connection failed, timed out or was cut off. Do not retry automatically.",
        ) from None


def post_asr(key, data, headers):
    try:
        with open_response("asr", key, data, headers) as res:
            result = json.load(res)
    except (ValueError, RecursionError):
        raise ToolError("invalid_response", "Upstream returned invalid JSON") from None
    if not isinstance(result, dict) or not isinstance(result.get("text"), str):
        raise ToolError("invalid_response", "Expected an ASR result from upstream")
    return result


@request_deadline(
    REQUEST_TIMEOUTS["asr"], "Request timed out. Do not retry automatically."
)
def request_asr(path, key):
    try:
        with multipart_body(path) as (data, headers):
            return post_asr(key, data, headers)
    except OSError:
        raise ToolError(
            "file_error", "Cannot prepare audio for upload", path=str(path)
        ) from None


def asr(arguments):
    key = read_api_key()
    source = input_file(arguments["file"])
    path = absolute_path(arguments["path"], "path")
    if path.suffix.lower() != ".json":
        raise ToolError("invalid_request", "path must end in .json")
    prepare_output(path)
    response = request_asr(source, key)
    result = save_json(response, path)
    result["characters"] = len(response["text"])
    duration = response.get("duration_ms")
    if type(duration) is int and duration >= 0:
        result["duration_ms"] = duration
    return result


@request_deadline(
    REQUEST_TIMEOUTS["tts"], "Request timed out. Do not retry automatically."
)
def request_audio(payload, key):
    try:
        data = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode(
            "utf-8"
        )
    except UnicodeError:
        raise ToolError(
            "invalid_request", "Request text must contain valid UTF-8 characters"
        ) from None
    if len(data) > MAX_REQUEST_BYTES:
        raise ToolError("invalid_request", "JSON request body must be at most 64 KiB")
    with open_response("tts", key, data, {"Content-Type": "application/json"}) as res:
        expected = "audio/wav" if payload["format"] == "wav" else "audio/pcm"
        if res.headers.get_content_type() != expected:
            raise ToolError("invalid_response", "Unexpected audio content type")
        # Reading without a byte limit detects truncated HTTP bodies.
        raw = res.read()
    if not raw:
        raise ToolError("invalid_response", "Upstream returned empty audio")
    if payload["format"] == "wav":
        if len(raw) < 12 or raw[:4] != b"RIFF" or raw[8:12] != b"WAVE":
            raise ToolError("invalid_response", "Expected WAV audio from upstream")
    elif len(raw) % 2:
        raise ToolError("invalid_response", "Incomplete 16-bit PCM audio")
    return raw


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


def audio_metadata(raw, output_format):
    if output_format == "pcm":
        return {
            "duration_ms": len(raw) * 1000 // (24000 * 2),
            "sample_rate": 24000,
            "channels": 1,
            "sample_width_bits": 16,
        }
    try:
        with io.BytesIO(raw) as stream, wave.open(stream, "rb") as audio:
            frame_bytes = audio.getnchannels() * audio.getsampwidth()
            # Streamed WAV headers may declare more frames than were received.
            frames = min(audio.getnframes(), (len(raw) - stream.tell()) // frame_bytes)
            if audio.getframerate() > 0:
                return {"duration_ms": frames * 1000 // audio.getframerate()}
    except (EOFError, wave.Error):
        pass
    return {}


def save_audio(raw, payload, path):
    metadata = audio_metadata(raw, payload["format"])
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent) as stream:
            stream.write(raw)
            stream.flush()
            target = publish_file(stream.name, path)
    except OSError:
        raise ToolError(
            "file_error", "Cannot save output audio", path=str(path.parent)
        ) from None
    return {
        "path": str(target),
        "format": payload["format"],
        "bytes": len(raw),
        "voice_id": payload["voice_id"],
        **metadata,
    }


def tts(arguments):
    key = read_api_key()
    payload, path = prepare_request(arguments)
    raw = request_audio(payload, key)
    return save_audio(raw, payload, path)


HANDLERS = {"asr": asr, "tts": tts}


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
