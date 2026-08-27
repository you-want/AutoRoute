#!/usr/bin/env python3
"""Codex session controller — switch model in a running session."""

from __future__ import annotations

import subprocess
import sys
from typing import Optional


def _find_codex_tty() -> Optional[str]:
    """Return the TTY device path of the most recently active Codex session.

    We look for the actual ``codex`` binary (not the ``node`` wrapper or the
    ``code-mode-host`` helper) that has a real terminal attached. Among
    candidates we pick the one with the highest PID, which is usually the
    session the user is currently interacting with.
    """
    try:
        result = subprocess.run(
            ["ps", "-eo", "pid,tty,args"],
            capture_output=True, text=True, timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None

    candidates: list[tuple[int, str]] = []
    for line in result.stdout.splitlines():
        parts = line.split(None, 2)
        if len(parts) < 3:
            continue
        pid_str, tty, args = parts
        if tty in ("?", "-", "??", ""):
            continue
        if not pid_str.isdigit():
            continue
        args_lower = args.lower()
        # Skip the `node` wrapper and the code-mode-host helper.
        if args_lower.startswith("node ") or "code-mode-host" in args_lower:
            continue
        if "/codex" not in args_lower:
            continue
        dev = f"/dev/{tty}" if not tty.startswith("/dev/") else tty
        candidates.append((int(pid_str), dev))

    if not candidates:
        return None
    candidates.sort(reverse=True)
    return candidates[0][1]


def _send_to_tty(tty: str, text: str) -> bool:
    """Write *text* followed by a newline to a TTY device."""
    try:
        with open(tty, "w") as f:
            f.write(text)
            f.flush()
        return True
    except OSError as exc:
        print(f"[AutoRoute] Could not write to {tty}: {exc}", file=sys.stderr)
        return False


def switch_model(
    model: str,
    effort: Optional[str] = None,
    tty_path: Optional[str] = None,
) -> bool:
    """Send ``/model`` (and optionally ``/effort``) to the running Codex TTY.

    Returns ``True`` on success, ``False`` otherwise.
    """
    target_tty = tty_path or _find_codex_tty()
    if not target_tty:
        print(
            "[AutoRoute] No running Codex session found; cannot auto-switch.",
            file=sys.stderr,
        )
        return False

    commands = f"/model {model}\n"
    if effort:
        commands += f"/effort {effort}\n"

    print(f"[AutoRoute] Sending model switch to {target_tty} …", file=sys.stderr)
    if _send_to_tty(target_tty, commands):
        print(
            f"[AutoRoute] ✅ Model switch queued: {model}"
            + (f" / effort={effort}" if effort else ""),
            file=sys.stderr,
        )
        return True
    return False


def quick_test() -> int:
    """Print diagnostics about the current Codex session for testing."""
    tty = _find_codex_tty()
    print(f"Detected Codex TTY: {tty or 'none'}", file=sys.stderr)
    if not tty:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(quick_test())
