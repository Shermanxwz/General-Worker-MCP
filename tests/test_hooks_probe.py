"""Test the hooks probe's result handling with the app-server exchange faked (no codex run).

The fake stands in for the subprocess handle: it replays canned JSON-RPC replies, so the
probe's own request/reply code and main() run for real.
"""

import contextlib
import importlib.util
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SPEC = importlib.util.spec_from_file_location(
    "codex_hooks_probe", Path(__file__).with_name("check-codex-hooks.py")
)
PROBE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PROBE)

WORKER_HOOK = "/opt/general-worker-mcp/runtime/worker-hook.mjs"
INITIALIZE_OK = {"id": 1, "result": {"userAgent": "fake"}}


def hook(command, **fields):
    entry = {"eventName": "userPromptSubmit", "handlerType": "command", "command": command,
             "source": "managed", "enabled": True, "trustStatus": "managed"}
    entry.update(fields)
    return entry


def hooks_reply(*entries, errors=(), warnings=()):
    return {"id": 2, "result": {"data": [{
        "cwd": "/project", "hooks": list(entries),
        "warnings": list(warnings), "errors": list(errors)}]}}


class FakeAppServer:
    """Stands in for the Popen handle: replays canned lines and keeps what the probe wrote."""

    def __init__(self, replies):
        self.pid = 4242  # never signalled: os.killpg is mocked while a probe runs
        self.stdin = io.BytesIO()
        self.stdout = io.BytesIO(b"".join(json.dumps(r).encode("utf-8") + b"\n" for r in replies))

    def wait(self):
        return 0

    def sent(self):
        return [json.loads(line) for line in self.stdin.getvalue().splitlines()]


class HooksProbeTests(unittest.TestCase):
    def run_probe(self, replies, args=("--expect", WORKER_HOOK)):
        server = FakeAppServer(replies)
        with mock.patch.object(PROBE.subprocess, "Popen", return_value=server), \
                mock.patch.object(PROBE.os, "killpg"), \
                mock.patch("sys.argv", ["check-codex-hooks.py", *args]), \
                contextlib.redirect_stdout(io.StringIO()) as out, \
                contextlib.redirect_stderr(io.StringIO()) as err:
            try:
                code = PROBE.main()
            except SystemExit as exc:  # argparse usage errors
                code = exc.code
        return code, out.getvalue(), err.getvalue(), server

    def test_listed_hook_with_expected_command_passes(self):
        code, out, err, _ = self.run_probe([
            INITIALIZE_OK,
            hooks_reply(hook(f"/usr/bin/node {WORKER_HOOK} --runtime codex")),
        ])
        self.assertEqual(code, 0, err)
        self.assertIn("found:", out)
        self.assertIn(f"command=/usr/bin/node {WORKER_HOOK} --runtime codex", out)

    def test_hook_for_another_script_fails(self):
        code, _, _, _ = self.run_probe([
            INITIALIZE_OK,
            hooks_reply(hook("/usr/bin/node /opt/other/runtime/worker-hook.mjs")),
        ])
        self.assertEqual(code, 1)

    def test_disabled_or_untrusted_hook_does_not_pass(self):
        for fields in ({"enabled": False}, {"trustStatus": "untrusted"}):
            with self.subTest(fields=fields):
                code, _, _, _ = self.run_probe([
                    INITIALIZE_OK,
                    hooks_reply(hook(f"/usr/bin/node {WORKER_HOOK}", **fields)),
                ])
                self.assertEqual(code, 1)

    def test_error_entry_fails_even_if_hook_matches(self):
        code, _, err, _ = self.run_probe([
            INITIALIZE_OK,
            hooks_reply(hook(f"/usr/bin/node {WORKER_HOOK}"),
                        errors=[{"path": "/project", "message": f"cannot load {WORKER_HOOK}"}]),
        ])
        self.assertEqual(code, 1)
        self.assertIn("error:", err)

    def test_warning_text_alone_does_not_pass(self):
        code, _, _, _ = self.run_probe([
            INITIALIZE_OK,
            hooks_reply(warnings=[f"{WORKER_HOOK} is not trusted"]),
        ])
        self.assertEqual(code, 1)

    def test_json_rpc_error_on_hooks_list_fails(self):
        code, _, err, _ = self.run_probe([
            INITIALIZE_OK,
            {"id": 2, "error": {"code": -32600, "message": "unknown method hooks/list"}},
        ])
        self.assertEqual(code, 1)
        self.assertIn("hooks/list failed", err)

    def test_json_rpc_error_on_initialize_fails(self):
        code, _, err, _ = self.run_probe([
            {"id": 1, "error": {"code": -32600, "message": "bad initialize"}},
        ])
        self.assertEqual(code, 1)
        self.assertIn("initialize failed", err)

    def test_app_server_exit_before_reply_fails(self):
        code, _, err, _ = self.run_probe([INITIALIZE_OK])  # stdout ends before hooks/list
        self.assertEqual(code, 1)
        self.assertIn("exited before replying", err)

    def test_request_names_the_given_directory(self):
        with tempfile.TemporaryDirectory() as cwd:
            code, _, err, server = self.run_probe(
                [INITIALIZE_OK, hooks_reply(hook(WORKER_HOOK))],
                args=("--cwd", cwd, "--expect", WORKER_HOOK))
        self.assertEqual(code, 0, err)
        methods = [msg.get("method") for msg in server.sent()]
        self.assertEqual(methods, ["initialize", "initialized", "hooks/list"])
        self.assertEqual(server.sent()[-1]["params"], {"cwds": [os.path.abspath(cwd)]})

    def test_empty_expect_is_a_usage_error(self):
        code, _, _, _ = self.run_probe([INITIALIZE_OK], args=("--expect", "  "))
        self.assertEqual(code, 2)


if __name__ == "__main__":
    unittest.main()
