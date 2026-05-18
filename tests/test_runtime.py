import unittest
import sys
import tempfile
import time
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


class QueryServerInfoTests(unittest.TestCase):
    def test_server_query_port_prefers_udp(self) -> None:
        runtime = DockerRuntime.__new__(DockerRuntime)
        server = SimpleNamespace(
            key="ze_xl_1",
            ports=[
                SimpleNamespace(host_port=28010, protocol="tcp"),
                SimpleNamespace(host_port=28010, protocol="udp"),
            ],
        )

        self.assertEqual(runtime._server_query_port(server), 28010)
        self.assertEqual(runtime._server_primary_port(server), 28010)

    def test_server_query_port_falls_back_when_udp_is_missing(self) -> None:
        runtime = DockerRuntime.__new__(DockerRuntime)
        server = SimpleNamespace(
            key="ze_xl_1",
            ports=[
                SimpleNamespace(host_port=28010, protocol="tcp"),
            ],
        )

        self.assertEqual(runtime._server_query_port(server), 28010)
        self.assertEqual(runtime._server_primary_port(server), 28010)

    def test_parses_a2s_info_response(self) -> None:
        payload = (
            b"\x11"
            + b"KepCs ZE\x00"
            + b"ze_example\x00"
            + b"csgo\x00"
            + b"Counter-Strike 2\x00"
            + (730).to_bytes(2, "little")
            + bytes([12, 64, 0])
            + b"d"
            + b"l"
            + bytes([0, 1])
        )

        result = DockerRuntime._parse_a2s_info_response(payload)

        self.assertEqual(result["serverName"], "KepCs ZE")
        self.assertEqual(result["map"], "ze_example")
        self.assertEqual(result["currentPlayers"], 12)
        self.assertEqual(result["maxPlayers"], 64)

    def test_inspect_server_includes_query_info(self) -> None:
        class FakeContainer:
            status = "running"
            id = "container-1"
            image = SimpleNamespace(tags=["steamrt3:latest"])
            attrs = {"State": {"Status": "running", "RestartCount": 1}}

            def reload(self) -> None:
                return None

        runtime = DockerRuntime.__new__(DockerRuntime)
        runtime.config = SimpleNamespace(
            server_query_host="127.0.0.1",
            server_query_enabled=True,
            server_query_timeout_seconds=2,
        )
        runtime.get_server = lambda _key: SimpleNamespace(
            key="ze_xl_1",
            catalog_server_id="catalog-1",
            container_name="kepcs-ze-xl-28010",
            groups=["ze_xl"],
            image="steamrt3:latest",
            labels={"kepcs.mode": "ze_xl", "kepcs.server_key": "ze_xl_1"},
        )
        runtime._get_container = lambda _name: FakeContainer()
        runtime._server_query_port = lambda _server: 28010
        runtime._query_server_info = lambda _server: {
            "serverName": "KepCs ZE",
            "map": "ze_example",
            "currentPlayers": 12,
            "maxPlayers": 64,
            "visibility": 0,
        }

        result = DockerRuntime.inspect_server(runtime, "ze_xl_1")

        self.assertEqual(result["primaryPort"], 28010)
        self.assertEqual(result["catalogServerId"], "catalog-1")
        self.assertIsNone(result["host"])
        self.assertEqual(result["queryHost"], "127.0.0.1")
        self.assertEqual(result["mode"], "ze_xl")
        self.assertEqual(result["containerStatus"], "running")
        self.assertEqual(result["agentA2sStatus"], "ok")
        self.assertIsNone(result["agentA2sError"])
        self.assertEqual(result["serverName"], "KepCs ZE")
        self.assertEqual(result["map"], "ze_example")
        self.assertEqual(result["currentPlayers"], 12)
        self.assertEqual(result["maxPlayers"], 64)

    def test_inspect_server_marks_local_timeout_without_hiding_running_container(self) -> None:
        class FakeContainer:
            status = "running"
            id = "container-1"
            image = SimpleNamespace(tags=["steamrt3:latest"])
            attrs = {"State": {"Status": "running", "RestartCount": 1}}

            def reload(self) -> None:
                return None

        runtime = DockerRuntime.__new__(DockerRuntime)
        runtime.config = SimpleNamespace(
            server_query_host="127.0.0.1",
            server_query_enabled=True,
            server_query_timeout_seconds=2,
        )
        runtime.get_server = lambda _key: SimpleNamespace(
            key="ze_xl_1",
            catalog_server_id="catalog-1",
            container_name="kepcs-ze-xl-28010",
            groups=["ze_xl"],
            image="steamrt3:latest",
            labels={"kepcs.mode": "ze_xl", "kepcs.server_key": "ze_xl_1"},
        )
        runtime._get_container = lambda _name: FakeContainer()
        runtime._server_query_port = lambda _server: 28010

        def raise_timeout(_server) -> None:
            raise TimeoutError("timed out")

        runtime._query_server_info = raise_timeout

        result = DockerRuntime.inspect_server(runtime, "ze_xl_1")

        self.assertEqual(result["containerStatus"], "running")
        self.assertEqual(result["agentA2sStatus"], "timeout")
        self.assertEqual(result["agentA2sError"], "timed out")
        self.assertEqual(result["queryError"], "timed out")

    def test_inspect_server_skips_a2s_when_container_is_not_running(self) -> None:
        class FakeContainer:
            status = "exited"
            id = "container-1"
            image = SimpleNamespace(tags=["steamrt3:latest"])
            attrs = {"State": {"Status": "exited", "RestartCount": 1}}

            def reload(self) -> None:
                return None

        runtime = DockerRuntime.__new__(DockerRuntime)
        runtime.get_server = lambda _key: SimpleNamespace(
            key="ze_xl_1",
            catalog_server_id="catalog-1",
            container_name="kepcs-ze-xl-28010",
            groups=["ze_xl"],
            image="steamrt3:latest",
            labels={"kepcs.mode": "ze_xl", "kepcs.server_key": "ze_xl_1"},
            ports=[SimpleNamespace(host_port=28010, protocol="udp")],
        )
        runtime.config = SimpleNamespace(server_query_host="127.0.0.1")
        runtime._get_container = lambda _name: FakeContainer()
        runtime._query_server_info = lambda _server: (_ for _ in ()).throw(AssertionError("A2S should not run"))

        result = DockerRuntime.inspect_server(runtime, "ze_xl_1")

        self.assertEqual(result["containerStatus"], "exited")
        self.assertEqual(result["agentA2sStatus"], "unknown")
        self.assertNotIn("agentA2sError", result)

    def test_list_servers_pending_snapshot_includes_explicit_status_fields(self) -> None:
        runtime = DockerRuntime.__new__(DockerRuntime)
        runtime.config = SimpleNamespace(
            server_query_enabled=True,
            server_query_cache_ttl_seconds=15,
            server_query_host="127.0.0.1",
            servers=[
                SimpleNamespace(
                    key="ze_xl_1",
                    catalog_server_id="catalog-1",
                    container_name="kepcs-ze-xl-28010",
                    groups=["ze_xl"],
                    image="steamrt3:latest",
                    labels={"kepcs.mode": "ze_xl", "kepcs.server_key": "ze_xl_1"},
                    ports=[SimpleNamespace(host_port=28010, protocol="udp")],
                ),
            ],
        )
        runtime._server_snapshot_lock = runtime_module.threading.Lock()
        runtime._server_snapshots = {}
        runtime._server_refresh_in_flight = False
        runtime._server_refresh_requested_at = 0.0

        refresh_calls: list[bool] = []
        runtime.refresh_server_snapshots_async = lambda force=False: refresh_calls.append(force) or True
        runtime.get_server = lambda key: runtime.config.servers[0]
        runtime._server_query_port = lambda _server: 28010

        rows = DockerRuntime.list_servers(runtime)

        self.assertEqual(refresh_calls, [False])
        self.assertEqual(rows[0]["containerStatus"], "pending")
        self.assertEqual(rows[0]["agentA2sStatus"], "pending")

    def test_list_servers_returns_pending_snapshot_while_async_refresh_runs(self) -> None:
        runtime = DockerRuntime.__new__(DockerRuntime)
        runtime.config = SimpleNamespace(
            server_query_enabled=True,
            server_query_cache_ttl_seconds=15,
            server_query_host="127.0.0.1",
            servers=[
                SimpleNamespace(
                    key="ze_xl_1",
                    catalog_server_id="catalog-1",
                    container_name="kepcs-ze-xl-28010",
                    groups=["ze_xl"],
                    image="steamrt3:latest",
                    labels={"kepcs.mode": "ze_xl", "kepcs.server_key": "ze_xl_1"},
                    ports=[SimpleNamespace(host_port=28010, protocol="udp")],
                ),
            ],
        )
        runtime._server_snapshot_lock = runtime_module.threading.Lock()
        runtime._server_snapshots = {}
        runtime._server_refresh_in_flight = False
        runtime._server_refresh_requested_at = 0.0

        refresh_calls: list[bool] = []
        runtime.refresh_server_snapshots_async = lambda force=False: refresh_calls.append(force) or True
        runtime.get_server = lambda key: runtime.config.servers[0]
        runtime._server_query_port = lambda _server: 28010

        rows = DockerRuntime.list_servers(runtime)

        self.assertEqual(refresh_calls, [False])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["status"], "pending")
        self.assertEqual(rows[0]["containerStatus"], "pending")
        self.assertEqual(rows[0]["agentA2sStatus"], "pending")
        self.assertEqual(rows[0]["catalogServerId"], "catalog-1")
        self.assertTrue(rows[0]["queryPending"])
        self.assertTrue(rows[0]["queryStale"])

    def test_list_servers_uses_cached_snapshot_without_blocking(self) -> None:
        runtime = DockerRuntime.__new__(DockerRuntime)
        runtime.config = SimpleNamespace(
            server_query_enabled=True,
            server_query_cache_ttl_seconds=15,
            servers=[
                SimpleNamespace(
                    key="ze_xl_1",
                    catalog_server_id="catalog-1",
                    container_name="kepcs-ze-xl-28010",
                    groups=["ze_xl"],
                    image="steamrt3:latest",
                    labels={"kepcs.mode": "ze_xl", "kepcs.server_key": "ze_xl_1"},
                    ports=[SimpleNamespace(host_port=28010, protocol="udp")],
                ),
            ],
        )
        runtime._server_snapshot_lock = runtime_module.threading.Lock()
        runtime._server_snapshots = {
            "ze_xl_1": {
                "key": "ze_xl_1",
                "catalogServerId": "catalog-1",
                "status": "running",
                "serverName": "KepCs ZE",
                "_refreshedAtMonotonic": time.monotonic(),
            },
        }
        runtime._server_refresh_in_flight = False
        runtime._server_refresh_requested_at = 0.0

        refresh_calls: list[bool] = []
        runtime.refresh_server_snapshots_async = lambda force=False: refresh_calls.append(force) or False

        rows = DockerRuntime.list_servers(runtime)

        self.assertEqual(refresh_calls, [False])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["status"], "running")
        self.assertEqual(rows[0]["catalogServerId"], "catalog-1")
        self.assertEqual(rows[0]["serverName"], "KepCs ZE")
        self.assertNotIn("_refreshedAtMonotonic", rows[0])

    def test_refresh_server_snapshots_async_refreshes_when_any_snapshot_is_stale(self) -> None:
        runtime = DockerRuntime.__new__(DockerRuntime)
        runtime.config = SimpleNamespace(
            server_query_enabled=True,
            server_query_cache_ttl_seconds=15,
            servers=[],
        )
        runtime._server_snapshot_lock = runtime_module.threading.Lock()
        runtime._server_snapshots = {
            "fresh": {
                "key": "fresh",
                "_refreshedAtMonotonic": time.monotonic(),
            },
            "stale": {
                "key": "stale",
                "_refreshedAtMonotonic": time.monotonic() - 30,
            },
        }
        runtime._server_refresh_in_flight = False
        runtime._server_refresh_requested_at = 0.0

        starts: list[str] = []

        class FakeThread:
            def __init__(self, *, target, name, daemon) -> None:
                self._target = target
                self.name = name
                self.daemon = daemon

            def start(self) -> None:
                starts.append(self.name)

        original_thread = runtime_module.threading.Thread
        runtime_module.threading.Thread = FakeThread
        try:
            started = DockerRuntime.refresh_server_snapshots_async(runtime)
        finally:
            runtime_module.threading.Thread = original_thread

        self.assertTrue(started)
        self.assertEqual(starts, ["kepagent-server-query-refresh"])
        self.assertTrue(runtime._server_refresh_in_flight)

    def test_refresh_server_snapshot_now_updates_cache_and_returns_snapshot(self) -> None:
        runtime = DockerRuntime.__new__(DockerRuntime)
        runtime._server_snapshot_lock = runtime_module.threading.Lock()
        runtime._server_snapshots = {}
        runtime.inspect_server = lambda key: {
            "key": key,
            "status": "running",
            "containerStatus": "running",
            "currentPlayers": 12,
            "maxPlayers": 64,
        }

        snapshot = DockerRuntime._refresh_server_snapshot_now(runtime, "ze_xl_1")

        self.assertEqual(snapshot["key"], "ze_xl_1")
        self.assertEqual(runtime._server_snapshots["ze_xl_1"]["currentPlayers"], 12)
        self.assertIn("_refreshedAtMonotonic", runtime._server_snapshots["ze_xl_1"])

    def test_build_summary_uses_provided_servers_without_refreshing_list(self) -> None:
        runtime = DockerRuntime.__new__(DockerRuntime)
        runtime.config = SimpleNamespace(servers=[object(), object(), object()])
        runtime.list_servers = lambda: (_ for _ in ()).throw(AssertionError("list_servers should not be called"))

        summary = DockerRuntime.build_summary(runtime, [
            {"state": "running"},
            {"state": "missing"},
            {"state": "exited"},
        ])

        self.assertEqual(summary, {
            "configuredServers": 3,
            "runningServers": 1,
            "missingServers": 1,
        })


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

    def test_batch_start_reports_runtime_state_during_each_step(self) -> None:
        calls: list[tuple[str, object]] = []

        runtime = DockerRuntime.__new__(DockerRuntime)
        runtime.config = SimpleNamespace(batch_start_interval_seconds=2)
        runtime._servers_for_keys = lambda keys: [SimpleNamespace(key=key) for key in keys]
        runtime._raise_if_cancel_requested = lambda: None
        runtime._emit_log = lambda _message, level="info": None
        runtime._state_reporter = lambda: calls.append(("report", None))
        runtime.start_server = lambda key: calls.append(("start", key)) or {
            "changed": True,
            "message": f"{key} started",
        }

        original_sleep = runtime_module.time.sleep
        runtime_module.time.sleep = lambda seconds: calls.append(("sleep", seconds))
        try:
            DockerRuntime.start_servers(runtime, ["ze_xl_1", "ze_xl_2"])
        finally:
            runtime_module.time.sleep = original_sleep

        self.assertEqual(
            calls,
            [
                ("start", "ze_xl_1"),
                ("report", None),
                ("sleep", 1),
                ("report", None),
                ("sleep", 1),
                ("report", None),
                ("start", "ze_xl_2"),
                ("report", None),
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
