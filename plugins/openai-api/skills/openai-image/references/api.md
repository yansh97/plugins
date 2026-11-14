# 接口参考

## 调用

仅支持 macOS。将示例路径替换为本技能 `scripts/run.py` 的绝对路径，无需设置 `cwd`。操作名是唯一命令参数；通过 `stdin` 传入 UTF-8 JSON 对象，以 EOF 结束。

```bash
/usr/bin/python3 "/本技能的绝对路径/scripts/run.py" generate <<'JSON'
{"prompt":"浅灰背景上的陶瓷花瓶产品图","path":"/Users/example/Downloads/vase-generate.png"}
JSON
```

```bash
/usr/bin/python3 "/本技能的绝对路径/scripts/run.py" edit <<'JSON'
{"prompt":"把花瓶改成蓝色，保留光照和背景","path":"/Users/example/Downloads/blue-vase-edit.png","image":["/Users/example/Downloads/vase-generate.png"]}
JSON
```

`--help` 离线列出操作和参数，无需配置。仅接受下表字段，字符串不得为空或全为空白；脚本补全默认值。请求总时限为 600 秒，包含请求准备和网络等待，不含结果保存时间。

配置由脚本读取：`~/.config/agents/config.json` → `openai-api.api_key` 和 `openai-api.base_url`。勿自行读取配置或传入密钥。

## 参数

| 参数 | 约束与默认值 |
|---|---|
| `prompt` | 必填字符串，最多 32000 字符 |
| `path` | 必填字符串；输出绝对路径，扩展名 `.png/.jpg/.jpeg`，支持展开 `~` |
| `image` | 仅 `edit` 使用且必填；1–16 个本地 PNG/JPEG/WebP 绝对路径，每个路径非空，每张图片非空且小于 50 MiB，支持展开 `~` |
| `model` | `gpt-image-2.6-flare` 或 `gpt-image-2.6-sunburst`；生成默认 Flare，编辑默认 Sunburst |
| `size` | `WIDTHxHEIGHT`；生成默认 `2560x1440`（16:9），编辑按主图计算，规则见下节 |
| `quality` | `low/medium/high/xhigh/max`，默认 `high` |
| `complexity` | `low/medium/high`，默认 `high` |
| `n` | 整数 1–10，默认 1 |
| `output_format` | `png/jpeg`，按扩展名补全；显式传入时须一致 |
| `background` | `opaque/transparent`，默认 `opaque`；透明仅限 PNG |
| `output_compression` | 整数 0–100，仅限 JPEG，默认 95；PNG 不传 |

## 尺寸

`size` 使用小写 `x`，不接受 `auto`：宽高为 16 的倍数，单边 ≤3840，长短边比 ≤3:1，总像素 655360–8294400。

### 生成

按用途选比例，下表尺寸均合法。默认短边约 1440 px，大尺寸约 2160 px；无线索时用 `2560x1440`。

| 比例 | 短边约 1440 px | 短边约 2160 px |
|---|---|---|
| 1:1 | 1440x1440 | 2160x2160 |
| 3:2 / 2:3 | 2160x1440 / 1440x2160 | 3264x2176 / 2176x3264 |
| 4:3 / 3:4 | 1920x1440 / 1440x1920 | 2880x2160 / 2160x2880 |
| 5:4 / 4:5 | 1840x1472 / 1472x1840 | 2720x2176 / 2176x2720 |
| 16:9 / 9:16 | 2560x1440 / 1440x2560 | 3840x2160 / 2160x3840 |

### 编辑

省略 `size` 时，按主图计算，其他参考图不决定尺寸；JPEG 按 EXIF 方向解释尺寸。合法尺寸保持不变；像素不足时等比例放大，像素或边长超限时等比例缩小，再取最近的 16 倍数，恰好居中时向上取整，必要时修正取整后的比例和像素边界。

例如 `1025x768` → `1024x768`，`500x500` → `816x816`，`4000x4000` → `2880x2880`。主图比例超过 3:1 时须先确定可接受的输出尺寸，并显式传入 `size`。上传文件保持原样。

## 结果

退出码 `0`：读取 `stdout` JSON；`1`：按下节处理错误。`stderr` 为日志；无 JSON 时结合日志报告执行失败。

完整结果先保存为文件，`stdout` 仅返回元信息。按实际返回路径预览或交付文件，不为预览或交付重复请求。

| 字段 | 用途 |
|---|---|
| `images[]` | 已保存图片列表 |
| `images[].path` | 图片的绝对路径 |
| `images[].size` | 图片的实际尺寸 |
| `images[].bytes` | 图片文件字节数 |
| `model` | 实际使用的模型 |
| `output_format` | 输出格式 |
| `requested_size` | 请求尺寸；交付尺寸以 `images[].size` 为准 |
| `quality` | 本次质量 |
| `complexity` | 本次复杂度 |
| `warnings` | 可选，实际尺寸与请求不同时返回，须告知用户 |
| `usage` | 可选，用量信息 |

图片已保存，无需下载。脚本创建父目录，不覆盖已有文件或输入图；单图重名时追加 `-1`、`-2`，多图从 `-1` 开始。按返回的实际路径交付。

## 错误处理

错误含 `code`、`message`，可附 `path`、`http_status`。部分保存失败时，`saved[]` 与 `images[]` 结构相同；先交付已有文件并说明未保存部分。

| `code` | 处理 |
|---|---|
| `invalid_request` | 按 `message` 修正参数、尺寸或输入文件 |
| `file_error` | 检查文件、目录与权限；有 `saved[]` 时先交付已有文件，不重新生成 |
| `moderation_blocked` | 告知请求被内容过滤，不自动重试 |
| `config_error` | 提示用户检查 `openai-api.api_key`、`openai-api.base_url` 和配置文件，不读取或输出密钥 |
| `auth_failed` | 提示用户检查密钥、组织、IP 白名单、权限或地区限制 |
| `billing_error` | 提示用户检查余额、用量额度或支出上限 |
| `rate_limited` / `network_error` / `service_error` | 报告限流、网络或服务故障，不重复提交 |
| `invalid_response` / `internal_error` | 停止调用并报告失败，不宣称图片已保存 |

失败不自动重试。超时、断连或服务错误后，上游仍可能已生成图片；保存失败不重新发起生成。

## 来源

[生成接口](https://developers.openai.com/api/reference/resources/images/methods/generate) · [编辑接口](https://developers.openai.com/api/reference/resources/images/methods/edit) · [图片指南](https://developers.openai.com/api/docs/guides/image-generation#handling-blocked-requests-and-other-errors) · [错误码](https://developers.openai.com/api/docs/guides/error-codes) · [HTTP 状态分类](https://developers.openai.com/api/reference/python#handling-errors)
