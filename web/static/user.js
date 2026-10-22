"use strict";

const state = { me: {}, chat: { state: "loading" }, loaded: new Set() };
const TITLES = { feed: "Feed", chat: "Advisor", forme: "For You", explore: "Explore", add: "Add a Book", profile: "Profile" };
// Tab that stays highlighted on views without their own tab
const TAB_OF = { add: "explore" };

// Status phrases while the advisor works: "library browsing" and "whimsical" sets
const LIBRARY_PHRASES = [
  "Browsing the shelves…",
  "Pulling volumes from the stacks…",
  "Checking the card catalog…",
  "Dusting off old editions…",
];
const WHIMSICAL_PHRASES = [
  "Asking the librarian…",
  "Brewing tea for a long read…",
  "Following the plot twists…",
  "Chasing footnotes…",
  "Whispering to the bookworms…",
];

function shuffled(list) {
  const a = [...list];
  for (let i = a.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1));
    [a[i], a[j]] = [a[j], a[i]];
  }
  return a;
}

// ---------- thinking indicator ----------

function thinking(phrases) {
  // Open with a library phrase, then mix in the rest
  const order = [...shuffled(LIBRARY_PHRASES).slice(0, 1), ...shuffled(phrases)];
  const queue = order.filter((p, i) => order.indexOf(p) === i);
  let n = 0;
  const text = el("span", { class: "thinking-text" }, queue[0]);
  const node = el("div", { class: "thinking", role: "status", "aria-live": "polite" },
    el("div", { class: "flipbook", "aria-hidden": "true" }, el("i"), el("i"), el("i")), text);
  const timer = setInterval(() => {
    n = (n + 1) % queue.length;
    text.textContent = queue[n];
    text.classList.remove("swap");
    void text.offsetWidth;
    text.classList.add("swap");
  }, 2400);
  node.stop = () => clearInterval(timer);
  return node;
}

// Reveals the reply word by word, like a streamed answer
function reveal(node, text) {
  return new Promise((resolve) => {
    if (reducedMotion.matches) { node.textContent = text; return resolve(); }
    const words = text.split(/(\s+)/);
    const perTick = Math.max(1, Math.ceil(words.length / 140));
    let i = 0;
    node.classList.add("caret");
    const tick = () => {
      node.textContent += words.slice(i, i + perTick).join("");
      i += perTick;
      if (i < words.length) setTimeout(tick, 24);
      else { node.classList.remove("caret"); resolve(); }
    };
    tick();
  });
}

// ---------- navigation ----------

const app = $("#app");
const currentView = () => app.dataset.view;

// Views are tabs ("feed") or detail pages ("user/12", "community/3"), rendered by social.js into the page panel
function showView(name) {
  const base = name.split("/")[0];
  const isPage = Boolean(PAGES[base]);
  if (!TITLES[name] && !isPage) name = "feed";
  app.dataset.view = name;
  const tab = isPage ? state.lastTab : TAB_OF[name] || name;
  if (!isPage) state.lastTab = TAB_OF[name] || name;
  $$(".tab").forEach((t) => t.setAttribute("aria-selected", t.dataset.view === tab));
  const panel = isPage ? "page" : name;
  $$("[data-panel]").forEach((p) => {
    const on = p.dataset.panel === panel;
    p.hidden = !on;
    if (on) { p.classList.remove("view-enter"); void p.offsetWidth; p.classList.add("view-enter"); }
  });
  $("#back-btn").hidden = !isPage && name !== "add";
  $("#newchat-btn").hidden = name !== "chat";
  if (location.hash.slice(1) !== name) history.replaceState(null, "", `#${name}`);
  window.scrollTo({ top: name === "chat" ? document.body.scrollHeight : 0 });
  if (isPage) return renderPage(name);
  $("#page-title").textContent = TITLES[name];
  renderIdentity();
  const first = !state.loaded.has(name);
  state.loaded.add(name);
  if (name === "feed") loadFeed();
  if (name === "explore" && first) loadExplore();
  if (name === "forme" && first) loadForMe();
  if (name === "add" && first) loadGenreHints();
  if (name === "profile") { fillProfile(); renderMySocial(); }
}

$$(".tab").forEach((t) => (t.onclick = () => showView(t.dataset.view)));
// Links between pages push history entries, so Back returns to where the reader came from
state.depth = 0;
window.addEventListener("hashchange", () => {
  const name = location.hash.slice(1);
  if (name !== currentView()) { state.depth++; showView(name); }
});
$("#back-btn").onclick = () => {
  if (state.depth > 0) { state.depth -= 2; history.back(); } // the hashchange it fires adds one back
  else showView(state.lastTab || "feed");
};

// ---------- status ----------

function setChatStatus(chat) {
  state.chat = chat;
  const ready = chat.state === "ready";
  const message = chat.state === "loading"
    ? "The advisor is warming up its search models. Chat will be ready in a minute; For You already works."
    : `The advisor is unavailable: ${chat.error || "unknown error"}. For You still works.`;
  $("#chat-alert").hidden = ready;
  $("#chat-alert").textContent = message;
  $("#add-alert").hidden = ready;
  $("#add-alert").textContent = ready ? "" : "Adding books needs the advisor's search index. " + message;
  $("#send-btn").disabled = !ready;
  $("#add-submit").disabled = !ready;
  const badge = $("#engine-badge");
  const [cls, label] = { ready: ["ok", "Ready"], loading: ["warn", "Warming up"] }[chat.state] || ["danger", "Unavailable"];
  badge.className = `badge ${cls}`;
  badge.textContent = label;
}

async function pollHealth() {
  try { setChatStatus((await api("/api/health")).chat); } catch {}
  setTimeout(pollHealth, state.chat.state === "loading" ? 4000 : 15000);
}

// ---------- profile ----------

function initials(name) {
  return (name || "").split(/\s+/).filter(Boolean).slice(0, 2).map((w) => w[0].toUpperCase()).join("") || "?";
}

function renderIdentity() {
  const { name, age } = state.me;
  $("#eyebrow").textContent = name ? `Hi, ${name}` : "Books Advisor";
  $("#avatar").textContent = initials(name);
  $("#profile-name").textContent = name || "Reader";
  const lang = $(`#p-lang option[value="${state.me.language}"]`);
  $("#profile-sub").textContent = age ? `${age} years · ${lang ? lang.textContent : "English"}` : "Not set up yet";
  $("#profile-alert").hidden = Boolean(age);
  $("#profile-email").textContent = state.me.email || "";
}

function fillProfile() {
  $("#p-name").value = state.me.name || "";
  $("#p-bio").value = state.me.bio || "";
  $("#p-age").value = state.me.age ?? "";
  const browser = (navigator.language || "en").slice(0, 2);
  const lang = state.me.language || browser;
  $("#p-lang").value = $(`#p-lang option[value="${lang}"]`) ? lang : "en";
}

function needProfile() {
  if (state.me.age) return false;
  toast("Set up your profile first");
  showView("profile");
  $("#p-age").focus();
  return true;
}

$("#profile-form").onsubmit = async (e) => {
  e.preventDefault();
  const firstTime = !state.me.age;
  $("#profile-save").disabled = true;
  try {
    const me = await api("/api/me", {
      method: "PUT",
      body: { name: $("#p-name").value, bio: $("#p-bio").value, age: Number($("#p-age").value), language: $("#p-lang").value },
    });
    Object.assign(state.me, me);
    renderIdentity();
    renderMySocial();
    state.loaded.delete("forme");
    toast("Profile saved");
    if (firstTime) showView("chat");
  } catch (err) {
    toast(err.message);
  } finally {
    $("#profile-save").disabled = false;
  }
};

// ---------- books & feedback ----------

const bookHref = (b) => `/api/books/${b.id}/open`;
const externalIcon = () => {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("class", "ext");
  svg.setAttribute("aria-hidden", "true");
  svg.innerHTML = '<path d="M14 4h6v6M20 4l-9 9M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>';
  return svg;
};
const linkProps = (b, cls, i, step) => ({
  class: `${cls} enter`, style: `animation-delay:${i * step}ms`,
  href: bookHref(b), target: "_blank", rel: "noopener", title: `Read about "${b.title}"`,
});

function bookTiles(books) {
  return el("div", { class: "books" }, books.map((b, i) =>
    el("a", linkProps(b, "book", i, 60), externalIcon(),
      el("span", { class: "book-n" }, i + 1),
      el("div", {},
        el("span", { class: "book-title" }, b.title), " ",
        el("span", { class: "book-meta" }, `· ${b.author}`)),
      b.genre ? el("span", { class: "book-meta", style: "grid-column:2" }, b.genre) : null,
      b.summary ? el("p", { class: "book-desc" }, b.summary) : null)));
}

function feedbackBar(setId) {
  const bar = el("div", { class: "feedback enter" }, el("span", { class: "muted small" }, "Good picks?"));
  const send = async (action) => {
    $$("button", bar).forEach((b) => (b.disabled = true));
    try {
      await api("/api/feedback", { method: "POST", body: { set_id: setId, action } });
      bar.replaceChildren(el("span", { class: `badge pop ${action === "like" ? "ok" : ""}` },
        action === "like" ? "Thanks! We'll find more like these" : "Got it, we'll steer away from these"));
    } catch (err) {
      $$("button", bar).forEach((b) => (b.disabled = false));
      toast(err.message);
    }
  };
  bar.append(
    el("button", { class: "btn sm", onclick: () => send("like") }, "👍 Yes"),
    el("button", { class: "btn sm", onclick: () => send("dislike") }, "👎 Not really"));
  return bar;
}

// ---------- chat ----------

const input = $("#chat-input");

function addMessage(cls, ...content) {
  $("#chat-empty").hidden = true;
  const msg = el("div", { class: `msg enter ${cls}` }, ...content);
  $("#messages").append(msg);
  scrollToEnd();
  return msg;
}

const scrollToEnd = () => window.scrollTo({ top: document.body.scrollHeight, behavior: reducedMotion.matches ? "auto" : "smooth" });

async function sendMessage(text) {
  text = text.trim();
  if (!text || needProfile()) return;
  if (state.chat.state !== "ready") return toast("The advisor isn't ready yet");
  input.value = "";
  autosize();
  addMessage("user", text);
  const indicator = thinking(WHIMSICAL_PHRASES);
  const msg = addMessage("bot", indicator);
  $("#send-btn").disabled = true;
  try {
    const res = await api("/api/chat", { method: "POST", body: { message: text } });
    indicator.stop();
    const body = el("p");
    msg.replaceChildren(body);
    msg.classList.add("wide");
    await reveal(body, res.reply);
    if (res.books.length) msg.append(bookTiles(res.books), feedbackBar(res.set_id));
  } catch (err) {
    indicator.stop();
    msg.replaceChildren(el("div", { class: "alert danger" }, err.message));
  } finally {
    $("#send-btn").disabled = state.chat.state !== "ready";
    scrollToEnd();
  }
}

function autosize() {
  input.style.height = "auto";
  input.style.height = `${Math.min(input.scrollHeight, 140)}px`;
}

$("#composer").onsubmit = (e) => { e.preventDefault(); sendMessage(input.value); };
input.addEventListener("input", autosize);
input.addEventListener("keydown", (e) => {
  // On touch keyboards Enter inserts a newline; the send button sends
  if (e.key === "Enter" && !e.shiftKey && !e.isComposing && matchMedia("(hover: hover)").matches) {
    e.preventDefault();
    if (!$("#send-btn").disabled) sendMessage(input.value);
  }
});
$$("[data-suggest]").forEach((c) => (c.onclick = () => sendMessage(c.dataset.suggest)));

$("#newchat-btn").onclick = async () => {
  if (!$("#messages .msg")) return;
  if (!(await confirmDialog("Start a new chat?", "The advisor will forget this conversation.", "Start over"))) return;
  await api("/api/chat/reset", { method: "POST" }).catch(() => {});
  $("#messages").replaceChildren($("#chat-empty"));
  $("#chat-empty").hidden = false;
  toast("New chat started");
};

// Voice input via the Web Speech API (the Telegram bot transcribes voice messages instead)
const Recognition = window.SpeechRecognition || window.webkitSpeechRecognition;
if (Recognition) {
  const mic = $("#mic-btn");
  const rec = new Recognition();
  rec.lang = navigator.language || "en-US";
  rec.interimResults = false;
  mic.hidden = false;
  mic.onclick = () => (mic.classList.contains("listening") ? rec.stop() : rec.start());
  rec.onstart = () => mic.classList.add("listening");
  rec.onend = () => mic.classList.remove("listening");
  rec.onerror = () => toast("Couldn't catch that, try again");
  rec.onresult = (e) => { input.value = e.results[0][0].transcript; autosize(); input.focus(); };
}

// ---------- For You ----------

async function loadForMe() {
  const body = $("#forme-body");
  if (!state.me.age) {
    body.replaceChildren(el("div", { class: "alert attention enter" },
      "Set up your profile to get personal picks. ",
      el("button", { class: "btn sm", style: "margin-left:6px", onclick: () => showView("profile") }, "Set up")));
    return;
  }
  const indicator = thinking(LIBRARY_PHRASES);
  body.replaceChildren(el("div", { class: "card" }, indicator));
  $("#forme-refresh").disabled = true;
  try {
    // Keep the animation on screen long enough to read one phrase
    const [res] = await Promise.all([api("/api/recommendations"), new Promise((r) => setTimeout(r, 900))]);
    body.replaceChildren(...(res.books.length
      ? [...res.books.map((b, i) => el("a", linkProps(b, "pick", i, 70), externalIcon(),
          el("div", { class: "pick-cover", style: "background:var(--accent)" }, (b.title || "?")[0]),
          el("div", { class: "pick-title" }, b.title),
          el("div", { class: "pick-meta" }, [b.author, b.genre].filter(Boolean).join(" · ")),
          b.summary ? el("p", { class: "book-desc" }, b.summary) : null)),
        feedbackBar(res.set_id)]
      : [el("p", { class: "muted" }, "The library is empty for now.")]));
  } catch (err) {
    body.replaceChildren(el("div", { class: "alert danger" }, err.message));
  } finally {
    indicator.stop();
    $("#forme-refresh").disabled = false;
  }
}
$("#forme-refresh").onclick = loadForMe;

// ---------- Add a book ----------

async function loadGenreHints() {
  try {
    const genres = await api("/api/genres?limit=30");
    $("#genre-list").replaceChildren(...genres.map((g) => el("option", { value: g.name })));
  } catch {}
}

$("#add-form").onsubmit = async (e) => {
  e.preventDefault();
  const body = Object.fromEntries(new FormData(e.target).entries());
  body.rating = body.rating ? Number(body.rating) : null;
  $("#add-submit").disabled = true;
  try {
    const book = await api("/api/books", { method: "POST", body });
    e.target.reset();
    toast(`"${book.title}" is now in the library`);
  } catch (err) {
    toast(err.message);
  } finally {
    $("#add-submit").disabled = state.chat.state !== "ready";
  }
};

// ---------- account: sign in, sign up, consent ----------

let consentVersion = null;
const screens = { auth: $("#auth"), consent: $("#consent-screen"), app: $("#app") };

function showScreen(name) {
  for (const [key, node] of Object.entries(screens)) node.hidden = key !== name;
  window.scrollTo({ top: 0 });
}

function showAuth(tab = "signin") {
  showScreen("auth");
  $$("[data-auth]").forEach((b) => b.setAttribute("aria-selected", b.dataset.auth === tab));
  $("#signin-form").hidden = tab !== "signin";
  $("#signup-form").hidden = tab !== "signup";
  const browser = (navigator.language || "en").slice(0, 2);
  if ($(`#su-lang option[value="${browser}"]`)) $("#su-lang").value = browser;
}

function showConsent() {
  showScreen("consent");
  $("#re-consent").checked = false;
  $("#re-consent-btn").disabled = true;
}

$$("[data-auth]").forEach((b) => (b.onclick = () => showAuth(b.dataset.auth)));
$("#su-consent").onchange = (e) => ($("#signup-btn").disabled = !e.target.checked);
$("#re-consent").onchange = (e) => ($("#re-consent-btn").disabled = !e.target.checked);

async function withButton(btn, fn) {
  btn.disabled = true;
  try { await fn(); } catch (err) { toast(err.message); } finally { btn.disabled = false; }
}

$("#signin-form").onsubmit = (e) => {
  e.preventDefault();
  withButton($("#signin-btn"), async () => {
    const res = await api("/api/auth/login", { method: "POST", body: { email: $("#si-email").value, password: $("#si-password").value } });
    setTokens(res);
    $("#si-password").value = "";
    await enter();
  });
};

$("#signup-form").onsubmit = (e) => {
  e.preventDefault();
  withButton($("#signup-btn"), async () => {
    if (!consentVersion) consentVersion = (await api("/api/auth/consent")).version;
    const res = await api("/api/auth/register", {
      method: "POST",
      body: {
        name: $("#su-name").value, email: $("#su-email").value, password: $("#su-password").value,
        age: Number($("#su-age").value), language: $("#su-lang").value,
        consent: $("#su-consent").checked, consent_version: consentVersion,
      },
    });
    setTokens(res);
    $("#su-password").value = "";
    toast("Welcome to Books Advisor!");
    await enter();
  });
};

$("#re-consent-btn").onclick = () => withButton($("#re-consent-btn"), async () => {
  state.me = await api("/api/me/consent", { method: "POST", body: { version: consentVersion } });
  await enter();
});
$("#re-consent-signout").onclick = async () => { await signOut(); showAuth(); };

$("#signout-btn").onclick = async () => {
  if (!(await confirmDialog("Sign out?", "You can sign back in any time with your email and password.", "Sign out"))) return;
  await signOut();
  resetApp();
  showAuth();
};

$("#export-btn").onclick = () => withButton($("#export-btn"), async () => {
  const data = await api("/api/me/export");
  const url = URL.createObjectURL(new Blob([JSON.stringify(data, null, 2)], { type: "application/json" }));
  el("a", { href: url, download: "books-advisor-my-data.json" }).click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
});

$("#delete-btn").onclick = () => { $("#del-password").value = ""; $("#delete-dialog").hidden = false; $("#del-password").focus(); };
$("#delete-cancel").onclick = () => ($("#delete-dialog").hidden = true);
$("#delete-form").onsubmit = async (e) => {
  e.preventDefault();
  try {
    await api("/api/me", { method: "DELETE", body: { password: $("#del-password").value } });
    $("#delete-dialog").hidden = true;
    clearTokens();
    resetApp();
    showAuth("signup");
    toast("Your account and data were deleted");
  } catch (err) {
    toast(err.message);
  }
};

// Forget everything from the previous reader when the account changes
function resetApp() {
  state.me = {};
  state.loaded.clear();
  $("#messages").replaceChildren($("#chat-empty"));
  $("#chat-empty").hidden = false;
  $("#forme-body").replaceChildren();
  resetSocial();
}

async function enter() {
  state.me = await api("/api/me");
  if (state.me.consent_required) return showConsent();
  showScreen("app");
  setChatStatus(state.me.chat);
  renderIdentity();
  fillProfile();
  showView(location.hash.slice(1) || "feed");
}

session.key = "ba.refresh";
session.onSignedOut = () => { resetApp(); showAuth(); };
session.onConsentRequired = showConsent;

// ---------- start ----------

(async function init() {
  api("/api/auth/consent").then((c) => (consentVersion = c.version)).catch(() => {});
  try {
    if (await refreshSession()) await enter();
    else showAuth();
  } catch (err) {
    toast(err.message);
    showAuth();
  }
  setTimeout(pollHealth, 4000);
})();
