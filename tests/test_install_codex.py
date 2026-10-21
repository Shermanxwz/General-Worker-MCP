"""Tests for scripts/codex.sh, the Codex target of install.sh. Run from the repo root:

    python3 -m unittest discover -s tests -p 'test_install_codex.py' -v
"""
import os
import stat
import sys
import unittest

from codex_fakes import FAKE_ID, FAKE_SUDO, RULES_SRC, RUNTIME_SRC, SKILL_SRC, Sandbox, make_fake

BEGIN = "# >>> general-worker-mcp (managed by install.sh)"
END = "# <<< general-worker-mcp"
OLD_TOML = b'[model_providers.x]\nname = "x"\n'


def mode(path):
    return stat.S_IMODE(path.stat().st_mode)


class CodexTarget(unittest.TestCase):
    def setUp(self):
        self.sb = Sandbox(self)


class InstallTests(CodexTarget):
    def test_copies_runtime_rules_and_skill(self):
        sb = self.sb
        result = sb.run("install")
        self.assertEqual(result.returncode, 0, result.stderr)
        for src in sorted(RUNTIME_SRC.glob("*.mjs")):
            dst = sb.worker_home / "runtime" / src.name
            self.assertEqual(dst.read_bytes(), src.read_bytes(), src.name)
            self.assertEqual(mode(dst), 0o644, src.name)
        for src in sorted(RULES_SRC.glob("*.md")):
            dst = sb.worker_home / "rules" / src.name
            self.assertEqual(dst.read_bytes(), src.read_bytes(), src.name)
            self.assertEqual(mode(dst), 0o644, src.name)
        self.assertEqual(sb.skill_dst.read_bytes(), SKILL_SRC.read_bytes())
        self.assertEqual(mode(sb.skill_dst), 0o644)
        for directory in (sb.worker_home / "runtime", sb.worker_home / "rules", sb.skill_dst.parent):
            self.assertEqual(mode(directory), 0o755, directory)

    def test_requirements_block_and_parent_directory(self):
        sb = self.sb
        result = sb.run("install")
        self.assertEqual(result.returncode, 0, result.stderr)
        lines = sb.req.read_text().splitlines()
        self.assertEqual(lines.count(BEGIN), 1)
        self.assertEqual(lines.count(END), 1)
        self.assertEqual(mode(sb.req), 0o644)
        self.assertTrue(sb.req.parent.is_dir())
        self.assertEqual(mode(sb.req.parent), 0o755)

    @unittest.skipIf(sys.version_info < (3, 11), "tomllib needs Python 3.11+")
    def test_requirements_parse_as_toml(self):
        import tomllib

        sb = self.sb
        sb.run("install")
        hooks = tomllib.loads(sb.req.read_text())["hooks"]
        self.assertEqual(hooks["managed_dir"], str(sb.worker_home))
        command = f"{sb.node} {sb.worker_home}/runtime/worker-hook.mjs --runtime codex"
        for event in ("UserPromptSubmit", "SessionStart", "PostToolUse"):
            handler = hooks[event][0]["hooks"][0]
            self.assertEqual(handler["type"], "command", event)
            self.assertEqual(handler["command"], command, event)
            self.assertEqual(handler["timeout"], 10, event)
        self.assertEqual(hooks["PostToolUse"][0]["matcher"], "^mcp__.*__(enter|exit)_worker_mode$")

    def test_reinstall_replaces_block_and_reregisters_mcp(self):
        sb = self.sb
        sb.run("install")
        first = sb.req.read_text()
        sb.req.write_text(first.replace("timeout = 10", "timeout = 99", 1))
        sb.log.unlink()
        result = sb.run("install")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(sb.req.read_text(), first)
        self.assertEqual(sb.req.read_text().count(BEGIN), 1)
        calls = sb.calls()
        self.assertTrue(any(c.startswith("codex mcp remove worker") for c in calls), calls)
        self.assertTrue(any(c.startswith("codex mcp add worker") for c in calls), calls)

    def test_append_keeps_existing_content_byte_for_byte(self):
        sb = self.sb
        sb.req.parent.mkdir(parents=True)
        sb.req.write_bytes(OLD_TOML)
        result = sb.run("install")
        self.assertEqual(result.returncode, 0, result.stderr)
        data = sb.req.read_bytes()
        self.assertTrue(data.startswith(OLD_TOML))
        self.assertIn(BEGIN.encode(), data)

    def test_existing_hooks_are_refused_and_file_untouched(self):
        for old in ('[hooks]\nmanaged_dir = "/elsewhere"\n', "[[hooks.Stop]]\ncommand = 'x'\n"):
            with self.subTest(old=old):
                sb = Sandbox(self)
                sb.req.parent.mkdir(parents=True)
                sb.req.write_text(old)
                result = sb.run("install")
                self.assertEqual(result.returncode, 1)
                self.assertEqual(sb.req.read_text(), old)
                self.assertIn("merge", result.stderr)

    def test_status_exit_codes(self):
        sb = self.sb
        sb.run("install")
        self.assertEqual(sb.run("status").returncode, 0)
        sb.skill_dst.write_text("old skill\n")
        result = sb.run("status")
        self.assertEqual(result.returncode, 1)
        self.assertIn("skill: differs", result.stdout)
        sb.run("install")
        self.assertEqual(sb.run("status").returncode, 0)
        sb.req.unlink()
        self.assertEqual(sb.run("status").returncode, 1)
        sb.run("install")
        sb.state.unlink()
        self.assertEqual(sb.run("status").returncode, 1)

    def test_mcp_step_fails_without_codex_home(self):
        result = self.sb.run("install", CODEX_HOME=None)
        self.assertEqual(result.returncode, 1)
        self.assertIn("CODEX_HOME is not set", result.stderr)
        self.assertTrue((self.sb.worker_home / "runtime" / "worker-mcp.mjs").exists())

    def test_worker_home_with_space_or_quote_is_refused(self):
        for bad in ("with space", 'quo"te'):
            with self.subTest(bad=bad):
                sb = Sandbox(self)
                result = sb.run("install", WORKER_HOME=str(sb.root / bad))
                self.assertEqual(result.returncode, 1)
                self.assertIn("spaces or quotes", result.stderr)
                self.assertFalse((sb.root / bad).exists())
                self.assertFalse(sb.req.exists())

    def test_root_runs_mcp_as_codex_home_owner(self):
        sb = self.sb
        owner = os.getuid()
        if owner == 0:  # running as root: give CODEX_HOME to nobody so that its owner is not root
            owner = 65534
            os.chown(sb.codex_home, owner, -1)
        make_fake(sb.bin / "id", FAKE_ID)
        make_fake(sb.bin / "sudo", FAKE_SUDO)
        result = sb.run("install")
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = sb.calls()
        sudo_calls = [c for c in calls if c.startswith("sudo ")]
        self.assertTrue(sudo_calls, calls)
        self.assertTrue(all(c.startswith(f"sudo -u #{owner} ") for c in sudo_calls), sudo_calls)
        self.assertTrue(any(c.endswith(f"CODEX_HOME={sb.codex_home}") for c in calls), calls)


class UninstallTests(CodexTarget):
    def test_uninstall_removes_everything(self):
        sb = self.sb
        sb.run("install")
        result = sb.run("uninstall")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(sb.req.exists())
        self.assertFalse(sb.skill_dst.parent.exists())
        self.assertFalse(sb.worker_home.exists())
        self.assertFalse(sb.state.exists())

    def test_uninstall_keeps_other_content(self):
        sb = self.sb
        sb.req.parent.mkdir(parents=True)
        sb.req.write_bytes(OLD_TOML)
        sb.run("install")
        result = sb.run("uninstall")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(sb.req.read_bytes(), OLD_TOML)


class UsageTests(CodexTarget):
    def test_bad_action_and_missing_environment(self):
        self.assertEqual(self.sb.run("bogus").returncode, 2)
        self.assertEqual(self.sb.run("install", NODE_BIN=None).returncode, 1)


if __name__ == "__main__":
    unittest.main()
