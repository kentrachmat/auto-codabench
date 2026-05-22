/* AutoCodabench chat UI tweaks (loaded as custom_js in chainlit config).
 *
 * Four responsibilities, all DOM-side only:
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
 * 3. Init banner + input lock from the moment the page loads.
 *    on_chat_start can take 5–30s (MCP probe + SDK connect). To avoid the
 *    chat looking ready before the server actually is, we:
 *      - inject a top-of-page banner the moment chat.js runs;
 *      - lock the textarea + send button;
 *      - keep both locked until we see READY_PHRASE in the DOM (the
 *        first stable string of the greeting). When the greeting lands
 *        we remove the banner and re-enable input.
 *    The lock is opt-in for chat pages only — we gate on the textarea
 *    existing, so the banner won't appear on the login screen.
 *
 * Chainlit re-renders aggressively, so everything below is idempotent —
 * safe to call from a MutationObserver on every DOM mutation.
 */
(function () {
    "use strict";

    // The first stable line of the greeting (set in web/app.py). When this
    // appears in the DOM we know on_chat_start has finished.
    const READY_PHRASE = "Tell me a competition idea";

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

    const INIT_BANNER_HTML = `
      <span class="ac-init-spinner" aria-hidden="true"></span>
      <span>
        <b>Initializing AutoCodabench…</b>
        spinning up MCP tool servers and the literature index — this takes
        up to 30s on first connect. Chat input is locked until ready.
      </span>
    `;

    // ---------------------------------------------------------------
    // (1) Tag step chips so CSS and inject logic have a stable hook.
    // ---------------------------------------------------------------
    function tagSteps() {
        document.querySelectorAll("button, [role='button']").forEach((el) => {
            const txt = (el.textContent || "").trim();
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
    // ---------------------------------------------------------------
    function injectInlineHelp() {
        document.querySelectorAll("[data-ac-step-btn='1']").forEach((btn) => {
            if (btn.dataset.acHelpDone) return;
            const controlsId = btn.getAttribute("aria-controls");
            let panel = controlsId ? document.getElementById(controlsId) : null;
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

    // ---------------------------------------------------------------
    // (4) Init banner + input lock, applied from the *very first*
    //     paint so the user never sees an unlocked chat.
    //
    // We gate the lock on `textarea` existing — the login page has no
    // textarea, so the banner only shows after the user has signed in
    // and the chat surface is visible.
    //
    // We unlock as soon as READY_PHRASE appears in the body's text.
    // ---------------------------------------------------------------
    function syncInitGate() {
        const onChatPage = !!document.querySelector("textarea");
        const isReady = document.body.textContent.includes(READY_PHRASE);

        // -- banner --
        let banner = document.getElementById("ac-init-banner");
        if (onChatPage && !isReady) {
            if (!banner) {
                banner = document.createElement("div");
                banner.id = "ac-init-banner";
                banner.innerHTML = INIT_BANNER_HTML;
                document.body.appendChild(banner);
            }
        } else if (banner) {
            banner.remove();
        }

        if (!onChatPage) return;  // login page: skip input lock too

        const locked = !isReady;

        // -- textarea --
        document.querySelectorAll("textarea").forEach((el) => {
            if (el.disabled !== locked) {
                el.disabled = locked;
                el.classList.toggle("ac-input-locked", locked);
            }
        });

        // -- send / submit buttons -- target by role/aria; Chainlit
        // doesn't expose a stable class. Be conservative.
        document.querySelectorAll(
            "button[type='submit'], button[aria-label*='Send' i]"
        ).forEach((el) => {
            if (el.disabled !== locked) {
                el.disabled = locked;
            }
        });
    }

    function tick() {
        syncInitGate();   // run first so the lock is up before anything else
        tagSteps();
        syncRunningDots();
        injectInlineHelp();
    }

    // Apply the lock as soon as possible — ideally before React mounts
    // the chat input. We call tick() once synchronously here, then again
    // on DOMContentLoaded, then twice on timers to catch late mounts,
    // then continuously via MutationObserver.
    tick();
    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", tick);
    }
    setTimeout(tick, 200);
    setTimeout(tick, 800);
    setTimeout(tick, 2500);
    new MutationObserver(tick).observe(document.documentElement, {
        childList: true,
        subtree: true,
        characterData: true,
    });
})();
