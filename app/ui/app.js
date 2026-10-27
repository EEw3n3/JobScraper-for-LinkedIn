// JobScraper window: start/stop the run, show its progress, and list applications that need the user.
// LinkedIn and cv.lv are two separate workspaces: each has its own theme, layout, progress, log and lists,
// and a run always belongs to one of them.

const SITES = {
  linkedin: {
    name: "LinkedIn",
    stages: ["Search LinkedIn", "Read descriptions", "AI score vs CV", "Mail", "Apply"],
    idle: "LinkedIn: ready to look for jobs",
    sub: "Finds fresh LinkedIn jobs for your roles, scores them against your CV and applies with Easy Apply and on company sites.",
    starting: "Searching LinkedIn for jobs…",
    login: "Not signed in to LinkedIn: run main.py login",
  },
  cvlv: {
    name: "cv.lv",
    stages: ["cv.lv IT jobs", "Read the ads", "AI score vs CV", "Mail", "Apply"],
    idle: "cv.lv: ready to look for jobs",
    sub: "Reads every cv.lv IT job, your saved ones first, scores them against your CV and applies with the cv.lv form or on the employer's site.",
    starting: "Reading cv.lv jobs…",
    login: "Not signed in to cv.lv: run main.py login --site cvlv",
  },
};
const STAGES = [
  { id: "collect", icon: "1", weight: 15 },
  { id: "parse", icon: "2", weight: 30 },
  { id: "analyze", icon: "3", weight: 25 },
  { id: "mail", icon: "4", weight: 5 },
  { id: "apply", icon: "5", weight: 25 },
];
const STATUS_TEXT = {
  applied: "Submitted", unconfirmed: "Submitted (unconfirmed)", ready: "Filled in, not submitted",
  needs_manual: "Needs you", external: "Company site only", already_applied: "Already applied",
  closed: "Job closed", no_apply_button: "No apply button", external_skipped: "Company site only",
};

const $ = (id) => document.getElementById(id);
const api = () => window.pywebview.api;

function newView() {
  return { stages: {}, counts: { found: 0, parsed: 0, scored: 0, applied: 0, manual: 0 }, log: "",
           now: "Waiting to start", percent: 0, label: null, mailOnly: false, mailCounts: null, items: [] };
}
const state = { running: false, site: "linkedin", runSite: null, views: { linkedin: newView(), cvlv: newView() },
                selected: null };
const view = () => state.views[state.site];
const shown = (v) => v === view();

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
function resetProgress(v) {
  v.stages = {};
  for (const s of STAGES) v.stages[s.id] = { status: "pending", done: 0, total: 0, info: "" };
  v.counts = { found: 0, parsed: 0, scored: 0, applied: 0, manual: 0 };
  v.log = "";
  v.label = null;
  if (shown(v)) $("log").textContent = "";
  setNow("Getting ready…", v);
  setPercent(0, v);
  renderStages();
  renderCounters();
}

const CHECK = '<svg aria-hidden="true" viewBox="0 0 12 12" width="10" height="10"><path d="M2.5 6.2l2.2 2.2 4.8-4.8" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg>';

// The stages are a diagram of numbered nodes joined by links. It is built once per site and then only its
// classes and ring offsets change, so the CSS transitions (ring filling, link filling, node lighting up) can play.
const RING = 2 * Math.PI * 20;

function buildStages() {
  const names = SITES[state.site].stages;
  $("stages").innerHTML = STAGES.map((s, i) => `<li class="stage pending" id="stage-${s.id}">
      <div class="node"><svg class="ring" aria-hidden="true" viewBox="0 0 48 48">
        <circle class="r-track" cx="24" cy="24" r="20"/>
        <circle class="r-fill" cx="24" cy="24" r="20" stroke-dasharray="${RING}" stroke-dashoffset="${RING}"/></svg>
        <span class="s-num">${s.icon}</span><span class="s-check">${CHECK}</span></div>
      ${i < STAGES.length - 1 ? '<div class="link" aria-hidden="true"><i></i></div>' : ""}
      <div class="s-name">${names[i]}</div><div class="s-info"></div></li>`).join("");
}

function renderStages() {
  if (!$("stages").children.length) buildStages();
  const v = view();
  STAGES.forEach((s, i) => {
    const st = v.stages[s.id] || { status: "pending" };
    const el = $(`stage-${s.id}`);
    const share = st.total ? Math.min(1, st.done / st.total) : 0;
    el.className = `stage ${st.status}${st.status === "active" && !st.total ? " waiting" : ""}`;
    el.setAttribute("aria-label", `Stage ${s.icon}: ${SITES[state.site].stages[i]}`);
    const offset = st.status === "active" ? RING * (1 - Math.max(share, 0.04)) : st.status === "pending" ? RING : 0;
    el.querySelector(".r-fill").setAttribute("stroke-dashoffset", offset.toFixed(2));
    el.querySelector(".s-num").textContent = st.status === "error" ? "!" : s.icon;
    el.querySelector(".s-info").textContent =
      st.info || { pending: "Waiting", active: "Running…", done: "Done", skipped: "Skipped", error: "Error" }[st.status];
  });
}

function renderCounters() {
  const c = view().counts;
  const items = [["Jobs found", c.found, ""], ["Read", c.parsed, ""], ["AI scored", c.scored, ""],
                 ["Submitted", c.applied, "ok"], ["Need you", c.manual, "warn"]];
  // Each count's bar is its share of the jobs found, so the strip reads as a funnel.
  const base = Math.max(c.found, 1);
  $("counters").innerHTML = items.map(([label, value, cls]) =>
    `<div class="counter ${cls}${value ? "" : " zero"}"><b>${value}</b><span>${label}</span>
      <div class="f-bar"><i style="width:${Math.min(100, Math.round((value / base) * 100))}%"></i></div></div>`).join("");
}

function overallPercent(v) {
  let sum = 0;
  for (const s of STAGES) {
    const st = v.stages[s.id];
    if (!st) continue;
    if (st.status === "done" || st.status === "skipped" || st.status === "error") sum += s.weight;
    else if (st.status === "active") sum += s.weight * (st.total ? Math.min(1, st.done / st.total) : 0.05);
  }
  return Math.round(sum);
}

function setPercent(value, v = view()) {
  v.percent = value;
  if (!shown(v)) return;
  $("percent").textContent = `${value}%`;
  $("bar-fill").style.width = `${value}%`;
  $("bar").setAttribute("aria-valuenow", value);
}

function setNow(text, v = view()) {
  v.now = text;
  if (shown(v)) $("now-text").textContent = text;
}

// The header of the shown site: its own run, or a note that the other site's run is going.
function renderHeader() {
  const v = view(), site = SITES[state.site];
  const mine = state.running && state.runSite === state.site;
  const busyElsewhere = state.running && !mine;
  $("start-btn").classList.toggle("running", mine);
  $("start-btn").disabled = busyElsewhere;
  $("start-label").textContent = mine ? "Stop" : "Start";
  $("mail-btn").disabled = state.running || !state.gmail;
  $("workday-btn").disabled = state.running;
  $("launch-title").textContent = mine ? (v.mailOnly ? "Checking mail" : `${site.name}: working on applications`)
    : busyElsewhere ? `${site.name}: waiting` : v.label === "done" ? `${site.name}: run finished`
    : v.label === "error" ? `${site.name}: run stopped` : site.idle;
  $("launch-sub").textContent = mine && v.mailOnly ? "Only sorting Gmail into labels. Nothing is sent."
    : mine ? "You can minimise the window: progress is shown below, and jobs that need you appear in the list."
    : busyElsewhere ? `A ${SITES[state.runSite].name} run is in progress. ${site.name} can start when it finishes.`
    : site.sub;
  document.querySelectorAll(".modes input, #dry-run").forEach((el) => (el.disabled = state.running));
  document.querySelectorAll(".site-tab").forEach((tab) =>
    tab.classList.toggle("busy", state.running && tab.dataset.site === state.runSite));
}

function setRunning(running, label) {
  state.running = running;
  if (!running && state.runSite) state.views[state.runSite].label = label || null;
  renderHeader();
}

function handle(event) {
  // Every event belongs to the site whose run is going, even while the other site is shown.
  const v = state.views[state.runSite || state.site];
  const st = v.stages[event.stage];
  switch (event.event) {
    case "stage":
      if (!st) break;
      if (event.state === "start") {
        st.status = "active"; st.total = event.total || 0; st.done = 0;
        st.info = event.total ? `0 of ${event.total}` : "Running…";
        if (event.stage === "apply") st.info = event.total ? `0 of ${event.total} jobs` : "No suitable jobs";
      } else if (event.state === "done") {
        st.status = "done";
        st.info = stageSummary(event, v);
        if (event.stage === "collect" && event.found !== undefined) v.counts.found = event.found;
        if (event.stage === "parse") v.counts.parsed = event.total || v.counts.parsed;
        if (event.stage === "analyze") v.counts.scored = (event.analyzed || 0) + (event.skipped || 0);
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
      if (event.text) setNow(event.text, v);
      if (event.stage === "collect" && event.found !== undefined) v.counts.found = event.found;
      if (event.stage === "parse") v.counts.parsed = event.done;
      if (event.stage === "analyze") v.counts.scored = event.done;
      break;
    case "result":
      if (event.status === "applied" || event.status === "unconfirmed") v.counts.applied += 1;
      if (event.status === "needs_manual" || event.status === "external") v.counts.manual += 1;
      log(`→ ${STATUS_TEXT[event.status] || event.status}: ${event.title}${event.company ? " · " + event.company : ""}`, v);
      if (shown(v)) refreshLists();
      break;
    case "mail":
      v.mailCounts = event;
      break;
    case "log":
      log(event.text, v);
      break;
    case "run":
      if (event.state === "done") setNow(event.error ? `Done with an error: ${event.error}` : "Done", v);
      break;
    case "exit":
      finish(event.code, v);
      return;
  }
  setPercent(overallPercent(v), v);
  if (shown(v)) { renderStages(); renderCounters(); }
}

function stageSummary(event, v) {
  if (event.stage === "collect") return `Found: ${event.found ?? 0}`;
  if (event.stage === "parse") return `Read: ${event.total ?? 0}`;
  if (event.stage === "analyze") return `Scored: ${(event.analyzed || 0) + (event.skipped || 0)}${event.failed ? `, errors ${event.failed}` : ""}`;
  if (event.stage === "apply") return `Submitted: ${(event.applied || 0) + (event.unconfirmed || 0)}`;
  if (event.stage === "mail" && v.mailCounts) return `Offers: ${v.mailCounts.offer || 0}, rejections: ${v.mailCounts.rejection || 0}`;
  return "Done";
}

function mailSummary(v) {
  const m = v.mailCounts || {};
  return `Mail checked: offers and interviews ${m.offer || 0}, rejections ${m.rejection || 0}, ` +
    `application confirmations ${m.applied || 0}. Nothing was sent.`;
}

function log(text, v = view()) {
  v.log += text + "\n";
  if (v.log.length > 60000) v.log = v.log.slice(-40000);
  if (!shown(v)) return;
  const el = $("log");
  el.textContent = v.log;
  el.scrollTop = el.scrollHeight;
}

function finish(code, v) {
  for (const s of STAGES) {
    const st = v.stages[s.id];
    if (st.status === "active") st.status = code === 0 || code === 2 ? "done" : "error";
  }
  const stopped = state.stopRequested;
  state.stopRequested = false;
  setPercent(code === 0 || code === 2 ? 100 : overallPercent(v), v);
  setNow(stopped ? "Stopped. What was collected is saved; the next run continues." :
    v.mailOnly ? (code === 0 ? mailSummary(v) : "Could not check mail, see the log") :
    code === 0 || code === 2 ? `Done: ${v.counts.applied} submitted, ${v.counts.manual} for you` :
    "The run ended with an error, see the log", v);
  setRunning(false, code === 0 || code === 2 ? "done" : "error");
  state.runSite = null;
  renderHeader();
  if (shown(v)) renderStages();
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
async function begin(v, skipped, args, now) {
  resetProgress(v);
  for (const id of skipped) v.stages[id].status = "skipped";
  renderStages();
  const [dryRun = false, skipScrape = false, mailOnly = false, workday = false] = args;
  const result = await api().start(dryRun, skipScrape, mailOnly, workday, state.site);
  if (!result.ok) { v.mailOnly = false; toast(result.error); return; }
  state.runSite = state.site;
  setRunning(true);
  setNow(now, v);
}

async function toggleRun() {
  if (state.running) {
    if (state.runSite !== state.site) return;
    state.stopRequested = true;
    setNow("Stopping…");
    await api().stop();
    return;
  }
  const v = view();
  const mode = document.querySelector("input[name=mode]:checked").value;
  v.mailOnly = false;
  await begin(v, mode === "apply" ? ["collect", "parse", "analyze", "mail"] : [], [$("dry-run").checked, mode === "apply"],
              mode === "apply" ? "Opening the browser to apply…" : SITES[state.site].starting);
}

// Only sorts Gmail (offers, rejections, confirmations): nothing is collected, scored or sent.
async function checkMail() {
  if (state.running) return;
  const v = view();
  v.mailOnly = true;
  v.mailCounts = null;
  await begin(v, ["collect", "parse", "analyze", "apply"], [false, false, true], "Sorting mail: offers, rejections, applications…");
}

// Workday jobs from earlier runs: each employer's site opens, the applicant signs in, the program fills the form.
async function applyWorkday() {
  if (state.running) return;
  const v = view();
  v.mailOnly = false;
  await begin(v, ["collect", "parse", "analyze", "mail"], [$("dry-run").checked, false, false, true],
              "Opening Workday: sign in on the employer's site in the Chrome window (Sign in with email); the app fills in the rest");
}

// ---------- switching sites --------------------------------------------------------------
function restoreView() {
  const v = view();
  $("stages").innerHTML = "";
  renderStages();
  renderCounters();
  $("log").textContent = v.log;
  $("log").scrollTop = $("log").scrollHeight;
  $("now-text").textContent = v.now;
  setPercent(v.percent, v);
  renderHeader();
}

async function switchSite(site) {
  if (site === state.site || !SITES[site]) return;
  const apply = () => {
    state.site = site;
    document.body.dataset.site = site;
    document.querySelectorAll(".site-tab").forEach((tab) => {
      tab.classList.toggle("active", tab.dataset.site === site);
      tab.setAttribute("aria-selected", String(tab.dataset.site === site));
    });
    restoreView();
  };
  try { localStorage.setItem("jobscraper.site", site); } catch (e) { /* storage may be off */ }
  // The whole screen is replaced: the old site slides away and the new one, in its own theme, slides in.
  if (document.startViewTransition && !matchMedia("(prefers-reduced-motion: reduce)").matches) {
    document.documentElement.dataset.direction = site === "cvlv" ? "forward" : "back";
    await document.startViewTransition(apply).updateCallbackDone;
  } else {
    apply();
  }
  await Promise.all([refreshFacts(), refreshLists()]);
}

// ---------- lists ----------------------------------------------------------------------
async function refreshLists() {
  const site = state.site;
  const [manual, applied] = await Promise.all([api().manual_list(site), api().applied_list(site)]);
  if (site !== state.site) return;  // the user switched site while the lists loaded
  view().items = manual;
  $("manual-count").textContent = manual.length;
  $("applied-count").textContent = applied.length;
  const name = SITES[site].name;
  $("manual-list").innerHTML = manual.length ? byDay(manual, (item, i) => `
    <div class="job">
      <div class="job-top"><div><div class="job-company">${escapeHtml(item.company || "Company")}</div>
        <div class="job-title">${escapeHtml(item.title)}</div></div>
        ${item.score ? `<span class="score">${item.score}</span>` : ""}</div>
      <div class="job-meta"><span class="chip">${escapeHtml(item.label)}</span>
        <span class="meta">${escapeHtml(formatTime(item.updated))}</span></div>
      <div class="job-reason">${escapeHtml(item.reason)}</div>
      <div class="job-actions"><button class="primary small" data-open="${i}">Details</button>
        <button class="secondary small" data-url="${escapeHtml(item.apply_url || item.link)}">${item.apply_url ? "Open form" : "Open job"}</button>
        ${hideButton(item)}</div>
    </div>`) :
    `<div class="empty"><div class="big">Nothing on ${name} needs you right now</div>${name} jobs where the app stopped and waits for you appear here.</div>`;
  $("applied-list").innerHTML = applied.length ? byDay(applied, (item) => `
    <div class="job">
      <div class="job-top"><div><div class="job-company">${escapeHtml(item.company || "Company")}</div>
        <div class="job-title">${escapeHtml(item.title)}</div></div>
        ${item.score ? `<span class="score">${item.score}</span>` : ""}</div>
      <div class="job-meta"><span class="chip ok">${item.manual ? "Applied myself" : "Submitted by the app"}</span>
        <span class="meta">${escapeHtml(formatTime(item.updated))}${item.ats ? " · " + escapeHtml(item.ats) : ""}</span></div>
      <div class="job-actions" style="margin-top:12px"><button class="secondary small" data-url="${escapeHtml(item.link)}">Job</button>
        ${item.cv_pdf ? `<button class="secondary small" data-path="${escapeHtml(item.cv_pdf)}">CV</button>` : ""}
        ${hideButton(item)}</div>
    </div>`) : `<div class="empty"><div class="big">No ${name} applications yet</div>They appear here after the first run.</div>`;
}

function hideButton(item) {
  return `<button class="ghost small hide-btn" data-hide="${escapeHtml(item.link)}" title="Remove from the list">Hide</button>`;
}

// The lists are newest first; a heading opens each day, so the cards of one run stay together.
function byDay(items, card) {
  let day = null;
  return items.map((item, i) => {
    const label = formatDay(item.updated);
    const head = label !== day ? `<div class="day">${escapeHtml(label)}</div>` : "";
    day = label;
    return head + card(item, i);
  }).join("");
}

function parseDate(value) {
  return value ? new Date(value.replace(" ", "T") + "Z") : null;
}

function formatDay(value) {
  const date = parseDate(value);
  if (!date) return "No date";
  const today = new Date();
  const yesterday = new Date(today.getFullYear(), today.getMonth(), today.getDate() - 1);
  if (date.toDateString() === today.toDateString()) return "Today";
  if (date.toDateString() === yesterday.toDateString()) return "Yesterday";
  return date.toLocaleDateString("en-GB", { weekday: "short", day: "numeric", month: "long" });
}

function formatTime(value) {
  const date = parseDate(value);
  return date ? date.toLocaleString("en-GB", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" }) : "";
}

async function hideJob(link) {
  await api().hide(link);
  toast("Hidden from the list");
  refreshLists();
}

async function openDetails(index) {
  const item = view().items[index];
  state.selected = item;
  $("m-company").textContent = item.company || "Company";
  $("m-title").textContent = item.title;
  $("m-label").textContent = item.label;
  $("m-reason").textContent = item.reason;
  $("m-summary").innerHTML = '<span class="loading"></span>AI is preparing a short summary…';
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
  const site = item.link.includes("cv.lv") ? "cv.lv" : "LinkedIn";
  return [item.apply_url ? "Open the form on the company site." : `Open the job on ${site}.`,
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
  const site = state.site;
  const s = await api().state();
  state.root = s.root;
  state.gmail = s.gmail;
  $("auto-submit").checked = s.auto_submit;
  const facts = [`Limit: ${s.daily_limit} applications a day on ${SITES[site].name}`, `Min. score: ${s.min_score}`,
                 site === "cvlv" ? "cv.lv form and employer sites" : s.apply_external ? "Easy Apply and company sites" : "Easy Apply only"];
  const today = await api().today(site);
  const sent = (today.applied || 0) + (today.unconfirmed || 0);
  facts.push(`Submitted today: ${sent}`);
  let html = facts.map((f) => `<span class="fact">${escapeHtml(f)}</span>`).join("");
  if (!s.signed_in) html += `<span class="fact warn">${escapeHtml(SITES[site].login)}</span>`;
  if (!s.gmail) html += `<span class="fact warn">Gmail is not connected</span>`;
  if (!s.auto_submit) html += `<span class="fact warn">Auto-submit is off: applications are only filled in</span>`;
  if (!s.ready) html += `<span class="fact warn">Environment not found: ${escapeHtml(s.root)}</span>`;
  if (site !== state.site) return;
  $("facts").innerHTML = html;
  if (!state.running && s.running) { state.runSite = state.runSite || site; setRunning(true); }
  renderHeader();
}

// ---------- wiring ---------------------------------------------------------------------
document.addEventListener("click", async (e) => {
  const target = e.target.closest("[data-open],[data-url],[data-path],[data-hide],.tab,.site-tab");
  if (!target) return;
  if (target.classList.contains("site-tab")) switchSite(target.dataset.site);
  else if (target.dataset.hide) {
    // Take the card away at once; the database write runs a short Python command.
    target.closest(".job").remove();
    hideJob(target.dataset.hide);
  } else if (target.classList.contains("tab")) {
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
  let saved = null;
  try { saved = localStorage.getItem("jobscraper.site"); } catch (e) { /* storage may be off */ }
  state.site = SITES[saved] ? saved : "linkedin";
  document.body.dataset.site = state.site;
  document.querySelectorAll(".site-tab").forEach((tab) => {
    tab.classList.toggle("active", tab.dataset.site === state.site);
    tab.setAttribute("aria-selected", String(tab.dataset.site === state.site));
  });
  for (const v of Object.values(state.views)) { resetProgress(v); v.now = "Waiting to start"; }
  restoreView();
  $("start-btn").addEventListener("click", toggleRun);
  $("mail-btn").addEventListener("click", checkMail);
  $("workday-btn").addEventListener("click", applyWorkday);
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
    toast("Marked: applied myself");
    closeModal(); refreshLists();
  });
  $("m-hide").addEventListener("click", () => {
    const link = state.selected.link;
    closeModal();
    hideJob(link);
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") closeModal();
    // Ctrl+1 / Ctrl+2 switch between the two sites.
    if (e.ctrlKey && (e.key === "1" || e.key === "2")) switchSite(e.key === "1" ? "linkedin" : "cvlv");
  });
  await refreshFacts();
  await refreshLists();
  poll();
});
