"use strict";
const $ = (selector) => document.querySelector(selector);
const csrf = $('meta[name="csrf-token"]').content;
let selectedTask = null;
let submitting = false;
let running = false;
let jobsPage = 1;
const labels = {running: "В работе", succeeded: "Готово", partial: "Частично", failed: "Ошибка", stopped: "Остановлено", interrupted: "Прервано"};
async function api(path, data) {
  const options = {credentials: "same-origin", cache: "no-store"};
  if (data !== undefined) Object.assign(options, {method: "POST", headers: {"Content-Type": "application/json", "X-CSRF-Token": csrf}, body: JSON.stringify(data)});
  const response = await fetch(path, options);
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || "Ошибка запроса");
  return result;
}
function element(tag, text, className) {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = text;
  if (className) node.className = className;
  return node;
}
function link(name, prefix) {
  const node = element("a", name);
  node.href = prefix + encodeURIComponent(name);
  return node;
}
function buttons() {
  document.querySelectorAll("[data-start]").forEach(button => { button.disabled = submitting || running; });
}
async function showLog(id) {
  selectedTask = id;
  const result = await api(`/api/tasks/${id}/log`);
  if (selectedTask !== id) return;
  $("#log-title").textContent = `Лог / ${id.slice(0, 8)}`;
  $("#log").textContent = result.text || "Пока нет вывода.";
}
function field(parent, label, value) {
  if (value === null || value === undefined || value === "") return;
  const block = element("div", undefined, "job-field");
  block.append(element("strong", label), element("p", String(value)));
  parent.append(block);
}
async function showJobs(page = 1) {
  const name = $("#db-select").value;
  if (!name) return;
  const data = await api(`/api/databases/${encodeURIComponent(name)}/jobs?page=${page}`);
  jobsPage = data.page;
  const view = $("#jobs-view");
  view.replaceChildren();
  for (const job of data.jobs) {
    const card = element("article", undefined, "job-card");
    const title = element("h4");
    if (typeof job.link === "string" && /^https?:\/\//i.test(job.link)) {
      const anchor = element("a", job.title || "Без названия");
      anchor.href = job.link;
      anchor.target = "_blank";
      anchor.rel = "noreferrer";
      title.append(anchor);
    } else title.textContent = job.title || "Без названия";
    card.append(title);
    field(card, "Статус", `${job.parse_status || "—"} / ${job.analysis_status || "—"}`);
    field(card, "Оценка", job.match_score === null ? null : `${job.match_score}%`);
    field(card, "Локация", job.country);
    field(card, "Уровень", job.grade);
    field(card, "Зарплата", job.salary);
    field(card, "Опубликовано", job.posted);
    field(card, "CV", job.recommended_cv);
    field(card, "Описание", job.description);
    field(card, "Анализ", job.analysis_report);
    field(card, "Сопроводительное письмо", job.cover_letter);
    field(card, "Ошибка", job.last_error);
    view.append(card);
  }
  if (!data.jobs.length) view.append(element("p", "В этой части базы вакансий нет.", "hint"));
  const pages = Math.max(1, Math.ceil(data.total / data.page_size));
  const controls = $("#jobs-pages");
  controls.replaceChildren(element("span", `Страница ${data.page} из ${pages} · ${data.total} вакансий`));
  const group = element("div");
  const previous = element("button", "Назад", "secondary");
  previous.type = "button"; previous.disabled = data.page <= 1;
  previous.onclick = () => showJobs(data.page - 1).catch(error => { $("#message").textContent = error.message; });
  const next = element("button", "Далее", "secondary");
  next.type = "button"; next.disabled = data.page >= pages;
  next.onclick = () => showJobs(data.page + 1).catch(error => { $("#message").textContent = error.message; });
  group.append(previous, next); controls.append(group);
}
async function refresh() {
  try {
    const data = await api("/api/state");
    $("#connection").textContent = "На связи";
    const active = data.tasks.find(task => task.status === "running");
    running = Boolean(active);
    buttons();
    $("#db-count").textContent = data.databases.length;
    $("#job-count").textContent = data.databases.reduce((n, db) => n + (db.total || 0), 0);
    $("#active-label").textContent = active ? active.command : "Можно запускать";
    $("#database-list").replaceChildren();
    const select = $("#db-select");
    const names = data.databases.map(db => db.name);
    if (JSON.stringify([...select.options].map(o => o.value)) !== JSON.stringify(names)) {
      const previous = select.value;
      select.replaceChildren(...names.map(name => new Option(name, name)));
      if (names.includes(previous)) select.value = previous;
    }
    for (const db of data.databases) {
      const row = element("tr");
      [db.name, db.total ?? "—", db.parsed ?? "—", db.analyzed ?? "—"].forEach(value => row.append(element("td", value)));
      const cell = element("td");
      const download = link(db.name, "/databases/");
      download.textContent = "SQLite ↓";
      cell.append(download); row.append(cell); $("#database-list").append(row);
    }
    if (!names.length) { const row = element("tr"); const cell = element("td", "Баз пока нет. Начните первый поиск."); cell.colSpan = 5; row.append(cell); $("#database-list").append(row); }
    $("#report-list").replaceChildren(...data.reports.map(name => link(name, "/reports/")));
    $("#cv-list").textContent = data.cvs.join(" · ") || "CV не найдены. Для AI добавьте их в каталог сервера.";
    $("#task-list").replaceChildren();
    for (const task of data.tasks) {
      const row = element("div", undefined, "task");
      const description = element("div", task.command, "description");
      description.append(element("small", `${new Date(task.started * 1000).toLocaleString("ru-RU")} · ${task.db || "новая база"}`));
      row.append(description, element("span", labels[task.status] || task.status, `badge ${task.status}`));
      const logs = element("button", "Лог", "secondary");
      logs.onclick = () => showLog(task.id).catch(error => { $("#message").textContent = error.message; });
      row.append(logs);
      if (task.status === "running") {
        const stop = element("button", "Остановить", "stop");
        stop.onclick = async () => {
          stop.disabled = true;
          try { await api(`/api/tasks/${task.id}/stop`, {}); $("#message").textContent = "Остановка запрошена. Ожидаем завершения браузеров."; }
          catch (error) { $("#message").textContent = error.message; }
        };
        row.append(stop);
      }
      $("#task-list").append(row);
    }
    if (!data.tasks.length) $("#task-list").textContent = "Здесь появится история запусков.";
    if (!selectedTask && data.tasks.length) selectedTask = data.tasks[0].id;
    if (selectedTask) await showLog(selectedTask);
  } catch (error) {
    $("#connection").textContent = "Нет связи";
    $("#message").textContent = error.message;
  }
}
async function submit(event) {
  event.preventDefault();
  if (submitting || running) return;
  const form = new FormData(event.target);
  let data;
  if (event.target.id === "scrape-form") {
    data = {command: "scrape", roles: form.get("roles"), countries: form.get("countries"), lookback: Number(form.get("lookback")), browsers: Number(form.get("browsers")), max_pages: Number(form.get("max_pages")), limit: Number(form.get("limit")), ai: form.has("ai"), interactive: form.has("interactive")};
  } else {
    data = {command: form.get("command"), db: form.get("db")};
    if (data.command === "export") {
      data.format = form.get("format");
      if (form.get("min_score") !== "") data.min_score = Number(form.get("min_score"));
    } else {
      data.limit = Number(form.get("limit")); data.ai = form.has("ai");
      if (data.command === "retry") { data.max_pages = Number(form.get("max_pages")); data.browsers = 1; data.refresh_searches = form.has("refresh_searches"); data.interactive = form.has("interactive"); }
    }
  }
  submitting = true; buttons();
  try {
    const result = await api("/api/tasks", data);
    selectedTask = result.task.id;
    $("#message").textContent = "Задача принята. Можно закрыть вкладку: работа продолжится на сервере.";
    await refresh();
  } catch (error) { $("#message").textContent = error.message; }
  finally { submitting = false; buttons(); }
}
$("#scrape-form").addEventListener("submit", submit);
$("#action-form").addEventListener("submit", submit);
$("#load-jobs").addEventListener("click", () => showJobs(1).catch(error => { $("#message").textContent = error.message; }));
async function poll() { await refresh(); window.setTimeout(poll, 3000); }
poll();
