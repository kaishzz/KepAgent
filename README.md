# KepAgent

KepAgent 是一个运行在 Linux 节点上的 CS2 Docker 管理 Agent，用于连接控制平面、上报节点状态，并执行服务器启停、分组控制、RCON 指令和版本更新检查。

## 当前功能

- Agent 鉴权与节点注册校验
- 心跳上报与节点状态摘要
- 命令轮询、领取、执行和结果回传
- Docker 单服启动、停止、重启、删除
- Docker 分组批量启动、停止、重启
- RCON 指令下发
- CS2 buildid 本地 / 远端版本检查
- 更新后校验与监控服稳定性检查

## 项目结构

```text
.
|-- kepagent/
|   |-- api.py
|   |-- app.py
|   |-- config.py
|   `-- runtime.py
|-- .env.example
|-- agent.example.yaml
|-- main.py
`-- requirements.txt
```

## 配置方式

### 1. 准备环境变量

复制 `.env.example` 为 `.env`，填写敏感配置：

```bash
cp .env.example .env
```

需要配置的变量：

- `KEPAGENT_API_BASE_URL`：控制平面地址
- `KEPAGENT_API_KEY`：Agent API Key
- `KEPAGENT_RCON_PASSWORD`：RCON 密码

`.env` 已加入 `.gitignore`，不会被提交到仓库。

### 2. 准备节点配置

复制 `agent.example.yaml` 为 `agent.yaml`，填写服务器定义、分组、挂载目录、端口和监控参数：

```bash
cp agent.example.yaml agent.yaml
```

`agent.yaml` 中的敏感字段会自动从 `.env` 读取。

## 已实现命令

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
- `node.check_update_monitor`
- `node.check_update_start`
- `node.get_oldver`
- `node.get_nowver`
- `node.monitor_check`
- `node.monitor_start`

## 运行

```bash
python -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
python main.py --config agent.yaml
```
