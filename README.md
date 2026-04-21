# KepAgent 0.4.0

KepAgent 是部署在 Linux 节点上的执行端 Agent，负责连接 `KepCs` 控制平面、上报节点心跳，并执行 Docker、RCON、版本维护与监控类命令。

配套控制平面仓库位于：

- `E:\GitHubProjects\WebSite\kepcs.kaish.cn`

## 当前职责

- 使用 `X-Agent-Key` / `Authorization: Bearer` 与控制平面鉴权
- 周期性心跳上报
- 轮询、领取、执行并回传命令结果
- 回传命令执行日志
- 管理 CS2 Docker 容器
- 执行 RCON 命令
- 执行 `steamcmd app_update ... validate`
- 执行更新后的崩溃检查，并按命令参数启动指定服务器

## 仓库结构

```text
.
|-- kepagent/
|   |-- api.py
|   |-- app.py
|   |-- config.py
|   |-- constants.py
|   `-- runtime.py
|-- .env.example
|-- agent.example.yaml
|-- main.py
|-- requirements.txt
`-- README.md
```

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

这些命令的单一来源定义在 `kepagent/constants.py`，心跳能力列表和命令分发都会直接复用这份集合，避免网站和 Agent 命令漂移。

## 配置

### 1. 准备 `.env`

```bash
cp .env.example .env
```

主要环境变量：

- `KEPAGENT_API_BASE_URL`
- `KEPAGENT_API_KEY`
- `KEPAGENT_RCON_PASSWORD`

### 2. 准备 `agent.yaml`

```bash
cp agent.example.yaml agent.yaml
```

`agent.yaml` 用于描述：

- 服务器列表
- 容器名、镜像、端口、挂载、环境变量
- 分组
- 单服兜底 `rcon_password`
- 监控服键值和监控阈值

### RCON 密码优先级

1. 控制平面透传的按服务器密码
2. `servers[].rcon_password`
3. 全局 `KEPAGENT_RCON_PASSWORD`

### 交互式控制台

如果需要 `docker attach` 进入容器，请为对应服务器同时设置：

- `stdin_open: true`
- `tty: true`

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

## systemd 示例

下面示例假设部署目录为 `/opt/kepagent`：

```ini
[Unit]
Description=KepAgent
After=network-online.target docker.service
Wants=network-online.target
Requires=docker.service

[Service]
Type=simple
User=root
WorkingDirectory=/opt/kepagent
ExecStart=/usr/bin/python3 /opt/kepagent/main.py --config /opt/kepagent/agent.yaml
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

常用命令：

```bash
systemctl daemon-reload
systemctl enable kepagent
systemctl start kepagent
systemctl status kepagent
journalctl -u kepagent -f
```

## 关键行为说明

### `node.check_update`

该命令会先读取本地 buildid，再读取远端最新 buildid。  
只有在版本存在差异时，才会继续停服、执行 `app_update validate`、运行崩溃检查，并在崩溃检查成功后启动服务器。  
如果命令 payload 没有显式传入 `monitorServerKey`，则回退到 `agent.yaml` 中的 `monitor_server_key`。

### `node.check_validate`

该命令会直接停服并执行 `steamcmd +app_update <app_id> validate`，不依赖“是否检测到新版本”。  
校验完成后会重新写入 `game/csgo/gameinfo.gi` 中的 Metamod 路径，确保更新后插件入口没有丢失。

### `node.monitor_check`

该命令会直接重建并启动监控服容器，然后轮询它是否在稳定窗口内崩溃。  
成功时只返回崩溃检查结果，不会自动启动其它服务器。

### `node.monitor_start`

行为与 `node.monitor_check` 一致，但监控成功后会继续启动服务器。  
如果 payload 没有提供 `startServerKeys`，默认会启动除监控服之外的全部服务器；如果 payload 提供了 `startServerKeys`，则只启动这些目标。

## 验证与安全

建议在改动后至少执行：

```bash
py -3 -m compileall kepagent main.py
py -3 -m pip_audit -r requirements.txt
```
