"""Rendering for the validation report and the check catalog.

Two surfaces share this module so the CLI and the web UI stay consistent:

- **Markdown** (``render_*_markdown``) — what the web UI renders and what the
  saved ``validation_report.md`` contains. Tables are split by validation
  *type* (1. Structural … 6. Governance), and citations are clickable links.
- **Terminal** (``render_*_terminal``) — the same content drawn as aligned,
  emoji-aware box tables for the CLI, with a clickable Sources footer (OSC-8).

Checks are presented user-first: the internal check id is never shown; an
``LLM-as-a-judge`` (Yes/No) column replaces the internal tier name.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import textwrap
import unicodedata
from itertools import groupby
from pathlib import Path
from typing import Any

from .base import REGISTRY, Status, tier_is_llm_judged
from .report import ValidationReport, _detail_lines, checklist_coverage, citation_url

# Check status (Status enum value) → emoji.
STATUS_EMOJI = {
    "pass": "✅", "fail": "❌", "finding": "❓",
    "attestation_required": "📋", "skipped": "⏭",
}
# Phase-1 design-assessment status → emoji.
ASSESS_EMOJI = {"ok": "✅", "warn": "❓", "missing": "❌"}

_BOOK_LABEL = "AI Competitions & Benchmarks (Pavão et al.)"
_DOCS_LABEL = "Codabench documentation"

# Terminal color for the report headings/verdict. Self-contained so this
# presentation layer keeps no CLI dependency; honors NO_COLOR / TERM=dumb /
# non-TTY exactly like ``cli/style.py``. Colors only outside-the-table text so
# box-table alignment is never affected.
_ANSI = {"orange": "\033[38;5;208m", "green": "\033[32m", "red": "\033[31m",
         "bold": "\033[1m", "reset": "\033[0m"}


def _color_on() -> bool:
    if os.environ.get("NO_COLOR") or os.environ.get("TERM") == "dumb":
        return False
    try:
        return bool(sys.stdout.isatty())
    except Exception:
        return False


def _paint(text: str, *names: str) -> str:
    if not names or not _color_on():
        return text
    return "".join(_ANSI[n] for n in names) + text + _ANSI["reset"]


def _yesno(flag: bool) -> str:
    return "Yes" if flag else "No"


def _cell(text: Any) -> str:
    """Sanitise a value for a markdown table cell."""
    return str(text if text is not None else "").replace("|", "\\|").replace("\n", " ").strip()


def _md_link(text: str, url: str | None) -> str:
    """A markdown hyperlink, or plain text when there is no URL."""
    text = _cell(text)
    return f"[{text}]({url})" if url else text


# ---------------------------------------------------------------------------
# Phase-1 design assessment loader (unchanged)
# ---------------------------------------------------------------------------

def load_design_assessment(source: str | Path | None) -> dict | None:
    """Load + validate a ``design_assessment.json`` from a file or a directory.

    Returns ``None`` if absent or malformed — callers degrade gracefully.
    """
    if source is None:
        return None
    p = Path(source)
    candidates = [p] if p.is_file() else [
        p / "design_assessment.json",
        p / "specs" / "design_assessment.json",
    ]
    for c in candidates:
        if not c.is_file():
            continue
        try:
            data = json.loads(c.read_text(encoding="utf-8"))
            if int(data.get("schema_version", 0)) != 1:
                return None
            secs = data.get("sections")
            if not isinstance(secs, list) or not secs:
                return None
            if not all(isinstance(s, dict) and s.get("name") and s.get("status")
                       for s in secs):
                return None
            return data
        except Exception:
            return None
    return None


# ===========================================================================
# Terminal box-table rendering (self-contained; emoji-aware; stdlib only)
# ===========================================================================

def _term_width(default: int = 100) -> int:
    try:
        cols = shutil.get_terminal_size((default, 24)).columns
    except Exception:
        cols = default
    return max(70, min(cols, 140))


def _disp_width(s: str) -> int:
    """Terminal display width, counting emoji (✅ ⚠️ ❌ 📋) as 2 cells."""
    width = 0
    chars = list(s)
    for i, ch in enumerate(chars):
        if ch == "️" or unicodedata.combining(ch):
            continue  # variation selector / combining mark: zero width
        nxt = chars[i + 1] if i + 1 < len(chars) else ""
        if nxt == "️" or unicodedata.east_asian_width(ch) in ("W", "F"):
            width += 2
        else:
            width += 1
    return width


def _pad(s: str, width: int) -> str:
    return s + " " * max(0, width - _disp_width(s))


def _wrap_cell(text: str, width: int) -> list[str]:
    out: list[str] = []
    for para in (text or "").split("\n"):
        out.extend(textwrap.wrap(para, width=max(4, width)) or [""])
    return out or [""]


def _fit_widths(natural: list[int], avail: int, min_w: int = 6) -> list[int]:
    widths = [max(1, w) for w in natural]
    while sum(widths) > avail and any(w > min_w for w in widths):
        idx = max(range(len(widths)), key=lambda k: widths[k])
        if widths[idx] <= min_w:
            break
        widths[idx] -= 1
    return widths


def _box_table(header: list[str], rows: list[list[str]], max_width: int) -> list[str]:
    """An aligned, box-bordered table with per-cell wrapping (no colour)."""
    all_rows = [header] + rows
    ncols = max(len(r) for r in all_rows)
    norm = [[(r[c] if c < len(r) else "") for c in range(ncols)] for r in all_rows]
    natural = [max((_disp_width(norm[r][c]) for r in range(len(norm))), default=1)
               for c in range(ncols)]
    overhead = 3 * ncols + 1
    avail = max(ncols * 6, max_width - overhead)
    widths = _fit_widths(natural, avail)

    def rule(left: str, mid: str, right: str) -> str:
        return left + mid.join("─" * (w + 2) for w in widths) + right

    def row_lines(cells: list[str]) -> list[str]:
        wrapped = [_wrap_cell(cells[c], widths[c]) for c in range(ncols)]
        height = max(len(w) for w in wrapped)
        out = []
        for li in range(height):
            parts = [" " + _pad(wrapped[c][li] if li < len(wrapped[c]) else "", widths[c]) + " "
                     for c in range(ncols)]
            out.append("│" + "│".join(parts) + "│")
        return out

    lines = [rule("┌", "┬", "┐")]
    lines += row_lines(norm[0])
    lines.append(rule("├", "┼", "┤"))
    for r in norm[1:]:
        lines += row_lines(r)
    lines.append(rule("└", "┴", "┘"))
    return lines


def _osc8(text: str, url: str) -> str:
    """A clickable terminal hyperlink (OSC-8). Supported by modern terminals;
    degrades to the visible text elsewhere."""
    esc = "\033"
    return f"{esc}]8;;{url}{esc}\\{text}{esc}]8;;{esc}\\"


def _sources_footer(citations: set[tuple[str, str]]) -> list[str]:
    """A 'Sources' line with the URLs that appeared — clickable (OSC-8) on a
    real terminal, plain ``label (url)`` when output is piped/redirected."""
    seen: dict[str, str] = {}
    for label, url in citations:
        if url:
            seen.setdefault(url, label)
    if not seen:
        return []
    tty = sys.stdout.isatty()
    parts = [_osc8(label, url) if tty else f"{label} ({url})"
             for url, label in seen.items()]
    return ["", "Sources: " + "   ·   ".join(parts)]


def _source_label(url: str | None) -> str | None:
    if not url:
        return None
    return _BOOK_LABEL if "ai-competitions-book" in url else _DOCS_LABEL


# ===========================================================================
# Check catalog  (`autocodabench checks`)
# ===========================================================================

def _catalog_groups():
    """checklist_coverage() rows grouped by type, in type order."""
    rows = checklist_coverage()
    rows.sort(key=lambda r: (r["type_no"], not r["llm_judged"], r["title"]))
    for _, grp in groupby(rows, key=lambda r: r["type"]):
        items = list(grp)
        yield items[0]["type"], items


def render_checks_catalog_markdown() -> str:
    """The `autocodabench checks` catalog: one table per validation type.

    Columns: Check name · LLM-as-a-judge · Description (with clickable citation)
    · How it was checked.
    """
    out = ["# Validation checks", "",
           "Every check `autocodabench validate` can perform, grouped by type. "
           "The **LLM-as-a-judge** column says whether an LLM is involved "
           "(deterministic checks are pure code).", ""]
    for type_label, items in _catalog_groups():
        out.append(f"## {type_label}")
        out.append("")
        out.append("| Check name | LLM-as-a-judge | Description | How it was checked |")
        out.append("|---|:--:|---|---|")
        for r in items:
            desc = _cell(r["description"])
            if r["citation"]:
                desc += " — " + _md_link(r["citation"], r["citation_url"])
            out.append(f"| {_cell(r['title'])} | {_yesno(r['llm_judged'])} "
                       f"| {desc} | {_cell(r['how'])} |")
        out.append("")
    return "\n".join(out)


def render_checks_catalog_terminal() -> str:
    """Terminal rendering of the catalog: a box table per type + Sources."""
    width = _term_width()
    out: list[str] = ["Validation checks — what `autocodabench validate` performs", ""]
    cites: set[tuple[str, str]] = set()
    for type_label, items in _catalog_groups():
        out.append(f"[{type_label}]")
        header = ["Check name", "LLM?", "Description", "How it was checked"]
        rows = []
        for r in items:
            desc = r["description"]
            if r["citation"]:
                desc += f" ({r['citation']})"
                lbl = _source_label(r["citation_url"])
                if lbl:
                    cites.add((lbl, r["citation_url"]))
            rows.append([r["title"], _yesno(r["llm_judged"]), desc, r["how"]])
        out += _box_table(header, rows, width)
        out.append("")
    out += _sources_footer(cites)
    return "\n".join(out)


# ===========================================================================
# Validation report  (`autocodabench validate`)
# ===========================================================================

def render_design_table(assessment: dict) -> str:
    """Table A — the Phase-1 7-section design scorecard (markdown)."""
    rows = "\n".join(
        f"| {_cell(s.get('name'))} "
        f"| {ASSESS_EMOJI.get(str(s.get('status')).lower(), '•')} "
        f"| {_cell(s.get('note'))} |"
        for s in assessment.get("sections", [])
    )
    return ("## 📐 Design assessment (Phase 1)\n\n"
            "| Design section | Status | Note |\n|---|:--:|---|\n" + rows)


def render_execution_section(report: ValidationReport) -> str | None:
    """The execution-evidence section (what ran, on what, how long), or None."""
    ex = report.execution_results
    if not ex:
        return None
    lines = [
        "## ▶ Execution",
        "_The bundle was run, not just inspected — real ingestion+scoring or "
        "notebook runs in the declared Docker image._",
        "",
    ]
    for r in ex:
        mark = STATUS_EMOJI.get(r.status.value, "·")
        lines.append(f"- {mark} **{_check_title(r.check_id)}** {_cell(r.message)}")
        for sub in _detail_lines(r.details):
            lines.append(f"    - {sub}")
    return "\n".join(lines)


def render_judged_section(report: ValidationReport) -> str:
    """Render only the LLM-judged advisory findings of a report (markdown)."""
    findings = report.by_status(Status.FINDING)
    if not findings:
        return ("## ✨ LLM-judged findings\n\n"
                "✅ The judge found no contradictions between the pages and "
                "`competition.yaml`.")
    rows = "\n".join(
        f"| ⚠️ | {_cell(_check_title(f.check_id))} | {_cell(f.where)} | {_cell(f.message)} |"
        for f in findings
    )
    return ("## ✨ LLM-judged findings (advisory)\n\n"
            "| | Check | Where | Finding |\n|:--:|---|---|---|\n" + rows)


def _check_title(check_id: str) -> str:
    c = REGISTRY.get(check_id)
    return c.title if c else check_id


def _report_groups(report: ValidationReport):
    """Report results grouped by validation type, in type order. Each yielded
    item is (type_label, [(result, title, llm_judged), …])."""
    enriched = []
    for r in report.results:
        c = REGISTRY.get(r.check_id)
        type_no = c.dimension.number if c else 99
        type_label = c.dimension.label if c else "Other"
        title = c.title if c else r.check_id
        llm = tier_is_llm_judged(c.tier) if c else False
        enriched.append((type_no, type_label, r, title, llm))
    enriched.sort(key=lambda e: (e[0], e[3]))
    for _, grp in groupby(enriched, key=lambda e: e[0]):
        items = list(grp)
        yield items[0][1], [(e[2], e[3], e[4]) for e in items]


def _detail_text(r) -> str:
    """A check result's detail: where (if any) + message + citation text."""
    bits = []
    if r.where:
        bits.append(f"[{r.where}]")
    if r.message:
        bits.append(r.message)
    detail = " ".join(bits)
    if r.citation:
        detail += f" — {r.citation}"
    return detail


def render_report_markdown(report: ValidationReport, *,
                           design_assessment: dict | None = None) -> str:
    """The full report: verdict, gate failures, design scorecard (if any),
    execution evidence, then the per-type Checks tables. Shared by CLI + web."""
    verdict = "✅ PASS" if report.ok else "❌ FAIL"
    parts = [
        f"# Bundle validation — {verdict}",
        "",
        f"Bundle: `{report.bundle_dir}`",
        "Results: " + ", ".join(f"{v} {k} {STATUS_EMOJI[k]}" for k, v in sorted(report.counts.items())),
    ]
    fails = report.by_status(Status.FAIL)
    if fails:
        parts.append("")
        parts.append("**Gate failures (fix before upload):**")
        parts += [f"- ❌ **{_cell(_check_title(r.check_id))}** {_cell(r.message)}"
                  for r in fails]
    if design_assessment:
        parts += ["", render_design_table(design_assessment)]
    ex = render_execution_section(report)
    if ex:
        parts += ["", ex]

    parts += ["", "## 🔎 Checks"]
    for type_label, items in _report_groups(report):
        parts += ["", f"### {type_label}", "",
                  "| Status | Short Description | LLM-as-a-judge | Detail |",
                  "|:--:|---|:--:|---|"]
        for r, title, llm in items:
            detail = _cell(r.message)
            if r.where:
                detail = f"`{_cell(r.where)}` · " + detail
            if r.citation:
                detail += " — " + _md_link(r.citation, citation_url(r.citation))
            parts.append(f"| {STATUS_EMOJI.get(r.status.value, '•')} | {_cell(title)} "
                         f"| {_yesno(llm)} | {detail} |")
    parts += ["", "_Run `autocodabench checks` to see every check type performed "
              "and how each is verified._"]
    return "\n".join(parts)


def render_report_terminal(report: ValidationReport, *,
                           design_assessment: dict | None = None) -> str:
    """Terminal rendering: verdict + gate failures + a box table per type."""
    width = _term_width()
    verdict = (_paint("✅ PASS", "green", "bold") if report.ok
               else _paint("❌ FAIL", "red", "bold"))
    out = [
        f"Bundle validation — {verdict}",
        f"Bundle: {report.bundle_dir}",
        "Results: " + ", ".join(f"{v} {k} {STATUS_EMOJI[k]}" for k, v in sorted(report.counts.items())),
        "",
    ]
    fails = report.by_status(Status.FAIL)
    if fails:
        out.append(_paint("Gate failures (fix before upload):", "red", "bold"))
        for r in fails:
            out.append(f"  ❌ {_check_title(r.check_id)} — {' '.join((r.message or '').split())}")
        out.append("")

    if design_assessment:
        out.append(_paint("[Design assessment (Phase 1)]", "orange", "bold"))
        rows = [[ASSESS_EMOJI.get(str(s.get("status")).lower(), "•"),
                 _cell(s.get("name")), _cell(s.get("note"))]
                for s in design_assessment.get("sections", [])]
        out += _box_table(["", "Design section", "Note"], rows, width)
        out.append("")

    cites: set[tuple[str, str]] = set()
    for type_label, items in _report_groups(report):
        out.append(_paint(f"[{type_label}]", "orange", "bold"))
        rows = []
        for r, title, llm in items:
            detail = _detail_text(r)
            if r.citation:
                lbl = _source_label(citation_url(r.citation))
                if lbl:
                    cites.add((lbl, citation_url(r.citation)))
            rows.append([STATUS_EMOJI.get(r.status.value, "•"), title, _yesno(llm), detail])
        out += _box_table(["", "Short Description", "LLM?", "Detail"], rows, width)
        out.append("")
    out += _sources_footer(cites)
    out += ["", "Run `autocodabench checks` to see every check type performed and "
            "how each is verified."]
    return "\n".join(out)
