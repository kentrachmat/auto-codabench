"""Shared CLI styling: the brand banner, a small ANSI palette, and
TTY/``NO_COLOR``-aware helpers for the human-facing CLI surfaces.

Dependency-free (stdlib only) and a leaf module — nothing in the package
imports *from* the CLI, so this stays importable without cycles. It follows the
project's hand-rolled-ANSI convention (see ``cli/progress.py`` and
``checks/render.py``) rather than pulling in ``rich``/``colorama``.

Everything degrades safely. Color is emitted only to an interactive TTY with
``NO_COLOR`` unset and ``TERM`` not ``dumb``; otherwise :func:`paint` returns
its text unchanged and :func:`banner` returns an empty string — so piped
output, JSON consumers, CI logs, and the replay/demo paths stay byte-clean.
"""
from __future__ import annotations

import os
import shutil
import sys

# --- palette (256-color; orange 208 is the autocodabench brand) ------------
ORANGE = "\033[38;5;208m"
WHITE = "\033[97m"
GREY = "\033[37m"
DIM = "\033[90m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
RED = "\033[31m"
BOLD = "\033[1m"
RESET = "\033[0m"

# Config-screen field alignment: "  label:        value".
LABEL_WIDTH = 15
VALUE_COL = 2 + LABEL_WIDTH + 1  # indent + padded label + one space

_WORDMARK = "AUTOCODABENCH"
_TAGLINE = "agentic authoring + pre-launch validation"


def _is_tty(stream=None) -> bool:
    """Whether *stream* (default stdout) is an interactive terminal."""
    stream = stream if stream is not None else sys.stdout
    try:
        return bool(stream.isatty())
    except Exception:
        return False


def color_enabled(stream=None) -> bool:
    """True when ANSI color is appropriate for *stream*: an interactive TTY,
    ``NO_COLOR`` unset, and not a ``dumb`` terminal."""
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("TERM") == "dumb":
        return False
    return _is_tty(stream)


def paint(text: str, *codes: str, stream=None) -> str:
    """Wrap *text* in ANSI *codes* when color is enabled for *stream*, else
    return it plain. Width-safe: the visible length is unchanged."""
    if not codes or not color_enabled(stream):
        return text
    return "".join(codes) + text + RESET


# --- composite helpers -----------------------------------------------------

def heading(text: str, *, stream=None) -> str:
    """A bold-orange section heading, e.g. ``▸ plan — configuration``."""
    return paint(f"▸ {text}", BOLD, ORANGE, stream=stream)


def field(label: str, value, *, width: int = LABEL_WIDTH, stream=None) -> str:
    """One aligned ``  label:   value`` config row (label dim, value plain)."""
    lbl = paint(f"{label + ':':<{width}}", DIM, stream=stream)
    return f"  {lbl} {value}"


def cont(text: str) -> str:
    """A continuation line aligned under the value column of :func:`field`."""
    return " " * VALUE_COL + text


def confirm(question: str, *, default_yes: bool = True, stream=None) -> str:
    """A ``[Y/n]`` prompt string for :func:`input` — bold-orange question, dim
    hint. Caller supplies any leading newline."""
    yn = "[Y/n]" if default_yes else "[y/N]"
    return f"{paint(question, BOLD, ORANGE, stream=stream)} {paint(yn, DIM, stream=stream)}: "


def info(message: str, *, stream=None) -> str:
    """Style an ``INFO: …`` diagnostic line by dimming the ``INFO:`` prefix."""
    if message.startswith("INFO:") and color_enabled(stream):
        return paint("INFO:", DIM, stream=stream) + message[len("INFO:"):]
    return message


def banner(*, stream=None) -> str:
    """The framed AUTOCODABENCH wordmark + tagline, sized to the terminal.

    Returns an empty string when *stream* (default stderr) is not a TTY, so
    machine-readable stdout is never touched. The box is drawn even under
    ``NO_COLOR`` (just without color); a very narrow terminal degrades to a
    single inline line.
    """
    stream = stream if stream is not None else sys.stderr
    if not _is_tty(stream):
        return ""

    cols = shutil.get_terminal_size((80, 24)).columns
    content_w = max(len(_WORDMARK), len(_TAGLINE))
    pad = 3
    if cols < content_w + pad + 3:  # no room for a frame — inline fallback
        return (paint(_WORDMARK, BOLD, ORANGE, stream=stream) + "  "
                + paint(_TAGLINE, DIM, stream=stream))

    inner = min(max(cols - 2, content_w + pad + 1), 62)
    border = paint("║", ORANGE, stream=stream)

    def line(text: str = "", *codes: str) -> str:
        if text and codes:
            body = (" " * pad + paint(text, *codes, stream=stream)
                    + " " * (inner - pad - len(text)))
        else:
            body = (" " * pad + text)
            body = body + " " * (inner - len(body))
        return border + body + border

    top = paint("╔" + "═" * inner + "╗", ORANGE, stream=stream)
    bot = paint("╚" + "═" * inner + "╝", ORANGE, stream=stream)
    return "\n".join([
        top,
        line(),
        line(_WORDMARK, BOLD, ORANGE),
        line(_TAGLINE, DIM),
        line(),
        bot,
    ])
