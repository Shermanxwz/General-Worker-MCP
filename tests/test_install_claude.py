"""Tests for the Claude target of install.sh (scripts/claude.sh) against fake claude and node CLIs."""
import json
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
REAL_NODE = shutil.which("node") or ""
STRIPPED = ("CLAUDE_BIN", "WORKER_MARKETPLACE_SOURCE", "CLAUDE_SKILLS_DIR", "CLAUDE_CONFIG_DIR",
            "FAKE_CLAUDE_FAIL", "FAKE_CLAUDE_KEEP_PLUGINS", "NODE_BIN")

# Fake claude: logs its argv. State in $FAKE_CLAUDE_STATE: plugins (one id per line) and markets.json, the
# marketplace as `plugin marketplace list --json` lists it (absent when none is added). As in the CLI, a folder
# source is recorded as {"source": "directory", "path": <absolute folder>} and owner/repo as
# {"source": "github", "repo": ...}; adding a folder without a manifest fails; removing a marketplace removes
# its plugins unless FAKE_CLAUDE_KEEP_PLUGINS is set.
FAKE_CLAUDE = """#!/bin/sh
echo "$*" >> "$FAKE_CLAUDE_LOG"
S=$FAKE_CLAUDE_STATE
touch "$S/plugins"
has_plugin() { grep -Fqx "$1" "$S/plugins"; }
has_market() { [ -f "$S/markets.json" ]; }
add_market() {
  src=$1
  case $src in "~/"*) src=$HOME/${src#"~/"} ;; esac
  if [ -d "$src" ]; then
    [ -f "$src/.claude-plugin/marketplace.json" ] || { echo "Marketplace file not found" >&2; exit 1; }
    p=$(cd "$src" && pwd)
    printf '[{"name":"general-worker-mcp","source":"directory","path":"%s","installLocation":"%s"}]' "$p" "$p" > "$S/markets.json"
  else
    case $src in /* | ./* | ../*) echo "Marketplace path not found: $src" >&2; exit 1 ;; esac
    printf '[{"name":"general-worker-mcp","source":"github","repo":"%s","installLocation":"/cache"}]' "$src" > "$S/markets.json"
  fi
}
if [ -n "${FAKE_CLAUDE_FAIL-}" ]; then
  case "$*" in
    "$FAKE_CLAUDE_FAIL" | "$FAKE_CLAUDE_FAIL "*) echo "fake failure" >&2; exit 1 ;;
  esac
fi
case "$*" in
  "plugin list") cat "$S/plugins" ;;
  "plugin marketplace list --json") if has_market; then cat "$S/markets.json"; else echo "[]"; fi ;;
  "plugin marketplace add "*) has_market && exit 1; add_market "$4" ;;
  "plugin marketplace update general-worker-mcp") has_market || exit 1 ;;
  "plugin marketplace remove general-worker-mcp") has_market || exit 1
    rm -f "$S/markets.json"
    [ -n "${FAKE_CLAUDE_KEEP_PLUGINS-}" ] || : > "$S/plugins" ;;
  "plugin install "*) has_market && ! has_plugin "$3" || exit 1; echo "$3" >> "$S/plugins" ;;
  "plugin update "*) has_plugin "$3" || exit 1 ;;
  "plugin uninstall "*) has_plugin "$3" || exit 1
    grep -vFx "$3" "$S/plugins" > "$S/p.tmp"; mv "$S/p.tmp" "$S/plugins" ;;
  *) exit 2 ;;
esac
"""

# Fake node: answers --version with the version it was written with; every other call runs the real node,
# which claude.sh uses to read the marketplace listing.
FAKE_NODE = '#!/bin/sh\ncase $1 in --version) echo %s ;; *) exec "%s" "$@" ;; esac\n'


def write_exec(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    path.chmod(0o755)
    return path


class ClaudeTargetTest(unittest.TestCase):
    def setUp(self):
        if not REAL_NODE:
            self.fail("node must be on PATH: claude.sh reads the marketplace listing with it")
        self.tmp = Path(tempfile.mkdtemp(prefix="claude-target-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.bin = self.tmp / "bin"
        self.config = self.tmp / "config"
        self.skills = self.config / "skills"
        self.state = self.tmp / "state"
        self.log = self.tmp / "claude.log"
        self.state.mkdir(parents=True)
        write_exec(self.bin / "claude", FAKE_CLAUDE)
        node = write_exec(self.bin / "node", FAKE_NODE % ("v22.0.0", REAL_NODE))
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

    def install(self, **env):
        return self.sh("install", "claude", **env)

    def status(self, **env):
        return self.sh("status", "claude", **env)

    def calls(self):
        return self.log.read_text().splitlines() if self.log.exists() else []

    def market(self):
        """The marketplace as the fake CLI lists it (`plugin marketplace list --json`), None when not added."""
        f = self.state / "markets.json"
        return json.loads(f.read_text())[0] if f.exists() else None

    def folder(self, name, manifest=True):
        """A folder under the test directory, with a marketplace manifest unless manifest is False."""
        d = self.tmp / name
        d.mkdir(parents=True, exist_ok=True)
        if manifest:
            (d / ".claude-plugin").mkdir(exist_ok=True)
            (d / ".claude-plugin" / "marketplace.json").write_text("{}")
        return d

    def test_install_adds_marketplace_then_installs_plugin(self):
        r = self.install()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.calls(), ["plugin marketplace list --json", f"plugin marketplace add {ROOT}",
                                        "plugin list", f"plugin install {PLUGIN} --scope user"])
        self.assertEqual(self.market()["path"], str(ROOT))

    def test_second_install_updates_marketplace_and_plugin(self):
        self.install()
        before = len(self.calls())
        r = self.install()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.calls()[before:], ["plugin marketplace list --json",
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
        self.assertEqual(self.calls(), ["plugin list", "plugin marketplace list --json"])
        self.install()
        before = len(self.calls())
        r = self.sh("uninstall", "claude")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.calls()[before:], ["plugin list", f"plugin uninstall {PLUGIN} --scope user",
                                                 "plugin marketplace list --json",
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
        old = write_exec(self.tmp / "old" / "node", FAKE_NODE % ("v16.20.0", REAL_NODE))
        spaced = write_exec(self.tmp / "dir with space" / "node", FAKE_NODE % ("v22.0.0", REAL_NODE))
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

    def test_source_change_on_reinstall_removes_and_re_adds_marketplace(self):
        a, b = self.folder("a"), self.folder("b")
        self.assertEqual(self.install(WORKER_MARKETPLACE_SOURCE=str(a)).returncode, 0)
        before = len(self.calls())
        r = self.install(WORKER_MARKETPLACE_SOURCE=str(b))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.calls()[before:], ["plugin marketplace list --json",
                                                 f"plugin marketplace remove {MARKET}",
                                                 f"plugin marketplace add {b}",
                                                 "plugin list", f"plugin install {PLUGIN} --scope user"])
        self.assertEqual(self.market()["path"], str(b))
        self.assertEqual(self.status(WORKER_MARKETPLACE_SOURCE=str(b)).returncode, 0)

    def test_source_change_updates_plugin_if_removal_leaves_it_installed(self):
        a, b = self.folder("a"), self.folder("b")
        self.install(WORKER_MARKETPLACE_SOURCE=str(a))
        before = len(self.calls())
        r = self.install(WORKER_MARKETPLACE_SOURCE=str(b), FAKE_CLAUDE_KEEP_PLUGINS="1")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.calls()[before:], ["plugin marketplace list --json",
                                                 f"plugin marketplace remove {MARKET}",
                                                 f"plugin marketplace add {b}",
                                                 "plugin list", f"plugin update {PLUGIN} --scope user"])
        self.assertEqual(self.market()["path"], str(b))

    def test_source_change_to_unusable_folder_fails_before_touching_marketplace(self):
        a = self.folder("a")
        self.install(WORKER_MARKETPLACE_SOURCE=str(a))
        for name, source in (("no manifest", self.folder("bad", manifest=False)),
                             ("missing", self.tmp / "missing")):
            with self.subTest(name):
                before = len(self.calls())
                r = self.install(WORKER_MARKETPLACE_SOURCE=str(source))
                self.assertEqual(r.returncode, 1)
                self.assertIn("failed", r.stderr)
                self.assertIn("marketplace.json", r.stderr)
                self.assertEqual(self.calls()[before:], [])
                self.assertEqual(self.market()["path"], str(a))

    def test_same_folder_spelled_differently_is_updated_not_switched(self):
        a = self.folder("a")
        self.install(WORKER_MARKETPLACE_SOURCE=str(a))
        # a trailing slash, a path relative to the working directory, and ~/ for the same folder
        for spelling in (f"{a}/", "a", "~/a"):
            with self.subTest(spelling=spelling):
                before = len(self.calls())
                r = self.install(WORKER_MARKETPLACE_SOURCE=spelling, HOME=str(self.tmp))
                self.assertEqual(r.returncode, 0, r.stderr)
                self.assertEqual(self.calls()[before:], ["plugin marketplace list --json",
                                                         f"plugin marketplace update {MARKET}",
                                                         "plugin list", f"plugin update {PLUGIN} --scope user"])

    def test_failed_steps_are_named_by_their_action(self):
        r = self.install(FAKE_CLAUDE_FAIL="plugin marketplace add")
        self.assertEqual(r.returncode, 1)
        self.assertIn("failed: marketplace add", r.stderr)
        self.assertNotIn("marketplace added", r.stdout + r.stderr)
        self.assertEqual(self.install().returncode, 0)
        r = self.install(FAKE_CLAUDE_FAIL="plugin marketplace update")
        self.assertEqual(r.returncode, 1)
        self.assertIn("failed: marketplace update", r.stderr)
        self.assertNotIn("marketplace updated", r.stdout + r.stderr)
        r = self.install(FAKE_CLAUDE_FAIL="plugin update")
        self.assertEqual(r.returncode, 1)
        self.assertIn("failed: plugin update", r.stderr)
        self.assertNotIn("plugin updated", r.stdout + r.stderr)

    def test_status_fails_when_folder_source_has_no_manifest(self):
        a = self.folder("a")
        self.install(WORKER_MARKETPLACE_SOURCE=str(a))
        (a / ".claude-plugin" / "marketplace.json").unlink()
        r = self.status(WORKER_MARKETPLACE_SOURCE=str(a))
        self.assertEqual(r.returncode, 1)
        self.assertIn("no .claude-plugin/marketplace.json", r.stdout)

    def test_status_fails_when_folder_source_was_moved(self):
        a = self.folder("a")
        self.install(WORKER_MARKETPLACE_SOURCE=str(a))
        a.rename(self.tmp / "moved")
        r = self.status(WORKER_MARKETPLACE_SOURCE=str(a))
        self.assertEqual(r.returncode, 1)
        self.assertIn("no .claude-plugin/marketplace.json", r.stdout)
        r = self.status(WORKER_MARKETPLACE_SOURCE=str(self.tmp / "moved"))
        self.assertEqual(r.returncode, 1)
        self.assertIn("run install to switch", r.stdout)

    def test_status_fails_when_recorded_source_differs(self):
        a, b = self.folder("a"), self.folder("b")
        self.install(WORKER_MARKETPLACE_SOURCE=str(a))
        r = self.status(WORKER_MARKETPLACE_SOURCE=str(b))
        self.assertEqual(r.returncode, 1)
        self.assertIn("run install to switch", r.stdout)
        r = self.status()  # the default source is this checkout, which is not the recorded one
        self.assertEqual(r.returncode, 1)
        self.assertIn("run install to switch", r.stdout)

    def test_marketplace_is_matched_by_name_not_by_path_text(self):
        other = [{"name": "other", "source": "directory", "path": str(self.tmp / MARKET), "installLocation": "x"}]
        (self.state / "markets.json").write_text(json.dumps(other))
        r = self.status(WORKER_MARKETPLACE_SOURCE=str(self.folder("a")))
        self.assertEqual(r.returncode, 1)
        self.assertIn("marketplace not added", r.stdout)


if __name__ == "__main__":
    unittest.main()
