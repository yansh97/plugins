# 接口参考

## 调用

仅支持 macOS。将示例路径替换为本技能 `scripts/run.py` 的绝对路径，无需设置 `cwd`。操作名是唯一命令参数；通过 `stdin` 传入 UTF-8 JSON 对象，以 EOF 结束。

```bash
/usr/bin/python3 "/本技能的绝对路径/scripts/run.py" ocr <<'JSON'
{"file":"/Users/example/Documents/report.pdf","path":"/Users/example/Downloads/report-ocr.json"}
JSON
```

`--help` 离线列出操作和参数，无需配置。仅接受下表字段，字符串不得为空或全为空白；脚本补全默认值。请求总时限为 210 秒，包含请求准备和网络等待，不含结果保存时间。

配置由脚本读取：`~/.config/agents/config.json` → `deepseek-api.api_key`。勿自行读取配置或传入密钥。

## 参数

| 参数 | 约束与默认值 |
|---|---|
| `file` | 必填字符串；本地文件绝对路径，支持展开 `~`；文件非空且不超过 100 MiB |
| `path` | 必填字符串；输出 JSON 绝对路径，扩展名 `.json`，支持展开 `~` |
| `purpose` | `text_recognition` 或 `rich_layout_recognition`，默认 `text_recognition` |

支持 PDF、Word（DOC、DOCX）、PowerPoint（PPT、PPTX）及 PNG、JPEG、WebP、GIF、BMP、TIFF 等图片，格式按内容识别，最多 1000 页。加密、损坏或不支持的文件需先处理。

`text_recognition` 对不含图片的 Word、PowerPoint 直接提取文字，其他文件逐页识别；`rich_layout_recognition` 对所有文件逐页识别。

## 结果

退出码 `0`：读取 `stdout` JSON；`1`：按下节处理错误。`stderr` 为日志；无 JSON 时结合日志报告执行失败。

完整结果先保存为文件，`stdout` 仅返回元信息。按实际返回路径读取或交付文件，不为补读或交付重复请求。

### 标准输出

| 字段 | 用途 |
|---|---|
| `path` | 实际保存的 JSON 绝对路径 |
| `bytes` | 文件字节数 |
| `page_count` | 已保存的页数 |
| `incomplete_page_count` | `finish_reason=length` 的页面数；大于 0 时须说明页面不完整 |

### 结果文件

完整识别结果保存为 UTF-8 JSON，保留上游字段：

| 字段 | 用途 |
|---|---|
| `page_count` | 页数，与 `pages` 长度一致 |
| `pages[].index` | 从 0 开始的页码，按页顺序返回 |
| `pages[].text` | 页面原始文字，可能含版面标记 |
| `pages[].finish_reason` | `stop` 表示完整，`length` 表示达到输出上限，须说明页面不完整 |
| `usage` | 用量参考，不用于计费 |

逐页识别的文字可能含 `<｜ref｜>`、`<｜box｜>` 等标记。区域坐标为 `[[x1, y1, x2, y2]]`，原点在左上角，按页宽高归一化到 0–1000；转换为像素时，横坐标乘页宽、纵坐标乘页高，再除以 1000。直接提取的文字不含这些标记。

脚本创建父目录，重名时追加 `-1`、`-2`，不覆盖已有文件。只使用返回的实际路径；需要 TXT、Markdown 时从已保存的 JSON 本地导出。

## 按需读取

不要直接回显整个 JSON。按任务选取页码、关键词或文本范围；全文整理时分批读取，不重复识别。以下示例只读取第一页的前 2000 个字符，按需调整页码和范围：

```bash
/usr/bin/python3 - "/实际返回的路径/report-ocr.json" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as stream:
    result = json.load(stream)
for page in result["pages"][:1]:
    print(page["index"], page.get("finish_reason"))
    print(page["text"][:2000])
PY
```

读取范围外的内容仍在文件中；本地截取不代表上游识别不完整，以 `finish_reason` 为准。

## 错误处理

错误含 `code`、`message`，可附 `path`、`http_status`。HTTP 200 也可能识别失败，以进程退出码和 JSON 为准。

| `code` | 处理 |
|---|---|
| `invalid_request` | 按 `message` 检查参数、文件格式、大小、页数和路径 |
| `file_error` | 检查输入文件、输出目录与权限；保存失败不重新识别 |
| `ocr_failed` | 报告识别失败；若 `message` 明确提示识别超时，可建议缩小文件，不猜测其他原因 |
| `config_error` | 提示用户检查 `deepseek-api.api_key` 和配置文件，不读取或输出密钥 |
| `auth_failed` / `billing_error` | 提示用户处理密钥或余额问题 |
| `rate_limited` | 告知并发识别过多，可稍后再试 |
| `network_error` / `service_error` | 报告上传、网络或服务故障；上游仍可能完成识别 |
| `invalid_response` / `internal_error` | 停止调用并报告失败，不补写识别内容 |

失败不自动重试。识别按成功页计费，断开连接不会取消已提交的识别；完成后仍可能计费，勿直接重传同一文件。

## 来源

[OCR 指南](https://api-docs.deepseek.com/guides/ocr/) · [OCR 接口](https://api-docs.deepseek.com/api/ocr/) · [通用错误码](https://api-docs.deepseek.com/quick_start/error_codes/)
