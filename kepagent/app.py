from __future__ import annotations

import argparse
import json
import logging
import platform
import socket
import time
from typing import Any, Callable

from .api import ControlPlaneClient
from .config import AgentConfig, load_config
from .constants import AGENT_VERSION, SUPPORTED_COMMANDS
from .runtime import CommandCancelled, DockerRuntime

LOGGER = logging.getLogger("kepagent")
MAX_FINISH_RESULT_BYTES = 8 * 1024
MAX_FINISH_BATCH_MESSAGES = 12
MAX_FINISH_TIMELINE_ENTRIES = 10
MAX_FINISH_TEXT_LENGTH = 300


class LiveCommandLogger:
    def __init__(
        self,
        emitter: Callable[[list[dict[str, str]]], None],
        *,
        batch_size: int = 10,
        flush_interval_seconds: float = 0.5,
    ) -> None:
        self._emitter = emitter
        self._batch_size = max(1, batch_size)
        self._flush_interval_seconds = max(0.0, flush_interval_seconds)
        self._messages: list[str] = []
        self._buffer: list[dict[str, str]] = []
        self._last_flush_at = 0.0

    def append(self, message: str) -> None:
        self.emit(message)

    def emit(self, message: str, *, level: str = "info") -> None:
        safe_message = str(message or "").strip()
        safe_level = str(level or "info").strip() or "info"
        if not safe_message:
            return

        self._messages.append(safe_message)
        self._buffer.append({"level": safe_level[:16], "message": safe_message})
        now = time.time()
        if len(self._buffer) >= self._batch_size or now - self._last_flush_at >= self._flush_interval_seconds:
            self.flush()

    def flush(self) -> None:
        if not self._buffer:
            return

        while self._buffer:
            batch = self._buffer[:200]
            self._emitter(batch)
            self._buffer = self._buffer[200:]

        self._last_flush_at = time.time()

    def messages(self) -> list[str]:
        return list(self._messages)


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
                "groupOrder": self.config.group_order,
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
    def _command_server_keys(payload: dict[str, Any], field_name: str = "serverKeys") -> list[str]:
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
    def _ok_result(logs: LiveCommandLogger, result: dict[str, Any]) -> dict[str, Any]:
        return {"ok": True, "logs": logs.messages(), "result": result}

    @staticmethod
    def _command_result(logs: LiveCommandLogger, result: dict[str, Any]) -> dict[str, Any]:
        return {"ok": bool(result.get("ok", True)), "logs": logs.messages(), "result": result}

    @staticmethod
    def _truncate_text(value: Any, limit: int = MAX_FINISH_TEXT_LENGTH) -> str:
        text = str(value or "").strip()
        if len(text) <= limit:
            return text
        return f"{text[: max(0, limit - 15)]}... (truncated)"

    @classmethod
    def _compact_server_snapshot(cls, payload: Any) -> dict[str, Any] | None:
        if not isinstance(payload, dict):
            return None
        return {
            key: payload.get(key)
            for key in ("key", "containerName", "state", "status", "primaryPort", "restartCount")
            if key in payload
        }

    @classmethod
    def _compact_server_batch(cls, payload: Any) -> dict[str, Any] | None:
        if not isinstance(payload, dict):
            return None

        compact = {
            key: payload.get(key)
            for key in ("scope", "group", "action", "changed", "total", "serverKeys", "defaulted", "monitorServerKey", "message")
            if key in payload
        }
        results = payload.get("results")
        if isinstance(results, list):
            messages = [
                cls._truncate_text(item.get("message"))
                for item in results
                if isinstance(item, dict) and str(item.get("message") or "").strip()
            ]
            if messages:
                compact["messages"] = messages[:MAX_FINISH_BATCH_MESSAGES]
            if len(results) > MAX_FINISH_BATCH_MESSAGES:
                compact["truncatedResults"] = len(results) - MAX_FINISH_BATCH_MESSAGES
        return compact

    @classmethod
    def _compact_monitor_launch(cls, payload: Any) -> dict[str, Any] | None:
        if not isinstance(payload, dict):
            return None

        compact = {
            key: payload.get(key)
            for key in ("monitorServerKey", "message")
            if key in payload
        }
        cleanup = cls._compact_server_batch(payload.get("cleanup"))
        if cleanup:
            compact["cleanup"] = cleanup

        launch = payload.get("launch")
        if isinstance(launch, dict):
            compact["launch"] = {
                key: launch.get(key)
                for key in ("changed", "message")
                if key in launch
            }
            server = cls._compact_server_snapshot(launch.get("server"))
            if server:
                compact["launch"]["server"] = server
        return compact

    @classmethod
    def _compact_update_result(cls, payload: Any) -> dict[str, Any] | None:
        if not isinstance(payload, dict):
            return None

        compact = {
            key: payload.get(key)
            for key in ("stopAll", "metamod")
            if key in payload
        }
        stop_all = cls._compact_server_batch(payload.get("stopAll"))
        if stop_all:
            compact["stopAll"] = stop_all

        metamod = payload.get("metamod")
        if isinstance(metamod, dict):
            compact["metamod"] = {
                key: metamod.get(key)
                for key in ("changed", "message")
                if key in metamod
            }

        output = str(payload.get("output") or "").strip()
        if output:
            compact["outputLineCount"] = len(output.splitlines())
        return compact

    @classmethod
    def _compact_finish_result(cls, command_type: str, result: Any) -> Any:
        if not isinstance(result, dict):
            return result

        compact = dict(result)
        if "update" in compact:
            compact["update"] = cls._compact_update_result(compact.get("update"))
        if "monitorServer" in compact:
            compact["monitorServer"] = cls._compact_server_snapshot(compact.get("monitorServer"))
        if "monitorLaunch" in compact:
            compact["monitorLaunch"] = cls._compact_monitor_launch(compact.get("monitorLaunch"))
        if "startServers" in compact:
            compact["startServers"] = cls._compact_server_batch(compact.get("startServers"))

        timeline = compact.get("timeline")
        if isinstance(timeline, list):
            if len(timeline) > MAX_FINISH_TIMELINE_ENTRIES:
                compact["timelineTruncated"] = len(timeline) - MAX_FINISH_TIMELINE_ENTRIES
            compact["timeline"] = timeline[-MAX_FINISH_TIMELINE_ENTRIES:]

        try:
            payload_size = len(json.dumps(compact, ensure_ascii=False).encode("utf-8"))
        except (TypeError, ValueError):
            return {
                "commandType": command_type,
                "message": cls._truncate_text(result.get("message")),
                "truncated": True,
            }

        if payload_size <= MAX_FINISH_RESULT_BYTES:
            return compact

        fallback = {
            "commandType": command_type,
            "message": cls._truncate_text(result.get("message")),
            "truncated": True,
            "resultBytes": payload_size,
        }
        for key in (
            "validated",
            "updated",
            "needsUpdate",
            "previousBuildId",
            "currentBuildId",
            "latestBuildId",
            "monitorServerKey",
            "scope",
            "action",
            "changed",
            "total",
            "serverKeys",
        ):
            if key in result:
                fallback[key] = result.get(key)
        start_servers = cls._compact_server_batch(result.get("startServers"))
        if start_servers:
            fallback["startServers"] = {
                key: start_servers.get(key)
                for key in ("changed", "total", "serverKeys", "message")
                if key in start_servers
            }
        return fallback

    def _handle_ping(self, _payload: dict[str, Any], logs: LiveCommandLogger) -> dict[str, Any]:
        logs.append("Ping command completed")
        return self._ok_result(logs, {"pong": True})

    def _handle_list_servers(self, _payload: dict[str, Any], logs: LiveCommandLogger) -> dict[str, Any]:
        logs.append("Collected docker server list")
        return self._ok_result(
            logs,
            {
                "summary": self.runtime.build_summary(),
                "servers": self.runtime.list_servers(),
            },
        )

    def _handle_server_action(self, action: str, payload: dict[str, Any], logs: LiveCommandLogger) -> dict[str, Any]:
        server_keys = self._command_server_keys(payload, "serverKeys")
        if server_keys:
            result = getattr(self.runtime, f"{action}_servers")(server_keys)
            logs.append(f"Batch {action} handled {result['total']} servers, changed {result['changed']}")
            return self._ok_result(logs, result)

        result = getattr(self.runtime, f"{action}_server")(self._command_key(payload))
        logs.append(result["message"])
        return self._ok_result(logs, result)

    def _handle_start_server(self, payload: dict[str, Any], logs: LiveCommandLogger) -> dict[str, Any]:
        return self._handle_server_action("start", payload, logs)

    def _handle_stop_server(self, payload: dict[str, Any], logs: LiveCommandLogger) -> dict[str, Any]:
        return self._handle_server_action("stop", payload, logs)

    def _handle_restart_server(self, payload: dict[str, Any], logs: LiveCommandLogger) -> dict[str, Any]:
        return self._handle_server_action("restart", payload, logs)

    def _handle_remove_server(self, payload: dict[str, Any], logs: LiveCommandLogger) -> dict[str, Any]:
        return self._handle_server_action("remove", payload, logs)

    def _handle_start_group(self, payload: dict[str, Any], logs: LiveCommandLogger) -> dict[str, Any]:
        result = self.runtime.start_group(self._command_group(payload))
        logs.append(f"Started group {result['group']}")
        return self._ok_result(logs, result)

    def _handle_stop_group(self, payload: dict[str, Any], logs: LiveCommandLogger) -> dict[str, Any]:
        result = self.runtime.stop_group(self._command_group(payload))
        logs.append(f"Force removed group {result['group']}")
        return self._ok_result(logs, result)

    def _handle_restart_group(self, payload: dict[str, Any], logs: LiveCommandLogger) -> dict[str, Any]:
        result = self.runtime.restart_group(self._command_group(payload))
        logs.append(f"Restarted group {result['group']}")
        return self._ok_result(logs, result)

    def _handle_kill_all(self, _payload: dict[str, Any], logs: LiveCommandLogger) -> dict[str, Any]:
        result = self.runtime.remove_all()
        logs.append(f"Removed {result['total']} configured containers")
        return self._ok_result(logs, result)

    def _handle_rcon_command(self, payload: dict[str, Any], logs: LiveCommandLogger) -> dict[str, Any]:
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

    def _handle_check_update(self, _payload: dict[str, Any], logs: LiveCommandLogger) -> dict[str, Any]:
        result = self.runtime.check_update()
        logs.append(result["message"])
        return self._command_result(logs, result)

    def _handle_check_validate(self, _payload: dict[str, Any], logs: LiveCommandLogger) -> dict[str, Any]:
        result = self.runtime.check_validate()
        logs.append(result["message"])
        return self._ok_result(logs, result)

    def _handle_get_oldver(self, _payload: dict[str, Any], logs: LiveCommandLogger) -> dict[str, Any]:
        result = self.runtime.get_oldver()
        logs.append(result["message"])
        return self._ok_result(logs, result)

    def _handle_get_nowver(self, _payload: dict[str, Any], logs: LiveCommandLogger) -> dict[str, Any]:
        result = self.runtime.get_nowver()
        logs.append(result["message"])
        return self._ok_result(logs, result)

    def _handle_monitor_check(self, _payload: dict[str, Any], logs: LiveCommandLogger) -> dict[str, Any]:
        result = self.runtime.monitor_check(
            start_after_success=False,
        )
        logs.append(result["message"])
        return self._command_result(logs, result)

    def _handle_monitor_start(self, _payload: dict[str, Any], logs: LiveCommandLogger) -> dict[str, Any]:
        result = self.runtime.monitor_check(
            start_after_success=True,
        )
        logs.append(result["message"])
        return self._command_result(logs, result)

    def execute_command(self, command: dict[str, Any], logs: LiveCommandLogger) -> dict[str, Any]:
        command_type = str(command.get("commandType") or "").strip()
        payload = command.get("payload") or {}
        logs.append(f"Executing command: {command_type}")
        handler = self.command_handlers.get(command_type)
        if handler is not None:
            self.runtime.set_log_emitter(logs.emit)
            return handler(payload, logs)

        raise RuntimeError(f"Unsupported command type: {command_type}")

    def process_one_command(self) -> None:
        command = self.client.claim_command()
        if not command:
            return

        command_id = str(command["id"])
        command_type = str(command.get("commandType") or "").strip()
        started = self.client.mark_command_started(command_id)
        if str((started or {}).get("status") or "").strip().upper() == "CANCELLED":
            LOGGER.info("Command %s was cancelled before execution", command_id)
            return

        self.runtime.set_cancel_reader(lambda: self._read_cancel_request(command_id))
        logs = LiveCommandLogger(lambda batch: self.client.append_command_logs(command_id, batch))
        try:
            execution = self.execute_command(command, logs)
            logs.flush()
            compact_result = self._compact_finish_result(command_type, execution.get("result"))
            self.client.finish_command(
                command_id,
                success=bool(execution.get("ok")),
                result=compact_result,
            )
        except CommandCancelled as exc:
            LOGGER.warning("Command cancelled: %s", exc)
            logs.emit(str(exc), level="warning")
            logs.flush()
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
            logs.emit(f"Command failed: {exc}", level="error")
            logs.flush()
            self.client.finish_command(
                command_id,
                success=False,
                error_message=str(exc),
            )
        finally:
            self.runtime.set_log_emitter(None)
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
