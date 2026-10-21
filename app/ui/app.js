// JobScraper window: start/stop the run, show its progress, and list applications that need the user.

const STAGES = [
  { id: "collect", name: "Collect jobs", icon: "1", weight: 15 },
  { id: "parse", name: "Read descriptions", icon: "2", weight: 30 },
  { id: "analyze", name: "AI score vs CV", icon: "3", weight: 25 },
  { id: "mail", name: "Mail", icon: "4", weight: 5 },
  { id: "apply", name: "Apply", icon: "5", weight: 25 },
];
const STATUS_TEXT = {
  applied: "Submitted", unconfirmed: "Submitted (unconfirmed)", ready: "Filled in, not submitted",
  needs_manual: "Needs you", external: "Company site only", already_applied: "Already applied",
  closed: "Job closed", no_apply_button: "No apply button", external_skipped: "Company site only",
};

const $ = (id) => document.getElementById(id);
const api = () => window.pywebview.api;
const state = { running: false, stages: {}, counts: { found: 0, parsed: 0, scored: 0, applied: 0, manual: 0 },
                current: null, items: [], selected: null };

function escapeHtml(text) {
  return String(text ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

function toast(text) {
  const el = $("toast");
  el.textContent = text;
  el.classList.remove("hidden");
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => el.classList.add("hidden"), 3200);
}

// ---------- progress -------------------------------------------------------------------
function resetProgress() {
  state.stages = {};
  for (const s of STAGES) state.stages[s.id] = { status: "pending", done: 0, total: 0, info: "" };
  state.counts = { found: 0, parsed: 0, scored: 0, applied: 0, manual: 0 };
  $("log").textContent = "";
  renderStages();
  renderCounters();
  setNow("Getting ready…");
  setPercent(0);
}

const CHECK = '<svg aria-hidden="true" viewBox="0 0 12 12" width="10" height="10"><path d="M2.5 6.2l2.2 2.2 4.8-4.8" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg>';

// The stages are a diagram of numbered nodes joined by links. It is built once and then only its classes
// and ring offsets change, so the CSS transitions (ring filling, link filling, node lighting up) can play.
const RING = 2 * Math.PI * 20;

function buildStages() {
  $("stages").innerHTML = STAGES.map((s, i) => `<li class="stage pending" id="stage-${s.id}">
      <div class="node"><svg class="ring" aria-hidden="true" viewBox="0 0 48 48">
        <circle class="r-track" cx="24" cy="24" r="20"/>
        <circle class="r-fill" cx="24" cy="24" r="20" stroke-dasharray="${RING}" stroke-dashoffset="${RING}"/></svg>
        <span class="s-num">${s.icon}</span><span class="s-check">${CHECK}</span></div>
      ${i < STAGES.length - 1 ? '<div class="link" aria-hidden="true"><i></i></div>' : ""}
      <div class="s-name">${s.name}</div><div class="s-info"></div></li>`).join("");
}

function renderStages() {
  if (!$("stages").children.length) buildStages();
  for (const s of STAGES) {
    const st = state.stages[s.id] || { status: "pending" };
    const el = $(`stage-${s.id}`);
    const share = st.total ? Math.min(1, st.done / st.total) : 0;
    el.className = `stage ${st.status}${st.status === "active" && !st.total ? " waiting" : ""}`;
    el.setAttribute("aria-label", `Stage ${s.icon}: ${s.name}`);
    const offset = st.status === "active" ? RING * (1 - Math.max(share, 0.04)) : st.status === "pending" ? RING : 0;
    el.querySelector(".r-fill").setAttribute("stroke-dashoffset", offset.toFixed(2));
    el.querySelector(".s-num").textContent = st.status === "error" ? "!" : s.icon;
    el.querySelector(".s-info").textContent =
      st.info || { pending: "Waiting", active: "Running…", done: "Done", skipped: "Skipped", error: "Error" }[st.status];
  }
}

function renderCounters() {
  const c = state.counts;
  const items = [["Jobs found", c.found, ""], ["Read", c.parsed, ""], ["Scored by AI", c.scored, ""],
                 ["Submitted", c.applied, "ok"], ["Needs you", c.manual, "warn"]];
  // Each count's bar is its share of the jobs found, so the strip reads as a funnel.
  const base = Math.max(c.found, 1);
  $("counters").innerHTML = items.map(([label, value, cls]) =>
    `<div class="counter ${cls}${value ? "" : " zero"}"><b>${value}</b><span>${label}</span>
      <div class="f-bar"><i style="width:${Math.min(100, Math.round((value / base) * 100))}%"></i></div></div>`).join("");
}

function overallPercent() {
  let sum = 0;
  for (const s of STAGES) {
    const st = state.stages[s.id];
    if (!st) continue;
    if (st.status === "done" || st.status === "skipped" || st.status === "error") sum += s.weight;
    else if (st.status === "active") sum += s.weight * (st.total ? Math.min(1, st.done / st.total) : 0.05);
  }
  return Math.round(sum);
}

function setPercent(value) {
  $("percent").textContent = `${value}%`;
  $("bar-fill").style.width = `${value}%`;
  $("bar").setAttribute("aria-valuenow", value);
}

function setNow(text) { $("now-text").textContent = text; }

function setRunning(running, label) {
  state.running = running;
  $("start-btn").classList.toggle("running", running);
  $("start-label").textContent = running ? "Stop" : "Start";
  $("launch-title").textContent = running ? "Working on applications" : label === "done" ? "Run finished"
    : label === "error" ? "Run stopped" : "Ready to look for jobs";
  $("launch-sub").textContent = running
    ? "You can minimise the window: progress is shown below, and jobs that need you appear on the right."
    : "Collects fresh jobs, scores them against your CV and applies to the best ones.";
  document.querySelectorAll(".modes input, #dry-run").forEach((el) => (el.disabled = running));
}

function handle(event) {
  const st = state.stages[event.stage];
  switch (event.event) {
    case "stage":
      if (!st) break;
      if (event.state === "start") {
        st.status = "active"; st.total = event.total || 0; st.done = 0;
        st.info = event.total ? `0 of ${event.total}` : "Running…";
        if (event.stage === "apply") st.info = event.total ? `0 of ${event.total} jobs` : "No suitable jobs";
      } else if (event.state === "done") {
        st.status = "done";
        st.info = stageSummary(event);
        if (event.stage === "collect" && event.found !== undefined) state.counts.found = event.found;
        if (event.stage === "parse") state.counts.parsed = event.total || state.counts.parsed;
        if (event.stage === "analyze") state.counts.scored = (event.analyzed || 0) + (event.skipped || 0);
      } else if (event.state === "skipped") {
        st.status = "skipped"; st.info = "Skipped";
      } else if (event.state === "error") {
        st.status = "error"; st.info = "Error, see the log";
      }
      break;
    case "step":
      if (!st) break;
      st.status = "active";
      st.done = event.done; st.total = event.total || st.total;
      st.info = st.total ? `${event.done} of ${st.total}` : "Running…";
      if (event.text) setNow(event.text);
      if (event.stage === "collect" && event.found !== undefined) state.counts.found = event.found;
      if (event.stage === "parse") state.counts.parsed = event.done;
      if (event.stage === "analyze") state.counts.scored = event.done;
      break;
    case "result":
      if (event.status === "applied" || event.status === "unconfirmed") state.counts.applied += 1;
      if (event.status === "needs_manual" || event.status === "external") state.counts.manual += 1;
      log(`→ ${STATUS_TEXT[event.status] || event.status}: ${event.title}${event.company ? " · " + event.company : ""}`);
      refreshLists();
      break;
    case "log":
      log(event.text);
      break;
    case "run":
      if (event.state === "done") setNow(event.error ? `Done with an error: ${event.error}` : "Done");
      break;
    case "exit":
      finish(event.code);
      return;
  }
  renderStages();
  renderCounters();
  setPercent(overallPercent());
}

function stageSummary(event) {
  if (event.stage === "collect") return `Found: ${event.found ?? 0}`;
  if (event.stage === "parse") return `Read: ${event.total ?? 0}`;
  if (event.stage === "analyze") return `Scored: ${(event.analyzed || 0) + (event.skipped || 0)}${event.failed ? `, ${event.failed} failed` : ""}`;
  if (event.stage === "apply") return `Submitted: ${(event.applied || 0) + (event.unconfirmed || 0)}`;
  return "Done";
}

function log(text) {
  const el = $("log");
  el.textContent += text + "\n";
  if (el.textContent.length > 60000) el.textContent = el.textContent.slice(-40000);
  el.scrollTop = el.scrollHeight;
}

function finish(code) {
  for (const s of STAGES) {
    const st = state.stages[s.id];
    if (st.status === "active") st.status = code === 0 || code === 2 ? "done" : "error";
  }
  const stopped = state.stopRequested;
  state.stopRequested = false;
  setRunning(false, code === 0 || code === 2 ? "done" : "error");
  renderStages();
  setPercent(code === 0 || code === 2 ? 100 : overallPercent());
  setNow(stopped ? "Stopped. Collected data is saved; the next run continues from here." :
    code === 0 || code === 2 ? `Done: ${state.counts.applied} submitted, ${state.counts.manual} need you` :
    "The run ended with an error, see the log");
  refreshLists();
  refreshFacts();
}

async function poll() {
  try {
    const events = await api().poll();
    for (const event of events) handle(event);
  } catch (e) { /* the window is closing */ }
  setTimeout(poll, state.running ? 350 : 1200);
}

// ---------- start / stop ---------------------------------------------------------------
async function toggleRun() {
  if (state.running) {
    state.stopRequested = true;
    setNow("Stopping…");
    await api().stop();
    return;
  }
  const mode = document.querySelector("input[name=mode]:checked").value;
  const dryRun = $("dry-run").checked;
  resetProgress();
  if (mode === "apply") {
    for (const id of ["collect", "parse", "analyze", "mail"]) state.stages[id].status = "skipped";
    renderStages();
  }
  const result = await api().start(dryRun, mode === "apply");
  if (!result.ok) { toast(result.error); return; }
  setRunning(true);
  setNow(mode === "apply" ? "Opening LinkedIn to apply…" : "Opening LinkedIn and searching for jobs…");
}

// ---------- lists ----------------------------------------------------------------------
async function refreshLists() {
  const [manual, applied] = await Promise.all([api().manual_list(), api().applied_list()]);
  state.items = manual;
  $("manual-count").textContent = manual.length;
  $("applied-count").textContent = applied.length;
  $("manual-list").innerHTML = manual.length ? manual.map((item, i) => `
    <div class="job">
      <div class="job-top"><div><div class="job-company">${escapeHtml(item.company || "Company")}</div>
        <div class="job-title">${escapeHtml(item.title)}</div></div>
        ${item.score ? `<span class="score">${item.score}</span>` : ""}</div>
      <span class="chip">${escapeHtml(item.label)}</span>
      <div class="job-reason">${escapeHtml(item.reason)}</div>
      <div class="job-actions"><button class="primary small" data-open="${i}">Details</button>
        <button class="secondary small" data-url="${escapeHtml(item.apply_url || item.link)}">${item.apply_url ? "Open the form" : "Open the job"}</button></div>
    </div>`).join("") :
    `<div class="empty"><div class="big">Nothing needs you right now</div>Jobs where the app stopped and needs you will appear here.</div>`;
  $("applied-list").innerHTML = applied.length ? applied.map((item) => `
    <div class="job">
      <div class="job-top"><div><div class="job-company">${escapeHtml(item.company || "Company")}</div>
        <div class="job-title">${escapeHtml(item.title)}</div></div>
        ${item.score ? `<span class="score">${item.score}</span>` : ""}</div>
      <span class="chip ok">${item.manual ? "Applied myself" : "Submitted by the app"}</span>
      <div class="meta" style="margin-top:8px">${escapeHtml(formatDate(item.updated))}${item.ats ? " · " + escapeHtml(item.ats) : ""}</div>
      <div class="job-actions" style="margin-top:10px"><button class="secondary small" data-url="${escapeHtml(item.link)}">Job</button>
        ${item.cv_pdf ? `<button class="secondary small" data-path="${escapeHtml(item.cv_pdf)}">CV</button>` : ""}</div>
    </div>`).join("") : `<div class="empty"><div class="big">No applications submitted yet</div>They will appear here after the first run.</div>`;
}

function formatDate(value) {
  if (!value) return "";
  const date = new Date(value.replace(" ", "T") + "Z");
  return date.toLocaleString("ru-RU", { day: "numeric", month: "long", hour: "2-digit", minute: "2-digit" });
}

async function openDetails(index) {
  const item = state.items[index];
  state.selected = item;
  $("m-company").textContent = item.company || "Company";
  $("m-title").textContent = item.title;
  $("m-label").textContent = item.label;
  $("m-reason").textContent = item.reason;
  $("m-summary").innerHTML = '<span class="loading"></span>AI is writing a short summary…';
  $("m-todo").innerHTML = "";
  $("m-open-form").classList.toggle("hidden", !item.apply_url);
  $("m-open-cv").classList.toggle("hidden", !item.cv_pdf);
  state.returnFocus = document.activeElement;
  $("modal").classList.remove("hidden");
  $("modal-close").focus();
  const info = await api().explain(item.link);
  if (state.selected !== item) return;
  $("m-summary").textContent = info.summary || "No summary available.";
  const todo = (info.todo && info.todo.length) ? info.todo : defaultTodo(item);
  $("m-todo").innerHTML = todo.map((t) => `<li>${escapeHtml(t)}</li>`).join("");
  if (info.note) toast(info.note);
}

function defaultTodo(item) {
  return [item.apply_url ? "Open the form on the company site." : "Open the job on LinkedIn.",
          "Fill in what the app stopped at and submit the application.",
          "Click \"I applied myself\" so the app leaves this job alone."];
}

function closeModal() {
  if ($("modal").classList.contains("hidden")) return;
  $("modal").classList.add("hidden");
  state.selected = null;
  // Give keyboard focus back to the button that opened the dialog.
  if (state.returnFocus && document.contains(state.returnFocus)) state.returnFocus.focus();
}

// ---------- settings / facts -------------------------------------------------------------
async function refreshFacts() {
  const s = await api().state();
  state.root = s.root;
  $("auto-submit").checked = s.auto_submit;
  const facts = [`Limit: ${s.daily_limit} applications a day`, `Min. score: ${s.min_score}`,
                 s.apply_external ? "Easy Apply and company sites" : "Easy Apply only"];
  const today = await api().today();
  const sent = (today.applied || 0) + (today.unconfirmed || 0);
  facts.push(`Submitted today: ${sent}`);
  let html = facts.map((f) => `<span class="fact">${escapeHtml(f)}</span>`).join("");
  if (!s.signed_in) html += `<span class="fact warn">Not signed in to LinkedIn: run main.py login</span>`;
  if (!s.gmail) html += `<span class="fact warn">Gmail is not connected</span>`;
  if (!s.auto_submit) html += `<span class="fact warn">Auto-submit is off: applications are only filled in</span>`;
  if (!s.ready) html += `<span class="fact warn">Python environment not found: ${escapeHtml(s.root)}</span>`;
  $("facts").innerHTML = html;
  if (!state.running && s.running) setRunning(true);
}

// ---------- wiring ---------------------------------------------------------------------
document.addEventListener("click", async (e) => {
  const target = e.target.closest("[data-open],[data-url],[data-path],.tab");
  if (!target) return;
  if (target.classList.contains("tab")) {
    document.querySelectorAll(".tab").forEach((t) => {
      t.classList.toggle("active", t === target);
      t.setAttribute("aria-selected", String(t === target));
    });
    $("manual-list").classList.toggle("hidden", target.dataset.tab !== "manual");
    $("applied-list").classList.toggle("hidden", target.dataset.tab !== "applied");
  } else if (target.dataset.open !== undefined) openDetails(Number(target.dataset.open));
  else if (target.dataset.url) api().open_url(target.dataset.url);
  else if (target.dataset.path) api().open_path(target.dataset.path);
});

window.addEventListener("pywebviewready", async () => {
  resetProgress();
  setNow("Waiting to start");
  $("start-btn").addEventListener("click", toggleRun);
  $("folder-btn").addEventListener("click", () => { if (state.root) api().open_path(state.root + "\\results"); });
  $("report-btn").addEventListener("click", async () => { if (!(await api().open_report())) toast("No report yet"); });
  $("auto-submit").addEventListener("change", async (e) => {
    const ok = await api().set_auto_submit(e.target.checked);
    toast(ok ? (e.target.checked ? "Auto-submit is on" : "Auto-submit is off: applications are only filled in")
             : "Could not change config\\applicant.json");
    refreshFacts();
  });
  $("modal-close").addEventListener("click", closeModal);
  $("modal").addEventListener("click", (e) => { if (e.target.id === "modal") closeModal(); });
  $("m-open-form").addEventListener("click", () => api().open_url(state.selected.apply_url));
  $("m-open-job").addEventListener("click", () => api().open_url(state.selected.link));
  $("m-open-cv").addEventListener("click", () => api().open_path(state.selected.cv_pdf));
  $("m-applied").addEventListener("click", async () => {
    await api().mark_applied(state.selected.link);
    toast("Marked as applied by you");
    closeModal(); refreshLists();
  });
  $("m-hide").addEventListener("click", async () => {
    await api().hide(state.selected.link);
    toast("Hidden from the list");
    closeModal(); refreshLists();
  });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeModal(); });
  await refreshFacts();
  await refreshLists();
  poll();
});
