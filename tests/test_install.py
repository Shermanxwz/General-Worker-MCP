"""Installer regression tests; every destination is inside a temporary directory."""

import os
from pathlib import Path
import shutil
import stat
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SHELL = shutil.which("sh")


class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="worker-install-test-")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.roots = {name: self.base / (name + " skills") for name in ("claude", "codex")}
        self.env = dict(os.environ, **{
            name.upper() + "_SKILLS_DIR": str(path) for name, path in self.roots.items()
        })

    def run_install(self, *args, expected=0, env=None):
        result = subprocess.run(
            [SHELL, str(ROOT / "install.sh"), *args],
            cwd=self.base, env=env or self.env, text=True, capture_output=True, timeout=10,
        )
        self.assertEqual(result.returncode, expected, result.stdout + result.stderr)
        return result

    def dest(self, name):
        return self.roots[name] / "worker" / "SKILL.md"

    def assert_installed(self, name):
        path = self.dest(name)
        self.assertEqual(path.read_bytes(), (ROOT / name / "skills/worker/SKILL.md").read_bytes())
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o644)

    def test_default_install_and_reinstall_are_idempotent(self):
        self.run_install()
        self.run_install("install", "all")
        for name in self.roots:
            self.assert_installed(name)
            self.assertFalse(self.dest(name).with_suffix(".md.bak").exists())

    def test_status_distinguishes_missing_current_and_modified(self):
        self.assertEqual(self.run_install("status").stdout.count("not installed"), 2)
        self.run_install()
        self.assertEqual(self.run_install("status").stdout.count("installed (up to date)"), 2)
        self.dest("claude").write_text("custom skill\n", encoding="utf-8")
        status = self.run_install("status").stdout
        self.assertIn("installed (differs from repo)", status)
        self.assertEqual(status.count("installed (up to date)"), 1)

    def test_backup_preserves_replaced_content_and_survives_reinstall(self):
        self.run_install()
        for revision in ("first custom version\n", "second custom version\n"):
            for name in self.roots:
                self.dest(name).write_text(revision, encoding="utf-8")
            self.run_install()
            self.run_install()
            for name in self.roots:
                self.assert_installed(name)
                self.assertEqual(self.dest(name).with_suffix(".md.bak").read_text(), revision)

    def test_targeted_operations_leave_other_target_unchanged(self):
        for name in self.roots:
            with self.subTest(target=name):
                self.run_install("install", name)
                self.assert_installed(name)
                other = "codex" if name == "claude" else "claude"
                self.assertFalse(self.dest(other).exists())
                self.run_install("uninstall", other)
                self.assert_installed(name)
                self.run_install("uninstall", name)
                self.assertFalse(self.dest(name).parent.exists())

    def test_uninstall_preserves_unrelated_files(self):
        self.run_install()
        for name, root in self.roots.items():
            self.dest(name).with_suffix(".md.bak").write_text("backup", encoding="utf-8")
            (root / "worker/notes.txt").write_text("keep notes", encoding="utf-8")
            (root / "other-skill").mkdir()
            (root / "other-skill/SKILL.md").write_text("keep skill", encoding="utf-8")
        self.run_install("uninstall")
        self.run_install("uninstall")
        for name, root in self.roots.items():
            self.assertFalse(self.dest(name).exists())
            self.assertFalse(self.dest(name).with_suffix(".md.bak").exists())
            self.assertEqual((root / "worker/notes.txt").read_text(), "keep notes")
            self.assertEqual((root / "other-skill/SKILL.md").read_text(), "keep skill")

    def test_missing_jq_blocks_only_claude(self):
        private_bin = self.base / "bin without jq"
        private_bin.mkdir()
        for name in ("dirname", "mkdir", "cp", "cmp", "chmod"):
            command = shutil.which(name)
            self.assertIsNotNone(command)
            (private_bin / name).symlink_to(command)
        env = dict(self.env, PATH=str(private_bin))
        result = self.run_install("install", "all", expected=1, env=env)
        self.assertIn("jq is not on PATH", result.stderr)
        self.assertFalse(self.dest("claude").exists())
        self.assert_installed("codex")
        self.run_install("install", "codex", env=env)

    def test_invalid_arguments_do_not_install_anything(self):
        for args in (("unknown",), ("install", "unknown"), ("install", "all", "extra")):
            with self.subTest(args=args):
                self.run_install(*args, expected=2)
                self.assertTrue(all(not path.exists() for path in self.roots.values()))
        self.assertIn("usage:", self.run_install("--help").stdout)


if __name__ == "__main__":
    unittest.main()
