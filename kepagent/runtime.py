from __future__ import annotations

import re
import queue
import subprocess
import threading
import time
from collections import defaultdict
from pathlib import Path
from typing import Any, Callable

import docker
from docker.errors import NotFound

from .config import AgentConfig, PortBinding, ServerDefinition, VolumeBinding


ANSI_ESCAPE_RE = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")


class CommandCancelled(RuntimeError):
    def __init__(self, message: str, *, force: bool = False) -> None:
        super().__init__(message)
        self.force = force


class DockerRuntime:
    def __init__(self, config: AgentConfig) -> None:
        self.config = config
        self.client = (
            docker.DockerClient(base_url=config.docker_base_url)
            if config.docker_base_url
            else docker.from_env()
        )
        self._servers = {server.key: server for server in config.servers}
        self._groups = self._build_groups(config.servers)
        self._cancel_reader: Callable[[], dict[str, Any] | None] | None = None
        self._log_emitter: Callable[[str, str], None] | None = None

    @staticmethod
    def _build_groups(servers: list[ServerDefinition]) -> dict[str, list[ServerDefinition]]:
        groups: dict[str, list[ServerDefinition]] = defaultdict(list)
        for server in servers:
            for group in server.groups:
                groups[group].append(server)
        return dict(groups)

    @staticmethod
    def _ports_value(ports: list[PortBinding]) -> dict[str, int]:
        return {
            f"{item.container_port}/{item.protocol}": item.host_port
            for item in ports
        }

    @staticmethod
    def _volumes_value(volumes: list[VolumeBinding]) -> dict[str, dict[str, str]]:
        return {
            item.host_path: {
                "bind": item.container_path,
                "mode": item.mode,
            }
            for item in volumes
        }

    def get_server(self, key: str) -> ServerDefinition:
        server = self._servers.get(key)
        if not server:
            raise RuntimeError(f"Unknown server key: {key}")
        return server

    def get_group(self, group: str) -> list[ServerDefinition]:
        if group == "ALL":
            return list(self.config.servers)

        servers = self._groups.get(group, [])
        if not servers:
            raise RuntimeError(f"Unknown or empty group: {group}")
        return servers

    def _servers_for_keys(self, keys: list[str]) -> list[ServerDefinition]:
        normalized_keys = []
        seen = set()

        for value in keys:
            safe_value = str(value or "").strip()
            if safe_value and safe_value not in seen:
                normalized_keys.append(safe_value)
                seen.add(safe_value)

        if not normalized_keys:
            raise RuntimeError("No server keys provided")

        missing_keys = [key for key in normalized_keys if key not in self._servers]
        if missing_keys:
            raise RuntimeError(f"Unknown server keys: {', '.join(missing_keys)}")

        return [self._servers[key] for key in normalized_keys]

    def _get_container(self, container_name: str):
        try:
            return self.client.containers.get(container_name)
        except NotFound:
            return None

    def set_cancel_reader(self, reader: Callable[[], dict[str, Any] | None] | None) -> None:
        self._cancel_reader = reader

    def set_log_emitter(self, emitter: Callable[[str, str], None] | None) -> None:
        self._log_emitter = emitter

    def _emit_log(self, message: str, *, level: str = "info") -> None:
        if self._log_emitter is None:
            return

        self._log_emitter(str(message or ""), level=str(level or "info"))

    @staticmethod
    def _format_elapsed(seconds: float) -> str:
        total_seconds = max(0, int(seconds))
        minutes, secs = divmod(total_seconds, 60)
        hours, minutes = divmod(minutes, 60)
        if hours > 0:
            return f"{hours}h {minutes}m {secs}s"
        if minutes > 0:
            return f"{minutes}m {secs}s"
        return f"{secs}s"

    def _current_cancel_request(self) -> dict[str, Any] | None:
        if self._cancel_reader is None:
            return None

        request = self._cancel_reader()
        return request if isinstance(request, dict) else None

    def _raise_if_cancel_requested(self) -> None:
        request = self._current_cancel_request()
        if not request:
            return

        raise CommandCancelled(
            "Command force cancelled by operator"
            if request.get("force")
            else "Command cancelled by operator",
            force=bool(request.get("force")),
        )

    def _run_process(
        self,
        args: list[str],
        *,
        timeout_seconds: int = 600,
        cwd: str | None = None,
        log_filter: Callable[[str, str], bool] | None = None,
        stop_condition: Callable[[str, str], bool] | None = None,
        use_pty: bool = False,
    ) -> dict[str, Any]:
        if use_pty:
            return self._run_process_with_pty(
                args,
                timeout_seconds=timeout_seconds,
                cwd=cwd,
                log_filter=log_filter,
                stop_condition=stop_condition,
            )

        self._raise_if_cancel_requested()

        process = subprocess.Popen(
            args,
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )

        started_at = time.time()
        output_parts: list[str] = []
        event_queue: queue.Queue[tuple[str, str | None]] = queue.Queue()
        stream_names: set[str] = set()
        stopped_early = False

        def read_stream(stream_name: str, stream: Any) -> None:
            try:
                while True:
                    chunk = stream.readline()
                    if chunk == "":
                        break
                    event_queue.put((stream_name, chunk))
            finally:
                try:
                    stream.close()
                finally:
                    event_queue.put((stream_name, None))

        if process.stdout is not None:
            stream_names.add("stdout")
            threading.Thread(
                target=read_stream,
                args=("stdout", process.stdout),
                daemon=True,
            ).start()

        if process.stderr is not None:
            stream_names.add("stderr")
            threading.Thread(
                target=read_stream,
                args=("stderr", process.stderr),
                daemon=True,
            ).start()

        closed_streams: set[str] = set()
        while len(closed_streams) < len(stream_names) or process.poll() is None:
            request = self._current_cancel_request()
            if request:
                if request.get("force"):
                    process.kill()
                else:
                    process.terminate()
                    try:
                        process.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        process.kill()

                try:
                    process.communicate(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.communicate()

                raise CommandCancelled(
                    "Command force cancelled by operator"
                    if request.get("force")
                    else "Command cancelled by operator",
                    force=bool(request.get("force")),
                )

            if time.time() - started_at > timeout_seconds:
                process.kill()
                process.wait(timeout=5)
                raise RuntimeError(f"Process timed out after {timeout_seconds} seconds")

            try:
                stream_name, chunk = event_queue.get(timeout=1)
            except queue.Empty:
                continue

            if chunk is None:
                closed_streams.add(stream_name)
                continue

            message = ANSI_ESCAPE_RE.sub("", str(chunk or "")).rstrip("\r\n")
            if not message:
                continue

            output_parts.append(message)
            if stop_condition and stop_condition(stream_name, message):
                stopped_early = True
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=5)
                break

            should_emit_log = log_filter(stream_name, message) if log_filter else True
            if not should_emit_log:
                continue

            if stream_name == "stderr":
                self._emit_log(f"[stderr] {message}", level="error")
            else:
                self._emit_log(message)

        output = "\n".join(output_parts).strip()
        return {
            "ok": stopped_early or process.returncode == 0,
            "code": 0 if stopped_early else process.returncode,
            "output": output,
            "stoppedEarly": stopped_early,
        }

    def _run_process_with_pty(
        self,
        args: list[str],
        *,
        timeout_seconds: int = 600,
        cwd: str | None = None,
        log_filter: Callable[[str, str], bool] | None = None,
        stop_condition: Callable[[str, str], bool] | None = None,
    ) -> dict[str, Any]:
        import os
        import pty
        import select

        self._raise_if_cancel_requested()

        master_fd, slave_fd = pty.openpty()
        try:
            process = subprocess.Popen(
                args,
                cwd=cwd,
                stdin=subprocess.DEVNULL,
                stdout=slave_fd,
                stderr=slave_fd,
                bufsize=0,
            )
        finally:
            os.close(slave_fd)

        started_at = time.time()
        output_parts: list[str] = []
        buffer = ""
        stopped_early = False

        def terminate_process() -> None:
            if process.poll() is not None:
                return

            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)

        def handle_message(message: str) -> bool:
            nonlocal stopped_early
            if not message:
                return False

            output_parts.append(message)
            if stop_condition and stop_condition("stdout", message):
                stopped_early = True
                terminate_process()
                return True

            should_emit_log = log_filter("stdout", message) if log_filter else True
            if should_emit_log:
                self._emit_log(message)
            return False

        try:
            while True:
                request = self._current_cancel_request()
                if request:
                    if request.get("force"):
                        process.kill()
                    else:
                        terminate_process()

                    raise CommandCancelled(
                        "Command force cancelled by operator"
                        if request.get("force")
                        else "Command cancelled by operator",
                        force=bool(request.get("force")),
                    )

                if time.time() - started_at > timeout_seconds:
                    process.kill()
                    process.wait(timeout=5)
                    raise RuntimeError(f"Process timed out after {timeout_seconds} seconds")

                ready, _, _ = select.select([master_fd], [], [], 1)
                if not ready:
                    if process.poll() is not None:
                        break
                    continue

                try:
                    chunk = os.read(master_fd, 4096)
                except OSError:
                    chunk = b""
                if not chunk:
                    if process.poll() is not None:
                        break
                    continue

                text = ANSI_ESCAPE_RE.sub("", chunk.decode("utf-8", errors="ignore"))
                if not text:
                    continue

                buffer += text.replace("\r\n", "\n")
                while True:
                    newline_index = len(buffer)
                    separator_length = 0
                    for token in ("\r", "\n"):
                        token_index = buffer.find(token)
                        if token_index != -1 and token_index < newline_index:
                            newline_index = token_index
                            separator_length = len(token)

                    if separator_length == 0:
                        break

                    message = buffer[:newline_index].strip()
                    buffer = buffer[newline_index + separator_length :]
                    if handle_message(message):
                        break

                if stopped_early:
                    break

            if buffer.strip():
                handle_message(buffer.strip())
        finally:
            os.close(master_fd)
            if process.poll() is None:
                process.wait(timeout=5)

        output = "\n".join(output_parts).strip()
        return {
            "ok": stopped_early or process.returncode == 0,
            "code": 0 if stopped_early else process.returncode,
            "output": output,
            "stoppedEarly": stopped_early,
        }

    def _server_primary_port(self, server: ServerDefinition) -> int:
        if not server.ports:
            raise RuntimeError(f"Server {server.key} has no host port configured")

        tcp_port = next((item.host_port for item in server.ports if item.protocol.lower() == "tcp"), None)
        return int(tcp_port or server.ports[0].host_port)

    def inspect_server(self, key: str) -> dict[str, Any]:
        server = self.get_server(key)
        container = self._get_container(server.container_name)

        if not container:
            return {
                "key": server.key,
                "containerName": server.container_name,
                "state": "missing",
                "status": "missing",
                "groups": server.groups,
                "image": server.image,
                "primaryPort": self._server_primary_port(server),
            }

        container.reload()
        state = container.attrs.get("State", {})
        return {
            "key": server.key,
            "containerName": server.container_name,
            "state": state.get("Status", container.status),
            "status": container.status,
            "id": container.id,
            "groups": server.groups,
            "image": container.image.tags or [server.image],
            "primaryPort": self._server_primary_port(server),
            "restartCount": int(state.get("RestartCount") or 0),
        }

    def list_servers(self) -> list[dict[str, Any]]:
        return [self.inspect_server(server.key) for server in self.config.servers]

    def start_server(self, key: str) -> dict[str, Any]:
        server = self.get_server(key)
        container = self._get_container(server.container_name)

        if container:
            container.reload()
            if container.status == "running":
                return {
                    "changed": False,
                    "message": f"{server.container_name} already running",
                    "server": self.inspect_server(key),
                }
            container.remove(force=True)

        self.client.containers.run(
            image=server.image,
            entrypoint=server.entrypoint,
            command=server.command or None,
            name=server.container_name,
            detach=True,
            stdin_open=server.stdin_open,
            tty=server.tty,
            environment=server.env,
            ports=self._ports_value(server.ports),
            volumes=self._volumes_value(server.volumes),
            labels=server.labels,
            working_dir=server.working_dir,
            network_mode=server.network_mode,
            restart_policy={"Name": server.restart_policy},
        )

        return {
            "changed": True,
            "message": f"{server.container_name} started",
            "server": self.inspect_server(key),
        }

    def stop_server(self, key: str) -> dict[str, Any]:
        server = self.get_server(key)
        container = self._get_container(server.container_name)

        if not container:
            return {
                "changed": False,
                "message": f"{server.container_name} not found",
                "server": self.inspect_server(key),
            }

        container.reload()
        container.remove(force=True)
        return {
            "changed": True,
            "message": f"{server.container_name} force removed",
            "server": self.inspect_server(key),
        }

    def restart_server(self, key: str, timeout: int = 10) -> dict[str, Any]:
        server = self.get_server(key)
        container = self._get_container(server.container_name)

        if not container:
            return self.start_server(key)

        container.restart(timeout=timeout)
        return {
            "changed": True,
            "message": f"{server.container_name} restarted",
            "server": self.inspect_server(key),
        }

    def remove_server(self, key: str, force: bool = True) -> dict[str, Any]:
        server = self.get_server(key)
        container = self._get_container(server.container_name)

        if not container:
            return {
                "changed": False,
                "message": f"{server.container_name} not found",
                "server": self.inspect_server(key),
            }

        container.remove(force=force)
        return {
            "changed": True,
            "message": f"{server.container_name} removed",
            "server": self.inspect_server(key),
        }

    def _run_group(self, group: str, action: str) -> dict[str, Any]:
        servers = self.get_group(group)
        results = []
        changed = 0

        for server in servers:
            self._raise_if_cancel_requested()
            method = getattr(self, f"{action}_server")
            result = method(server.key)
            results.append(result)
            if result.get("changed"):
                changed += 1

        return {
            "group": group,
            "action": action,
            "changed": changed,
            "total": len(results),
            "results": results,
        }

    def _run_servers(self, action: str, server_keys: list[str]) -> dict[str, Any]:
        servers = self._servers_for_keys(server_keys)
        results = []
        changed = 0

        for server in servers:
            self._raise_if_cancel_requested()
            method = getattr(self, f"{action}_server")
            result = method(server.key)
            results.append(result)
            if result.get("changed"):
                changed += 1

        return {
            "scope": "servers",
            "action": action,
            "serverKeys": [server.key for server in servers],
            "changed": changed,
            "total": len(results),
            "results": results,
        }

    def _run_all(self, action: str) -> dict[str, Any]:
        results = []
        changed = 0

        for server in self.config.servers:
            self._raise_if_cancel_requested()
            method = getattr(self, f"{action}_server")
            result = method(server.key)
            results.append(result)
            if result.get("changed"):
                changed += 1

        return {
            "group": "ALL",
            "action": action,
            "changed": changed,
            "total": len(results),
            "results": results,
        }

    def start_group(self, group: str) -> dict[str, Any]:
        return self._run_group(group, "start")

    def stop_group(self, group: str) -> dict[str, Any]:
        return self._run_group(group, "stop")

    def restart_group(self, group: str) -> dict[str, Any]:
        return self._run_group(group, "restart")

    def start_all(self) -> dict[str, Any]:
        return self._run_all("start")

    def remove_all(self) -> dict[str, Any]:
        return self._run_all("remove")

    def _resolve_monitor_server_key(self, monitor_server_key: str | None = None) -> str:
        explicit_key = str(monitor_server_key or "").strip()
        if explicit_key:
            return explicit_key

        configured_key = str(self.config.monitor_server_key or "").strip()
        if configured_key:
            return configured_key

        if self.config.servers:
            return self.config.servers[0].key

        raise RuntimeError("No monitor server key configured")

    def _default_start_server_keys(self, monitor_server_key: str) -> list[str]:
        keys = [server.key for server in self.config.servers if server.key != monitor_server_key]
        return keys or [server.key for server in self.config.servers]

    def _launch_monitor_server(self, monitor_server_key: str) -> dict[str, Any]:
        cleanup = self.remove_server(monitor_server_key)
        launch = self.start_server(monitor_server_key)
        return {
            "monitorServerKey": monitor_server_key,
            "cleanup": cleanup,
            "launch": launch,
            "message": f"Recreated monitor server {monitor_server_key}",
        }

    def start_after_monitor(
        self,
        *,
        monitor_server_key: str,
        start_server_keys: list[str] | None = None,
    ) -> dict[str, Any]:
        normalized_keys = (
            [server.key for server in self._servers_for_keys(start_server_keys)]
            if start_server_keys
            else self._default_start_server_keys(monitor_server_key)
        )
        result = self._run_servers("start", normalized_keys)
        return {
            **result,
            "defaulted": not bool(start_server_keys),
            "monitorServerKey": monitor_server_key,
            "message": (
                f"Started {result['total']} selected servers"
                if start_server_keys
                else f"Started {result['total']} servers after monitor success"
            ),
        }

    def build_summary(self) -> dict[str, Any]:
        servers = self.list_servers()
        running = sum(1 for item in servers if item["state"] == "running")
        return {
            "configuredServers": len(self.config.servers),
            "runningServers": running,
            "missingServers": sum(1 for item in servers if item["state"] == "missing"),
        }

    def send_rcon_command(
        self,
        group: str,
        command: str,
        *,
        server_keys: list[str] | None = None,
        targets: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        try:
            from rcon import Client
        except ImportError as exc:  # pragma: no cover - depends on deployment package
            raise RuntimeError("Python rcon package is not installed") from exc

        override_password_by_key: dict[str, str] = {}
        if isinstance(targets, list):
            for item in targets:
                if not isinstance(item, dict):
                    continue
                key = str(item.get("key") or "").strip()
                password = str(item.get("password") or "").strip()
                if key and password:
                    override_password_by_key[key] = password

        effective_server_keys = server_keys or list(override_password_by_key.keys())
        resolved_targets = (
            self._servers_for_keys(effective_server_keys)
            if effective_server_keys
            else self.get_group(group)
        )
        results = []
        success = 0

        for server in resolved_targets:
            self._raise_if_cancel_requested()
            port = self._server_primary_port(server)
            response_text = ""
            ok = False
            error_message = ""
            password = (
                override_password_by_key.get(server.key)
                or str(server.rcon_password or "").strip()
                or self.config.rcon_password
            )
            try:
                if not str(password or "").strip():
                    raise RuntimeError("RCON password is empty")

                with Client(
                    self.config.rcon_host,
                    port,
                    passwd=password,
                    timeout=self.config.rcon_timeout_seconds,
                ) as client:
                    response_text = str(client.run(command) or "").strip()
                ok = True
                success += 1
            except Exception as exc:  # noqa: BLE001
                error_message = str(exc)

            results.append(
                {
                    "key": server.key,
                    "port": port,
                    "ok": ok,
                    "response": response_text,
                    "error": error_message or None,
                }
            )

        return {
            "group": group,
            "command": command,
            "total": len(results),
            "success": success,
            "failed": len(results) - success,
            "results": results,
            "message": f"RCON sent to {len(results)} servers, success {success}, failed {len(results) - success}",
        }

    def get_oldver(self) -> dict[str, Any]:
        self._raise_if_cancel_requested()
        manifest_path = Path(self.config.cs2_root) / "steamapps" / f"appmanifest_{self.config.app_id}.acf"
        if not manifest_path.exists():
            raise RuntimeError(f"Manifest not found: {manifest_path}")

        content = manifest_path.read_text(encoding="utf-8", errors="ignore")
        matched = re.search(r'"buildid"\s+"(\d+)"', content)
        if not matched:
            raise RuntimeError("Failed to extract local buildid")

        build_id = matched.group(1)
        self._emit_log(f"Read local manifest buildid {build_id}")
        return {
            "buildId": build_id,
            "message": f"Current buildid: {build_id}",
        }

    @staticmethod
    def _extract_remote_buildid_from_appinfo(output: str) -> str | None:
        matched = re.search(
            r'"branches"\s*:?\s*\{.*?"public"\s*:?\s*\{.*?"buildid"\s*:?\s*"([^"]+)"',
            str(output or ""),
            flags=re.DOTALL,
        )
        if not matched:
            return None
        return matched.group(1)

    def get_nowver(self) -> dict[str, Any]:
        self._raise_if_cancel_requested()
        self._emit_log(f"Running steamcmd app_info_print for app {self.config.app_id}")
        started_at = time.time()
        timeout_seconds = 120
        try:
            result = subprocess.run(
                [
                    self.config.steamcmd_sh,
                    "+login",
                    "anonymous",
                    "+app_info_print",
                    str(self.config.app_id),
                    "+quit",
                ],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="ignore",
                timeout=timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(
                f"steamcmd app_info_print timed out after {timeout_seconds} seconds while checking app {self.config.app_id}"
            ) from exc

        self._raise_if_cancel_requested()
        output = ANSI_ESCAPE_RE.sub("", result.stdout or "").strip()
        stderr_output = ANSI_ESCAPE_RE.sub("", result.stderr or "").strip()
        if result.returncode != 0:
            raise RuntimeError(stderr_output or output or "steamcmd app_info_print failed")

        self._emit_log(
            f"steamcmd app_info_print completed in {self._format_elapsed(time.time() - started_at)}, parsing remote buildid"
        )
        build_id = self._extract_remote_buildid_from_appinfo(output)
        if not build_id:
            raise RuntimeError("Failed to extract remote buildid")
        self._emit_log(f"Resolved remote buildid {build_id}")
        return {
            "buildId": build_id,
            "message": f"Latest buildid: {build_id}",
        }

    @staticmethod
    def _insert_metamod_search_path(content: str) -> tuple[str, bool]:
        newline = "\r\n" if "\r\n" in content else "\n"
        normalized = content.replace("\r\n", "\n")

        if re.search(
            r"(?m)^[ \t]*Game[ \t]+csgo/addons/metamod(?:[ \t]*(?://.*)?)?$",
            normalized,
        ):
            return content, False

        match = re.search(
            r"(?m)^([ \t]*)Game([ \t]+)csgo(?:[ \t]*(?://.*)?)?$",
            normalized,
        )
        if not match:
            raise RuntimeError("Game csgo search path not found in gameinfo.gi")

        indent, separator = match.group(1), match.group(2)
        metamod_line = f"{indent}Game{separator}csgo/addons/metamod"
        updated = (
            normalized[: match.start()]
            + metamod_line
            + "\n"
            + normalized[match.start() :]
        )
        return updated.replace("\n", newline), True

    def ensure_metamod_path(self) -> dict[str, Any]:
        self._raise_if_cancel_requested()
        target = Path(self.config.cs2_root) / "game" / "csgo" / "gameinfo.gi"
        if not target.exists():
            raise RuntimeError(f"gameinfo.gi not found: {target}")

        content = target.read_text(encoding="utf-8", errors="ignore")
        updated, changed = self._insert_metamod_search_path(content)
        if not changed:
            self._emit_log("Metamod search path already exists in gameinfo.gi")
            return {"changed": False, "message": "Metamod path already exists"}

        target.write_text(updated, encoding="utf-8")
        self._emit_log("Inserted Metamod search path into gameinfo.gi")
        return {"changed": True, "message": "Metamod path inserted"}

    def _run_app_update_validate(self) -> dict[str, Any]:
        self._raise_if_cancel_requested()
        self._emit_log("Removing configured containers before steamcmd validate")
        stop_all = self.remove_all()
        self._emit_log(
            f"Removed {stop_all['changed']} of {stop_all['total']} configured containers before validate"
        )
        manifest_path = Path(self.config.cs2_root) / "steamapps" / f"appmanifest_{self.config.app_id}.acf"
        if manifest_path.exists():
            manifest_path.unlink()
            self._emit_log(f"Deleted manifest before validate: {manifest_path}")
        else:
            self._emit_log(f"Manifest already missing before validate: {manifest_path}")
        self._emit_log(f"Running steamcmd app_update {self.config.app_id} validate")
        result = self._run_process(
            [
                self.config.steamcmd_sh,
                "+force_install_dir",
                self.config.cs2_root,
                "+login",
                "anonymous",
                "+app_update",
                str(self.config.app_id),
                "validate",
                "+quit",
            ],
            timeout_seconds=3600,
            use_pty=True,
        )
        if not result["ok"]:
            raise RuntimeError(result["output"] or "steamcmd app_update failed")

        self._emit_log("steamcmd app_update validate completed successfully")
        metamod = self.ensure_metamod_path()
        return {
            "stopAll": stop_all,
            "output": result["output"],
            "metamod": metamod,
        }

    def _run_update_pipeline(
        self,
        *,
        start_after_success: bool,
        monitor_server_key: str | None = None,
        start_server_keys: list[str] | None = None,
    ) -> dict[str, Any]:
        self._raise_if_cancel_requested()
        self._emit_log("Checking local buildid")
        local_build = self.get_oldver()["buildId"]
        self._emit_log("Checking latest remote buildid")
        remote_build = self.get_nowver()["buildId"]
        needs_update = local_build != remote_build
        self._emit_log(f"Compared buildid local={local_build} remote={remote_build}")

        if not needs_update:
            self._emit_log(
                "No update detected, skipped validate and monitor check. Run node.monitor_check if you still want a crash check."
            )
            return {
                "currentBuildId": local_build,
                "latestBuildId": remote_build,
                "needsUpdate": False,
                "updated": False,
                "validated": False,
                "monitor": None,
                "message": "Already latest version, skipped validate and monitor",
            }

        self._emit_log("Update detected, starting validate pipeline")
        validated = self.check_validate()
        self._emit_log("Validate completed, starting monitor check")
        monitored = self.monitor_check(
            start_after_success=start_after_success,
            monitor_server_key=monitor_server_key,
            start_server_keys=start_server_keys,
        )
        return {
            **validated,
            "currentBuildId": validated["currentBuildId"],
            "latestBuildId": remote_build,
            "needsUpdate": False,
            "monitor": monitored,
            "message": monitored["message"],
        }

    def check_update(
        self,
        *,
        monitor_server_key: str | None = None,
        start_server_keys: list[str] | None = None,
    ) -> dict[str, Any]:
        return self._run_update_pipeline(
            start_after_success=True,
            monitor_server_key=monitor_server_key,
            start_server_keys=start_server_keys,
        )

    def check_validate(self) -> dict[str, Any]:
        self._raise_if_cancel_requested()
        before_build = self.get_oldver()["buildId"]
        update = self._run_app_update_validate()
        latest = self.get_oldver()["buildId"]
        return {
            "validated": True,
            "updated": before_build != latest,
            "previousBuildId": before_build,
            "currentBuildId": latest,
            "latestBuildId": latest,
            "needsUpdate": False,
            "message": (
                f"Validated and updated to buildid {latest}"
                if before_build != latest
                else f"Validated current buildid {latest}"
            ),
            "update": update,
        }

    def monitor_check(
        self,
        start_after_success: bool = False,
        *,
        monitor_server_key: str | None = None,
        start_server_keys: list[str] | None = None,
    ) -> dict[str, Any]:
        self._raise_if_cancel_requested()
        monitor_key = self._resolve_monitor_server_key(monitor_server_key)
        server = self.get_server(monitor_key)
        self._emit_log(
            f"Launching monitor server {monitor_key} using container {server.container_name}"
        )
        self._emit_log(
            "Monitor thresholds: "
            f"stable={self.config.monitor_stable_seconds}s, "
            f"recoverTimeout={self.config.monitor_recover_timeout_seconds}s, "
            f"poll={max(1, self.config.monitor_poll_interval_seconds)}s"
        )
        launch = self._launch_monitor_server(monitor_key)
        container = self._get_container(server.container_name)
        if not container:
            raise RuntimeError(f"Monitor container missing after launch: {server.container_name}")

        container.reload()
        base_restart_count = int(container.attrs.get("RestartCount") or container.attrs.get("State", {}).get("RestartCount") or 0)
        running_since = 0.0
        non_running_since = 0.0
        last_status = ""
        last_restart_count: int | None = None
        monitor_started_at = time.time()
        last_progress_log_at = 0.0
        timeline: list[dict[str, Any]] = []

        while True:
            self._raise_if_cancel_requested()
            container = self._get_container(server.container_name)
            if not container:
                raise RuntimeError(f"Monitor container missing: {server.container_name}")

            container.reload()
            state = container.attrs.get("State", {})
            status = str(state.get("Status") or container.status or "").lower()
            restart_count = int(state.get("RestartCount") or 0)
            delta = restart_count - base_restart_count
            now = time.time()

            timeline.append({"status": status, "restartCount": restart_count, "timestamp": int(now)})
            if status != last_status or restart_count != last_restart_count:
                self._emit_log(f"Monitor {monitor_key}: status={status}, restartCount={restart_count}")
                last_restart_count = restart_count

            if delta >= self.config.monitor_restart_threshold:
                self.remove_server(monitor_key)
                raise RuntimeError(f"Restart threshold reached for {monitor_key}: {delta}")

            if status == "running":
                if last_status != "running":
                    running_since = now
                    non_running_since = 0.0

                if now - last_progress_log_at >= max(5, self.config.monitor_poll_interval_seconds):
                    running_elapsed = now - running_since if running_since > 0 else 0
                    total_elapsed = now - monitor_started_at
                    self._emit_log(
                        f"Monitor {monitor_key}: elapsed={self._format_elapsed(total_elapsed)}, "
                        f"stableRunning={self._format_elapsed(running_elapsed)}/{self.config.monitor_stable_seconds}s, "
                        f"restartCount={restart_count}"
                    )
                    last_progress_log_at = now

                if now - running_since >= self.config.monitor_stable_seconds:
                    self._emit_log(
                        f"Monitor {monitor_key} stayed running for {self.config.monitor_stable_seconds} seconds"
                    )
                    container.stop(timeout=10)
                    result: dict[str, Any] = {
                        "ok": True,
                        "monitorServerKey": monitor_key,
                        "monitorServer": self.inspect_server(monitor_key),
                        "monitorLaunch": launch,
                        "timeline": timeline[-50:],
                        "message": f"Monitor success after {self.config.monitor_stable_seconds} stable seconds",
                    }
                    if start_after_success:
                        self._emit_log("Monitor passed, starting selected servers")
                        result["startServers"] = self.start_after_monitor(
                            monitor_server_key=monitor_key,
                            start_server_keys=start_server_keys,
                        )
                        self._emit_log(result["startServers"]["message"])
                        result["message"] = (
                            f"{result['message']}, {result['startServers']['message'].lower()}"
                        )
                    return result
            else:
                if non_running_since <= 0:
                    non_running_since = now
                if now - last_progress_log_at >= max(5, self.config.monitor_poll_interval_seconds):
                    waiting_elapsed = now - non_running_since if non_running_since > 0 else 0
                    total_elapsed = now - monitor_started_at
                    self._emit_log(
                        f"Monitor {monitor_key}: elapsed={self._format_elapsed(total_elapsed)}, "
                        f"status={status}, recoverWait={self._format_elapsed(waiting_elapsed)}/{self.config.monitor_recover_timeout_seconds}s, "
                        f"restartCount={restart_count}"
                    )
                    last_progress_log_at = now
                if now - non_running_since >= self.config.monitor_recover_timeout_seconds:
                    self.remove_server(monitor_key)
                    raise RuntimeError(f"Monitor timeout for {monitor_key}: status={status}")

            last_status = status
            time.sleep(max(1, self.config.monitor_poll_interval_seconds))
