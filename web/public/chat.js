/* AutoCodabench chat UI tweaks (loaded as custom_js in chainlit config).
 *
 * DOM-side helpers only — the agent's tool-call activity is rendered as an
 * inline CLI-style log by the server (web/streaming.py:TurnView), so this file
 * no longer touches tool chips at all. Responsibilities:
 *
 * 1. Init banner + input lock from the moment the page loads.
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
 * 2. Attach-only composer mode, phase pills, and the persistent workspace
 *    side panel (notebook / transcript / cost / downloads / publish).
 *
 * IMPORTANT: only ever mount/restyle OUR OWN injected elements (appended to
 * document.body). Never hide or restyle Chainlit's React-managed message DOM —
 * doing so crashes the frontend and drops the websocket.
 *
 * Chainlit re-renders aggressively, so everything below is idempotent —
 * safe to call from a MutationObserver on every DOM mutation.
 */
(function () {
    "use strict";

    // Stable greeting phrases (set in web/session_manager.py). When any
    // appears in the DOM we know on_chat_start has finished and can release
    // the init lock. Option A uses the first; Option B (validate) the second.
    const READY_PHRASES = ["Tell me a competition idea", "Attach your bundle"];

    const INIT_BANNER_HTML = `
      <span class="ac-init-spinner" aria-hidden="true"></span>
      <span><b>Starting up…</b> connecting tools — this takes a few seconds.</span>
    `;

    // Chooser is on screen once the entry cards are tagged (or its heading
    // text is present). The server runs the MCP probe BEFORE showing the
    // chooser, so by the time it appears initialization is actually done —
    // the banner should be gone even though no greeting phrase exists yet.
    function _chooserShown(bodyText) {
        return !!document.querySelector("button[data-ac-entry]") ||
               bodyText.includes("Choose how you'd like to start");
    }

    // ---------------------------------------------------------------
    // (4) Init banner + input lock, applied from the *very first*
    //     paint so the user never sees an unlocked chat.
    //
    // We gate on `textarea` existing — the login page has no textarea, so the
    // banner only shows after sign-in.
    //
    // Banner: shows ONLY while genuinely initializing — i.e. before EITHER the
    // chooser appears (MCP probe done) OR a greeting lands. It used to persist
    // through the whole chooser because it only watched the greeting phrases.
    // Input lock: stays on until a greeting lands, so on the landing the user
    // picks via the cards rather than typing.
    // ---------------------------------------------------------------
    function syncInitGate() {
        const onChatPage = !!document.querySelector("textarea");
        const bodyText = document.body.textContent;
        const greetingReady = READY_PHRASES.some((p) => bodyText.includes(p));
        const initializing = onChatPage && !greetingReady && !_chooserShown(bodyText);

        // -- banner --
        let banner = document.getElementById("ac-init-banner");
        if (initializing) {
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

        const locked = !greetingReady;

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
    // (4b) Attach-only input mode (Option B — validate an existing bundle).
    //
    // The server sets `input_mode` in phase_state.json:
    //   "normal"      — full composer (default).
    //   "attach_only" — typing disabled, but the file-attach + send buttons
    //                   stay usable so the user can upload a .zip and send.
    //   "locked"      — same as attach_only visually (validation in flight).
    // We use `readOnly` (not `disabled`) so the paperclip/send stay active.
    // window.__acInputMode is refreshed by the phase_state poll.
    // ---------------------------------------------------------------
    const _ATTACH_PH = "Attach your bundle .zip and press send →";
    function syncInputMode() {
        if (!document.querySelector("textarea")) return;  // login page
        const mode = window.__acInputMode || "normal";
        const restrict = (mode === "attach_only" || mode === "locked");
        document.querySelectorAll("textarea").forEach((el) => {
            if (restrict) {
                if (!el.readOnly) el.readOnly = true;
                el.classList.add("ac-attach-only");
                // Save the real placeholder once so we can restore it later.
                if (el.dataset.acOrigPh === undefined) {
                    el.dataset.acOrigPh = el.placeholder || "";
                }
                if (el.placeholder !== _ATTACH_PH) el.placeholder = _ATTACH_PH;
            } else {
                if (el.readOnly) el.readOnly = false;
                el.classList.remove("ac-attach-only");
                // Restore the original placeholder so the attach prompt doesn't
                // linger after switching back to a normal composer (New Chat).
                if (el.placeholder === _ATTACH_PH) {
                    el.placeholder = el.dataset.acOrigPh || "";
                }
                if (el.dataset.acOrigPh !== undefined) delete el.dataset.acOrigPh;
            }
        });
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

    // Locate the Readme link in the header. Cached after first hit.
    function _findReadmeButton() {
        if (window.__acReadmeBtn && document.body.contains(window.__acReadmeBtn)) {
            return window.__acReadmeBtn;
        }
        for (const el of document.querySelectorAll("a, button")) {
            if ((el.textContent || "").trim() === "Readme") {
                window.__acReadmeBtn = el;
                return el;
            }
        }
        return null;
    }

    // Flash a red outline on the Readme button. Triggered when the user clicks
    // a progress-only phase pill — the nudge is "phases advance via the chat
    // Proceed buttons; see the Readme to learn the flow".
    function _flashReadmeForHelp() {
        const btn = _findReadmeButton();
        if (!btn) return;
        btn.classList.remove("ac-readme-flash");
        void btn.offsetWidth;  // restart the animation on rapid repeat clicks
        btn.classList.add("ac-readme-flash");
        setTimeout(() => btn.classList.remove("ac-readme-flash"), 3200);
    }

    // ---- phase pill: custom instant tooltip + click-to-advance ----
    function _acPillTipEl() {
        let t = document.getElementById("ac-pill-tip");
        if (!t) {
            t = document.createElement("div");
            t.id = "ac-pill-tip";
            t.hidden = true;
            document.body.appendChild(t);
        }
        return t;
    }
    function _acShowPillTip(pill) {
        const t = _acPillTipEl();
        t.textContent = pill.dataset.tip || "";
        t.hidden = false;
        const r = pill.getBoundingClientRect();
        const w = t.offsetWidth || 240;
        let left = r.left + r.width / 2 - w / 2;
        left = Math.max(8, Math.min(left, window.innerWidth - w - 8));
        t.style.left = `${Math.round(left)}px`;
        t.style.top = `${Math.round(r.bottom + 8)}px`;
    }
    function _acHidePillTip() {
        const t = document.getElementById("ac-pill-tip");
        if (t) t.hidden = true;
    }
    // Advance by click-simulating the in-chat "▶ Proceed to Phase N" button.
    function _acAdvanceToPhase(num) {
        const wanted = `Proceed to Phase ${num}`;
        for (const b of document.querySelectorAll("button, a")) {
            if ((b.textContent || "").includes(wanted)) {
                try { b.scrollIntoView({block: "center"}); } catch (e) {}
                try { b.click(); } catch (e) {}
                return;
            }
        }
        _flashReadmeForHelp();  // button not present yet — nudge to the Readme
    }

    // ---------------------------------------------------------------
    // Persistent cost / context widget (bottom-left). Fed by the
    // phase_state.json poll (state.cost + state.context), refreshed by
    // the server after every turn — so the session spend and context use
    // are ALWAYS visible instead of buried in a per-turn footer line.
    // ---------------------------------------------------------------
    function _renderCostWidget(state) {
        if (!document.querySelector("textarea")) return;  // login page
        const cost = state && state.cost;
        const ctx = state && state.context;
        if (!cost) return;
        let w = document.getElementById("ac-cost-widget");
        if (!w) {
            w = document.createElement("div");
            w.id = "ac-cost-widget";
            w.innerHTML =
                '<div class="ac-cost-top">' +
                    '<span class="ac-cost-budget"></span>' +
                    '<span class="ac-cost-bar"><i></i></span>' +
                '</div>' +
                '<div class="ac-cost-ctx"></div>';
            document.body.appendChild(w);
        }
        const usd = cost.cumulative_usd || 0;
        const budget = cost.budget_usd || 0;
        const pct = Math.max(0, Math.min(100, cost.pct || 0));
        const ctxPct = ctx ? (ctx.pct || 0) : 0;
        w.querySelector(".ac-cost-budget").textContent =
            `💰 $${usd.toFixed(2)} / $${budget.toFixed(2)}`;
        const bar = w.querySelector(".ac-cost-bar > i");
        bar.style.width = pct + "%";
        bar.style.background = pct > 85 ? "hsl(0 72% 52%)"
            : pct > 60 ? "hsl(38 92% 50%)" : "hsl(var(--primary))";
        w.querySelector(".ac-cost-ctx").textContent =
            `🧠 ${ctxPct.toFixed(1)}% context`;
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

            // Always-visible cost / context widget.
            _renderCostWidget(state);

            // Cache the input mode for syncInputMode() (attach-only lock).
            window.__acInputMode = state.input_mode || "normal";

            // Pills are PROGRESS-ONLY: the guided wizard advances phases via
            // explicit "Proceed" buttons in chat, not by clicking pills.
            const sig = JSON.stringify({
                cur:   state.current,
                mode:  window.__acInputMode,
                // include `exists` — the advance pill turns on when the active
                // phase's artifact appears, so a status-only sig would miss it.
                items: (state.phases || []).map((x) => [x.id, x.status, x.exists]),
            });
            if (sig === _lastPhasePillsSig) return;
            _lastPhasePillsSig = sig;

            const ICON = {active: " ●", done: " ✓", skipped: " ⤼", pending: ""};
            const TIP  = {
                active:  "In progress",
                done:    "Completed",
                skipped: "Skipped (you started later in the pipeline)",
                pending: "Upcoming",
            };
            // 1–2 sentence explanation of each phase, shown on hover. Keyed by
            // phase id (config.py: plan / bundle / validate).
            const PHASE_DESC = {
                plan:     "Shape your idea into a one-page competition plan "
                        + "(task, data, metric, baseline, rules, ethics, schedule).",
                bundle:   "A fresh agent turns the plan into a full Codabench "
                        + "bundle and runs the baseline in Docker to prove it works.",
                validate: "Run the checks plus a Docker baseline execution, then "
                        + "get a PASS/FAIL report.",
            };
            const phases = state.phases || [];
            // The next phase becomes enterable once the active phase's artifact
            // exists (e.g. the plan is saved → you can proceed to Phase 2).
            const activeIdx = phases.findIndex((p) => p.status === "active");
            const advanceIdx = (activeIdx >= 0 && phases[activeIdx].exists)
                ? activeIdx + 1 : -1;

            pillsHost.innerHTML = "";
            phases.forEach((ph, idx) => {
                const pill = document.createElement("span");
                pill.className = "ac-pp ac-pp-" + ph.status;
                pill.dataset.phaseId     = ph.id;
                pill.dataset.phaseStatus = ph.status;
                const isAdvance = idx === advanceIdx;
                pill.textContent = `${idx + 1}. ${ph.title}`
                    + (isAdvance ? " ▸" : (ICON[ph.status] || ""));
                const desc = PHASE_DESC[ph.id] || ph.title;

                // Custom instant tooltip (native `title` is delayed/unreliable).
                pill.dataset.tip = isAdvance
                    ? `${desc}\n\n▸ Click to proceed to ${ph.title}.`
                    : (ph.status === "active"
                        ? `${desc}\n\n● In progress.`
                        : `${desc}\n\n${TIP[ph.status] || "Upcoming"}.`);
                pill.addEventListener("mouseenter", () => _acShowPillTip(pill));
                pill.addEventListener("mouseleave", _acHidePillTip);

                if (isAdvance) {
                    // Clickable: proceed to this phase.
                    pill.classList.add("ac-pp-advance");
                    pill.addEventListener("click", () => {
                        _acHidePillTip();
                        _acAdvanceToPhase(idx + 1);
                    });
                } else if (ph.status !== "active") {
                    // Progress-only — clicking nudges the Readme.
                    pill.classList.add("ac-pp-hint");
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
        // The greeting includes `_session \`<hex>\``. We must RE-SCAN every
        // call (not cache permanently): "New Chat" swaps the greeting and
        // session id WITHOUT a page reload, so a cached id would keep us
        // polling the previous session's phase_state.json — carrying its
        // stale input_mode (the bug where the validate-mode lock leaks across
        // New Chat, or fails to apply). On change we drop per-session caches.
        const text = document.body.textContent || "";
        // `\s*` (not `\s+`): the greeting footer renders the id in a chip as
        // "session" + "<hex>" with no whitespace between the two elements, so
        // textContent reads "session1dffc8eeffae". Older "session `hex`" prose
        // still matches too.
        const matches = text.match(/session\s*`?[a-f0-9]{8,16}`?/g);
        let sid = null;
        if (matches && matches.length) {
            const m = matches[matches.length - 1].match(/([a-f0-9]{8,16})/);
            sid = m ? m[1] : null;
        }
        if (sid && sid !== window.__acSessionId) {
            window.__acSessionId = sid;
            // Seed the lock from the fresh greeting so there's no typable
            // flash before the first phase_state poll; the JSON is then
            // authoritative (e.g. it flips to "normal" after validation).
            window.__acInputMode =
                text.includes("Attach your bundle") ? "attach_only" : "normal";
            // Drop every per-session cache so pills/panel/downloads re-sync.
            _lastPhasePillsSig = "";
            _lastFileListSig = "";
            _lastDownloadsSig = "";
            for (const k in _lastTagByUrl) delete _lastTagByUrl[k];
            window.__acPendingModeFetch = true;
            // Reset the cost widget — it re-populates from the new session's
            // first phase_state poll.
            document.getElementById("ac-cost-widget")?.remove();
        }
        return window.__acSessionId || null;
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

            // --- downloads footer ---
            const footer  = panel.querySelector(".ac-side-footer");
            const dlHost  = footer?.querySelector(".ac-dl-buttons");
            if (footer && dlHost) {
                // The footer always has at least workspace.zip in the
                // downloads list (built every turn). Keep it visible
                // throughout so the user always knows where to look.
                footer.setAttribute("data-state",
                    downloads.length > 0 ? "shown" : "hidden");

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
                                ? "Available after Phase 2 — Competition Creation finishes"
                                : d.kind === "validation"
                                ? "Available after Phase 3 — Validation finishes"
                                : "Not ready yet");
                        const sizeHTML = ready
                            ? `<span class="ac-dl-size">${_formatBytes(d.size)}</span>`
                            : `<span class="ac-dl-size ac-dl-pending">not ready</span>`;
                        const descHTML = d.desc
                            ? `<span class="ac-dl-desc">${d.desc}</span>`
                            : "";
                        el.innerHTML =
                            `<span class="ac-dl-top">` +
                                `<span class="ac-dl-label">${d.name}</span>` +
                                sizeHTML +
                            `</span>` +
                            descHTML;
                        dlHost.appendChild(el);
                    });
                }
            }
        } catch (e) {
            // Network blip / not-yet-written → silent.
        }
    }

    // NOTE: the "Publish to Codabench" panel form was removed — that flow is
    // not maintained for now. The backend route still exists but is unused.

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
        const bodyText = document.body.textContent;
        const isReady = READY_PHRASES.some((p) => bodyText.includes(p));
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

    // ---------------------------------------------------------------
    // (8) Top-right Settings menu.
    //
    // Chainlit ships two separate header controls: a standalone theme
    // toggle (#theme-toggle) and an avatar/user menu (#user-nav-button
    // → Settings + Logout). We fold both into ONE gear dropdown so the
    // top-right has a single, tidy settings affordance with:
    //   - a Light / Dark / System segmented switch, and
    //   - a Logout row.
    // The natives are hidden via login.css. Theme is applied by
    // mirroring Chainlit's own mechanism (toggle `dark` on <html> +
    // persist localStorage "theme"), verified to stick without a reload.
    // ---------------------------------------------------------------

    const AC_ICON = {
        gear: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/></svg>',
        sun: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M6.3 17.7l-1.4 1.4M19.1 4.9l-1.4 1.4"/></svg>',
        moon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"/></svg>',
        system: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="2" y="3" width="20" height="14" rx="2"/><path d="M8 21h8M12 17v4"/></svg>',
        logout: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"/><path d="M16 17l5-5-5-5M21 12H9"/></svg>',
        readme: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20"/><path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z"/></svg>',
    };

    function _acCurrentTheme() {
        const v = localStorage.getItem("theme");
        return (v === "light" || v === "dark" || v === "system") ? v : "system";
    }

    function _acApplyTheme(mode) {
        localStorage.setItem("theme", mode);
        const dark = mode === "dark" ||
            (mode === "system" &&
             window.matchMedia("(prefers-color-scheme: dark)").matches);
        document.documentElement.classList.toggle("dark", dark);
        // Reflect selection in the segmented control.
        document.querySelectorAll(".ac-theme-opt").forEach((o) => {
            o.classList.toggle("ac-active", o.dataset.mode === mode);
        });
    }

    function _acLogout() {
        // POST /logout clears the httpOnly auth cookie server-side; then
        // we bounce to /login (requireLogin forces a fresh sign-in).
        fetch("/logout", {method: "POST", credentials: "same-origin"})
            .catch(() => {})
            .finally(() => { window.location.href = "/login"; });
    }

    function _acCloseSettingsMenu() {
        const menu = document.getElementById("ac-settings-menu");
        const btn = document.getElementById("ac-settings-btn");
        if (menu) menu.hidden = true;
        if (btn) btn.setAttribute("aria-expanded", "false");
    }

    function _acPositionSettingsMenu() {
        const menu = document.getElementById("ac-settings-menu");
        const btn = document.getElementById("ac-settings-btn");
        if (!menu || !btn || menu.hidden) return;
        const r = btn.getBoundingClientRect();
        const w = menu.offsetWidth || 248;
        let left = r.right - w;                 // right-align to the gear
        left = Math.max(8, Math.min(left, window.innerWidth - w - 8));
        menu.style.top = `${Math.round(r.bottom + 8)}px`;
        menu.style.left = `${Math.round(left)}px`;
    }

    function _acFillUserName() {
        // Best-effort: show who's signed in. Cached after first fetch.
        if (window.__acUserFetched) return;
        window.__acUserFetched = true;
        fetch("/user", {credentials: "same-origin"})
            .then((r) => (r.ok ? r.json() : null))
            .then((u) => {
                if (!u) return;
                const name = u.display_name || u.identifier || "Signed in";
                const nameEl = document.querySelector(".ac-set-user-name");
                const avEl = document.querySelector(".ac-set-avatar");
                if (nameEl) nameEl.textContent = name;
                if (avEl) avEl.textContent = (name[0] || "?").toUpperCase();
            })
            .catch(() => {});
    }

    function _ensureSettingsMenu() {
        if (!document.querySelector("textarea")) return;  // login page

        // (a) the dropdown card — mounted once on <body> so header
        //     re-renders never blow it away.
        let menu = document.getElementById("ac-settings-menu");
        if (!menu) {
            const cur = _acCurrentTheme();
            const opt = (mode, icon, label) =>
                `<button type="button" class="ac-theme-opt${mode === cur ? " ac-active" : ""}" ` +
                `data-mode="${mode}" title="${label} theme">${icon}<span>${label}</span></button>`;
            menu = document.createElement("div");
            menu.id = "ac-settings-menu";
            menu.hidden = true;
            menu.innerHTML =
                `<div class="ac-set-user">` +
                    `<span class="ac-set-avatar">?</span>` +
                    `<span class="ac-set-user-meta">` +
                        `<span class="ac-set-user-name">Signed in</span>` +
                        `<span class="ac-set-user-sub">AutoCodabench</span>` +
                    `</span>` +
                `</div>` +
                `<div class="ac-set-label">Appearance</div>` +
                `<div class="ac-theme-seg">` +
                    opt("light", AC_ICON.sun, "Light") +
                    opt("dark", AC_ICON.moon, "Dark") +
                    opt("system", AC_ICON.system, "System") +
                `</div>` +
                `<div class="ac-set-sep"></div>` +
                `<button type="button" class="ac-set-row" id="ac-readme-row">` +
                    `${AC_ICON.readme}<span>Readme</span></button>` +
                `<button type="button" class="ac-set-row ac-set-danger" id="ac-logout-row">` +
                    `${AC_ICON.logout}<span>Log out</span></button>`;
            document.body.appendChild(menu);

            menu.querySelectorAll(".ac-theme-opt").forEach((o) => {
                o.addEventListener("click", (e) => {
                    e.stopPropagation();
                    _acApplyTheme(o.dataset.mode);
                });
            });
            // Readme: delegate to the native (now hidden) Readme button.
            menu.querySelector("#ac-readme-row").addEventListener("click", (e) => {
                e.stopPropagation();
                _acCloseSettingsMenu();
                const rb = document.getElementById("readme-button");
                if (rb) rb.click();
            });
            menu.querySelector("#ac-logout-row")
                .addEventListener("click", (e) => { e.stopPropagation(); _acLogout(); });
            // Keep clicks inside the menu from bubbling to the document
            // close-handler.
            menu.addEventListener("click", (e) => e.stopPropagation());
            _acFillUserName();
            // Sync the active indicator to the live theme on first build.
            _acApplyTheme(_acCurrentTheme());
        }

        // (b) the gear button. Anchor it right AFTER the phase pills when they
        //     exist — the user always sees the pills, so that's the most
        //     reliable spot and puts Settings next to the phase bar. On the
        //     landing (no pills yet) fall back to just before the hidden Readme
        //     button. Re-placed whenever Chainlit's re-render moves things.
        const pills  = document.getElementById("ac-phase-pills");
        const readme = _findReadmeButton();
        if (pills || (readme && readme.parentElement)) {
            let btn = document.getElementById("ac-settings-btn");
            if (!btn) {
                btn = document.createElement("button");
                btn.id = "ac-settings-btn";
                btn.type = "button";
                btn.setAttribute("aria-label", "Settings");
                btn.setAttribute("aria-haspopup", "menu");
                btn.setAttribute("aria-expanded", "false");
                btn.title = "Settings";
                btn.innerHTML = AC_ICON.gear;
                btn.addEventListener("click", (e) => {
                    e.stopPropagation();
                    const m = document.getElementById("ac-settings-menu");
                    if (!m) return;
                    const willOpen = m.hidden;
                    m.hidden = !willOpen;
                    btn.setAttribute("aria-expanded", String(willOpen));
                    if (willOpen) { _acFillUserName(); _acPositionSettingsMenu(); }
                });
            }
            if (pills) {
                // BEFORE the pills (to their left) — keeps the gear clear of
                // the fixed workspace sliver that hugs the right edge and would
                // otherwise cover it.
                if (pills.previousElementSibling !== btn) {
                    pills.insertAdjacentElement("beforebegin", btn);
                }
            } else if (readme.previousElementSibling !== btn) {
                readme.parentElement.insertBefore(btn, readme);
            }
        }

        // One-time global wiring: outside-click + Escape close, reposition.
        if (!window.__acSettingsWired) {
            window.__acSettingsWired = true;
            document.addEventListener("click", () => _acCloseSettingsMenu());
            document.addEventListener("keydown", (e) => {
                if (e.key === "Escape") _acCloseSettingsMenu();
            });
            window.addEventListener("resize", _acPositionSettingsMenu);
            window.addEventListener("scroll", _acPositionSettingsMenu, true);
        }
    }

    // ---------------------------------------------------------------
    // (9) Landing chooser cards.
    //
    // The two entry options are cl.Action buttons (server: _ask_entry_mode).
    // We tag each by its (clean, fixed) label so login.css can render them
    // as big product-style cards with an icon + description, instead of the
    // default emoji chat buttons. Idempotent; safe to run every tick.
    // ---------------------------------------------------------------
    const _AC_ENTRY = {
        "Create from scratch": "create",
        "Validate a bundle": "validate",
    };
    function _tagEntryCards() {
        document.querySelectorAll("button").forEach((el) => {
            const mode = _AC_ENTRY[(el.textContent || "").trim()];
            if (mode && el.dataset.acEntry !== mode) el.dataset.acEntry = mode;
        });
    }

    function tick() {
        syncInitGate();   // run first so the lock is up before anything else
        _currentSessionId();  // detect New-Chat session swap early (resets caches)
        if (window.__acPendingModeFetch) {
            // New session detected — pull its phase_state now instead of
            // waiting up to 2s, so the composer lock is correct immediately.
            window.__acPendingModeFetch = false;
            _refreshPhasePillsFromState();
        }
        syncInputMode();  // apply the attach-only / locked composer mode
        _injectSidePanel();      // sci-space-style persistent workspace panel
        _ensurePhasePills();     // header-row phase pills (slim, no chips)
        _ensureSettingsMenu();   // top-right gear: theme switch + logout
        _tagEntryCards();        // landing: tag the two entry options as cards
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
