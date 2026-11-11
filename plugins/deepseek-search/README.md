# DeepSeek Search

搜索网页、抓取正文。

## 配置

配置文件见[仓库说明](../../README.md#配置)。在 [DeepSeek 平台](https://platform.deepseek.com)创建 API Key，填入 `deepseek-search.api_key`。

## 使用

直接描述需求，例如：

- “查找 Python 3.9 的官方发布说明，附上来源。”
- “阅读这个网页，提取安装步骤。”

## 默认与限制

搜索默认返回 10 条结果，请求直连 DeepSeek 官方接口，总时限 45 秒。网页地址不得携带凭据；登录墙或纯脚本页面可能无法读取。

参数、查询语法和返回见[接口参考](skills/deepseek-search/references/api.md)。
