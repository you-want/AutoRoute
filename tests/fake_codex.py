#!/usr/bin/env python3
"""Tiny fake codex executable used only by offline availability tests."""

import json
import sys


model = next((sys.argv[index + 1] for index, value in enumerate(sys.argv[:-1]) if value == "-m"), "unknown")
if "exec" not in sys.argv:
    if model.endswith("-bad"):
        raise SystemExit(1)
    print("OK")
    raise SystemExit(0)
if model.endswith("-bad"):
    print(json.dumps({"type": "error", "message": "unavailable"}))
    raise SystemExit(1)
print(json.dumps({"type": "turn.started"}))
print(json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": "OK"}}))
print(json.dumps({"type": "turn.completed", "usage": {"input_tokens": 1, "output_tokens": 1}}))
