# 接口参考

## 请求

配置见[插件说明](../../../README.md#配置)。服务在 `base_url` 后追加 `/images/generations` 或 `/images/edits`，分别使用 JSON 和 `multipart/form-data`（`image[]`）。请求总时限 600 秒（含上传体准备、上传和响应读取），不使用代理，不自动重试。

仅支持 `gpt-image-2.6-flare`、`gpt-image-2.6-sunburst`，均为非流式调用。

## 参数与默认值

上游参数核对日期：2026-11-10。

| 参数 | 上游默认值 | 插件约束与默认值 |
|---|---|---|
| `prompt` | 必填 | 非空，最多 32000 字符 |
| `path` | 插件专用 | 必填，绝对路径，扩展名 `.png/.jpg/.jpeg`，支持展开 `~` |
| `image` | 编辑必填 | 1–16 个本地 PNG/JPEG/WebP 绝对路径，每张小于 50 MiB，支持展开 `~` |
| `model` | 生成需指定；编辑为 2.5 Sunburst | 仅 2.6 Flare/Sunburst；生成默认 Flare，编辑默认 Sunburst |
| `quality` | `auto` | `low/medium/high/xhigh/max`，默认 `high` |
| `complexity` | 2.6 为 `high` | `low/medium/high`，默认 `high` |
| `size` | `auto` | `WIDTHxHEIGHT`；生成默认 `1920x1440`，编辑按主图计算 |
| `background` | `auto` | `opaque/transparent`，默认 `opaque`；透明仅限 PNG |
| `output_format` | `png` | `png/jpeg`，按扩展名补全；显式传入时须一致 |
| `output_compression` | 生成 100；编辑未注明 | 整数 0–100，仅限 JPEG，默认 95；PNG 不传 |
| `n` | 1 | 整数 1–10，默认 1 |
| `moderation` | `auto` | 固定 `low` |
| `stream` | `false` | 固定 `false` |

服务补全并显式发送默认值，仅接受工具定义中的参数；`moderation`、`stream` 不开放设置。尺寸不接受 `auto`，2.6 按约定沿用官方 2.5 限制：宽高为 16 的倍数，单边 ≤3840，长短边比 ≤3:1，总像素 655360–8294400。

## 编辑尺寸

读取主图文件头，JPEG 按 EXIF 方向解释尺寸，合法尺寸保持不变：

- 需要缩放时优先保持原比例：像素不足选最小合法尺寸，超限选最大合法尺寸。
- 无法精确保留比例时，选最接近等比缩放目标的合法网格点；同距时优先接近原比例和目标像素数。
- 仅不满足 16 倍数时，选最近的合法网格点。

例如 `1025x768` → `1024x768`，`500x500` → `816x816`，`4000x4000` → `2880x2880`。原图比例超过 3:1 时须明确指定输出尺寸。上传文件保持原样。

## 文件与返回

MCP 按调用方提供的 `path` 保存，自动创建父目录。单图优先使用原名，重名时追加 `-1`、`-2`；多图从 `-1` 开始，跳过已有名称。文件原子发布，不覆盖已有文件或输入图，重名不触发上游重试。

成功返回 `images[]`（绝对 `path`、实际 `size`、`bytes`），以及 `model`、`output_format`、`requested_size`、`quality`、`complexity` 和上游提供的 `usage`。不返回 Base64；实际尺寸与请求不同时附 `warnings`。

## 错误

失败时 `isError=true`，正文为含 `code`、`message` 的 JSON。`config_error` 附配置文件 `path`；HTTP 错误附可用的 `http_status`、`request_id`、`upstream_code`、`upstream_type`、`upstream_param`。不返回配置内容、原始上游错误正文或密钥。

错误码为 `config_error`、`invalid_request`、`auth_failed`、`insufficient_balance`、`rate_limited`、`network_error`、`service_error`、`invalid_response`、`file_error`、`internal_error`。部分保存失败时，`saved` 列出已有文件。

## 来源

[生成接口](https://developers.openai.com/api/reference/resources/images/methods/generate) · [编辑接口](https://developers.openai.com/api/reference/resources/images/methods/edit) · [图片指南](https://developers.openai.com/api/docs/guides/image-generation)
