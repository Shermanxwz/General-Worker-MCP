"""Parse skill frontmatter as YAML, not only as matching text lines."""

from pathlib import Path
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[1]


class MetadataTests(unittest.TestCase):
    def metadata(self, provider):
        path = ROOT / provider / "skills/worker/SKILL.md"
        parts = path.read_text(encoding="utf-8").split("---", 2)
        self.assertEqual(len(parts), 3, "frontmatter must have opening and closing delimiters")
        self.assertFalse(parts[0].strip())
        metadata = yaml.safe_load(parts[1])
        self.assertIsInstance(metadata, dict)
        self.assertEqual(metadata["name"], path.parent.name)
        self.assertIsInstance(metadata["description"], str)
        self.assertTrue(metadata["description"].strip())
        self.assertTrue(parts[2].strip(), "skill instructions must not be empty")
        return metadata

    def test_codex_frontmatter(self):
        self.metadata("codex")

    def test_claude_hook_is_in_parsed_frontmatter(self):
        metadata = self.metadata("claude")
        self.assertIs(metadata["disable-model-invocation"], True)
        hooks = metadata["hooks"]["PreToolUse"]
        self.assertEqual(len(hooks), 1)
        self.assertIsInstance(hooks[0]["matcher"], str)
        command = hooks[0]["hooks"][0]
        self.assertEqual(command["type"], "command")
        self.assertTrue(command["command"].strip())


if __name__ == "__main__":
    unittest.main()
