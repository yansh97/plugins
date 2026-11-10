# 接口参考

## 搜索

`web_search` 参数：

| 参数 | 约束与默认值 |
|---|---|
| `query` | 必填，非空字符串，最多 128 字符 |
| `count` | 整数 1–50，默认 10 |
| `need_content` | 布尔值，默认 `false`；为每条结果附正文 |
| `ori_question` | 可选，用户原始问题，非空字符串，最多 128 字符 |

返回 JSON，`results` 为结果列表，无结果时为空。每项字段如下：

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

`web_fetch.url` 必填，为不含凭据的 HTTP(S) 绝对地址，最多 8192 字符。返回 JSON，包含 `title` 和 Markdown `content`，标题可能为空。

## 错误

失败时 `isError=true`，正文为含 `code`、`message` 的 JSON。`config_error` 附配置文件 `path`，HTTP 错误附 `http_status`；不返回原始上游错误或密钥。

| `code` | 含义 |
|---|---|
| `config_error` | 配置缺失或无效 |
| `invalid_request` | 请求不合法或被上游拒绝，含无法细分的 HTTP 4xx |
| `auth_failed` | 密钥或访问权限问题 |
| `billing_error` | 余额或额度不足 |
| `rate_limited` | 请求限流 |
| `network_error` | 连接失败或请求超时 |
| `service_error` | 上游服务故障（HTTP 5xx） |
| `invalid_response` | 上游响应不符合预期 |
| `internal_error` | 插件内部异常 |
