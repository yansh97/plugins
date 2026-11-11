# yansh97/plugins

ZCode 插件市场，提供技能和 MCP 工具，每个插件可独立安装。

仅支持在 macOS 上运行，需有 `/usr/bin/python3`（Python 3.9 或以上），无需安装第三方 Python 包。远程工作区的插件运行主机也须为 macOS。

## 插件

| 插件 | 版本 | 功能 |
|---|---|---|
| [DeepSeek Search](plugins/deepseek-search/README.md) | 0.1.2 | 网页搜索与正文抓取 |
| [OpenAI Image](plugins/openai-image/README.md) | 0.1.2 | 图片生成与编辑 |

## 安装与使用

在 ZCode 的「设置 → 插件 → 创建 → 添加插件市场」中填入仓库地址 `https://github.com/yansh97/plugins`，或本地仓库的绝对路径，然后安装并启用所需插件。

配置密钥后直接描述需求，也可输入 `$` 或 `/` 选择技能。插件的 MCP 服务随启停自动加载，无需手动添加。操作说明见 [ZCode 插件文档](https://zcode.z.ai/cn/docs/plugin)。

插件升级时先刷新市场源，再在「管理已安装」中更新。

## 配置

所有插件共用 `~/.config/agents/config.json`。在仓库目录复制[示例配置](config.example.json)，已有文件会保留：

```bash
mkdir -p ~/.config/agents
cp -n config.example.json ~/.config/agents/config.json
chmod 600 ~/.config/agents/config.json
```

按各插件说明填写所需配置，其余配置段可删除；已有文件请手动合并对应配置段。

文件使用 UTF-8 JSON，不支持注释或尾随逗号。插件仅读取此文件，修改后下次调用生效，无需重启；真实密钥不要写入仓库。
