"""Fake codex, node, id and sudo executables and a per-test sandbox for tests/test_install_codex.py.

Stdlib only. Every install path lives under a temp root; the fakes sit in root/bin, which is put
first on PATH, so the real codex, node, id and sudo are never used.
"""
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "codex.sh"
RUNTIME_SRC = ROOT / "plugins" / "worker" / "runtime"
RULES_SRC = ROOT / "plugins" / "worker" / "rules"
SKILL_SRC = ROOT / "codex" / "skills" / "worker" / "SKILL.md"

# Logs argv and the CODEX_HOME it saw. FAKE_STATE exists while the worker MCP entry is registered.
FAKE_CODEX = """#!/bin/sh
echo "codex $* CODEX_HOME=${CODEX_HOME-<unset>}" >> "$FAKE_LOG"
case "$1 $2" in
  "mcp list") if [ -f "$FAKE_STATE" ]; then echo "worker  stdio"; else echo "No MCP servers configured."; fi ;;
  "mcp add") : > "$FAKE_STATE" ;;
  "mcp remove") rm -f "$FAKE_STATE" ;;
esac
"""

FAKE_NODE = "#!/bin/sh\necho v22.0.0\n"
FAKE_ID = "#!/bin/sh\necho 0\n"

# Logs argv, drops "-u <uid>" and runs the rest of the command.
FAKE_SUDO = """#!/bin/sh
echo "sudo $*" >> "$FAKE_LOG"
shift 2
exec "$@"
"""


def make_fake(path, body):
    path.write_text(body)
    path.chmod(0o755)


class Sandbox:
    """One test's temp root: fakes in bin/, every install path under the root."""

    def __init__(self, testcase):
        self.root = Path(tempfile.mkdtemp(prefix="codex-test-"))
        testcase.addCleanup(shutil.rmtree, self.root, True)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.node = self.bin / "node"
        self.codex = self.bin / "codex"
        make_fake(self.node, FAKE_NODE)
        make_fake(self.codex, FAKE_CODEX)
        self.log = self.root / "fake.log"
        self.state = self.root / "mcp.state"
        self.codex_home = self.root / "codex-home"
        self.codex_home.mkdir()
        self.worker_home = self.root / "opt" / "worker"
        self.skills = self.root / "skills"
        self.skill_dst = self.skills / "worker" / "SKILL.md"
        self.req = self.root / "etc" / "codex" / "requirements.toml"

    def run(self, action, **overrides):
        """Run scripts/codex.sh with REPO_DIR, NODE_BIN and the fakes set; None in overrides unsets a variable."""
        env = dict(os.environ)
        env.update(
            PATH=f"{self.bin}{os.pathsep}{os.environ.get('PATH', '')}",
            REPO_DIR=str(ROOT),
            NODE_BIN=str(self.node),
            CODEX_BIN=str(self.codex),
            CODEX_HOME=str(self.codex_home),
            WORKER_HOME=str(self.worker_home),
            CODEX_SKILLS_DIR=str(self.skills),
            CODEX_REQUIREMENTS_FILE=str(self.req),
            FAKE_LOG=str(self.log),
            FAKE_STATE=str(self.state),
        )
        for key, value in overrides.items():
            if value is None:
                env.pop(key, None)
            else:
                env[key] = value
        return subprocess.run(["sh", str(SCRIPT), action], env=env, capture_output=True, text=True)

    def calls(self):
        return self.log.read_text().splitlines() if self.log.exists() else []
