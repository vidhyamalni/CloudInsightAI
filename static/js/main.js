/* CloudInsight AI — interactions */
(function () {
  "use strict";
  const $ = (s, c) => (c || document).querySelector(s);
  const $$ = (s, c) => [...(c || document).querySelectorAll(s)];
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const csrfToken = () => { const m = document.querySelector('meta[name="csrf-token"]'); return m ? m.content : ""; };
  const icons = () => { if (window.lucide) lucide.createIcons(); };
  document.addEventListener("DOMContentLoaded", icons);

  /* Toasts */
  window.toast = function (msg, type) {
    const root = $("#toast-root"); if (!root) return;
    const t = document.createElement("div");
    t.className = "toast" + (type === "error" ? " error" : "");
    t.textContent = msg; root.appendChild(t);
    setTimeout(() => { t.style.opacity = "0"; t.style.transition = "opacity .4s"; setTimeout(() => t.remove(), 400); }, 3800);
  };

  /* Reveal on scroll */
  const io = new IntersectionObserver((es) => es.forEach((e) => {
    if (e.isIntersecting) { e.target.classList.add("in"); io.unobserve(e.target); }
  }), { threshold: 0.12 });
  $$(".reveal").forEach((el) => io.observe(el));

  /* Landing nav */
  const nav = $("#siteNav");
  if (nav) {
    window.addEventListener("scroll", () => nav.classList.toggle("scrolled", window.scrollY > 8), { passive: true });
    const burger = $("#navBurger");
    if (burger) burger.addEventListener("click", () => nav.classList.toggle("open"));
  }

  /* Solutions tabs */
  $$(".tab").forEach((b) => b.addEventListener("click", () => {
    $$(".tab").forEach((x) => x.classList.remove("is-active"));
    $$(".tab-panel").forEach((x) => x.classList.remove("is-active"));
    b.classList.add("is-active");
    const p = $(`.tab-panel[data-panel="${b.dataset.tab}"]`); if (p) p.classList.add("is-active");
  }));

  /* Auth tabs + password toggle */
  $$(".auth-tab").forEach((b) => b.addEventListener("click", () => switchAuth(b.dataset.auth)));
  $$("[data-switch]").forEach((a) => a.addEventListener("click", (e) => { e.preventDefault(); switchAuth(a.dataset.switch); }));
  function switchAuth(mode) {
    $$(".auth-tab").forEach((x) => x.classList.toggle("is-active", x.dataset.auth === mode));
    $$(".auth-form").forEach((f) => f.classList.toggle("is-active", f.dataset.form === mode));
  }
  $$(".pw-toggle").forEach((b) => b.addEventListener("click", () => {
    const i = document.getElementById(b.dataset.target);
    i.type = i.type === "password" ? "text" : "password";
    b.innerHTML = `<i data-lucide="${i.type === "password" ? "eye" : "eye-off"}"></i>`; icons();
  }));

  /* Contact form (landing) */
  const cf = $("#contactForm");
  if (cf) cf.addEventListener("submit", async (e) => {
    e.preventDefault();
    const fd = new FormData(cf);
    try {
      const r = await fetch("/contact", { method: "POST", headers: { "Content-Type": "application/json", "X-CSRF-Token": csrfToken() },
        body: JSON.stringify(Object.fromEntries(fd)) });
      if (!r.ok) throw new Error();
      cf.reset(); toast("Message received — we'll get back to you soon.");
    } catch { toast("Could not send the message. Please try again.", "error"); }
  });

  /* Analysis loading overlay: real elapsed-time counter, no artificial delay, no fake progress */
  const overlay = $("#analysis-overlay"), form = $("#analysis-form");
  if (overlay && form) {
    const timerEl = $("#loadTimer"); let busy = false;
    const fmt = (s) => String(Math.floor(s / 60)).padStart(2, "0") + ":" + String(s % 60).padStart(2, "0");
    form.addEventListener("submit", (e) => {
      if (busy) { e.preventDefault(); return; }
      busy = true;                                   // the browser submits the form right away
      overlay.classList.add("show");
      const t0 = performance.now();
      setInterval(() => { if (timerEl) timerEl.textContent = fmt(Math.floor((performance.now() - t0) / 1000)); }, 250);
    });
    window.addEventListener("pageshow", (ev) => { if (ev.persisted) { overlay.classList.remove("show"); busy = false; } });
  }

  /* Sidebar (mobile) */
  const shell = $("#appShell"), sb = $("#sideBurger");
  if (sb) sb.addEventListener("click", () => shell.classList.toggle("nav-open"));

  /* Typewriter helper */
  async function typeInto(el, text, speed) {
    if (!el) return;
    el.classList.add("typing");
    for (let i = 2; i <= text.length; i += 2) { el.textContent = text.slice(0, i); await sleep(speed || 12); }
    el.textContent = text; el.classList.remove("typing");
  }

  /* ---- Report page: AI insights ---- */
  const aiBox = $("#ai-insights");
  if (aiBox) {
    const esc = (s) => { const d = document.createElement("div"); d.textContent = s == null ? "" : String(s); return d.innerHTML; };
    const card = (title, icon, body) =>
      `<div class="ai-card"><h4><i data-lucide="${icon}"></i>${title}</h4><p>${esc(body)}</p></div>`;
    const list = (title, icon, items) =>
      `<div class="ai-card"><h4><i data-lucide="${icon}"></i>${title}</h4><ul class="dot-list">${items.map((x) => `<li>${esc(x)}</li>`).join("")}</ul></div>`;

    function render(ins, offline, offlineMsg, reason) {
      const notice = (offline && offlineMsg)
        ? `<div class="notice warn"><i data-lucide="cloud-off"></i><div><strong>${esc(offlineMsg)}</strong>
           Showing a data-derived summary generated from your own report instead.${reason ? `<br><small>Reason: ${esc(reason)}</small>` : ""}</div></div>` : "";
      aiBox.innerHTML = notice + `
        <span class="ai-tag ${offline ? "offline" : ""}">${offline ? "Offline data-derived summary" : "Generated by Gemini AI"}</span>
        <p class="ai-summary typed-target"></p>
        <div class="ai-grid">
          ${card("Cost Insights", "circle-dollar-sign", ins.cost_insights)}
          ${card("Security Insights", "shield-check", ins.security_insights)}
          ${card("Storage Insights", "hard-drive", ins.storage_insights)}
          ${card("Risk Explanation", "alert-triangle", ins.risk_explanation)}
        </div>
        <div class="ai-lists">
          ${list("Optimization Suggestions", "gauge", ins.optimization || [])}
          ${list("Cloud Architecture Suggestions", "network", ins.architecture || [])}
          ${list("Actionable Recommendations", "check-circle-2", ins.recommendations || [])}
        </div>`;
      icons();
      typeInto($(".typed-target", aiBox), ins.summary || "", 10);
    }

    fetch(`/api/ai/insights/${aiBox.dataset.report}`).then((r) => r.json()).then((j) => {
      if (j.available) render(j.insights, false);
      else if (j.fallback) render(j.fallback, true, j.message, j.error);
      else {
        aiBox.innerHTML = `<div class="notice warn"><i data-lucide="cloud-off"></i><div><strong>${esc(j.message || "AI analysis is temporarily unavailable. Your cloud analysis is still available.")}</strong>${j.error ? `<br><small>Reason: ${esc(j.error)}</small>` : ""}</div></div>`;
        icons();
      }
    }).catch((err) => {
      aiBox.innerHTML = `<div class="notice warn"><i data-lucide="cloud-off"></i><div>
        <strong>AI analysis is temporarily unavailable. Your cloud analysis is still available.</strong><br><small>Reason: the insights request failed (${esc(err && err.message ? err.message : "network or server error")}). Check the terminal running app.py for the error.</small></div></div>`;
      icons();
    });
  }

  /* ---- AI Assistant chat ---- */
  const chatForm = $("#chatForm");
  if (chatForm) {
    const shell = $("#chatShell"), scroll = $("#chatScroll"), input = $("#chatText");
    const avatar = $("#aiAvatar"), statusEl = $("#aiStatus"), scope = $("#chatScope");
    const idleText = statusEl ? statusEl.textContent : "";
    let busy = false;
    const think = (on) => {
      if (avatar) avatar.classList.toggle("is-thinking", on);
      if (statusEl) statusEl.textContent = on ? "Thinking…" : idleText;
    };
    function addMsg(role) {
      const m = document.createElement("div"); m.className = "msg " + role;
      const b = document.createElement("div"); b.className = "bubble"; m.appendChild(b);
      scroll.appendChild(m); scroll.scrollTop = scroll.scrollHeight; return b;
    }
    scroll.scrollTop = scroll.scrollHeight;
    if (scope) scope.addEventListener("change", () => { window.location.href = "/assistant?report=" + encodeURIComponent(scope.value); });
    chatForm.addEventListener("submit", async (e) => {
      e.preventDefault();
      const q = input.value.trim();
      if (!q || busy) return;
      busy = true; input.value = "";
      addMsg("user").textContent = q;
      const t = addMsg("ai"); t.classList.add("typing-dots"); think(true);
      try {
        const r = await fetch("/api/ai/ask", { method: "POST", headers: { "Content-Type": "application/json", "X-CSRF-Token": csrfToken() },
          body: JSON.stringify({ question: q, report_id: Number(shell.dataset.report) || 0 }) });
        const j = await r.json();
        t.classList.remove("typing-dots");
        await typeInto(t, j.answer || "Sorry, something went wrong.", 8);
        if (j.gemini === false && j.error) {
          const n = document.createElement("div"); n.className = "ai-error-note";
          n.textContent = "Gemini unavailable: " + j.error; t.parentElement.appendChild(n);
        }
      } catch {
        t.classList.remove("typing-dots");
        t.textContent = "AI analysis is temporarily unavailable. Your cloud analysis is still available.";
      }
      think(false); scroll.scrollTop = scroll.scrollHeight; busy = false; input.focus();
    });
    $$(".chip").forEach((c) => c.addEventListener("click", () => { input.value = c.dataset.q; chatForm.requestSubmit(); }));
  }

  /* ---- Company file: AI requirement extraction ---- */
  const upZone = $("#uploadZone"), upBtn = $("#uploadBtn"), upInput = $("#companyFile"), upRes = $("#extractResult");
  if (upBtn && upInput && upZone) {
    const F_LABELS = { company_name: "Company", industry: "Industry", region: "Region", monthly_budget: "Budget/mo",
      storage_gb: "Storage (GB)", expected_users: "Users", compute_req: "Compute", data_sensitivity: "Sensitivity",
      availability: "Availability", compliance: "Compliance" };
    const esc2 = (s) => { const d = document.createElement("div"); d.textContent = s == null ? "" : String(s); return d.innerHTML; };

    function fillFields(fields) {
      Object.entries(fields).forEach(([k, v]) => {
        const el = document.getElementById(k);
        if (!el) return;
        if (el.tagName === "SELECT") {
          const opt = [...el.options].find((o) => o.value === String(v));
          if (opt) el.value = String(v);
        } else if (k !== "company_name" || !el.value.trim()) {
          el.value = v;
        }
        el.classList.add("extracted");
        setTimeout(() => el.classList.remove("extracted"), 2400);
      });
    }

    async function doExtract(file) {
      upRes.hidden = false;
      upRes.innerHTML = `<span class="extract-chip loading">Extracting requirements with AI…</span>`;
      const fd = new FormData(); fd.append("file", file);
      try {
        const r = await fetch("/api/extract", { method: "POST", headers: { "X-CSRF-Token": csrfToken() }, body: fd });
        const j = await r.json();
        if (!j.ok) throw new Error(j.error || "Extraction failed.");
        fillFields(j.fields);
        const chips = Object.entries(j.fields)
          .map(([k, v]) => `<span class="extract-chip">${F_LABELS[k] || k}: <b>${esc2(v)}</b></span>`).join("");
        upRes.innerHTML = `<p class="extract-title">Extracted requirements (${j.source === "gemini" ? "Gemini AI" : "rule-based parser"}) — highlighted fields below were auto-filled and stay editable:</p><div class="extract-chips">${chips}</div>`;
        toast("Requirements extracted — review the highlighted fields.");
      } catch (err) {
        upRes.innerHTML = `<span class="extract-chip error">${esc2(err.message)}</span>`;
      }
    }

    upBtn.addEventListener("click", () => upInput.click());
    upInput.addEventListener("change", () => { if (upInput.files[0]) doExtract(upInput.files[0]); });
    ["dragover", "dragleave", "drop"].forEach((ev) => upZone.addEventListener(ev, (e) => {
      e.preventDefault();
      upZone.classList.toggle("drag", ev === "dragover");
      if (ev === "drop" && e.dataTransfer.files[0]) {
        upInput.files = e.dataTransfer.files;
        doExtract(e.dataTransfer.files[0]);
      }
    }));
  }
  /* ---- Local date/time: stored timestamps are UTC, show them in the viewer's own time zone ---- */
  (function localTimes() {
    const F = {
      date: { day: "2-digit", month: "long", year: "numeric" },
      month: { month: "long" }, year: { year: "numeric" },
      time: { hour: "2-digit", minute: "2-digit", hour12: true },
      datetime: { day: "2-digit", month: "long", year: "numeric", hour: "2-digit", minute: "2-digit", hour12: true },
    };
    F.full = F.datetime;
    $$(".js-local[data-utc]").forEach((el) => {
      const d = new Date(el.dataset.utc);
      const f = F[el.dataset.fmt];
      if (isNaN(d) || !f) return;
      try { el.textContent = d.toLocaleString("en-GB", f).replace(" am", " AM").replace(" pm", " PM"); el.title = "Shown in your local time"; } catch { /* keep UTC fallback */ }
    });
  })();

  /* ---- History page: filter + expandable details ---- */
  const hTable = $("#historyTable");
  if (hTable) {
    $$(".js-details", hTable).forEach((b) => b.addEventListener("click", () => {
      const detail = b.closest("tr").nextElementSibling;
      const open = detail.hidden;
      detail.hidden = !open; b.setAttribute("aria-expanded", String(open)); b.textContent = open ? "Hide" : "Details";
    }));
    const f = $("#historyFilter"), empty = $("#historyEmpty");
    if (f) f.addEventListener("input", () => {
      const term = f.value.trim().toLowerCase(); let shown = 0;
      $$(".h-row", hTable).forEach((row) => {
        const hit = !term || row.textContent.toLowerCase().includes(term);
        row.hidden = !hit; if (hit) shown++;
        const det = row.nextElementSibling; if (det && !hit) det.hidden = true;
      });
      if (empty) empty.hidden = shown > 0;
    });
  }

  /* ---- Analysis form: pre-fill from a previously analysed company (inputs only) ---- */
  const profSel = $("#profileSelect");
  if (profSel && window.COMPANY_PROFILES) {
    profSel.addEventListener("change", () => {
      const p = window.COMPANY_PROFILES[Number(profSel.value)];
      if (profSel.value === "" || !p) return;
      Object.entries(p).forEach(([k, v]) => {
        if (k === "id" || v == null) return;
        const el = document.getElementById(k);
        if (!el) return;
        if (el.tagName === "SELECT") { const o = [...el.options].find((x) => x.value === String(v) || x.text === String(v)); if (o) el.value = o.value; }
        else el.value = v;
        el.classList.add("extracted"); setTimeout(() => el.classList.remove("extracted"), 2400);
      });
    });
  }

  /* ---- Analysis form: keep what the user typed after a validation error ---- */
  if (window.FORM_VALUES && Object.keys(window.FORM_VALUES).length) {
    Object.entries(window.FORM_VALUES).forEach(([k, v]) => {
      const el = document.getElementById(k);
      if (!el || v == null) return;
      if (el.tagName === "SELECT") { const o = [...el.options].find((x) => x.value === String(v) || x.text === String(v)); if (o) el.value = o.value; }
      else el.value = v;
    });
  }
})();
