# An execution trace of `autocodabench validate-bundle`

This document presents a complete execution trace of the command

```bash
autocodabench validate-bundle experiments/bundle_creation_test/competitions/style-trans-fair/ground_truth/bundle
```

from shell invocation to process exit code. It is written for a reader who
has not previously studied this codebase. Each stage names the relevant
file and function so that the trace can be followed in an editor, or
stepped through interactively: set a breakpoint at each stage's anchor and
use the `validate:` configurations in `.vscode/launch.json` (F5 to launch,
F11 to step into a call, and the Call Stack panel to observe the current
position).

> Line numbers cited below are anchors as of the commit that added this
> document; they drift as code changes, but the function names will not.

---

## Overview: the call graph

```
shell: autocodabench validate-bundle <bundle>
  └─ cli/main.py: main()                     ← console-script entry point
       ├─ auth.load_dotenv()                 ← read <cwd>/.env (never overrides real env)
       ├─ argparse: _build_parser()          ← parse subcommand + flags
       └─ _cmd_validate()
            ├─ [--judged only] auth preflight
            └─ checks/api.py: validate_bundle_path()
                 ├─ resolve dir-or-zip
                 ├─ facts.py: CompetitionFacts.discover()
                 ├─ base.py: CheckContext.from_bundle_dir()   ← parse competition.yaml ONCE
                 ├─ base.py: run_checks()                     ← loop the check registry
                 │    ├─ deterministic.py: 11 checks (gates + findings)
                 │    │    └─ SchemaLint → core/bundle_io.py: validate_bundle()
                 │    └─ attestations.py: 5 human-only boxes
                 ├─ [--judged only] judged.py: run_judged_checks()
                 └─ report.py: ValidationReport
            └─ print markdown or JSON → exit 0 (ok) / 1 (gated)
```

Two design properties organize everything that follows:

1. **`competition.yaml` is parsed exactly once** into a plain dictionary,
   and every check reads from that dictionary (together with the bundle
   directory, for questions of file existence). There is no other shared
   state.
2. **Checks occupy three epistemic tiers**, and the tiers never mix:
   *deterministic* (code computes PASS/FAIL — the only tier that can
   gate), *judged* (an LLM grades a rubric — advisory FINDINGs only), and
   *attestation* (criteria only a human can verify — surfaced as unchecked
   boxes).

---

## 1. Stage 0 — from shell to Python

`autocodabench` is not a shell script. `pyproject.toml` declares:

```toml
[project.scripts]
autocodabench = "autocodabench.cli.main:main"
```

When the package is installed with `pip install -e .`, pip writes a small
launcher onto the `PATH` that imports `autocodabench.cli.main` and calls
`main()`. The first line of project code that executes is therefore
`main` in `src/autocodabench/cli/main.py`.

(`validate-bundle` is a subcommand of the single `autocodabench` entry
point, so the validator behaves as a standalone tool that can be pointed
at any bundle, whether hand-written or generated. `validate` is retained
as a back-compatible alias for the same subcommand.)

## 2. Stage 1 — CLI: argument parsing, `.env` loading, and dispatch

**File: `src/autocodabench/cli/main.py`**

```python
def main(argv=None) -> int:
    from ..auth import load_dotenv
    load_dotenv()                     # <cwd>/.env, if present
    args = _build_parser().parse_args(argv)
    return args.func(args)            # → _cmd_validate for the validate-bundle subcommand
```

`_build_parser()` registers the subcommand and its flag surface:

```python
p = sub.add_parser("validate-bundle", aliases=["validate"], ...)
_add_validate_args(p)                 # bundle, --facts, --judged, --backend, --model, --json
p.set_defaults(func=_cmd_validate)
```

- `_add_validate_args` defines the flag surface. With no flags, the
  invocation follows the fully **keyless** path: deterministic and
  attestation tiers only, with no LLM and no network access.
- `load_dotenv()` (`auth.py`) is a small (~20-line) stdlib-only parser:
  it reads `KEY=VALUE` lines from `./.env`, tolerating `export` prefixes,
  quotes, and comments, and **never overrides** a variable already set in
  the real environment. This is the mechanism by which an
  `ANTHROPIC_API_KEY=...` entry in a `.env` file becomes visible to the
  `--judged` path.

Control then passes to `_cmd_validate(args)`:

```python
def _cmd_validate(args) -> int:
    from ..checks import validate_bundle_path
    if args.judged and not _require_live_claude_auth(args.backend):
        return 2                                          # refused before any work
    backend = None
    if args.judged and (args.backend or args.model):
        from ..backends import resolve_backend
        backend = resolve_backend(args.backend, model=args.model)
    report = validate_bundle_path(args.bundle, facts_path=args.facts,
                                  judged=args.judged, backend=backend)
    print(report.to_markdown())       # or json.dumps(report.to_dict()) with --json
    return 0 if report.ok else 1
```

Three details merit attention:

- **The auth preflight fires only for `--judged` on the Claude backend.**
  `ensure_live_auth()` (`auth.py`) checks for a subscription login or an
  `ANTHROPIC_API_KEY`; if neither exists and the process is attached to an
  interactive terminal, it guides the user through logging in or pasting a
  key (input hidden, with an optional save to `./.env`). Non-interactive
  contexts receive an explicit refusal with guidance and exit code 2,
  rather than an opaque SDK error well into the run.
- `--backend ollama:llama3.1` (or `openai:…`, or a URL) routes the judged
  tier through `backends/openai_compat.py` instead of Claude. The backend
  is treated as a *measured variable*, not a hard binding.
- Imports are deliberately lazy (placed inside the function): the keyless
  path never imports the SDK, which is what keeps continuous-integration
  and reviewer machines free of credentials.

## 3. Stage 2 — entering the check framework

**File: `src/autocodabench/checks/api.py`**

`validate_bundle_path` is a one-line synchronous wrapper:

```python
def validate_bundle_path(bundle, *, facts_path=None, judged=False, backend=None):
    return asyncio.run(validate_bundle_path_async(...))
```

(The implementation is asynchronous underneath because judged checks
await an LLM; the deterministic path runs through the event loop without
ever yielding.)

`validate_bundle_path_async` begins with the module's most important
non-obvious lines — the **imports that register the checks**:

```python
from . import attestations as _attestations  # noqa: F401
from . import deterministic as _deterministic  # noqa: F401
from . import judged as _judged  # noqa: F401
```

Importing those modules executes every `@register` decorator
(`base.py:146`), which **instantiates each check class and stores the
instance** in the module-global `REGISTRY` dictionary, keyed by check id.
By the time any function in `api.py` runs, the registry is fully
populated. No registration happens at validation time; when stepping
through with a debugger, this has already occurred during import.

The bundle argument is then resolved (`api.py:30-49`):

- a **directory** is used as-is;
- a **`.zip`** is extracted into a `tempfile.TemporaryDirectory` (cleaned
  up in the `finally:` block at the end), and `_locate_bundle_root`
  handles a common packaging error — zipping the *containing folder*: if
  `competition.yaml` is not at the zip root but exactly one subdirectory
  contains it, that subdirectory is used.

## 4. Stage 3 — constructing the context (the only parse)

**Files: `checks/facts.py`, then `checks/base.py`**

```python
facts = CompetitionFacts.discover(bundle_dir, facts_path)
ctx = CheckContext.from_bundle_dir(bundle_dir, facts=facts)
```

`CompetitionFacts` (`facts.py`) implements the **declare-then-verify**
side channel: a six-field dataclass (`anticipated_error_rate`,
`test_set_size`, `unit_of_generalization`, `external_data_allowed`,
`prizes`, `task_type`) loaded from `--facts <file>`, else from
`<bundle>/competition_facts.yaml`, else left empty. Some checklist items
are unverifiable from the bundle alone — rather than guessing, checks
that need such a fact *consume a declaration* and verify against it.
Unknown keys in the YAML are a hard error, so typographical mistakes
cannot pass silently.

`CheckContext.from_bundle_dir` (`base.py:74-84`) is where the bundle's
configuration becomes data:

```python
comp = None
yaml_path = bundle_dir / "competition.yaml"
if yaml_path.is_file():
    try:
        loaded = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
        comp = loaded if isinstance(loaded, dict) else None
    except yaml.YAMLError:
        comp = None
return cls(bundle_dir=bundle_dir, comp=comp, facts=facts or CompetitionFacts())
```

The entire shared state of a validation run is these three fields:
`bundle_dir` (a `Path`), `comp` (the parsed YAML dictionary, or `None`
when the file is missing or corrupt — every check must tolerate that
case), and `facts`. The helper `ctx.phases()` (`base.py:86-89`) returns
`comp["phases"]` filtered to dictionaries, or `[]`.

> **Observation:** placing a breakpoint on the `return` line of
> `from_bundle_dir` and inspecting `comp` in the Variables panel shows
> the exact dictionary that every subsequent check reads.

## 5. Stage 4 — the dispatch loop

**File: `checks/base.py:162` — `run_checks(ctx)`**

```python
for check in checks_for(tiers):              # registry, sorted by (tier, id)
    if check.tier == Tier.JUDGED:
        continue                             # judged is async; dispatched separately
    missing = check.missing_facts(ctx)
    if missing:
        results.append(check.skipped(f"requires facts not provided: ..."))
        continue
    results.extend(check.run(ctx))
```

Three rules are visible directly in the loop:

1. Judged checks are skipped here. They require `await`, so `api.py`
   dispatches them after this loop (Stage 6).
2. A check whose `requires_facts` are not declared reports **SKIPPED with
   instructions**; a missing fact is never a silent pass.
3. Each check returns a *list* of `CheckResult`s, since some checks emit
   one result per phase or per leaderboard column.

## 6. Stage 5 — inside a deterministic check (two worked examples)

**File: `checks/deterministic.py`**

### Example A: `DevPhaseDuration` (line 78) — pure dictionary arithmetic

```python
def run(self, ctx):
    phases = ctx.phases()
    if not phases:
        return [self.skipped("no phases declared")]
    first = phases[0]
    start, end = _parse_date(first.get("start")), _parse_date(first.get("end"))
    if start is None or end is None:
        return [self.skipped("first phase start/end not parseable as dates", where="phases[0]")]
    days = (end - start).days
    if days >= 40:
        return [self.passed(f"development phase runs {days} days", where="phases[0]")]
    return [self.finding(f"development phase runs only {days} days — ...", where="phases[0]")]
```

The control flow proceeds as follows: obtain the phase list from the
shared dictionary; an absent phase list yields SKIP, since the schema
check owns that failure and checks do not double-report; parse the first
phase's `start` and `end` (`_parse_date` tries `%Y-%m-%d %H:%M:%S`, then
`%Y-%m-%d`, and also accepts values PyYAML has already converted to
`datetime`); unparseable dates are SKIPPED, never guessed; otherwise the
check is plain subtraction against a threshold drawn from the literature
(the citation on the class, `Pavão et al. (Ch. 13)`, is stamped onto
every result it emits).

The verdict vocabulary is significant: a too-short phase is a
**finding**, not a FAIL — such a configuration is legal, merely a
documented risk. The same pattern holds for `TwoPhaseStructure`
(line 59: `len(ctx.phases()) >= 2`), `DailySubmissionCap` (line 105:
reads `max_submissions_per_day` on every non-final phase),
`FinalPhaseSubmissionLimit` (reads `max_submissions` on the last phase),
and `LeaderboardSortingDeclared` (walks
`comp["leaderboards"][i]["columns"][j]["sorting"]`) — approximately 20
lines each, taking a dictionary in and producing cited results out.

### Example B: `SchemaLint` (line 33) — the check that gates

```python
def run(self, ctx):
    report = validate_bundle(ctx.bundle_dir.name, str(ctx.bundle_dir.parent))
    issues = report.get("issues") or []
    if not issues:
        return [self.passed("competition.yaml parses; all referenced files exist; ...")]
    out = []
    for issue in issues:
        status = Status.FAIL if issue.get("severity") == "error" else Status.FINDING
        ...
```

This is the only deterministic check that leaves the YAML dictionary and
walks the filesystem — and it does so by delegating to the *core layer*:
`core/bundle_io.py:validate_bundle` (line 351), a pure-I/O lint that
checks, in order:

1. `competition.yaml` parses and contains all required top-level keys;
2. every file the YAML references actually exists (logo, terms, every
   page, every program directory, data directories, solutions);
3. every leaderboard `column.key` appears as a key the scoring program
   writes (a static scan of `score.py` for `json.dump` keys — where
   certainty is unavailable, the lint warns rather than fails);
4. each scoring and ingestion program carries runnable metadata —
   `metadata.yaml` **or** the legacy extensionless `metadata` filename
   (production Codabench accepts both; the validator incorrectly gated
   the legacy form until the STYLE-TRANS-FAIR ground-truth bundle
   demonstrated otherwise — see CHANGELOG);
5. phases are sorted, non-overlapping, and reference declared tasks;
6. tasks referenced from phases and solutions exist by index.

Error-severity issues become the report's **gate failures**; warnings
become findings. This is why a structurally broken bundle fails
validation while a merely risky design only accumulates advisories.

After the deterministic checks, the same loop runs
`checks/attestations.py` — five checks whose `run()` unconditionally
returns `ATTESTATION_REQUIRED` results ("external review of the task
design happened", "legal signed off on the prize terms", and so on).
Code cannot know these facts; the report surfaces them as unchecked
boxes rather than asserting them.

## 7. Stage 6 — the judged tier (only with `--judged`)

**File: `checks/judged.py`**

Returning to `api.py`:

```python
if judged:
    if backend is None:
        from ..backends import get_claude_backend
        backend = get_claude_backend()
    results.extend(await run_judged_checks(ctx, backend))
```

`run_judged_checks` iterates over the same `REGISTRY`, selecting the
`JudgedCheck` instances. Each one:

1. **builds one rubric prompt** (`build_prompt`) — for example,
   `DocsConfigConsistency` embeds the raw `competition.yaml` text (capped
   at 8k characters) and all `pages/*.md` (capped at 16k) and asks for
   *contradictions only*, quoted from both sides, as strict JSON;
2. **runs it as a tool-less backend session**:
   `await backend.run(AgentTask(prompt=prompt, allowed_tools=[]))` — the
   judge cannot browse and cannot run code; it reads only what is in the
   prompt;
3. **parses the verdict** with `_extract_json` (a fenced ```json``` block
   or the first `{...}` in the reply). Unparseable output yields
   **SKIPPED** ("judge returned no parseable JSON verdict"); a failed
   session yields SKIPPED with the error. A pass is *never* fabricated.

The constructed results are `FINDING`s (or an advisory pass). By
construction there is no code path from a judged check to `Status.FAIL`
— an LLM's opinion can warn, but it cannot gate. This is the falsifiable
answer to the objection that the system merely asks the model: the
property called *valid* is decided entirely by Stage 5.

## 8. Stage 7 — the report and the exit code

**File: `checks/report.py`**

Every result funnels into one dataclass:

```python
@dataclass
class ValidationReport:
    bundle_dir: Path
    results: list[CheckResult]
    facts: CompetitionFacts

    @property
    def ok(self) -> bool:
        return not any(r.status == Status.FAIL for r in self.results)
```

The `ok` property expresses the entire gating policy in one line: **only
deterministic FAILs block**. `to_markdown()` renders five sections in
severity order — *Gate failures* (which must be fixed), *Findings
(advisory)*, *Attestations required* (where an unchecked box is not
equivalent to completion), *Skipped*, and *Passed* — each line carrying
its check id, locator (`where`), message, and citation. `to_dict()`
produces the same content for `--json`.

Control returns to `_cmd_validate`, which prints the rendering and maps
`report.ok` to the process exit code: **0** indicates that no gates
failed, **1** indicates that at least one gate failed, and **2**
indicates that `--judged` was refused for lack of authentication. That
exit code is what continuous-integration systems consume.

---

## Following the trace live: breakpoints per stage

The following table lists one breakpoint per stage, together with the
state worth inspecting at each.

| Stage | Breakpoint | What to inspect there |
|---|---|---|
| 1 CLI | `cli/main.py` → `_cmd_validate` first line | `args` |
| 2 entry | `checks/api.py` → `validate_bundle_path_async` first line | `path`, zip-vs-dir branch |
| 3 context | `checks/base.py:84` (`from_bundle_dir` return) | `comp` — the parsed YAML dict |
| 4 dispatch | `checks/base.py:162` (`run_checks` loop) | conditional bp: `check.id == "dev-phase-duration"` |
| 5 a check | `checks/deterministic.py:86` (`DevPhaseDuration.run`) | `phases`, `days` |
| 5 core lint | `core/bundle_io.py:351` (`validate_bundle`) | `issues` accumulating |
| 6 judged | `checks/judged.py` → `parse_verdict` | `text` — the raw LLM reply |
| 7 report | `checks/report.py` → `ok` property | `self.results` |

Launch with the **"validate: ground-truth bundle (keyless)"**
configuration in `.vscode/launch.json`, step into each call with F11,
and observe the Call Stack panel reproduce the call graph at the top of
this page.

## Reproducing on real artifacts

```bash
# the human-built production bundle (passes the gates; 4 advisory findings,
# incl. uncapped submissions — which --judged then catches contradicting its own pages)
autocodabench validate-bundle experiments/bundle_creation_test/competitions/style-trans-fair/ground_truth/bundle

# machine-readable
autocodabench validate-bundle <bundle> --json | python -m json.tool

# add the LLM-judged advisory tier (prompts for auth if you have none)
autocodabench validate-bundle <bundle> --judged

# the full check inventory, by tier, with citations
autocodabench checks list
```

## Related reading

- `docs/architecture.md` — the maintainer's map of all layers (this
  document covers the `checks/` + `core/` slice).
- `docs/scientific-validation.md` — *why* the tiers exist and how each
  check's decision rule is justified.
- `tests/test_checks.py` — every behavior above as a minimal runnable
  example; "Debug Test" on any case drops you straight into a check.
