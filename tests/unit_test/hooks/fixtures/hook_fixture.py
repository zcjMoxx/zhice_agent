"""Standalone Hook process fixture. It intentionally imports no agent modules."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

payload = json.load(sys.stdin)
name = payload["hook_name"]

if name == "continue":
    result = {"action": "continue"}
elif name == "block":
    result = {"action": "block", "code": "BUSINESS_BLOCKED", "message": "blocked by fixture"}
elif name == "modify":
    arguments = dict(payload["arguments"])
    arguments["path"] = "allowed.txt"
    result = {"action": "modify", "arguments": arguments}
elif name == "enrich":
    result = {
        "action": "enrich",
        "display": {"title": "fixture enriched", "icon": "search"},
        "ui_metadata": {"detail_type": "summary", "detail_data": {"items": ["ok"]}},
    }
elif name == "timeout":
    time.sleep(1)
    result = {"action": "continue"}
elif name == "spawn-timeout":
    creationflags = int(getattr(subprocess, "CREATE_NO_WINDOW", 0))
    child = subprocess.Popen(  # noqa: S603 - fixed interpreter and fixture code.
        [sys.executable, "-c", "import time; time.sleep(60)"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        shell=False,
        creationflags=creationflags,
    )
    pid_path = Path(os.environ["ZHICE_AGENT_WORKSPACE"]) / "hook-tree-pids.json"
    pid_path.write_text(
        json.dumps({"parent": os.getpid(), "child": child.pid}),
        encoding="utf-8",
    )
    time.sleep(60)
    result = {"action": "continue"}
elif name == "invalid-json":
    sys.stdout.write("not-json")
    raise SystemExit(0)
elif name == "invalid-fields":
    result = {"action": "continue", "unexpected": True}
elif name == "oversize":
    result = {"action": "continue", "padding": "x" * 5000}
elif name == "exception":
    raise RuntimeError("fixture failure")
else:
    raise RuntimeError(f"unknown fixture mode: {name}")

json.dump(result, sys.stdout, ensure_ascii=False)
