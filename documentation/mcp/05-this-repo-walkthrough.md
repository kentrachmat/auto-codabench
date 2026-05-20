# Chapter 5 — This Repo, Walked Through Line by Line

The repo has exactly four files that matter:

```
semanticscholar-MCP-Server/
├── requirements.txt              ← three deps
├── semantic_scholar_search.py    ← thin wrapper over the SS Python client
├── semantic_scholar_server.py    ← the MCP server itself
└── README.md                     ← install + config
```

All the design choices from Chapter 4 are here. We'll read both Python files
top to bottom and annotate what each line is doing and *why*.

## 5.1 — `requirements.txt`

```
requests
bs4
mcp
semanticscholar
```

Four dependencies, two of which earn their keep:

- `mcp` — the official Anthropic SDK. Brings `FastMCP`, the protocol
  implementation, the JSON-RPC layer, schema generation, stdio transport.
- `semanticscholar` — the third-party Python wrapper over Semantic Scholar's
  REST API. Handles HTTP, retries, pagination, parsing into typed `Paper` and
  `Author` objects.

`requests` and `bs4` (BeautifulSoup) are declared but not used in the two
Python source files. They're probably leftovers from an earlier prototype. If
you were tidying this repo you'd remove them.

> **Reading code lesson**: `requirements.txt` is a *claim*, not a fact. Always
> check what's actually imported. Here, `grep -r "import requests"` returns
> nothing.

## 5.2 — `semantic_scholar_search.py` (the data layer)

This file's job is one sentence: *isolate the SS Python wrapper from the MCP
server*. Nothing in here knows about MCP. You could import these functions into
a CLI, a web app, or a notebook unchanged.

### Imports

```python
import semanticscholar as sch
from semanticscholar import SemanticScholar, Author, Paper
from typing import List, Dict, Any
```

`SemanticScholar` is the client class. `Author` and `Paper` are typed return
objects. `sch` is the module itself, used later to catch
`sch.SemanticScholarException`.

### `initialize_client`

```python
def initialize_client() -> SemanticScholar:
    """Initialize the SemanticScholar client."""
    return SemanticScholar()
```

Constructs the HTTP client. The public API doesn't require auth, so no key.
Wrapped in a function so the MCP server can call it once at startup without
knowing the constructor details.

### `search_papers`

```python
def search_papers(client: SemanticScholar, query: str, limit: int = 10) -> List[Dict[str, Any]]:
    """Search for papers using a query string."""
    results = client.search_paper(query, limit=limit)
    return [
        {
            "paperId": paper.paperId,
            "title": paper.title,
            "abstract": paper.abstract,
            "year": paper.year,
            "authors": [{"name": author.name, "authorId": author.authorId} for author in paper.authors],
            "url": paper.url,
            "venue": paper.venue,
            "publicationTypes": paper.publicationTypes,
            "citationCount": paper.citationCount
        } for paper in results
    ]
```

Three things to notice:

1. **It returns plain dicts**, not `Paper` objects. The MCP layer wants
   JSON-serialisable values. Doing the conversion *here* keeps the MCP layer
   trivial.
2. **The author sub-objects are flattened to `{name, authorId}` pairs**, not
   left as `Author` objects. Same reason — and crucially, `authorId` is
   preserved so the model can pivot to `get_author_details`.
3. **Nine fields per paper.** This is the curated v1 set we discussed in
   Chapter 4. No `tldr`, no `externalIds`, no `embedding`. Opinionated by
   design.

### `get_paper_details` and `get_author_details`

```python
def get_paper_details(client: SemanticScholar, paper_id: str) -> Paper:
    """Get details of a specific paper."""
    return client.get_paper(paper_id)

def get_author_details(client: SemanticScholar, author_id: str) -> Author:
    """Get details of a specific author."""
    return client.get_author(author_id)
```

These are *one-liners*. They literally just delegate to the wrapper. Notice
they **return the wrapper's native objects**, not dicts. That's a small
inconsistency with `search_papers` — and the MCP layer compensates by doing
the dict conversion itself. Why the asymmetry? Probably because
`get_paper_details`' result is also reused by `get_citations_and_references`
(see below), which needs the `Paper` object's `.citations` and `.references`
attributes. Returning dicts here would have forced an awkward second
conversion. Pragmatic.

### `get_citations_and_references`

```python
def get_citations_and_references(paper: Paper) -> Dict[str, List[Dict[str, Any]]]:
    """Get citations and references for a paper."""
    return {
        "citations": paper.citations,
        "references": paper.references
    }
```

This takes a `Paper` (not a `paper_id`) and returns the two lists. Worth
noticing: `paper.citations` and `paper.references` may be **lazy** in the
underlying client — the first access triggers an extra HTTP fetch. That's why
the MCP server calls `get_paper_details` first, *then* passes the resulting
`Paper` into this function: the lazy fields will fire on first access.

### `main()`

A small self-test:

```python
def main():
    try:
        client = initialize_client()
        search_results = search_papers(client, "machine learning")
        ...
    except sch.SemanticScholarException as e:
        print(f"An error occurred: {e}")
```

This lets you run the data layer in isolation:

```bash
python semantic_scholar_search.py
```

…and see a few paper records print without touching MCP at all. Useful for
debugging connectivity issues without dragging the protocol into it.

> **Lesson**: keep your data layer runnable on its own. If the bug is "the
> server isn't returning anything", running the data layer directly cuts the
> search space in half — either the API is the problem or the protocol is.

## 5.3 — `semantic_scholar_server.py` (the MCP layer)

This file is the MCP surface: 129 lines, four tools, one log line per call.

### Imports and setup

```python
from typing import Any, List, Dict
import asyncio
import logging
from mcp.server.fastmcp import FastMCP
from semantic_scholar_search import initialize_client, search_papers, get_paper_details, get_author_details, get_citations_and_references
```

Notice the clean separation: every SS-specific call comes from
`semantic_scholar_search`. The MCP file doesn't import `semanticscholar` itself,
because it doesn't need to — that's the whole point of the two-file split.

```python
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
```

Logging at INFO, written to stderr (the default). With stdio transport, stderr
is what shows up in Claude Desktop's "server logs" panel. INFO is verbose
enough to debug, quiet enough to not flood.

```python
mcp = FastMCP("semanticscholar")
client = initialize_client()
```

Construct the server (named `semanticscholar`) and a single shared SS client.
The client is created at *import time*, not per-call. Two consequences:

- All tools share one HTTP connection pool. Good.
- If SS is unreachable at startup, the import fails before any tool can run.
  The host will log this and the server will exit. That's a reasonable
  failure mode — we don't want a server with no working backend.

### Tool 1: `search_semantic_scholar`

```python
@mcp.tool()
async def search_semantic_scholar(query: str, num_results: int = 10) -> List[Dict[str, Any]]:
    logging.info(f"Searching for papers with query: {query}, num_results: {num_results}")
    """
    Search for papers on Semantic Scholar using a query string.

    Args:
        query: Search query string
        num_results: Number of results to return (default: 10)

    Returns:
        List of dictionaries containing paper information
    """
    try:
        results = await asyncio.to_thread(search_papers, client, query, num_results)
        return results
    except Exception as e:
        return [{"error": f"An error occurred while searching: {str(e)}"}]
```

Annotations:

- `@mcp.tool()` — registers the function. The framework introspects the
  signature: `query: str, num_results: int = 10` becomes a JSON schema with
  `query` required and `num_results` defaulting to 10.
- `async def` — FastMCP awaits this directly in the event loop.
- `logging.info(...)` — fires before every call. Shows up in stderr.
- **Subtle bug**: the `logging.info` line is placed *above* the docstring.
  Python is forgiving — a docstring only counts as one if it's the first
  statement in the function body. So this function technically has *no
  docstring*, and FastMCP would fall back to using the function name as the
  description. In practice many FastMCP versions also read attached
  `__doc__` set after definition, and may not exhibit this bug. The
  cleaner fix is to put the docstring first, then the log line. (We'll note
  this as a real improvement opportunity at the end of the chapter.)
- `await asyncio.to_thread(search_papers, client, query, num_results)` —
  exactly the bridge from Chapter 4: a sync function called from an async
  context without blocking the event loop. The thread pool is `asyncio`'s
  default executor.
- `try/except` — catches *any* exception. The error is returned as a list
  containing a single `{"error": ...}` dict. The model sees the field and
  understands. (You could argue it should be `[{"error": ...}]` vs
  `{"error": ...}` — for consistency with the success shape, a list-of-one
  is the right choice and that's what the code does.)

### Tool 2: `get_semantic_scholar_paper_details`

```python
@mcp.tool()
async def get_semantic_scholar_paper_details(paper_id: str) -> Dict[str, Any]:
    logging.info(f"Fetching paper details for paper ID: {paper_id}")
    """..."""
    try:
        paper = await asyncio.to_thread(get_paper_details, client, paper_id)
        return {
            "paperId": paper.paperId,
            "title": paper.title,
            "abstract": paper.abstract,
            "year": paper.year,
            "authors": [{"name": author.name, "authorId": author.authorId} for author in paper.authors],
            "url": paper.url,
            "venue": paper.venue,
            "publicationTypes": paper.publicationTypes,
            "citationCount": paper.citationCount
        }
    except Exception as e:
        return {"error": f"An error occurred while fetching paper details: {str(e)}"}
```

This is where the dict-conversion happens that the data layer didn't do for us.
Same nine fields as `search`, which is nice for consistency: the model gets the
same shape whether it found the paper by searching or by ID.

Note this function **doesn't** include `citations` or `references` — those
require a second access. They're delivered by the dedicated tool below. Good
decomposition: each tool does one thing, the model picks which one it needs.

### Tool 3: `get_semantic_scholar_author_details`

```python
@mcp.tool()
async def get_semantic_scholar_author_details(author_id: str) -> Dict[str, Any]:
    logging.info(f"Fetching author details for author ID: {author_id}")
    """..."""
    try:
        author = await asyncio.to_thread(get_author_details, client, author_id)
        return {
            "authorId": author.authorId,
            "name": author.name,
            "url": author.url,
            "affiliations": author.affiliations,
            "paperCount": author.paperCount,
            "citationCount": author.citationCount,
            "hIndex": author.hIndex
        }
    except Exception as e:
        return {"error": f"An error occurred while fetching author details: {str(e)}"}
```

Same pattern. Seven fields about the author. Notice that `authorId` is included
in the return — so the model could pass this same ID back into the tool if it
wanted to (or paste it into a URL). IDs being round-trippable is the secret
sauce of multi-step LLM workflows.

### Tool 4: `get_semantic_scholar_citations_and_references`

```python
@mcp.tool()
async def get_semantic_scholar_citations_and_references(paper_id: str) -> Dict[str, List[Dict[str, Any]]]:
    logging.info(f"Fetching citations and references for paper ID: {paper_id}")
    """..."""
    try:
        paper = await asyncio.to_thread(get_paper_details, client, paper_id)
        citations_refs = await asyncio.to_thread(get_citations_and_references, paper)
        return {
            "citations": [
                {
                    "paperId": citation.paperId,
                    "title": citation.title,
                    "year": citation.year,
                    "authors": [{"name": author.name, "authorId": author.authorId} for author in citation.authors]
                } for citation in citations_refs["citations"]
            ],
            "references": [
                {
                    "paperId": reference.paperId,
                    "title": reference.title,
                    "year": reference.year,
                    "authors": [{"name": author.name, "authorId": author.authorId} for author in reference.authors]
                } for reference in citations_refs["references"]
            ]
        }
    except Exception as e:
        return {"error": f"An error occurred while fetching citations and references: {str(e)}"}
```

The most interesting tool. Two `asyncio.to_thread` calls in sequence:

1. Fetch the paper to get a `Paper` object.
2. Then access `.citations` / `.references`, which the SS wrapper lazily
   resolves with another HTTP call.

The result is normalised: each citation/reference returns the same four fields
(`paperId`, `title`, `year`, `authors[]`). Crucially, **the `paperId` field
means the model can pivot directly into `get_paper_details` on any
citation/reference it finds interesting.** That's the multi-step workflow:

> "Find the most-cited paper among the references of attention-is-all-you-need,
> then tell me about its authors."

`search → get_paper_details → get_citations_and_references → get_paper_details
→ get_author_details`. Five tool calls, all chained via IDs.

### The `__main__` block

```python
if __name__ == "__main__":
    logging.info("Starting Semantic Scholar MCP server")
    mcp.run(transport='stdio')
```

`mcp.run(transport='stdio')` is the only call that starts the protocol. It
blocks the main thread, reading JSON-RPC from stdin and writing to stdout,
until stdin closes (i.e., the host shuts the server down). On exit, the
function returns and the process ends.

## 5.4 — The full mental model in one diagram

```
   ┌────────────────────────────────────────────────────────────┐
   │ semantic_scholar_server.py                                 │
   │                                                            │
   │   FastMCP("semanticscholar")                               │
   │   ┌──────────────────────────────────────────────────┐     │
   │   │ @mcp.tool()  search_semantic_scholar             │ ──┐ │
   │   │ @mcp.tool()  get_semantic_scholar_paper_details  │ ──┼─┼──► sync calls
   │   │ @mcp.tool()  get_semantic_scholar_author_details │ ──┤ │     hidden in
   │   │ @mcp.tool()  get_semantic_scholar_citations_...  │ ──┘ │     thread pool
   │   └──────────────────────────────────────────────────┘     │
   │                                                            │
   │              asyncio.to_thread(...)                        │
   │                       │                                    │
   │                       ▼                                    │
   │ ┌────────────────────────────────────────────────────────┐ │
   │ │ semantic_scholar_search.py                             │ │
   │ │   search_papers       → client.search_paper()  ──┐     │ │
   │ │   get_paper_details   → client.get_paper()      ─┤     │ │
   │ │   get_author_details  → client.get_author()     ─┤     │ │
   │ │   get_citations_...   → paper.citations/refs    ─┘     │ │
   │ └────────────────────────────────────────────────────────┘ │
   │                       │                                    │
   │                       ▼                                    │
   │              semanticscholar (PyPI)                        │
   │                       │                                    │
   │                       ▼                                    │
   │        api.semanticscholar.org (HTTPS)                     │
   └────────────────────────────────────────────────────────────┘
```

Three layers. The top one talks MCP. The middle one shapes data. The bottom
one talks HTTP. Each layer ignores the others' concerns. That's why this code
is so small — and why it would be straightforward to swap, say, the SS wrapper
for an HTTP call to a different paper database, without touching the MCP
layer at all.

## 5.5 — What could be improved

A short, honest list:

- **Docstring placement bug** in every tool: the `logging.info(...)` line
  precedes the docstring, so technically the docstring isn't recognized as
  such by Python. Move it: docstring first, then log.
- **Unused dependencies**: `requests` and `bs4` are in `requirements.txt` but
  never imported.
- **`SemanticScholarException` is unused** by the server. The catch is a bare
  `Exception`. Catching the wrapper's typed exception would let the server
  give cleaner errors for known cases (rate-limited, paper not found) versus
  truly unexpected ones.
- **Field pruning is hard-coded.** Adding a `fields: list[str]` argument would
  let advanced callers trim further (or expand). Worth it in a v2.
- **The Semantic Scholar wrapper supports caching**; this repo doesn't enable
  it. For an interactive chat where the model might re-query the same paper
  twice in a session, in-memory caching is a quick win.
- **No tests.** The data layer has a `main()` self-test, which is nice, but
  there's no harness that pretends to be Claude and hits the MCP server.
  Writing one — even fifty lines — would catch protocol regressions early.

None of these are deal-breakers. The repo's value isn't its polish — it's that
the bones are so clean. You can see the pattern of *every* MCP server in
~190 lines of Python. That's worth the unused imports.

In the next chapter we draw the whole picture: what happens, end to end, when
a user types a single sentence into Claude Desktop and gets a real, fresh
paper back.
