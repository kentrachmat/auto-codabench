/* AutoCodabench chat UI tweaks (loaded as custom_js in chainlit config).
 *
 * Two responsibilities, both DOM-side only:
 *
 * 1. Inline help inside expanded tool-step panels.
 *    The agent emits each MCP call as a cl.Step. Chainlit renders it as a
 *    collapsible "chip" with the input/output JSON revealed on expand. We
 *    inject a compact `<div class="ac-help-inline">` *inside* every panel
 *    so the help is only visible when the panel is open. No corner widget.
 *
 * 2. Animated dots on "Running …" chips.
 *    app.py sets the step name to `Running <operation>` while the call is
 *    in flight and rewrites it to `<operation>` (dropping the prefix) once
 *    the result arrives. We watch all step-chip buttons; if the label
 *    starts with `Running `, we append three pulsing dots. When the prefix
 *    is gone, we remove them.
 *
 * Chainlit re-renders aggressively, so everything below is idempotent —
 * safe to call from a MutationObserver on every DOM mutation.
 */
(function () {
    "use strict";

    // Short, tool-agnostic legend inserted into each expanded step. Kept
    // compact on purpose: the user expands a tool to see the JSON, not to
    // read a wall of prose.
    const HELP_HTML = `
      <div class="ac-help-title">What this chip is</div>
      <p>One MCP call the agent made — input JSON above, output below.
      The full audit trail (raw JSON of every call, plus stdout) lives on
      disk under <code>auto_codabench/runs/&lt;your session&gt;/</code>.</p>
      <p><b>autocodabench</b> tools write competition-bundle files and
      structured run-events. <b>alex-mcp</b> tools look up papers in
      OpenAlex / PubMed / ORCID.</p>
    `;

    // ---------------------------------------------------------------
    // (1) Tag step chips so CSS and inject logic have a stable hook.
    // ---------------------------------------------------------------
    function tagSteps() {
        document.querySelectorAll("button, [role='button']").forEach((el) => {
            const txt = (el.textContent || "").trim();
            // Anything that we set as a step name starts with either
            // "Running " (in-flight) or one of the operation labels. We
            // can't enumerate the latter, so use "Running " as the gate
            // for tagging on first appearance — once a chip is tagged
            // it stays tagged even after the prefix drops.
            if (!el.dataset.acStepBtn) {
                if (/^Running /.test(txt)) {
                    el.dataset.acStepBtn = "1";
                    const host = el.closest("[data-step-id]")
                        || el.parentElement?.parentElement
                        || el.parentElement;
                    if (host) host.setAttribute("data-ac-step", "1");
                }
            }
        });
    }

    // ---------------------------------------------------------------
    // (2) Pulsing dots while the chip label starts with "Running ".
    // ---------------------------------------------------------------
    function syncRunningDots() {
        document.querySelectorAll("[data-ac-step-btn='1']").forEach((btn) => {
            const txt = (btn.textContent || "").trim();
            const isRunning = /^Running /.test(txt);
            const existing = btn.querySelector(".ac-dots");
            if (isRunning && !existing) {
                const dots = document.createElement("span");
                dots.className = "ac-dots";
                // aria-hidden so screen readers don't read three dots.
                dots.setAttribute("aria-hidden", "true");
                dots.innerHTML = "<span>.</span><span>.</span><span>.</span>";
                btn.appendChild(dots);
            } else if (!isRunning && existing) {
                existing.remove();
            }
        });
    }

    // ---------------------------------------------------------------
    // (3) Inline help inside each expanded step panel.
    //
    // Strategy: find every step button (data-ac-step-btn) and look up
    // its associated panel via `aria-controls` (Radix UI sets this on
    // their Collapsible / Accordion primitives, which Chainlit uses).
    // Inject the help as the panel's last child once, then let the
    // browser show/hide it along with the panel.
    // ---------------------------------------------------------------
    function injectInlineHelp() {
        document.querySelectorAll("[data-ac-step-btn='1']").forEach((btn) => {
            if (btn.dataset.acHelpDone) return;
            const controlsId = btn.getAttribute("aria-controls");
            let panel = controlsId ? document.getElementById(controlsId) : null;
            // Fallbacks for non-aria implementations: nearest expanded
            // sibling, or the immediate next sibling of the button's
            // parent block.
            if (!panel) {
                panel = btn.closest("[data-step-id]")?.querySelector(
                    "[data-state='open'], [data-state='closed']"
                );
            }
            if (!panel) {
                panel = btn.parentElement?.nextElementSibling
                    || btn.nextElementSibling;
            }
            if (!panel || panel.querySelector(":scope > .ac-help-inline")) return;
            const help = document.createElement("div");
            help.className = "ac-help-inline";
            help.innerHTML = HELP_HTML;
            panel.appendChild(help);
            btn.dataset.acHelpDone = "1";
        });
    }

    function tick() {
        tagSteps();
        syncRunningDots();
        injectInlineHelp();
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", tick);
    } else {
        tick();
    }
    setTimeout(tick, 800);
    setTimeout(tick, 2500);
    new MutationObserver(tick).observe(document.body, {
        childList: true,
        subtree: true,
        characterData: true,  // so we notice name changes (Running -> done)
    });
})();
