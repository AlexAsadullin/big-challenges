"use strict";
// Helpers shared by the reader app and the admin console.

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];
const reducedMotion = matchMedia("(prefers-reduced-motion: reduce)");

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

const fmt = (n) => (n == null ? "—" : Number(n).toLocaleString("en-US"));
const fmtRating = (r) => (r ? Number(r).toFixed(1) : "—");
const fmtPct = (r) => (r == null ? "—" : `${Math.round(r * 100)}%`);

// SQLite timestamps are UTC "YYYY-MM-DD HH:MM:SS"
function ago(ts) {
  if (!ts) return "never";
  const s = (Date.now() - new Date(`${ts.replace(" ", "T")}Z`)) / 1000;
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)} min ago`;
  if (s < 86400) return `${Math.floor(s / 3600)} h ago`;
  if (s < 86400 * 30) return `${Math.floor(s / 86400)} d ago`;
  return ts.slice(0, 10);
}

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
    throw new Error("No connection to the server");
  }
  setOnline(true);
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const detail = Array.isArray(data.detail) ? "Please check the highlighted fields" : data.detail;
    const err = new Error(detail || `Error ${res.status}`);
    err.status = res.status;
    throw err;
  }
  return data;
}

function setOnline(on) {
  const dot = $("#conn-dot");
  if (dot) dot.className = on ? "dot" : "dot off";
  const text = $("#conn-text");
  if (text) {
    text.textContent = on ? "Online" : "Offline";
    if (text.classList.contains("badge")) text.className = `badge ${on ? "ok" : "danger"}`;
  }
}

let toastTimer;
function toast(text) {
  const t = $("#toast");
  t.textContent = text;
  t.hidden = true;
  void t.offsetWidth; // restart the entrance animation
  t.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (t.hidden = true), 2800);
}

function confirmDialog(title, text, yesLabel = "Confirm") {
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
      document.removeEventListener("keydown", onKey);
      resolve(v);
    };
    const onKey = (e) => e.key === "Escape" && done(false);
    document.addEventListener("keydown", onKey);
    $("#confirm-yes").onclick = () => done(true);
    $("#confirm-no").onclick = () => done(false);
    dlg.onclick = (e) => e.target === dlg && done(false);
  });
}

// ---------- charts ----------

function withTip(node, text) {
  const tip = $("#tip");
  node.addEventListener("mousemove", (e) => {
    tip.textContent = text;
    tip.hidden = false;
    tip.style.left = `${Math.min(e.clientX + 12, innerWidth - tip.offsetWidth - 8)}px`;
    tip.style.top = `${e.clientY - 36}px`;
  });
  node.addEventListener("mouseleave", () => (tip.hidden = true));
  return node;
}

const emptyChart = (text) => el("p", { class: "muted" }, text);

function hbarChart(items, unit, empty = "No data yet") {
  if (!items.length) return emptyChart(empty);
  const max = Math.max(1, ...items.map((i) => i.count));
  return el("div", { class: "hbars" }, items.map((i) =>
    withTip(el("div", { class: "hbar" },
      el("span", { class: "hbar-name", title: i.name }, i.name),
      el("div", { class: "hbar-track" }, el("div", { class: "hbar-fill", style: `width:${(i.count / max) * 100}%` })),
      el("span", { class: "hbar-val" }, fmt(i.count))), `${i.name}: ${fmt(i.count)} ${unit}`)));
}

// labels: "all" puts a value over every bar; "max" only over the tallest (for dense series)
function columnChart(items, { tip, axis = (i) => i.name, labels = "all", empty = "No data yet" } = {}) {
  if (!items.some((i) => i.count)) return emptyChart(empty);
  const max = Math.max(1, ...items.map((i) => i.count));
  const peak = items.findIndex((i) => i.count === max);
  return el("div", {},
    el("div", { class: "cols" }, items.map((i, n) =>
      withTip(el("div", { class: "col" },
        i.count && (labels === "all" || n === peak) ? el("span", { class: "col-val" }, fmt(i.count)) : null,
        el("div", { class: "col-fill", style: `height:${(i.count / max) * 100}%` })), tip(i)))),
    el("div", { class: "col-axis" }, items.map((i, n) => el("span", {}, axis(i, n)))));
}

function stackChart(parts, empty = "No data yet") {
  const total = parts.reduce((s, p) => s + p.value, 0);
  if (!total) return emptyChart(empty);
  return el("div", {},
    el("div", { class: "stack-bar" }, parts.filter((p) => p.value).map((p) =>
      withTip(el("div", { style: `flex:${p.value};background:${p.color}` }),
        `${p.name}: ${fmt(p.value)} (${Math.round((p.value / total) * 100)}%)`))),
    el("div", { class: "legend" }, parts.map((p) =>
      el("span", {}, el("i", { style: `background:${p.color}` }), p.name, " ", el("b", {}, fmt(p.value))))));
}

const kpiTile = (value, label) =>
  el("div", { class: "kpi" }, el("div", { class: "kpi-value num" }, value), el("div", { class: "kpi-label" }, label));
