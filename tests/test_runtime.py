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


if __name__ == "__main__":
    unittest.main()
