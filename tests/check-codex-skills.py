#!/usr/bin/env python3
"""Check that a local `codex app-server` lists a skill named "worker" for a directory.

Starts `codex app-server` with CODEX_HOME set to a fresh temp dir (the real ~/.codex is
never read), sends initialize and skills/list over stdio, prints the non-system skills it
finds, and exits 0 if an enabled one is named "worker", 1 otherwise. Optionally
requires that skill's source path, so another installed copy cannot hide a broken
checkout in CI. Stdlib only; no model turns are started.
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
    """Return the JSON-RPC reply with this id; notifications and other output are skipped."""
    while True:
        try:
            raw = lines.get(timeout=max(deadline - time.monotonic(), 0))
        except queue.Empty:
            raise TimeoutError(f"no reply to request {req_id} within {TIMEOUT_S:.0f} s")
        if raw is None:
            raise RuntimeError(f"codex app-server exited before replying to request {req_id}")
        try:
            msg = json.loads(raw)
        except ValueError:
            continue
        if isinstance(msg, dict) and msg.get("id") == req_id:
            return msg


def stop(proc):
    """Kill the app-server and anything it started (its whole process group), then reap it."""
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except OSError:
        pass  # the group is already gone
    proc.wait()


def probe(cwd, home):
    """Run the app-server for cwd. Returns (skills, load_errors) from skills/list."""
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
        send({"id": 2, "method": "skills/list", "params": {"cwds": [cwd]}})
        reply = wait_for_reply(lines, 2, deadline)
        if "error" in reply:
            raise RuntimeError(f"skills/list failed: {reply['error']}")
        skills, errors = [], []
        for entry in (reply.get("result") or {}).get("data") or []:
            skills.extend(entry.get("skills") or [])
            errors.extend(entry.get("errors") or [])
        return skills, errors
    finally:
        if proc is not None:
            stop(proc)
        errlog.close()


def main():
    ap = argparse.ArgumentParser(description='Check that codex lists a skill named "worker".')
    ap.add_argument("--cwd", metavar="DIR",
                    help="directory to list skills for (default: a fresh temp dir)")
    ap.add_argument("--expected-path", metavar="SKILL_MD",
                    help="require the enabled worker skill to come from this file")
    args = ap.parse_args()

    made = []  # temp dirs this run creates; removed on exit. A --cwd directory is never removed.
    try:
        home = tempfile.mkdtemp(prefix="codex-probe-home-")
        made.append(home)
        if args.cwd:
            cwd = os.path.abspath(args.cwd)
            if not os.path.isdir(cwd):
                ap.error(f"not a directory: {args.cwd}")
        else:
            cwd = tempfile.mkdtemp(prefix="codex-probe-cwd-")
            made.append(cwd)

        try:
            skills, errors = probe(cwd, home)
        except (OSError, RuntimeError, TimeoutError) as exc:
            print(f"probe failed: {exc}", file=sys.stderr)
            try:
                with open(os.path.join(home, STDERR_NAME), "rb") as f:
                    tail = f.read().decode("utf-8", "replace").splitlines()[-20:]
                if tail:
                    print("app-server stderr (last lines):", file=sys.stderr)
                    for line in tail:
                        print("  " + line, file=sys.stderr)
            except OSError:
                pass
            return 1

        shown = [s for s in skills if s.get("scope") != "system"]
        for s in shown:
            print(f"{str(s.get('name')):<24} scope={str(s.get('scope')):<8} {s.get('path')}")
        if not shown:
            print("(no non-system skills listed)")
        for e in errors:
            print(f"load error: {json.dumps(e, ensure_ascii=False)}", file=sys.stderr)

        found = [s for s in shown if s.get("name") == "worker" and s.get("enabled") is not False]
        if args.expected_path:
            expected = os.path.realpath(args.expected_path)
            found = [s for s in found if s.get("path") and os.path.realpath(s["path"]) == expected]
        if errors:
            return 1
        if found:
            print(f"worker: found (scope={found[0].get('scope')})")
            return 0
        print("worker: NOT found (enabled, at expected path)" if args.expected_path
              else "worker: NOT found (enabled)")
        return 1
    finally:
        for d in made:
            shutil.rmtree(d, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
