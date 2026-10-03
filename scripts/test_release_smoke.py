"""Fail-closed release smoke contracts. Run: python3 scripts/test_release_smoke.py."""

import copy
import tomllib
import unittest
from pathlib import Path

from release_smoke import RELAY_AUDIENCE, RELAY_URL, check_config, check_responses


class ReleaseSmoke(unittest.TestCase):
    def setUp(self):
        self.payload = {
            "health": {"ok": True},
            "server_info": {
                "id": "relay_cp_dev",
                "name": "Test Relay",
                "version": "0.1.0",
                "relay_url": RELAY_URL,
                "features": {"multi_user": True},
            },
            "relay_audience": RELAY_AUDIENCE,
        }

    def test_correct_stack_contract_passes(self):
        check_config({"server": {"url": RELAY_AUDIENCE}})
        check_responses(self.payload)

    def test_unmodified_example_and_wrong_url_fail(self):
        example = Path(__file__).resolve().parents[1] / "infra/relay/relay.toml.example"
        with example.open("rb") as f:
            config = tomllib.load(f)
        with self.assertRaisesRegex(ValueError, r"relay.toml.*url"):
            check_config(config)
        with self.assertRaises(ValueError):
            check_config({"server": {"url": "https://wrong.example"}})
        with self.assertRaises(ValueError):
            check_config({})

    def test_health_200_with_wrong_body_fails(self):
        for body in ({"ok": False}, {"ok": "true"}, {"ok": 1}, {}, "ok"):
            with self.subTest(body=body):
                self.payload["health"] = body
                with self.assertRaisesRegex(ValueError, "/health"):
                    check_responses(self.payload)

    def test_server_info_200_with_wrong_body_fails(self):
        original = copy.deepcopy(self.payload["server_info"])
        for field, bad in (
            ("id", ""),
            ("name", None),
            ("version", ""),
            ("relay_url", "wss://example.com"),
            ("features", {"multi_user": "true"}),
        ):
            with self.subTest(field=field):
                self.payload["server_info"] = {**original, field: bad}
                with self.assertRaisesRegex(ValueError, "/server/info"):
                    check_responses(self.payload)
        self.payload["server_info"] = {}
        with self.assertRaises(ValueError):
            check_responses(self.payload)

    def test_control_plane_audience_mismatch_fails(self):
        self.payload["relay_audience"] = "https://wrong.example"
        with self.assertRaisesRegex(ValueError, "audience"):
            check_responses(self.payload)


if __name__ == "__main__":
    unittest.main(verbosity=2)
