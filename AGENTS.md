# 开发指南

本仓库维护 ZCode、Codex 插件市场 `yansh97`。清单遵循 [ZCode 插件文档](https://zcode.z.ai/cn/docs/plugin)和 [OpenAI 插件文档](https://developers.openai.com/plugins/build/plugins)，技能遵循 [ZCode 技能文档](https://zcode.z.ai/cn/docs/skill)和 [OpenAI 技能文档](https://developers.openai.com/plugins/build/skills)。

## 目录与清单

```text
marketplace.json
.agents/plugins/marketplace.json
config.example.json
README.md
plugins/<plugin>/
├── .zcode-plugin/plugin.json
├── .codex-plugin/plugin.json
└── skills/<skill>/
    ├── SKILL.md
    ├── scripts/run.py
    └── references/api.md
```

- ZCode 市场使用根目录 `marketplace.json`，Codex 市场使用 `.agents/plugins/marketplace.json`；均按 DeepSeek API、OpenAI API 的顺序登记插件，每个插件可独立安装。
- ZCode 插件清单使用 `.zcode-plugin/plugin.json`，Codex 使用 `.codex-plugin/plugin.json`；两者的 `skills` 均指向 `./skills/`，Codex 展示信息放在 `interface`。仅声明已有组件，不注册 MCP 服务。
- ZCode 市场条目的 `source` 和 Codex 市场条目的 `source.path` 均相对仓库根目录，组件路径相对插件根目录。仅使用官方文档列出的清单字段。
- 两种插件清单与 ZCode 市场条目均以 `name`、`version`、`description` 开头，对应值保持一致；其后放来源、元数据和组件声明。Codex 市场条目仅登记名称、来源、策略和分类，版本与描述由插件清单提供。同类配置保持相同的字段顺序。
- 插件按厂商组织，技能按任务关联性和说明规模分组。DeepSeek API 包含 `deepseek-web`、`deepseek-ocr`、`deepseek-audio`，OpenAI API 包含 `openai-image`；同一插件内的技能共用配置。
- 技能直接放在 `skills/<skill>/SKILL.md`，不嵌套分组。Frontmatter 必须包含 `name` 和具体的 `description`，描述不超过 1024 字符。

## 脚本约定

- 仅支持 macOS，兼容 Python 3.9，只使用标准库。启动时检查平台，其他系统向 `stderr` 提示并以退出码 `1` 退出。
- 每个技能的脚本自包含于 `scripts/run.py`，不依赖 pip、虚拟环境、工作目录、脚本位置或其他插件；允许少量重复代码。
- 技能根据实际加载目录定位脚本，使用 `/usr/bin/python3` 和脚本绝对路径执行，不依赖环境变量或设置 `cwd`。输入、输出文件均使用绝对路径。
- 按参数校验、请求、文件处理和命令入口划分函数。操作名使用 `search`、`fetch`、`ocr`、`asr`、`tts`、`generate`、`edit`。参数定义内联，处理函数显式映射，启动时核对操作与定义是否一致，不读取插件清单。
- 参数按操作和字段定义扁平规则，供校验和 `--help` 共用，覆盖类型、必填、默认值、长度、枚举和范围。脚本补全默认值，组合限制在业务函数中校验；校验范围限于接口约束和本地操作要求。
- 操作名是唯一命令参数，业务参数通过 `stdin` 传入 UTF-8 JSON 对象，读到 EOF 后处理。每次运行只执行一次，`stdout` 输出一个 UTF-8 JSON 结果，使用 `ensure_ascii=False` 保留非 ASCII 字符，日志写入 `stderr`；成功退出码为 `0`，失败为 `1`。
- `--help` 列出操作及参数定义，无需配置或网络。日志不得包含密钥、上传内容或原始上游错误。
- 采用完整输入、完整结果的调用方式。TTS 收齐 HTTP 音频后再保存，OCR 允许上游保活空白；不提供 WebSocket、SSE 或实时流式交互。
- 文件类操作先保存完整结果，`stdout` 仅返回实际路径、大小及操作相关元信息，不回显文件内容。技能按任务读取、预览或交付已保存文件，不为补读或交付重复请求。网页操作直接返回 JSON。
- 技能负责参数选择和必要澄清，合理默认值直接使用。用户指定的保存位置和文件名优先，其余由模型确定；脚本负责处理重名序号。
- 产物命名遵循 README 的[结果与文件](README.md#结果与文件)约定，技能给出对应操作的命名示例。默认目录和文件名由技能确定并通过 `path` 传入；脚本不推导默认路径、不规范化用户文件名。

请求总时限在主线程通过 `SIGALRM` / `setitimer` 实现，覆盖上传准备和网络等待。结束或异常时清理定时器，结果保存不计入请求时限。技能命令提前返回进程标识时，继续等待同一进程。

| 技能 / 操作 | 脚本请求时限 | 技能命令总时限至少 |
|---|---|---|
| DeepSeek Web | 45 秒 | 60 秒 |
| DeepSeek OCR | 210 秒 | 240 秒 |
| DeepSeek Audio / ASR | 660 秒 | 720 秒 |
| DeepSeek Audio / TTS | 1800 秒 | 1860 秒 |
| OpenAI Image | 600 秒 | 720 秒 |

## 配置约定

- 仅从 `Path.home() / ".config/agents/config.json"` 读取配置，每次调用重新读取。配置按插件 ID 分组，分组名与插件清单的 `name` 一致。
- 配置字段与 `config.example.json` 保持同步，示例密钥留空。其余运行值使用常量或内部逻辑。
- 不读取 `XDG_CONFIG_HOME`，不提供 `userConfig`、环境变量、命令行配置或代理入口。密钥不得进入业务参数、命令行、URL 或仓库。

## 错误处理

- 按后续处理方式区分错误：修正请求、换用来源、处理账户或等待重试。处理方式相同的错误使用同一分类。
- 上游错误优先按 HTTP 状态码分类，无法细分的 4xx 归为 `invalid_request`，5xx 归为 `service_error`。正文仅用于识别文档中明确列出的错误码，缺失或解析失败时仍须按状态码兜底。不匹配错误文案，不添加推测的格式或别名。
- 修改错误映射前，核对对应接口的官方文档，不跨服务套用同一状态码的含义。来源链接集中维护在各技能的 `references/api.md`。
- 返回 `code` 和简短的 `message`，按需附加 `http_status`、相关 `path` 或已保存文件 `saved`。不返回原始上游错误、过滤细节或与后续处理无关的诊断字段。
- 脚本不自动重试。OCR、音频和图片请求超时或断连后，上游仍可能完成处理，技能也不得自动重试；网页检索遇到临时故障时可有限重试。

## 文档约定

- 使用简体中文，中文与英文、数字之间留空格，代码标识使用反引号。代码注释和错误消息使用简洁英文。
- 使用简短、直接的句式和具体用词，相同行为使用相同表述。精简文案时保留默认值、限制和流程。
- 参数与响应字段优先使用表格，每行说明一个字段；调用决策、组合约束和处理流程使用短文。
- SKILL 按何时调用、调用、参数选择、保存、结果、失败组织；无文件产物时省略保存。接口参考按调用、参数、结果、错误处理、来源组织，尺寸与按需读取等内容按需插入。
- 插件展示名为 DeepSeek API、OpenAI API；技能展示名为 DeepSeek Web、DeepSeek OCR、DeepSeek Audio、OpenAI Image。保持插件 ID、操作名、配置键和模型名稳定。
- 用户文档集中维护在根 README，不单独维护插件 README。同一规则集中说明，通过链接引用，必要时保留默认行为摘要。
- 技能用于补充 ZCode、Codex 的能力。README 与 SKILL 写明内置能力的优先级、无需调用的条件和适用的本地替代方式；按当前会话实际可用的工具判断，尊重用户指定的工具、模型和交付格式。
- 保留规范文件名，其他文档使用简短的小写英文名，多词用连字符。

| 文件 | 读者与内容 |
|---|---|
| `AGENTS.md` | 开发者：目录、实现约定、验证和发布流程 |
| `README.md` | 用户：插件列表、运行要求、两种客户端的安装、共用配置、使用示例、默认行为和限制 |
| `SKILL.md` | 调用模型：参数选择、执行流程和失败处理 |
| `references/api.md` | 调用模型：脚本调用、参数、约束和返回；不复述上游请求实现或历史取舍 |

## 验证

在仓库根目录运行静态检查：

```bash
ruff check plugins
ruff format --check plugins
ty check plugins
```

本项目不维护测试文件。行为验证使用仓库外的临时文件，完成后删除；默认不读取真实配置，不发起付费调用。

以下命令列出操作及参数定义，无需配置或网络：

```bash
/usr/bin/python3 plugins/deepseek-api/skills/deepseek-web/scripts/run.py --help
/usr/bin/python3 plugins/deepseek-api/skills/deepseek-ocr/scripts/run.py --help
/usr/bin/python3 plugins/deepseek-api/skills/deepseek-audio/scripts/run.py --help
/usr/bin/python3 plugins/openai-api/skills/openai-image/scripts/run.py --help
```

- 离线检查两套清单与市场字段、组件路径、技能格式、示例配置、参数与默认值、超时、配置更新、错误分类、平台检查、标准输入输出和退出码；核对文档中的文件名，并验证重名和多图编号。
- 将脚本单独复制到临时目录，在空工作目录下运行，确认不依赖原路径，并覆盖含空格的脚本路径。
- 两种客户端分别联调。联调前刷新市场并更新安装副本，必要时重启客户端；检查安装、技能发现、脚本定位、配置读取和长请求等待。
- 交付时分别说明离线验证、客户端联调和上游调用的完成情况。

## 版本与发布

- 各插件独立管理版本，技能和脚本随所属插件发布，不单独编号。
- 同一批未提交改动只递增一次版本，中途修改不重复递增。两种插件清单、ZCode 市场条目和根 README 的版本保持同步，脚本不维护版本常量。
- 新增插件时登记两份市场清单。未经用户要求，不提交或发布。
