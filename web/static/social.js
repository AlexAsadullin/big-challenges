"use strict";
// Social side of the reader app: feed, explore, public profiles, catalog authors, published books and communities.
// Detail pages are hash routes ("#user/12") rendered into the page panel; user.js routes to them via PAGES.

const PAGES = {
  user: renderUserPage,
  author: renderAuthorPage,
  community: renderCommunityPage,
  publication: renderPublicationPage,
  publish: renderPublishForm,
  "new-community": renderCommunityForm,
  "edit-community": renderCommunityForm,
  following: renderFollowingPage,
  followers: renderFollowersPage,
};

const social = { feedOffset: 0, explore: { tab: "communities", q: "", offset: 0 }, pageToken: 0 };

const go = (route) => { location.hash = route; };
const plural = (n, one, many = `${one}s`) => `${fmt(n)} ${n === 1 ? one : many}`;
const avatar = (name, cls = "") => el("div", { class: `avatar ${cls}` }, initials(name));
const personLink = (p) => el("a", { class: "link", href: `#user/${p.id}` }, p.name);
const muted = (...kids) => el("p", { class: "muted small" }, ...kids);
const emptyNote = (text, ...actions) => el("div", { class: "card empty-card enter" }, el("p", { class: "muted" }, text), ...actions);

function setHeading(title, eyebrow) {
  $("#page-title").textContent = title;
  $("#eyebrow").textContent = eyebrow;
}

async function renderPage(route) {
  const [base, id] = route.split("/");
  const token = ++social.pageToken;
  const panel = $('[data-panel="page"]');
  setHeading("", "Loading…");
  panel.replaceChildren(el("div", { class: "card" }, thinking(LIBRARY_PHRASES)));
  try {
    const node = await PAGES[base](id ? Number(id) : null);
    if (token === social.pageToken) panel.replaceChildren(node);
  } catch (err) {
    if (token !== social.pageToken) return;
    setHeading("Not available", "Books Advisor");
    panel.replaceChildren(el("div", { class: "alert danger" }, err.message));
  }
}

const rerender = () => renderPage(currentView());

function followButton(path, following, onChange) {
  const btn = el("button", { class: "btn sm" });
  const paint = () => {
    btn.textContent = following ? "Following" : "Follow";
    btn.className = `btn sm ${following ? "" : "primary"}`;
  };
  paint();
  btn.onclick = () => withButton(btn, async () => {
    following = (await api(`${path}/follow`, { method: following ? "DELETE" : "POST" })).is_following;
    paint();
    if (onChange) onChange(following);
  });
  return btn;
}

function stat(value, label, href) {
  const kids = [el("b", { class: "num" }, fmt(value)), el("span", {}, label)];
  return href ? el("a", { class: "stat", href }, ...kids) : el("div", { class: "stat" }, ...kids);
}

function listItem(href, name, sub, right) {
  return el("a", { class: "list-item enter", href }, avatar(name, "sm"),
    el("div", { class: "list-item-text" }, el("span", { class: "list-item-title" }, name), sub ? el("span", { class: "muted small" }, sub) : null),
    right || null);
}

// ---------- published books & posts ----------

function publicationEmbed(pub) {
  return el("a", { class: "embed", href: `#publication/${pub.id}` },
    el("div", { class: "pick-cover", style: "background:var(--c2, var(--accent))" }, (pub.title || "?")[0]),
    el("div", {},
      el("div", { class: "pick-title" }, pub.title),
      el("div", { class: "pick-meta" }, [pub.author.name, pub.genre].filter(Boolean).join(" · ")),
      pub.summary ? el("p", { class: "book-desc", style: "margin:4px 0 0" }, pub.summary) : null));
}

function publicationCard(pub) {
  return el("article", { class: "card post enter" },
    el("div", { class: "post-head" }, avatar(pub.author.name, "sm"),
      el("div", { class: "post-who" },
        el("span", {}, personLink(pub.author), " published a book"),
        el("span", { class: "muted small" }, ago(pub.created_at)))),
    publicationEmbed(pub));
}

function postCard(post, { inCommunity = false } = {}) {
  const body = el("p", { class: "post-body" }, post.body || "");
  body.hidden = !post.body;
  const head = el("div", { class: "post-head" }, avatar(inCommunity ? post.author.name : post.community.name, "sm"),
    el("div", { class: "post-who" },
      inCommunity ? el("span", {}, personLink(post.author))
        : el("a", { class: "post-title", href: `#community/${post.community.id}` }, post.community.name),
      el("span", { class: "muted small" }, inCommunity ? "" : ["by ", personLink(post.author), " · "],
        ago(post.created_at), post.updated_at ? " · edited" : "")));
  const card = el("article", { class: "card post enter" }, head, body,
    post.publication ? publicationEmbed(post.publication) : null);
  if (post.can_edit) {
    const edit = el("button", { class: "btn sm ghost" }, "Edit");
    const del = el("button", { class: "btn sm ghost" }, "Delete");
    head.append(el("div", { class: "post-actions" }, edit, del));
    del.onclick = async () => {
      if (!(await confirmDialog("Delete this post?", "It disappears from the community and everyone's feed.", "Delete"))) return;
      withButton(del, async () => { await api(`/api/posts/${post.id}`, { method: "DELETE" }); card.remove(); toast("Post deleted"); });
    };
    edit.onclick = () => {
      const area = el("textarea", { class: "textarea", maxlength: 5000 });
      area.value = post.body || "";
      const save = el("button", { class: "btn sm primary" }, "Save");
      const editor = el("div", { class: "form-stack", style: "margin-top:10px" }, area,
        el("div", { class: "row end" }, el("button", { class: "btn sm ghost", onclick: () => { editor.remove(); body.hidden = !post.body; } }, "Cancel"), save));
      save.onclick = () => withButton(save, async () => {
        const updated = await api(`/api/posts/${post.id}`, { method: "PUT", body: { body: area.value } });
        card.replaceWith(postCard(updated, { inCommunity }));
      });
      body.hidden = true;
      body.after(editor);
      area.focus();
    };
  }
  return card;
}

// ---------- feed ----------

async function loadFeed(more = false) {
  const body = $("#feed-body");
  const btn = $("#feed-more");
  if (!more) {
    social.feedOffset = 0;
    body.replaceChildren(el("div", { class: "card" }, thinking(LIBRARY_PHRASES)));
  }
  btn.disabled = true;
  try {
    const res = await api(`/api/feed?offset=${social.feedOffset}&limit=20`);
    const cards = res.items.map((item) => (item.kind === "post" ? postCard(item.post) : publicationCard(item.publication)));
    if (more) body.append(...cards);
    else body.replaceChildren(...(cards.length ? cards : [emptyNote(
      "Your feed is empty. Join communities and follow readers to see their posts and new books here.",
      el("div", { class: "row", style: "margin-top:12px" },
        el("a", { class: "btn primary", href: "#explore" }, "Explore communities"),
        el("button", { class: "btn", onclick: () => showView("chat") }, "Ask the advisor")))]));
    social.feedOffset = res.next_offset ?? social.feedOffset;
    btn.hidden = res.next_offset == null;
  } catch (err) {
    if (!more) body.replaceChildren(el("div", { class: "alert danger" }, err.message));
    else toast(err.message);
  } finally {
    btn.disabled = false;
  }
}
$("#feed-more").onclick = () => loadFeed(true);

// ---------- explore ----------

const EXPLORE = {
  communities: {
    placeholder: "Search communities",
    url: (q, offset) => `/api/communities?q=${encodeURIComponent(q)}&offset=${offset}&limit=30`,
    items: (res) => res,
    row: (c) => listItem(`#community/${c.id}`, c.name, c.description || `by ${c.owner.name}`,
      el("span", { class: "badge neutral" }, c.role ? roleLabel(c.role) : plural(c.members, "member"))),
    empty: "No communities yet. Start the first one!",
  },
  authors: {
    placeholder: "Search authors",
    url: (q, offset) => `/api/authors?q=${encodeURIComponent(q)}&offset=${offset}&limit=30`,
    items: (res) => res.authors,
    row: (a) => listItem(`#author/${a.id}`, a.name, `${plural(a.books, "book")} in the library`,
      a.is_following ? el("span", { class: "badge ok" }, "Following") : null),
    empty: "No authors match.",
  },
  people: {
    placeholder: "Search readers by name",
    url: (q) => `/api/users?q=${encodeURIComponent(q)}&limit=30`,
    items: (res) => res,
    row: (u) => listItem(`#user/${u.id}`, u.name, u.bio || plural(u.followers, "follower"),
      u.is_following ? el("span", { class: "badge ok" }, "Following") : null),
    empty: "No readers match.",
  },
};

const roleLabel = (role) => ({ owner: "Creator", admin: "Admin", member: "Joined" })[role] || role;

async function loadExplore(more = false) {
  const { tab, q } = social.explore;
  const cfg = EXPLORE[tab];
  const list = $("#explore-list");
  if (!more) social.explore.offset = 0;
  $("#explore-more").disabled = true;
  try {
    const items = cfg.items(await api(cfg.url(q, social.explore.offset)));
    if (tab !== social.explore.tab || q !== social.explore.q) return; // a newer search started
    const rows = items.map(cfg.row);
    if (more) $(".list", list).append(...rows);
    else list.replaceChildren(rows.length ? el("div", { class: "card list" }, rows) : emptyNote(cfg.empty));
    social.explore.offset += items.length;
    $("#explore-more").hidden = tab === "people" || items.length < 30;
  } catch (err) {
    toast(err.message);
  } finally {
    $("#explore-more").disabled = false;
  }
}

function setExploreTab(tab) {
  social.explore.tab = tab;
  $$("[data-explore]").forEach((b) => b.setAttribute("aria-selected", b.dataset.explore === tab));
  $("#explore-q").placeholder = EXPLORE[tab].placeholder;
  $("#explore-create").hidden = tab !== "communities";
  loadExplore();
}

$$("[data-explore]").forEach((b) => (b.onclick = () => setExploreTab(b.dataset.explore)));
let exploreTimer;
$("#explore-q").addEventListener("input", (e) => {
  clearTimeout(exploreTimer);
  exploreTimer = setTimeout(() => { social.explore.q = e.target.value.trim(); loadExplore(); }, 250);
});
$("#explore-more").onclick = () => loadExplore(true);

// ---------- my profile (top of the Profile tab) ----------

async function renderMySocial() {
  const box = $("#my-social");
  if (!state.me.id) return;
  try {
    const [me, books, managed] = await Promise.all([
      api(`/api/users/${state.me.id}`),
      api(`/api/users/${state.me.id}/publications?limit=50`),
      api("/api/communities?scope=managed&limit=100"),
    ]);
    box.replaceChildren(
      el("div", { class: "card" },
        el("div", { class: "stats" },
          stat(me.followers, "followers", `#followers/${me.id}`),
          stat(me.following, "following", `#following/${me.id}`),
          stat(me.publications, "books", `#user/${me.id}`)),
        el("a", { class: "btn", style: "margin-top:14px;width:100%", href: `#user/${me.id}` }, "View my public profile")),
      el("div", { class: "card" },
        el("div", { class: "card-head" }, el("h2", {}, "My books"), el("a", { class: "btn sm primary", href: "#publish" }, "Publish a book")),
        books.length ? el("div", { class: "embeds" }, books.map(publicationEmbed))
          : muted("Books you write appear on your profile and in your followers' feeds.")),
      el("div", { class: "card" },
        el("div", { class: "card-head" }, el("h2", {}, "My communities"), el("a", { class: "btn sm", href: "#new-community" }, "Create")),
        managed.length ? el("div", { class: "list" }, managed.map((c) =>
          listItem(`#community/${c.id}`, c.name, plural(c.members, "member"), el("span", { class: "badge neutral" }, roleLabel(c.role)))))
          : muted("Communities you create or help run appear here.")));
  } catch (err) {
    box.replaceChildren(el("div", { class: "alert danger", style: "margin-bottom:16px" }, err.message));
  }
}

function resetSocial() {
  $("#my-social").replaceChildren();
  $("#feed-body").replaceChildren();
  $("#explore-list").replaceChildren();
  social.explore = { tab: "communities", q: "", offset: 0 };
  $("#explore-q").value = "";
}

// ---------- reader & author pages ----------

async function renderUserPage(id) {
  const [user, books] = await Promise.all([api(`/api/users/${id}`), api(`/api/users/${id}/publications?limit=50`)]);
  setHeading(user.name, user.is_me ? "Your public profile" : "Reader");
  const followers = stat(user.followers, "followers", `#followers/${id}`);
  const action = user.is_me
    ? el("a", { class: "btn sm", href: "#profile" }, "Edit profile")
    : followButton(`/api/users/${id}`, user.is_following, (on) => {
      user.followers += on ? 1 : -1;
      followers.replaceWith(stat(user.followers, "followers", `#followers/${id}`));
    });
  return el("div", {},
    el("div", { class: "card" },
      el("div", { class: "entity-head" }, avatar(user.name, "lg"),
        el("div", { class: "entity-text" }, el("h2", {}, user.name), muted(`Joined ${ago(user.joined)}`)), action),
      user.bio ? el("p", { class: "bio" }, user.bio) : null,
      el("div", { class: "stats" }, followers, stat(user.following, "following", `#following/${id}`), stat(user.publications, "books")),
      user.manages.length ? el("div", { class: "chips", style: "margin-top:14px" }, user.manages.map((c) =>
        el("a", { class: "chip", href: `#community/${c.id}` }, c.name, " · ", roleLabel(c.role)))) : null),
    el("div", { class: "card" },
      el("div", { class: "card-head" }, el("h2", {}, user.is_me ? "My books" : "Books"),
        user.is_me ? el("a", { class: "btn sm primary", href: "#publish" }, "Publish a book") : null),
      books.length ? el("div", { class: "embeds" }, books.map(publicationEmbed))
        : muted(user.is_me ? "You haven't published anything yet." : "No published books yet.")));
}

async function renderAuthorPage(id) {
  const author = await api(`/api/authors/${id}`);
  setHeading(author.name, "Author");
  const followers = stat(author.followers, "followers");
  return el("div", {},
    el("div", { class: "card" },
      el("div", { class: "entity-head" }, avatar(author.name, "lg"),
        el("div", { class: "entity-text" }, el("h2", {}, author.name), muted("From the library catalog")),
        followButton(`/api/authors/${id}`, author.is_following, (on) => {
          author.followers += on ? 1 : -1;
          followers.replaceWith(stat(author.followers, "followers"));
        })),
      el("div", { class: "stats" }, stat(author.books, "books"), followers)),
    el("div", { class: "card" }, el("h2", { style: "margin-bottom:12px" }, "Books in the library"),
      author.book_list.length ? bookTiles(author.book_list) : muted("No books in the library.")));
}

async function peopleList(people, empty) {
  return el("div", { class: "card list" }, people.length ? people.map((p) => listItem(`#user/${p.id}`, p.name)) : muted(empty));
}

async function renderFollowingPage(id) {
  const [user, following] = await Promise.all([api(`/api/users/${id}`), api(`/api/users/${id}/following`)]);
  setHeading("Following", user.name);
  return el("div", {},
    el("h2", { class: "list-heading" }, "Readers"), await peopleList(following.users, "Not following any readers."),
    el("h2", { class: "list-heading" }, "Authors"),
    el("div", { class: "card list" }, following.authors.length
      ? following.authors.map((a) => listItem(`#author/${a.id}`, a.name, "Author")) : muted("Not following any authors.")));
}

async function renderFollowersPage(id) {
  const [user, followers] = await Promise.all([api(`/api/users/${id}`), api(`/api/users/${id}/followers`)]);
  setHeading("Followers", user.name);
  return peopleList(followers, "No followers yet.");
}

// ---------- published book page & form ----------

async function renderPublicationPage(id) {
  const pub = await api(`/api/publications/${id}`);
  setHeading(pub.title, "Book");
  const reposted = el("div", { class: "chips" }, pub.reposted_to.map((c) => el("a", { class: "chip", href: `#community/${c.id}` }, c.name)));
  const page = el("div", {},
    el("div", { class: "card" },
      el("div", { class: "entity-head" },
        el("div", { class: "pick-cover", style: "background:var(--c2, var(--accent))" }, (pub.title || "?")[0]),
        el("div", { class: "entity-text" }, el("h2", {}, pub.title),
          el("p", { class: "muted small" }, "by ", personLink(pub.author), ` · ${ago(pub.created_at)}`, pub.updated_at ? " · edited" : ""))),
      pub.genre ? el("div", { class: "chips", style: "margin-bottom:12px" }, pub.genre.split(",").map((g) => el("span", { class: "badge neutral" }, g.trim()))) : null,
      el("p", { class: "bio" }, pub.description),
      pub.link ? el("a", { class: "btn sm", style: "margin-top:12px", href: pub.link, target: "_blank", rel: "noopener" }, "Open link ↗") : null,
      pub.reposted_to.length ? el("div", { style: "margin-top:14px" }, muted("Reposted to"), reposted) : null));
  if (pub.content) page.append(el("div", { class: "card" }, el("h2", { style: "margin-bottom:12px" }, "Read"), el("div", { class: "book-text" }, pub.content)));
  if (pub.is_mine) page.append(await repostCard(pub, reposted), el("div", { class: "card row" },
    el("a", { class: "btn", href: `#publish/${pub.id}` }, "Edit"),
    el("button", {
      class: "btn danger", onclick: async (e) => {
        if (!(await confirmDialog("Delete this book?", "It's removed from your profile, along with its reposts in communities.", "Delete"))) return;
        withButton(e.target, async () => { await api(`/api/publications/${pub.id}`, { method: "DELETE" }); toast("Book deleted"); go(`user/${state.me.id}`); });
      },
    }, "Delete")));
  return page;
}

async function repostCard(pub, reposted) {
  const managed = (await api("/api/communities?scope=managed&limit=100"))
    .filter((c) => !pub.reposted_to.some((r) => r.id === c.id));
  const card = el("div", { class: "card form-stack" }, el("h2", {}, "Repost to a community"));
  if (!managed.length) {
    card.append(muted(pub.reposted_to.length ? "It's in every community you manage." : "You can repost your books to communities you created or help run."),
      el("a", { class: "btn", href: "#new-community" }, "Create a community"));
    return card;
  }
  const select = el("select", { class: "select" }, managed.map((c) => el("option", { value: c.id }, c.name)));
  const comment = el("textarea", { class: "textarea", maxlength: 5000, rows: 2, style: "min-height:70px", placeholder: "Add a comment (optional)" });
  const btn = el("button", { class: "btn primary" }, "Repost");
  btn.onclick = () => withButton(btn, async () => {
    const post = await api(`/api/communities/${select.value}/posts`, { method: "POST", body: { publication_id: pub.id, body: comment.value } });
    toast(`Reposted to ${post.community.name}`);
    rerender();
  });
  card.append(select, comment, btn);
  return card;
}

async function renderPublishForm(id) {
  const pub = id ? await api(`/api/publications/${id}`) : {};
  setHeading(id ? "Edit book" : "Publish a book", "Your books");
  const field = (key, label, input, hint) => el("div", { class: "field" },
    el("label", { for: `pub-${key}` }, label, hint ? el("span", { class: "hint" }, ` ${hint}`) : null), input);
  const inputs = {
    title: el("input", { class: "input", id: "pub-title", required: true, maxlength: 300 }),
    genre: el("input", { class: "input", id: "pub-genre", maxlength: 200, placeholder: "Fantasy, Short stories" }),
    description: el("textarea", { class: "textarea", id: "pub-description", required: true, maxlength: 5000, placeholder: "What is it about?" }),
    content: el("textarea", { class: "textarea book-input", id: "pub-content", maxlength: 500000, placeholder: "Paste or write the text of your book" }),
    link: el("input", { class: "input", id: "pub-link", type: "url", inputmode: "url", maxlength: 500, placeholder: "https://…" }),
  };
  for (const [key, input] of Object.entries(inputs)) input.value = pub[key] || "";
  const submit = el("button", { class: "btn primary" }, id ? "Save changes" : "Publish");
  const form = el("form", { class: "card form-stack" },
    field("title", "Title", inputs.title),
    field("genre", "Genre", inputs.genre, "optional"),
    field("description", "Description", inputs.description),
    field("content", "Text", inputs.content, "optional"),
    field("link", "Link", inputs.link, "optional, e.g. where to buy or read it"),
    submit);
  form.onsubmit = (e) => {
    e.preventDefault();
    withButton(submit, async () => {
      const body = Object.fromEntries(Object.entries(inputs).map(([k, v]) => [k, v.value]));
      const saved = await api(id ? `/api/publications/${id}` : "/api/publications", { method: id ? "PUT" : "POST", body });
      toast(id ? "Saved" : `"${saved.title}" is published`);
      go(`publication/${saved.id}`);
    });
  };
  return el("div", {}, el("p", { class: "section-note" }, "Your book appears on your profile and in your followers' feeds. You can repost it to communities you manage."), form);
}

// ---------- communities ----------

async function renderCommunityForm(id) {
  const community = id ? await api(`/api/communities/${id}`) : {};
  setHeading(id ? "Edit community" : "New community", "Communities");
  const name = el("input", { class: "input", id: "c-name", required: true, minlength: 3, maxlength: 80, placeholder: "Sci-fi book club" });
  const description = el("textarea", { class: "textarea", id: "c-description", maxlength: 2000, placeholder: "What is this community about?" });
  name.value = community.name || "";
  description.value = community.description || "";
  const submit = el("button", { class: "btn primary" }, id ? "Save" : "Create community");
  const form = el("form", { class: "card form-stack" },
    el("div", { class: "field" }, el("label", { for: "c-name" }, "Name"), name),
    el("div", { class: "field" }, el("label", { for: "c-description" }, "Description ", el("span", { class: "hint" }, "optional")), description),
    submit);
  form.onsubmit = (e) => {
    e.preventDefault();
    withButton(submit, async () => {
      const saved = await api(id ? `/api/communities/${id}` : "/api/communities", {
        method: id ? "PUT" : "POST", body: { name: name.value, description: description.value },
      });
      toast(id ? "Saved" : `${saved.name} is ready`);
      go(`community/${saved.id}`);
    });
  };
  return el("div", {},
    id ? null : el("p", { class: "section-note" }, "You'll be its creator: you can appoint admins, and only you and your admins can post."),
    form);
}

async function renderCommunityPage(id) {
  const [c, posts] = await Promise.all([api(`/api/communities/${id}`), api(`/api/communities/${id}/posts?limit=20`)]);
  setHeading(c.name, "Community");
  const actions = el("div", { class: "row", style: "margin-top:14px" });
  if (c.role === "owner") {
    actions.append(
      el("a", { class: "btn sm", href: `#edit-community/${id}` }, "Edit"),
      el("button", {
        class: "btn sm danger", onclick: async (e) => {
          if (!(await confirmDialog(`Delete ${c.name}?`, "The community and all its posts are deleted for everyone. It can't be undone.", "Delete"))) return;
          withButton(e.target, async () => { await api(`/api/communities/${id}`, { method: "DELETE" }); toast("Community deleted"); go("explore"); });
        },
      }, "Delete"));
  } else {
    const joined = Boolean(c.role);
    actions.append(el("button", {
      class: `btn sm ${joined ? "" : "primary"}`,
      onclick: (e) => withButton(e.target, async () => {
        await api(`/api/communities/${id}/join`, { method: joined ? "DELETE" : "POST" });
        toast(joined ? `You left ${c.name}` : `You joined ${c.name}`);
        rerender();
      }),
    }, joined ? "Leave" : "Join"));
  }
  const page = el("div", {},
    el("div", { class: "card" },
      el("div", { class: "entity-head" }, avatar(c.name, "lg"),
        el("div", { class: "entity-text" }, el("h2", {}, c.name), el("p", { class: "muted small" }, "Created by ", personLink(c.owner))),
        c.role ? el("span", { class: "badge neutral" }, roleLabel(c.role)) : null),
      c.description ? el("p", { class: "bio" }, c.description) : null,
      el("div", { class: "stats" }, stat(c.members, "members"), stat(c.posts, "posts"), stat(c.admins.length + 1, "managers")),
      actions));
  if (c.role === "owner" || c.role === "admin") page.append(adminsCard(c));
  if (c.can_post) page.append(await composerCard(c));

  const list = el("div", {}, posts.map((p) => postCard(p, { inCommunity: true })));
  if (!posts.length) list.append(emptyNote(c.can_post ? "No posts yet. Write the first one above." : "No posts yet."));
  page.append(list);
  if (posts.length === 20) {
    let offset = 20;
    const more = el("button", { class: "btn more-btn" }, "Show more");
    more.onclick = () => withButton(more, async () => {
      const next = await api(`/api/communities/${id}/posts?offset=${offset}&limit=20`);
      offset += next.length;
      list.append(...next.map((p) => postCard(p, { inCommunity: true })));
      if (next.length < 20) more.remove();
    });
    page.append(more);
  }
  return page;
}

function adminsCard(c) {
  const isOwner = c.role === "owner";
  const rows = [listItem(`#user/${c.owner.id}`, c.owner.name, "Creator")];
  for (const admin of c.admins) {
    const mine = admin.id === state.me.id;
    const remove = isOwner || mine ? el("button", {
      class: "btn sm ghost", onclick: (e) => {
        e.preventDefault();
        withButton(e.currentTarget, async () => {
          await api(`/api/communities/${c.id}/admins/${admin.id}`, { method: "DELETE" });
          toast(mine ? "You stepped down as admin" : `${admin.name} is no longer an admin`);
          rerender();
        });
      },
    }, mine ? "Step down" : "Remove") : null;
    rows.push(listItem(`#user/${admin.id}`, admin.name, "Admin", remove));
  }
  const card = el("div", { class: "card" }, el("h2", { style: "margin-bottom:10px" }, "Admins"),
    muted("Only the creator and admins can post and edit posts here."), el("div", { class: "list", style: "margin-top:10px" }, rows));
  if (isOwner) {
    const picker = el("div", { class: "list", style: "margin-top:10px" });
    const open = el("button", { class: "btn sm", style: "margin-top:12px" }, "Add an admin");
    open.onclick = () => withButton(open, async () => {
      const members = (await api(`/api/communities/${c.id}/members`)).filter((m) => m.role === "member");
      open.remove();
      picker.replaceChildren(...(members.length ? members.map((m) => listItem(`#user/${m.id}`, m.name, "Member", el("button", {
        class: "btn sm primary", onclick: (e) => {
          e.preventDefault();
          withButton(e.currentTarget, async () => {
            await api(`/api/communities/${c.id}/admins`, { method: "POST", body: { account_id: m.id } });
            toast(`${m.name} is now an admin`);
            rerender();
          });
        },
      }, "Make admin"))) : [muted("Admins are picked from members. Nobody else has joined yet.")]));
    });
    card.append(open, picker);
  }
  return card;
}

async function composerCard(c) {
  const books = await api(`/api/users/${state.me.id}/publications?limit=100`);
  const text = el("textarea", { class: "textarea", maxlength: 5000, rows: 3, style: "min-height:80px", placeholder: `Write to ${c.name}…` });
  const book = el("select", { class: "select" }, el("option", { value: "" }, "No book attached"),
    books.map((b) => el("option", { value: b.id }, `Repost: ${b.title}`)));
  const submit = el("button", { class: "btn primary" }, "Post");
  submit.onclick = () => withButton(submit, async () => {
    await api(`/api/communities/${c.id}/posts`, { method: "POST", body: { body: text.value, publication_id: book.value ? Number(book.value) : null } });
    toast("Posted");
    rerender();
  });
  return el("div", { class: "card form-stack" }, el("h2", {}, "New post"), text, books.length ? book : null,
    el("div", { class: "row end" }, submit));
}
