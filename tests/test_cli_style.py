"""Unit tests for the CLI styling leaf (keyless, fast, no SDK).

Color is exercised against fake streams so the result never depends on whether
the test runner itself has a TTY.
"""
from __future__ import annotations

import re

from autocodabench.cli import style

_ANSI_RE = re.compile(r"\033\[[0-9;]*m")


class _Stream:
    def __init__(self, tty: bool) -> None:
        self._tty = tty

    def isatty(self) -> bool:
        return self._tty


TTY = _Stream(True)
PIPE = _Stream(False)


def _enable_color(monkeypatch) -> None:
    """Make color eligible: a real-looking terminal, NO_COLOR unset."""
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setenv("TERM", "xterm-256color")


def _strip(s: str) -> str:
    return _ANSI_RE.sub("", s)


# --- paint / color gating --------------------------------------------------

def test_paint_plain_when_not_tty():
    assert style.paint("hi", style.ORANGE, stream=PIPE) == "hi"


def test_paint_wraps_on_tty(monkeypatch):
    _enable_color(monkeypatch)
    assert style.paint("hi", style.ORANGE, stream=TTY) == style.ORANGE + "hi" + style.RESET


def test_paint_without_codes_is_identity(monkeypatch):
    _enable_color(monkeypatch)
    assert style.paint("hi", stream=TTY) == "hi"


def test_no_color_env_disables(monkeypatch):
    monkeypatch.setenv("NO_COLOR", "1")
    assert style.paint("hi", style.ORANGE, stream=TTY) == "hi"


def test_term_dumb_disables(monkeypatch):
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setenv("TERM", "dumb")
    assert style.paint("hi", style.ORANGE, stream=TTY) == "hi"


# --- banner ----------------------------------------------------------------

def test_banner_empty_off_tty():
    assert style.banner(stream=PIPE) == ""


def test_banner_drawn_and_colored_on_tty(monkeypatch):
    _enable_color(monkeypatch)
    b = style.banner(stream=TTY)
    assert "AUTOCODABENCH" in b
    assert "╔" in b and "╚" in b          # framed (box drawing)
    assert style.ORANGE in b               # branded


def test_banner_rows_share_visible_width(monkeypatch):
    _enable_color(monkeypatch)
    rows = [_strip(ln) for ln in style.banner(stream=TTY).splitlines()]
    assert len({len(ln) for ln in rows}) == 1, rows   # perfectly aligned box


# --- field / cont / heading ------------------------------------------------

def test_field_value_column_is_constant_plain():
    a = style.field("idea", "X", stream=PIPE)
    b = style.field("output mode", "Y", stream=PIPE)
    assert a.index("X") == b.index("Y") == style.VALUE_COL


def test_cont_aligns_under_value_column():
    assert style.cont("next").index("next") == style.VALUE_COL


def test_heading_plain():
    assert style.heading("plan — configuration", stream=PIPE) == "▸ plan — configuration"


# --- confirm / info --------------------------------------------------------

def test_confirm_plain():
    assert style.confirm("Go?", stream=PIPE) == "Go? [Y/n]: "
    assert style.confirm("Go?", default_yes=False, stream=PIPE) == "Go? [y/N]: "


def test_info_dims_prefix_on_tty(monkeypatch):
    _enable_color(monkeypatch)
    out = style.info("INFO: Claude auth = subscription login", stream=TTY)
    assert out.startswith(style.DIM + "INFO:" + style.RESET)
    assert _strip(out) == "INFO: Claude auth = subscription login"


def test_info_plain_off_tty():
    assert style.info("INFO: x", stream=PIPE) == "INFO: x"


def test_info_passthrough_for_non_info_line(monkeypatch):
    _enable_color(monkeypatch)
    assert style.info("hello", stream=TTY) == "hello"
