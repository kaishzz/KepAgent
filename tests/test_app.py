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

from kepagent.app import KepAgentApp


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
                "containerName": "kepcs-ze-xl-test-32010",
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
                        "containerName": "kepcs-ze-xl-test-32010",
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
                                f"kepcs-ze-xl-{28000 + index * 10}"
                                if index <= 6
                                else f"kepcs-ze-pt-{29000 + (index - 6) * 10}"
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
        self.assertNotIn("results", compact["startServers"])


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

        app = KepAgentApp.__new__(KepAgentApp)
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


if __name__ == "__main__":
    unittest.main()
