from __future__ import annotations

from typing import Any

import requests


class ControlPlaneClient:
    def __init__(self, base_url: str, api_key: str, timeout_seconds: int = 15) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Accept": "application/json",
                "X-Agent-Key": api_key,
                "Authorization": f"Bearer {api_key}",
            }
        )

    def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        headers = dict(kwargs.pop("headers", {}) or {})

        if "json" in kwargs:
            headers.setdefault("Content-Type", "application/json")

        response = self.session.request(
            method=method,
            url=f"{self.base_url}{path}",
            timeout=self.timeout_seconds,
            headers=headers or None,
            **kwargs,
        )

        try:
            data = response.json()
        except ValueError:
            data = None

        if response.status_code >= 400:
            message = ""

            if isinstance(data, dict):
                message = str(data.get("message") or "").strip()

            if not message:
                message = response.text.strip()

            if not message:
                message = f"HTTP {response.status_code}"

            raise RuntimeError(
                f"Control plane request failed: {response.status_code} {message}"
            )

        if not isinstance(data, dict):
            raise RuntimeError("Control plane returned a non-JSON response")

        if not data.get("success", False):
            raise RuntimeError(data.get("message", "Control plane request failed"))
        return data

    def fetch_me(self) -> dict[str, Any]:
        return self._request("GET", "/agent/api/me")

    def send_heartbeat(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._request("POST", "/agent/api/heartbeat", json=payload)

    def claim_command(self) -> dict[str, Any] | None:
        data = self._request("POST", "/agent/api/commands/claim")
        return data.get("command")

    def fetch_command(self, command_id: str) -> dict[str, Any] | None:
        data = self._request("GET", f"/agent/api/commands/{command_id}")
        return data.get("command")

    def mark_command_started(self, command_id: str) -> dict[str, Any]:
        return self._request("POST", f"/agent/api/commands/{command_id}/start")

    def append_command_logs(self, command_id: str, logs: list[dict[str, str]]) -> dict[str, Any]:
        return self._request("POST", f"/agent/api/commands/{command_id}/logs", json={"logs": logs})

    def finish_command(
        self,
        command_id: str,
        *,
        success: bool,
        result: dict[str, Any] | list[Any] | str | None = None,
        error_message: str | None = None,
        cancelled: bool = False,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "success": success,
            "cancelled": cancelled,
        }
        if result is not None:
            payload["result"] = result
        if error_message:
            payload["errorMessage"] = error_message
        return self._request("POST", f"/agent/api/commands/{command_id}/finish", json=payload)
