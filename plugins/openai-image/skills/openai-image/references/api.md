# 接口参考

`image-generation` 按文字生成，`image-edit` 上传本地参考图并按文字编辑。两者共用以下参数，`image` 仅用于编辑。

## 参数与默认值

| 参数 | 约束与默认值 |
|---|---|
| `prompt` | 必填，非空字符串，最多 32000 字符 |
| `path` | 必填，绝对路径，扩展名 `.png/.jpg/.jpeg`，支持展开 `~` |
| `image` | 编辑必填，1–16 个本地 PNG/JPEG/WebP 绝对路径，每张非空且小于 50 MiB，支持展开 `~` |
| `model` | `gpt-image-2.6-flare` 或 `gpt-image-2.6-sunburst`；生成默认 Flare，编辑默认 Sunburst |
| `quality` | `low/medium/high/xhigh/max`，默认 `high` |
| `complexity` | `low/medium/high`，默认 `high` |
| `size` | `WIDTHxHEIGHT`；生成默认 `2560x1440`（16:9），编辑按主图计算 |
| `background` | `opaque/transparent`，默认 `opaque`；透明仅限 PNG |
| `output_format` | `png/jpeg`，按扩展名补全；显式传入时须一致 |
| `output_compression` | 整数 0–100，仅限 JPEG，默认 95；PNG 不传 |
| `n` | 整数 1–10，默认 1 |

默认值由服务补全并显式发送，不依赖上游默认值。仅接受上述参数；内容过滤固定为 `moderation=low`，使用非流式调用。

尺寸不接受 `auto`：宽高为 16 的倍数，单边 ≤3840，长短边比 ≤3:1，总像素 655360–8294400。

## 编辑尺寸

省略 `size` 时按主图计算，JPEG 按 EXIF 方向解释尺寸，合法尺寸保持不变：

- 需要缩放时优先保持主图比例：像素不足选最小合法尺寸，超限选最大合法尺寸。
- 无法精确保留比例，或仅不满足 16 倍数时，选最接近目标的合法尺寸。

例如 `1025x768` → `1024x768`，`500x500` → `816x816`，`4000x4000` → `2880x2880`。主图比例超过 3:1 时须明确指定输出尺寸。上传文件保持原样。

## 文件与返回

按 `path` 保存并创建父目录，不覆盖已有文件或输入图。单图优先使用原名，重名时追加 `-1`、`-2`；多图从 `-1` 开始，跳过已有名称。

成功返回 `images[]`（绝对 `path`、实际 `size`、`bytes`），以及 `model`、`output_format`、`requested_size`、`quality`、`complexity` 和上游提供的 `usage`。不返回 Base64；实际尺寸与请求不同时附 `warnings`。

## 错误

失败时 `isError=true`，正文为含 `code`、`message` 的 JSON。配置和文件相关错误可附 `path`，HTTP 错误附 `http_status`；部分保存失败时，`saved` 列出已有文件。不返回原始上游错误或密钥。

| `code` | 含义 |
|---|---|
| `config_error` | 配置缺失或无效 |
| `invalid_request` | 请求不合法或被上游拒绝，含无法细分的 HTTP 4xx |
| `moderation_blocked` | 请求触发内容过滤 |
| `auth_failed` | 密钥或访问权限问题 |
| `billing_error` | 余额或额度不足 |
| `rate_limited` | 请求限流 |
| `network_error` | 连接失败或请求超时 |
| `service_error` | 上游服务故障（HTTP 5xx） |
| `invalid_response` | 上游响应不符合预期 |
| `file_error` | 文件读取、上传准备或保存失败 |
| `internal_error` | 插件内部异常 |

## 来源

[生成接口](https://developers.openai.com/api/reference/resources/images/methods/generate) · [编辑接口](https://developers.openai.com/api/reference/resources/images/methods/edit) · [图片指南](https://developers.openai.com/api/docs/guides/image-generation)
