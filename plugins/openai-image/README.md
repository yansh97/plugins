# OpenAI Image

生成、编辑图片，提供 `image-generation`、`image-edit` 工具和创作技能，输出本地 PNG/JPEG 文件。

## 配置

配置文件见[仓库说明](../../README.md#配置)。填写 `openai-image.api_key` 和 `openai-image.base_url`；地址须含版本前缀，如 `https://api.openai.com/v1`，无需追加具体接口路径。

## 使用

直接描述画面或编辑要求，例如：

- “生成一张 16:9 的陶瓷花瓶产品图，浅灰背景。”
- “把 images/vase.jpg 中的花瓶改成蓝色，保留光照和背景。”

模型按场景选择参数。目录默认系统下载目录，文件名按内容生成；用户指定项优先。MCP 自动创建目录，重名或多图追加序号，不覆盖文件。

## 默认与限制

生成默认 Flare、`1920x1440`；编辑默认 Sunburst，尽量保持主图尺寸。质量和复杂度均为 `high`，格式按场景选择 PNG 或 JPEG。

编辑需提供本地参考图和文字。接口须支持所用 2.6 模型及文件上传，不使用代理。请求总时限 600 秒，不自动重试；超时或断连时，上游可能已完成生成。

参数、尺寸和文件行为见[接口参考](skills/openai-image/references/api.md)。
