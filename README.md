# KepAgent 0.4.0

KepAgent 是部署在 Linux 节点上的执行端 Agent，负责和 KepCs 控制平面对接、上报节点状态，并执行 Docker、RCON、更新维护与崩溃检查命令。

配套控制平面仓库：

- `E:\GitHubProjects\WebSite\kepcs.kaish.cn`

## 当前标识约定

`agent.example.yaml` 当前使用的服务器标识和容器命名规则如下：

- 训练服键值：`ze_xl_1` 到 `ze_xl_6`
- 跑图服键值：`ze_pt_1` 到 `ze_pt_6`
- 测试服键值：`ze_xl_test`、`ze_pt_test`
- 训练服容器：`kepcs-ze-xl-<port>`
- 跑图服容器：`kepcs-ze-pt-<port>`
- 测试服容器：`kepcs-ze-xl-test-<port>`、`kepcs-ze-pt-test-<port>`
- 默认分组：`all`、`ze_xl`、`ze_pt`、`test`

## 当前职责

- 使用 Agent API Key 与控制平面鉴权
- 定时心跳上报节点信息
- 轮询、领取、执行并回传节点命令
- 回传执行日志和结果摘要
- 管理 CS2 Docker 容器
- 按单个 `key` 或批量 `serverKeys` 处理服务器启动、停止、重启和删除命令
- 发送 RCON 命令
- 执行 `steamcmd app_update ... validate`
- 执行单监控服或多模式崩溃检查，并在对应模式检查成功后启动该模式配置的服务器

## 支持命令

- `agent.ping`
- `docker.list_servers`
- `docker.start_server`
- `docker.stop_server`
- `docker.restart_server`
- `docker.remove_server`
- `docker.start_group`
- `docker.stop_group`
- `docker.restart_group`
- `node.kill_all`
- `node.rcon_command`
- `node.check_update`
- `node.check_validate`
- `node.get_oldver`
- `node.get_nowver`
- `node.monitor_check`
- `node.monitor_start`

## 配置

### 1. 环境变量

```bash
cp .env.example .env
```

常用变量：

- `KEPAGENT_API_BASE_URL`
- `KEPAGENT_API_KEY`
- `KEPAGENT_RCON_PASSWORD`

### 2. 节点配置

```bash
cp agent.example.yaml agent.yaml
```

`agent.yaml` 当前负责描述：

- 服务器键值与容器名
- 镜像、端口、挂载、环境变量
- 分组与分组显示名
- `monitor_server_key`
- RCON 兜底密码
- 监控轮询、稳定时长和恢复超时
- `monitor_profiles` 多模式监控配置

多模式崩溃检查示例：

```yaml
monitor_profiles:
  - key: "ze_xl"
    monitor_server_key: "ze_xl_1"
  - key: "ze_pt"
    monitor_server_key: "ze_pt_1"

servers:
  - key: "ze_xl_test"
    groups: ["test"]
    start_after_monitor: false
```

RCON 密码优先级：

1. 控制平面命令 payload 透传
2. `servers[].rcon_password`
3. 全局 `KEPAGENT_RCON_PASSWORD`

## 运行

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
python3 main.py --config agent.yaml
```

查看版本：

```bash
python3 main.py --version
```

## 关键行为

- `node.check_update`：先尝试比对本地和远端 buildid；如果本地没有 manifest，会先打印“没有 manifest”并直接进入停服、`validate`、崩溃检查和启动流程
- `node.check_validate`：直接停服并执行 `validate`；如果本地没有 manifest，会先打印“没有 manifest”再继续
- `docker.start_server`、`docker.stop_server`、`docker.restart_server`、`docker.remove_server`：支持 `payload.key` 单服执行，也支持 `payload.serverKeys` 批量执行并返回汇总结果；重启会先强制删除容器再按配置重新创建启动
- `node.monitor_check`：只运行崩溃检查，不自动启动其它服务器；配置了 `monitor_profiles` 时会按 profile 逐个检查
- `node.monitor_start`：监控通过后启动 YAML 中配置的目标；配置了 `monitor_profiles` 时各模式独立检查，某个模式失败只会阻止该模式启动，不影响其它已通过模式
- `monitor_profiles[].monitor_server_key` 指定该模式用于崩溃检查的服务器，例如 `ze_xl_1`、`ze_pt_1`
- `monitor_profiles[].start_server_keys` 可显式指定该模式检查成功后启动哪些服务器；不填写时默认启动同名分组里 `start_after_monitor: true` 的服务器
- `servers[].start_after_monitor: false` 可让测试服或备用服不参与自动启动

## 测试

```powershell
py -3 -m unittest discover -s tests
```
