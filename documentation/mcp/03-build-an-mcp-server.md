# Chapter 3 — Building Your Own MCP Server

The goal of this chapter is to get you to the moment where Claude Desktop is
talking to a server *you wrote*. Not Semantic Scholar yet — something dumb and
fast, so that the *process* sinks in. Once that loop is closed in your head, the
Semantic Scholar server in Chapters 4–6 will read like a special case of the
pattern you already know.

We'll build a tiny "personal notebook" server: it can save a note, list notes,
and read one back. Three tools, ~40 lines of Python.

## 3.1 — Setup

You need Python 3.10+ and the `mcp` package. From any directory:

```bash
python3 -m venv .venv
source .venv/bin/activate         # on Windows: .venv\Scripts\activate
pip install mcp
```

That's all. You don't need a framework, a database, or a port. The MCP package
brings `FastMCP`, the decorator-based server we met in Chapter 2.

## 3.2 — The server: `notebook_server.py`

Create a file called `notebook_server.py`:

```python
from mcp.server.fastmcp import FastMCP
from pathlib import Path

NOTES_DIR = Path.home() / "mcp_notes"
NOTES_DIR.mkdir(exist_ok=True)

mcp = FastMCP("notebook")

@mcp.tool()
def save_note(title: str, body: str) -> str:
    """Save a note to the user's notebook.

    Args:
        title: A short title used as the filename (no extension).
        body:  The full text of the note.

    Returns:
        A confirmation message.
    """
    (NOTES_DIR / f"{title}.md").write_text(body, encoding="utf-8")
    return f"Saved note '{title}'."

@mcp.tool()
def list_notes() -> list[str]:
    """List the titles of every saved note."""
    return [p.stem for p in NOTES_DIR.glob("*.md")]

@mcp.tool()
def read_note(title: str) -> str:
    """Read the body of a saved note.

    Args:
        title: The title of the note to read.
    """
    path = NOTES_DIR / f"{title}.md"
    if not path.exists():
        return f"No note titled '{title}'."
    return path.read_text(encoding="utf-8")

if __name__ == "__main__":
    mcp.run(transport="stdio")
```

That is a complete, working MCP server.

Let's walk through it like a code review.

### `mcp = FastMCP("notebook")`

Creates the server object. The string `"notebook"` is what shows up in the
host's "connected servers" list. It's purely cosmetic but the host displays it,
so make it something a human will recognize.

### `@mcp.tool()`

This is where the magic happens. The decorator:

- Reads the function name → that's the tool name.
- Reads the **type hints** → that's the JSON schema. `str` becomes
  `{"type": "string"}`, `list[str]` becomes `{"type": "array", "items": {...}}`,
  and so on.
- Reads the **docstring** → that's the description the model sees. The
  `Args:` section is also parsed for per-argument descriptions in some
  configurations.
- Reads the **default values** → those become `default` in the schema, and the
  argument is no longer in `required`.

You get all of this for free. There is no separate schema to keep in sync.
**That single fact is FastMCP's reason for existing.**

### Return values

You can return any JSON-serialisable Python value. FastMCP wraps it into the
right MCP `content` shape. If you want fine control, you can return MCP-native
`TextContent` / `ImageContent` objects, but plain strings, dicts, and lists
work for almost everything.

### `mcp.run(transport="stdio")`

The blocking call that starts the event loop, reads requests from stdin, and
writes responses to stdout. Anything you print to `print(...)` goes to stdout
and **breaks the protocol** because stdout is the transport channel. Use
`logging` instead — it goes to stderr by default.

> **Common newbie crash**: you `print("DEBUG:", x)` inside a tool, the host
> sees garbage on the JSON-RPC stream, and the server appears to "not work."
> Always log, never print, in stdio-transport servers.

## 3.3 — Try it without a host first

Before plugging it into Claude, let's verify the server actually starts. From a
terminal:

```bash
python notebook_server.py
```

The process will start and *appear to hang*. That's correct — it's waiting for
JSON-RPC on stdin. To prove it's alive, paste this single line and press Enter:

```
{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"manual","version":"0"}}}
```

You should see an `initialize` response on stdout. If you do, the server is
healthy. Press `Ctrl-C` to stop it.

## 3.4 — Hook it into Claude Desktop

Find your `claude_desktop_config.json`:

- macOS: `~/Library/Application Support/Claude/claude_desktop_config.json`
- Windows: `%APPDATA%\Claude\claude_desktop_config.json`

Add (or extend) the `mcpServers` section:

```json
{
  "mcpServers": {
    "notebook": {
      "command": "/absolute/path/to/.venv/bin/python",
      "args": ["/absolute/path/to/notebook_server.py"]
    }
  }
}
```

Two things people get wrong here:

1. **Use absolute paths.** Claude Desktop launches the command without your
   shell's PATH, so `python` alone may not resolve, and `~` will not expand.
2. **Use the Python from the venv where `mcp` is installed.** That's why we
   wrote `.venv/bin/python` and not `python3`.

Restart Claude Desktop. In the bottom right of a chat you should see a hammer
or plug icon indicating connected MCP servers. Open it and confirm
`notebook` appears with three tools.

Now try it. Ask Claude:

> *Save a note titled "groceries" with the body "milk, eggs, bread", then list
> my notes.*

You'll see Claude call `save_note`, then `list_notes`. The result is your
note actually appearing on disk in `~/mcp_notes/groceries.md`. The first time
that loop closes is genuinely magical. Take a beat.

## 3.5 — Sync vs async tools

The `notebook_server.py` tools above are all *synchronous* — plain `def`
functions. That's fine when your tool's body is fast (a few file ops). But what
if a tool needs to make an HTTP call that might take seconds?

If you write the tool as `async def`, FastMCP awaits it directly inside its
event loop, and other tool calls can interleave. But if you write a *sync*
function that blocks (say, `requests.get(...)`), it will freeze the entire
server's event loop until it returns. That's usually fine for a single user, but
it's still a code smell and it matters more under load.

The clean pattern, and the one this repo's Semantic Scholar server uses, is:

```python
@mcp.tool()
async def search(query: str) -> list[dict]:
    """..."""
    return await asyncio.to_thread(blocking_search, query)
```

`asyncio.to_thread` runs the blocking call in a worker thread and returns an
awaitable, so the event loop stays responsive. Use this whenever you have to
call a sync HTTP client (or the `semanticscholar` package, which is sync) from
inside an async tool.

## 3.6 — Surfacing errors gracefully

When the network is down, your tool will raise. FastMCP turns the exception
into an `isError: true` response, which means the model sees something like:

> Tool `search` returned an error: ConnectionError: ...

The model will usually adapt: apologise to the user, try again, or pick a
different tool. But you can do better by *catching* and returning a friendly,
structured error:

```python
@mcp.tool()
async def search(query: str) -> list[dict] | dict:
    try:
        return await asyncio.to_thread(blocking_search, query)
    except Exception as e:
        return {"error": f"Search failed: {e}"}
```

The Semantic Scholar server does exactly this — every tool wraps its body in a
`try/except` and returns an `{"error": "..."}` dict on failure. The model can
read the error, decide whether to retry with different arguments, and tell the
user gracefully.

This is a judgment call. Letting exceptions propagate is *also* fine; it gives
the model a louder signal that something is broken. Pick one style and apply
it consistently.

## 3.7 — Choosing good tool surface

The mechanical part of building an MCP server is easy. The hard, *interesting*
part is the design question:

> **Which functions should I expose, with which arguments, named how, described
> how?**

Some rules I've found useful:

1. **Match the model's vocabulary, not your codebase's.** The model knows the
   word "search"; it may not know your internal name "lookupV2Coalesced".
2. **Prefer fewer, more powerful tools over many fiddly ones.** A `search_paper`
   tool with an optional `year` filter is better than `search_paper_by_year`
   and `search_paper_recent` as two separate tools — fewer choices for the
   model to confuse.
3. **Give honest, specific descriptions.** "Searches Semantic Scholar, which
   indexes most published computer science and biomedical papers but is
   incomplete for the humanities" is more useful than "Searches papers."
4. **Return *structured* data when possible.** Lists of dicts. Models can read
   plain prose, but they reason much better over consistent fields.
5. **Make IDs explicit and round-trippable.** If `search` returns paper IDs
   in a `paperId` field, then `get_paper_details` should take a `paper_id`
   argument named the obvious thing. Models notice the symmetry.
6. **Don't expose write tools without thinking.** Tools can have side effects.
   "Send email" is a tool. "Delete file" is a tool. The host has permission
   UI, but you the server author are still responsible for not building a
   foot-gun.

The Semantic Scholar repo follows all of these. Chapter 4 walks through the
specific design choices.

## 3.8 — Beyond tools

Once you're comfortable with tools, the same `FastMCP` object exposes:

```python
@mcp.resource("note://{title}")
def read_note_resource(title: str) -> str:
    ...

@mcp.prompt()
def summarise_notes() -> list[Message]:
    ...
```

These map to `resources/*` and `prompts/*` in the protocol. The repo we're
studying doesn't use either, so we won't dwell on them — but now you know how
to find them when you want them.

## 3.9 — What you should be able to do now

After this chapter you should be able to:

- Stand up a brand-new MCP server in a single Python file.
- Plug it into Claude Desktop via `claude_desktop_config.json`.
- Watch the model invoke your tools.
- Debug it: read stderr logs, fix print/log mistakes, handle exceptions.

If you can do that with the notebook server, the rest of the book is bookkeeping.
In Chapter 4 we'll walk through the *design* of an MCP server for Semantic
Scholar — what tools to build, what arguments they should take, what to return —
before we look at how this repo actually wrote it in Chapter 5.
