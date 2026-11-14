# 接口参考

## 调用

仅支持 macOS。将示例路径替换为本技能 `scripts/run.py` 的绝对路径，无需设置 `cwd`。操作名是唯一命令参数；通过 `stdin` 传入 UTF-8 JSON 对象，以 EOF 结束。

```bash
/usr/bin/python3 "/本技能的绝对路径/scripts/run.py" asr <<'JSON'
{"file":"/Users/example/Recordings/message.wav","path":"/Users/example/Downloads/message-asr.json"}
JSON
```

```bash
/usr/bin/python3 "/本技能的绝对路径/scripts/run.py" tts <<'JSON'
{"text":"你好，欢迎收听。","path":"/Users/example/Downloads/welcome-tts.wav"}
JSON
```

`--help` 离线列出操作和参数，无需配置。仅接受下表字段，字符串不得为空或全为空白；脚本补全默认值。请求总时限：`asr` 为 660 秒，`tts` 为 1800 秒；均包含请求准备和网络等待，不含结果保存时间。

配置由脚本读取：`~/.config/agents/config.json` → `deepseek-api.api_key`。勿自行读取配置或传入密钥。

## 参数

### `asr`

| 参数 | 约束与默认值 |
|---|---|
| `file` | 必填字符串；本地文件绝对路径，支持展开 `~`；文件非空且不超过 8 MiB |
| `path` | 必填字符串；输出 JSON 绝对路径，扩展名 `.json`，支持展开 `~` |

支持 16-bit PCM WAV、Ogg Opus，最长 120 秒；格式和时长由服务端校验。WAV 采样率为 8000、16000、22050、24000、32000、44100 或 48000 Hz。支持单声道、双声道，双声道仅识别左声道；语言自动识别。

### `tts`

| 参数 | 约束与默认值 |
|---|---|
| `text` | 必填字符串，完整朗读文本 |
| `path` | 必填字符串；输出绝对路径，扩展名 `.wav/.pcm`，支持展开 `~` |
| `voice_id` | `stella`（女声，默认）或 `tide`（男声） |
| `format` | `wav` 或 `pcm`；按路径扩展名补全，显式传入时须一致 |
| `filter_markdown` | 布尔值，默认 `true`；过滤 Markdown 标记 |

每次最多 10000 个计费字符，汉字计 2 个，其他字符计 1 个，以服务端判断为准；JSON 请求体最多 64 KiB。单次合成最长 30 分钟音频。不接受 `stream`、语言或语速参数。

## 结果

退出码 `0`：读取 `stdout` JSON；`1`：按下节处理错误。`stderr` 为日志；无 JSON 时结合日志报告执行失败。

完整结果先保存为文件，`stdout` 仅返回元信息。按实际返回路径读取、播放或交付文件，不为补读或交付重复请求。

### `asr`

返回元信息：

| 字段 | 用途 |
|---|---|
| `path` | 实际保存的 JSON 绝对路径 |
| `bytes` | 文件字节数 |
| `characters` | 转写文字的字符数 |
| `duration_ms` | 可选，音频时长，毫秒 |

ASR 完整结果保存为 UTF-8 JSON，保留上游字段：

| 字段 | 用途 |
|---|---|
| `text` | 完整转写文字 |
| `duration_ms` | 音频时长，毫秒 |
| `request_id` | 请求 ID |

### `tts`

返回元信息：

| 字段 | 用途 |
|---|---|
| `path` | 实际保存的绝对路径 |
| `format` | 音频格式 |
| `bytes` | 文件字节数 |
| `voice_id` | 所用声音 |
| `duration_ms` | 音频时长，毫秒，向下取整；WAV 无法解析时省略 |
| `sample_rate` | 仅 PCM 返回，采样率为 24000 Hz |
| `channels` | 仅 PCM 返回，声道数为 1 |
| `sample_width_bits` | 仅 PCM 返回，采样位数为 16 |

自动创建父目录，重名时追加 `-1`、`-2`，不覆盖已有文件。按返回路径交付，不自行推测文件名。

时长由音频数据计算，不按朗读文本估算；WAV 头声明的帧数超过实际数据时，按实际可用的完整帧计算。本地解析器不支持 WAV 格式时仍保存文件，省略时长，不重新合成。

PCM 是 24 kHz、16-bit signed、单声道的裸采样数据，没有 WAV 文件头；播放或转换时使用返回的采样参数。脚本能检测 HTTP 传输截断，不能检测服务正常结束却少合成内容的情况。

## 按需读取

不要直接回显整个 ASR JSON。需要分析时按任务分段读取 `text`；需要 TXT 时本地导出，不重复转写。以下示例只读取前 2000 个字符，按需调整范围：

```bash
/usr/bin/python3 - "/实际返回的路径/message-asr.json" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as stream:
    result = json.load(stream)
print(result["text"][:2000])
PY
```

其余转写文字仍在文件中。全文整理时分段处理，不把本地读取范围当成完整转写结果。

## 错误处理

错误含 `code`、`message`，可附 `path`、`http_status`。按 `code` 处理，不推测原因。

| `code` | 处理 |
|---|---|
| `invalid_request` | 按 `message` 检查参数、音频要求、文本长度与请求体大小；原因不明时说明请求被拒绝 |
| `file_error` | 检查文件、目录与权限；保存失败不重新转写或合成 |
| `config_error` | 提示用户检查 `deepseek-api.api_key` 和配置文件，不读取或输出密钥 |
| `auth_failed` / `billing_error` | 提示用户处理密钥或余额问题 |
| `rate_limited` | 告知并发请求过多，可稍后再试 |
| `network_error` / `service_error` | 报告上传、连接、接收或服务故障，不重复提交 |
| `invalid_response` / `internal_error` | 停止调用并报告失败，不交付未保存的结果 |

失败不自动重试。超时、断连或服务错误不代表上游未处理；不重复上传等待中的文件，不因结果接收、保存或读取失败重新转写或合成。

## 来源

[ASR 指南](https://api-docs.deepseek.com/guides/asr/) · [ASR 接口](https://api-docs.deepseek.com/api/asr/) · [TTS 指南](https://api-docs.deepseek.com/guides/tts/) · [TTS 接口](https://api-docs.deepseek.com/api/tts/) · [通用错误码](https://api-docs.deepseek.com/quick_start/error_codes/)
