from __future__ import annotations

import re
import subprocess
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

import docker
from docker.errors import NotFound

from .config import AgentConfig, PortBinding, ServerDefinition, VolumeBinding


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

    def _servers_for_group(self, group: str) -> list[ServerDefinition]:
        return self.get_group(group)

    def _get_container(self, container_name: str):
        try:
            return self.client.containers.get(container_name)
        except NotFound:
            return None

    def _run_process(
        self,
        args: list[str],
        *,
        timeout_seconds: int = 600,
        cwd: str | None = None,
    ) -> dict[str, Any]:
        completed = subprocess.run(
            args,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
        output = "\n".join(
            part.strip()
            for part in [completed.stdout, completed.stderr]
            if str(part or "").strip()
        ).strip()
        return {
            "ok": completed.returncode == 0,
            "code": completed.returncode,
            "output": output,
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

    def stop_server(self, key: str, timeout: int = 10) -> dict[str, Any]:
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

    def _run_all(self, action: str) -> dict[str, Any]:
        results = []
        changed = 0

        for server in self.config.servers:
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

    def build_summary(self) -> dict[str, Any]:
        servers = self.list_servers()
        running = sum(1 for item in servers if item["state"] == "running")
        return {
            "configuredServers": len(self.config.servers),
            "runningServers": running,
            "missingServers": sum(1 for item in servers if item["state"] == "missing"),
        }

    def send_rcon_command(self, group: str, command: str) -> dict[str, Any]:
        if not self.config.rcon_password:
            raise RuntimeError("RCON password is empty")

        try:
            from rcon import Client
        except ImportError as exc:  # pragma: no cover - depends on deployment package
            raise RuntimeError("Python rcon package is not installed") from exc

        targets = self._servers_for_group(group)
        results = []
        success = 0

        for server in targets:
          port = self._server_primary_port(server)
          response_text = ""
          ok = False
          error_message = ""
          try:
              with Client(
                  self.config.rcon_host,
                  port,
                  passwd=self.config.rcon_password,
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
        manifest_path = Path(self.config.cs2_root) / "steamapps" / f"appmanifest_{self.config.app_id}.acf"
        if not manifest_path.exists():
            raise RuntimeError(f"Manifest not found: {manifest_path}")

        content = manifest_path.read_text(encoding="utf-8", errors="ignore")
        matched = re.search(r'"buildid"\s+"(\d+)"', content)
        if not matched:
            raise RuntimeError("Failed to extract local buildid")

        build_id = matched.group(1)
        return {
            "buildId": build_id,
            "message": f"Current buildid: {build_id}",
        }

    def get_nowver(self) -> dict[str, Any]:
        result = self._run_process(
            [
                self.config.steamcmd_sh,
                "+login",
                "anonymous",
                "+app_info_print",
                str(self.config.app_id),
                "+quit",
            ],
            timeout_seconds=180,
        )
        if not result["ok"]:
            raise RuntimeError(result["output"] or "steamcmd app_info_print failed")

        output = result["output"]
        matched = re.search(r'"public"\s*\{.*?"buildid"\s*"(\d+)"', output, flags=re.S)
        if not matched:
            raise RuntimeError("Failed to extract remote buildid")

        build_id = matched.group(1)
        return {
            "buildId": build_id,
            "message": f"Latest buildid: {build_id}",
        }

    def ensure_metamod_path(self) -> dict[str, Any]:
        target = Path(self.config.cs2_root) / "game" / "csgo" / "gameinfo.gi"
        if not target.exists():
            raise RuntimeError(f"gameinfo.gi not found: {target}")

        content = target.read_text(encoding="utf-8", errors="ignore").replace("\r\n", "\n")
        metamod_line = "                        Game    csgo/addons/metamod"
        if "csgo/addons/metamod" in content:
            return {"changed": False, "message": "Metamod path already exists"}

        updated = content.replace("\n                        Game    csgo", f"\n{metamod_line}\n                        Game    csgo", 1)
        target.write_text(updated, encoding="utf-8")
        return {"changed": True, "message": "Metamod path inserted"}

    def _run_update(self) -> dict[str, Any]:
        self.remove_all()
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
        )
        if not result["ok"]:
            raise RuntimeError(result["output"] or "steamcmd app_update failed")

        metamod = self.ensure_metamod_path()
        return {
            "output": result["output"],
            "metamod": metamod,
        }

    def check_update(self) -> dict[str, Any]:
        local_build = self.get_oldver()["buildId"]
        remote_build = self.get_nowver()["buildId"]
        needs_update = local_build != remote_build
        return {
            "currentBuildId": local_build,
            "latestBuildId": remote_build,
            "needsUpdate": needs_update,
            "message": "Update available" if needs_update else "Already latest version",
        }

    def check_validate(self) -> dict[str, Any]:
        check = self.check_update()
        if not check["needsUpdate"]:
            return check

        update = self._run_update()
        latest = self.get_oldver()["buildId"]
        return {
            **check,
            "updated": True,
            "currentBuildId": latest,
            "message": f"Updated to buildid {latest}",
            "update": update,
        }

    def monitor_check(self, start_after_success: bool = False) -> dict[str, Any]:
        monitor_key = self.config.monitor_server_key or (self.config.servers[0].key if self.config.servers else "")
        if not monitor_key:
            raise RuntimeError("No monitor server key configured")

        server = self.get_server(monitor_key)
        container = self._get_container(server.container_name)
        if not container:
            raise RuntimeError(f"Monitor container missing: {server.container_name}")

        container.reload()
        base_restart_count = int(container.attrs.get("RestartCount") or container.attrs.get("State", {}).get("RestartCount") or 0)
        running_since = 0.0
        non_running_since = 0.0
        last_status = ""
        timeline: list[dict[str, Any]] = []

        while True:
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

            if delta >= self.config.monitor_restart_threshold:
                self.remove_server(monitor_key)
                raise RuntimeError(f"Restart threshold reached for {monitor_key}: {delta}")

            if status == "running":
                if last_status != "running":
                    running_since = now
                    non_running_since = 0.0

                if now - running_since >= self.config.monitor_stable_seconds:
                    container.stop(timeout=10)
                    result: dict[str, Any] = {
                        "ok": True,
                        "monitorServerKey": monitor_key,
                        "timeline": timeline[-50:],
                        "message": f"Monitor success after {self.config.monitor_stable_seconds} stable seconds",
                    }
                    if start_after_success:
                        result["startAll"] = self.start_all()
                        result["message"] = f"{result['message']}, all servers started"
                    return result
            else:
                if non_running_since <= 0:
                    non_running_since = now
                elif now - non_running_since >= self.config.monitor_recover_timeout_seconds:
                    self.remove_server(monitor_key)
                    raise RuntimeError(f"Monitor timeout for {monitor_key}: status={status}")

            last_status = status
            time.sleep(max(1, self.config.monitor_poll_interval_seconds))

    def check_update_monitor(self, start_after_success: bool = False) -> dict[str, Any]:
        validated = self.check_validate()
        if not validated.get("updated"):
            return validated

        monitored = self.monitor_check(start_after_success=start_after_success)
        return {
            **validated,
            "monitor": monitored,
            "message": monitored["message"],
        }
