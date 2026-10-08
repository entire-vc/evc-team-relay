"""Check the real CI path rules with GitLab's Ruby glob flags.

Run: python3 scripts/test_ci_rules.py (requires PyYAML and Ruby).
The complement globs deliberately use braces and character classes. Ruby's
FNM_EXTGLOB supports brace alternatives, but does not support shell !(...) globs.
GitLab matcher: https://docs.gitlab.com/ci/yaml/#ruleschanges
"""

import copy
import json
import random
import re
import shlex
import subprocess
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
COMPONENTS = {
    "control-plane": (
        "lint-python",
        "test-python",
        "alembic-migration-smoke",
        "check-migration-drift",
    ),
    "relay-server": ("relay-server-tests",),
    "web-publish": (
        "typecheck-svelte",
        "test-svelte",
        "bundle-budget-svelte",
        "browser-budget-svelte",
    ),
}
NON_RUNTIME = (
    "tests",
    "docs",
    "README.md",
    ".gitignore",
    "pytest.ini",
    "conftest.py",
    ".coveragerc",
)
RUBY_MATCH = """
require 'json'
input = JSON.parse(STDIN.read)
flags = File::FNM_PATHNAME | File::FNM_DOTMATCH | File::FNM_EXTGLOB
puts JSON.generate(input.map { |pattern, path| File.fnmatch(pattern, path, flags) })
"""


def complement(names):
    """Match any nonempty path segment except the exact listed names."""
    alternatives = []

    def walk(prefix, remaining):
        if "" in remaining:
            alternatives.append(prefix + "?*")
            return
        if prefix:
            alternatives.append(prefix)
        letters = sorted({name[0] for name in remaining})
        alternatives.append(prefix + "[!" + "".join(letters) + "]*")
        for letter in letters:
            walk(prefix + letter, [name[1:] for name in remaining if name[0] == letter])

    walk("", list(names))
    return "{" + ",".join(alternatives) + "}{,/**/*}"


def matches_many(pairs):
    result = subprocess.run(
        ["ruby", "-e", RUBY_MATCH],
        input=json.dumps(pairs),
        text=True,
        capture_output=True,
        check=True,
    )
    return json.loads(result.stdout)


def github_matches(pattern, path):
    # Workflow path globs: ** includes slashes; * does not. Only this documented
    # subset is used here: https://docs.github.com/actions/writing-workflows/workflow-syntax-for-github-actions#filter-pattern-cheat-sheet
    escaped = re.escape(pattern)
    escaped = escaped.replace(r"\*\*", ".*").replace(r"\*", "[^/]*")
    return re.fullmatch(escaped, path) is not None


def condition(expression, context):
    # Evaluate only the boolean grammar used by these rules. Reject unsupported
    # syntax so the contract cannot silently accept an unmodelled CI condition.
    token_pattern = re.compile(r'\s*(\$[A-Z_]+|"[^"\n]*"|==|!=|&&|\|\||[()])')
    tokens = []
    position = 0
    while position < len(expression):
        match = token_pattern.match(expression, position)
        if match is None:
            raise ValueError("unsupported rule condition syntax")
        tokens.append(match[1])
        position = match.end()
    index = 0

    def value():
        nonlocal index
        token = tokens[index]
        index += 1
        if token.startswith("$"):
            return context.get(token[1:], "")
        if token.startswith('"'):
            return json.loads(token)
        raise ValueError("expected variable or string")

    def atom():
        nonlocal index
        if tokens[index] == "(":
            index += 1
            result = disjunction()
            if index >= len(tokens) or tokens[index] != ")":
                raise ValueError("unclosed condition parentheses")
            index += 1
            return result
        left = value()
        if index < len(tokens) and tokens[index] in ("==", "!="):
            operator = tokens[index]
            index += 1
            right = value()
            return left == right if operator == "==" else left != right
        return bool(left)

    def conjunction():
        nonlocal index
        result = atom()
        while index < len(tokens) and tokens[index] == "&&":
            index += 1
            right = atom()
            result = result and right
        return result

    def disjunction():
        nonlocal index
        result = conjunction()
        while index < len(tokens) and tokens[index] == "||":
            index += 1
            right = conjunction()
            result = result or right
        return result

    result = disjunction()
    if index != len(tokens):
        raise ValueError("unsupported trailing rule condition")
    return result


def selected(config, job, paths, context):
    for rule in config[job].get("rules", [{"when": "on_success"}]):
        if "if" in rule and not condition(rule["if"], context):
            continue
        if "changes" in rule:
            patterns = rule["changes"]
            if not any(matches_many([(pattern, path) for pattern in patterns for path in paths])):
                continue
        return rule.get("when") != "never"
    return False


def context(source="merge_request_event", branch="feature", before="1234", open_mrs=""):
    return {
        "CI_PIPELINE_SOURCE": source,
        "CI_COMMIT_BRANCH": branch,
        "CI_DEFAULT_BRANCH": "main",
        "CI_COMMIT_BEFORE_SHA": before,
        "CI_OPEN_MERGE_REQUESTS": open_mrs,
    }


class CIRules(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # BaseLoader keeps GitHub's YAML 1.2 'on' key as a string.
        cls.config = yaml.safe_load((ROOT / ".gitlab-ci.yml").read_text())
        cls.deploy = yaml.load(
            (ROOT / ".github/workflows/deploy.yml").read_text(), Loader=yaml.BaseLoader
        )

    def assert_components(self, paths, expected, ctx=None, config=None):
        for component, jobs in COMPONENTS.items():
            for job in jobs:
                with self.subTest(paths=paths, job=job):
                    self.assertEqual(
                        selected(config or self.config, job, paths, ctx or context()),
                        component in expected,
                    )

    def test_condition_parser_rejects_unsupported_syntax(self):
        for expression in (
            "unknown()",
            "$CI_PIPELINE_SOURCE =~ /push/",
            '$CI_PIPELINE_SOURCE == "push" trailing',
        ):
            with self.assertRaises(ValueError):
                condition(expression, context())

    def test_component_and_mixed_paths(self):
        for component in COMPONENTS:
            paths = [f"apps/{component}/nested/.hidden/source.txt"]
            self.assert_components(paths, {component})
        self.assert_components(
            ["apps/control-plane/tests/test_a.py", "apps/web-publish/src/a.svelte"],
            {"control-plane", "web-publish"},
        )

    def test_shared_unknown_deleted_and_renamed_paths(self):
        # GitLab changes evaluates both old/deleted and new paths. In either
        # direction a cross-component rename must require both sets of checks.
        self.assert_components(
            ["apps/control-plane/deleted.py", "apps/relay-server/renamed.rs"],
            {"control-plane", "relay-server"},
        )
        for path in (
            ".gitlab-ci.yml",
            ".github/workflows/ci.yml",
            "docs/guide.md",
            "uv.lock",
            "infra/new.yml",
            "scripts/new.py",
            "future/.nested/file",
            "new-root.txt",
            "apps/new-component/main.py",
            "apps/control-plane-extra/main.py",
            "apps/relay-server-old/main.py",
            "apps/web-publish-v2/main.py",
            "apps/.hidden/main.py",
            "apps",
            "apps/control-plane",
            "apps/relay-server",
            "apps/web-publish",
            "apps/c",
            "apps/w",
            "apps/r",
        ):
            self.assert_components([path], set(COMPONENTS))

    def test_main_manual_schedule_api_new_branch_are_full(self):
        for ctx in (
            context("push", "main"),
            context("web"),
            context("schedule"),
            context("api"),
            context("push", before="0" * 40),
        ):
            self.assert_components(["apps/web-publish/src/a.svelte"], set(COMPONENTS), ctx)
        # MR before_sha is always zero; it must not force every component on.
        self.assert_components(
            ["apps/web-publish/src/a.svelte"],
            {"web-publish"},
            context(before="0" * 40),
        )

    def test_workflow_preserves_full_runs_with_open_mr(self):
        config = {"pipeline": {"rules": self.config["workflow"]["rules"]}}
        for source in ("web", "schedule", "api"):
            self.assertTrue(selected(config, "pipeline", [], context(source, open_mrs="1")))
        self.assertTrue(selected(config, "pipeline", [], context("push", "main", open_mrs="1")))
        self.assertFalse(selected(config, "pipeline", [], context("push", open_mrs="1")))

    def test_deploy_runtime_and_test_scope(self):
        skip = [
            "apps/control-plane/tests/test_a.py",
            "apps/control-plane/tests/.nested/x",
            "apps/control-plane/docs/new.md",
            "apps/control-plane/pytest.ini",
            "apps/control-plane/conftest.py",
            "apps/control-plane/.coveragerc",
            "apps/control-plane/README.md",
            ".gitlab-ci.yml",
            ".github/workflows/deploy.yml",
            "docs/DEPLOY.md",
            "apps/web-publish/src/a.svelte",
            "apps/web-publish/package-lock.json",
            "scripts/test_ci_rules.py",
        ]
        ship = [
            "apps/control-plane/app/db/migrations/new.py",
            "apps/control-plane/main.py",
            "apps/control-plane/uv.lock",
            "apps/control-plane/pyproject.toml",
            "apps/control-plane/Dockerfile",
            "apps/control-plane/.new-runtime",
            "apps/control-plane/new-runtime/.nested/a.py",
            "apps/control-plane/tests-runtime/x",
            "apps/control-plane/docs-runtime/x",
            "scripts/deploy.sh",
        ]
        for path, expected in [(p, False) for p in skip] + [(p, True) for p in ship]:
            with self.subTest(path=path):
                self.assertEqual(
                    selected(
                        self.config,
                        "trigger:prod-team-relay",
                        [path],
                        context("push", "main"),
                    ),
                    expected,
                )
                patterns = self.deploy["on"]["push"]["paths"]
                accepted = False
                # The actual GitHub workflow uses simple ** globs only.
                for pattern in patterns:
                    negated = pattern.startswith("!")
                    pattern = pattern.removeprefix("!")
                    if github_matches(pattern, path):
                        accepted = not negated
                self.assertEqual(accepted, expected)
        self.assertFalse(selected(self.config, "trigger:prod-team-relay", ship, context()))
        self.assertFalse(
            selected(
                self.config,
                "trigger:prod-team-relay",
                ship,
                context("schedule", "main"),
            )
        )
        self.assertTrue(
            selected(
                self.config,
                "trigger:prod-team-relay",
                skip + ship,
                context("push", "main"),
            )
        )

    def test_glob_complements_with_independent_semantic_oracle(self):
        randomizer = random.Random(451)
        for names in (("apps",), tuple(COMPONENTS), NON_RUNTIME):
            segments = set(names) | {".", "..", ".hidden", "unknown", "new-runtime"}
            for name in names:
                for length in range(1, len(name) + 1):
                    segments.add(name[:length])
                    segments.add(name[:length] + "-new")
                    segments.add(name[:length] + ".hidden")
                for index in range(len(name)):
                    segments.add(name[:index] + "z" + name[index + 1 :])
            segments.update(
                "".join(
                    randomizer.choices("abcdefghijklmnopqrstuvwxyz.-_", k=randomizer.randint(1, 30))
                )
                for _ in range(300)
            )
            paths = [
                part + suffix
                for part in segments
                for suffix in ("", "/file", "/.hidden/nested/file")
            ]
            actual = matches_many([(complement(names), path) for path in paths])
            expected = [path.split("/")[0] not in names for path in paths]
            self.assertEqual(actual, expected)

    def test_actual_complement_patterns_are_documented_generation(self):
        for component, jobs in COMPONENTS.items():
            expected = [
                f"apps/{component}/**/*",
                complement(("apps",)),
                "apps/" + complement(tuple(COMPONENTS)),
                "{apps," + ",".join("apps/" + name for name in COMPONENTS) + "}",
            ]
            for job in jobs:
                self.assertEqual(self.config[job]["rules"][-1]["changes"], expected)
        self.assertEqual(
            self.config["trigger:prod-team-relay"]["rules"][0]["changes"],
            ["apps/control-plane/" + complement(NON_RUNTIME), "scripts/deploy.sh"],
        )

    def test_bad_unknown_exclusion_is_detected(self):
        bad = copy.deepcopy(self.config)
        bad["test-python"]["rules"][-1]["changes"] = ["apps/control-plane/**/*"]
        self.assertFalse(selected(bad, "test-python", ["future/runtime.py"], context()))
        self.assertTrue(selected(self.config, "test-python", ["future/runtime.py"], context()))

    def test_bad_blanket_deploy_is_detected(self):
        bad = copy.deepcopy(self.config)
        bad["trigger:prod-team-relay"]["rules"][0]["changes"] = ["apps/control-plane/**/*"]
        self.assertTrue(
            selected(
                bad,
                "trigger:prod-team-relay",
                ["apps/control-plane/tests/test_a.py"],
                context("push", "main"),
            )
        )
        self.assertFalse(
            selected(
                self.config,
                "trigger:prod-team-relay",
                ["apps/control-plane/tests/test_a.py"],
                context("push", "main"),
            )
        )

    def test_canonical_security_is_unconditional_and_uses_existing_policy(self):
        semgrep = self.config["semgrep-security"]
        trivy = self.config["trivy-security"]
        for job in ("semgrep-security", "trivy-security"):
            self.assertFalse(self.config[job]["allow_failure"])
            for ctx in (context(), context("push", "main"), context("schedule")):
                self.assertTrue(selected(self.config, job, ["apps/web-publish/src/x"], ctx))
        self.assertEqual(semgrep["script"], ["semgrep ci --exclude='apps/relay-server/archive'"])
        self.assertEqual(
            semgrep["variables"]["SEMGREP_RULES"],
            "p/security-audit p/secrets p/owasp-top-ten p/python",
        )
        self.assertEqual(
            trivy["script"],
            [
                "trivy fs --scanners vuln --severity CRITICAL,HIGH --exit-code 1 "
                "--ignore-unfixed --ignorefile .trivyignore --format table ."
            ],
        )
        for scan in (semgrep, trivy):
            self.assertRegex(scan["image"]["name"], r"@sha256:[0-9a-f]{64}$")
            self.assertEqual(scan["image"]["entrypoint"], [""])

    def test_required_gates_and_deploy_needs_remain_blocking(self):
        for job in (
            "release-smoke-contract",
            "artifact-relocation-contract",
            "topology-gate",
            "hold-gate",
            "ci-rules-contract",
        ):
            self.assertFalse(self.config[job].get("allow_failure", False))
            self.assertTrue(selected(self.config, job, ["apps/web-publish/src/x"], context()))
        bridge = self.config["trigger:prod-team-relay"]
        self.assertEqual(
            set(bridge["needs"]),
            set(COMPONENTS["control-plane"]) | {"semgrep-security", "trivy-security"},
        )
        self.assertFalse(bridge["interruptible"])
        self.assertEqual(bridge["trigger"], {"project": "entire-vc/deploy", "branch": "main"})
        self.assertIn("workflow_dispatch", self.deploy["on"])

    def assert_daily_isolation_contract(self, config):
        serial = shlex.split(" ".join(config["test-python"]["script"]))
        self.assertNotIn("-n", serial)
        self.assertNotIn("--numprocesses", serial)
        self.assertFalse(any(token.startswith(("-n", "--numprocesses=")) for token in serial))
        self.assertNotIn("pytest-xdist", " ".join(serial))
        job = config["test-python-shuffled"]
        command = shlex.split(" ".join(job["script"]))
        self.assertEqual(command[command.index("--with") + 1], "pytest-xdist==3.8.0")
        self.assertEqual(command[command.index("-n") + 1], "2")
        self.assertEqual(command[command.index("--dist") + 1], "load")
        self.assertIn("--shuffle-seed=$CI_PIPELINE_ID", command)
        self.assertIn("--junitxml=shuffled-results.xml", command)
        self.assertEqual(job["artifacts"]["when"], "always")
        self.assertEqual(
            job["artifacts"]["reports"]["junit"],
            "apps/control-plane/shuffled-results.xml",
        )
        self.assertFalse(job.get("allow_failure", False))
        self.assertEqual(job.get("services", []), [])
        for source in (
            "schedule",
            "web",
            "api",
            "push",
            "merge_request_event",
            "trigger",
        ):
            for enabled in ("", "0", "1"):
                ctx = context(source, "main")
                ctx["TEST_ISOLATION_DAILY"] = enabled
                self.assertEqual(
                    selected(config, "test-python-shuffled", [], ctx),
                    enabled == "1" and source in ("schedule", "web", "api"),
                    (source, enabled),
                )
        # An isolation run never authorizes a production deployment, even when
        # the manual pipeline contains a runtime path.
        for source in ("schedule", "web", "api"):
            ctx = context(source, "main")
            ctx["TEST_ISOLATION_DAILY"] = "1"
            self.assertFalse(
                selected(
                    config,
                    "trigger:prod-team-relay",
                    ["apps/control-plane/app/main.py"],
                    ctx,
                )
            )

    def test_daily_isolation_retains_serial_default_and_bounded_reproduction(self):
        self.assert_daily_isolation_contract(self.config)

    def test_daily_isolation_contract_rejects_known_bad_configurations(self):
        mutants = {
            "serial_parallelism": lambda config: config["test-python"]["script"].append("-n 2"),
            "unbounded_workers": lambda config: config["test-python-shuffled"][
                "script"
            ].__setitem__(
                0,
                config["test-python-shuffled"]["script"][0].replace("-n 2", "-n auto"),
            ),
            "unpinned_xdist": lambda config: config["test-python-shuffled"]["script"].__setitem__(
                0, config["test-python-shuffled"]["script"][0].replace("==3.8.0", "")
            ),
            "missing_seed": lambda config: config["test-python-shuffled"]["script"].__setitem__(
                0,
                config["test-python-shuffled"]["script"][0].replace(
                    '--shuffle-seed="$CI_PIPELINE_ID"', ""
                ),
            ),
            "missing_artifact": lambda config: config["test-python-shuffled"]["artifacts"].pop(
                "reports"
            ),
            "nonblocking": lambda config: config["test-python-shuffled"].update(allow_failure=True),
            "unconditional": lambda config: config["test-python-shuffled"].update(
                rules=[{"when": "on_success"}]
            ),
        }
        for name, mutate in mutants.items():
            with self.subTest(mutant=name):
                bad = copy.deepcopy(self.config)
                mutate(bad)
                with self.assertRaises((AssertionError, KeyError, ValueError)):
                    self.assert_daily_isolation_contract(bad)


if __name__ == "__main__":
    unittest.main(verbosity=2)
