# AGENTS.md

适用路径：`E:\GitHubProjects\KepRepository\KepAgent`

### 先读

1. `C:\Users\24854\.codex\AGENTS.md`
2. 本 `AGENTS.md` 文件
3. 与任务直接相关的源码、测试和部署文件

### 仓库边界

- KepAgent：`E:\GitHubProjects\KepRepository\KepAgent`
- 配套网站仓库：`E:\GitHubProjects\WebSite\kepcs.kaish.cn`
- 涉及网站与 Agent 集成、RCON 流程、命令 payload、审计展示或共享配置预期时，直接检查 `kepcs.kaish.cn` 仓库，不要再次询问路径
- 如果 Agent 变更依赖网站侧变更，除非用户明确要求仅修改 Agent，否则默认按跨仓库任务处理

### 硬规则

- 改前先确认入口、调用链、配置来源、部署影响
- 不靠记忆判断命令格式、字段名、审计展示或网站侧预期；用代码、测试和跨仓库实现确认

### 配置与部署

- 本地运行默认读取 `config.yaml`
- `config.yaml` 属于本地私有配置，必须保持忽略，不提交、不推送、不打印密钥
- 如果新增或修改 `internal/config/config.go` 中的配置字段，要检查本地 `config.yaml` 是否需要同步结构
- 同步私有配置时，只同步名称、结构和预期 key，不覆盖已有真实值
- 除非用户明确要求，否则不要创建公开 YAML 配置示例

### 验证与收尾

- 改 Go 代码后，至少运行受影响范围测试；无更小范围时运行 `go test ./...`
- 改配置结构后，要明确说明是否已检查本地 `config.yaml`
- 如果任务涉及跨仓库契约，要明确说明是否同时核对了 `kepcs.kaish.cn`
- 最后回复要写清：改了哪些模块、跑了哪些验证、是否涉及 `config.yaml`、是否跨仓库核对、是否 commit
