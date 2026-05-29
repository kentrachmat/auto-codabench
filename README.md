---
title: AutoCodabench
emoji: 🧪
colorFrom: indigo
colorTo: blue
sdk: docker
app_port: 7860
pinned: false
license: mit
short_description: Chat assistant for designing Codabench competitions.
---

# AutoCodabench

A scientific-friend chat assistant that takes a one-line competition
idea and walks you through two phases to a ready-to-upload Codabench
`.zip`:

1. **📝 Plan** — short, citation-grounded design conversation. Saves
   `implementation_plan.md` covering task / data / metric / baseline /
   rules / ethics / schedule.
2. **📦 Competition Creation** — a fresh agent reads the locked plan
   and packages `competition.yaml`, scoring program, baseline, pages,
   into a Codabench-shaped `.zip`. One-click upload to Codabench
   returns the live competition URL.

> **This `README.md` is also the Hugging Face Spaces metadata file** —
> the YAML header above configures the Space (Docker SDK, port 7860).
> Don't delete it on the HF side; edit prose freely below.

## Where to look

| You are… | Read |
|----------|------|
| Trying the Web UI (Space or `chainlit run`) | [`auto_codabench/INSTRUCTION_FOR_USER.md`](auto_codabench/INSTRUCTION_FOR_USER.md) §A |
| Wiring the CLI (Claude Desktop / Claude Code) | [`auto_codabench/INSTRUCTION_FOR_USER.md`](auto_codabench/INSTRUCTION_FOR_USER.md) §B |
| Deploying the HF Space (operator) | [`web/README.md`](web/README.md) |
| Hacking the package internals | [`auto_codabench/README.md`](auto_codabench/README.md) |
| **System diagrams** (architecture + Phase 1→2 sequence) | [`auto_codabench/README.md` § System diagrams](auto_codabench/README.md#system-diagrams) |
| **Skill provenance** (where each `SKILL.md` came from, why) | [`auto_codabench/skills/<name>/README.md`](auto_codabench/skills/) — one per skill |
| Test harness around the package | [`experiments/bundle_creation_test/README.md`](experiments/bundle_creation_test/README.md) |

## What's where

| Path | What it is |
|------|------------|
| `web/` | Chainlit app — the chat UI deployed by this Space. |
| `auto_codabench/` | The autocodabench MCP server + skill files + bundle output. |
| `documentation/codabench_bundle_upload/` | Reference Codabench REST-API upload helper (the `upload_zip` function in `auto_codabench/mcp_server/tools/upload.py` imports from here). |
| `Dockerfile` | Used by HF Spaces to build the image. |
| OpenAlex MCP | Installed from upstream `git+https://github.com/drAbreu/alex-mcp.git@v4.8.2` by the Dockerfile and locally — not vendored. |

## What you need (operator)

- An **Anthropic API key** (Anthropic API is *separate* from Claude
  Max / Pro — set `ANTHROPIC_API_KEY` in HF Repository Secrets).
- An email for the OpenAlex polite-pool (`OPENALEX_MAILTO`).
- A shared password (`SHARED_PASSWORD`) gating the UI for invited
  collaborators.
- A Chainlit auth secret (`CHAINLIT_AUTH_SECRET`; run
  `chainlit create-secret` once).

Codabench credentials are no longer required as Repository Secrets —
the Web UI's Publish form takes them from the user directly.
Optionally set `CODABENCH_USERNAME` / `CODABENCH_PASSWORD` for CLI
uploads via `autocodabench_upload_bundle`.

See [`web/README.md`](web/README.md) §2 for the full deploy checklist.

## How it talks to itself

```
  Browser ─► Chainlit UI ─► ClaudeSDKClient ─► api.anthropic.com
                          │
                          ├─► subprocess: python -m auto_codabench.mcp_server.server
                          │      (open run, write plan, write bundle, validate, zip)
                          ├─► subprocess: python -m alex_mcp.server
                          │      (OpenAlex / PubMed / ORCID lookups)
                          └─► POST /ac/upload-codabench  (FastAPI route mounted on
                                 the same app — direct upload, no LLM cost)
```

## License

MIT.
