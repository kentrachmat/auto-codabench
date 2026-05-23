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

    // ---------------------------------------------------------------
    // (5) Tag the phase-switch action buttons by their label text so
    //     CSS can style them big + pulsing. The labels are stable —
    //     they come from app.py's cl.Action(label=...) constants.
    // ---------------------------------------------------------------
    function tagPhaseActions() {
        document.querySelectorAll("button").forEach((btn) => {
            const t = (btn.textContent || "").trim();
            if (!t || btn.dataset.acImplButton) return;
            if (t.startsWith("🛠 START IMPLEMENTATION")) {
                btn.dataset.acImplButton = "primary";
            } else if (t.startsWith("✅ YES — switch to IMPLEMENTATION")) {
                btn.dataset.acImplButton = "confirm";
            } else if (t.startsWith("❌ Cancel — keep planning")) {
                btn.dataset.acImplButton = "cancel";
            }
        });
    }

    // ---------------------------------------------------------------
    // (7) Persistent right panel — sci-space style.
    //
    // Fixed-position aside on the right of the viewport. Always
    // visible once the chat page is ready and we know the session
    // id. Contains:
    //   - a tab strip at the top (notebook / transcript / cost / specs);
    //   - an iframe whose src points at the current tab's URL under
    //     /public/sessions/<sid>/... — files written by the server's
    //     _write_public_artifacts() after every turn.
    //
    // The panel does NOT replace Chainlit's element drawer (we keep
    // the inline chips too) — it just augments the chat with a
    // workspace pane that never disappears unless the user collapses
    // it with the chevron.
    // ---------------------------------------------------------------

    function _currentSessionId() {
        // Greeting includes `_session \`<12-char hex>\` · model …`.
        // Pull it from the page text on first sight, then cache.
        if (window.__acSessionId) return window.__acSessionId;
        const text = document.body.textContent || "";
        const m = text.match(/session\s+`?([a-f0-9]{8,16})`?/);
        if (m) {
            window.__acSessionId = m[1];
            return m[1];
        }
        return null;
    }

    let _lastManifestSig = "";
    async function _refreshSidePanelFromManifest() {
        const sid = _currentSessionId();
        const panel = document.getElementById("ac-side-panel");
        if (!sid || !panel) return;
        // Skip the network round-trip if the user has the panel
        // collapsed — there's no visible content to update.
        if (panel.classList.contains("ac-collapsed")) return;
        try {
            const r = await fetch(
                `/public/sessions/${sid}/manifest.json?t=${Date.now()}`,
                {cache: "no-cache"},
            );
            if (!r.ok) return;
            const m = await r.json();
            const sig = JSON.stringify(m.files || []);
            const tabsHost = panel.querySelector(".ac-tabs");
            const iframe   = panel.querySelector("#ac-side-iframe");
            if (sig !== _lastManifestSig) {
                _lastManifestSig = sig;
                tabsHost.innerHTML = "";
                (m.files || []).forEach((f, i) => {
                    const b = document.createElement("button");
                    b.type = "button";
                    b.className = "ac-tab" + (i === 0 ? " ac-tab-active" : "");
                    b.dataset.url = f.url;
                    b.textContent = f.name;
                    b.addEventListener("click", () => {
                        tabsHost.querySelectorAll(".ac-tab").forEach(
                            (x) => x.classList.remove("ac-tab-active"));
                        b.classList.add("ac-tab-active");
                        iframe.src = f.url + `?t=${Date.now()}`;
                    });
                    tabsHost.appendChild(b);
                });
                if ((m.files || []).length > 0 && !iframe.dataset.acPinned) {
                    iframe.src = m.files[0].url + `?t=${Date.now()}`;
                }
            } else {
                // Same file set but content may have changed (notebook
                // got more cells). Reload the iframe of the active tab.
                const active = tabsHost.querySelector(".ac-tab-active");
                if (active && !iframe.dataset.acPinned) {
                    iframe.src = active.dataset.url + `?t=${Date.now()}`;
                }
            }
        } catch (e) {
            // Network blip / not-yet-written → silent.
        }
    }

    function _setPanelCollapsed(panel, collapsed) {
        panel.classList.toggle("ac-collapsed", collapsed);
        // Only reserve right-side body padding when the panel is open;
        // otherwise the chat takes the full viewport width.
        document.body.classList.toggle("ac-side-active", !collapsed);
        const btn = panel.querySelector("#ac-side-collapse");
        if (btn) {
            btn.innerHTML = collapsed ? "📁 Workspace" : "›";
            btn.title = collapsed
                ? "Open the workspace panel (notebook, transcript, …)"
                : "Collapse the workspace panel";
        }
    }

    function _injectSidePanel() {
        if (document.getElementById("ac-side-panel")) return;
        const sid = _currentSessionId();
        if (!sid) return;
        if (!document.querySelector("textarea")) return; // login screen
        const panel = document.createElement("aside");
        panel.id = "ac-side-panel";
        panel.innerHTML = `
            <header class="ac-side-header">
                <span class="ac-side-title">📁 Workspace</span>
                <div class="ac-side-actions">
                    <button id="ac-side-refresh" type="button"
                            title="Reload the active file">↻</button>
                    <button id="ac-side-collapse" type="button"
                            title="Collapse the panel">›</button>
                </div>
            </header>
            <div class="ac-tabs"></div>
            <iframe id="ac-side-iframe"
                    src="about:blank"
                    sandbox="allow-same-origin"></iframe>
        `;
        document.body.appendChild(panel);

        // Start collapsed — chat takes the whole page. User opens the
        // panel when they want to see the notebook / transcript / etc.
        _setPanelCollapsed(panel, true);

        const iframe = panel.querySelector("#ac-side-iframe");
        panel.querySelector("#ac-side-refresh").addEventListener("click", () => {
            const active = panel.querySelector(".ac-tab-active");
            if (active) iframe.src = active.dataset.url + `?t=${Date.now()}`;
        });
        panel.querySelector("#ac-side-collapse").addEventListener("click", () => {
            const becomingCollapsed = !panel.classList.contains("ac-collapsed");
            _setPanelCollapsed(panel, becomingCollapsed);
            // When opening, immediately fetch the latest manifest so
            // the user doesn't see stale content.
            if (!becomingCollapsed) _refreshSidePanelFromManifest();
        });

        // First fetch + then periodic refresh every 3.5 s. We
        // intentionally don't poll the file URL directly — the
        // manifest tells us when there's something new.
        _refreshSidePanelFromManifest();
        setInterval(_refreshSidePanelFromManifest, 3500);
    }


    // ---------------------------------------------------------------
    // (6) Persistent "📁 Files" toggle.
    //
    // Chainlit's element drawer (the right-side viewer) closes when the
    // user clicks outside it, and there's no native re-open affordance
    // — they'd have to scroll up to find the chip that opened it.
    // We inject a fixed-position button on the right edge of the page
    // that, when clicked, simulates a click on the most-recent element
    // chip in the chat, re-opening the drawer with the latest file
    // set. The button only appears once at least one chip exists.
    // ---------------------------------------------------------------
    function _findFileChips() {
        // Chainlit ≥ 2.x renders elements as clickable nodes with one
        // of these stable hooks. We try the most specific first and
        // fall back. Anything starting with "📄" or "📓" is our own
        // label prefix from web/app.py:_collect_side_files().
        const selectors = [
            "[data-element-id]",
            "[data-element-name]",
            'a[href*="/element/"]',
            "button.cl-element",
        ];
        const seen = new Set();
        const chips = [];
        for (const sel of selectors) {
            document.querySelectorAll(sel).forEach((el) => {
                if (seen.has(el)) return;
                seen.add(el);
                chips.push(el);
            });
        }
        // Last-resort heuristic — any clickable node whose visible text
        // starts with our emoji prefix.
        if (chips.length === 0) {
            document.querySelectorAll("button, a, [role='button']").forEach((el) => {
                const t = (el.textContent || "").trim();
                if (t.startsWith("📄 ") || t.startsWith("📓 ")) chips.push(el);
            });
        }
        return chips;
    }

    function syncFilesToggle() {
        // Don't show during init.
        const isReady = document.body.textContent.includes(READY_PHRASE);
        const onChatPage = !!document.querySelector("textarea");
        const chips = _findFileChips();
        let btn = document.getElementById("ac-files-toggle");
        if (!onChatPage || !isReady || chips.length === 0) {
            if (btn) btn.remove();
            return;
        }
        if (!btn) {
            btn = document.createElement("button");
            btn.id = "ac-files-toggle";
            btn.type = "button";
            btn.setAttribute("aria-label",
                "Reopen the file viewer (notebook, transcript, specs, …)");
            btn.title = "Reopen the file viewer";
            btn.innerHTML = "📁 Files";
            btn.addEventListener("click", () => {
                const all = _findFileChips();
                if (all.length === 0) return;
                const last = all[all.length - 1];
                try { last.scrollIntoView({behavior: "auto", block: "center"}); } catch {}
                try { last.click(); } catch {}
            });
            document.body.appendChild(btn);
        }
        // Update the count label so users see at a glance how many.
        const expected = `📁 Files (${chips.length})`;
        if (btn.innerHTML !== expected) btn.innerHTML = expected;
    }

    function tick() {
        syncInitGate();   // run first so the lock is up before anything else
        _injectSidePanel();  // sci-space-style persistent workspace panel
        tagSteps();
        syncRunningDots();
        injectInlineHelp();
        tagPhaseActions();
        // syncFilesToggle is now redundant — the persistent panel is
        // the primary file viewer. Keep the function around for the
        // edge case where the panel can't materialise (no session id).
        if (!document.getElementById("ac-side-panel")) {
            syncFilesToggle();
        } else {
            const stale = document.getElementById("ac-files-toggle");
            if (stale) stale.remove();
        }
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
