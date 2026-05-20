# Chapter 2 — MCP, Technically

In Chapter 1 we said MCP is "a standard way for an AI app to discover and use
external tools and data." Now we make that precise.

If you remember just one sentence from this chapter, make it this:

> **An MCP server is a process that speaks JSON-RPC 2.0 over some transport
> (most often stdin/stdout), implementing a small fixed set of methods that let
> a host discover capabilities and invoke them.**

That's it. Every other technical detail in MCP is a consequence of that
sentence.

## 2.1 — JSON-RPC 2.0 in 90 seconds

JSON-RPC 2.0 is one of the oldest, simplest RPC formats. Every message is a JSON
object with at most four fields. There are exactly three kinds of message:

**Request** — "please do something and tell me what happened"
```json
{
  "jsonrpc": "2.0",
  "id": 7,
  "method": "tools/list",
  "params": {}
}
```

**Response** — the reply to a request, matched by `id`
```json
{
  "jsonrpc": "2.0",
  "id": 7,
  "result": { "tools": [ ... ] }
}
```

or, on error:
```json
{
  "jsonrpc": "2.0",
  "id": 7,
  "error": { "code": -32601, "message": "Method not found" }
}
```

**Notification** — like a request but with no `id` and no reply expected. Used
for one-way signals (e.g. "I'm initialized now", "this list changed").
```json
{
  "jsonrpc": "2.0",
  "method": "notifications/initialized"
}
```

That is the *entire* protocol grammar. MCP layers a vocabulary of `method` names
and parameter shapes on top of it — `initialize`, `tools/list`, `tools/call`,
`resources/list`, `resources/read`, `prompts/list`, `prompts/get`, and a handful
of others.

## 2.2 — Transports: how the bytes get from A to B

JSON-RPC doesn't say *how* the messages are delivered. MCP defines two transports:

### stdio (the common case, and what this repo uses)

The host *spawns the server as a child process*. It writes JSON-RPC messages to
the child's `stdin` and reads JSON-RPC messages from the child's `stdout`. Each
message is a single line of JSON, terminated by `\n`. (The spec calls this
"line-delimited JSON".) `stderr` is reserved for logs — the host typically
captures it and shows it in a sidebar.

stdio is brilliantly simple. There's no port, no auth, no TLS, no CORS. The OS
already isolates the process for you. The lifetime of the server is exactly the
lifetime of the host, because the parent process owns its child.

This is why `semantic_scholar_server.py` ends with:

```python
mcp.run(transport='stdio')
```

That tells FastMCP: read JSON-RPC from stdin, write JSON-RPC to stdout, log to
stderr, exit when stdin closes.

### Streamable HTTP (the remote case)

For servers that live somewhere else — a SaaS, a remote container — MCP defines
an HTTP-based transport. The client sends requests via POST, and the server can
stream responses back (and push server-initiated messages) over a single long-
lived HTTP connection. Earlier versions of MCP used a separate "HTTP+SSE"
transport; that's now folded into Streamable HTTP.

For this repo you can ignore Streamable HTTP entirely. The server runs locally,
the host launches it, stdio is sufficient.

## 2.3 — The lifecycle

Every MCP session goes through three phases. Understanding the phases makes the
log output of any MCP server suddenly readable.

```
   ┌──────────────┐   ┌───────────────┐   ┌──────────────┐
   │ Initialize   │ ► │ Operate       │ ► │ Shutdown     │
   │ (handshake)  │   │ (do things)   │   │ (stdin EOF)  │
   └──────────────┘   └───────────────┘   └──────────────┘
```

### Initialization

1. Host → server: `initialize` request, including
   - `protocolVersion` — which spec revision the host speaks
   - `capabilities` — what *the host* can do (e.g. "I support sampling")
   - `clientInfo` — name/version of the host
2. Server → host: an `initialize` response, including
   - the protocol version it agrees to
   - its own `capabilities` (`tools`, `resources`, `prompts`, `logging`, etc.)
   - `serverInfo` (name, version)
3. Host → server: a notification `notifications/initialized`. This signals "we
   are now in the operate phase, you may start doing real work."

Capability negotiation matters: a server that only implements tools will
advertise `{"tools": {}}` in its capabilities and *not* `{"resources": {}}`. The
host then knows not to ask it for resources.

### Operation

This is where the actual back-and-forth happens. The host calls methods like:

- `tools/list` — give me your tool catalog
- `tools/call` — run this tool with these arguments
- `resources/list`, `resources/read`, `resources/subscribe`
- `prompts/list`, `prompts/get`

And the server can send notifications back, like `notifications/tools/list_changed`
to tell the host its tool catalog just changed.

### Shutdown

The host closes stdin. The server should drain in-flight requests and exit. With
stdio this is automatic: when the parent closes the pipe, reading from stdin
returns EOF, and the server's main loop ends.

## 2.4 — A `tools/list` exchange, on the wire

To make all of this concrete, here is the exact pair of messages the host and
server in this repo exchange when the host asks for the tool catalog.

**Host → Server:**
```json
{"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
```

**Server → Host** (abbreviated):
```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "result": {
    "tools": [
      {
        "name": "search_semantic_scholar",
        "description": "Search for papers on Semantic Scholar using a query string.\n\nArgs:\n    query: Search query string\n    num_results: Number of results to return (default: 10)\n\nReturns:\n    List of dictionaries containing paper information",
        "inputSchema": {
          "type": "object",
          "properties": {
            "query":       {"type": "string"},
            "num_results": {"type": "integer", "default": 10}
          },
          "required": ["query"]
        }
      },
      { "name": "get_semantic_scholar_paper_details", "...": "..." },
      { "name": "get_semantic_scholar_author_details", "...": "..." },
      { "name": "get_semantic_scholar_citations_and_references", "...": "..." }
    ]
  }
}
```

Two things to notice:

1. The `inputSchema` is **JSON Schema** — the same JSON Schema your favourite
   API tools probably use. Every MCP tool advertises one. The model uses it to
   decide how to call the tool; the host uses it to validate arguments before
   sending them.
2. The `description` is the function's docstring, lifted verbatim. The model
   reads this to decide whether the tool is the right one for the job. The
   docstring is *prompt engineering*. Treat it that way.

## 2.5 — A `tools/call` exchange

When the model decides to call a tool, the host sends:

```json
{
  "jsonrpc": "2.0",
  "id": 2,
  "method": "tools/call",
  "params": {
    "name": "search_semantic_scholar",
    "arguments": {
      "query": "retrieval augmented generation",
      "num_results": 3
    }
  }
}
```

The server runs the function and replies:

```json
{
  "jsonrpc": "2.0",
  "id": 2,
  "result": {
    "content": [
      {
        "type": "text",
        "text": "[{\"paperId\": \"abc123\", \"title\": \"RAG: ...\", ... }]"
      }
    ],
    "isError": false
  }
}
```

Tool return values are wrapped in a `content` list whose items can be `text`,
`image`, or other typed parts. FastMCP does that wrapping for you — your Python
function can return a `list[dict]` and the framework serializes it into a `text`
content block.

If the function raises, `isError` is `true` and the `text` contains the error.
This is how the model "sees" failures: it gets text back saying *something went
wrong*, and can decide whether to retry, ask the user, or give up.

## 2.6 — Tools vs Resources vs Prompts, more precisely

We met these in Chapter 1 as "verbs, nouns, recipes." Here are the precise
semantic differences.

|                  | **Tools**                          | **Resources**                       | **Prompts**                              |
|------------------|-------------------------------------|--------------------------------------|-------------------------------------------|
| Who invokes      | The model                           | The host (often after user action)   | The user                                  |
| Side effects?    | Allowed and common                  | Should be a pure read                | Pure                                       |
| Has arguments?   | Yes, JSON-Schema'd                  | Identified by URI                    | Yes, typed arguments                       |
| Discovery method | `tools/list`                        | `resources/list`                     | `prompts/list`                             |
| Invocation method| `tools/call`                        | `resources/read`                     | `prompts/get`                              |
| Output           | `content[]`                         | `contents[]` (text or blob)          | A list of messages to inject               |
| Typical UI       | LLM decides automatically           | "Attach a resource" picker           | Slash-command menu                         |

A useful mental model: **tools change the world, resources describe the world,
prompts shape the conversation.**

This repo only uses tools. Semantic Scholar is *external* state, the model wants
to *query* that state, so tools are the right primitive. You could imagine a
future version exposing each paper as a resource (URI like `paper://abc123`) or
a "summarise-paper" prompt; but tools alone are sufficient and that's where the
authors stopped.

## 2.7 — FastMCP, the high-level Python API

Writing the JSON-RPC by hand would be painful. The official Python SDK ships
two layers:

- `mcp.server.Server` — the low-level layer, where you register handlers for
  each method (`@server.list_tools()`, `@server.call_tool()`).
- `mcp.server.fastmcp.FastMCP` — a high-level decorator API inspired by FastAPI.

`FastMCP` lets you write:

```python
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("my-server")

@mcp.tool()
async def add(a: int, b: int) -> int:
    """Add two numbers."""
    return a + b

if __name__ == "__main__":
    mcp.run(transport="stdio")
```

That tiny snippet:

- Creates a server named `my-server`.
- Registers an `add` tool. The framework reads the function signature, builds a
  JSON schema (`{ "a": int, "b": int }`, both required), and uses the docstring
  as the description.
- Runs over stdio.

Everything in `semantic_scholar_server.py` is a variation on this pattern. When
you read Chapter 5 you'll see it's almost depressing how little code there is.
FastMCP carries the protocol weight.

## 2.8 — Things FastMCP does that you don't see

Worth knowing, so you don't think the file is doing something it isn't:

- **Schema generation** from your type hints, including support for `pydantic`
  models, enums, `Optional`, default values, etc.
- **Validation** of incoming arguments against that schema before your function
  runs. If the model sends `num_results: "ten"`, you'll never see it — the
  framework will return an error.
- **Async dispatch.** Tools can be sync or async. FastMCP awaits async ones
  directly; sync ones it offloads. The Semantic Scholar `python` package is
  sync, so this repo uses `asyncio.to_thread` inside its async tool to avoid
  blocking the event loop. We'll revisit that in Chapter 5.
- **Error handling.** Exceptions become `isError: true` content automatically.
- **Logging facility.** It hooks into MCP's `logging/setLevel` so the host can
  request more or less verbose logs.

## 2.9 — A minimal but real handshake, end to end

To cement the picture, here is a complete, realistic transcript of a session in
which the host connects, lists tools, calls one, and shuts down. Lines marked
`H>` are host → server; `S>` are server → host.

```
H> {"jsonrpc":"2.0","id":1,"method":"initialize",
    "params":{"protocolVersion":"2024-11-05",
              "capabilities":{},
              "clientInfo":{"name":"Claude Desktop","version":"x.y"}}}

S> {"jsonrpc":"2.0","id":1,
    "result":{"protocolVersion":"2024-11-05",
              "capabilities":{"tools":{}},
              "serverInfo":{"name":"semanticscholar","version":"..."}}}

H> {"jsonrpc":"2.0","method":"notifications/initialized"}

H> {"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}

S> {"jsonrpc":"2.0","id":2,"result":{"tools":[ ...four tools... ]}}

H> {"jsonrpc":"2.0","id":3,"method":"tools/call",
    "params":{"name":"search_semantic_scholar",
              "arguments":{"query":"diffusion models","num_results":3}}}

S> {"jsonrpc":"2.0","id":3,
    "result":{"content":[{"type":"text","text":"[ ...three papers... ]"}],
              "isError":false}}

# (Host closes stdin; server exits.)
```

Read that transcript twice. The whole rest of this book is variations on it.

## 2.10 — What's next

We now have:

- **A purpose** (Chapter 1): plug-and-play tool exposure for LLMs.
- **A protocol** (this chapter): JSON-RPC 2.0 over stdio, with a handful of
  named methods organized into init/operate/shutdown.
- **A Python SDK** (`FastMCP`) that turns decorated functions into tools.

In Chapter 3 we use all of that to build a generic toy MCP server from scratch —
not Semantic Scholar yet, just a tiny "calculator" or "weather" server — and
hook it up to Claude Desktop so you see the loop work end to end.
