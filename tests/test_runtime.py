import unittest
import sys
import types

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


if __name__ == "__main__":
    unittest.main()
