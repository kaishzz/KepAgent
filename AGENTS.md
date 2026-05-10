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

### 示例文件同步

- 示例文件与真实文件必须在结构上保持一致
- 在这个仓库中，主要映射关系是 `.env.example` -> `.env` 和 `agent.example.yaml` -> `agent.yaml`
- YAML 示例文件必须暴露 `kepagent/config.py` 中支持的全部配置字段，便于运维人员直接从示例里发现可用变量
- 当新增或修改 Agent 配置字段时，要同时更新 `agent.example.yaml`，以及任何被忽略但配套的示例文件，例如 `agent.example2.yaml`
- 保持 `agent.example.yaml` 与 `agent.example2.yaml` 在顶层变量名、注释意图、字段顺序，以及重复服务器字段骨架上保持一致
- 同步 YAML 示例文件时，保留每个真实文件中的现有实际数据，例如服务器 key、容器名、分组、端口、挂载、标签、命令，以及各模式专属值
- 对可选字段，优先在 YAML 示例中写出显式空占位，而不是省略：空字符串使用 `""`，可空 Docker 选项使用 `null`，空列表使用 `[]`，空映射使用 `{}`
- 重复服务器示例在可行情况下应保持相同字段骨架，包括 `start_after_monitor`、`working_dir`、`network_mode`、`entrypoint`、`command`、`env`、`ports`、`volumes`、`labels`、`stdin_open`、`tty` 和 `restart_policy`
- 当示例文件新增 key、字段或变量时，把缺失项补到真实文件中
- 当示例文件移除废弃 key 或字段时，只有它们属于同一套镜像结构时，才从真实文件中移除对应项
- 当示例文件重命名或重构 key / 字段时，要把相同的名称与结构变更同步到真实文件，并尽可能保留用户已有的值
- 同步的是名称、结构和预期 key，不要在未明确要求的情况下覆盖已有的密钥、令牌、密码、URL、路径或其他用户数据
- 不要因为示例文件变化，就把真实值替换成示例占位值
- 除非用户明确要求，否则不要打印任何密钥内容
- 如果真实配置文件不存在，要说明这一点；除非任务明确需要，否则不要创建

### 完成检查

- 修改示例配置文件后，要明确检查对应的真实配置文件是否也已核对并同步
- 如果真实配置文件不存在，因此未能更新，要清楚说明
