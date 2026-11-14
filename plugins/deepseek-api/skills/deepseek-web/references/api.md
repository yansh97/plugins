# 接口参考

## 调用

仅支持 macOS。将示例路径替换为本技能 `scripts/run.py` 的绝对路径，无需设置 `cwd`。操作名是唯一命令参数；通过 `stdin` 传入 UTF-8 JSON 对象，以 EOF 结束。

```bash
/usr/bin/python3 "/本技能的绝对路径/scripts/run.py" search <<'JSON'
{"query":"Python 3.9 官方发布说明","count":5}
JSON
```

```bash
/usr/bin/python3 "/本技能的绝对路径/scripts/run.py" fetch <<'JSON'
{"url":"https://example.com"}
JSON
```

`--help` 离线列出操作和参数，无需配置。仅接受下表字段，字符串不得为空或全为空白；脚本补全默认值。请求总时限为 45 秒，包含请求准备和网络等待。

配置由脚本读取：`~/.config/agents/config.json` → `deepseek-api.api_key`。勿自行读取配置或传入密钥。

## 参数

### `search`

| 参数 | 约束与默认值 |
|---|---|
| `query` | 必填字符串，最多 128 字符 |
| `count` | 整数 1–50，默认 10 |
| `need_content` | 布尔值，默认 `false`；为每条结果附正文 |
| `ori_question` | 可选字符串，用户原始问题，最多 128 字符 |

查询语法：`site:example.com` 限定站点，多个站点用 `OR`；`-词`、`-site:example.com` 排除内容；`"精确短语"` 精确匹配。`lang:zh,en` 限定语言，`after:2025-01-01`、`before:2026-06-01` 限定发布日期。多个词或短语用空格分隔。

### `fetch`

| 参数 | 约束与默认值 |
|---|---|
| `url` | 必填字符串，最多 8192 字符；HTTP(S) 绝对地址，不含用户名或密码 |

## 结果

退出码 `0`：读取 `stdout` JSON；`1`：按下节处理错误。`stderr` 为日志；无 JSON 时结合日志报告执行失败。

两个操作均直接返回 JSON，不保存文件。预计正文较长时，执行前将 `stdout` 重定向到临时 JSON 文件，再按需读取；勿重复请求以补读结果。

### `search`

返回 `results[]`，为空表示无结果。每项字段：

| 字段 | 用途 |
|---|---|
| `title` | 标题 |
| `url` | 来源链接 |
| `summary` | 完整摘要，优先使用 |
| `snippet` | 简短摘要 |
| `content` | 正文；未请求或不可用时为空 |
| `rerank_score` | 相关性 0–3，≥1.5 为优质 |
| `authority` | 权威度 0–4，≥3 为高权威，-1 为未知 |
| `date` | RFC 3339 UTC 时间，`null` 表示未知 |

### `fetch`

返回字段：

| 字段 | 用途 |
|---|---|
| `title` | 标题，可能为空 |
| `content` | Markdown 正文 |

## 错误处理

错误含 `code`、`message`，可附 `path`、`http_status`。按 `code` 处理，不推测原因。

| `code` | 处理 |
|---|---|
| `invalid_request` | 按 `message` 修正参数或 URL |
| `fetch_failed` | 换用其他来源，不重试原地址 |
| `config_error` | 提示用户检查 `deepseek-api.api_key` 和配置文件，不读取或输出密钥 |
| `auth_failed` / `billing_error` | 提示用户处理密钥或余额问题 |
| `rate_limited` / `network_error` / `service_error` | 等待后最多重试一次，再次失败即停止 |
| `invalid_response` / `internal_error` | 停止调用并报告失败，不用记忆补写结果 |

## 来源

[Search / Fetch 指南](https://api-docs.deepseek.com/guides/search/#errors) · [搜索接口](https://api-docs.deepseek.com/api/search/) · [抓取接口](https://api-docs.deepseek.com/api/fetch/)
