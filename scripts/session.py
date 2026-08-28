"""Session switching boundary for the current Codex process."""
from __future__ import annotations

try:
    from .session_controller import switch_model
except ImportError:  # pragma: no cover
    from session_controller import switch_model

__all__ = ["switch_model"]
