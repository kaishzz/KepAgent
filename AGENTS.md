## KepAgent 项目约定

以下规则适用于 `E:\GitHubProjects\KepRepository\KepAgent` 工作区

## 通用要求

- 在此项目中开始工作前，先阅读共享要求文件 `C:\Users\24854\.codex\AGENTS.md`
- 将 `C:\Users\24854\.codex\AGENTS.md` 视为本项目的通用基线指令集
- 先应用共享要求，再应用本文件中的 KepAgent 专属规则
- 如果共享要求与本文件中的明确规则冲突，在 `E:\GitHubProjects\KepRepository\KepAgent` 内工作时以本文件为准
- 不要在这里重复共享基线内容；`C:\Users\24854\.codex\AGENTS.md` 是唯一事实来源

### 规范路径

- 本仓库在本地的规范路径是 `E:\GitHubProjects\KepRepository\KepAgent`
- 如果其他工作区提到 `KepAgent`，除非用户明确说明，否则默认指这个路径

### 配套网站仓库

- 配套的网站 / 控制平面仓库是 `E:\GitHubProjects\WebSite\kepcs.kaish.cn`
- 当任务涉及网站与 Agent 集成、RCON 流程、命令 payload 格式、审计展示，或共享配置预期时，直接检查该网站仓库，不要再次询问路径
- 如果某个 KepAgent 变更依赖网站侧变更，除非用户明确要求仅修改 Agent，否则默认按跨仓库任务处理

### 私有配置文件

- 本仓库公开仅用于源码托管和 GitHub Actions 自动构建，不维护公开 YAML 配置示例
- 本地运行默认读取 `config.yaml`
- `config.yaml`、`config1.yaml`、`config2.yaml` 都属于本地私有配置，必须保持忽略，不要提交或推送
- 如果新增或修改 `internal/config/config.go` 中的配置字段，要检查本地存在的 `config.yaml`、`config1.yaml`、`config2.yaml` 是否需要同步结构
- 同步本地私有配置时，保留现有实际数据，例如服务器 key、容器名、分组、端口、挂载、标签、命令，以及各模式专属值
- 同步的是名称、结构和预期 key，不要在未明确要求的情况下覆盖已有的密钥、令牌、密码、URL、路径或其他用户数据
- 除非用户明确要求，否则不要打印任何密钥内容
- 如果对应私有配置文件不存在，要说明这一点；除非任务明确需要，否则不要创建

### 完成检查

- 修改配置结构后，要明确检查本地私有配置文件是否存在并已核对
- 如果私有配置文件不存在，因此未能更新，要清楚说明
