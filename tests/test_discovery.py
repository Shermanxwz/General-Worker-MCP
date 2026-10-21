"""Test discovery result handling without starting an app-server or a model."""

import contextlib
import importlib.util
import io
import unittest
from pathlib import Path
from unittest import mock

SPEC = importlib.util.spec_from_file_location(
    "codex_probe", Path(__file__).with_name("check-codex-skills.py")
)
PROBE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PROBE)


class DiscoveryTests(unittest.TestCase):
    def check_result(self, skills, errors=(), args=()):
        with mock.patch.object(PROBE, "probe", return_value=(skills, errors)), \
                mock.patch("sys.argv", ["check-codex-skills.py", *args]), \
                contextlib.redirect_stdout(io.StringIO()), \
                contextlib.redirect_stderr(io.StringIO()):
            return PROBE.main()

    def skill(self, **overrides):
        skill = dict(name="worker", scope="repo", path="/project/worker/SKILL.md", enabled=True)
        skill.update(overrides)
        return skill

    def test_enabled_skill_at_expected_path_passes(self):
        self.assertEqual(self.check_result(
            [self.skill()], args=("--expected-path", "/project/worker/SKILL.md")), 0)

    def test_other_installed_copy_cannot_mask_missing_checkout(self):
        self.assertEqual(self.check_result(
            [self.skill()], args=("--expected-path", "/different/worker/SKILL.md")), 1)

    def test_disabled_or_system_skill_does_not_pass(self):
        for overrides in ({"enabled": False}, {"scope": "system"}):
            with self.subTest(overrides=overrides):
                self.assertEqual(self.check_result([self.skill(**overrides)]), 1)

    def test_loading_errors_fail_even_if_worker_was_found(self):
        self.assertEqual(self.check_result([self.skill()], errors=[{"message": "bad YAML"}]), 1)

    def test_empty_result_fails(self):
        self.assertEqual(self.check_result([]), 1)


if __name__ == "__main__":
    unittest.main()
