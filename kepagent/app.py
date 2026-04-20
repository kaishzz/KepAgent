from __future__ import annotations

import argparse
import logging
import platform
import socket
import time
from typing import Any

from .api import ControlPlaneClient
from .config import AgentConfig, load_config
from .constants import AGENT_VERSION, SUPPORTED_COMMANDS
from .runtime import CommandCancelled, DockerRuntime

LOGGER = logging.getLogger("kepagent")


class KepAgentApp:
    def __init__(self, config: AgentConfig) -> None:
        self.config = config
        self.client = ControlPlaneClient(
            base_url=config.api_base_url,
            api_key=config.api_key,
            timeout_seconds=config.request_timeout_seconds,
        )
        self.runtime = DockerRuntime(config)
        self.command_handlers = {
            "agent.ping": self._handle_ping,
            "docker.list_servers": self._handle_list_servers,
            "docker.start_server": self._handle_start_server,
            "docker.stop_server": self._handle_stop_server,
            "docker.restart_server": self._handle_restart_server,
            "docker.remove_server": self._handle_remove_server,
            "docker.start_group": self._handle_start_group,
            "docker.stop_group": self._handle_stop_group,
            "docker.restart_group": self._handle_restart_group,
            "node.kill_all": self._handle_kill_all,
            "node.rcon_command": self._handle_rcon_command,
            "node.check_update": self._handle_check_update,
            "node.check_validate": self._handle_check_validate,
            "node.get_oldver": self._handle_get_oldver,
            "node.get_nowver": self._handle_get_nowver,
            "node.monitor_check": self._handle_monitor_check,
            "node.monitor_start": self._handle_monitor_start,
        }
        missing_handlers = set(SUPPORTED_COMMANDS) - set(self.command_handlers)
        extra_handlers = set(self.command_handlers) - set(SUPPORTED_COMMANDS)
        if missing_handlers or extra_handlers:
            raise RuntimeError(
                "Command catalog mismatch: "
                f"missing={sorted(missing_handlers)} extra={sorted(extra_handlers)}"
            )

    def build_heartbeat_payload(self) -> dict[str, Any]:
        return {
            "agentVersion": AGENT_VERSION,
            "hostname": socket.gethostname(),
            "platform": f"{platform.system()} {platform.release()}",
            "capabilities": list(SUPPORTED_COMMANDS),
            "summary": self.runtime.build_summary(),
            "stats": {
                "pythonVersion": platform.python_version(),
            },
            "servers": self.runtime.list_servers(),
            "metadata": {
                "machine": platform.machine(),
                "node": platform.node(),
                "groupLabels": self.config.group_labels,
            },
        }

    def emit_logs(self, command_id: str, logs: list[str]) -> None:
        if not logs:
            return

        batch = [{"level": "info", "message": line} for line in logs if line.strip()]
        if not batch:
            return

        self.client.append_command_logs(command_id, batch[:200])

    def _read_cancel_request(self, command_id: str) -> dict[str, Any] | None:
        try:
            command = self.client.fetch_command(command_id)
        except Exception as exc:  # noqa: BLE001
            LOGGER.debug("Failed to refresh command state for %s: %s", command_id, exc)
            return None

        if not isinstance(command, dict):
            return None

        result = command.get("result")
        if not isinstance(result, dict):
            return None

        control = result.get("control")
        if not isinstance(control, dict):
            return None

        requested_at = str(control.get("cancellationRequestedAt") or "").strip()
        if not requested_at:
            return None

        return {
            "force": bool(control.get("force")),
            "requestedAt": requested_at,
        }

    @staticmethod
    def _command_key(payload: dict[str, Any]) -> str:
        return str(payload.get("key", "")).strip()

    @staticmethod
    def _command_group(payload: dict[str, Any]) -> str:
        return str(payload.get("group", "")).strip()

    @staticmethod
    def _command_server_keys(payload: dict[str, Any], field_name: str = "startServerKeys") -> list[str]:
        values = payload.get(field_name)
        if not isinstance(values, list):
            return []

        normalized: list[str] = []
        seen: set[str] = set()
        for value in values:
            safe_value = str(value or "").strip()
            if safe_value and safe_value not in seen:
                normalized.append(safe_value)
                seen.add(safe_value)
        return normalized

    @staticmethod
    def _command_monitor_server_key(payload: dict[str, Any]) -> str | None:
        safe_value = str(payload.get("monitorServerKey") or "").strip()
        return safe_value or None

    @staticmethod
    def _ok_result(logs: list[str], result: dict[str, Any]) -> dict[str, Any]:
        return {"ok": True, "logs": logs, "result": result}

    def _handle_ping(self, _payload: dict[str, Any], logs: list[str]) -> dict[str, Any]:
        logs.append("Ping command completed")
        return self._ok_result(logs, {"pong": True})

    def _handle_list_servers(self, _payload: dict[str, Any], logs: list[str]) -> dict[str, Any]:
        logs.append("Collected docker server list")
        return self._ok_result(
            logs,
            {
                "summary": self.runtime.build_summary(),
                "servers": self.runtime.list_servers(),
            },
        )

    def _handle_start_server(self, payload: dict[str, Any], logs: list[str]) -> dict[str, Any]:
        result = self.runtime.start_server(self._command_key(payload))
        logs.append(result["message"])
        return self._ok_result(logs, result)

    def _handle_stop_server(self, payload: dict[str, Any], logs: list[str]) -> dict[str, Any]:
        result = self.runtime.stop_server(self._command_key(payload))
        logs.append(result["message"])
        return self._ok_result(logs, result)

    def _handle_restart_server(self, payload: dict[str, Any], logs: list[str]) -> dict[str, Any]:
        result = self.runtime.restart_server(self._command_key(payload))
        logs.append(result["message"])
        return self._ok_result(logs, result)

    def _handle_remove_server(self, payload: dict[str, Any], logs: list[str]) -> dict[str, Any]:
        result = self.runtime.remove_server(self._command_key(payload))
        logs.append(result["message"])
        return self._ok_result(logs, result)

    def _handle_start_group(self, payload: dict[str, Any], logs: list[str]) -> dict[str, Any]:
        result = self.runtime.start_group(self._command_group(payload))
        logs.append(f"Started group {result['group']}")
        return self._ok_result(logs, result)

    def _handle_stop_group(self, payload: dict[str, Any], logs: list[str]) -> dict[str, Any]:
        result = self.runtime.stop_group(self._command_group(payload))
        logs.append(f"Force removed group {result['group']}")
        return self._ok_result(logs, result)

    def _handle_restart_group(self, payload: dict[str, Any], logs: list[str]) -> dict[str, Any]:
        result = self.runtime.restart_group(self._command_group(payload))
        logs.append(f"Restarted group {result['group']}")
        return self._ok_result(logs, result)

    def _handle_kill_all(self, _payload: dict[str, Any], logs: list[str]) -> dict[str, Any]:
        result = self.runtime.remove_all()
        logs.append(f"Removed {result['total']} configured containers")
        return self._ok_result(logs, result)

    def _handle_rcon_command(self, payload: dict[str, Any], logs: list[str]) -> dict[str, Any]:
        group = str(payload.get("group") or "ALL").strip() or "ALL"
        command = str(payload.get("command") or "").strip()
        server_keys = [
            str(value or "").strip()
            for value in (payload.get("serverKeys") or [])
            if str(value or "").strip()
        ]
        targets = payload.get("targets") if isinstance(payload.get("targets"), list) else None
        if not command:
            raise RuntimeError("RCON command cannot be empty")

        result = self.runtime.send_rcon_command(
            group,
            command,
            server_keys=server_keys or None,
            targets=targets,
        )
        logs.append(result["message"])
        return self._ok_result(logs, result)

    def _handle_check_update(self, payload: dict[str, Any], logs: list[str]) -> dict[str, Any]:
        result = self.runtime.check_update(
            monitor_server_key=self._command_monitor_server_key(payload),
            start_server_keys=self._command_server_keys(payload),
        )
        logs.append(result["message"])
        return self._ok_result(logs, result)

    def _handle_check_validate(self, _payload: dict[str, Any], logs: list[str]) -> dict[str, Any]:
        result = self.runtime.check_validate()
        logs.append(result["message"])
        return self._ok_result(logs, result)

    def _handle_get_oldver(self, _payload: dict[str, Any], logs: list[str]) -> dict[str, Any]:
        result = self.runtime.get_oldver()
        logs.append(result["message"])
        return self._ok_result(logs, result)

    def _handle_get_nowver(self, _payload: dict[str, Any], logs: list[str]) -> dict[str, Any]:
        result = self.runtime.get_nowver()
        logs.append(result["message"])
        return self._ok_result(logs, result)

    def _handle_monitor_check(self, payload: dict[str, Any], logs: list[str]) -> dict[str, Any]:
        result = self.runtime.monitor_check(
            start_after_success=False,
            monitor_server_key=self._command_monitor_server_key(payload),
            start_server_keys=self._command_server_keys(payload),
        )
        logs.append(result["message"])
        return self._ok_result(logs, result)

    def _handle_monitor_start(self, payload: dict[str, Any], logs: list[str]) -> dict[str, Any]:
        result = self.runtime.monitor_check(
            start_after_success=True,
            monitor_server_key=self._command_monitor_server_key(payload),
            start_server_keys=self._command_server_keys(payload),
        )
        logs.append(result["message"])
        return self._ok_result(logs, result)

    def execute_command(self, command: dict[str, Any]) -> dict[str, Any]:
        command_type = str(command.get("commandType") or "").strip()
        payload = command.get("payload") or {}
        logs: list[str] = [f"Executing command: {command_type}"]
        handler = self.command_handlers.get(command_type)
        if handler is not None:
            return handler(payload, logs)

        raise RuntimeError(f"Unsupported command type: {command_type}")

    def process_one_command(self) -> None:
        command = self.client.claim_command()
        if not command:
            return

        command_id = str(command["id"])
        started = self.client.mark_command_started(command_id)
        if str((started or {}).get("status") or "").strip().upper() == "CANCELLED":
            LOGGER.info("Command %s was cancelled before execution", command_id)
            return

        self.runtime.set_cancel_reader(lambda: self._read_cancel_request(command_id))
        try:
            execution = self.execute_command(command)
            self.emit_logs(command_id, execution.get("logs", []))
            self.client.finish_command(
                command_id,
                success=bool(execution.get("ok")),
                result=execution.get("result"),
            )
        except CommandCancelled as exc:
            LOGGER.warning("Command cancelled: %s", exc)
            self.emit_logs(command_id, [str(exc)])
            self.client.finish_command(
                command_id,
                success=False,
                result={
                    "cancelled": True,
                    "force": exc.force,
                    "message": str(exc),
                },
                error_message=str(exc),
                cancelled=True,
            )
        except Exception as exc:  # noqa: BLE001
            LOGGER.exception("Command execution failed")
            self.emit_logs(command_id, [f"Command failed: {exc}"])
            self.client.finish_command(
                command_id,
                success=False,
                error_message=str(exc),
            )
        finally:
            self.runtime.set_cancel_reader(None)

    def run_forever(self) -> int:
        LOGGER.info("KepAgent starting")
        try:
            me = self.client.fetch_me()
        except Exception as exc:  # noqa: BLE001
            LOGGER.error("Agent bootstrap failed: %s", exc)
            return 1

        LOGGER.info(
            "Connected to control plane as node %s",
            ((me.get("node") or {}).get("name") or (me.get("node") or {}).get("code") or "unknown"),
        )

        next_heartbeat = 0.0
        next_poll = 0.0

        while True:
            now = time.monotonic()

            if now >= next_heartbeat:
                try:
                    payload = self.build_heartbeat_payload()
                    self.client.send_heartbeat(payload)
                except Exception as exc:  # noqa: BLE001
                    LOGGER.warning("Heartbeat failed: %s", exc)
                next_heartbeat = now + max(1, self.config.heartbeat_interval_seconds)

            if now >= next_poll:
                try:
                    self.process_one_command()
                except Exception as exc:  # noqa: BLE001
                    LOGGER.warning("Poll cycle failed: %s", exc)
                next_poll = now + max(1, self.config.poll_interval_seconds)

            time.sleep(0.5)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="KepCs Docker control agent")
    parser.add_argument("--config", default="agent.yaml", help="Path to agent YAML config")
    parser.add_argument("--version", action="version", version=f"KepAgent {AGENT_VERSION}")
    return parser


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="[%(asctime)s] %(levelname)s %(name)s: %(message)s",
    )
    parser = build_parser()
    args = parser.parse_args()
    config = load_config(args.config)
    app = KepAgentApp(config)
    return app.run_forever()
