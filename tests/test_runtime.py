import unittest
import sys
import tempfile
import types
from pathlib import Path
from types import SimpleNamespace

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

from kepagent import runtime as runtime_module
from kepagent.runtime import DockerRuntime


class ExtractRemoteBuildIdFromAppInfoTests(unittest.TestCase):
    def test_extracts_buildid_from_vdf_style_output(self) -> None:
        output = """
AppID : 730, change number : 35356215/0, last change : Tue Apr 21 13:02:26 2026
"730"
{
    "depots"
    {
        "branches"
        {
            "public"
            {
                "buildid"        "19876543"
            }
        }
    }
}
"""

        self.assertEqual(
            DockerRuntime._extract_remote_buildid_from_appinfo(output),
            "19876543",
        )

    def test_extracts_buildid_from_json_style_output(self) -> None:
        output = """
{
  "730": {
    "depots": {
      "branches": {
        "public": {
          "buildid": "29876543"
        }
      }
    }
  }
}
"""

        self.assertEqual(
            DockerRuntime._extract_remote_buildid_from_appinfo(output),
            "29876543",
        )

    def test_returns_none_when_public_buildid_is_missing(self) -> None:
        output = """
{
  "730": {
    "depots": {
      "branches": {
        "beta": {
          "buildid": "39876543"
        }
      }
    }
  }
}
"""

        self.assertIsNone(DockerRuntime._extract_remote_buildid_from_appinfo(output))


class CleanupSteamappsBeforeValidateTests(unittest.TestCase):
    def test_removes_manifest_and_transient_steamapps_directories(self) -> None:
        logs: list[tuple[str, str]] = []

        with tempfile.TemporaryDirectory() as tmpdir:
            steamapps_path = Path(tmpdir) / "steamapps"
            steamapps_path.mkdir()

            manifest_path = steamapps_path / "appmanifest_730.acf"
            manifest_path.write_text('"buildid" "0"\n', encoding="utf-8")

            downloading_path = steamapps_path / "downloading"
            (downloading_path / "state").mkdir(parents=True)
            (downloading_path / "state" / "chunk.bin").write_text("x", encoding="utf-8")

            temp_path = steamapps_path / "temp"
            (temp_path / "cache").mkdir(parents=True)
            (temp_path / "cache" / "tmp.txt").write_text("x", encoding="utf-8")

            runtime = DockerRuntime.__new__(DockerRuntime)
            runtime.config = SimpleNamespace(cs2_root=tmpdir, app_id=730)
            runtime._log_emitter = lambda message, level="info": logs.append((level, message))

            runtime._cleanup_steamapps_before_validate()

            self.assertFalse(manifest_path.exists())
            self.assertFalse(downloading_path.exists())
            self.assertFalse(temp_path.exists())
            self.assertIn(
                ("info", f"Deleted manifest before validate: {manifest_path}"),
                logs,
            )
            self.assertIn(
                ("info", f"Deleted steamapps directory before validate: {downloading_path}"),
                logs,
            )
            self.assertIn(
                ("info", f"Deleted steamapps directory before validate: {temp_path}"),
                logs,
            )


class CheckValidateTests(unittest.TestCase):
    def test_continues_when_manifest_is_missing_before_validate(self) -> None:
        logs: list[tuple[str, str]] = []

        with tempfile.TemporaryDirectory() as tmpdir:
            steamapps_path = Path(tmpdir) / "steamapps"
            steamapps_path.mkdir()
            manifest_path = steamapps_path / "appmanifest_730.acf"

            runtime = DockerRuntime.__new__(DockerRuntime)
            runtime.config = SimpleNamespace(cs2_root=tmpdir, app_id=730)
            runtime._log_emitter = lambda message, level="info": logs.append((level, message))
            runtime._raise_if_cancel_requested = lambda: None

            def fake_run_app_update_validate() -> dict[str, object]:
                logs.append(("info", "validate pipeline continued"))
                manifest_path.write_text('"buildid" "42"\n', encoding="utf-8")
                return {
                    "stopAll": {"changed": 0, "total": 0},
                    "output": "",
                    "metamod": {"changed": False, "message": "Metamod path already exists"},
                }

            runtime._run_app_update_validate = fake_run_app_update_validate

            result = runtime.check_validate()

            self.assertTrue(result["validated"])
            self.assertFalse(result["updated"])
            self.assertIsNone(result["previousBuildId"])
            self.assertEqual(result["currentBuildId"], "42")
            self.assertEqual(result["latestBuildId"], "42")
            self.assertEqual(result["message"], "Validated current buildid 42")
            self.assertIn(
                ("info", f"没有 manifest: {manifest_path}"),
                logs,
            )
            self.assertIn(("info", "validate pipeline continued"), logs)


class CheckUpdateTests(unittest.TestCase):
    def test_enters_validate_directly_when_manifest_is_missing(self) -> None:
        logs: list[tuple[str, str]] = []

        with tempfile.TemporaryDirectory() as tmpdir:
            steamapps_path = Path(tmpdir) / "steamapps"
            steamapps_path.mkdir()
            manifest_path = steamapps_path / "appmanifest_730.acf"

            runtime = DockerRuntime.__new__(DockerRuntime)
            runtime.config = SimpleNamespace(cs2_root=tmpdir, app_id=730)
            runtime._log_emitter = lambda message, level="info": logs.append((level, message))
            runtime._raise_if_cancel_requested = lambda: None
            runtime.get_nowver = lambda: (_ for _ in ()).throw(AssertionError("get_nowver should not be called"))

            def fake_run_app_update_validate() -> dict[str, object]:
                logs.append(("info", "validate pipeline continued"))
                manifest_path.write_text('"buildid" "42"\n', encoding="utf-8")
                return {
                    "stopAll": {"changed": 0, "total": 0},
                    "output": "",
                    "metamod": {"changed": False, "message": "Metamod path already exists"},
                }

            runtime._run_app_update_validate = fake_run_app_update_validate
            runtime.monitor_check = lambda **kwargs: {"ok": True, "message": "Monitor success"}

            result = runtime.check_update()

            self.assertTrue(result["validated"])
            self.assertFalse(result["updated"])
            self.assertIsNone(result["previousBuildId"])
            self.assertEqual(result["currentBuildId"], "42")
            self.assertEqual(result["latestBuildId"], "42")
            self.assertEqual(result["message"], "Monitor success")
            self.assertEqual(result["monitor"]["message"], "Monitor success")
            self.assertIn(
                ("info", f"没有 manifest: {manifest_path}"),
                logs,
            )
            self.assertIn(("info", "没有 manifest，直接进入 validate 流程"), logs)
            self.assertIn(("info", "validate pipeline continued"), logs)


class DefaultStartServerSelectionTests(unittest.TestCase):
    def test_uses_start_after_monitor_flag_for_default_start_targets(self) -> None:
        runtime = DockerRuntime.__new__(DockerRuntime)
        runtime.config = SimpleNamespace(
            servers=[
                SimpleNamespace(key="ze_xl_1", groups=["all", "ze_xl"], start_after_monitor=True),
                SimpleNamespace(key="ze_pt_1", groups=["all", "ze_pt"], start_after_monitor=True),
                SimpleNamespace(key="ze_xl_test", groups=["test"], start_after_monitor=False),
                SimpleNamespace(key="ze_pt_test", groups=["test"], start_after_monitor=False),
            ]
        )

        self.assertEqual(
            runtime._default_start_server_keys("ze_pt_test"),
            ["ze_xl_1", "ze_pt_1"],
        )

    def test_profile_defaults_to_matching_group_with_start_flag(self) -> None:
        runtime = DockerRuntime.__new__(DockerRuntime)
        runtime.config = SimpleNamespace(
            servers=[
                SimpleNamespace(key="ze_xl_1", groups=["ze_xl"], start_after_monitor=True),
                SimpleNamespace(key="ze_xl_2", groups=["ze_xl"], start_after_monitor=True),
                SimpleNamespace(key="ze_pt_1", groups=["ze_pt"], start_after_monitor=True),
                SimpleNamespace(key="ze_xl_test", groups=["ze_xl", "test"], start_after_monitor=False),
            ]
        )
        profile = SimpleNamespace(key="ze_xl", monitor_server_key="ze_xl_1", start_server_keys=None)

        self.assertEqual(
            runtime._default_profile_start_server_keys(profile),
            ["ze_xl_1", "ze_xl_2"],
        )

    def test_empty_explicit_start_targets_do_not_fall_back_to_defaults(self) -> None:
        runtime = DockerRuntime.__new__(DockerRuntime)
        runtime._run_servers = lambda action, keys: {
            "action": action,
            "serverKeys": keys,
            "changed": 0,
            "total": len(keys),
            "results": [],
        }

        result = runtime.start_after_monitor(
            monitor_server_key="ze_xl_1",
            start_server_keys=[],
        )

        self.assertFalse(result["defaulted"])
        self.assertEqual(result["serverKeys"], [])
        self.assertEqual(result["total"], 0)

    def test_monitor_profiles_continue_after_one_profile_fails(self) -> None:
        runtime = DockerRuntime.__new__(DockerRuntime)
        runtime.config = SimpleNamespace(
            monitor_profiles=[
                SimpleNamespace(key="ze_xl", monitor_server_key="ze_xl_1", start_server_keys=None),
                SimpleNamespace(key="ze_pt", monitor_server_key="ze_pt_1", start_server_keys=None),
            ],
        )
        runtime._raise_if_cancel_requested = lambda: None
        runtime._emit_log = lambda _message, level="info": None
        runtime._default_profile_start_server_keys = lambda profile: [f"{profile.key}_1", f"{profile.key}_2"]

        calls: list[tuple[str | None, list[str] | None]] = []

        def fake_monitor_check_single(**kwargs):
            monitor_key = kwargs.get("monitor_server_key")
            start_keys = kwargs.get("start_server_keys")
            calls.append((monitor_key, start_keys))
            if monitor_key == "ze_pt_1":
                raise RuntimeError("ze_pt crashed")
            return {
                "ok": True,
                "monitorServerKey": monitor_key,
                "message": f"{monitor_key} passed",
            }

        runtime._monitor_check_single = fake_monitor_check_single

        result = runtime.monitor_check(start_after_success=True)

        self.assertFalse(result["ok"])
        self.assertEqual(result["success"], 1)
        self.assertEqual(result["failed"], 1)
        self.assertEqual(
            calls,
            [
                ("ze_xl_1", ["ze_xl_1", "ze_xl_2"]),
                ("ze_pt_1", ["ze_pt_1", "ze_pt_2"]),
            ],
        )
        self.assertEqual(result["profileResults"][0]["profileKey"], "ze_xl")
        self.assertEqual(result["profileResults"][1]["profileKey"], "ze_pt")
        self.assertFalse(result["profileResults"][1]["ok"])


class RestartServerTests(unittest.TestCase):
    def test_recreates_container_with_force_remove_before_start(self) -> None:
        calls: list[tuple[str, object]] = []

        class FakeContainer:
            def remove(self, force: bool = False) -> None:
                calls.append(("remove", force))

        runtime = DockerRuntime.__new__(DockerRuntime)
        runtime.get_server = lambda key: SimpleNamespace(container_name="kepcs-ze-xl-28010")
        runtime._get_container = lambda name: calls.append(("get_container", name)) or FakeContainer()
        runtime.start_server = lambda key: calls.append(("start_server", key)) or {
            "changed": False,
            "message": "start result",
            "server": {"key": key},
        }

        result = DockerRuntime.restart_server(runtime, "ze_xl_1")

        self.assertEqual(
            calls,
            [
                ("get_container", "kepcs-ze-xl-28010"),
                ("remove", True),
                ("start_server", "ze_xl_1"),
            ],
        )
        self.assertTrue(result["changed"])
        self.assertTrue(result["removed"])
        self.assertEqual(result["message"], "kepcs-ze-xl-28010 recreated")


class BatchStartIntervalTests(unittest.TestCase):
    def test_batch_start_waits_between_selected_servers(self) -> None:
        calls: list[tuple[str, object]] = []
        logs: list[str] = []

        runtime = DockerRuntime.__new__(DockerRuntime)
        runtime.config = SimpleNamespace(batch_start_interval_seconds=2)
        runtime._servers_for_keys = lambda keys: [SimpleNamespace(key=key) for key in keys]
        runtime._raise_if_cancel_requested = lambda: None
        runtime._emit_log = lambda message, level="info": logs.append(message)
        runtime.start_server = lambda key: calls.append(("start", key)) or {
            "changed": True,
            "message": f"{key} started",
        }

        original_sleep = runtime_module.time.sleep
        runtime_module.time.sleep = lambda seconds: calls.append(("sleep", seconds))
        try:
            result = DockerRuntime.start_servers(runtime, ["ze_xl_1", "ze_xl_2", "ze_xl_3"])
        finally:
            runtime_module.time.sleep = original_sleep

        self.assertEqual(
            calls,
            [
                ("start", "ze_xl_1"),
                ("sleep", 1),
                ("sleep", 1),
                ("start", "ze_xl_2"),
                ("sleep", 1),
                ("sleep", 1),
                ("start", "ze_xl_3"),
            ],
        )
        self.assertEqual(result["total"], 3)
        self.assertEqual(
            logs,
            [
                "Waiting 2s before starting next server ze_xl_2",
                "Waiting 2s before starting next server ze_xl_3",
            ],
        )

    def test_single_start_and_batch_stop_do_not_wait(self) -> None:
        calls: list[tuple[str, object]] = []

        runtime = DockerRuntime.__new__(DockerRuntime)
        runtime.config = SimpleNamespace(batch_start_interval_seconds=30)
        runtime._servers_for_keys = lambda keys: [SimpleNamespace(key=key) for key in keys]
        runtime._raise_if_cancel_requested = lambda: None
        runtime._emit_log = lambda _message, level="info": None
        runtime.start_server = lambda key: calls.append(("start", key)) or {
            "changed": True,
            "message": f"{key} started",
        }
        runtime.stop_server = lambda key: calls.append(("stop", key)) or {
            "changed": True,
            "message": f"{key} stopped",
        }

        original_sleep = runtime_module.time.sleep
        runtime_module.time.sleep = lambda seconds: calls.append(("sleep", seconds))
        try:
            DockerRuntime.start_servers(runtime, ["ze_xl_1"])
            DockerRuntime.stop_servers(runtime, ["ze_xl_1", "ze_xl_2"])
        finally:
            runtime_module.time.sleep = original_sleep

        self.assertEqual(
            calls,
            [
                ("start", "ze_xl_1"),
                ("stop", "ze_xl_1"),
                ("stop", "ze_xl_2"),
            ],
        )

    def test_batch_restart_removes_all_before_delayed_starts(self) -> None:
        calls: list[tuple[str, object]] = []
        servers = [
            SimpleNamespace(key="ze_xl_1", container_name="kepcs-ze-xl-28010"),
            SimpleNamespace(key="ze_xl_2", container_name="kepcs-ze-xl-28020"),
        ]

        class FakeContainer:
            def __init__(self, name: str) -> None:
                self.name = name

            def remove(self, force: bool = False) -> None:
                calls.append((f"remove:{self.name}", force))

        runtime = DockerRuntime.__new__(DockerRuntime)
        runtime.config = SimpleNamespace(batch_start_interval_seconds=2)
        runtime._servers_for_keys = lambda _keys: servers
        runtime._raise_if_cancel_requested = lambda: None
        runtime._emit_log = lambda _message, level="info": None
        runtime._get_container = lambda name: calls.append(("get_container", name)) or FakeContainer(name)
        runtime.start_server = lambda key: calls.append(("start", key)) or {
            "changed": True,
            "message": f"{key} started",
        }

        original_sleep = runtime_module.time.sleep
        runtime_module.time.sleep = lambda seconds: calls.append(("sleep", seconds))
        try:
            result = DockerRuntime.restart_servers(runtime, ["ze_xl_1", "ze_xl_2"])
        finally:
            runtime_module.time.sleep = original_sleep

        self.assertEqual(
            calls,
            [
                ("get_container", "kepcs-ze-xl-28010"),
                ("remove:kepcs-ze-xl-28010", True),
                ("get_container", "kepcs-ze-xl-28020"),
                ("remove:kepcs-ze-xl-28020", True),
                ("start", "ze_xl_1"),
                ("sleep", 1),
                ("sleep", 1),
                ("start", "ze_xl_2"),
            ],
        )
        self.assertEqual(result["changed"], 2)
        self.assertTrue(result["results"][0]["removed"])
        self.assertEqual(result["results"][0]["message"], "kepcs-ze-xl-28010 recreated")


class RconPasswordTests(unittest.TestCase):
    def test_uses_payload_password_for_rcon(self) -> None:
        calls: list[tuple[str, int, str, int, str]] = []

        class FakeClient:
            def __init__(self, host: str, port: int, *, passwd: str, timeout: int) -> None:
                self.host = host
                self.port = port
                self.passwd = passwd
                self.timeout = timeout

            def __enter__(self):
                return self

            def __exit__(self, _exc_type, _exc, _tb) -> None:
                return None

            def run(self, command: str) -> str:
                calls.append((self.host, self.port, self.passwd, self.timeout, command))
                return "ok"

        original_rcon = sys.modules.get("rcon")
        sys.modules["rcon"] = types.SimpleNamespace(Client=FakeClient)
        try:
            runtime = DockerRuntime.__new__(DockerRuntime)
            runtime.config = SimpleNamespace(rcon_host="127.0.0.1", rcon_timeout_seconds=5)
            runtime._raise_if_cancel_requested = lambda: None
            runtime._server_primary_port = lambda _server: 27015
            runtime._servers_for_keys = lambda keys: [
                SimpleNamespace(key=key)
                for key in keys
            ]

            result = runtime.send_rcon_command(
                "ALL",
                "status",
                server_keys=["ze_xl_1"],
                targets=[{"key": "ze_xl_1", "password": "db-secret"}],
            )
        finally:
            if original_rcon is None:
                sys.modules.pop("rcon", None)
            else:
                sys.modules["rcon"] = original_rcon

        self.assertEqual(calls, [("127.0.0.1", 27015, "db-secret", 5, "status")])
        self.assertEqual(result["success"], 1)

    def test_missing_payload_password_reports_empty(self) -> None:
        class FakeClient:
            def __init__(self, *_args, **_kwargs) -> None:
                raise AssertionError("RCON client should not be created without a password")

        original_rcon = sys.modules.get("rcon")
        sys.modules["rcon"] = types.SimpleNamespace(Client=FakeClient)
        try:
            runtime = DockerRuntime.__new__(DockerRuntime)
            runtime.config = SimpleNamespace(rcon_host="127.0.0.1", rcon_timeout_seconds=5)
            runtime._raise_if_cancel_requested = lambda: None
            runtime._server_primary_port = lambda _server: 27015
            runtime._servers_for_keys = lambda keys: [
                SimpleNamespace(key=key)
                for key in keys
            ]

            result = runtime.send_rcon_command(
                "ALL",
                "status",
                server_keys=["ze_xl_1"],
                targets=[{"key": "ze_xl_1"}],
            )
        finally:
            if original_rcon is None:
                sys.modules.pop("rcon", None)
            else:
                sys.modules["rcon"] = original_rcon

        self.assertEqual(result["success"], 0)
        self.assertEqual(result["failed"], 1)
        self.assertEqual(result["results"][0]["error"], "RCON password is empty")

    def test_targets_without_server_keys_are_not_used_as_target_source(self) -> None:
        original_rcon = sys.modules.get("rcon")
        sys.modules["rcon"] = types.SimpleNamespace(Client=object)
        try:
            runtime = DockerRuntime.__new__(DockerRuntime)
            runtime._servers_for_keys = lambda _keys: (_ for _ in ()).throw(
                AssertionError("targets should not be used as server key compatibility input")
            )

            result = runtime.send_rcon_command(
                "ALL",
                "status",
                targets=[{"key": "ze_xl_1", "password": "db-secret"}],
            )
        finally:
            if original_rcon is None:
                sys.modules.pop("rcon", None)
            else:
                sys.modules["rcon"] = original_rcon

        self.assertEqual(result["total"], 0)
        self.assertEqual(result["success"], 0)
        self.assertEqual(result["failed"], 0)


if __name__ == "__main__":
    unittest.main()
