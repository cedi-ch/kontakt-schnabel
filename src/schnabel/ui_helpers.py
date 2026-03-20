"""Shared UI helpers for all TUI modules."""

import readchar
from rich.text import Text


def truncate(s: str, maxlen: int = 30) -> str:
    """Truncate a string to maxlen chars, adding ellipsis if needed."""
    if len(s) <= maxlen:
        return s
    return s[:maxlen - 1] + "\u2026"


def confidence_bar(confidence: float, width: int = 25) -> Text:
    """Render a confidence bar as a Rich Text object."""
    filled = int(confidence * width)
    empty = width - filled
    pct = f"{confidence * 100:.0f}%"

    if confidence >= 0.9:
        color = "green"
    elif confidence >= 0.7:
        color = "yellow"
    else:
        color = "red"

    bar = Text()
    bar.append("\u2588" * filled, style=color)
    bar.append("\u2591" * empty, style="dim")
    bar.append(f" {pct}", style=f"bold {color}")
    return bar


def safe_readchar() -> str | None:
    """Read a single character, handling errors gracefully.

    Returns the character, or None if reading failed (EOF, broken pipe, etc.).
    """
    try:
        return readchar.readchar()
    except (EOFError, KeyboardInterrupt):
        return None
    except Exception:
        return None
