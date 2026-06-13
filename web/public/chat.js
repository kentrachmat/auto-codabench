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
      disk under <code>.autocodabench/runs/&lt;your session&gt;/</code>.</p>
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
    //     CSS can style / hide them. The labels are stable — they come
    //     from app.py's cl.Action(label=...) constants.
    //
    // The new 3-phase model uses synthetic label prefixes
    // (AC_ADVANCE::<phase>, AC_REVERT::<phase>) so chat.js can find
    // them deterministically and simulate clicks from the phase-bar
    // pill interactions. We tag them with data attributes here and
    // hide them via CSS.
    // ---------------------------------------------------------------
    function tagPhaseActions() {
        document.querySelectorAll("button").forEach((btn) => {
            const t = (btn.textContent || "").trim();
            if (!t) return;

            // Legacy 2-phase buttons (kept for completeness, the new
            // model doesn't emit these anymore).
            if (!btn.dataset.acImplButton) {
                if (t.startsWith("🛠 START IMPLEMENTATION")) {
                    btn.dataset.acImplButton = "primary";
                } else if (t.startsWith("✅ YES — switch to IMPLEMENTATION")) {
                    btn.dataset.acImplButton = "confirm";
                } else if (t.startsWith("❌ Cancel — keep planning")) {
                    btn.dataset.acImplButton = "cancel";
                }
            }

            // 3-phase model: hidden navigation buttons. Match both the
            // "AC_ADVANCE::kit" / "AC_REVERT::plan" forms.
            if (!btn.dataset.acPhaseNav) {
                const m = t.match(/^AC_(ADVANCE|REVERT)::([a-z]+)/);
                if (m) {
                    btn.dataset.acPhaseNav  = m[1].toLowerCase();  // advance|revert
                    btn.dataset.acPhaseTarget = m[2];              // plan|kit|bundle
                }
            }
        });
    }

    // Click the hidden cl.Action matching (kind, target). Walks the
    // DOM bottom-up so we always pick the most recent button if
    // Chainlit happened to leave older ones around.
    function _clickHiddenPhaseNav(kind, target) {
        const btns = Array.from(document.querySelectorAll(
            `button[data-ac-phase-nav="${kind}"][data-ac-phase-target="${target}"]`
        ));
        if (btns.length === 0) {
            console.warn("[autocodabench] no hidden phase-nav button for",
                kind, target);
            return false;
        }
        const target_btn = btns[btns.length - 1];
        try { target_btn.click(); return true; }
        catch (e) { console.error("[autocodabench] phase-nav click failed", e);
                    return false; }
    }


    // ---------------------------------------------------------------
    // (5b) PHASE PILLS in the Chainlit header strip.
    //
    // Two pills in web v1 (Plan / Competition Creation) injected as
    // siblings of the existing "Readme" + "New chat" buttons in
    // Chainlit's header. Black background — pure status indicator;
    // clicking advances or reverts. Per-turn context-% and cost are
    // surfaced inline in each assistant turn's footer (app.py), not
    // here — the header stays uncluttered.
    //
    // The list of pills is driven entirely by phase_state.json — adding
    // a third phase later (or going back to the 3-phase Kit flow) only
    // requires changes server-side; this code rebuilds whatever the
    // server says is current. PHASE_ORDER below is unused legacy.
    // ---------------------------------------------------------------

    let _lastPhasePillsSig = "";

    // Locate the host strip in Chainlit's chrome where the
    // "Readme" / "New chat" buttons live. Strategy: find any of those
    // buttons by visible text, then walk up to the nearest <header>
    // or to the parent that holds both. We then insert our pills
    // BEFORE the "Readme" button so the pills sit on the left of the
    // existing controls.
    function _findHeaderHost() {
        const candidates = [];
        document.querySelectorAll("a, button").forEach((el) => {
            const t = (el.textContent || "").trim();
            if (t === "Readme" || t === "New Chat" || t === "New chat") {
                candidates.push(el);
            }
        });
        if (candidates.length === 0) return null;
        // Use the LEFTMOST candidate (lowest .getBoundingClientRect().x)
        // — that's normally the Readme link in the header right cluster.
        candidates.sort((a, b) => {
            const ra = a.getBoundingClientRect();
            const rb = b.getBoundingClientRect();
            return ra.x - rb.x;
        });
        const anchor = candidates[0];
        // Common parent that contains anchor + a few siblings. Walk up
        // a couple of levels until we hit a flex/row container.
        let host = anchor.parentElement;
        for (let i = 0; i < 3 && host; i += 1) {
            const cs = window.getComputedStyle(host);
            if ((cs.display === "flex" || cs.display === "inline-flex")
                && cs.flexDirection !== "column") return host;
            host = host.parentElement;
        }
        return anchor.parentElement;
    }

    function _ensurePhasePills() {
        if (document.getElementById("ac-phase-pills")) return;
        if (!document.querySelector("textarea")) return; // login
        if (!_currentSessionId()) return;
        const host = _findHeaderHost();
        if (!host) return;
        const pills = document.createElement("div");
        pills.id = "ac-phase-pills";
        pills.className = "ac-phase-pills";
        // Insert as the FIRST child of the header cluster so the pills
        // appear to the left of the existing buttons.
        host.insertBefore(pills, host.firstChild);
    }

    // Locate the Readme link in the header (same heuristic as
    // _findHeaderHost). Cached after first hit.
    function _findReadmeButton() {
        if (window.__acReadmeBtn
            && document.body.contains(window.__acReadmeBtn)) {
            return window.__acReadmeBtn;
        }
        const all = document.querySelectorAll("a, button");
        for (const el of all) {
            if ((el.textContent || "").trim() === "Readme") {
                window.__acReadmeBtn = el;
                return el;
            }
        }
        return null;
    }

    // Flash a red outline on the Readme button for a few seconds. Used
    // when the user clicks a pill that can't do anything yet — the
    // implicit nudge is "see Readme for how the bar works". CSS handles
    // the animation; we just toggle the class.
    function _flashReadmeForHelp() {
        const btn = _findReadmeButton();
        if (!btn) return;
        btn.classList.remove("ac-readme-flash");
        // Force a reflow so the class is re-added cleanly (otherwise
        // rapid repeat clicks don't restart the animation).
        void btn.offsetWidth;
        btn.classList.add("ac-readme-flash");
        // Auto-strip the class so a one-off click doesn't leave a
        // permanent outline once the animation ends. Match the CSS
        // animation duration (3 s).
        setTimeout(() => btn.classList.remove("ac-readme-flash"), 3200);
    }

    async function _refreshPhasePillsFromState() {
        const sid = _currentSessionId();
        if (!sid) return;
        _ensurePhasePills();
        const pillsHost = document.getElementById("ac-phase-pills");
        if (!pillsHost) return;
        try {
            const r = await fetch(
                `/public/sessions/${sid}/phase_state.json?t=${Date.now()}`,
                {cache: "no-cache"},
            );
            if (!r.ok) return;
            const state = await r.json();
            // Skip rebuilds when phase status hasn't changed (don't
            // re-render on every cost/ctx tick).
            const sig = JSON.stringify({
                cur:   state.current,
                nxt:   state.next,
                can:   state.can_advance,
                items: (state.phases || []).map((x) => [x.id, x.status]),
            });
            if (sig === _lastPhasePillsSig) return;
            _lastPhasePillsSig = sig;

            pillsHost.innerHTML = "";
            (state.phases || []).forEach((ph, idx) => {
                const pill = document.createElement("button");
                pill.type = "button";
                pill.className = "ac-pp ac-pp-" + ph.status;
                pill.dataset.phaseId     = ph.id;
                pill.dataset.phaseStatus = ph.status;
                const isAdvanceTarget =
                    (ph.status === "pending"
                     && state.next === ph.id
                     && state.can_advance);
                const num = idx + 1;
                const lockIcon = (ph.status === "locked") ? " 🔒" : "";
                const advIcon  = isAdvanceTarget ? " ▶" : "";
                pill.textContent = `${num}. ${ph.title}${lockIcon}${advIcon}`;
                // Tooltips + click behavior:
                if (ph.status === "active") {
                    pill.title = "Currently in " + ph.title;
                    // Active pill — clicking it doesn't go anywhere, but
                    // users tend to click their current phase to "open"
                    // something. Flash the Readme so they learn the bar.
                    pill.addEventListener("click", _flashReadmeForHelp);
                } else if (ph.status === "locked") {
                    pill.title = "Click to revise " + ph.title +
                        " (discards everything after this phase)";
                    pill.addEventListener("click", () => {
                        const curName = (state.phases || []).find(
                            (x) => x.id === state.current)?.title
                            || state.current;
                        const ok = confirm(
                            "« BACK to " + ph.title + "\n\n" +
                            "This will REOPEN " + ph.title + " for editing " +
                            "and DISCARD the " + curName + " artifact " +
                            "(it will be regenerated next time you " +
                            "advance forward). The " + ph.title +
                            " content itself is preserved so you can " +
                            "edit it in place.\n\n" +
                            "Continue?");
                        if (!ok) return;
                        _clickHiddenPhaseNav("revert", ph.id);
                    });
                } else if (isAdvanceTarget) {
                    pill.title = "Click to advance to " + ph.title;
                    pill.addEventListener("click", () => {
                        const curName = (state.phases || []).find(
                            (x) => x.id === state.current)?.title
                            || state.current;
                        const ok = confirm(
                            "▶ ADVANCE to " + ph.title + "\n\n" +
                            "This will LOCK " + curName + " (its artifact " +
                            "becomes read-only at this phase's agent) and " +
                            "start " + ph.title + " with a FRESH agent " +
                            "that has NO memory of this conversation — " +
                            "only the locked artifact carries forward.\n\n" +
                            "You can come back later by clicking the 🔒 " +
                            curName + " pill, but doing so will discard " +
                            "any " + ph.title + " output and start over " +
                            "when you re-advance.\n\n" +
                            "Continue?");
                        if (!ok) return;
                        _clickHiddenPhaseNav("advance", ph.id);
                    });
                } else {
                    // Pending but not yet eligible to advance. Tell the
                    // user via the Readme (more learnable than a toast).
                    pill.title = "Complete the current phase first — " +
                        "see Readme for how the phase bar works";
                    pill.addEventListener("click", _flashReadmeForHelp);
                }
                pillsHost.appendChild(pill);
            });
        } catch (e) {
            // Silent — state file may not exist yet on first paint.
        }
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

    // Per-URL `tag` (size+mtime from manifest.json) of the version we
    // currently have showing in the iframe. Updated only when we
    // actually (re)load that URL into the iframe — used to detect
    // real content changes vs. the manifest just being re-written
    // with the same data. Without this, the iframe reloaded on every
    // 3.5 s tick and the user's scroll position kept jumping to the
    // top. Initial-load case: tag captured at first iframe.src set.
    const _lastTagByUrl = {};
    let _lastFileListSig = "";

    function _tabsListSig(files) {
        // Sig keys ONLY off the file list shape (URL + name), not
        // content tags — content changes shouldn't rebuild the tabs.
        return JSON.stringify((files || []).map((f) => [f.url, f.name]));
    }

    let _lastDownloadsSig = "";

    function _formatBytes(n) {
        if (!n && n !== 0) return "";
        if (n < 1024) return `${n} B`;
        if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
        return `${(n / (1024 * 1024)).toFixed(1)} MB`;
    }

    async function _refreshSidePanelFromManifest() {
        const sid = _currentSessionId();
        const panel = document.getElementById("ac-side-panel");
        if (!sid || !panel) return;
        // Skip the network round-trip if the user has the panel
        // collapsed — there's no visible content to update.
        if (panel.getAttribute("data-state") === "collapsed") return;
        try {
            const r = await fetch(
                `/public/sessions/${sid}/manifest.json?t=${Date.now()}`,
                {cache: "no-cache"},
            );
            if (!r.ok) return;
            const m = await r.json();
            // New manifest shape: `tabs` (viewable) + `downloads` (real
            // files). Fall back to legacy `files` if the backend hasn't
            // been updated.
            const tabs      = m.tabs || m.files || [];
            const downloads = m.downloads || [];
            const tabsHost  = panel.querySelector(".ac-tabs");
            const iframe    = panel.querySelector("#ac-side-iframe");

            // --- tabs (iframe-viewable) ---
            const tabsSig = _tabsListSig(tabs);
            if (tabsSig !== _lastFileListSig) {
                _lastFileListSig = tabsSig;
                const wasActiveUrl = tabsHost.querySelector(".ac-tab-active")
                    ?.dataset.url || null;
                tabsHost.innerHTML = "";
                tabs.forEach((f, i) => {
                    const isActive = wasActiveUrl
                        ? f.url === wasActiveUrl
                        : i === 0;
                    const b = document.createElement("button");
                    b.type = "button";
                    b.className = "ac-tab" + (isActive ? " ac-tab-active" : "");
                    b.dataset.url = f.url;
                    b.textContent = f.name;
                    b.addEventListener("click", () => {
                        tabsHost.querySelectorAll(".ac-tab").forEach(
                            (x) => x.classList.remove("ac-tab-active"));
                        b.classList.add("ac-tab-active");
                        iframe.src = f.url + `?t=${Date.now()}`;
                        _lastTagByUrl[f.url] = f.tag;
                    });
                    tabsHost.appendChild(b);
                });
                if (iframe.src === "about:blank" || !iframe.src) {
                    const target = tabs.find((f) => f.url === wasActiveUrl)
                        || tabs[0];
                    if (target) {
                        iframe.src = target.url + `?t=${Date.now()}`;
                        _lastTagByUrl[target.url] = target.tag;
                    }
                }
            }

            // For the currently-active tab, ONLY reload if its content
            // actually changed since we last loaded it. Killed the
            // scroll-to-top bug.
            const active = tabsHost.querySelector(".ac-tab-active");
            if (active) {
                const activeFile = tabs.find((f) => f.url === active.dataset.url);
                if (activeFile
                    && activeFile.tag
                    && _lastTagByUrl[activeFile.url] !== activeFile.tag) {
                    iframe.src = activeFile.url + `?t=${Date.now()}`;
                    _lastTagByUrl[activeFile.url] = activeFile.tag;
                }
            }

            // --- downloads + publish footer ---
            const footer  = panel.querySelector(".ac-side-footer");
            const dlHost  = footer?.querySelector(".ac-dl-buttons");
            const pubSec  = footer?.querySelector(".ac-pub-section");
            if (footer && dlHost) {
                // The footer always has at least workspace.zip in the
                // downloads list (built every turn). Keep it visible
                // throughout so the user always knows where to look.
                footer.setAttribute("data-state",
                    downloads.length > 0 ? "shown" : "hidden");
                const bundleEntry = downloads.find(
                    (d) => d.kind === "bundle");
                const bundleReady = !!(bundleEntry && bundleEntry.ready);
                pubSec?.setAttribute("data-bundle-ready",
                    bundleReady ? "yes" : "no");

                const dlSig = JSON.stringify(downloads.map(
                    (d) => [d.url, d.tag, d.size, !!d.ready]));
                if (dlSig !== _lastDownloadsSig) {
                    _lastDownloadsSig = dlSig;
                    dlHost.innerHTML = "";
                    downloads.forEach((d) => {
                        const ready = d.ready !== false;
                        const tag   = ready ? "a" : "div";
                        const el    = document.createElement(tag);
                        el.className = "ac-dl-btn" + (ready ? "" : " ac-dl-disabled");
                        if (ready) {
                            el.href = d.url;
                            el.setAttribute("download", d.filename || "");
                        }
                        el.dataset.kind = d.kind;
                        el.title = ready
                            ? `Download ${d.filename || ""}`
                            : (d.kind === "bundle"
                                ? "Available after Phase 2 finishes"
                                : "Not ready yet");
                        const sizeHTML = ready
                            ? `<span class="ac-dl-size">${_formatBytes(d.size)}</span>`
                            : `<span class="ac-dl-size">— not ready</span>`;
                        el.innerHTML =
                            `<span class="ac-dl-label">${d.name}</span>`
                            + sizeHTML;
                        dlHost.appendChild(el);
                    });
                }
            }
        } catch (e) {
            // Network blip / not-yet-written → silent.
        }
    }

    function _wirePublishForm(panel) {
        const sec = panel.querySelector(".ac-pub-section");
        if (!sec || sec.dataset.acWired) return;
        sec.dataset.acWired = "1";

        // Header toggle expands/collapses the form.
        const toggle = sec.querySelector(".ac-pub-toggle");
        toggle?.addEventListener("click", (e) => {
            e.stopPropagation();
            const open = sec.getAttribute("data-state") !== "collapsed";
            sec.setAttribute("data-state", open ? "collapsed" : "open");
        });

        const form   = sec.querySelector(".ac-pub-form");
        const status = sec.querySelector(".ac-pub-status");
        const submit = sec.querySelector(".ac-pub-submit");
        if (!form || !status || !submit) return;

        // Clicks inside the form shouldn't bubble up to the panel-level
        // "click anywhere to open" handler (matters when the panel is
        // collapsed — though we set the form hidden then anyway).
        form.addEventListener("click", (e) => e.stopPropagation());

        form.addEventListener("submit", async (e) => {
            e.preventDefault();
            e.stopPropagation();
            const sid = _currentSessionId();
            if (!sid) {
                status.innerHTML =
                    "<span class='ac-pub-err'>session id missing — refresh</span>";
                return;
            }
            const fd = new FormData(form);
            const username = (fd.get("username") || "").toString().trim();
            const password = (fd.get("password") || "").toString();
            if (!username || !password) {
                status.innerHTML =
                    "<span class='ac-pub-err'>username + password required</span>";
                return;
            }
            // Disable form, show spinner.
            submit.disabled = true;
            const origLabel = submit.textContent;
            submit.textContent = "⏳ Uploading…";
            status.innerHTML =
                "<span class='ac-pub-info'>uploading to Codabench "
                + "(can take 30–90 s while it unpacks)…</span>";
            try {
                const r = await fetch("/ac/upload-codabench", {
                    method:  "POST",
                    headers: {"Content-Type": "application/json"},
                    body:    JSON.stringify({
                        session_id: sid,
                        username, password,
                    }),
                });
                // Read the body ONCE as text so we can surface it raw
                // when JSON parsing fails or the server returns an
                // unexpected shape. Previously the bare `out.error` ||
                // "unknown error" fallback hid the real failure mode.
                const raw  = await r.text();
                const safe = (s) => String(s)
                    .replace(/&/g, "&amp;").replace(/</g, "&lt;")
                    .replace(/>/g, "&gt;");
                let out = null;
                try { out = raw ? JSON.parse(raw) : null; }
                catch { out = null; }

                if (!out) {
                    status.innerHTML =
                        `<span class='ac-pub-err'>❌ server returned `
                        + `HTTP ${r.status} non-JSON</span>`
                        + `<details><summary>response body</summary>`
                        + `<pre>${safe(raw).slice(0, 1500)}</pre></details>`;
                    return;
                }
                if (!out.ok) {
                    const msg = out.error
                        || `HTTP ${r.status} with no error field`;
                    status.innerHTML =
                        `<span class='ac-pub-err'>❌ ${safe(msg)}</span>`
                        + `<details><summary>full response (HTTP `
                        + `${r.status})</summary>`
                        + `<pre>${safe(JSON.stringify(out, null, 2))}</pre>`
                        + `</details>`;
                    return;
                }
                const url = out.competition_url || "#";
                status.innerHTML =
                    "<span class='ac-pub-ok'>✅ Published!</span><br>" +
                    `<a class="ac-pub-link" href="${url}" `
                    + `target="_blank" rel="noopener">${safe(url)}</a>`;
                // Clear password but keep username so the user can
                // retry / re-publish without retyping it.
                form.querySelector("input[name='password']").value = "";
            } catch (err) {
                status.innerHTML =
                    "<span class='ac-pub-err'>❌ network error: "
                    + (err?.message || err) + "</span>";
            } finally {
                submit.disabled = false;
                submit.textContent = origLabel;
            }
        });
    }

    function _setPanelCollapsed(panel, collapsed) {
        // Use data-state as the primary hook (resistant to React
        // re-renders that may strip our classes); class is also set
        // for older CSS rules that target it.
        panel.setAttribute("data-state", collapsed ? "collapsed" : "open");
        panel.classList.toggle("ac-collapsed", collapsed);
        panel.setAttribute("aria-expanded", String(!collapsed));
        // Only reserve right-side body padding when the panel is OPEN.
        // Collapsed sliver is small enough to overlay the chat edge.
        document.body.classList.toggle("ac-side-active", !collapsed);
        const btn = panel.querySelector("#ac-side-collapse");
        if (btn) {
            btn.innerHTML = collapsed ? "📁 Workspace" : "›";
            btn.title = collapsed
                ? "Open workspace (notebook, transcript, …)"
                : "Collapse the workspace panel";
            btn.setAttribute("aria-label", btn.title);
        }
    }

    function _injectSidePanel() {
        if (document.getElementById("ac-side-panel")) return;
        const sid = _currentSessionId();
        if (!sid) return;
        if (!document.querySelector("textarea")) return; // login screen
        const panel = document.createElement("aside");
        panel.id = "ac-side-panel";
        // Pre-set data-state in the HTML so first paint reflects the
        // collapsed sizing before JS runs again.
        panel.setAttribute("data-state", "collapsed");
        panel.setAttribute("aria-expanded", "false");
        panel.innerHTML = `
            <header class="ac-side-header">
                <span class="ac-side-title">📁 Workspace</span>
                <div class="ac-side-actions">
                    <button id="ac-side-refresh" type="button"
                            title="Reload the active file"
                            aria-label="Reload">↻</button>
                    <button id="ac-side-collapse" type="button"
                            title="Open workspace"
                            aria-label="Open workspace">📁 Workspace</button>
                </div>
            </header>
            <div class="ac-tabs"></div>
            <iframe id="ac-side-iframe"
                    src="about:blank"
                    sandbox="allow-same-origin"></iframe>
            <div class="ac-side-footer" data-state="hidden">
                <section class="ac-dl-section">
                    <div class="ac-foot-title">Downloads</div>
                    <div class="ac-dl-buttons"></div>
                </section>
                <section class="ac-pub-section" data-state="collapsed">
                    <div class="ac-foot-title ac-pub-toggle">
                        🚀 Publish to Codabench
                        <span class="ac-pub-chev">▾</span>
                    </div>
                    <form class="ac-pub-form" autocomplete="off">
                        <label>Username
                          <input type="text" name="username"
                                 autocomplete="username"
                                 placeholder="codabench username" required>
                        </label>
                        <label>Password
                          <input type="password" name="password"
                                 autocomplete="current-password"
                                 placeholder="codabench password" required>
                        </label>
                        <button type="submit" class="ac-pub-submit">
                            🚀 Upload &amp; publish
                        </button>
                        <div class="ac-pub-status" role="status"></div>
                    </form>
                </section>
            </div>
        `;
        document.body.appendChild(panel);
        // Sync class/state after the panel is in the DOM (we set
        // data-state in HTML for first-paint, but need to set the
        // class + body state too).
        _setPanelCollapsed(panel, true);

        const iframe = panel.querySelector("#ac-side-iframe");

        // -----  Click model  -----
        //
        // (1) Whole panel: clicks open it when collapsed. This makes
        //     the 44px sliver fully clickable rather than requiring
        //     a tiny button hit. We early-return when not collapsed
        //     so iframe / tab clicks in the open state aren't hijacked.
        panel.addEventListener("click", (e) => {
            if (panel.getAttribute("data-state") !== "collapsed") return;
            // Open.
            _setPanelCollapsed(panel, false);
            // Pull a fresh manifest right away so the user sees current
            // content immediately, not stale or empty placeholders.
            _refreshSidePanelFromManifest();
        });

        // (2) Refresh button in the OPEN-state header.
        panel.querySelector("#ac-side-refresh").addEventListener("click", (e) => {
            e.stopPropagation();  // don't bubble to the panel listener
            const active = panel.querySelector(".ac-tab-active");
            if (active) iframe.src = active.dataset.url + `?t=${Date.now()}`;
        });

        // (3) Collapse / open toggle button. Stops propagation so the
        //     panel-level "open on any click" doesn't immediately
        //     re-open after we just closed.
        panel.querySelector("#ac-side-collapse").addEventListener("click", (e) => {
            e.stopPropagation();
            const isCollapsedNow = panel.getAttribute("data-state") === "collapsed";
            _setPanelCollapsed(panel, !isCollapsedNow);
            if (isCollapsedNow) _refreshSidePanelFromManifest();
        });

        // (4) Tab strip — also stop propagation when clicking tab buttons.
        panel.querySelector(".ac-tabs").addEventListener("click", (e) => {
            e.stopPropagation();
        });

        // Wire the bottom-panel publish form (idempotent — guarded
        // internally by a data-attr).
        _wirePublishForm(panel);

        // First fetch + then periodic refresh every 3.5 s, but only
        // while the panel is OPEN (see _refreshSidePanelFromManifest).
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
        _injectSidePanel();      // sci-space-style persistent workspace panel
        _ensurePhasePills();     // header-row phase pills (slim, no chips)
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

    // Poll the phase state JSON on its own ~2 s timer so pill updates
    // feel snappy without re-fetching the full workspace manifest.
    setInterval(_refreshPhasePillsFromState, 2000);

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
