# 开发约定

## 结构与配置

仓库为 ZCode 插件市场 `yansh97/plugins`，市场名为 `yansh97`。清单与启动配置遵循 [ZCode 插件文档](https://zcode.z.ai/cn/docs/plugin)，技能遵循 [ZCode 技能文档](https://zcode.z.ai/cn/docs/skill)。

根目录 `marketplace.json` 登记插件，各插件可独立安装：

```text
plugins/<name>/
├── .zcode-plugin/plugin.json
├── mcp/server.py
├── skills/<name>/
│   ├── SKILL.md
│   └── references/api.md
└── README.md
```

- 使用推荐的 `.zcode-plugin/plugin.json`，只声明已有组件：`skills` 指向 `./skills/`，`mcpServers` 内联声明服务。
- 市场条目的 `source` 相对仓库根目录；清单中的组件路径相对插件根目录。仅使用官方文档列出的字段。
- 同类配置保持相同的字段和条目顺序；市场条目与插件清单均以 `name`、`version`、`description` 开头，其后放各自的来源、元数据和组件声明。插件顺序统一为 DeepSeek Search、OpenAI Image；MCP 启动字段依次为 `type`、`command`、`args`、`timeoutMs`。
- MCP 服务使用 `type: stdio`、`/usr/bin/python3` 和 `${ZCODE_PLUGIN_ROOT}/mcp/server.py`。脚本和图片路径均为绝对路径，无需设置 `cwd`。
- `timeoutMs` 分别为 DeepSeek Search 的 60000、OpenAI Image 的 720000，长于请求总时限 45、600 秒；启动超时采用 ZCode 默认值。
- MCP 服务键名由 ZCode 自动加命名空间，无需手动添加前缀。技能放在 `skills/<名>/SKILL.md`，不使用嵌套分组；frontmatter 必须含 `name` 和具体的 `description`，描述不超过 1024 字符。

## 实现

- 仅支持 macOS，启动时检查平台，其他系统向 `stderr` 提示并退出。兼容 Python 3.9，仅用标准库。服务自包含于 `mcp/server.py`，不依赖 pip、虚拟环境、工作目录、脚本位置或其他插件；允许少量重复。
- 按参数校验、请求、文件处理和协议分派划分函数。工具定义与服务信息内联，处理函数显式映射，启动时核对一致性，不读取插件清单。
- 服务在主线程用 `SIGALRM` / `setitimer` 限制请求总耗时，覆盖上传准备和网络等待；结束或异常时清理定时器。图片保存位于请求计时之外，宿主超时须留出保存余量。
- 参数定义与运行时校验一致，覆盖类型、长度、枚举、范围和组合限制；服务补全默认值。
- `stdout` 每行一条 JSON-RPC 消息，日志写入 `stderr`。无效消息不终止服务，通知不返回响应；不记录密钥、上传内容或原始上游错误。
- 配置只读 `Path.home() / ".config/agents/config.json"`，按插件 ID 分组，每次调用重新读取。启动和列出工具无需配置；字段与 `config.example.json` 同步，示例密钥留空。
- 配置路径固定，不读取 `XDG_CONFIG_HOME`，不提供 `userConfig`、环境变量、命令行配置或代理入口；其余值使用常量或内部逻辑。密钥不进入工具参数、`args`、URL 或仓库。
- MCP 定义参数并执行约束；技能选择参数、澄清需求，合理默认值直接使用。图片目录与文件名由模型确定，MCP 接收绝对路径并处理重名序号。

## 错误处理

- 错误按模型的后续操作分类：修正请求、换用来源、处理账户和等待重试应能区分，处理方式相同则合并。
- 上游错误先按 HTTP 状态码分类，无法细分的 4xx 归为 `invalid_request`，5xx 归为 `service_error`；正文仅辅助识别明确的错误码，缺失或解析失败不影响兜底。不匹配错误文案，不堆叠推测的格式或别名。
- 修改映射前核实对应接口的官方文档，不跨服务套用同一状态的含义；来源链接集中维护在各插件的 `references/api.md`。
- 保留 `code` 和简短 `message`，按需附 `http_status`、相关 `path` 或已保存文件 `saved`；不返回原始上游错误、过滤细节或与后续操作无关的诊断字段。
- 服务不自动重试。图片请求超时或断连不代表上游未生成，技能也不得自动重试；检索技能可对临时故障有限重试。

## 文案

- 使用简体中文，中文与英文、数字间留空格，代码标识加反引号；代码注释和错误消息使用简洁英文。
- 沿用简短、直接的句式和具体用词；相同行为使用相同表述，精简措辞不改变默认值、限制或流程。
- 展示名为 DeepSeek Search、OpenAI Image。插件 ID、工具 ID、配置键和模型名保持稳定。
- 插件清单与市场条目的 `name`、`version`、`description` 保持一致。
- 每份文档只保留目标读者完成任务所需的信息；同一规则集中维护，通过链接引用，必要的默认行为摘要可保留。
- 根 README 面向用户，说明插件列表、运行要求、ZCode 安装入口和共用配置；插件 README 说明功能、配置项、使用示例、默认行为和限制，不重复客户端教程或内部实现。
- AGENTS 面向开发者，说明结构、实现原则、验证和发布；SKILL 面向调用模型，写决策、流程和失败处理；`references/api.md` 写工具参数、约束和返回，不复述上游请求实现或历史取舍。
- 保留规范文件名；其他文档使用简短的小写英文名，多词用连字符。

## 验证与发布

```bash
ruff check plugins
ruff format --check plugins
ty check plugins
```

本项目不维护测试文件。行为验证在仓库外使用临时文件，完成后删除；不默认读取真实配置或发起付费调用。

在插件目录列出工具，无需配置或网络：

```bash
printf '%s\n' '{"jsonrpc":"2.0","id":1,"method":"tools/list"}' \
  | /usr/bin/python3 mcp/server.py
```

- 离线检查清单与市场字段、组件路径、技能格式、示例配置、默认值、超时、配置热更新、错误分类和平台检查；将脚本单独复制到临时目录，在空工作目录下验证不依赖原路径。
- 客户端联调验证安装、技能发现、配置读取、工具注册和启停，确认每个插件只注册一组 MCP 工具。
- 同一批未提交改动只递增一次版本，中途修改不重复递增；插件清单、`SERVER_INFO`、市场条目和根 README 的版本保持同步。新增插件登记 `marketplace.json`。
- 客户端联调时刷新市场并更新安装副本，必要时重启客户端。不自动提交或发布；交付时区分离线验证、客户端联调和上游调用。
