# Plugins

个人插件市场，提供技能和 MCP 工具。

当前启动配置适用于 macOS，使用 `/usr/bin/python3`，无需第三方 Python 包。

## 插件

| 插件 | 版本 | 功能 |
|---|---|---|
| [DeepSeek Search](plugins/deepseek-search/README.md) | 0.1.0 | 网页搜索与正文抓取 |
| [OpenAI Image](plugins/openai-image/README.md) | 0.1.0 | 图片生成与编辑 |

## 配置

所有插件共用 `~/.config/agents/config.json`。在仓库目录复制[示例配置](config.example.json)，已有文件会保留：

```bash
mkdir -p ~/.config/agents
cp -n config.example.json ~/.config/agents/config.json
chmod 600 ~/.config/agents/config.json
```

填写所用插件的配置，其余配置段可删除：

| 配置段 | 必填字段 |
|---|---|
| `deepseek-search` | `api_key` |
| `openai-image` | `api_key`、`base_url`；地址须含版本前缀，如 `/v1` |

文件使用 UTF-8 JSON，不支持注释或尾随逗号。修改后无需重启；不接受环境变量或客户端配置覆盖。真实密钥不要写入仓库。

使用示例和默认行为见各插件说明，开发约定见 [AGENTS.md](AGENTS.md)。
