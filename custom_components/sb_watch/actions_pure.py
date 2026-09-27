"""format_message, kept HA-free so the tests can import it (actions.py imports HA)."""

from __future__ import annotations

MAX_NAMES = 8


def format_message(names: list[str], count: int, pending_act: str | None = None, warn_secs: float | None = None) -> str:
    """One line a phone can show: the first names, how many more, and what will happen."""
    shown = ", ".join(names[:MAX_NAMES])
    more = f" and {count - MAX_NAMES} more" if count > MAX_NAMES else ""
    msg = f"{count} active: {shown}{more}" if count else "Clear"
    if pending_act and warn_secs:
        mins = int(round(warn_secs / 60))
        when = f"{mins} min" if mins >= 1 else f"{int(warn_secs)} s"
        msg += f" — {pending_act.replace('_', ' ')} in {when} unless cleared"
    return msg
