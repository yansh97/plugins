# 接口参考

## 请求

配置见[插件说明](../../../README.md#配置)。地址固定为 `https://api.deepseek.com`，搜索和抓取分别使用 `/v1/tools/search`、`/v1/tools/fetch`。请求总时限 45 秒，不使用代理，不自动重试。

## 搜索

`web_search` 参数：

| 参数 | 约束与默认值 |
|---|---|
| `query` | 必填，非空字符串，最多 128 字符 |
| `count` | 整数 1–50，默认 10 |
| `need_content` | 布尔值，默认 `false`；为每条结果附正文 |
| `ori_question` | 可选，用户原始问题，非空字符串，最多 128 字符 |

返回 API 原始 JSON，`results` 中的字段如下：

| 字段 | 说明 |
|---|---|
| `title` / `url` | 标题与链接 |
| `site_name` / `site_icon` | 站点名称与图标 |
| `snippet` | 简短摘要，约 100–200 字符 |
| `summary` | 完整摘要，约 600–800 字符 |
| `rerank_score` | 相关性 0–3，≥1.5 为优质 |
| `authority` | 权威度 0–4，≥3 为高权威，-1 为未知 |
| `date` | RFC 3339 UTC 时间，`null` 表示未知 |
| `content` | `need_content=true` 时附加的正文 |

### 查询语法

- `site:example.com`：限定站点，多个站点用 `OR` 连接。
- `-词`、`-site:example.com`：排除词或站点；`"精确短语"`：精确匹配。
- `lang:zh`：限定语言，多种语言用逗号分隔，如 `lang:zh,en`。
- `after:2025-01-01`、`before:2026-06-01`：限定发布日期。
- 多个词或短语用空格分隔。

## 抓取

`web_fetch.url` 必填，为不含凭据的 HTTP(S) 绝对地址，最多 8192 字符。返回 API 原始 JSON，包含 `title` 和 Markdown `content`，标题可能为空。

## 错误

失败时 `isError=true`，正文为含 `code`、`message` 的 JSON。`config_error` 附配置文件 `path`；HTTP 错误附可用的 `http_status`、`request_id`、`upstream_code`、`upstream_type`、`upstream_param`。不返回配置内容、原始上游错误正文或密钥。
