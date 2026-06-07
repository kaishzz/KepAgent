import unittest

from kepagent.config import AgentConfig


class AgentConfigTests(unittest.TestCase):
    def test_numeric_group_keys_are_normalized_to_strings(self) -> None:
        config = AgentConfig.model_validate(
            {
                "api_base_url": "https://example.test",
                "api_key": "test-key",
                "group_labels": {
                    2102: "训练服",
                    2193: "跑图测试服",
                },
                "group_order": [2102, 2193],
                "servers": [
                    {
                        "key": "ze_pt_test",
                        "container_name": "kepcs-ze-pt-test",
                        "image": "steamrt3:latest",
                        "groups": [2193],
                    },
                ],
            },
        )

        self.assertEqual(config.group_labels, {"2102": "训练服", "2193": "跑图测试服"})
        self.assertEqual(config.group_order, ["2102", "2193"])
        self.assertEqual(config.servers[0].groups, ["2193"])


if __name__ == "__main__":
    unittest.main()
