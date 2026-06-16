"""Phase-1 research capability: external knowledge sources for the planner.

Phase 1 (plan) is far more useful when it can look at what already exists than
when it draws on the backbone's training data alone. Two structured sources plus
the agent's own web search give it that reach:

- **OpenAlex** — recent *related competition and benchmark papers* (e.g. from the
  NeurIPS Competition track or the Datasets & Benchmarks track), via the
  external ``openalex-research-mcp`` server (31 tools incl. topic/keyword works
  search, related-works, and a ``top_ai_conferences`` venue preset). Launched
  with ``npx``; keyless against the OpenAlex API (a courtesy email is polite).
- **Kaggle** — *how similar competitions are hosted* (metric, submission caps,
  team-size limits, phase deadlines, full description/rules pages), via the
  **first-party** Kaggle tools in our own MCP server
  (``autocodabench_search_kaggle_competitions`` / ``..._get_kaggle_competition``;
  see ``mcp.tools.research``). First-party because the published Kaggle MCP
  servers are broken or OAuth-only; the Kaggle SDK works keyless on PUBLIC
  competitions with a token.
- **Web search** — the SDK's built-in ``WebSearch`` / ``WebFetch``, a *last
  resort* (single-source and easily biased) for what the structured sources miss.

This module turns a small declarative :class:`ResearchConfig` into the concrete
external-MCP-server spec, extra tool allowlist, and environment additions the
plan phase hands the backend, and reports — for the benchmark and the CLI banner
— exactly which sources a given backbone could *actually* use. That last point is
load-bearing for benchmark fairness: **only the Claude backend can spawn external
MCP servers and call WebSearch/WebFetch**; OpenAI-compatible backbones get none
of them, and that asymmetry must be recorded, not hidden. (The first-party
Kaggle tools are served by the always-present autocodabench MCP server, so on a
Claude backend they need no external launcher — only the ``kaggle`` package and a
token.)

Nothing here requires the user to supply a private key. OpenAlex needs only a
courtesy contact email; Kaggle's public competition reads work against a shared
throw-away token when the user has not set their own.
"""
from __future__ import annotations

import importlib.util
import os
import shlex
import shutil
from dataclasses import asdict, dataclass
from pathlib import Path

# A public, throw-away Kaggle API token used by default so Phase 1 can read
# PUBLIC competitions out of the box without the user supplying a secret. Users
# are encouraged to generate their own at https://www.kaggle.com/settings/api
# and set KAGGLE_API_TOKEN (or ~/.kaggle/access_token); this is only a fallback.
_KAGGLE_FALLBACK_TOKEN = "KGAT_a440cc408e3a8f3175848842afa5bd04"

# OpenAlex external server: launched via npx (Node), overridable via env for
# pip/global installs. The first token is the launcher we check for on PATH.
_OPENALEX_DEFAULT_CMD = "npx -y openalex-research-mcp"
_OPENALEX_CMD_ENV = "AUTOCODABENCH_OPENALEX_MCP_CMD"

# The first-party Kaggle tools live in the autocodabench MCP server, so enabling
# Kaggle only adds these to the plan phase's allowlist (no external server).
_KAGGLE_TOOLS = [
    "mcp__autocodabench__autocodabench_search_kaggle_competitions",
    "mcp__autocodabench__autocodabench_get_kaggle_competition",
]


@dataclass
class ResearchConfig:
    """What external research Phase 1 is *allowed* to use (all on by default).

    The user can turn the whole capability off, or any single source off, before
    a run starts. This is intent, not availability — :func:`resolve` reconciles
    it against what the launcher/backend/packages can actually provide.
    """

    enabled: bool = True
    openalex: bool = True
    kaggle: bool = True
    web_search: bool = True

    @classmethod
    def off(cls) -> "ResearchConfig":
        return cls(enabled=False, openalex=False, kaggle=False, web_search=False)

    def wants(self, source: str) -> bool:
        return bool(self.enabled and getattr(self, source))

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ResolvedResearch:
    """The concrete, backend-aware result of applying a :class:`ResearchConfig`."""

    servers: dict          # external MCP servers to add (openalex)
    tools: list[str]       # extra allowed_tools (mcp__openalex__*, kaggle tools, …)
    env: dict              # env to merge into the phase + the autocodabench server
    web_search: bool       # whether WebSearch/WebFetch are actually enabled
    sources: dict          # per-source status string for display/recording
    backend_supported: bool  # can this backbone use external MCP/web at all?

    @property
    def any_active(self) -> bool:
        return bool(self.servers) or any("on" == self.sources.get(s, "")[:2]
                                         for s in self.sources)

    def effective(self) -> dict:
        """Which sources are actually wired AND usable on this backbone."""
        active = lambda s: self.sources.get(s, "").startswith("on")
        return {"openalex": active("openalex"), "kaggle": active("kaggle"),
                "web_search": self.web_search}


def _split_cmd(env_value: str | None, default: str) -> list[str]:
    return shlex.split(env_value or default)


def _openalex_email() -> str:
    """Courtesy contact email for the OpenAlex API's polite pool (not a secret)."""
    return (os.environ.get("AUTOCODABENCH_OPENALEX_MAILTO")
            or os.environ.get("OPENALEX_EMAIL")
            or os.environ.get("OPENALEX_MAILTO")
            or "autocodabench@users.noreply.github.com")


def kaggle_token(*, allow_fallback: bool = True) -> tuple[str | None, bool]:
    """Return ``(token, is_user_supplied)`` for the Kaggle tools.

    Prefers a user token (``KAGGLE_API_TOKEN`` env, then
    ``~/.kaggle/access_token``); otherwise the shared throw-away fallback so
    public reads work with no setup. A ``~/.kaggle/kaggle.json`` (username/key)
    also counts as user-supplied. ``is_user_supplied`` lets the banner tell the
    user whether they are on their own credentials or the shared token.
    """
    if os.environ.get("KAGGLE_API_TOKEN"):
        return os.environ["KAGGLE_API_TOKEN"].strip(), True
    try:
        tok_file = Path.home() / ".kaggle" / "access_token"
        if tok_file.is_file() and tok_file.read_text(encoding="utf-8").strip():
            return tok_file.read_text(encoding="utf-8").strip(), True
        if (Path.home() / ".kaggle" / "kaggle.json").is_file():
            return None, True   # username/key on disk; the SDK reads it directly
    except OSError:
        pass
    return (_KAGGLE_FALLBACK_TOKEN, False) if allow_fallback else (None, False)


def backend_supports_research(backend) -> bool:
    """Only the Claude backend can spawn external MCP servers / call WebSearch.

    The OpenAI-compatible backend executes a fixed local-tool surface and ignores
    ``mcp_servers``; it has no web tool. Recording this keeps cross-backbone
    benchmark numbers honest about who had internet/MCP reach.
    """
    return getattr(backend, "name", "") == "claude"


def resolve(config: ResearchConfig | None, *, backend=None) -> ResolvedResearch:
    """Reconcile *config* against launcher/package/backend availability.

    Returns the external MCP servers, extra tools, and env additions to wire into
    the plan phase, plus a per-source status (``on`` / ``off`` / ``unavailable:
    …``) for the banner and the benchmark record.
    """
    config = config if config is not None else ResearchConfig()
    supported = backend is None or backend_supports_research(backend)
    servers: dict = {}
    tools: list[str] = []
    env: dict = {}
    sources: dict = {}

    # --- OpenAlex (external MCP server via npx) -----------------------------
    if not config.wants("openalex"):
        sources["openalex"] = "off (disabled by user)"
    elif not supported:
        sources["openalex"] = "unavailable: backbone has no MCP support (Claude only)"
    else:
        cmd = _split_cmd(os.environ.get(_OPENALEX_CMD_ENV), _OPENALEX_DEFAULT_CMD)
        if not shutil.which(cmd[0]):
            sources["openalex"] = (f"unavailable: launcher '{cmd[0]}' not found "
                                   f"(install Node/npx or set {_OPENALEX_CMD_ENV})")
        else:
            servers["openalex"] = {
                "type": "stdio", "command": cmd[0], "args": cmd[1:],
                "env": {**os.environ, "OPENALEX_EMAIL": _openalex_email()},
            }
            tools.append("mcp__openalex__*")
            sources["openalex"] = "on — related competition/benchmark papers"

    # --- Kaggle (first-party tools in the autocodabench MCP server) ---------
    if not config.wants("kaggle"):
        sources["kaggle"] = "off (disabled by user)"
    elif not supported:
        sources["kaggle"] = "unavailable: backbone has no MCP support (Claude only)"
    elif importlib.util.find_spec("kaggle") is None:
        sources["kaggle"] = ("unavailable: the 'kaggle' package is not installed "
                             "(it ships with the base install — reinstall autocodabench)")
    else:
        tok, user_supplied = kaggle_token()
        if tok:
            env["KAGGLE_API_TOKEN"] = tok
        tools.extend(_KAGGLE_TOOLS)
        sources["kaggle"] = ("on — similar competitions (your Kaggle credentials)"
                             if user_supplied else
                             "on — similar competitions (shared public token — set "
                             "KAGGLE_API_TOKEN for your own)")

    # --- Web search (SDK built-in) -----------------------------------------
    web_search = config.wants("web_search") and supported
    if config.wants("web_search") and not supported:
        sources["web_search"] = "unavailable: backbone has no web tool (Claude only)"
    elif web_search:
        sources["web_search"] = "on — last-resort internet search"
        tools += ["WebSearch", "WebFetch"]
    else:
        sources["web_search"] = "off (disabled by user)"

    return ResolvedResearch(servers=servers, tools=tools, env=env,
                            web_search=web_search, sources=sources,
                            backend_supported=supported)


def describe(resolved: ResolvedResearch) -> list[str]:
    """Human-readable banner lines, one per source."""
    order = ("openalex", "kaggle", "web_search")
    pretty = {"openalex": "OpenAlex (related papers)",
              "kaggle": "Kaggle (similar competitions)",
              "web_search": "Web search (last resort)"}
    return [f"{pretty[s]}: {resolved.sources.get(s, 'off')}" for s in order]
