"use strict";

const $ = (sel) => document.querySelector(sel);
const state = { me: null, loaded: new Set(), catalog: { q: "", genre: "", sort: "title", offset: 0, active: null } };
const PAGE = 50;

// ---------- утилиты ----------

function el(tag, props = {}, ...kids) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(props)) {
    if (v == null || v === false) continue;
    if (k === "class") node.className = v;
    else if (k === "style") node.style.cssText = v;
    else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
    else node.setAttribute(k, v === true ? "" : v);
  }
  for (const kid of kids.flat()) {
    if (kid == null || kid === false) continue;
    node.append(kid instanceof Node ? kid : document.createTextNode(String(kid)));
  }
  return node;
}

const fmt = (n) => (n == null ? "—" : Number(n).toLocaleString("ru-RU"));
const fmtRating = (r) => (r ? Number(r).toFixed(1) : "—");

async function api(path, opts = {}) {
  let res;
  try {
    res = await fetch(path, {
      ...opts,
      headers: opts.body ? { "Content-Type": "application/json" } : undefined,
      body: opts.body ? JSON.stringify(opts.body) : undefined,
    });
  } catch {
    setOnline(false);
    throw new Error("Нет соединения с сервером");
  }
  setOnline(true);
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const detail = Array.isArray(data.detail) ? "Проверьте заполнение полей" : data.detail;
    const err = new Error(detail || `Ошибка ${res.status}`);
    err.status = res.status;
    throw err;
  }
  return data;
}

let toastTimer;
function toast(text) {
  const t = $("#toast");
  t.textContent = text;
  t.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (t.hidden = true), 2800);
}

function confirmDialog(title, text, yesLabel = "Подтвердить") {
  const dlg = $("#confirm-dialog");
  $("#confirm-title").textContent = title;
  $("#confirm-text").textContent = text;
  $("#confirm-yes").textContent = yesLabel;
  dlg.hidden = false;
  $("#confirm-no").focus();
  return new Promise((resolve) => {
    const done = (v) => {
      dlg.hidden = true;
      $("#confirm-yes").onclick = $("#confirm-no").onclick = dlg.onclick = null;
      resolve(v);
    };
    $("#confirm-yes").onclick = () => done(true);
    $("#confirm-no").onclick = () => done(false);
    dlg.onclick = (e) => e.target === dlg && done(false);
  });
}

// ---------- статус ----------

function setOnline(on) {
  $("#conn-dot").className = on ? "dot" : "dot off";
  $("#conn-text").textContent = on ? "онлайн" : "офлайн";
}

function setChatStatus(chat) {
  const badge = $("#chat-badge");
  const alert = $("#chat-alert");
  const labels = { ready: ["ok", "ИИ-чат готов"], loading: ["warn", "ИИ-чат загружается"], unavailable: ["danger", "ИИ-чат недоступен"] };
  const [cls, text] = labels[chat.state] || labels.unavailable;
  badge.className = `badge ${cls}`;
  badge.textContent = text;
  alert.hidden = chat.state === "ready";
  alert.textContent =
    chat.state === "loading"
      ? "Модели поиска ещё загружаются — чат заработает через минуту. Каталог и подборки уже доступны."
      : `Чат недоступен: ${chat.error || "неизвестная ошибка"}. Каталог и подборки работают.`;
  $("#send-btn").disabled = chat.state !== "ready";
  $("#add-submit").disabled = chat.state !== "ready";
  state.chatState = chat.state;
}

async function pollHealth() {
  try {
    const h = await api("/api/health");
    setChatStatus(h.chat);
  } catch {}
  setTimeout(pollHealth, state.chatState === "loading" ? 4000 : 15000);
}

// ---------- профиль ----------

function openAgeDialog() {
  $("#age-input").value = state.me?.age ?? "";
  $("#age-dialog").hidden = false;
  $("#age-input").focus();
}

function updateProfileButton() {
  $("#profile-btn").textContent = state.me?.age ? `Возраст: ${state.me.age}` : "Указать возраст";
}

function needAge() {
  if (state.me?.age) return false;
  openAgeDialog();
  return true;
}

$("#profile-btn").onclick = openAgeDialog;
$("#age-cancel").onclick = () => ($("#age-dialog").hidden = true);
$("#age-form").onsubmit = async (e) => {
  e.preventDefault();
  try {
    const lang = (navigator.language || "ru").slice(0, 2);
    const me = await api("/api/me", { method: "PUT", body: { age: Number($("#age-input").value), language: lang } });
    Object.assign(state.me, me);
    updateProfileButton();
    $("#age-dialog").hidden = true;
    toast("Возраст сохранён");
    if (currentView() === "forme") loadForMe();
  } catch (err) {
    toast(err.message);
  }
};

// ---------- навигация ----------

const currentView = () => document.querySelector('.segmented [aria-selected="true"]').dataset.view;

function showView(name) {
  document.querySelectorAll(".segmented button").forEach((b) => b.setAttribute("aria-selected", b.dataset.view === name));
  document.querySelectorAll("[data-panel]").forEach((p) => (p.hidden = p.dataset.panel !== name));
  history.replaceState(null, "", `#${name}`);
  const first = !state.loaded.has(name);
  state.loaded.add(name);
  if (name === "forme" && first) loadForMe();
  if (name === "catalog" && first) { loadGenres(); loadCatalog(true); }
  if (name === "stats") loadStats();
  if (name === "chat") $("#chat-input").focus();
}

document.querySelectorAll(".segmented button").forEach((b) => (b.onclick = () => showView(b.dataset.view)));
window.addEventListener("hashchange", () => {
  const name = location.hash.slice(1);
  if (name !== currentView() && document.querySelector(`[data-view="${name}"]`)) showView(name);
});

// ---------- книги и обратная связь ----------

function bookList(books, { withDesc = false } = {}) {
  return el("div", { class: "books" },
    books.map((b, i) =>
      el("div", { class: "book" },
        el("span", { class: "book-n" }, i + 1),
        el("div", {},
          el("span", { class: "book-title" }, b.title), " ",
          el("span", { class: "book-meta" }, `· ${b.author}${b.genre ? ` · ${b.genre}` : ""}`)),
        withDesc && b.description ? el("p", { class: "book-desc" }, b.description) : null)));
}

function feedbackBar(setId) {
  const bar = el("div", { class: "feedback" }, el("span", { class: "muted small" }, "Подборка подходит?"));
  const send = async (action) => {
    bar.querySelectorAll("button").forEach((b) => (b.disabled = true));
    try {
      await api("/api/feedback", { method: "POST", body: { set_id: setId, action } });
      bar.replaceChildren(el("span", { class: `badge ${action === "like" ? "ok" : ""}` },
        action === "like" ? "Спасибо за лайк!" : "Учтём ваш отзыв"));
    } catch (err) {
      bar.querySelectorAll("button").forEach((b) => (b.disabled = false));
      toast(err.message);
    }
  };
  bar.append(
    el("button", { class: "btn sm", onclick: () => send("like") }, "👍 Да"),
    el("button", { class: "btn sm", onclick: () => send("dislike") }, "👎 Не то"));
  return bar;
}

// ---------- чат ----------

const input = $("#chat-input");

function addMessage(cls, ...content) {
  $("#chat-empty").hidden = true;
  const msg = el("div", { class: `msg ${cls}` }, ...content);
  $("#messages").append(msg);
  msg.scrollIntoView({ block: "end", behavior: "smooth" });
  return msg;
}

async function sendMessage(text) {
  text = text.trim();
  if (!text || needAge()) return;
  input.value = "";
  autosize();
  addMessage("user", text);
  const pending = addMessage("bot pending", "Ищу книги и думаю над ответом…");
  $("#send-btn").disabled = true;
  try {
    const res = await api("/api/chat", { method: "POST", body: { message: text } });
    pending.classList.remove("pending");
    pending.replaceChildren(el("p", {}, res.reply), ...(res.books.length ? [bookList(res.books), feedbackBar(res.set_id)] : []));
  } catch (err) {
    pending.classList.remove("pending");
    pending.replaceChildren(el("div", { class: "alert danger" }, err.message));
  } finally {
    $("#send-btn").disabled = state.chatState !== "ready";
    pending.scrollIntoView({ block: "end", behavior: "smooth" });
  }
}

function autosize() {
  input.style.height = "auto";
  input.style.height = `${Math.min(input.scrollHeight, 180)}px`;
}

$("#composer").onsubmit = (e) => { e.preventDefault(); sendMessage(input.value); };
input.addEventListener("input", autosize);
input.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); if (!$("#send-btn").disabled) sendMessage(input.value); }
});
document.querySelectorAll("[data-suggest]").forEach((c) => (c.onclick = () => state.chatState === "ready" ? sendMessage(c.dataset.suggest) : toast("Чат ещё не готов")));

$("#newchat-btn").onclick = async () => {
  if (!(await confirmDialog("Начать новый чат?", "История диалога будет удалена, советчик забудет прошлые запросы.", "Начать заново"))) return;
  await api("/api/chat/reset", { method: "POST" }).catch(() => {});
  $("#messages").replaceChildren($("#chat-empty"));
  $("#chat-empty").hidden = false;
  toast("Новый чат начат");
};

// голосовой ввод через Web Speech API (в боте — распознавание голосовых сообщений)
const Recognition = window.SpeechRecognition || window.webkitSpeechRecognition;
if (Recognition) {
  const mic = $("#mic-btn");
  const rec = new Recognition();
  rec.lang = "ru-RU";
  rec.interimResults = false;
  mic.hidden = false;
  mic.onclick = () => (mic.classList.contains("listening") ? rec.stop() : rec.start());
  rec.onstart = () => mic.classList.add("listening");
  rec.onend = () => mic.classList.remove("listening");
  rec.onerror = () => toast("Не удалось распознать речь");
  rec.onresult = (e) => { input.value = e.results[0][0].transcript; autosize(); input.focus(); };
}

// ---------- для меня ----------

async function loadForMe() {
  const body = $("#forme-body");
  if (!state.me?.age) {
    body.replaceChildren(el("div", { class: "alert attention" }, "Укажите возраст, чтобы получить персональную подборку. ",
      el("button", { class: "btn sm", style: "margin-left:6px", onclick: openAgeDialog }, "Указать")));
    return;
  }
  body.replaceChildren(el("p", { class: "muted" }, "Подбираем книги…"));
  try {
    const res = await api("/api/recommendations");
    body.replaceChildren(...(res.books.length
      ? [bookList(res.books, { withDesc: true }), feedbackBar(res.set_id)]
      : [el("p", { class: "muted" }, "В каталоге пока нет книг.")]));
  } catch (err) {
    body.replaceChildren(el("div", { class: "alert danger" }, err.message));
  }
}
$("#forme-refresh").onclick = loadForMe;

// ---------- каталог ----------

async function loadGenres() {
  try {
    const genres = await api("/api/genres");
    const box = $("#cat-genres");
    const chip = (name, label) => el("button", {
      class: `chip${state.catalog.genre === name ? " active" : ""}`,
      onclick: () => { state.catalog.genre = name; loadGenresActive(); loadCatalog(true); },
      "data-genre": name,
    }, label);
    box.replaceChildren(chip("", "Все"), ...genres.map((g) => chip(g.name, g.name)));
  } catch {}
}
function loadGenresActive() {
  document.querySelectorAll("#cat-genres .chip").forEach((c) => c.classList.toggle("active", c.dataset.genre === state.catalog.genre));
}

async function loadCatalog(reset) {
  const c = state.catalog;
  if (reset) c.offset = 0;
  const params = new URLSearchParams({ q: c.q, genre: c.genre, sort: c.sort, offset: c.offset, limit: PAGE });
  try {
    const res = await api(`/api/books?${params}`);
    const rows = res.books.map((b) => {
      const tr = el("tr", { class: c.active === b.id ? "active" : null },
        el("td", {}, el("div", { class: "cell-title" }, b.title), el("div", { class: "muted small" }, b.author)),
        el("td", { class: "small" }, b.genre || "—"),
        el("td", { class: "num" }, fmtRating(b.rating)),
        el("td", { class: "num" }, fmt(b.likes)));
      tr.onclick = () => {
        c.active = b.id;
        document.querySelectorAll("#cat-rows tr").forEach((r) => r.classList.remove("active"));
        tr.classList.add("active");
        showDetail(b);
      };
      return tr;
    });
    if (reset) $("#cat-rows").replaceChildren(...rows);
    else $("#cat-rows").append(...rows);
    if (!res.total) $("#cat-rows").replaceChildren(el("tr", {}, el("td", { colspan: 4, class: "muted" }, "Ничего не найдено")));
    c.offset += res.books.length;
    $("#cat-total").textContent = `${fmt(res.total)} книг`;
    $("#cat-more").hidden = c.offset >= res.total;
  } catch (err) {
    toast(err.message);
  }
}

function showDetail(b) {
  $("#cat-detail").replaceChildren(
    el("h3", {}, b.title),
    el("p", { class: "muted" }, b.author),
    el("div", { class: "badges" },
      (b.genre || "").split(",").filter(Boolean).map((g) => el("span", { class: "badge" }, g.trim())),
      b.rating ? el("span", { class: "badge ok num" }, `★ ${fmtRating(b.rating)}`) : null),
    el("p", {}, b.description || "Описания нет."),
    el("div", { class: "kpis", style: "margin-top:16px" },
      el("div", { class: "kpi" }, el("div", { class: "kpi-value num" }, fmt(b.likes || 0)), el("div", { class: "kpi-label" }, "лайков")),
      el("div", { class: "kpi" }, el("div", { class: "kpi-value num" }, fmt(b.views || 0)), el("div", { class: "kpi-label" }, "просмотров"))),
    el("div", { class: "row", style: "margin-top:16px" },
      el("button", { class: "btn sm", onclick: () => { showView("chat"); sendMessage(`Расскажи подробнее о книге «${b.title}» (${b.author}) и посоветуй похожие`); } }, "Спросить в чате")));
}

let searchTimer;
$("#cat-q").addEventListener("input", (e) => {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(() => { state.catalog.q = e.target.value; loadCatalog(true); }, 250);
});
$("#cat-sort").onchange = (e) => { state.catalog.sort = e.target.value; loadCatalog(true); };
$("#cat-more").onclick = () => loadCatalog(false);

// ---------- добавление книги ----------

$("#add-form").onsubmit = async (e) => {
  e.preventDefault();
  const f = new FormData(e.target);
  const body = Object.fromEntries(f.entries());
  body.rating = body.rating ? Number(body.rating) : null;
  $("#add-submit").disabled = true;
  try {
    const book = await api("/api/books", { method: "POST", body });
    e.target.reset();
    toast(`«${book.title}» добавлена в каталог`);
    state.loaded.delete("catalog");
  } catch (err) {
    toast(err.message);
  } finally {
    $("#add-submit").disabled = state.chatState !== "ready";
  }
};

// ---------- статистика ----------

const tip = $("#tip");
function withTip(node, text) {
  node.addEventListener("mousemove", (e) => {
    tip.textContent = text;
    tip.hidden = false;
    tip.style.left = `${Math.min(e.clientX + 12, innerWidth - tip.offsetWidth - 8)}px`;
    tip.style.top = `${e.clientY - 36}px`;
  });
  node.addEventListener("mouseleave", () => (tip.hidden = true));
  return node;
}

function renderHBars(target, items, unit) {
  const max = Math.max(1, ...items.map((i) => i.count));
  target.replaceChildren(...(items.length ? items.map((i) =>
    withTip(el("div", { class: "hbar" },
      el("span", { class: "hbar-name", title: i.name }, i.name),
      el("div", { class: "hbar-track" }, el("div", { class: "hbar-fill", style: `width:${(i.count / max) * 100}%` })),
      el("span", { class: "hbar-val" }, fmt(i.count))), `${i.name}: ${fmt(i.count)} ${unit}`))
    : [el("p", { class: "muted" }, "Нет данных")]));
}

function renderCols(target, items) {
  const max = Math.max(1, ...items.map((i) => i.count));
  if (!items.some((i) => i.count)) { target.replaceChildren(el("p", { class: "muted" }, "Пока никто не указал возраст")); return; }
  target.replaceChildren(
    el("div", { class: "cols" }, items.map((i) =>
      withTip(el("div", { class: "col" },
        i.count ? el("span", { class: "col-val" }, fmt(i.count)) : null,
        el("div", { class: "col-fill", style: `height:${(i.count / max) * 100}%` })), `${i.name} лет: ${fmt(i.count)} чел.`))),
    el("div", { class: "col-axis" }, items.map((i) => el("span", {}, i.name))));
}

function renderActions(target, k) {
  const parts = [
    { name: "Просмотры", value: k.views, color: "var(--c1)" },
    { name: "Лайки", value: k.likes, color: "var(--c2)" },
    { name: "Дизлайки", value: k.dislikes, color: "var(--c3)" },
  ];
  const total = parts.reduce((s, p) => s + p.value, 0);
  if (!total) { target.replaceChildren(el("p", { class: "muted" }, "Реакций пока нет")); return; }
  target.replaceChildren(
    el("div", { class: "stack-bar" }, parts.filter((p) => p.value).map((p) =>
      withTip(el("div", { style: `flex:${p.value};background:${p.color}` }), `${p.name}: ${fmt(p.value)} (${Math.round((p.value / total) * 100)}%)`))),
    el("div", { class: "legend" }, parts.map((p) =>
      el("span", {}, el("i", { style: `background:${p.color}` }), p.name, " ", el("b", {}, fmt(p.value))))));
}

async function loadStats() {
  try {
    const s = await api("/api/stats");
    const k = s.kpis;
    const kpi = (v, label) => el("div", { class: "kpi" }, el("div", { class: "kpi-value num" }, fmt(v)), el("div", { class: "kpi-label" }, label));
    $("#kpis").replaceChildren(kpi(k.books, "книг в каталоге"), kpi(k.users, "читателей"), kpi(k.sets, "подборок выдано"), kpi(k.likes, "лайков"), kpi(k.dislikes, "дизлайков"));
    renderHBars($("#chart-genres"), s.genres, "книг");
    renderCols($("#chart-ages"), s.ages);
    renderActions($("#chart-actions"), k);
    $("#top-rows").replaceChildren(...s.top_books.map((b) =>
      el("tr", { onclick: () => { showView("catalog"); showDetail(b); } },
        el("td", {}, el("div", { class: "cell-title" }, b.title), el("div", { class: "muted small" }, b.author)),
        el("td", { class: "num" }, fmt(b.likes)),
        el("td", { class: "num" }, fmt(b.views)))));
  } catch (err) {
    toast(err.message);
  }
}
$("#stats-refresh").onclick = loadStats;

// ---------- старт ----------

document.addEventListener("keydown", (e) => {
  if (e.key !== "Escape") return;
  if (!$("#age-dialog").hidden) $("#age-dialog").hidden = true;
  if (!$("#confirm-dialog").hidden) $("#confirm-no").click();
});

(async function init() {
  try {
    state.me = await api("/api/me");
    setChatStatus(state.me.chat);
  } catch (err) {
    state.me = {};
    toast(err.message);
  }
  updateProfileButton();
  const initial = location.hash.slice(1);
  showView(document.querySelector(`[data-view="${initial}"]`) ? initial : "chat");
  if (!state.me.age) openAgeDialog();
  setTimeout(pollHealth, 4000);
})();
