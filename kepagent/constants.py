from __future__ import annotations

AGENT_VERSION = "0.2.0"

SUPPORTED_COMMANDS = (
    "agent.ping",
    "docker.list_servers",
    "docker.start_server",
    "docker.stop_server",
    "docker.restart_server",
    "docker.remove_server",
    "docker.start_group",
    "docker.stop_group",
    "docker.restart_group",
    "node.kill_all",
    "node.rcon_command",
    "node.check_update",
    "node.check_validate",
    "node.check_update_monitor",
    "node.check_update_start",
    "node.get_oldver",
    "node.get_nowver",
    "node.monitor_check",
    "node.monitor_start",
)

__all__ = [
    "AGENT_VERSION",
    "SUPPORTED_COMMANDS",
]
