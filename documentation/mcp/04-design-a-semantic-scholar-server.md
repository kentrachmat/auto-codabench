# Chapter 4 — Designing an MCP Server for Semantic Scholar

We now know how to build *any* MCP server. Let's slow down and answer a more
interesting question: *if you'd never seen this repo and someone said "build me
an MCP server for Semantic Scholar", what would you do?*

Pretend it's day one. Empty folder. We'll think out loud, then in the next
chapter look at what this repo actually shipped.

## 4.1 — Know your domain first

Before any code, ask: **what is Semantic Scholar, and what does its API let me
do?**

Semantic Scholar (SS) is a free academic search engine run by the Allen
Institute for AI. It indexes hundreds of millions of papers — most published CS,
biomedicine, parts of social sciences. For each paper, SS tracks:

- A globally unique `paperId` (a 40-character hex string)
- Title, abstract, year, venue, publication types, URL
- Author list, each with their own `authorId`
- The list of papers it **cites** (its references)
- The list of papers that **cite it** (its citations)
- Citation count, influential citation count
- Various external IDs (DOI, ArXiv ID, MAG, PubMed, …)

For each author, SS tracks:

- `authorId`, name, affiliations
- Paper count, citation count, h-index
- The list of their papers

There's a public REST API, but a Python wrapper called `semanticscholar` already
exists and handles authentication (none required for public access), pagination,
and rate limiting. We will use it. Two minutes of "don't reinvent the HTTP
client."

> **Design principle**: when there's a sane Python wrapper for the API, lean on
> it. The MCP server is a *thin shim*. Its job is to translate between MCP's
> world (JSON-RPC over stdio) and the wrapper's world (Python objects). The
> less business logic you write, the better.

## 4.2 — Who is the user?

A subtle but important question. Our user is **the language model**, mediated by
the host's UI. The model is reading our tool descriptions and deciding what to
call. So:

- Tool names should make sense to a model that has read the internet but maybe
  not your codebase. `search_paper` is better than `s.q()` even if you're a
  golfer.
- Descriptions should be specific enough that the model can pick the right one
  from a list of four similar tools without guessing. "Search for papers" is
  *too* generic if you also have "Get paper details" — the model might confuse
  them when the user says "look up this paper."
- Argument names should be the obvious ones a model would generate. `query`,
  `paper_id`, `author_id`. Not `q`, `pid`, `aid`.

There's a second, hidden user: **the developer who installs the server** (you,
your future self, your colleague). For them:

- Errors should be readable. Stack traces ending up in the LLM's context window
  is a bad day for everyone.
- Logs should be tagged so they can grep them in stderr.
- Side effects should be predictable. No writing files unless the tool's name
  says so.

## 4.3 — Pick the primitives

From Chapter 2: tools change the world, resources describe it, prompts shape
the conversation.

Semantic Scholar is external state we want to *query*. The natural fit is
**tools**: the model picks one, calls it, gets back data. We don't need
resources (the data is unbounded; you can't list every paper) and we don't need
prompts (we have no opinion on how the model should phrase a literature
review).

So: **tools only**. That's a clean decision that constrains everything else.

## 4.4 — Enumerate the tools

Brainstorm: what would a researcher want their AI assistant to be able to do
with SS?

- Search for papers by keyword
- Look up a specific paper by ID and read its details
- Look up a specific author and see their stats
- Walk the citation graph: see what cites X, and what X cites
- (Maybe) get recommendations
- (Maybe) batch fetch
- (Maybe) full-text search

For a v1, the first four are clearly the most useful. The last three are
optimisations or extensions. Let's commit to:

1. `search_semantic_scholar(query, num_results)`
2. `get_semantic_scholar_paper_details(paper_id)`
3. `get_semantic_scholar_author_details(author_id)`
4. `get_semantic_scholar_citations_and_references(paper_id)`

This is exactly the set this repo ships. Good — let's defend that choice.

### Why namespace the tool names with `semantic_scholar`?

When the model sees tools from *multiple* MCP servers, names are flat. If your
host also has a Wikipedia MCP server with a `search` tool, and we just called
ours `search`, the model has to disambiguate from descriptions alone. Prefixing
each tool with `semantic_scholar` makes it unambiguous at a glance. It also
saves the user from a "wait, why did it search Wikipedia when I asked for
papers?" moment.

The cost is verbosity in the tool catalog. The benefit is the model never
confuses providers. Easy trade.

### Why combine citations and references in one tool?

You could split them: `get_citations(paper_id)` and `get_references(paper_id)`.
The repo combines them. Why?

- Both are derived from the same `Paper` object. The underlying SS API call
  already returns both. Splitting forces two round-trips for what is naturally
  one operation.
- The model almost always wants to know both — a researcher cares about "what
  led to this paper" *and* "what came after." Forcing two tool calls is friction.
- Combined returns let you label them cleanly: `{"citations": [...],
  "references": [...]}`. The model can read the keys and pick which side to
  reason over.

This is a recurring tension: **fine-grained tools vs coarse-grained tools**.
Fine-grained gives the model precise control. Coarse-grained reduces the number
of round-trips and the chance of confusion. There's no global right answer —
but in this case, the citation graph is so often traversed in pairs that
combining wins.

### Why expose `num_results` but not other filters?

`num_results` is the one parameter where the model's intent strongly affects
both quality and latency. *Three* papers is a focused look; *fifty* is a survey.
Letting the model choose pays off.

Other filters (year, venue, field) could be exposed — and a v2 server probably
would. But adding more parameters means more for the model to think about, more
for the description to explain, and more for the schema to validate. For a v1
that proves the value, two-arg `search(query, num_results)` is right-sized.

> **Design principle**: every argument has a cost in confusion. Add them only
> when their absence hurts more than their presence.

## 4.5 — Argument types and validation

JSON Schema, generated from type hints, gives us validation for free. Decisions
to make:

- `query: str` — required, no default. Searching with an empty query is
  meaningless.
- `num_results: int = 10` — optional with sensible default. Ten is enough to be
  useful, small enough to not flood context.
- `paper_id: str` — required. SS accepts multiple ID formats (their hex ID, DOI,
  ArXiv, MAG, etc.), so `str` is correct; we don't want to over-constrain.
- `author_id: str` — same reasoning.

Note that we do *not* enforce, in the schema, that `paper_id` looks like a
40-character hex string. Why? Because SS happily accepts a DOI like
`10.18653/v1/N19-1423` or an ArXiv ID like `arXiv:1706.03762`. If we
regex-validated the input we'd lock out valid IDs.

> **Design principle**: validate at the system boundary you actually control —
> the format your tool returns — and let the underlying service be the source
> of truth for what *it* accepts.

## 4.6 — Return shape

The single most-undervalued design decision in an MCP server is **the shape of
what tools return**.

Bad return:
```
"Found a paper called 'Attention Is All You Need' from 2017 by 8 authors."
```

This is prose. The model can read it, but it can't easily compare papers,
extract IDs, or feed them back into other tools. **Plain text returns make
tool composition hard.**

Better return:
```json
{
  "paperId": "204e3073870fae3d05bcbc2f6a8e263d9b72e776",
  "title": "Attention Is All You Need",
  "year": 2017,
  "authors": [
    {"name": "Ashish Vaswani",  "authorId": "40348417"},
    {"name": "Noam Shazeer",    "authorId": "1696220"},
    ...
  ],
  "venue": "NIPS",
  "citationCount": 89312
}
```

Three things this shape does right:

1. **Structured fields** the model can reason over: pick the year, compare
   citation counts, list authors.
2. **Round-trippable IDs**: the model can now call `get_semantic_scholar_author_
   details` on any author it finds. Tools compose into mini-workflows.
3. **No extra prose**. The model knows how to write English; you don't have to
   write it for it.

This is exactly the shape the repo's `search_papers` returns. We'll see it in
Chapter 5.

A subtle but important sub-decision: **what do you leave out?** The SS `Paper`
object has dozens of fields — `tldr`, `externalIds`, `fieldsOfStudy`,
`openAccessPdf`, `embedding`, `isOpenAccess`, … . Returning all of them bloats
context, slows the model, and dilutes the signal. The repo picks ~9 fields that
matter for the common case. A v2 might add a parameter `fields: list[str]`
letting the model choose. For v1, opinionated defaults beat configurability.

## 4.7 — Async, threads, and not blocking the loop

The `semanticscholar` package is **synchronous**. Its methods do blocking HTTP
calls. If we wrote our tools as `async def` and called `client.search_paper(...)`
directly, we'd block the entire MCP event loop while waiting on the network.
For a single user that mostly works, but it's lazy and it bites under any kind
of concurrency.

The fix is two lines:

```python
async def search(query: str, num_results: int = 10):
    return await asyncio.to_thread(search_papers, client, query, num_results)
```

`asyncio.to_thread` runs the blocking function in a worker thread and gives us
back an awaitable. The event loop stays free to handle other requests, logging,
and stdin reads while the network call is in flight.

> **Design principle**: when bridging an async framework (FastMCP) to a sync
> library (semanticscholar), use `asyncio.to_thread`. Always.

## 4.8 — Error handling philosophy

There are three reasonable strategies:

1. **Let exceptions propagate.** FastMCP will return an `isError: true`
   response. The model sees a clear "tool failed" signal.
2. **Catch and return structured errors.** `{"error": "..."}`. The model sees
   it as data, not a tool failure.
3. **Catch and *summarise*.** Map common failure modes to user-friendly text.

This repo uses option 2 everywhere: every tool wraps its body in `try/except`
and returns `{"error": str(e)}`. The model sees a normal-looking response with
an `error` field, reads it, and can decide what to do.

Trade-off: option 2 hides errors from the host's UI. If the host has a
"surface tool errors" banner, option 1 lights it up; option 2 keeps it dark
because, from the host's perspective, the tool succeeded — it returned a value.

Both are reasonable. Pick one and be consistent. The repo's consistency is
fine.

## 4.9 — Logging

Two things to keep in mind:

- **Write to stderr, not stdout.** stdout is the JSON-RPC channel; anything you
  print there corrupts the protocol. Use the `logging` module and configure it
  to stderr (the default). The repo does this with
  `logging.basicConfig(level=logging.INFO, ...)`.
- **Log on entry, not just on error.** When something goes wrong six weeks from
  now, `INFO: Searching for papers with query: ...` is the breadcrumb you'll
  thank yourself for. Log the inputs (carefully — no secrets) at the top of
  each tool.

## 4.10 — The "shape" of the final design

If we line up all the decisions:

| Decision               | Choice                                         | Why                                                                 |
|------------------------|-------------------------------------------------|---------------------------------------------------------------------|
| Primitives             | Tools only                                      | We're querying external state; no need for resources or prompts.   |
| Tool count             | 4                                               | Smallest set that covers search, drill-down, author, citation graph.|
| Tool name prefix       | `semantic_scholar_*`                            | Disambiguate from other MCP servers in the same host.              |
| Underlying client      | `semanticscholar` package                       | Don't reinvent the HTTP/parsing layer.                              |
| Sync/async             | Async tools, `asyncio.to_thread` around sync calls | Don't block the event loop.                                       |
| Argument validation    | JSON Schema from type hints                     | Free, consistent, model-friendly.                                  |
| Return shape           | Structured dicts with round-trippable IDs       | Enables tool composition.                                          |
| Error handling         | Catch + return `{"error": "..."}`               | Consistent; model can read the field.                              |
| Logging                | `logging.INFO`, stderr                          | Greppable; doesn't corrupt the protocol.                            |
| Transport              | stdio                                           | Local server, owned by the host process.                           |
| Framework              | FastMCP                                         | Decorators + auto schema; cuts boilerplate to nothing.              |

If you set out tomorrow to write a Wikipedia MCP server, or an arXiv one, or a
Spotify one, you'd make almost the same table — only the four tools at the top
would change.

## 4.11 — What we'd add in v2 (and why we didn't)

- **Field selection** (`fields: list[str]`) on `search` and `get_paper_details`
  — lets the model trim context when it knows what it cares about.
- **Year / venue / openAccess filters** on `search` — for surveying recent or
  open-access literature.
- **Bulk fetch** — `get_papers(paper_ids: list[str])`. Faster than N round-
  trips.
- **A `recommend_papers(paper_id)` tool** — SS offers a recommendations
  endpoint; the model could chain it after `search`.
- **A resource for each paper** — URI like `paper://<paperId>` returning the
  abstract or open-access PDF text, so the user can `@`-attach a paper into
  the conversation.

Every one of these is a reasonable add. None is needed for the core loop —
which is exactly the v1 the repo ships. Now we know what the design *is*. In
Chapter 5 we'll look at how the authors actually wrote it in Python.
