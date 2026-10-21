"""Fake codex, node, id and sudo executables and a per-test sandbox for tests/test_install_codex.py.

Stdlib only. Every install path lives under a temp root; the fakes sit in root/bin, which is put
first on PATH, so the real codex, id and sudo are never used. The fake node runs the real node for
everything except its version check, because the installer reads `codex mcp list --json` with it.
"""
import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "codex.sh"
RUNTIME_SRC = ROOT / "plugins" / "worker" / "runtime"
RULES_SRC = ROOT / "plugins" / "worker" / "rules"
SKILL_SRC = ROOT / "codex" / "skills" / "worker" / "SKILL.md"
REAL_NODE = shutil.which("node")

# Stands in for codex 0.163.0 and implements the three calls the installer makes, with the output of
# the real CLI: `mcp list --json` prints a JSON array of servers on stdout (`[]` when there are none),
# `mcp add` prints "Added global MCP server '<name>'.", and `mcp remove` prints "Removed ..." or
# "No MCP server named '<name>' found." and exits 0 in both cases. Servers live in FAKE_STATE as a
# JSON array. Logs argv and the CODEX_HOME it saw.
FAKE_CODEX_BODY = r"""import json
import os
import sys

args = sys.argv[1:]
with open(os.environ["FAKE_LOG"], "a") as log:
    log.write("codex %s CODEX_HOME=%s\n" % (" ".join(args), os.environ.get("CODEX_HOME", "<unset>")))
state = os.environ["FAKE_STATE"]


def load():
    if not os.path.exists(state):
        return []
    with open(state) as f:
        return json.load(f)


def save(servers):
    with open(state, "w") as f:
        json.dump(servers, f)


if args == ["mcp", "list", "--json"]:
    print(json.dumps(load(), indent=2))
elif len(args) >= 5 and args[:2] == ["mcp", "add"] and args[3] == "--":
    name = args[2]
    entry = {"name": name, "enabled": True, "transport": {"type": "stdio", "command": args[4], "args": args[5:]}}
    save([s for s in load() if s["name"] != name] + [entry])
    print("Added global MCP server '%s'." % name)
elif len(args) == 3 and args[:2] == ["mcp", "remove"]:
    servers = load()
    kept = [s for s in servers if s["name"] != args[2]]
    if len(kept) == len(servers):
        print("No MCP server named '%s' found." % args[2])
    else:
        save(kept)
        print("Removed global MCP server '%s'." % args[2])
else:
    sys.stderr.write("fake codex: not implemented: %s\n" % " ".join(args))
    sys.exit(2)
"""
FAKE_CODEX = f"#!{sys.executable}\n{FAKE_CODEX_BODY}"

# Reports Node 22 for the version check; anything else runs the real node (see the module docstring).
FAKE_NODE = """#!/bin/sh
if [ "$1" = --version ]; then echo v22.0.0; exit 0; fi
exec %s "$@"
"""

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
        if REAL_NODE is None:
            testcase.skipTest("node is needed: the installer reads codex mcp list --json with it")
        self.root = Path(tempfile.mkdtemp(prefix="codex-test-"))
        testcase.addCleanup(shutil.rmtree, self.root, True)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.node = self.bin / "node"
        self.codex = self.bin / "codex"
        make_fake(self.node, FAKE_NODE % shlex.quote(REAL_NODE))
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

    def seed(self, *servers):
        """Replace the fake codex's MCP servers; each entry is (name, command, args)."""
        self.state.write_text(json.dumps([
            {"name": name, "enabled": True, "transport": {"type": "stdio", "command": command, "args": list(args)}}
            for name, command, args in servers
        ]))

    def servers(self):
        """Names of the MCP servers the fake codex holds, in order."""
        if not self.state.exists():
            return []
        return [entry["name"] for entry in json.loads(self.state.read_text())]
