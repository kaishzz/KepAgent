import sys
import types
import unittest

if "docker" not in sys.modules:
    docker_module = types.ModuleType("docker")
    docker_module.DockerClient = object
    docker_module.from_env = lambda: None

    errors_module = types.ModuleType("docker.errors")

    class NotFound(Exception):
        pass

    errors_module.NotFound = NotFound
    docker_module.errors = errors_module

    sys.modules["docker"] = docker_module
    sys.modules["docker.errors"] = errors_module

from kepagent.app import KepAgentApp, LiveCommandLogger


class CompactFinishResultTests(unittest.TestCase):
    def test_compacts_large_check_update_result(self) -> None:
        result = {
            "validated": True,
            "updated": True,
            "previousBuildId": "1",
            "currentBuildId": "2",
            "latestBuildId": "2",
            "message": "Monitor success after 120 stable seconds, started 12 servers after monitor success",
            "update": {
                "stopAll": {
                    "action": "remove",
                    "group": "ALL",
                    "changed": 12,
                    "total": 13,
                    "results": [{"message": f"removed server {index}", "changed": True} for index in range(13)],
                },
                "output": "\n".join(f"line {index}" for index in range(300)),
                "metamod": {"changed": True, "message": "Metamod path inserted"},
            },
            "monitorServer": {
                "key": "ze_xl_test",
                "containerName": "kepcs-2192-1",
                "state": "running",
                "status": "running",
                "primaryPort": 32010,
                "restartCount": 0,
                "id": "very-long-id",
            },
            "monitorLaunch": {
                "monitorServerKey": "ze_xl_test",
                "message": "Recreated monitor server ze_xl_test",
                "cleanup": {
                    "action": "remove",
                    "changed": 1,
                    "total": 1,
                    "results": [{"message": "cleanup"}],
                },
                "launch": {
                    "changed": True,
                    "message": "started",
                    "server": {
                        "key": "ze_xl_test",
                        "containerName": "kepcs-2192-1",
                        "state": "running",
                        "status": "running",
                        "primaryPort": 32010,
                        "restartCount": 0,
                    },
                },
            },
            "timeline": [{"status": "running", "restartCount": 0, "timestamp": index} for index in range(50)],
            "startServers": {
                "scope": "servers",
                "action": "start",
                "changed": 12,
                "total": 12,
                "serverKeys": [f"ze_xl_{index}" for index in range(1, 7)] + [f"ze_pt_{index}" for index in range(1, 7)],
                "results": [
                    {
                        "changed": True,
                        "message": f"server {index} started",
                        "server": {
                            "key": (
                                f"ze_xl_{index}"
                                if index <= 6
                                else f"ze_pt_{index - 6}"
                            ),
                            "containerName": (
                                f"kepcs-2102-{index}"
                                if index <= 6
                                else f"kepcs-2103-{index - 6}"
                            ),
                        },
                    }
                    for index in range(1, 13)
                ],
                "message": "Started 12 servers after monitor success",
            },
        }

        compact = KepAgentApp._compact_finish_result("node.check_update", result)

        self.assertTrue(compact["validated"])
        self.assertEqual(compact["update"]["outputLineCount"], 300)
        self.assertNotIn("output", compact["update"])
        self.assertEqual(len(compact["timeline"]), 10)
        self.assertEqual(compact["timelineTruncated"], 40)
        self.assertEqual(compact["startServers"]["total"], 12)
        self.assertEqual(len(compact["startServers"]["messages"]), 12)
        self.assertEqual(len(compact["startServers"]["results"]), 12)
        self.assertEqual(compact["startServers"]["results"][0]["server"]["key"], "ze_xl_1")


class ProcessOneCommandTests(unittest.TestCase):
    def test_finishes_successful_command_without_name_error(self) -> None:
        finished: dict[str, object] = {}
        appended_batches: list[list[dict[str, str]]] = []

        class FakeClient:
            def claim_command(self):
                return {
                    "id": "command-1",
                    "commandType": "node.get_oldver",
                    "payload": {},
                }

            def mark_command_started(self, _command_id: str):
                return {"status": "RUNNING"}

            def append_command_logs(self, _command_id: str, batch):
                appended_batches.append(list(batch))

            def send_heartbeat(self, _payload):
                return {"success": True}

            def finish_command(self, command_id: str, *, success: bool, result, **kwargs):
                finished.update(
                    {
                        "command_id": command_id,
                        "success": success,
                        "result": result,
                        "extra": kwargs,
                    }
                )

        class FakeRuntime:
            def set_cancel_reader(self, _reader):
                return None

            def set_log_emitter(self, _emitter):
                return None

            def set_state_reporter(self, _reporter):
                return None

            def list_servers(self):
                return []

            def build_summary(self, servers=None):
                return {"configuredServers": 0, "runningServers": 0, "missingServers": 0}

        app = KepAgentApp.__new__(KepAgentApp)
        app.config = types.SimpleNamespace(group_labels={}, group_order=[])
        app.client = FakeClient()
        app.runtime = FakeRuntime()
        app.execute_command = lambda _command, logs: (
            logs.append("Current buildid: 22880072") or {"ok": True, "result": {"message": "Current buildid: 22880072"}}
        )

        app.process_one_command()

        self.assertEqual(finished["command_id"], "command-1")
        self.assertTrue(finished["success"])
        self.assertEqual(
            finished["result"],
            {"message": "Current buildid: 22880072"},
        )
        self.assertGreaterEqual(len(appended_batches), 1)

    def test_log_upload_failure_does_not_fail_command(self) -> None:
        finished: dict[str, object] = {}
        upload_attempts: list[list[dict[str, str]]] = []

        class FakeClient:
            def claim_command(self):
                return {
                    "id": "command-2",
                    "commandType": "node.check_update",
                    "payload": {},
                }

            def mark_command_started(self, _command_id: str):
                return {"status": "RUNNING"}

            def append_command_logs(self, _command_id: str, batch):
                upload_attempts.append(list(batch))
                raise RuntimeError("log upload timeout")

            def send_heartbeat(self, _payload):
                return {"success": True}

            def finish_command(self, command_id: str, *, success: bool, result=None, **kwargs):
                finished.update(
                    {
                        "command_id": command_id,
                        "success": success,
                        "result": result,
                        "extra": kwargs,
                    }
                )

        class FakeRuntime:
            def set_cancel_reader(self, _reader):
                return None

            def set_log_emitter(self, _emitter):
                return None

            def set_state_reporter(self, _reporter):
                return None

            def list_servers(self):
                return []

            def build_summary(self, servers=None):
                return {"configuredServers": 0, "runningServers": 0, "missingServers": 0}

        app = KepAgentApp.__new__(KepAgentApp)
        app.config = types.SimpleNamespace(group_labels={}, group_order=[])
        app.client = FakeClient()
        app.runtime = FakeRuntime()
        app.execute_command = lambda _command, logs: (
            logs.append("Update detected") or {"ok": True, "result": {"message": "Monitor success", "updated": True}}
        )

        app.process_one_command()

        self.assertEqual(finished["command_id"], "command-2")
        self.assertTrue(finished["success"])
        self.assertEqual(
            finished["result"],
            {"message": "Monitor success", "updated": True},
        )
        self.assertGreaterEqual(len(upload_attempts), 1)

    def test_finish_timeout_after_success_does_not_mark_command_failed(self) -> None:
        finished_attempts: list[dict[str, object]] = []

        class FakeClient:
            def __init__(self) -> None:
                self._finish_calls = 0

            def claim_command(self):
                return {
                    "id": "command-3",
                    "commandType": "node.check_update",
                    "payload": {},
                }

            def mark_command_started(self, _command_id: str):
                return {"status": "RUNNING"}

            def append_command_logs(self, _command_id: str, _batch):
                return None

            def send_heartbeat(self, _payload):
                return {"success": True}

            def finish_command(self, command_id: str, *, success: bool, result=None, **kwargs):
                self._finish_calls += 1
                finished_attempts.append(
                    {
                        "command_id": command_id,
                        "success": success,
                        "result": result,
                        "extra": kwargs,
                    }
                )
                raise RuntimeError("finish timeout")

            def fetch_command(self, _command_id: str):
                return {"id": "command-3", "status": "SUCCEEDED"}

        class FakeRuntime:
            def set_cancel_reader(self, _reader):
                return None

            def set_log_emitter(self, _emitter):
                return None

            def set_state_reporter(self, _reporter):
                return None

            def list_servers(self):
                return []

            def build_summary(self, servers=None):
                return {"configuredServers": 0, "runningServers": 0, "missingServers": 0}

        app = KepAgentApp.__new__(KepAgentApp)
        app.config = types.SimpleNamespace(group_labels={}, group_order=[])
        app.client = FakeClient()
        app.runtime = FakeRuntime()
        app.execute_command = lambda _command, logs: (
            logs.append("Monitor success") or {"ok": True, "result": {"message": "Monitor success", "updated": True}}
        )

        app.process_one_command()

        self.assertEqual(len(finished_attempts), 1)
        self.assertTrue(finished_attempts[0]["success"])
        self.assertEqual(
            finished_attempts[0]["result"],
            {"message": "Monitor success", "updated": True},
        )


class ServerActionHandlerTests(unittest.TestCase):
    def test_uses_batch_server_keys_when_present(self) -> None:
        class FakeRuntime:
            def __init__(self) -> None:
                self.calls: list[tuple[str, list[str]]] = []

            def start_servers(self, server_keys: list[str]) -> dict[str, object]:
                self.calls.append(("start_servers", server_keys))
                return {
                    "scope": "servers",
                    "action": "start",
                    "serverKeys": server_keys,
                    "changed": 2,
                    "total": len(server_keys),
                    "results": [],
                }

            def start_server(self, _key: str) -> dict[str, object]:
                raise AssertionError("single-server handler should not be used")

        runtime = FakeRuntime()
        app = KepAgentApp.__new__(KepAgentApp)
        app.runtime = runtime
        logs = LiveCommandLogger(lambda _batch: None)

        result = app._handle_start_server(
            {"serverKeys": ["ze_xl_1", " ", "ze_xl_1", "ze_pt_1"]},
            logs,
        )

        self.assertEqual(runtime.calls, [("start_servers", ["ze_xl_1", "ze_pt_1"])])
        self.assertTrue(result["ok"])
        self.assertEqual(result["result"]["total"], 2)
        self.assertEqual(result["logs"], ["Batch start handled 2 servers, changed 2"])

    def test_keeps_single_key_compatibility(self) -> None:
        class FakeRuntime:
            def __init__(self) -> None:
                self.calls: list[tuple[str, str]] = []

            def restart_server(self, key: str) -> dict[str, object]:
                self.calls.append(("restart_server", key))
                return {"changed": True, "message": f"{key} restarted"}

            def restart_servers(self, _server_keys: list[str]) -> dict[str, object]:
                raise AssertionError("batch handler should not be used")

        runtime = FakeRuntime()
        app = KepAgentApp.__new__(KepAgentApp)
        app.runtime = runtime
        logs = LiveCommandLogger(lambda _batch: None)

        result = app._handle_restart_server({"key": "ze_xl_1"}, logs)

        self.assertEqual(runtime.calls, [("restart_server", "ze_xl_1")])
        self.assertTrue(result["ok"])
        self.assertEqual(result["result"]["message"], "ze_xl_1 restarted")
        self.assertEqual(result["logs"], ["ze_xl_1 restarted"])


class HeartbeatPayloadTests(unittest.TestCase):
    def test_build_heartbeat_payload_reuses_single_server_snapshot(self) -> None:
        class FakeRuntime:
            def __init__(self) -> None:
                self.calls: list[tuple[str, object]] = []

            def list_servers(self, *, use_cached_only=False):
                self.calls.append(("list_servers", use_cached_only))
                return [{"key": "ze_xl_1", "state": "running"}]

            def refresh_server_snapshots_now(self, server_keys):
                self.calls.append(("refresh_server_snapshots_now", list(server_keys)))
                return [{"key": key, "state": "running"} for key in server_keys]

            def build_summary(self, servers=None):
                self.calls.append(("build_summary", servers))
                return {"configuredServers": 1, "runningServers": 1, "missingServers": 0}

        app = KepAgentApp.__new__(KepAgentApp)
        app.config = types.SimpleNamespace(group_labels={"2102": "训练服"}, group_order=["2102"])
        app.runtime = FakeRuntime()

        payload = app.build_heartbeat_payload()

        self.assertEqual(payload["servers"], [{"key": "ze_xl_1", "state": "running"}])
        self.assertEqual(payload["summary"]["runningServers"], 1)
        self.assertEqual(app.runtime.calls[0], ("list_servers", False))
        self.assertEqual(app.runtime.calls[1][0], "build_summary")
        self.assertIs(app.runtime.calls[1][1], payload["servers"])

    def test_build_heartbeat_payload_force_refreshes_target_servers(self) -> None:
        class FakeRuntime:
            def __init__(self) -> None:
                self.calls: list[tuple[str, object]] = []

            def list_servers(self, *, use_cached_only=False):
                self.calls.append(("list_servers", use_cached_only))
                return [{"key": "ze_xl_1", "state": "running", "online": 12, "capacity": 64}]

            def refresh_server_snapshots_now(self, server_keys):
                self.calls.append(("refresh_server_snapshots_now", list(server_keys)))
                return [{"key": key, "state": "running"} for key in server_keys]

            def build_summary(self, servers=None):
                self.calls.append(("build_summary", servers))
                return {"configuredServers": 1, "runningServers": 1, "missingServers": 0}

        app = KepAgentApp.__new__(KepAgentApp)
        app.config = types.SimpleNamespace(group_labels={"2102": "训练服"}, group_order=["2102"])
        app.runtime = FakeRuntime()

        payload = app.build_heartbeat_payload(["ze_xl_1"])

        self.assertEqual(payload["servers"][0]["online"], 12)
        self.assertEqual(payload["servers"][0]["capacity"], 64)
        self.assertEqual(app.runtime.calls[0], ("refresh_server_snapshots_now", ["ze_xl_1"]))
        self.assertEqual(app.runtime.calls[1], ("list_servers", True))

    def test_report_runtime_state_reuses_heartbeat_payload(self) -> None:
        sent_payloads: list[dict[str, object]] = []

        class FakeClient:
            def send_heartbeat(self, payload):
                sent_payloads.append(payload)
                return {"success": True}

        app = KepAgentApp.__new__(KepAgentApp)
        app.client = FakeClient()
        app.build_heartbeat_payload = lambda server_keys=None: {"servers": [{"key": "ze_xl_1"}], "summary": {"runningServers": 1}, "serverKeys": server_keys}

        app.report_runtime_state(["ze_xl_1"])

        self.assertEqual(
            sent_payloads,
            [{"servers": [{"key": "ze_xl_1"}], "summary": {"runningServers": 1}, "serverKeys": ["ze_xl_1"]}],
        )

    def test_report_runtime_state_safely_swallows_errors(self) -> None:
        class FakeClient:
            def send_heartbeat(self, _payload):
                raise RuntimeError("network down")

        app = KepAgentApp.__new__(KepAgentApp)
        app.client = FakeClient()
        app.build_heartbeat_payload = lambda server_keys=None: {"servers": [], "serverKeys": server_keys}

        self.assertFalse(app.report_runtime_state_safely(["ze_xl_1"]))


class CompactServerSnapshotTests(unittest.TestCase):
    def test_keeps_runtime_status_fields_for_server_patch(self) -> None:
        compact = KepAgentApp._compact_server_snapshot(
            {
                "key": "ze_xl_1",
                "containerName": "kepcs-2102-1",
                "state": "running",
                "status": "running",
                "containerStatus": "running",
                "agentA2sStatus": "ok",
                "online": 12,
                "capacity": 64,
                "map": "ze_mist",
                "serverName": "训练服 1",
                "queryPending": False,
                "queryStale": False,
                "restartCount": 1,
            }
        )

        self.assertEqual(compact["containerStatus"], "running")
        self.assertEqual(compact["agentA2sStatus"], "ok")
        self.assertEqual(compact["online"], 12)
        self.assertEqual(compact["capacity"], 64)
        self.assertEqual(compact["map"], "ze_mist")


if __name__ == "__main__":
    unittest.main()
