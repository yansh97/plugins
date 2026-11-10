#!/usr/bin/env python3
"""OpenAI Image MCP server; Python 3.9 standard library only."""

import base64
import http.client
import io
import json
import math
import os
import re
import shutil
import signal
import struct
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

SERVER_INFO = {"name": "openai-image", "version": "0.1.0"}
PROTOCOL_VERSIONS = ("2024-11-05", "2025-03-26", "2025-06-18")
FLARE = "gpt-image-2.6-flare"
SUNBURST = "gpt-image-2.6-sunburst"
SIZE_PATTERN = r"([1-9][0-9]{0,3})x([1-9][0-9]{0,3})"
MIN_PIXELS, MAX_PIXELS, MAX_EDGE = 655360, 8294400, 3840
MAX_IMAGE_BYTES = 50 * 1024 * 1024
MAX_RESPONSE_BYTES = 256 * 1024 * 1024
REQUEST_TIMEOUT = 600


IMAGE_PROPERTIES = {
    "prompt": {
        "type": "string",
        "minLength": 1,
        "maxLength": 32000,
        "description": "图片描述或编辑指令；编辑时说明修改项与保留项。",
    },
    "path": {
        "type": "string",
        "minLength": 1,
        "maxLength": 4096,
        "description": "输出绝对路径，扩展名 .png/.jpg/.jpeg；重名或多图自动追加序号，不覆盖文件。",
    },
    "model": {
        "type": "string",
        "enum": [SUNBURST, FLARE],
        "default": FLARE,
        "description": "生成默认 Flare，编辑默认 Sunburst。",
    },
    "size": {
        "type": "string",
        "pattern": f"^{SIZE_PATTERN}$",
        "description": "WIDTHxHEIGHT，不接受 auto。宽高为 16 的倍数，单边 ≤3840，比例 ≤3:1，总像素 655360–8294400。生成默认 1920x1440，编辑按主图计算合法尺寸。",
    },
    "quality": {
        "type": "string",
        "enum": ["low", "medium", "high", "xhigh", "max"],
        "default": "high",
        "description": "渲染质量。",
    },
    "complexity": {
        "type": "string",
        "enum": ["low", "medium", "high"],
        "default": "high",
        "description": "规划复杂度。",
    },
    "n": {
        "type": "integer",
        "minimum": 1,
        "maximum": 10,
        "default": 1,
        "description": "输出数量。",
    },
    "output_format": {
        "type": "string",
        "enum": ["png", "jpeg"],
        "description": "按 path 扩展名补全，显式传入时须一致。",
    },
    "background": {
        "type": "string",
        "enum": ["opaque", "transparent"],
        "default": "opaque",
        "description": "背景类型，透明仅限 PNG。",
    },
    "output_compression": {
        "type": "integer",
        "minimum": 0,
        "maximum": 100,
        "description": "JPEG 压缩质量，默认 95；PNG 不传。",
    },
}

TOOLS = [
    {
        "name": "image-generation",
        "description": "按文字生成图片，保存到 path，返回路径、尺寸和 usage。",
        "inputSchema": {
            "type": "object",
            "properties": IMAGE_PROPERTIES,
            "required": ["prompt", "path"],
            "additionalProperties": False,
        },
    },
    {
        "name": "image-edit",
        "description": "上传本地参考图并按文字编辑，保存到 path，返回路径、尺寸和 usage。",
        "inputSchema": {
            "type": "object",
            "properties": {
                **IMAGE_PROPERTIES,
                "model": {**IMAGE_PROPERTIES["model"], "default": SUNBURST},
                "image": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 16,
                    "items": {"type": "string", "minLength": 1, "maxLength": 4096},
                    "description": "本地 PNG/JPEG/WebP 绝对路径，每张小于 50 MiB；第一张为主图，其余为参考。上传原文件，不接收 URL。",
                },
            },
            "required": ["prompt", "path", "image"],
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
    """Bound a request on the service's main thread (macOS/Linux)."""

    def expire(signum, frame):
        raise ToolError("network_error", message)

    previous_handler = signal.signal(signal.SIGALRM, expire)
    try:
        signal.setitimer(signal.ITIMER_REAL, REQUEST_TIMEOUT)
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous_handler)


def read_config(fields):
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
    result = {}
    for field in fields:
        value = section.get(field)
        if not isinstance(value, str) or not value.strip():
            raise ToolError(
                "config_error", f"{name}.{field} must be a nonempty string", **details
            )
        result[field] = value.strip()
    key = result["api_key"]
    if any(not 33 <= ord(char) <= 126 for char in key):
        raise ToolError("config_error", f"Invalid {name}.api_key", **details)
    return result


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


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
        or (kind == "array" and isinstance(value, list))
    )
    if not valid_type:
        raise ToolError("invalid_request", f"{name} must be {kind}")
    if "enum" in field and value not in field["enum"]:
        raise ToolError("invalid_request", f"Unsupported {name}")
    if isinstance(value, str):
        if not value.strip() or not field.get("minLength", 0) <= len(
            value
        ) <= field.get("maxLength", sys.maxsize):
            raise ToolError("invalid_request", f"{name} has an invalid length")
        if "pattern" in field and not re.fullmatch(field["pattern"], value):
            raise ToolError("invalid_request", f"Invalid {name} format")
    elif type(value) is int:
        if not field.get("minimum", value) <= value <= field.get("maximum", value):
            raise ToolError("invalid_request", f"{name} is outside its allowed range")
    elif isinstance(value, list):
        if not field["minItems"] <= len(value) <= field["maxItems"]:
            raise ToolError("invalid_request", f"Invalid number of {name}")
        for item in value:
            validate_field(name, item, field["items"])


def validate_arguments(arguments, schema):
    """Validate arguments and apply schema defaults."""
    if not isinstance(arguments, dict):
        raise ToolError("invalid_request", "arguments must be an object")
    properties = schema["properties"]
    if set(arguments) - properties.keys():
        raise ToolError("invalid_request", "Unknown argument")
    if any(name not in arguments for name in schema["required"]):
        raise ToolError("invalid_request", "Missing required argument")
    result = dict(arguments)
    for name, field in properties.items():
        if name not in result and "default" in field:
            result[name] = field["default"]
        if name in result:
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


def parse_size(value):
    match = re.fullmatch(SIZE_PATTERN, value)
    if not match:
        raise ToolError("invalid_request", "size must be WIDTHxHEIGHT, without auto")
    width, height = map(int, match.groups())
    if not valid_size(width, height):
        raise ToolError(
            "invalid_request",
            "size requires multiples of 16, edges <=3840, ratio <=3:1, and 655360..8294400 pixels",
        )
    return width, height


def edit_size(width, height):
    if valid_size(width, height):
        return f"{width}x{height}"
    if width <= 0 or height <= 0 or max(width, height) > 3 * min(width, height):
        raise ToolError(
            "invalid_request",
            "Input aspect ratio exceeds 3:1; specify an agreed output size",
        )
    pixels = width * height
    if pixels < MIN_PIXELS:
        scale = math.sqrt(MIN_PIXELS / pixels)
    else:
        scale = min(1, MAX_EDGE / max(width, height), math.sqrt(MAX_PIXELS / pixels))
    target_width, target_height = width * scale, height * scale

    if scale != 1:
        divisor = math.gcd(width, height)
        unit_width, unit_height = width // divisor * 16, height // divisor * 16
        unit_pixels = unit_width * unit_height
        minimum = math.isqrt((MIN_PIXELS - 1) // unit_pixels) + 1
        maximum = min(
            MAX_EDGE // max(unit_width, unit_height),
            math.isqrt(MAX_PIXELS // unit_pixels),
        )
        if minimum <= maximum:
            multiplier = minimum if pixels < MIN_PIXELS else maximum
            return f"{unit_width * multiplier}x{unit_height * multiplier}"

    def distance(size):
        w, h = size
        return (
            (w / target_width - 1) ** 2 + (h / target_height - 1) ** 2,
            abs(w / h - width / height),
            abs(w * h - target_width * target_height),
        )

    candidates = (
        (w, h)
        for w in range(16, MAX_EDGE + 1, 16)
        for h in range(16, MAX_EDGE + 1, 16)
        if valid_size(w, h)
    )
    nearest = min(candidates, key=distance)
    return f"{nearest[0]}x{nearest[1]}"


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
    stream.seek(0)
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


def image_path(value):
    if "\x00" in value:
        raise ToolError("invalid_request", "Image paths must not contain null bytes")
    try:
        path = Path(value).expanduser()
    except RuntimeError:
        raise ToolError("invalid_request", "Cannot expand image path") from None
    if not path.is_absolute():
        raise ToolError("invalid_request", "Image paths must be absolute")
    return path


def load_images(paths):
    images = []
    for value in paths:
        path = image_path(value)
        try:
            path = path.resolve()
            if not path.is_file() or not 0 < path.stat().st_size < MAX_IMAGE_BYTES:
                raise ToolError(
                    "invalid_request", "Each image must be a file below 50 MiB"
                )
            with path.open("rb") as stream:
                info = read_image_info(stream)
            if info.width <= 0 or info.height <= 0:
                raise ValueError("Empty dimensions")
        except (ValueError, struct.error):
            raise ToolError(
                "invalid_request", "Invalid PNG, JPEG or WebP input"
            ) from None
        except (OSError, RuntimeError):
            raise ToolError(
                "file_error", "Cannot read input image", path=str(path)
            ) from None
        images.append(InputImage(path, info))
    return images


def output_path(value):
    path = image_path(value)
    if path.suffix.lower() not in (".png", ".jpg", ".jpeg"):
        raise ToolError("invalid_request", "path must end in .png, .jpg or .jpeg")
    return path


def prepare_request(arguments, editing):
    images = load_images(arguments["image"]) if editing else []
    path = output_path(arguments["path"])
    output_format = "png" if path.suffix.lower() == ".png" else "jpeg"
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
            else "1920x1440"
        )
    parse_size(size)
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
        raise ToolError("file_error", "Output directory is not writable") from None
    return payload, images, path


def api_settings():
    config = read_config(("api_key", "base_url"))
    base = config["base_url"].rstrip("/")
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
            "openai-image.base_url must be HTTP(S), without credentials, query or fragment",
            path=str(Path.home() / ".config/agents/config.json"),
        )
    return base, config["api_key"]


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


def upstream_error(exc):
    status = exc.code
    code, message = "invalid_request", "Upstream rejected the request"
    if status in (401, 403):
        code, message = "auth_failed", "Check the configured API key and permissions"
    elif status == 402:
        code, message = (
            "insufficient_balance",
            "Upstream account balance is insufficient",
        )
    elif status == 429:
        code, message = "rate_limited", "Upstream rate limit reached"
    elif status >= 500:
        code, message = "service_error", "Upstream service failed"
    details = {"http_status": status}
    request_id = exc.headers.get("x-request-id", "")
    if re.fullmatch(r"[A-Za-z0-9_.:-]{1,200}", request_id):
        details["request_id"] = request_id
    with exc:
        try:
            body = json.loads(exc.read(65536))
            error = body.get("error", {}) if isinstance(body, dict) else {}
            if isinstance(error, dict):
                for name in ("code", "type", "param"):
                    value = error.get(name)
                    if isinstance(value, str) and re.fullmatch(
                        r"[A-Za-z0-9_.\[\]-]{1,100}", value
                    ):
                        details["upstream_" + name] = value
        except (ValueError, OSError, http.client.HTTPException):
            pass
    return ToolError(code, message, **details)


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
            raw = res.read(MAX_RESPONSE_BYTES + 1)
        if len(raw) > MAX_RESPONSE_BYTES:
            raise ToolError("invalid_response", "Upstream response is too large")
        result = json.loads(raw)
        if not isinstance(result, dict):
            raise ToolError("invalid_response", "Expected a JSON object from upstream")
        return result
    except urllib.error.HTTPError as exc:
        raise upstream_error(exc) from None
    except ValueError:
        raise ToolError("invalid_response", "Upstream returned invalid JSON") from None
    except (urllib.error.URLError, OSError, http.client.HTTPException):
        raise ToolError(
            "network_error",
            "Connection failed or timed out; generation may have completed. Do not retry automatically.",
        ) from None


@request_deadline(
    "Request timed out; generation may have completed. Do not retry automatically."
)
def request_images(payload, images, base, key):
    if not images:
        return post_image_request(
            base + "/images/generations",
            key,
            json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            {"Content-Type": "application/json"},
        )
    try:
        with multipart_body(payload, images) as (data, headers):
            return post_image_request(base + "/images/edits", key, data, headers)
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
            temporary = None
            try:
                with tempfile.NamedTemporaryFile(
                    dir=path.parent, delete=False
                ) as stream:
                    temporary = Path(stream.name)
                    stream.write(raw)
                    stream.flush()
                    os.fsync(stream.fileno())
                target, index = publish_image(temporary, path, index)
                saved.append(
                    {
                        "path": str(target),
                        "size": f"{info.width}x{info.height}",
                        "bytes": len(raw),
                    }
                )
            finally:
                if temporary is not None:
                    temporary.unlink(missing_ok=True)
    except OSError:
        raise ToolError(
            "file_error", "Could not save all output files", saved=saved
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


def run_image_tool(arguments, editing):
    base, key = api_settings()
    payload, images, path = prepare_request(arguments, editing)
    response = request_images(payload, images, base, key)
    return save_images(response, payload, path)


def image_generation(arguments):
    return run_image_tool(arguments, editing=False)


def image_edit(arguments):
    return run_image_tool(arguments, editing=True)


HANDLERS = {"image-generation": image_generation, "image-edit": image_edit}


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
            sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
            sys.stdout.flush()


def main():
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        if isinstance(stream, io.TextIOWrapper):
            stream.reconfigure(encoding="utf-8")
    if len(TOOLS_BY_NAME) != len(TOOLS) or TOOLS_BY_NAME.keys() != HANDLERS.keys():
        raise ValueError("Tool definitions and handlers do not match")
    serve()


if __name__ == "__main__":
    main()
