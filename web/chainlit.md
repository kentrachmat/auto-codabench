<div align="center" style="margin:6px 0 2px">
  <img src="/public/codabench-logo.png" alt="Codabench" style="height:40px" />
  <div style="color:hsl(var(--muted-foreground));font-size:14px;margin-top:6px">
    Design &amp; validate Codabench competitions — just by chatting.
  </div>
</div>

---

### 🧭 The three phases

<div style="display:flex;gap:10px;flex-wrap:wrap;margin:8px 0 4px">
  <div style="flex:1;min-width:150px;padding:14px;border:1px solid hsl(var(--border));border-radius:12px;background:hsl(var(--accent))">
    <div style="font-size:24px;line-height:1">📝</div>
    <div style="font-weight:700;margin-top:6px">1 · Plan</div>
    <div style="font-size:12.5px;color:hsl(var(--muted-foreground));margin-top:3px">Chat an idea into a design plan.</div>
  </div>
  <div style="flex:1;min-width:150px;padding:14px;border:1px solid hsl(var(--border));border-radius:12px;background:hsl(var(--accent))">
    <div style="font-size:24px;line-height:1">📦</div>
    <div style="font-weight:700;margin-top:6px">2 · Build</div>
    <div style="font-size:12.5px;color:hsl(var(--muted-foreground));margin-top:3px">Agent writes the bundle &amp; runs it.</div>
  </div>
  <div style="flex:1;min-width:150px;padding:14px;border:1px solid hsl(var(--border));border-radius:12px;background:hsl(var(--accent))">
    <div style="font-size:24px;line-height:1">✅</div>
    <div style="font-weight:700;margin-top:6px">3 · Validate</div>
    <div style="font-size:12.5px;color:hsl(var(--muted-foreground));margin-top:3px">Checks + report: ready to launch?</div>
  </div>
</div>

The **phase bar** (top-right) shows where you are — `●` active · `✓` done.
Hover any phase for a one-line reminder. You move forward with the **▶ Proceed**
buttons in the chat.

---

### 🚀 Two ways to start

Every new chat opens with a choice (switch later via **New Chat**, top-left):

- **🛠 Create from scratch** — you have an *idea*. Go through all three phases below.
- **✅ Validate a bundle** — you already have a `.zip`. Jump straight to **Phase 3**;
  the composer locks to *attach-only* (drop your bundle and send). No bundle? That
  screen has a **⬇ Download example bundle** button to try.

---

## 📝 Phase 1 · Plan

*Design the competition together — the agent acts as a scientific collaborator.*

- **You provide:** a one-sentence idea — e.g. *“a fair chest-X-ray pneumonia
  challenge, scored by balanced accuracy.”* You can also **drop a PDF or markdown
  proposal** and it'll seed the plan.
- **What happens:** the agent researches related work (OpenAlex / Kaggle), then
  works through the **7 design sections** with you — *task, data, metric, baseline,
  rules, ethics, schedule* — asking questions until the design is coherent.
- **You get:** a one-page **`implementation_plan.md`** in the **workspace panel**
  on the right. Read it, push back, refine.
- **Next:** when it looks right, click **▶ Proceed to Phase 2**.

## 📦 Phase 2 · Competition Creation (Build)

*A fresh agent turns the approved plan into a real Codabench bundle.*

- **Input:** **only** the locked `implementation_plan.md` — this agent never sees
  your chat, which keeps the build honest (no leaked answers).
- **What happens:** it writes the full bundle — `competition.yaml`, the
  `scoring_program/`, a **baseline solution**, the ingestion program, participant
  **pages**, and a **starting kit** — then **builds and runs it in Docker** (using
  the bundle's `docker_image`) to prove the baseline actually produces a score.
  Expect **~5–10 min** (longer the first time, while the image downloads).
- **You get:** a **`bundle.zip`** to download, plus an **⬆️ Upload to Codabench**
  button.
- **Next:** click **▶ Proceed to Phase 3**.

## ✅ Phase 3 · Validation

*The pre-launch safety check — would this competition run cleanly?*

- **Input:** the bundle — automatically from Phase 2, or (Validate path) the
  `.zip` you **attach**.
- **What happens:** runs the full **check framework** — schema and file-reference
  checks, plus a real **Docker execution of the baseline** — and, on the create
  path, a **design scorecard** grading your plan against best practice. You can
  then run an optional **✨ LLM-judged** pass that flags participant pages
  contradicting `competition.yaml`.
- **You get:** a **✅ PASS / ❌ FAIL** verdict, two result tables, and a
  downloadable **`validation_report.md`**.
- **Next:** fix any **❌** gate failures and re-validate; **⚠️** findings are
  advisory.

---

### 📊 How to read the report

| Badge | Meaning |
|---|---|
| ❌ **Gate failure** | **Must fix before upload** — bad config, broken file paths, a baseline that crashes in Docker. |
| ⚠️ **Finding** | Advisory design risk, with a citation. Doesn't block upload. |
| 📋 **Attestation** | A criterion only a human can certify. |
| • **Skipped** | Couldn't run here (e.g. no Docker) — not a failure. |

Everything downloads from the **workspace panel**: the plan, `bundle.zip`,
`validation_report.md`, and a combined `workspace.zip`.

---

<div style="font-size:12px;color:hsl(var(--muted-foreground))">
⚙ Change the model &amp; theme from <b>settings</b> (top-right) · 💸 each session has a spend cap (default $5) · 🐳 without Docker, baseline-execution checks are skipped, not failed.
</div>
