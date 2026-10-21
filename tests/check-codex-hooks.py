#!/usr/bin/env python3
"""Check that a local `codex app-server` lists a hook whose text contains --expect.

Starts `codex app-server` with CODEX_HOME set to a fresh temp dir (the real ~/.codex is
never read), sends initialize, initialized and hooks/list for one directory over stdio,
prints one line per hook it finds, and exits 0 only if some string in the result contains
the --expect text. The result is searched recursively without assuming its schema beyond
JSON-RPC: a string inside an entry marked enabled=false or trustStatus=untrusted does not
count, text under a key named like a warning is not a match, and any non-empty error the
result reports fails the check. Stdlib only; no model turns are started.
"""
import argparse
import json
import os
import queue
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time

TIMEOUT_S = 30.0
STDERR_NAME = "app-server.stderr"


def pump(stream, lines):
    """Copy the child's stdout lines into a queue; None marks the end of the stream."""
    for raw in iter(stream.readline, b""):
        lines.put(raw)
    lines.put(None)


def wait_for_reply(lines, req_id, deadline):
    """Return the JSON-RPC response with this id; notifications and other output are skipped."""
    while True:
        try:
            raw = lines.get(timeout=max(deadline - time.monotonic(), 0))
        except queue.Empty:
            raise TimeoutError(f"no reply to request {req_id} within {TIMEOUT_S:.0f} s") from None
        if raw is None:
            raise RuntimeError(f"codex app-server exited before replying to request {req_id}")
        try:
            msg = json.loads(raw)
        except ValueError:
            continue
        if isinstance(msg, dict) and msg.get("id") == req_id and "method" not in msg:
            return msg


def stop(proc):
    """Kill the app-server and anything it started (its whole process group), then reap it."""
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except OSError:
        pass  # the group is already gone
    proc.wait()


def probe(cwd, home):
    """Run the app-server for cwd. Returns the result object of hooks/list."""
    errlog = open(os.path.join(home, STDERR_NAME), "wb")
    proc = None
    try:
        proc = subprocess.Popen(
            ["codex", "app-server"],
            cwd=cwd,
            env=dict(os.environ, CODEX_HOME=home),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=errlog,
            start_new_session=True,  # own process group, so stop() reaches helper processes too
        )
        lines = queue.Queue()
        threading.Thread(target=pump, args=(proc.stdout, lines), daemon=True).start()

        def send(obj):
            proc.stdin.write((json.dumps(obj) + "\n").encode("utf-8"))
            proc.stdin.flush()

        deadline = time.monotonic() + TIMEOUT_S
        send({"id": 1, "method": "initialize",
              "params": {"clientInfo": {"name": "probe", "version": "0"}}})
        reply = wait_for_reply(lines, 1, deadline)
        if "error" in reply:
            raise RuntimeError(f"initialize failed: {reply['error']}")
        send({"method": "initialized", "params": {}})
        send({"id": 2, "method": "hooks/list", "params": {"cwds": [cwd]}})
        reply = wait_for_reply(lines, 2, deadline)
        if "error" in reply:
            raise RuntimeError(f"hooks/list failed: {reply['error']}")
        return reply.get("result") or {}
    finally:
        if proc is not None:
            stop(proc)
        errlog.close()


def scan(node, expect, hits, problems, notes, disabled=False):
    """Collect strings containing expect, plus non-empty error and warning values, from a JSON tree.

    An entry that codex reports as disabled or untrusted is listed but never runs, so its
    strings do not count. Absent fields change nothing.
    """
    if isinstance(node, dict):
        disabled = (disabled or node.get("enabled") is False
                    or node.get("trustStatus") == "untrusted")
        for key, value in node.items():
            name = str(key).lower()
            if "error" in name or "warning" in name:
                if value not in (None, False, "", [], {}):
                    (problems if "error" in name else notes).append(
                        json.dumps(value, ensure_ascii=False))
            else:
                scan(value, expect, hits, problems, notes, disabled)
    elif isinstance(node, list):
        for item in node:
            scan(item, expect, hits, problems, notes, disabled)
    elif isinstance(node, str) and not disabled and expect in node:
        hits.append(node)


def field(entry, key):
    value = entry.get(key)
    return "-" if value is None else str(value)


def hook_lines(node, event="?"):
    """One line per hook entry (a dict with a string "command").

    The event comes from the nearest entry that names it.
    """
    lines = []
    if isinstance(node, dict):
        event = str(node.get("eventName") or node.get("event") or event)
        if isinstance(node.get("command"), str):
            lines.append(
                f"{event} matcher={field(node, 'matcher')} source={field(node, 'source')} "
                f"enabled={field(node, 'enabled')} trust={field(node, 'trustStatus')} "
                f"command={node['command']}")
        for value in node.values():
            lines.extend(hook_lines(value, event))
    elif isinstance(node, list):
        for item in node:
            lines.extend(hook_lines(item, event))
    return lines


def report_probe_failure(exc, home):
    print(f"probe failed: {exc}", file=sys.stderr)
    try:
        with open(os.path.join(home, STDERR_NAME), "rb") as f:
            tail = f.read().decode("utf-8", "replace").splitlines()[-20:]
    except OSError:
        tail = []
    if tail:
        print("app-server stderr (last lines):", file=sys.stderr)
        for line in tail:
            print("  " + line, file=sys.stderr)


def main():
    ap = argparse.ArgumentParser(description="Check that codex lists a hook whose text contains --expect.")
    ap.add_argument("--cwd", metavar="DIR",
                    help="directory to list hooks for (default: a fresh temp dir)")
    ap.add_argument("--expect", metavar="TEXT", required=True,
                    help="text that must appear in a listed hook, e.g. the absolute path of worker-hook.mjs")
    args = ap.parse_args()
    if not args.expect.strip():
        ap.error("--expect must not be empty")

    made = []  # temp dirs this run creates; removed on exit. A --cwd directory is never removed.
    try:
        home = tempfile.mkdtemp(prefix="codex-hooks-home-")
        made.append(home)
        if args.cwd:
            cwd = os.path.abspath(args.cwd)
            if not os.path.isdir(cwd):
                ap.error(f"not a directory: {args.cwd}")
        else:
            cwd = tempfile.mkdtemp(prefix="codex-hooks-cwd-")
            made.append(cwd)

        try:
            result = probe(cwd, home)
        except (OSError, RuntimeError, TimeoutError) as exc:
            report_probe_failure(exc, home)
            return 1

        lines = hook_lines(result)
        for line in lines:
            print(f"hook: {line}")
        if not lines:
            print("(no hooks listed)")

        hits, problems, notes = [], [], []
        scan(result, args.expect, hits, problems, notes)
        for note in notes:
            print(f"warning: {note}", file=sys.stderr)
        for problem in problems:
            print(f"error: {problem}", file=sys.stderr)
        if problems:
            return 1
        if hits:
            print(f"found: {len(hits)} string(s) contain the expected text")
            return 0
        print(f"NOT found: no enabled hook contains {args.expect!r}", file=sys.stderr)
        return 1
    finally:
        for d in made:
            shutil.rmtree(d, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
