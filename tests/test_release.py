"""Release consistency checks: one version across the plugin, the server and the changelog, and an MIT license.

Uses only the standard library. Reads files in this repository; no network, no model calls.
"""

import json
import re
import unittest
from pathlib import Path

ROOT =Path(__file__).resolve().parents[1]
PLUGIN_MANIFEST = ROOT / "plugins" / "worker" / ".claude-plugin" / "plugin.json"
SERVER = ROOT / "plugins" / "worker" / "runtime" / "worker-mcp.mjs"
CHANGELOG = ROOT / "CHANGELOG.md"
LICENSE = ROOT / "LICENSE"
SERVER_VERSION = re.compile(r"serverInfo:\s*\{[^}]*?\bversion:\s*['\"]([^'\"]+)['\"]")
CHANGELOG_VERSION = re.compile(r"^## \[(\d+\.\d+\.\d+)\]", re.MULTILINE)


def version_key(version):
    return tuple(int(part) for part in version.split("."))


def plugin_manifest():
    return json.loads(PLUGIN_MANIFEST.read_text(encoding="utf-8"))


class VersionConsistencyTests(unittest.TestCase):
    def test_plugin_version_matches_server_info(self):
        match = SERVER_VERSION.search(SERVER.read_text(encoding="utf-8"))
        self.assertIsNotNone(match, "worker-mcp.mjs must declare serverInfo with a version")
        self.assertEqual(match.group(1), plugin_manifest()["version"])

    def test_newest_changelog_entry_matches_version(self):
        self.assertTrue(CHANGELOG.is_file(), "CHANGELOG.md is missing")
        versions = CHANGELOG_VERSION.findall(CHANGELOG.read_text(encoding="utf-8"))
        self.assertTrue(versions, "CHANGELOG.md needs '## [x.y.z]' headings")
        newest = max(versions, key=version_key)
        self.assertEqual(versions[0], newest, "the newest CHANGELOG entry must be listed first")
        self.assertEqual(newest, plugin_manifest()["version"])


class LicenseTests(unittest.TestCase):
    def test_license_file_is_mit(self):
        self.assertTrue(LICENSE.is_file(), "LICENSE is missing")
        text = LICENSE.read_text(encoding="utf-8")
        self.assertTrue(text.startswith("MIT License"))
        self.assertIn("General-Worker-MCP contributors", text)

    def test_plugin_manifest_declares_mit(self):
        self.assertEqual(plugin_manifest().get("license"), "MIT")


if __name__ == "__main__":
    unittest.main()
