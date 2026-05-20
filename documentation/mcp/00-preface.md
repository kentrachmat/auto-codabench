# Preface — How to Read This Book

You picked up this repo because you wanted to know what **MCP** actually is. Maybe
you've seen the acronym on Twitter, or in a config file, or someone told you "just
add an MCP server" and you nodded but didn't really know what they meant.

This book is the answer to that. It is written for one specific reader: someone
who is comfortable with code but who has never been told *clearly* what MCP is,
what it isn't, why it exists, and how a tiny 130-line Python file like
`semantic_scholar_server.py` ends up giving Claude the power to search academic
papers.

By the end of this book you should be able to:

1. Explain MCP to a friend in three sentences, without using jargon.
2. Read an MCP server's source code and predict what it will do.
3. Write your own MCP server for an API you care about.
4. Trace a single user message — *"find me recent papers about diffusion models"* —
   from the moment you press Enter in Claude all the way to the Semantic Scholar
   HTTP servers and back, and know exactly what happened at every hop.

---

## The chapters

The chapters are deliberately ordered so each one *earns* the next. You can read
out of order, but you'll get the most out of them in sequence.

| #  | File                                  | What it gives you |
|----|---------------------------------------|-------------------|
| 1  | `01-mcp-intuitive.md`                 | The "why MCP exists" intuition, with analogies. No code. |
| 2  | `02-mcp-technical.md`                 | The actual protocol: JSON-RPC, transports, the three primitives (tools, resources, prompts), the handshake. |
| 3  | `03-build-an-mcp-server.md`           | A from-zero tutorial: build a toy MCP server in ~30 lines, then run it against Claude Desktop. |
| 4  | `04-design-a-semantic-scholar-server.md` | Step into the shoes of someone designing *this* repo from scratch. Which tools to expose? What types? What's a paper ID? |
| 5  | `05-this-repo-walkthrough.md`         | Line-by-line tour of `semantic_scholar_server.py` and `semantic_scholar_search.py`. |
| 6  | `06-call-flowchart.md`                | A single end-to-end trace: from Claude's UI → MCP → Python → Semantic Scholar's API → back. Diagrams included. |

---

## How the chapters relate

```
            ┌────────────────────────────┐
            │ Ch 1: Intuition            │
            │ (no code, just the story)  │
            └─────────────┬──────────────┘
                          │
            ┌─────────────▼──────────────┐
            │ Ch 2: The Protocol         │
            │ (JSON-RPC, handshake,      │
            │  tools/resources/prompts)  │
            └─────────────┬──────────────┘
                          │
            ┌─────────────▼──────────────┐
            │ Ch 3: Build a toy server   │
            │ (generic, not SS yet)      │
            └─────────────┬──────────────┘
                          │
            ┌─────────────▼──────────────┐
            │ Ch 4: Design for SS        │
            │ (what would YOU do?)       │
            └─────────────┬──────────────┘
                          │
            ┌─────────────▼──────────────┐
            │ Ch 5: This repo's code     │
            │ (what they actually did)   │
            └─────────────┬──────────────┘
                          │
            ┌─────────────▼──────────────┐
            │ Ch 6: End-to-end trace     │
            │ (the whole journey)        │
            └────────────────────────────┘
```

Chapters 1 and 2 are about MCP-the-idea. Chapter 3 is about building any MCP
server. Chapters 4–6 zoom in on this repo specifically.

---

## A note on style

I will try to do what Grant Sanderson (3blue1brown) does: explain things by
showing you the *problem the abstraction solves*, not by defining the abstraction
first. The reason MCP feels mysterious to most people is that it's usually
introduced as a list of features ("it has tools, resources, prompts, and
sampling") before you ever see a problem that needs those features. We'll go in
the other direction.

When I say "Claude" I mean any model running inside an MCP host. The same story
works for Cursor, Windsurf, Cline, or any other host.

Open Chapter 1 when you're ready.
