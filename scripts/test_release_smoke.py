"""Fail-closed release smoke contracts. Run: python3 scripts/test_release_smoke.py."""

import copy
import tomllib
import unittest
from pathlib import Path

import release_notes
from release_smoke import RELAY_AUDIENCE, RELAY_URL, check_config, check_responses

REPO = "entire-vc/evc-team-relay"
WORKFLOW = Path(__file__).resolve().parents[1] / ".github/workflows/release.yml"


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


class ReleaseNotes(unittest.TestCase):
    # What docker/metadata-action `type=semver,pattern={{version}}` publishes for
    # tag v1.13.10: no `v` prefix. Stands in for ghcr in the check.
    PUBLISHED = frozenset(
        f"ghcr.io/{REPO}/{image}:1.13.10" for image in release_notes.IMAGES
    )

    def inspect(self, ref):
        return ref in self.PUBLISHED

    def test_rendered_pull_lines_drop_the_v_prefix_and_resolve(self):
        notes = release_notes.render(REPO, "v1.13.10")
        self.assertEqual(set(release_notes.pull_refs(notes)), self.PUBLISHED)
        release_notes.check(notes, inspect=self.inspect)

    def test_v_prefixed_pull_lines_fail(self):
        # The pre-fix template: `docker pull ...:${{ github.ref_name }}`.
        old = "\n".join(
            f"docker pull ghcr.io/{REPO}/{image}:v1.13.10"
            for image in release_notes.IMAGES
        )
        with self.assertRaisesRegex(ValueError, "do not exist.*:v1.13.10"):
            release_notes.check(old, inspect=self.inspect)

    def test_notes_without_pull_lines_fail(self):
        with self.assertRaisesRegex(ValueError, "no `docker pull`"):
            release_notes.check("## Docker Images\n", inspect=self.inspect)

    def test_non_semver_tag_is_rejected(self):
        for tag in ("vnext", "v1.13", "main"):
            with self.subTest(tag=tag), self.assertRaises(ValueError):
                release_notes.render(REPO, tag)

    def test_workflow_publishes_the_checked_notes(self):
        workflow = WORKFLOW.read_text()
        self.assertNotIn(
            ":${{ github.ref_name }}",
            workflow,
            "pull tag must not be the v-prefixed ref name",
        )
        self.assertNotRegex(workflow, r"(?m)^\s+body: \|")
        self.assertIn("body_path: release-notes.md", workflow)
        smoke = workflow[
            workflow.index("  smoke-test:") : workflow.index("  promote-latest:")
        ]
        self.assertIn("release_notes.py check", smoke)


if __name__ == "__main__":
    unittest.main(verbosity=2)
