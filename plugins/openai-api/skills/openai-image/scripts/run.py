#!/usr/bin/env python3
"""OpenAI Image script; Python 3.9 standard library only."""

import base64
import http.client
import io
import json
import math
import os
import re
import shutil
import signal
import stat
import struct
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from functools import partial
from pathlib import Path

PLUGIN_ID = "openai-api"
FLARE = "gpt-image-2.6-flare"
SUNBURST = "gpt-image-2.6-sunburst"
SIZE_PATTERN = r"([1-9][0-9]{0,3})x([1-9][0-9]{0,3})"
MIN_PIXELS, MAX_PIXELS, MAX_EDGE = 655360, 8294400, 3840
MAX_IMAGE_BYTES = 50 * 1024 * 1024
REQUEST_TIMEOUT = 600


IMAGE_ARGUMENTS = {
    "prompt": {"type": "string", "required": True, "length": [1, 32000]},
    "path": {"type": "string", "required": True},
    "model": {
        "type": "string",
        "choices": [SUNBURST, FLARE],
        "default": FLARE,
    },
    "size": {"type": "string", "pattern": SIZE_PATTERN},
    "quality": {
        "type": "string",
        "choices": ["low", "medium", "high", "xhigh", "max"],
        "default": "high",
    },
    "complexity": {
        "type": "string",
        "choices": ["low", "medium", "high"],
        "default": "high",
    },
    "n": {"type": "integer", "range": [1, 10], "default": 1},
    "output_format": {"type": "string", "choices": ["png", "jpeg"]},
    "background": {
        "type": "string",
        "choices": ["opaque", "transparent"],
        "default": "opaque",
    },
    "output_compression": {"type": "integer", "range": [0, 100]},
}

ARGUMENTS = {
    "generate": IMAGE_ARGUMENTS,
    "edit": {
        **IMAGE_ARGUMENTS,
        "model": {**IMAGE_ARGUMENTS["model"], "default": SUNBURST},
        "image": {
            "type": "string_list",
            "required": True,
            "length": [1, 16],
        },
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


def read_config():
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
    result = {}
    for field in ("api_key", "base_url"):
        value = section.get(field)
        value = value.strip() if isinstance(value, str) else ""
        if not value:
            raise ToolError(
                "config_error",
                f"{PLUGIN_ID}.{field} must be a nonempty string",
                **details,
            )
        result[field] = value
    key = result["api_key"]
    if any(not 33 <= ord(char) <= 126 for char in key):
        raise ToolError("config_error", f"Invalid {PLUGIN_ID}.api_key", **details)
    base = result["base_url"].rstrip("/")
    try:
        parsed = urllib.parse.urlsplit(base)
        valid = (
            parsed.scheme in ("http", "https")
            and parsed.hostname
            and not parsed.username
            and not parsed.password
            and not parsed.query
            and not parsed.fragment
        )
        _ = parsed.port
    except ValueError:
        valid = False
    if not valid or any(ord(char) <= 32 for char in base):
        raise ToolError(
            "config_error",
            f"{PLUGIN_ID}.base_url must be HTTP(S), without credentials, query or fragment",
            **details,
        )
    return base, key


@dataclass(frozen=True)
class ImageInfo:
    format: str
    width: int
    height: int


@dataclass(frozen=True)
class InputImage:
    path: Path
    info: ImageInfo


def validate_field(name, value, field):
    kind = field["type"]
    valid_type = (
        (kind == "string" and isinstance(value, str))
        or (kind == "integer" and type(value) is int)
        or (kind == "string_list" and isinstance(value, list))
    )
    if not valid_type:
        raise ToolError("invalid_request", f"{name} must be {kind}")
    if "choices" in field and value not in field["choices"]:
        raise ToolError("invalid_request", f"Unsupported {name}")
    if isinstance(value, str):
        low, high = field.get("length", (0, sys.maxsize))
        if not value.strip() or not low <= len(value) <= high:
            raise ToolError("invalid_request", f"{name} has an invalid length")
        if "pattern" in field and not re.fullmatch(field["pattern"], value):
            raise ToolError("invalid_request", f"Invalid {name} format")
    elif type(value) is int:
        low, high = field["range"]
        if not low <= value <= high:
            raise ToolError("invalid_request", f"{name} is outside its allowed range")
    elif isinstance(value, list):
        low, high = field["length"]
        if not low <= len(value) <= high:
            raise ToolError("invalid_request", f"Invalid number of {name}")
        if any(not isinstance(item, str) or not item.strip() for item in value):
            raise ToolError("invalid_request", f"{name} must contain nonblank strings")


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
        validate_field(name, result[name], field)
    return result


def valid_size(width, height):
    return (
        width > 0
        and height > 0
        and width % 16 == height % 16 == 0
        and max(width, height) <= MAX_EDGE
        and max(width, height) <= 3 * min(width, height)
        and MIN_PIXELS <= width * height <= MAX_PIXELS
    )


def edit_size(width, height):
    if valid_size(width, height):
        return f"{width}x{height}"
    short, long = sorted((width, height))
    if short <= 0 or long > 3 * short:
        raise ToolError(
            "invalid_request",
            "Input aspect ratio exceeds 3:1; specify an agreed output size",
        )
    pixels = short * long
    if pixels < MIN_PIXELS:
        scale = math.sqrt(MIN_PIXELS / pixels)
    else:
        scale = min(1, MAX_EDGE / long, math.sqrt(MAX_PIXELS / pixels))
    short = int(short * scale / 16 + 0.5) * 16
    long = int(long * scale / 16 + 0.5) * 16

    # Correct boundary crossings introduced by rounding.
    if long > 3 * short:
        short += 16
    if short * long < MIN_PIXELS:
        short = (MIN_PIXELS + long * 16 - 1) // (long * 16) * 16
    elif short * long > MAX_PIXELS:
        long = MAX_PIXELS // (short * 16) * 16
    return f"{long}x{short}" if width >= height else f"{short}x{long}"


def exif_orientation(data):
    if data.startswith(b"Exif\x00\x00"):
        data = data[6:]
    if len(data) < 8 or data[:2] not in (b"II", b"MM"):
        return 1
    endian = "<" if data[:2] == b"II" else ">"
    if struct.unpack_from(endian + "H", data, 2)[0] != 42:
        return 1
    offset = struct.unpack_from(endian + "I", data, 4)[0]
    if offset + 2 > len(data):
        return 1
    count = struct.unpack_from(endian + "H", data, offset)[0]
    for index in range(count):
        position = offset + 2 + index * 12
        if position + 12 > len(data):
            break
        tag, kind, length = struct.unpack_from(endian + "HHI", data, position)
        if tag == 0x112 and kind == 3 and length == 1:
            return struct.unpack_from(endian + "H", data, position + 8)[0]
    return 1


def jpeg_info(stream):
    orientation = 1
    dimensions = None
    start_of_frame = set(range(0xC0, 0xD0)) - {0xC4, 0xC8, 0xCC}
    while True:
        marker = stream.read(1)
        if marker != b"\xff":
            break
        while marker == b"\xff":
            marker = stream.read(1)
        if not marker or marker[0] in (0xD9, 0xDA):
            break
        if marker[0] in range(0xD0, 0xD9) or marker[0] == 0x01:
            continue
        length = stream.read(2)
        if len(length) != 2:
            break
        size = int.from_bytes(length, "big") - 2
        if size < 0:
            break
        segment = stream.read(size)
        if len(segment) != size:
            break
        if marker[0] == 0xE1 and segment.startswith(b"Exif\x00\x00"):
            orientation = exif_orientation(segment)
        elif marker[0] in start_of_frame and len(segment) >= 6:
            height, width = struct.unpack_from(">HH", segment, 1)
            dimensions = (width, height)
    if dimensions is None:
        raise ValueError("Missing JPEG dimensions")
    width, height = dimensions
    if orientation in (5, 6, 7, 8):
        width, height = height, width
    return ImageInfo("jpeg", width, height)


def read_image_info(stream):
    header = stream.read(32)
    if header.startswith(b"\x89PNG\r\n\x1a\n") and header[12:16] == b"IHDR":
        width, height = struct.unpack(">II", header[16:24])
        return ImageInfo("png", width, height)
    if header.startswith(b"\xff\xd8"):
        stream.seek(2)
        return jpeg_info(stream)
    if header[:4] == b"RIFF" and header[8:12] == b"WEBP":
        kind = header[12:16]
        if kind == b"VP8X" and len(header) >= 30:
            width = 1 + int.from_bytes(header[24:27], "little")
            height = 1 + int.from_bytes(header[27:30], "little")
        elif kind == b"VP8 " and header[23:26] == b"\x9d\x01\x2a":
            width, height = struct.unpack("<HH", header[26:30])
            width, height = width & 0x3FFF, height & 0x3FFF
        elif kind == b"VP8L" and len(header) >= 25 and header[20] == 0x2F:
            bits = int.from_bytes(header[21:25], "little")
            width, height = (bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1
        else:
            raise ValueError("Unsupported WebP header")
        return ImageInfo("webp", width, height)
    raise ValueError("Expected a PNG, JPEG or WebP image")


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


def load_images(paths):
    images = []
    for value in paths:
        path = absolute_path(value, "image")
        try:
            file_stat = path.stat()
            if not stat.S_ISREG(file_stat.st_mode):
                raise ToolError(
                    "file_error",
                    "Input path must name an existing file",
                    path=str(path),
                )
            if not 0 < file_stat.st_size < MAX_IMAGE_BYTES:
                raise ToolError(
                    "invalid_request",
                    "Each image must be nonempty and below 50 MiB",
                    path=str(path),
                )
            with path.open("rb") as stream:
                info = read_image_info(stream)
            if info.width <= 0 or info.height <= 0:
                raise ValueError("Empty dimensions")
        except (ValueError, struct.error):
            raise ToolError(
                "invalid_request", "Invalid PNG, JPEG or WebP input", path=str(path)
            ) from None
        except OSError:
            raise ToolError(
                "file_error", "Cannot read input image", path=str(path)
            ) from None
        images.append(InputImage(path, info))
    return images


def prepare_request(arguments, editing):
    images = load_images(arguments["image"]) if editing else []
    path = absolute_path(arguments["path"], "path")
    output_format = {".png": "png", ".jpg": "jpeg", ".jpeg": "jpeg"}.get(
        path.suffix.lower()
    )
    if output_format is None:
        raise ToolError("invalid_request", "path must end in .png, .jpg or .jpeg")
    if arguments.get("output_format", output_format) != output_format:
        raise ToolError(
            "invalid_request", "output_format must match the path extension"
        )
    background = arguments["background"]
    if background == "transparent" and output_format != "png":
        raise ToolError("invalid_request", "Transparent output requires PNG")
    if "output_compression" in arguments and output_format != "jpeg":
        raise ToolError(
            "invalid_request", "output_compression is only available for JPEG"
        )
    size = arguments.get("size")
    if size is None:
        size = (
            edit_size(images[0].info.width, images[0].info.height)
            if editing
            else "2560x1440"
        )
    else:
        width, height = map(int, size.split("x"))
        if not valid_size(width, height):
            raise ToolError(
                "invalid_request",
                "size requires multiples of 16, edges <=3840, ratio <=3:1, and 655360..8294400 pixels",
            )
    payload = {
        "model": arguments["model"],
        "prompt": arguments["prompt"],
        "n": arguments["n"],
        "quality": arguments["quality"],
        "complexity": arguments["complexity"],
        "size": size,
        "background": background,
        "output_format": output_format,
        "moderation": "low",
        "stream": False,
    }
    if output_format == "jpeg":
        payload["output_compression"] = arguments.get("output_compression", 95)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryFile(dir=path.parent):
            pass
    except OSError:
        raise ToolError(
            "file_error", "Output directory is not writable", path=str(path.parent)
        ) from None
    return payload, images, path


@contextmanager
def multipart_body(payload, images):
    boundary = uuid.uuid4().hex
    with tempfile.TemporaryFile() as body:
        for name, value in payload.items():
            text = str(value).lower() if isinstance(value, bool) else str(value)
            body.write(
                (
                    f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{text}\r\n'
                ).encode()
            )
        for index, image in enumerate(images, 1):
            body.write(
                (
                    f'--{boundary}\r\nContent-Disposition: form-data; name="image[]"; '
                    f'filename="image-{index}.{image.info.format}"\r\n'
                    f"Content-Type: image/{image.info.format}\r\n\r\n"
                ).encode("ascii")
            )
            with image.path.open("rb") as source:
                shutil.copyfileobj(source, body, length=65536)
            body.write(b"\r\n")
        body.write(f"--{boundary}--\r\n".encode("ascii"))
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


def read_upstream_error(exc):
    try:
        with exc:
            if exc.code not in (400, 429):
                return {}
            body = json.load(exc)
    except (ValueError, OSError, http.client.HTTPException, RecursionError):
        return {}
    if isinstance(body, dict) and isinstance(body.get("error"), dict):
        return body["error"]
    return {}


def upstream_error(exc):
    status = exc.code
    error = read_upstream_error(exc)
    code, message = "invalid_request", "Upstream rejected the request"
    if status == 401:
        code, message = (
            "auth_failed",
            "Check the API key, organization and IP allowlist",
        )
    elif status == 403:
        code, message = (
            "auth_failed",
            "Check API access permissions and supported region",
        )
    elif status == 408:
        code, message = (
            "network_error",
            "Upstream request timed out; generation may have completed. Do not retry automatically.",
        )
    elif 500 <= status < 600:
        code, message = "service_error", "Upstream service failed"
    elif status == 400 and error.get("code") == "moderation_blocked":
        code = "moderation_blocked"
        message = "Image request was blocked by content filtering. Do not retry automatically."
    elif status == 429:
        if error.get("type") == "insufficient_quota" or error.get("code") in (
            "credit_balance_exhausted",
            "organization_spend_limit_exceeded",
            "project_spend_limit_exceeded",
            "organization_usage_limit_exceeded",
        ):
            code, message = (
                "billing_error",
                "Check the account credits and usage or spend limits",
            )
        else:
            code, message = "rate_limited", "Upstream rate limit reached"

    return ToolError(code, message, http_status=status)


def post_image_request(url, key, data, headers):
    request = urllib.request.Request(
        url,
        data=data,
        headers={**headers, "Authorization": "Bearer " + key},
        method="POST",
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
        raise upstream_error(exc) from None
    except (ValueError, RecursionError):
        raise ToolError("invalid_response", "Upstream returned invalid JSON") from None
    except (OSError, http.client.HTTPException):
        raise ToolError(
            "network_error",
            "Connection failed or timed out; generation may have completed. Do not retry automatically.",
        ) from None


@request_deadline(
    REQUEST_TIMEOUT,
    "Request timed out; generation may have completed. Do not retry automatically.",
)
def request_images(payload, images, base, key):
    try:
        if not images:
            return post_image_request(
                base + "/images/generations",
                key,
                json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                {"Content-Type": "application/json"},
            )
        with multipart_body(payload, images) as (data, headers):
            return post_image_request(base + "/images/edits", key, data, headers)
    except UnicodeError:
        raise ToolError(
            "invalid_request", "Request text must contain valid UTF-8 characters"
        ) from None
    except OSError:
        raise ToolError("file_error", "Cannot prepare input files for upload") from None


def decode_images(response, payload):
    data = response.get("data")
    if not isinstance(data, list) or len(data) != payload["n"]:
        raise ToolError("invalid_response", "Unexpected number of output images")
    decoded = []
    for item in data:
        if not isinstance(item, dict) or not isinstance(item.get("b64_json"), str):
            raise ToolError("invalid_response", "Expected base64 image data")
        try:
            raw = base64.b64decode(item["b64_json"], validate=True)
            info = read_image_info(io.BytesIO(raw))
        except (ValueError, struct.error):
            raise ToolError("invalid_response", "Invalid output image data") from None
        if (
            info.format != payload["output_format"]
            or info.width <= 0
            or info.height <= 0
        ):
            raise ToolError(
                "invalid_response", "Output image format does not match request"
            )
        decoded.append((raw, info))
    return decoded


def publish_image(temporary, path, index):
    while True:
        target = path.with_name(f"{path.stem}-{index}{path.suffix}") if index else path
        index += 1
        try:
            # A hard link publishes the complete file without overwriting.
            os.link(temporary, target)
            return target, index
        except FileExistsError:
            continue


def save_images(response, payload, path):
    decoded = decode_images(response, payload)
    saved = []
    index = 1 if len(decoded) > 1 else 0
    try:
        for raw, info in decoded:
            with tempfile.NamedTemporaryFile(dir=path.parent) as stream:
                stream.write(raw)
                stream.flush()
                target, index = publish_image(stream.name, path, index)
                saved.append(
                    {
                        "path": str(target),
                        "size": f"{info.width}x{info.height}",
                        "bytes": len(raw),
                    }
                )
    except OSError:
        raise ToolError(
            "file_error",
            "Could not save all output files",
            path=str(path.parent),
            saved=saved,
        ) from None
    result = {
        "images": saved,
        "model": payload["model"],
        "output_format": payload["output_format"],
        "requested_size": payload["size"],
        "quality": payload["quality"],
        "complexity": payload["complexity"],
    }
    if any(image["size"] != payload["size"] for image in saved):
        result["warnings"] = [
            "Upstream returned dimensions different from requested_size; files retain the returned dimensions."
        ]
    if isinstance(response.get("usage"), dict):
        result["usage"] = response["usage"]
    return result


def run_image(arguments, editing):
    base, key = read_config()
    payload, images, path = prepare_request(arguments, editing)
    response = request_images(payload, images, base, key)
    return save_images(response, payload, path)


HANDLERS = {
    "generate": partial(run_image, editing=False),
    "edit": partial(run_image, editing=True),
}


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
