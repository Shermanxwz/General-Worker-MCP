"""Metadata checks for the Claude Code plugin: marketplace, manifest, MCP config, hooks and skills.

Uses only unittest and PyYAML. Reads files in this repository; no network, no model calls.
"""

import json
import re
import unittest
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
PLUGIN_DIR = ROOT / "plugins" / "worker"
HOOK_EVENTS = {"UserPromptSubmit", "SessionStart", "PreToolUse", "PostToolUse"}
HOOK_COMMAND = 'node "${CLAUDE_PLUGIN_ROOT}/runtime/worker-hook.mjs" --runtime claude'
HOOK_TIMEOUT = 10
RUNTIME_FILES = {"runtime/worker-hook.mjs", "runtime/worker-mcp.mjs"}
NAME_PATTERN = r"^[a-z0-9]+(-[a-z0-9]+)*$"
# Strings each plugin skill body must contain.
SKILL_MARKERS = {
    "on": ["enter_worker_mode", "$ARGUMENTS", "【Worker 模式规则", "用户输入 /worker:on"],
    "off": ["exit_worker_mode", "Worker 模式已关闭", "用户输入 /worker:off"],
    "status": ["【Worker 模式规则", "Worker 模式当前：开"],
}


def load_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def read_skill(path):
    """Return (frontmatter mapping, body) for a SKILL.md with YAML frontmatter between '---' lines."""
    lines = path.read_text(encoding="utf-8").splitlines()
    closing = [i for i, line in enumerate(lines) if i > 0 and line.strip() == "---"]
    if not lines or lines[0].strip() != "---" or not closing:
        raise AssertionError(f"{path} needs YAML frontmatter between two '---' lines")
    metadata = yaml.safe_load("\n".join(lines[1:closing[0]]))
    if not isinstance(metadata, dict):
        raise AssertionError(f"{path} frontmatter must be a YAML mapping")
    return metadata, "\n".join(lines[closing[0] + 1:])


def plugin_skill(name):
    return read_skill(PLUGIN_DIR / "skills" / name / "SKILL.md")


def runtime_paths(value):
    """Relative paths of every ${CLAUDE_PLUGIN_ROOT}/... reference inside a parsed JSON value."""
    found = set()
    if isinstance(value, dict):
        for item in value.values():
            found |= runtime_paths(item)
    elif isinstance(value, list):
        for item in value:
            found |= runtime_paths(item)
    elif isinstance(value, str):
        found.update(re.findall(r'\$\{CLAUDE_PLUGIN_ROOT\}/([^\s"]+)', value))
    return found


class MarketplaceTests(unittest.TestCase):
    def test_marketplace_lists_the_worker_plugin(self):
        market = load_json(ROOT / ".claude-plugin" / "marketplace.json")
        self.assertEqual(market["name"], "general-worker-mcp")
        self.assertEqual(market["owner"]["name"], "Shermanxwz")
        self.assertEqual(len(market["plugins"]), 1)
        entry = market["plugins"][0]
        self.assertEqual(entry["name"], "worker")
        self.assertTrue(entry.get("description", "").strip())
        self.assertEqual(entry["source"], "./plugins/worker")
        self.assertNotIn("..", entry["source"].split("/"))
        source = ROOT / entry["source"]
        self.assertTrue(source.is_dir(), "marketplace source must be a directory in this repo")
        manifest = load_json(source / ".claude-plugin" / "plugin.json")
        self.assertEqual(manifest["name"], entry["name"], "entry name must equal the plugin manifest name")


class PluginManifestTests(unittest.TestCase):
    def test_plugin_manifest_fields(self):
        manifest = load_json(PLUGIN_DIR / ".claude-plugin" / "plugin.json")
        self.assertEqual(manifest["name"], "worker")
        self.assertRegex(manifest["name"], NAME_PATTERN)
        self.assertEqual(manifest["version"], "2.0.0")
        self.assertTrue(manifest.get("description", "").strip())
        self.assertEqual(manifest["author"]["name"], "Shermanxwz")
        self.assertEqual(manifest["repository"], "https://github.com/Shermanxwz/General-Worker-MCP")


class McpConfigTests(unittest.TestCase):
    def test_worker_server_is_the_only_registration(self):
        config = load_json(PLUGIN_DIR / ".mcp.json")
        self.assertEqual(list(config["mcpServers"]), ["worker"])
        server = config["mcpServers"]["worker"]
        self.assertEqual(server["command"], "node")
        self.assertEqual(server["args"], ["${CLAUDE_PLUGIN_ROOT}/runtime/worker-mcp.mjs"])


class HooksConfigTests(unittest.TestCase):
    def setUp(self):
        self.hooks = load_json(PLUGIN_DIR / "hooks" / "hooks.json")["hooks"]

    def test_events_and_handlers(self):
        self.assertEqual(set(self.hooks), HOOK_EVENTS)
        for event in sorted(HOOK_EVENTS):
            with self.subTest(event=event):
                groups = self.hooks[event]
                self.assertEqual(len(groups), 1)
                handlers = groups[0]["hooks"]
                self.assertEqual(len(handlers), 1)
                self.assertEqual(handlers[0]["type"], "command")
                self.assertEqual(handlers[0]["command"], HOOK_COMMAND)
                self.assertEqual(handlers[0]["timeout"], HOOK_TIMEOUT)

    def test_only_post_tool_use_has_a_matcher(self):
        for event in ("UserPromptSubmit", "SessionStart", "PreToolUse"):
            with self.subTest(event=event):
                self.assertNotIn("matcher", self.hooks[event][0])
        self.assertIn("matcher", self.hooks["PostToolUse"][0])

    def test_post_tool_use_matcher_selects_only_plugin_control_tools(self):
        matcher = self.hooks["PostToolUse"][0]["matcher"]
        for tool in (
            "mcp__plugin_worker_worker__enter_worker_mode",
            "mcp__plugin_worker_worker__exit_worker_mode",
        ):
            with self.subTest(tool=tool):
                self.assertIsNotNone(re.search(matcher, tool))
        for tool in (
            "mcp__evil__exit_worker_mode",
            "mcp__worker__exit_worker_mode",
            "mcp__github__push_files",
            "Bash",
            "Edit",
        ):
            with self.subTest(tool=tool):
                self.assertIsNone(re.search(matcher, tool))


class RuntimeReferenceTests(unittest.TestCase):
    def test_config_references_exactly_the_runtime_files(self):
        mcp = load_json(PLUGIN_DIR / ".mcp.json")
        hooks = load_json(PLUGIN_DIR / "hooks" / "hooks.json")
        self.assertEqual(runtime_paths(mcp) | runtime_paths(hooks), RUNTIME_FILES)

    def test_runtime_files_exist(self):
        for rel in sorted(RUNTIME_FILES):
            with self.subTest(file=rel):
                self.assertTrue((PLUGIN_DIR / rel).is_file(), f"missing plugins/worker/{rel}")


class PluginSkillTests(unittest.TestCase):
    def test_frontmatter(self):
        # Names are quoted in the skill files: a bare on/off is a boolean in YAML 1.1 (PyYAML).
        for name in SKILL_MARKERS:
            with self.subTest(skill=name):
                metadata, body = plugin_skill(name)
                self.assertEqual(metadata.get("name"), name)
                self.assertIsInstance(metadata.get("description"), str)
                self.assertTrue(metadata["description"].strip())
                self.assertIs(metadata.get("disable-model-invocation"), True)
                self.assertTrue(body.strip())

    def test_bodies_contain_their_instructions(self):
        for name, markers in SKILL_MARKERS.items():
            with self.subTest(skill=name):
                _, body = plugin_skill(name)
                for marker in markers:
                    self.assertIn(marker, body)

    def test_on_skill_takes_task_arguments(self):
        metadata, _ = plugin_skill("on")
        self.assertEqual(metadata.get("argument-hint"), "[任务；可写明 worker 用的模型和强度]")

    def test_status_skill_names_no_tools(self):
        _, body = plugin_skill("status")
        self.assertNotIn("_worker_mode", body)


class CodexSkillTests(unittest.TestCase):
    def test_codex_frontmatter(self):
        metadata, body = read_skill(ROOT / "codex" / "skills" / "worker" / "SKILL.md")
        self.assertEqual(metadata.get("name"), "worker")
        self.assertIsInstance(metadata.get("description"), str)
        self.assertTrue(metadata["description"].strip())
        self.assertTrue(body.strip())


class V1RemovalTests(unittest.TestCase):
    def test_v1_claude_skill_and_hook_test_are_removed(self):
        self.assertFalse((ROOT / "claude").exists())
        self.assertFalse((ROOT / "tests" / "test-hook.sh").exists())


if __name__ == "__main__":
    unittest.main()
