# KepAgent 0.2.0

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
- 监控更新后容器稳定性，并按配置自动拉起全部服务器

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
- `node.check_update_monitor`
- `node.check_update_start`
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

### `node.check_validate`

该命令会直接执行 `steamcmd +app_update <app_id> validate`，不再依赖“是否检测到新版本”。  
也就是说，即使当前已经是最新版本，仍会执行完整性校验，这和控制台中的“验证游戏完整性”文案保持一致。

### `node.check_update_monitor`

该命令只在检测到新版本并完成更新后，才继续执行监控服稳定性检查。

### `node.check_update_start`

行为与 `node.check_update_monitor` 一致，但监控成功后会启动全部已配置服务器。

## 验证与安全

建议在改动后至少执行：

```bash
py -3 -m compileall kepagent main.py
py -3 -m pip_audit -r requirements.txt
```

当前依赖安全扫描结果：`requirements.txt` 中未发现已知漏洞。
