# Chapter 1 — MCP, Intuitively

*No code in this chapter. Just the idea.*

## 1.1 — A language model is a brain in a jar

Imagine a brilliant person. They have read most of the public internet up to some
cutoff date. They can write code, summarize papers, plan trips, draft emails.

But here is the catch: they are sealed inside a glass jar. They cannot see your
files. They cannot Google anything. They cannot check today's weather. They
cannot open your email. Anything that happened after their training cutoff is
invisible to them. Anything *private* to you — your calendar, your codebase, your
company's database — was never in their training data and never will be.

This is a large language model. Brilliant, but isolated. The most important
single fact about LLMs is that **they only know what you put in the context window
right now**. Everything else is a hallucination waiting to happen.

So how do you put real, fresh, private, *useful* information into the jar?

You hand it through a slot. You write some code that says "look up this user's
calendar, then paste the answer into the prompt." That works. Now do it for
email. Now do it for GitHub. Now do it for Jira. Now do it for the local
filesystem. Now for the company wiki. Now for Stripe. Now for the database.

Every one of those slots is custom-built. Different shape, different schema,
different glue code. You wrote them for Claude — but next month you also want to
use them with GPT-4 in Cursor. And with Llama in your own app. So you write them
again. And again.

This is the **N × M problem**: N AI applications times M tools equals N×M custom
integrations. It does not scale.

## 1.2 — The USB-C insight

Look at the back of any laptop made in the last ten years. Notice how almost
everything plugs into the same little oval port. Monitors. Phones. Hard drives.
Headphones. Power. You don't have a special "monitor port" and a special "hard
drive port" and a special "headphone port" anymore. You have *one* port, and the
protocol behind it (USB-C / Thunderbolt) is rich enough that the laptop can ask
each device "what are you, and what can you do?" and react accordingly.

USB-C didn't make laptops smarter. It made the world *pluggable*.

MCP is the same idea for LLMs.

> MCP — the **Model Context Protocol** — is a standard way for an AI app to
> discover and use external tools and data, without the app and the tool ever
> having to know about each other in advance.

You write one MCP server for, say, Semantic Scholar. From that moment on, *any*
MCP-aware host — Claude Desktop, Cursor, Windsurf, Cline, a custom in-house chat
app — can plug it in and the model gains the ability to search papers. You did
not write four integrations. You wrote one.

That is the entire pitch. The rest of the chapter is just unpacking what
"discovery" and "use" really mean.

## 1.3 — The three roles

There are three roles in an MCP system. It is *very* worth slowing down on these
because almost every confusion later traces back to mixing them up.

```
   ┌────────────────┐    ┌─────────────┐    ┌─────────────┐
   │  HOST          │    │  CLIENT     │    │  SERVER     │
   │  (the app you  │◄──►│ (the MCP    │◄──►│ (your tool  │
   │   are using)   │    │  plumbing)  │    │  provider)  │
   └────────────────┘    └─────────────┘    └─────────────┘
```

- **Host** — the application the *human* is using. Claude Desktop. Cursor. Your
  own chatbot. The host is the thing with the chat UI. The host also runs the
  language model (or talks to the model API). Crucially, the host is *in charge*:
  it decides when to call a tool, it asks the user for permission, it shows
  results.
- **Server** — a separate process that exposes one or more capabilities ("search
  papers", "read this file", "create a Linear ticket"). The server is dumb about
  AI; it just answers questions in the MCP protocol. The Semantic Scholar
  server in this repo is one of these.
- **Client** — a small piece of code *inside the host* that speaks MCP to one
  specific server. If the host has three MCP servers configured, it has three
  clients running. You almost never write a client by hand; the SDK does it.

The mental picture I want you to lock in:

> **One host can have many servers.** The host is your AI app; each server is a
> capability you've plugged into it. The "client" is the wire between them.

If you've ever used a browser with extensions, the analogy is exact:

| Browser term       | MCP term         |
|--------------------|------------------|
| Browser (Chrome)   | Host             |
| Extension          | Server           |
| Extension API      | The MCP protocol |

Extensions don't know about each other. Chrome doesn't know what any specific
extension does until it asks. New extensions can appear at any time. You write
an extension once and it works in every Chromium browser. Same energy.

## 1.4 — What does a server actually offer?

When a host connects to a server, it asks: "What have you got?"

A server can offer three kinds of things. These are the **MCP primitives**:

### Tools — verbs

A tool is a function the model can call. `search_semantic_scholar(query)`. The
model sees a name, a description, and a JSON schema for the arguments. When the
model decides "I want to search for papers about transformers", the host runs the
tool and feeds the result back into the conversation.

Tools are *model-controlled*. The model picks when to use them.

### Resources — nouns

A resource is a piece of data the host can read and inject into context. "Here is
the contents of `README.md`." "Here is the row from the database for user 42."
"Here is the PDF of paper 1706.03762."

Resources are *application-controlled*. Usually the user or the host decides to
attach a resource. The model doesn't go off and read random resources on its own.

### Prompts — recipes

A prompt is a reusable template the user can invoke. "Summarize-paper" might be
a prompt that takes a paper ID and produces a structured analysis. The user
picks it from a menu (often as a slash command). It's a way for the server to
ship *known good prompt patterns* alongside the data and tools.

Prompts are *user-controlled*.

Most servers, including the one in this repo, only implement tools. Tools are the
star of the show. Resources and prompts are powerful but optional.

A nice way to remember it:

| Primitive  | Who triggers it | Real-world analogy             |
|------------|-----------------|---------------------------------|
| Tool       | The model       | A function call                 |
| Resource   | The host/app    | An attachment (`@file.pdf`)     |
| Prompt     | The user        | A slash command (`/summarize`)  |

## 1.5 — A worked walkthrough, no code

Let's pretend you've installed the Semantic Scholar MCP server inside Claude
Desktop, and you ask:

> *"Find me three recent papers about retrieval-augmented generation, and tell
> me which one has the most citations."*

Here is what happens, in plain English:

1. **Claude Desktop boots.** It reads its config file and sees one MCP server is
   configured: `semanticscholar`, started with `python semantic_scholar_server.py`.
   It launches that as a subprocess.
2. **Handshake.** Claude Desktop and the server briefly introduce themselves. The
   host says "hi, I'm Claude Desktop version X." The server says "hi, I support
   the tools primitive."
3. **Tool discovery.** The host asks "what tools do you have?" The server replies
   with the list:
   - `search_semantic_scholar`
   - `get_semantic_scholar_paper_details`
   - `get_semantic_scholar_author_details`
   - `get_semantic_scholar_citations_and_references`
   along with a description and an argument schema for each.
4. **You type your question.** The host sends your message to the language model
   *along with the list of tools it now knows about*. Critically, this is the
   same mechanism as any other tool-use call to the model — the host has just
   collected the tool definitions from the MCP server instead of hard-coding
   them.
5. **The model picks a tool.** Claude reads the tool list, decides
   `search_semantic_scholar` is the right thing, and emits a tool call:
   `search_semantic_scholar(query="retrieval-augmented generation",
   num_results=3)`.
6. **The host runs the tool, via MCP.** The host doesn't *execute* the Python
   function itself. It tells the MCP server "please call this tool with these
   arguments." The server runs the function, which hits the Semantic Scholar
   HTTP API, gets a list of papers, and returns them.
7. **The result is fed back to the model.** Claude now has three real, current,
   structured paper records in its context.
8. **The model writes its answer.** It compares citation counts, picks the
   winner, and writes the response you actually see in the UI.

Notice three things about this walkthrough:

- **The language model never spoke to Semantic Scholar.** It only spoke to its
  host. The MCP server is what spoke to Semantic Scholar.
- **The host is the orchestrator.** It runs the loop. It calls the model. It
  routes tool calls. It enforces permissions.
- **The model could have refused.** If the question hadn't needed a paper search,
  Claude would simply not have called the tool, and the MCP server would have
  sat idle.

## 1.6 — What MCP is *not*

A surprising amount of confusion vanishes if we say what MCP is *not*:

- **MCP is not an LLM.** No models live inside it. It is plumbing.
- **MCP is not a web framework.** A server is just a process; it doesn't even
  need to listen on a port (the most common transport is stdin/stdout).
- **MCP is not a replacement for the model's tool-use feature.** It *uses* tool
  use. It is a layer on top: a standard way to *deliver* tool definitions and
  execute tool calls.
- **MCP is not specific to Anthropic or Claude.** Anthropic created and open-
  sourced it, but it is model-agnostic and has been adopted by many other
  hosts. A non-Anthropic model running in an MCP-aware host can use MCP
  servers identically.
- **MCP is not magic; it is a thin JSON-RPC protocol.** When you actually look
  at the wire format in the next chapter, you may be surprised how small it is.

## 1.7 — Why this is a big deal (and why it's also, honestly, kind of simple)

If you're feeling a little underwhelmed — "wait, that's it? It's just an agreed-
upon way to expose functions to a chat app?" — then you have *exactly* the right
intuition. That is *literally* it.

The reason MCP feels important is not the technology. The technology is
deliberately small and boring. The reason it matters is the network effect: once
a critical mass of hosts and servers all speak the same protocol, capability
becomes a thing you can install rather than a thing you have to engineer. The
moment someone publishes a "Linear MCP server", every MCP host on Earth can
suddenly create Linear tickets. That is what USB-C did for hardware, and that is
what MCP is doing for LLM tooling.

The small, boring technology is what makes the network effect possible — because
small, boring technologies are the only kind that ever achieve standardization.

---

In the next chapter we lift the hood. You'll see the actual JSON messages flying
back and forth, learn what "stdio transport" really means, and meet `FastMCP` —
the Python helper this repo uses to turn a decorated function into a
fully-fledged MCP tool in three lines.
