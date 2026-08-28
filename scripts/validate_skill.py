#!/usr/bin/env python3
"""Small, dependency-free validation for the repository's Codex skill."""
from __future__ import annotations

import sys
from pathlib import Path


def main() -> int:
    root = Path(sys.argv[1] if len(sys.argv) > 1 else ".").resolve()
    skill = root / "SKILL.md"
    if not skill.is_file():
        print("SKILL.md is missing", file=sys.stderr)
        return 1
    text = skill.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        print("SKILL.md must begin with YAML front matter", file=sys.stderr)
        return 1
    front_matter, separator, body = text[4:].partition("\n---\n")
    required = ("name:", "description:")
    if not separator or not all(key in front_matter for key in required):
        print("SKILL.md front matter requires name and description", file=sys.stderr)
        return 1
    if not body.lstrip().startswith("# "):
        print("SKILL.md requires a top-level title", file=sys.stderr)
        return 1
    print(f"skill validation passed: {skill}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
