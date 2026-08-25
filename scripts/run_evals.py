#!/usr/bin/env python3
"""Run the bundled routing-level evaluation cases."""

import argparse
import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cases", default=str(ROOT / "evals" / "cases.json"))
    parser.add_argument("--models-file")
    args = parser.parse_args()
    cases = json.loads(Path(args.cases).read_text(encoding="utf-8"))
    failures = []
    for case in cases:
        command = [sys.executable, str(ROOT / "scripts" / "autoroute.py"), "--json"]
        if args.models_file:
            command.extend(["--models-file", args.models_file])
        command.append(case["prompt"])
        result = subprocess.run(command, check=True, capture_output=True, text=True)
        routed = json.loads(result.stdout)
        ok = routed["level"] == case["expected_level"]
        print(f"{'PASS' if ok else 'FAIL'} {case['id']}: {routed['level']} (score {routed['score']})")
        if not ok:
            failures.append((case["id"], case["expected_level"], routed["level"]))
    if failures:
        print(json.dumps(failures), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
