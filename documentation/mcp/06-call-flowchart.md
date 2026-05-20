# Chapter 6 — A Single Call, From Keystroke to Paper and Back

You've now seen the parts. This chapter assembles them. We'll follow one
concrete user prompt all the way through, drawing each hop. By the end you
should be able to draw this diagram from memory, and that means you understand
MCP.

The prompt we'll trace:

> *"Find me three recent papers about retrieval-augmented generation."*

You type that into Claude Desktop, with the Semantic Scholar MCP server
installed. Here is what happens.

## 6.1 — The high-level pipeline

```
   ┌──────────┐     ┌──────────────┐     ┌──────────────────┐     ┌──────────────────┐
   │   YOU    │ ──► │ Claude       │ ──► │ Semantic Scholar │ ──► │ Semantic Scholar │
   │ (typing) │     │ Desktop      │     │ MCP server       │     │ HTTPS API        │
   │          │ ◄── │ + Claude LLM │ ◄── │ (Python process) │ ◄── │ (api.s.s.org)    │
   └──────────┘     └──────────────┘     └──────────────────┘     └──────────────────┘
       ▲                   ▲                     ▲                        ▲
       │                   │                     │                        │
   the human            the host             the MCP server         the upstream API
```

Each arrow is a different protocol:

| Hop                                | Protocol                            |
|------------------------------------|-------------------------------------|
| You ↔ Claude Desktop               | Mouse, keyboard, OS GUI events       |
| Claude Desktop ↔ Anthropic API     | HTTPS, Anthropic's Messages API      |
| Claude Desktop ↔ MCP server        | JSON-RPC 2.0 over stdio (pipes)      |
| MCP server ↔ api.semanticscholar.org | HTTPS, Semantic Scholar's REST API |

These are four very different worlds. The reason your prompt works is that
each adjacent pair speaks an agreed protocol — and *Claude Desktop is the
hinge that translates between two of them*.

## 6.2 — The sequence diagram

This is the canonical drawing. It shows every message in time order. Read it
top to bottom.

```
You        Claude Desktop (host)       Claude (LLM API)        MCP Server (Python)      Semantic Scholar API
 │                │                            │                       │                          │
 │                │  (at host startup, once)   │                       │                          │
 │                │ ─── spawn subprocess ────────────────────────────► │                          │
 │                │ ◄─── stdin/stdout pipes open ─────────────────────│                          │
 │                │                            │                       │                          │
 │                │ ─── JSON-RPC initialize ─────────────────────────► │                          │
 │                │ ◄── initialize result (capabilities: tools) ───── │                          │
 │                │ ─── notifications/initialized ─────────────────► │                          │
 │                │                            │                       │                          │
 │                │ ─── JSON-RPC tools/list ─────────────────────────► │                          │
 │                │ ◄── 4 tool defs + JSON schemas ───────────────── │                          │
 │                │                            │                       │                          │
 │  (you type)    │                            │                       │                          │
 │ ─ "Find me 3 ─►│                            │                       │                          │
 │   papers..."   │                            │                       │                          │
 │                │ ── Messages API request ──►│                       │                          │
 │                │   (your prompt + tool defs)│                       │                          │
 │                │                            │                       │                          │
 │                │                            │ (model thinks)        │                          │
 │                │ ◄── tool_use block ────── │                       │                          │
 │                │   name=search_semantic_scholar                     │                          │
 │                │   args={query:"RAG", num_results:3}                │                          │
 │                │                            │                       │                          │
 │                │ ─── JSON-RPC tools/call ─────────────────────────► │                          │
 │                │   {name, args}             │                       │                          │
 │                │                            │                       │ ── HTTPS GET ──────────►│
 │                │                            │                       │  /graph/v1/paper/search?│
 │                │                            │                       │     query=RAG&limit=3   │
 │                │                            │                       │ ◄── JSON: [paper, paper, paper]
 │                │                            │                       │                          │
 │                │ ◄── tools/call result ─────────────────────────── │                          │
 │                │   content: [{type:text,    │                       │                          │
 │                │             text:"[...]"}] │                       │                          │
 │                │                            │                       │                          │
 │                │ ── Messages API request ──►│                       │                          │
 │                │   (prior turn + tool_result)                       │                          │
 │                │                            │                       │                          │
 │                │                            │ (model thinks)        │                          │
 │                │ ◄── final text answer ──── │                       │                          │
 │                │                            │                       │                          │
 │ ◄── rendered ──│                            │                       │                          │
 │     answer     │                            │                       │                          │
 │                │                            │                       │                          │
```

There's a lot in that picture. Let's walk through it in three phases.

## 6.3 — Phase A: Host startup (happens once, before you type anything)

When Claude Desktop launches, it reads
`~/Library/Application Support/Claude/claude_desktop_config.json`. There it
sees:

```json
{
  "mcpServers": {
    "semanticscholar": {
      "command": "python",
      "args": ["/path/to/semantic_scholar_server.py"]
    }
  }
}
```

The host:

1. **Spawns a subprocess.** Equivalent to running
   `python /path/to/semantic_scholar_server.py` with `stdin` and `stdout`
   wired to pipes the host controls. Anything the server prints to `stderr`
   goes to the host's log panel.
2. **Sends `initialize`** over the stdin pipe. This is the JSON-RPC handshake
   from Chapter 2. The host advertises its capabilities; the server responds
   with `{"tools": {}}` (it supports tools, nothing else).
3. **Sends `notifications/initialized`** — the "we're live, you may begin
   working" signal.
4. **Sends `tools/list`** to learn what's on offer. The server responds with
   four tool definitions, each with a name, a description (the function's
   docstring), and an `inputSchema` (JSON Schema generated from the type
   hints).

At this point the host has, in memory, a catalog like:

```python
[
  {"name": "search_semantic_scholar", "description": "...", "inputSchema": {...}},
  {"name": "get_semantic_scholar_paper_details", "description": "...", "inputSchema": {...}},
  {"name": "get_semantic_scholar_author_details", "description": "...", "inputSchema": {...}},
  {"name": "get_semantic_scholar_citations_and_references", "description": "...", "inputSchema": {...}},
]
```

None of this needed your involvement. The host is *primed*.

## 6.4 — Phase B: You type, the model decides, the tool runs

Now you press Enter. The host has to:

### Step 1 — Bundle your prompt with the tool catalog

The host doesn't send your raw text to the model and *also*, separately, tell
it about tools. The Anthropic Messages API has a `tools` field in the request.
The host fills it with the MCP tool catalog, lightly reformatted:

```python
anthropic.messages.create(
    model="claude-opus-4-7",
    messages=[{"role": "user", "content": "Find me three recent papers about RAG"}],
    tools=[
        {"name": "search_semantic_scholar", "description": "...", "input_schema": {...}},
        ...
    ],
    ...
)
```

Notice this is the *exact* same tool-use mechanism Claude would use even
without MCP. **MCP is not a different way of doing tool use; it is a different
way of *delivering tool definitions to the host*.** The model doesn't know
or care that the tool came from an MCP server.

### Step 2 — The model decides

Claude reads the prompt, reads the tool list, and emits a response containing
a `tool_use` content block:

```json
{
  "type": "tool_use",
  "id": "toolu_abc",
  "name": "search_semantic_scholar",
  "input": {"query": "retrieval-augmented generation", "num_results": 3}
}
```

It's saying: "I'd like to call this tool with these arguments." It does **not**
execute it. It can't — it's a remote LLM with no internet access of its own.

### Step 3 — The host routes the call to the MCP server

The host looks at the tool name. It says "I got `search_semantic_scholar` from
the `semanticscholar` MCP server." It writes a JSON-RPC `tools/call` request to
that server's stdin:

```json
{
  "jsonrpc": "2.0",
  "id": 17,
  "method": "tools/call",
  "params": {
    "name": "search_semantic_scholar",
    "arguments": {"query": "retrieval-augmented generation", "num_results": 3}
  }
}
```

(Most hosts will also prompt *you* for permission the first time, depending on
your settings. That step happens between the model's `tool_use` and the
host writing to stdin.)

### Step 4 — The server runs Python

Inside the server, FastMCP's event loop reads that line, validates the
arguments against the JSON schema, and dispatches to the registered
coroutine:

```python
async def search_semantic_scholar(query: str, num_results: int = 10):
    ...
    results = await asyncio.to_thread(search_papers, client, query, num_results)
    return results
```

`asyncio.to_thread` schedules `search_papers` to run in a worker thread. That
thread calls into the `semanticscholar` Python package, which issues an HTTP
GET to:

```
GET https://api.semanticscholar.org/graph/v1/paper/search?query=retrieval-augmented%20generation&limit=3
```

The response is parsed into `Paper` objects, which `search_papers` reshapes
into a list of nine-field dicts, which the tool function returns.

### Step 5 — The host hands the result to the model

FastMCP wraps the return value into a `tools/call` response over JSON-RPC:

```json
{
  "jsonrpc": "2.0",
  "id": 17,
  "result": {
    "content": [{"type": "text", "text": "[{\"paperId\": ...}, ...]"}],
    "isError": false
  }
}
```

The host reads this and pushes it back to the model as a `tool_result`:

```python
anthropic.messages.create(
    ...,
    messages=[
        {"role": "user", "content": "Find me three..."},
        {"role": "assistant", "content": [<the prior tool_use>]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "toolu_abc", "content": "[...]"}]}
    ],
    ...
)
```

### Step 6 — The model writes the final answer

Claude now has the three real papers in its context. It writes prose: "Here
are three recent papers on retrieval-augmented generation: ..." with the
titles, authors, and years it pulled from the tool result. The host streams
that text back to your screen.

## 6.5 — A more interesting case: chained calls

If you'd asked something deeper —

> *"…and tell me which of those papers cites the most others."*

— the model would chain. After getting the search results, it would emit a
*second* `tool_use`, calling
`get_semantic_scholar_citations_and_references(paper_id="...")` once for each
of the three papers, comparing the lengths of the returned reference arrays,
and *then* writing the final answer.

The full sequence becomes:

```
Phase A (startup, once)
Phase B repeats:
   model picks tool → host calls MCP → server hits SS → result back to model
…until the model has enough info to write the final answer.
```

The number of tool calls is decided **by the model**, dynamically. Some
prompts use zero; some use ten. The host's job is just to keep the loop going
until the model stops emitting `tool_use` blocks.

## 6.6 — Layered swimlanes (the same picture, organized by protocol)

Here is one more view of the same thing, this time arranged by *which
protocol is being spoken*:

```
 ┌────────────────────────────────────────────────────────────────────┐
 │ GUI events (mouse, keystrokes)                                     │
 │ ▲                                                                  │
 │ │  You ◄────── Claude Desktop ──────                                │
 │ │              UI / chat surface                                    │
 └─┼──────────────────────────────────────────────────────────────────┘
   │
 ┌─┼──────────────────────────────────────────────────────────────────┐
 │ │ Anthropic Messages API (HTTPS, JSON)                             │
 │ │                                                                  │
 │ │  Claude Desktop  ◄──────►  Anthropic / Claude model              │
 │ │  (sends prompts + tools; receives text + tool_use)               │
 └─┼──────────────────────────────────────────────────────────────────┘
   │
 ┌─┼──────────────────────────────────────────────────────────────────┐
 │ │ MCP (JSON-RPC 2.0 over stdio pipes)                              │
 │ │                                                                  │
 │ │  Claude Desktop  ◄──────►  Semantic Scholar MCP server           │
 │ │  initialize / tools/list / tools/call                            │
 └─┼──────────────────────────────────────────────────────────────────┘
   │
 ┌─┼──────────────────────────────────────────────────────────────────┐
 │ │ Semantic Scholar REST API (HTTPS, JSON)                          │
 │ │                                                                  │
 │ │  semanticscholar PyPI client  ◄──────►  api.semanticscholar.org  │
 │ │  GET /graph/v1/paper/search, /paper/{id}, /author/{id}, …        │
 └────────────────────────────────────────────────────────────────────┘
```

Each lane is independent. The model has no idea HTTPS exists. The Semantic
Scholar API has no idea what MCP is. Claude Desktop is the only piece that
speaks multiple protocols — and it is, in the end, the entire reason this
whole thing works.

## 6.7 — A debugging map

Once you can see the four lanes, debugging becomes structured. When something
breaks, you ask "**which lane?**":

| Symptom                                              | Which lane?                                                          |
|------------------------------------------------------|-----------------------------------------------------------------------|
| The server doesn't appear in the host's tool list    | MCP lane — server failed to start or to respond to `initialize`. Check stderr.  |
| The model answers but never calls the tool           | Messages API lane — tool description may be unclear, or model judged tool unnecessary. |
| The tool is called but errors out                    | MCP lane (validation) **or** SS lane (HTTP). Read the error string.  |
| Wrong / stale data                                   | SS lane — actual upstream API result. Run `semantic_scholar_search.py`'s `main()` to confirm. |
| Tool runs, but final answer ignores the data         | Messages API lane — model may be choosing not to use the result. Inspect the conversation in the host. |
| Server crashes mid-call                              | Python lane — read the traceback in stderr.                          |

That mapping is the practical payoff of internalising the layering. *"Which
protocol is the bug in?"* is almost always the first useful question.

## 6.8 — Putting it all together

You should now be able to look at a single user message in Claude Desktop and
predict, with no fewer than ten arrows, exactly what happens between the
keystroke and the answer:

1. Host already has tool catalog from earlier `tools/list`.
2. Host sends your prompt + catalog to Claude via Messages API.
3. Claude responds with a `tool_use` block.
4. Host sends a `tools/call` to the SS MCP server over stdio.
5. Server validates args, dispatches to `search_semantic_scholar`.
6. Tool function calls `asyncio.to_thread(search_papers, ...)`.
7. Worker thread calls into `semanticscholar` package.
8. Package makes HTTPS GET to api.semanticscholar.org.
9. JSON response is parsed into `Paper` objects, reshaped to dicts.
10. Dicts returned through to MCP layer, wrapped as `tools/call` result.
11. Host pushes result back to Claude as a `tool_result`.
12. Claude writes the final prose answer, streamed to the UI.

And then you read three nice paper titles and don't realize a dozen processes
just coordinated to give them to you.

That is the entire point of MCP. The complexity is real, but it is paid once,
by the protocol designers and SDK authors, so that anyone can write a 130-line
Python file and gain a *capability* — and so any AI app, present or future,
can use that capability without ever knowing the file existed.

---

That's the book. If you got this far, you understand MCP not as a buzzword but
as a small, well-shaped piece of plumbing — and you can build, audit, or debug
one yourself. Welcome to the network effect.
