# DeepSeek Search

搜索网页、抓取正文，提供 `web_search`、`web_fetch` 工具和检索技能。

## 配置

配置文件见[仓库说明](../../README.md#配置)。在 [DeepSeek 平台](https://platform.deepseek.com)创建 API Key，填入 `deepseek-search.api_key`。

## 使用

直接描述需求，例如：

- “查找 Python 3.9 的官方发布说明，附上来源。”
- “阅读这个网页，提取安装步骤。”

## 默认与限制

搜索默认返回 10 条摘要，需要全文时再抓取。接口地址固定，不使用代理；请求总时限 45 秒，不自动重试。抓取 URL 不得携带凭据，登录墙或纯脚本页面可能无法读取。

参数、查询语法和返回见[接口参考](skills/deepseek-search/references/api.md)。
