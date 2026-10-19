"use strict";

const LANGUAGES = {
  en: "English", ru: "Russian", de: "German", fr: "French", es: "Spanish", it: "Italian",
  pt: "Portuguese", uk: "Ukrainian", kk: "Kazakh", zh: "Chinese", ja: "Japanese", tr: "Turkish",
};
const langName = (code) => (code ? LANGUAGES[code] || code : "—");
const state = {
  view: "overview",
  users: { q: "", source: "all", sort: "last_active", active: null },
  books: { q: "", genre: "", sort: "newest", offset: 0, active: null },
  settings: null,
};
const PAGE = 50;

async function adminApi(path, opts) {
  try {
    return await api(path, opts);
  } catch (err) {
    if (err.status === 401) showLogin();
    throw err;
  }
}

// ---------- session ----------

function showLogin(disabled = false) {
  $("#console").hidden = true;
  $("#login").hidden = false;
  $("#login-disabled").hidden = !disabled;
  $("#login-fields").hidden = disabled;
  if (!disabled) $("#password").focus();
}

function showConsole() {
  $("#login").hidden = true;
  $("#console").hidden = false;
  const name = location.hash.slice(1);
  showView($(`.nav-item[data-view="${name}"]`) ? name : "overview");
  pollHealth();
}

$("#login-form").onsubmit = async (e) => {
  e.preventDefault();
  $("#login-btn").disabled = true;
  try {
    await api("/api/admin/login", { method: "POST", body: { password: $("#password").value } });
    $("#password").value = "";
    showConsole();
  } catch (err) {
    toast(err.message);
    $("#password").select();
  } finally {
    $("#login-btn").disabled = false;
  }
};

$("#logout-btn").onclick = async () => {
  await api("/api/admin/logout", { method: "POST" }).catch(() => {});
  showLogin();
};

async function pollHealth() {
  if ($("#console").hidden) return;
  try {
    const { chat } = await api("/api/health");
    const [cls, label] = { ready: ["ok", "Advisor ready"], loading: ["warn", "Advisor loading"] }[chat.state] || ["danger", "Advisor unavailable"];
    $("#engine-badge").className = `badge ${cls}`;
    $("#engine-badge").textContent = label;
    state.engine = chat;
  } catch {}
  setTimeout(pollHealth, 15000);
}

// ---------- navigation ----------

const LOADERS = { overview: loadOverview, users: loadUsers, books: () => { loadGenres(); loadBooks(true); }, settings: loadSettings };

async function showView(name) {
  if (state.view === "settings" && name !== "settings" && isDirty()
      && !(await confirmDialog("Discard unsaved changes?", "Your settings edits haven't been saved.", "Discard"))) return;
  state.view = name;
  $$(".nav-item").forEach((b) => b.classList.toggle("active", b.dataset.view === name));
  $$("[data-panel]").forEach((p) => {
    p.hidden = p.dataset.panel !== name;
    if (!p.hidden) { p.classList.remove("view-enter"); void p.offsetWidth; p.classList.add("view-enter"); }
  });
  if (location.hash.slice(1) !== name) history.replaceState(null, "", `#${name}`);
  LOADERS[name]();
}

$$(".nav-item").forEach((b) => (b.onclick = () => showView(b.dataset.view)));
$$("[data-refresh]").forEach((b) => (b.onclick = () => LOADERS[b.dataset.refresh]()));
window.addEventListener("hashchange", () => {
  const name = location.hash.slice(1);
  if (name !== state.view && LOADERS[name] && !$("#console").hidden) showView(name);
});

// ---------- overview ----------

const shortDate = (iso) => new Date(`${iso}T00:00:00`).toLocaleDateString("en-US", { month: "short", day: "numeric" });

async function loadOverview() {
  let o;
  try { o = await adminApi("/api/admin/overview"); } catch (err) { return toast(err.message); }
  const k = o.kpis;
  $("#kpis").replaceChildren(
    el("div", { class: "kpi" }, el("div", { class: "kpi-value num" }, fmt(k.users)), el("div", { class: "kpi-label" }, "Users"),
      el("div", { class: "kpi-sub" }, `${fmt(k.web_users)} web · ${fmt(k.telegram_users)} Telegram`)),
    kpiTile(fmt(k.active_7d), "Active in 7 days"),
    kpiTile(fmt(k.books), "Books"),
    kpiTile(fmt(k.sets), "Recommendation sets"),
    kpiTile(fmt(k.likes), "Likes"),
    kpiTile(fmtPct(k.like_rate), "Like rate"));

  const alert = $("#engine-alert");
  alert.hidden = o.engine.state === "ready";
  alert.textContent = o.engine.state === "loading"
    ? "The advisor is still loading its models. Chat and adding books are unavailable to readers until it's ready."
    : `The advisor is unavailable: ${o.engine.error || "unknown error"}. Readers can only use For You.`;

  $("#chart-days").replaceChildren(columnChart(o.sets_per_day, {
    labels: "max",
    axis: (i, n) => (n % 2 === 0 ? shortDate(i.name) : ""),
    tip: (i) => `${shortDate(i.name)}: ${fmt(i.count)} sets`,
    empty: "No recommendations in the last 14 days",
  }));
  $("#chart-reactions").replaceChildren(stackChart([
    { name: "Likes", value: k.likes, color: "var(--c1)" },
    { name: "Dislikes", value: k.dislikes, color: "var(--c2)" },
  ], "No reactions yet"));
  $("#chart-genres").replaceChildren(hbarChart(o.genres, "books"));
  $("#chart-ages").replaceChildren(columnChart(o.ages, { tip: (i) => `${i.name} years: ${fmt(i.count)} readers`, empty: "No reader has set an age yet" }));
  $("#chart-langs").replaceChildren(hbarChart(o.languages.map((l) => ({ ...l, name: langName(l.name) })), "readers", "No languages yet"));
  $("#top-rows").replaceChildren(...o.top_books.map((b) =>
    el("tr", { onclick: () => { state.books.active = b.id; showView("books").then(() => showBook(b)); } },
      el("td", {}, el("div", { class: "cell-title" }, b.title), el("div", { class: "muted small" }, b.author)),
      el("td", { class: "small" }, b.genre || "—"),
      el("td", { class: "num" }, fmt(b.likes)),
      el("td", { class: "num" }, fmt(b.dislikes)),
      el("td", { class: "num" }, fmt(b.views)),
      el("td", { class: "num" }, b.mean_age == null ? "—" : Math.round(b.mean_age)))));
}

// ---------- users ----------

const sourceBadge = (s) => el("span", { class: `badge ${s === "web" ? "" : "neutral"}` }, s === "web" ? "Web" : "Telegram");
const userName = (u) => u.name || (u.source === "web" ? "Anonymous reader" : "Telegram reader");

async function loadUsers() {
  const u = state.users;
  let res;
  try { res = await adminApi(`/api/admin/users?${new URLSearchParams({ q: u.q, source: u.source, sort: u.sort })}`); }
  catch (err) { return toast(err.message); }
  $("#users-sub").textContent = `${fmt(res.total)} ${res.total === 1 ? "user" : "users"}`;
  $("#user-rows").replaceChildren(...(res.users.length ? res.users.map((x) => {
    const tr = el("tr", { class: u.active === x.id ? "active" : null },
      el("td", {}, el("div", { class: "cell-title" }, userName(x)), el("div", { class: "muted small num" }, `id ${x.id}`)),
      el("td", {}, sourceBadge(x.source)),
      el("td", { class: "num" }, x.age ?? "—"),
      el("td", {}, langName(x.language)),
      el("td", { class: "num" }, fmt(x.sets)),
      el("td", { class: "num" }, fmt(x.likes)),
      el("td", { class: "num" }, fmt(x.dislikes)),
      el("td", { class: "small", title: x.last_active || "" }, ago(x.last_active)));
    tr.onclick = () => {
      u.active = x.id;
      $$("#user-rows tr").forEach((r) => r.classList.toggle("active", r === tr));
      showUser(x.id);
    };
    return tr;
  }) : [el("tr", {}, el("td", { colspan: 8, class: "muted" }, "No users match"))]));
}

async function showUser(id) {
  const box = $("#user-detail");
  let d;
  try { d = await adminApi(`/api/admin/users/${id}`); } catch (err) { return toast(err.message); }
  const list = (items, empty) => (items.length
    ? el("ul", { class: "list" }, items.map((t) => el("li", {}, t)))
    : el("p", { class: "muted small" }, empty));
  box.replaceChildren(el("div", { class: "enter" },
    el("div", { class: "row", style: "gap:12px;margin-bottom:12px" },
      el("div", { class: "avatar" }, (d.name || "?").slice(0, 1).toUpperCase()),
      el("div", {}, el("h3", { style: "margin:0" }, userName(d)), el("div", { class: "muted small num" }, `id ${d.id}`))),
    el("div", { class: "badges" }, sourceBadge(d.source),
      d.age != null ? el("span", { class: "badge neutral" }, `${d.age} years`) : null,
      d.language ? el("span", { class: "badge neutral" }, langName(d.language)) : null),
    el("div", { class: "kpis", style: "grid-template-columns:repeat(3,1fr)" },
      kpiTile(fmt(d.sets), "sets"), kpiTile(fmt(d.likes), "likes"), kpiTile(fmt(d.dislikes), "dislikes")),
    el("p", { class: "muted small", style: "margin-top:12px" },
      `Last active ${ago(d.last_active)}`, d.created_at ? ` · joined ${d.created_at.slice(0, 10)}` : "",
      d.chat_messages != null ? ` · ${d.chat_messages} messages in the current chat` : ""),
    el("h4", {}, "Liked"), list(d.liked, "No likes yet"),
    el("h4", {}, "Disliked"), list(d.disliked, "No dislikes yet"),
    el("h4", {}, "Recent recommendations"),
    d.recent_sets.length
      ? d.recent_sets.map((s) => el("div", { class: "set" }, el("time", {}, ago(s.at)), s.books.join(" · ")))
      : el("p", { class: "muted small" }, "Nothing recommended yet")));
}

let userTimer;
$("#u-q").addEventListener("input", (e) => {
  clearTimeout(userTimer);
  userTimer = setTimeout(() => { state.users.q = e.target.value; loadUsers(); }, 250);
});
$("#u-sort").onchange = (e) => { state.users.sort = e.target.value; loadUsers(); };
$$("#u-source .chip").forEach((c) => (c.onclick = () => {
  state.users.source = c.dataset.source;
  $$("#u-source .chip").forEach((x) => x.classList.toggle("active", x === c));
  loadUsers();
}));

// ---------- books ----------

async function loadGenres() {
  try {
    const genres = await adminApi("/api/genres?limit=14");
    const chip = (name, label) => el("button", {
      class: `chip${state.books.genre === name ? " active" : ""}`, "data-genre": name,
      onclick: () => {
        state.books.genre = name;
        $$("#b-genres .chip").forEach((c) => c.classList.toggle("active", c.dataset.genre === name));
        loadBooks(true);
      },
    }, label);
    $("#b-genres").replaceChildren(chip("", "All genres"), ...genres.map((g) => chip(g.name, g.name)));
  } catch {}
}

async function loadBooks(reset) {
  const b = state.books;
  if (reset) b.offset = 0;
  let res;
  try { res = await adminApi(`/api/admin/books?${new URLSearchParams({ q: b.q, genre: b.genre, sort: b.sort, offset: b.offset, limit: PAGE })}`); }
  catch (err) { return toast(err.message); }
  const rows = res.books.map((x) => {
    const tr = el("tr", { class: b.active === x.id ? "active" : null },
      el("td", { class: "num muted" }, x.id),
      el("td", {}, el("div", { class: "cell-title" }, x.title), el("div", { class: "muted small" }, x.author)),
      el("td", { class: "small" }, x.genre || "—"),
      el("td", { class: "num" }, fmtRating(x.rating)),
      el("td", { class: "num" }, fmt(x.views)),
      el("td", { class: "num" }, fmt(x.likes)),
      el("td", { class: "num" }, fmt(x.dislikes)),
      el("td", { class: "num" }, x.mean_age == null ? "—" : Math.round(x.mean_age)));
    tr.onclick = () => {
      b.active = x.id;
      $$("#book-rows tr").forEach((r) => r.classList.toggle("active", r === tr));
      showBook(x);
    };
    return tr;
  });
  if (reset) $("#book-rows").replaceChildren(...rows);
  else $("#book-rows").append(...rows);
  if (!res.total) $("#book-rows").replaceChildren(el("tr", {}, el("td", { colspan: 8, class: "muted" }, "No books match")));
  b.offset += res.books.length;
  $("#books-sub").textContent = `${fmt(res.total)} ${res.total === 1 ? "book" : "books"}`;
  $("#b-more").hidden = b.offset >= res.total;
}

function showBook(b) {
  const reactions = (b.likes || 0) + (b.dislikes || 0);
  $("#book-detail").replaceChildren(el("div", { class: "enter" },
    el("h3", {}, b.title),
    el("p", { class: "muted" }, b.author),
    el("div", { class: "badges" },
      (b.genre || "").split(",").filter(Boolean).map((g) => el("span", { class: "badge" }, g.trim())),
      b.rating ? el("span", { class: "badge ok num" }, `★ ${fmtRating(b.rating)}`) : null),
    el("p", {}, b.description || "No description."),
    el("div", { class: "kpis", style: "margin-top:16px;grid-template-columns:repeat(2,1fr)" },
      kpiTile(fmt(b.views || 0), "views"), kpiTile(reactions ? fmtPct((b.likes || 0) / reactions) : "—", "like rate"),
      kpiTile(fmt(b.likes || 0), "likes"), kpiTile(fmt(b.dislikes || 0), "dislikes")),
    el("p", { class: "muted small", style: "margin-top:12px" },
      `Book #${b.id}`, b.mean_age != null ? ` · mean reader age ${Math.round(b.mean_age)}` : "")));
}

let bookTimer;
$("#b-q").addEventListener("input", (e) => {
  clearTimeout(bookTimer);
  bookTimer = setTimeout(() => { state.books.q = e.target.value; loadBooks(true); }, 250);
});
$("#b-sort").onchange = (e) => { state.books.sort = e.target.value; loadBooks(true); };
$("#b-more").onclick = () => loadBooks(false);

// ---------- settings ----------

const LLM_FIELDS = [
  { key: "temperature", label: "Temperature", hint: "Higher is more creative, lower is more focused.", min: 0, max: 2, step: 0.05 },
  { key: "top_p", label: "Top-p", hint: "Share of probability mass the model samples from.", min: 0.05, max: 1, step: 0.05 },
  { key: "books_in_prompt", label: "Books in prompt", hint: "How many reranked books the model sees and shows.", min: 1, max: 20, step: 1 },
  { key: "history_length", label: "Chat history length", hint: "Messages kept per reader, including the context.", min: 2, max: 50, step: 1 },
];
const REC_FIELDS = [
  { key: "top_k", label: "Books per set", hint: "How many books For You returns.", min: 1, max: 20, step: 1 },
  { key: "like", label: "Like weight", hint: "Cold start: score per like.", min: 0, max: 5, step: 0.05 },
  { key: "view", label: "View weight", hint: "Cold start: score per view.", min: 0, max: 5, step: 0.05 },
  { key: "dislike", label: "Dislike penalty", hint: "Cold start: subtracted per dislike.", min: 0, max: 5, step: 0.05 },
  { key: "age", label: "Age match weight", hint: "Bonus for books read by people of a similar age.", min: 0, max: 5, step: 0.05 },
  { key: "age_scale", label: "Age tolerance, years", hint: "Larger means age matters less sharply.", min: 1, max: 50, step: 1 },
  { key: "language", label: "Language weight", hint: "Bonus for activity from readers of the same language.", min: 0, max: 2, step: 0.01 },
  { key: "language_like", label: "Same-language like multiplier", hint: "A same-language like counts this many views.", min: 0, max: 10, step: 0.5 },
];

function sliderField(group, f, defaults) {
  const id = `s-${group}-${f.key}`;
  const range = el("input", { type: "range", min: f.min, max: f.max, step: f.step, "aria-label": f.label });
  const number = el("input", { class: "input num", type: "number", id, min: f.min, max: f.max, step: f.step, "data-group": group, "data-key": f.key });
  range.oninput = () => { number.value = range.value; markDirty(); };
  number.oninput = () => { range.value = number.value; markDirty(); };
  number.sync = (v) => { number.value = v; range.value = v; };
  return el("div", { class: "field" },
    el("label", { for: id }, f.label),
    el("div", { class: "slider" }, range, number),
    el("span", { class: "hint" }, `${f.hint} Default: ${defaults[f.key]}.`));
}

function readForm() {
  const out = { llm: {}, recsys: {} };
  $$("#settings-form [data-key]").forEach((n) => {
    out[n.dataset.group][n.dataset.key] = n.tagName === "TEXTAREA" ? n.value : Number(n.value);
  });
  return out;
}

function fillForm(settings) {
  $$("#settings-form [data-key]").forEach((n) => {
    const v = settings[n.dataset.group][n.dataset.key];
    if (n.sync) n.sync(v); else n.value = v;
  });
  markDirty();
}

const isDirty = () => state.settings && JSON.stringify(readForm()) !== JSON.stringify(state.settings);

function markDirty() {
  const dirty = isDirty();
  $("#settings-save").disabled = !dirty;
  $("#settings-discard").disabled = !dirty;
  $("#save-state").textContent = dirty ? "You have unsaved changes" : "All changes saved";
}

async function loadSettings() {
  let res;
  try { res = await adminApi("/api/admin/settings"); } catch (err) { return toast(err.message); }
  const d = res.defaults;
  $("#llm-fields").replaceChildren(
    ...LLM_FIELDS.map((f) => sliderField("llm", f, d.llm)),
    el("div", { class: "field" },
      el("label", { for: "s-llm-system_prompt" }, "System prompt"),
      el("textarea", { class: "textarea", id: "s-llm-system_prompt", "data-group": "llm", "data-key": "system_prompt", maxlength: 4000, oninput: markDirty }),
      el("span", { class: "hint" }, "Existing conversations switch to the new prompt immediately.")));
  $("#rec-fields").replaceChildren(...REC_FIELDS.map((f) => sliderField("recsys", f, d.recsys)));
  const ready = res.engine.state === "ready";
  $("#llm-badge").className = `badge ${ready ? "ok" : "warn"}`;
  $("#llm-badge").textContent = ready ? "Live" : "Applies when the advisor loads";
  state.settings = res.settings;
  state.defaults = d;
  fillForm(res.settings);
}

$("#settings-form").onsubmit = async (e) => {
  e.preventDefault();
  $("#settings-save").disabled = true;
  try {
    const res = await adminApi("/api/admin/settings", { method: "PUT", body: readForm() });
    state.settings = res.settings;
    fillForm(res.settings);
    toast("Settings saved");
  } catch (err) {
    toast(err.message);
    markDirty();
  }
};
$("#settings-discard").onclick = () => fillForm(state.settings);
$("#settings-reset").onclick = async () => {
  if (!(await confirmDialog("Reset all settings?", "Every model and recommender setting goes back to its default.", "Reset"))) return;
  try {
    const res = await adminApi("/api/admin/settings/reset", { method: "POST" });
    state.settings = res.settings;
    fillForm(res.settings);
    toast("Settings reset to defaults");
  } catch (err) { toast(err.message); }
};
window.addEventListener("beforeunload", (e) => { if (isDirty()) e.preventDefault(); });

// ---------- start ----------

(async function init() {
  try {
    const s = await api("/api/admin/session");
    if (!s.enabled) showLogin(true);
    else if (!s.signed_in) showLogin();
    else showConsole();
  } catch (err) {
    toast(err.message);
  }
})();
