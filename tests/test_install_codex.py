"""Tests for scripts/codex.sh, the Codex target of install.sh. Run from the repo root:

    python3 -m unittest discover -s tests -p 'test_install_codex.py' -v
"""
import os
import re
import shutil
import stat
import subprocess
import sys
import unittest
from pathlib import Path

from codex_fakes import FAKE_ID, FAKE_SUDO, ROOT, RULES_SRC, RUNTIME_SRC, SKILL_SRC, Sandbox, make_fake

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

    def test_missing_runtime_sources_are_reported(self):
        # A checkout without plugins/worker cannot supply the runtime: the step fails and creates nothing.
        sb = self.sb
        broken = sb.root / "checkout"
        shutil.copytree(ROOT / "scripts", broken / "scripts")
        shutil.copytree(ROOT / "codex", broken / "codex")
        result = sb.run("install", REPO_DIR=str(broken))
        self.assertEqual(result.returncode, 1)
        self.assertIn("runtime or rules directory missing", result.stderr)
        self.assertFalse(sb.worker_home.exists())

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

    def test_directories_are_0755_under_a_strict_umask(self):
        # The installer creates directories under umask 022, so the parents it creates are 0755 as well.
        sb = self.sb
        result = sb.run("install", umask=0o077)
        self.assertEqual(result.returncode, 0, result.stderr)
        for directory in (sb.root / "opt", sb.worker_home, sb.worker_home / "runtime", sb.worker_home / "rules",
                          sb.root / "skills", sb.skill_dst.parent, sb.root / "etc", sb.req.parent):
            self.assertEqual(mode(directory), 0o755, directory)

    def test_post_tool_use_matcher_selects_only_the_codex_control_tools(self):
        self.sb.run("install")
        found = re.search(r'^matcher = "(.*)"$', self.sb.req.read_text(), re.M)
        self.assertIsNotNone(found)
        pattern = found.group(1)
        self.assertEqual(pattern, "^mcp__worker__(enter|exit)_worker_mode$")
        for name, expected in (("mcp__worker__enter_worker_mode", True),
                               ("mcp__worker__exit_worker_mode", True),
                               ("mcp__plugin_worker_worker__enter_worker_mode", False),
                               ("mcp__docs__enter_worker_mode", False),
                               ("mcp__worker__status", False)):
            with self.subTest(name):
                self.assertEqual(re.search(pattern, name) is not None, expected)

    def test_install_warns_about_a_non_default_requirements_path(self):
        result = self.sb.run("install")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Codex reads only /etc/codex/requirements.toml", result.stderr)

    def test_runtime_copy_failure_stops_the_install(self):
        sb = self.sb
        (sb.worker_home / "runtime" / "worker-hook.mjs").mkdir(parents=True)  # a directory where the copy goes
        result = sb.run("install")
        self.assertEqual(result.returncode, 1)
        self.assertIn("is a directory", result.stderr)
        self.assertEqual(sb.calls(), [], "hooks and MCP must not be registered")
        self.assertFalse(sb.req.exists())
        self.assertFalse(sb.skill_dst.exists())

    def test_missing_entry_file_stops_the_install_before_hooks_and_mcp(self):
        for entry in ("worker-hook.mjs", "worker-mcp.mjs"):
            with self.subTest(entry):
                sb = Sandbox(self)
                checkout = sb.make_checkout(drop_runtime=(entry,))
                result = sb.run("install", REPO_DIR=str(checkout))
                self.assertEqual(result.returncode, 1)
                self.assertIn(f"{entry} is missing", result.stderr)
                self.assertEqual(sb.calls(), [], "hooks and MCP must not be registered")
                self.assertFalse(sb.req.exists())
                self.assertFalse(sb.skill_dst.exists())

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
        self.assertEqual(hooks["PostToolUse"][0]["matcher"], "^mcp__worker__(enter|exit)_worker_mode$")

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

    def test_unsafe_worker_home_is_refused(self):
        # WORKER_HOME is written into the hook commands: absolute, letters, digits and . _ / + - only.
        for label in ("with space", 'quo"te', "back\\slash", "dol$ar", "relative"):
            with self.subTest(label=label):
                sb = Sandbox(self)
                value = "relative/worker" if label == "relative" else str(sb.root / label)
                result = sb.run("install", WORKER_HOME=value)
                self.assertEqual(result.returncode, 1)
                self.assertIn("WORKER_HOME must be an absolute path of letters, digits and . _ / + - only", result.stderr)
                self.assertFalse(Path(value).exists())
                self.assertFalse(sb.req.exists())
                self.assertEqual(sb.calls(), [])

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


class ManagedBlockTests(CodexTarget):
    def test_block_without_end_marker_is_left_alone(self):
        # Without the end marker an edit could cut into the content after the start marker, so each step refuses.
        sb = self.sb
        sb.req.parent.mkdir(parents=True)
        damaged = BEGIN + '\n[hooks]\nmanaged_dir = "/elsewhere"\n# keep me\n'
        sb.req.write_text(damaged)
        result = sb.run("uninstall")
        self.assertEqual(result.returncode, 1)
        self.assertIn("no end marker", result.stderr)
        self.assertEqual(sb.req.read_text(), damaged)
        result = sb.run("install")
        self.assertEqual(result.returncode, 1)
        self.assertIn("no end marker", result.stderr)
        self.assertEqual(sb.req.read_text(), damaged)
        self.assertIn("requirements block: differs", sb.run("status").stdout)


    def test_hooks_key_inside_another_table_is_allowed(self):
        sb = self.sb
        sb.req.parent.mkdir(parents=True)
        sb.req.write_text("[features]\nhooks = true\n")
        result = sb.run("install")
        self.assertEqual(result.returncode, 0, result.stderr)
        text = sb.req.read_text()
        self.assertTrue(text.startswith("[features]\nhooks = true\n"))
        self.assertEqual(text.count(BEGIN), 1)

    @unittest.skipIf(sys.version_info < (3, 11), "tomllib needs Python 3.11+")
    def test_features_hooks_and_managed_hooks_parse_together(self):
        import tomllib

        sb = self.sb
        sb.req.parent.mkdir(parents=True)
        sb.req.write_text("[features]\nhooks = true\n")
        self.assertEqual(sb.run("install").returncode, 0)
        parsed = tomllib.loads(sb.req.read_text())
        self.assertIs(parsed["features"]["hooks"], True)
        self.assertEqual(parsed["hooks"]["managed_dir"], str(sb.worker_home))

    def test_hooks_defined_outside_the_managed_block_are_refused(self):
        managed = f'{BEGIN}\n[hooks]\nmanaged_dir = "/old"\n{END}\n'
        cases = {
            "root key": "hooks = true\n",
            "root dotted key": "hooks.Stop = []\n",
            "spaced table header": '[ hooks ]\nmanaged_dir = "/x"\n',
            "array of tables": "[[hooks]]\n",
            "table after the managed block": managed + "[hooks.Stop]\n",
        }
        for label, text in cases.items():
            with self.subTest(label):
                sb = Sandbox(self)
                sb.req.parent.mkdir(parents=True)
                sb.req.write_text(text)
                result = sb.run("install")
                self.assertEqual(result.returncode, 1, result.stderr)
                self.assertIn("merge", result.stderr)
                self.assertEqual(sb.req.read_text(), text)


class SkillBackupTests(CodexTarget):
    def backup(self):
        return self.sb.skill_dst.with_name("SKILL.md.bak")

    def test_first_original_is_kept_and_uninstall_restores_it(self):
        sb = self.sb
        sb.skill_dst.parent.mkdir(parents=True)
        sb.skill_dst.write_text("old skill\n")
        self.assertEqual(sb.run("install").returncode, 0)
        self.assertEqual(self.backup().read_text(), "old skill\n")
        sb.skill_dst.write_text("edited by hand\n")
        result = sb.run("install")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("is not the worker skill", result.stderr)
        self.assertEqual(self.backup().read_text(), "old skill\n", "a later install must not replace the backup")
        self.assertEqual(sb.skill_dst.read_bytes(), SKILL_SRC.read_bytes())
        result = sb.run("uninstall")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(sb.skill_dst.read_text(), "old skill\n")
        self.assertFalse(self.backup().exists())
        self.assertFalse(sb.skill_dst.with_name("SKILL.md.installed").exists())

    def test_reinstall_over_an_older_copy_of_the_skill_keeps_no_backup(self):
        sb = self.sb
        sb.run("install")
        older = SKILL_SRC.read_bytes() + b"<!-- an older release of this skill -->\n"
        sb.skill_dst.write_bytes(older)
        sb.skill_dst.with_name("SKILL.md.installed").write_bytes(older)
        result = sb.run("install")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(self.backup().exists(), "our own older copy is not an original")
        self.assertEqual(sb.skill_dst.read_bytes(), SKILL_SRC.read_bytes())
        result = sb.run("uninstall")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(sb.skill_dst.exists())
        self.assertFalse(sb.skill_dst.parent.exists())

    def test_uninstall_leaves_a_skill_file_it_did_not_write(self):
        sb = self.sb
        sb.run("install")
        sb.skill_dst.write_text("my own skill\n")
        result = sb.run("uninstall")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("left in place", result.stderr)
        self.assertEqual(sb.skill_dst.read_text(), "my own skill\n")
        self.assertFalse(sb.skill_dst.with_name("SKILL.md.installed").exists())


class UninstallTests(CodexTarget):
    def test_uninstall_removes_everything(self):
        sb = self.sb
        sb.run("install")
        result = sb.run("uninstall")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(sb.req.exists())
        self.assertFalse(sb.skill_dst.parent.exists())
        self.assertFalse(sb.worker_home.exists())
        self.assertEqual(sb.servers(), [])

    def test_uninstall_keeps_other_content(self):
        sb = self.sb
        sb.req.parent.mkdir(parents=True)
        sb.req.write_bytes(OLD_TOML)
        sb.run("install")
        result = sb.run("uninstall")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(sb.req.read_bytes(), OLD_TOML)


class McpTests(CodexTarget):
    # Another server whose args contain "worker": a substring match on `codex mcp list` would count it.
    DECOY = ("docs", "/usr/bin/node", ["/srv/worker/tools.mjs"])

    def test_status_does_not_count_a_server_whose_args_contain_worker(self):
        sb = self.sb
        sb.seed(self.DECOY)
        result = sb.run("status")
        self.assertEqual(result.returncode, 1)
        self.assertIn("MCP server worker: not listed", result.stdout)
        self.assertNotIn("MCP server worker: listed", result.stdout)

    def test_install_and_uninstall_touch_only_the_worker_entry(self):
        sb = self.sb
        sb.seed(self.DECOY)
        result = sb.run("install")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(sb.servers(), ["docs", "worker"])
        self.assertIn("MCP server worker: listed", sb.run("status").stdout)
        result = sb.run("uninstall")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(sb.servers(), ["docs"])
        self.assertFalse(any(c.startswith("codex mcp remove docs") for c in sb.calls()), sb.calls())
        self.assertIn("MCP server worker not listed", sb.run("uninstall").stdout)

    def test_unreadable_server_list_is_an_error(self):
        result = self.sb.run("status", CODEX_HOME=None)
        self.assertEqual(result.returncode, 1)
        self.assertIn("MCP server worker: could not list", result.stdout)

    def test_unreadable_server_list_blocks_registration(self):
        # Without the list the installer cannot tell whether worker is registered, so it adds nothing.
        sb = self.sb
        broken = sb.bin / "codex-broken"
        make_fake(broken, '#!/bin/sh\necho "broken $*" >> "$FAKE_LOG"\necho "unknown option" >&2\nexit 2\n')
        result = sb.run("install", CODEX_BIN=str(broken))
        self.assertEqual(result.returncode, 1)
        self.assertIn("could not read the MCP server list", result.stderr)
        self.assertEqual(sb.calls(), ["broken mcp list --json"])
        self.assertNotIn("next:", result.stdout)

    def test_missing_codex_binary_is_named(self):
        result = self.sb.run("install", CODEX_BIN=str(self.sb.root / "nope" / "codex"))
        self.assertEqual(result.returncode, 1)
        self.assertIn("CODEX_BIN is not a command", result.stderr)

    def test_failed_mcp_remove_is_reported(self):
        # The wrapper lists servers through the fake codex but refuses `mcp remove`.
        sb = self.sb
        sb.seed(("worker", "/usr/bin/node", ["/x/worker-mcp.mjs"]))
        wrapper = sb.bin / "codex-no-remove"
        make_fake(wrapper, f'#!/bin/sh\nif [ "$1" = mcp ] && [ "$2" = remove ]; then echo refused >&2; exit 3; fi\n'
                           f'exec "{sb.codex}" "$@"\n')
        result = sb.run("uninstall", CODEX_BIN=str(wrapper))
        self.assertEqual(result.returncode, 1)
        self.assertIn("could not remove the worker MCP server", result.stderr)
        self.assertEqual(sb.servers(), ["worker"])


class RequirementsPathTests(CodexTarget):
    def test_status_warns_when_the_requirements_path_is_not_the_default(self):
        sb = self.sb
        sb.run("install")
        result = sb.run("status")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("CODEX_REQUIREMENTS_FILE is", result.stderr)
        self.assertIn("Codex reads only /etc/codex/requirements.toml", result.stderr)

    def test_status_has_no_warning_for_the_default_path(self):
        # Status only reads the default file; nothing is written outside the sandbox.
        result = self.sb.run("status", CODEX_REQUIREMENTS_FILE=None)
        self.assertNotIn("Codex reads only", result.stderr)


class UsageTests(CodexTarget):
    def test_bad_action_and_missing_environment(self):
        self.assertEqual(self.sb.run("bogus").returncode, 2)
        self.assertEqual(self.sb.run("install", NODE_BIN=None).returncode, 1)

    def test_unsafe_node_bin_is_refused(self):
        # NODE_BIN is written into the hook commands too: absolute, letters, digits and . _ / + - only.
        for label in ("relative", 'quo"te', "back\\slash", "dol$ar", "with space"):
            with self.subTest(label=label):
                sb = Sandbox(self)
                value = "bin/node" if label == "relative" else f"{sb.root / label}/node"
                result = sb.run("install", NODE_BIN=value)
                self.assertEqual(result.returncode, 1)
                self.assertIn("NODE_BIN must be an absolute path of letters, digits and . _ / + - only", result.stderr)
                self.assertFalse((sb.worker_home / "runtime").exists())
                self.assertFalse(sb.req.exists())
                self.assertEqual(sb.calls(), [])


class ScriptModeTests(unittest.TestCase):
    """Checks the executable bit and git's recorded mode, not the exact on-disk bits, so any umask passes."""

    EXECUTABLE = ("install.sh", "scripts/claude.sh", "scripts/codex.sh",
                  "tests/check-codex-skills.py", "tests/check-codex-hooks.py")
    SOURCED = ("scripts/common.sh", "scripts/codex-files.sh", "scripts/codex-config.sh")

    def recorded_mode(self, name):
        """The mode git has in its index for the path; None when git is unavailable or the path is not tracked."""
        try:
            out = subprocess.run(["git", "-C", str(ROOT), "ls-files", "-s", "--", name],
                                 capture_output=True, text=True, check=True).stdout
        except (OSError, subprocess.CalledProcessError):
            return None
        return out.split()[0] if out.strip() else None

    def test_executable_files(self):
        for name in self.EXECUTABLE:
            with self.subTest(name):
                self.assertTrue(mode(ROOT / name) & stat.S_IXUSR, name)
                recorded = self.recorded_mode(name)
                if recorded is not None:
                    self.assertEqual(recorded, "100755", name)

    def test_sourced_files(self):
        for name in self.SOURCED:
            with self.subTest(name):
                self.assertFalse(mode(ROOT / name) & (stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH), name)
                recorded = self.recorded_mode(name)
                if recorded is not None:
                    self.assertEqual(recorded, "100644", name)


if __name__ == "__main__":
    unittest.main()
