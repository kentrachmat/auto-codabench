"""First-party Phase-1 research tools — Kaggle competition discovery.

Why first-party rather than an external MCP server: the published Kaggle MCP
servers are either single-purpose (dataset download only) or fail to install,
and the official server is OAuth/remote (no headless use). The Kaggle SDK,
however, exposes exactly what Phase-1 needs — competition *search* and full
*description/rules pages* — and works keyless against PUBLIC competitions with a
token. So we wrap it directly here, with controlled tool names the plan skill
can rely on. (OpenAlex, by contrast, has an excellent external server —
``openalex-research-mcp`` — so that source stays external; see
``agent.research``.)

``kaggle`` is imported lazily inside each tool so the keyless validator/replay
paths never load it, and a missing install degrades to a clear error rather than
breaking the MCP server import. Authentication is read from the environment
(``KAGGLE_API_TOKEN`` / ``~/.kaggle``), injected by the plan phase.
"""
from __future__ import annotations

import asyncio
import logging
import re
from typing import Any

from ..instance import mcp
from ...run_log import logged_tool

log = logging.getLogger("autocodabench.research")


def _slug(competition: str) -> str:
    """Accept a full Kaggle URL or a bare slug; return the slug."""
    s = (competition or "").strip().rstrip("/")
    m = re.search(r"competitions/([^/?#]+)", s)
    return m.group(1) if m else s.split("/")[-1]


def _authed_api():
    """Return an authenticated Kaggle API client (lazy import)."""
    from kaggle import api  # raises ImportError if the extra isn't installed
    api.authenticate()
    return api


def _competition_record(c: Any) -> dict[str, Any]:
    """Project a Kaggle competition object to the hosting-relevant fields."""
    g = lambda name: getattr(c, name, None)
    return {
        "title": g("title"),
        "url": g("url") or g("ref"),
        "category": g("category"),
        "evaluation_metric": g("evaluation_metric"),
        "reward": g("reward"),
        "deadline": str(g("deadline")) if g("deadline") else None,
        "enabled_date": str(g("enabled_date")) if g("enabled_date") else None,
        "max_daily_submissions": g("max_daily_submissions"),
        "max_team_size": g("max_team_size"),
        "team_count": g("team_count"),
        "organization_name": g("organization_name") or g("host_name"),
        "description": g("description"),
        "is_kernels_submissions_only": g("is_kernels_submissions_only"),
    }


@mcp.tool()
@logged_tool("autocodabench_search_kaggle_competitions")
async def autocodabench_search_kaggle_competitions(
    query: str,
    limit: int = 10,
) -> dict[str, Any]:
    """Search PUBLIC Kaggle competitions to learn how similar competitions are
    hosted in practice — their evaluation metric, submission caps, team-size
    limits, phase deadlines, and reward.

    Use this in Phase 1 to ground §3 Metric, §5 Rules, and §7 Schedule in
    state-of-the-art hosting practice rather than the model's prior alone.

    Args:
        query: free-text search (e.g. "image classification fairness").
        limit: maximum competitions to return (default 10).

    Returns a dict with ``competitions`` (a list of hosting-relevant fields) or
    an ``error`` string (e.g. when the ``kaggle`` extra is not installed).
    """
    def _work() -> dict[str, Any]:
        try:
            api = _authed_api()
        except ImportError:
            return {"error": "the Kaggle research source requires the 'kaggle' "
                             "package — it ships with the base install; reinstall autocodabench."}
        except Exception as e:  # auth / network
            return {"error": f"Kaggle authentication failed: {type(e).__name__}: {e}"}
        try:
            resp = api.competitions_list(search=query)
            # kaggle >=2.x returns an ApiListCompetitionsResponse whose
            # `.competitions` is a list (possibly empty); older kaggle returned a
            # plain iterable. Handle both — and never fall through to `list(resp)`
            # on an empty result, which would raise (the response isn't iterable).
            comps = getattr(resp, "competitions", None)
            if comps is None:
                try:
                    comps = list(resp)
                except TypeError:
                    comps = []
            records = [_competition_record(c) for c in comps[: max(1, limit)]]
            return {"query": query, "count": len(records), "competitions": records}
        except Exception as e:
            return {"error": f"Kaggle search failed: {type(e).__name__}: {e}"}

    return await asyncio.to_thread(_work)


@mcp.tool()
@logged_tool("autocodabench_get_kaggle_competition")
async def autocodabench_get_kaggle_competition(
    competition: str,
    page_name: str | None = None,
) -> dict[str, Any]:
    """Fetch a PUBLIC Kaggle competition's full description pages (overview,
    data, rules, evaluation) — the authoritative text for how a real
    competition specifies its task, metric, and rules.

    Args:
        competition: a competition slug or full URL
            (e.g. "titanic" or "https://www.kaggle.com/competitions/titanic").
        page_name: optional single page to fetch (e.g. "rules", "overview");
            omit to return all pages.

    Returns a dict with ``pages`` mapping page name → markdown content, or an
    ``error`` string.
    """
    slug = _slug(competition)

    def _work() -> dict[str, Any]:
        try:
            api = _authed_api()
        except ImportError:
            return {"error": "the Kaggle research source requires the 'kaggle' "
                             "package — it ships with the base install; reinstall autocodabench."}
        except Exception as e:
            return {"error": f"Kaggle authentication failed: {type(e).__name__}: {e}"}
        try:
            pages = api.competition_list_pages(slug, page_name)
            seq = getattr(pages, "pages", None) or pages
            out = {}
            for p in seq:
                name = getattr(p, "name", None) or "page"
                out[name] = getattr(p, "content", None) or ""
            return {"competition": slug, "pages": out}
        except Exception as e:
            return {"error": f"Kaggle page fetch failed for {slug!r}: "
                             f"{type(e).__name__}: {e}"}

    return await asyncio.to_thread(_work)
