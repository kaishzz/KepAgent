from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, ValidationError

ENV_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")


class PortBinding(BaseModel):
    host_port: int
    container_port: int
    protocol: str = "tcp"


class VolumeBinding(BaseModel):
    host_path: str
    container_path: str
    mode: str = "rw"


class ServerDefinition(BaseModel):
    key: str
    container_name: str
    image: str
    groups: list[str] = Field(default_factory=list)
    start_after_monitor: bool = True
    entrypoint: list[str] | None = None
    command: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    ports: list[PortBinding] = Field(default_factory=list)
    volumes: list[VolumeBinding] = Field(default_factory=list)
    labels: dict[str, str] = Field(default_factory=dict)
    working_dir: str | None = None
    network_mode: str | None = None
    rcon_password: str = ""
    stdin_open: bool = False
    tty: bool = False
    restart_policy: str = "unless-stopped"


class MonitorProfile(BaseModel):
    key: str
    monitor_server_key: str
    start_server_keys: list[str] | None = None


class AgentConfig(BaseModel):
    api_base_url: str
    api_key: str
    poll_interval_seconds: int = 3
    heartbeat_interval_seconds: int = 5
    request_timeout_seconds: int = 15
    docker_base_url: str | None = None
    group_labels: dict[str, str] = Field(default_factory=dict)
    group_order: list[str] = Field(default_factory=list)
    rcon_host: str = "127.0.0.1"
    rcon_password: str = ""
    rcon_timeout_seconds: int = 5
    steamcmd_sh: str = "/data/steamcmd/steamcmd.sh"
    cs2_root: str = "/data/cs2"
    app_id: int = 730
    monitor_server_key: str | None = None
    monitor_poll_interval_seconds: int = 5
    monitor_stable_seconds: int = 120
    monitor_recover_timeout_seconds: int = 120
    monitor_restart_threshold: int = 2
    monitor_profiles: list[MonitorProfile] = Field(default_factory=list)
    servers: list[ServerDefinition] = Field(default_factory=list)


def _strip_optional_quotes(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        return value[1:-1]
    return value


def _load_dotenv_file(path: Path) -> None:
    if not path.exists():
        return

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].strip()
        if "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        if not key:
            continue

        os.environ.setdefault(key, _strip_optional_quotes(value.strip()))


def _resolve_env_placeholders(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _resolve_env_placeholders(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_resolve_env_placeholders(item) for item in value]
    if not isinstance(value, str):
        return value

    def replace(match: re.Match[str]) -> str:
        env_name = match.group(1)
        default_value = match.group(2)
        env_value = os.environ.get(env_name)
        if env_value is not None:
            return env_value
        if default_value is not None:
            return default_value
        raise SystemExit(
            f"Missing environment variable '{env_name}'. "
            f"Please define it in .env or the shell environment."
        )

    return ENV_PATTERN.sub(replace, value)


def load_config(path: str | Path) -> AgentConfig:
    config_path = Path(path)
    dotenv_candidates = [config_path.with_name(".env")]

    cwd_dotenv = Path.cwd() / ".env"
    if cwd_dotenv not in dotenv_candidates:
        dotenv_candidates.append(cwd_dotenv)

    for dotenv_path in dotenv_candidates:
        _load_dotenv_file(dotenv_path)

    raw: dict[str, Any] = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    raw = _resolve_env_placeholders(raw)

    try:
        return AgentConfig.model_validate(raw)
    except ValidationError as exc:
        raise SystemExit(f"Invalid config file: {exc}") from exc
