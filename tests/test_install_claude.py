"""Tests for the Claude target of install.sh (scripts/claude.sh) against fake claude and node CLIs."""
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(os.path.abspath(__file__)).parent.parent
PLUGIN = "worker@general-worker-mcp"
MARKET = "general-worker-mcp"
V1_MARKER = 'has("agent_id")'
STRIPPED = ("CLAUDE_BIN", "WORKER_MARKETPLACE_SOURCE", "CLAUDE_SKILLS_DIR", "CLAUDE_CONFIG_DIR",
            "FAKE_CLAUDE_FAIL", "NODE_BIN")

# Fake claude: logs its argv; marketplace and plugin names are kept one per line in $FAKE_CLAUDE_STATE.
FAKE_CLAUDE = """#!/bin/sh
echo "$*" >> "$FAKE_CLAUDE_LOG"
S=$FAKE_CLAUDE_STATE
touch "$S/markets" "$S/plugins"
has() { grep -Fqx "$2" "$S/$1"; }
[ "${FAKE_CLAUDE_FAIL-}" != "$1 $2" ] || { echo "fake failure" >&2; exit 1; }
case "$*" in
  "plugin list") cat "$S/plugins" ;;
  "plugin marketplace list") cat "$S/markets" ;;
  "plugin marketplace add "*) has markets general-worker-mcp && exit 1; echo general-worker-mcp >> "$S/markets" ;;
  "plugin marketplace update general-worker-mcp") has markets general-worker-mcp || exit 1 ;;
  "plugin marketplace remove general-worker-mcp") has markets general-worker-mcp || exit 1
    grep -vFx general-worker-mcp "$S/markets" > "$S/m.tmp"; mv "$S/m.tmp" "$S/markets" ;;
  "plugin install "*) has markets general-worker-mcp && ! has plugins "$3" || exit 1; echo "$3" >> "$S/plugins" ;;
  "plugin update "*) has plugins "$3" || exit 1 ;;
  "plugin uninstall "*) has plugins "$3" || exit 1
    grep -vFx "$3" "$S/plugins" > "$S/p.tmp"; mv "$S/p.tmp" "$S/plugins" ;;
  *) exit 2 ;;
esac
"""

# Fake node: answers --version with the version it was written with.
FAKE_NODE = "#!/bin/sh\ncase $1 in --version) echo %s ;; esac\n"


def write_exec(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    path.chmod(0o755)
    return path


class ClaudeTargetTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="claude-target-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.bin = self.tmp / "bin"
        self.config = self.tmp / "config"
        self.skills = self.config / "skills"
        self.state = self.tmp / "state"
        self.log = self.tmp / "claude.log"
        self.state.mkdir(parents=True)
        write_exec(self.bin / "claude", FAKE_CLAUDE)
        node = write_exec(self.bin / "node", FAKE_NODE % "v22.0.0")
        self.env = {k: v for k, v in os.environ.items() if k not in STRIPPED}
        self.env.update(
            PATH=f"{self.bin}{os.pathsep}{os.environ.get('PATH', '')}",
            HOME=str(self.tmp / "home"),
            CLAUDE_CONFIG_DIR=str(self.config),
            CLAUDE_SKILLS_DIR=str(self.skills),
            NODE_BIN=str(node),
            FAKE_CLAUDE_LOG=str(self.log),
            FAKE_CLAUDE_STATE=str(self.state),
        )

    def sh(self, *args, **env):
        """Runs install.sh with the given arguments; keyword arguments override the environment."""
        return subprocess.run(["sh", str(ROOT / "install.sh"), *args], cwd=self.tmp,
                              env={**self.env, **env}, capture_output=True, text=True, timeout=60)

    def install(self):
        return self.sh("install", "claude")

    def calls(self):
        return self.log.read_text().splitlines() if self.log.exists() else []

    def test_install_adds_marketplace_then_installs_plugin(self):
        r = self.install()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.calls(), ["plugin marketplace list", f"plugin marketplace add {ROOT}",
                                        "plugin list", f"plugin install {PLUGIN} --scope user"])

    def test_second_install_updates_marketplace_and_plugin(self):
        self.install()
        before = len(self.calls())
        r = self.install()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.calls()[before:], ["plugin marketplace list",
                                                 f"plugin marketplace update {MARKET}",
                                                 "plugin list", f"plugin update {PLUGIN} --scope user"])

    def test_status_is_one_until_installed(self):
        self.assertEqual(self.sh("status", "claude").returncode, 1)
        self.install()
        r = self.sh("status", "claude")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn(PLUGIN, r.stdout)

    def test_uninstall_removes_plugin_and_marketplace(self):
        self.assertEqual(self.sh("uninstall", "claude").returncode, 0)
        self.assertEqual(self.calls(), ["plugin list", "plugin marketplace list"])
        self.install()
        before = len(self.calls())
        r = self.sh("uninstall", "claude")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.calls()[before:], ["plugin list", f"plugin uninstall {PLUGIN} --scope user",
                                                 "plugin marketplace list",
                                                 f"plugin marketplace remove {MARKET}"])
        self.assertEqual(self.sh("status", "claude").returncode, 1)

    def test_v1_skill_removed_with_backup_and_empty_dir(self):
        d = self.skills / "worker"
        d.mkdir(parents=True)
        (d / "SKILL.md").write_text(f"# v1\nx | {V1_MARKER}\n")
        (d / "SKILL.md.bak").write_text("old\n")
        r = self.install()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse(d.exists())

    def test_foreign_skill_is_left_alone_with_warning_and_status_one(self):
        d = self.skills / "worker"
        d.mkdir(parents=True)
        (d / "SKILL.md").write_text("# my own skill\n")
        r = self.install()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual((d / "SKILL.md").read_text(), "# my own skill\n")
        self.assertIn("warning", r.stderr)
        self.assertEqual(self.sh("status", "claude").returncode, 1)

    def test_bad_node_bin_fails_before_claude_is_called(self):
        old = write_exec(self.tmp / "old" / "node", FAKE_NODE % "v16.20.0")
        spaced = write_exec(self.tmp / "dir with space" / "node", FAKE_NODE % "v22.0.0")
        cases = {"missing": self.tmp / "nope" / "node", "too old": old, "space": spaced}
        for name, node in cases.items():
            with self.subTest(name):
                r = self.sh("install", "claude", NODE_BIN=str(node))
                self.assertEqual(r.returncode, 1)
                self.assertIn("NODE_BIN", r.stderr)
                self.assertEqual(self.calls(), [])

    def test_bad_arguments_exit_2_and_call_nothing(self):
        for args in (["bogus"], ["install", "bogus"], ["frobnicate", "claude"], ["install", "claude", "extra"]):
            with self.subTest(args=args):
                r = self.sh(*args)
                self.assertEqual(r.returncode, 2)
                self.assertIn("usage", r.stderr)
                self.assertEqual(self.calls(), [])

    def test_help_exits_0_with_usage(self):
        for flag in ("--help", "-h"):
            with self.subTest(flag=flag):
                r = self.sh(flag)
                self.assertEqual(r.returncode, 0)
                self.assertIn("usage:", r.stdout)
                self.assertIn("CLAUDE_BIN", r.stdout)
                self.assertEqual(self.calls(), [])

    def test_failed_plugin_install_exits_1(self):
        r = self.sh("install", "claude", FAKE_CLAUDE_FAIL="plugin install")
        self.assertEqual(r.returncode, 1)
        self.assertIn("failed", r.stderr)

    def test_defaults_follow_environment(self):
        d = self.skills / "worker"
        d.mkdir(parents=True)
        (d / "SKILL.md").write_text(f"# v1\n{V1_MARKER}\n")
        r = self.sh("install", "claude", CLAUDE_SKILLS_DIR="", WORKER_MARKETPLACE_SOURCE="Shermanxwz/General-Worker-MCP")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("plugin marketplace add Shermanxwz/General-Worker-MCP", self.calls())
        self.assertFalse(d.exists())


if __name__ == "__main__":
    unittest.main()
