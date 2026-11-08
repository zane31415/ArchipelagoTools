"use strict";

// ---- routing. Server mode: /r/<slug> or /r/<slug>/admin/<token>.
// Static mode (GitHub Pages etc., see static.js): room.html?r=<slug>
const STATIC = typeof SDTStatic !== "undefined";
const parts = location.pathname.split("/").filter(Boolean);
const SLUG = STATIC ? (new URLSearchParams(location.search).get("r") || "") : decodeURIComponent(parts[1] || "");
const TOKEN = !STATIC && parts[2] === "admin" ? decodeURIComponent(parts[3] || "") : null;
const headers = TOKEN ? {"x-admin-token": TOKEN} : {};

let VIEW = null;
const openDetails = new Set();  // keep expanded chains open across re-renders

// ---- tiny DOM helper (never uses innerHTML for data)
function h(tag, attrs, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v == null || v === false) continue;
    if (k === "class") el.className = v;
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (k in el && k !== "list") el[k] = v;
    else el.setAttribute(k, v);
  }
  for (const kid of kids.flat()) if (kid != null && kid !== false) el.append(kid instanceof Node ? kid : String(kid));
  return el;
}
const $ = id => document.getElementById(id);

function ago(epoch) {
  if (!epoch) return "never";
  const s = Math.max(0, Date.now() / 1000 - epoch);
  if (s < 90) return "just now";
  if (s < 5400) return Math.round(s / 60) + "m ago";
  if (s < 172800) return Math.round(s / 3600) + "h ago";
  return Math.round(s / 86400) + "d ago";
}

const UNCERTAIN_TITLE = "Some locations have rules too large to export exactly (\"N of a group\" style). " +
  "They aren't confirmed in logic, but might be.";
const STATE_LABEL ={bk: "BK", low: "Nearly BK", ok: "Playing", goal: "Goal", done: "All checked"};
const STATE_ORDER = {bk: 0, low: 1, ok: 2, done: 3, goal: 4};

// ---- data loading
async function load(force = false) {
  let data;
  if (STATIC) {
    data = await SDTStatic.getState(SLUG, force);
    if (data.cooldown) showBanner("Refreshed very recently; try again in a minute.", false);
  } else {
    try {
      const r = await fetch(`/api/r/${encodeURIComponent(SLUG)}/state`, {headers});
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      data = await r.json();
    } catch (e) {
      showBanner(`Could not reach the tracker server (${e.message}).`, true);
      return;
    }
  }
  $("title").textContent = data.title;
  document.title = data.title + " · Sphere Dependency Tracker";
  $("admin-badge").hidden = !data.admin;
  if (TOKEN && !data.admin) showBanner("Admin token not accepted; showing the public view.", true);

  if (data.phase !== "ready" || !data.view) {
    const msg = data.phase === "error" ? `Setup failed: ${data.error}`
      : data.phase === "ready" ? (data.error || "Fetching the latest data from archipelago.gg…")
      : "Rebuilding the multiworld from the spoiler and yamls. This takes about half a minute on startup…";
    showBanner(msg, data.phase === "error");
    $("main").hidden = true;
    if (data.phase !== "error") setTimeout(load, 3000);
    return;
  }
  if (data.error) showBanner(data.error, true); else hideBanner();
  VIEW = data.view;
  $("main").hidden = false;
  render();
}

function showBanner(text, isError) {
  const b = $("banner");
  b.textContent = text;
  b.className = "banner" + (isError ? " error" : "");
  b.hidden = false;
}
function hideBanner() { $("banner").hidden = true; }

// ---- rendering
function render() {
  const v = VIEW;
  $("subtitle").textContent = `AP ${v.room.ap_version} · seed ${v.room.seed} · ${v.players.length} players`;
  $("refreshed").textContent = "Updated " + ago(v.room.last_refresh);
  indexHints(v);
  renderSummary(v);
  renderPlayers(v);
  renderHintFilters(v);
  renderHints(v);
  $("spheres-section").hidden = !v.spheres.length;
  renderSpheres(v);
}

function renderSummary(v) {
  const count = s => v.players.filter(p => p.state === s).length;
  const checks = v.players.reduce((a, p) => a + p.checks, 0);
  const total = v.players.reduce((a, p) => a + p.total, 0);
  const frontier = v.spheres.find(s => s.checked < s.total);
  const openHints = v.hints.filter(x => !x.found).length;
  const stat = (n, label) => h("div", {class: "stat"}, h("b", {}, n), h("span", {}, label));
  $("summary").replaceChildren(...[
    stat(`${checks}/${total}`, `checks (${Math.round(100 * checks / Math.max(total, 1))}%)`),
    stat(count("bk"), "BK"),
    stat(count("low"), "nearly BK"),
    stat(count("ok"), "playing"),
    stat(count("goal") + count("done"), "finished"),
    v.spheres.length ? stat(frontier ? frontier.sphere : "–", "earliest open sphere") : null,
    stat(openHints, "unfound hints"),
  ].filter(Boolean));
}

function holderLine(hd) {
  // a location that holds a needed item, possibly with its own needs (recursive chain)
  const status = hd.checked ? h("span", {class: "yes"}, "checked")
    : hd.in_logic ? h("span", {class: "yes"}, `in logic for ${hd.finder_name}`)
    : hd.cycle ? h("span", {class: "cyc"}, "circular dependency")
    : h("span", {class: "no"}, `not in logic for ${hd.finder_name}`);
  const line = h("div", {class: "holder"}, "at ", h("b", {}, hd.location), ` (${hd.finder_name}) · `, status,
    hd.hinted ? " · hinted" : "");
  if (hd.needs) line.append(renderNeeds(hd.needs, hd.finder));
  return line;
}

// slot = the player whose logic this is (needed items are items for that slot).
function renderNeeds(n, slot) {
  if (n.kind === "in_logic") return h("div", {class: "holder"}, h("span", {class: "yes"}, "In logic now"));
  if (n.kind === "unknown") return h("div", {class: "unknown"}, "Couldn't work out the requirements (logic quirk).");
  const kindText = (n.kind === "any" ? (n.items.length > 1 ? "Any one of:" : "Needs:") : "All of:") +
    (n.approx ? " (one known option; this rule has others)" : "");
  const ul = h("ul", {class: "needs"});
  for (const it of n.items) {
    const label = it.count > 1 ? `${it.name} ×${it.count}` : it.name;
    const hinted = hintsFor(slot, it.name);
    const li = h("li", {}, h("span", {class: "need-item"}, label), hinted.length ? hintedTag() : null);
    if (it.holders && it.holders.length) {
      for (const hd of it.holders) li.append(holderLine(hd));
    } else if (hinted.length) {
      // Public view without a resolved holder: point at the hint itself.
      for (const x of hinted) li.append(h("div", {class: "holder"}, "hinted at ", h("b", {}, x.location),
        ` (${x.finder_name}) · `, x.in_logic ? h("span", {class: "yes"}, `in logic for ${x.finder_name}`)
                                             : h("span", {class: "no"}, `not in logic for ${x.finder_name}`)));
    } else if (!VIEW.admin) li.append(h("div", {class: "unknown"}, "location not hinted"));
    if (it.more_holders) li.append(h("div", {class: "unknown"}, `+${it.more_holders} more cop${it.more_holders > 1 ? "ies" : "y"} elsewhere`));
    ul.append(li);
  }
  return h("div", {}, h("span", {class: "kind"}, kindText), ul);
}

// ---- players table: column definitions, sorting, filtering, visibility

function dur(sec) {
  sec = Math.max(0, sec);
  if (sec < 3600) return Math.max(1, Math.round(sec / 60)) + "m";
  if (sec < 86400) { const hr = Math.floor(sec / 3600), m = Math.round((sec % 3600) / 60); return m ? `${hr}h ${m}m` : `${hr}h`; }
  const d = Math.floor(sec / 86400), hr = Math.round((sec % 86400) / 3600);
  return hr ? `${d}d ${hr}h` : `${d}d`;
}
const now = () => Date.now() / 1000;

// Times come from our own polling, which only runs while someone has the page open. A change is
// known to lie between the poll before it ("from") and the poll that saw it ("at"); if that window
// is wide (nobody was watching), show it as a range instead of pretending to know.
function windowWide(from, at) {
  return from && at - from > 1.5 * ((VIEW && VIEW.room.poll_seconds) || 300);
}
function agoRange(from, at) {
  return windowWide(from, at) ? `${dur(now() - at)}–${dur(now() - from)} ago` : ago(at);
}

function bkTimer(p) {
  if (p.state === "bk") {
    if (!p.bk_changed_at) return {text: `for ≥${dur(now() - p.tracking_since)}`};
    return {text: windowWide(p.bk_changed_from, p.bk_changed_at)
      ? `for ${dur(now() - p.bk_changed_at)}–${dur(now() - p.bk_changed_from)}`
      : `for ${dur(now() - p.bk_changed_at)}`};
  }
  if ((p.state === "ok" || p.state === "low") && p.bk_changed_at)
    return {text: `un-BK'd ${agoRange(p.bk_changed_from, p.bk_changed_at)}`};
  return null;
}

function lastReceived(p) {
  if (p.last_received) return {text: agoRange(p.last_received_from, p.last_received), v: p.last_received};
  if (!p.tracking_since) return {text: "–", v: 0};
  return {text: `>${dur(now() - p.tracking_since)} ago`, v: p.tracking_since - 1e9};
}

const COLUMNS = [
  {key: "player", label: "Player", sort: p => p.name.toLowerCase(),
   cell: p => h("td", {}, h("div", {class: "pname"}, p.name))},
  {key: "game", label: "Game", sort: p => p.game.toLowerCase(),
   cell: p => h("td", {class: "pgame"}, p.game)},
  {key: "status", label: "Status",
   title: "BK = nothing in logic. BK / un-BK times are measured by this tracker's polling.",
   // BK first (longest BK on top), then nearly BK, playing, finished
   sort: p => [STATE_ORDER[p.state], p.state === "bk" ? (p.bk_changed_at || p.tracking_since || 0) : 0],
   cell: p => {
     const t = bkTimer(p);
     const maybe = p.state === "bk" && p.uncertain > 0;
     return h("td", {class: "status-cell"},
       h("span", {class: "pill " + p.state, title: maybe ? UNCERTAIN_TITLE : null},
         STATE_LABEL[p.state] + (maybe ? "?" : "")),
       t ? h("div", {class: "timer"}, t.text) : null);
   }},
  {key: "checks", label: "Checks", sort: p => p.checks / Math.max(p.total, 1),
   cell: p => h("td", {class: "num"}, `${p.checks}/${p.total}`,
     h("div", {class: "bar"}, h("div", {style: `width:${100 * p.checks / Math.max(p.total, 1)}%`})))},
  {key: "in_logic", label: "In logic", title: "Unchecked locations currently in logic", sort: p => p.in_logic,
   cell: p => h("td", {class: "num"}, p.in_logic,
     p.uncertain ? h("span", {class: "maybe", title: UNCERTAIN_TITLE}, ` +${p.uncertain}?`) : null)},
  {key: "sphere", label: "Sphere", title: "Earliest playthrough sphere this player still has open",
   sort: p => p.frontier_sphere ?? 999, cell: p => h("td", {class: "num"}, p.frontier_sphere ?? "–")},
  {key: "last_active", label: "Last active", title: "Last check sent (from the room tracker)",
   sort: p => -(p.last_activity || 0), cell: p => h("td", {class: "num"}, ago(p.last_activity))},
  {key: "last_received", label: "Last received",
   title: "When this tracker last saw the player's received-item count go up (accurate to one poll).",
   sort: p => -lastReceived(p).v, cell: p => h("td", {class: "num"}, lastReceived(p).text)},
  {key: "unlocks", label: "What would unblock them", sort: null,
   cell: p => h("td", {}, p.unlocks.length
     ? p.unlocks.map(u => h("div", {class: "unlock"},
         h("span", {class: "need-item"}, u.name), " ", h("span", {class: "gain"}, `+${u.gain}`),
         hintsFor(p.slot, u.name).length ? hintedTag() : null,
         ...(u.holders || []).map(holderLine)))
     : (p.state === "bk" ? h("span", {class: "unknown"}, "needs several items at once") : ""))},
  {key: "hints", label: "Hints in their world",
   title: "Unfound hints whose location is in this player's game. In-logic ones first.",
   sort: p => -slotHints(p.slot).length,
   cell: p => {
     const list = slotHints(p.slot);
     return h("td", {class: "slot-hints"}, list.length ? list.map(slotHint) : h("span", {class: "muted small"}, "–"));
   }},
];

function slotHints(slot) {
  return VIEW.hints.filter(x => x.finder === slot && !x.found)
    .sort((a, b) => (b.in_logic - a.in_logic) || a.item.localeCompare(b.item));
}

// Compact hint for the players table: item -> receiver + location on the left, needs on the right.
function slotHint(x) {
  const info = h("div", {class: "hint-info"},
    h("div", {}, h("span", {class: "hint-item"}, x.item), ` → ${x.receiver_name}`,
      x.status === "priority" ? h("span", {class: "pill priority sh-pill"}, "priority") : null),
    h("div", {class: "hint-loc small"}, x.location));
  return h("div", {class: "slot-hint"}, info, needsPanel(x, "p:"));
}
const ALL_COLS = COLUMNS.map(c => c.key);
const SHOT_COLS = ["player", "status", "in_logic", "last_active"];
const STATE_FILTERS = [["bk", "BK"], ["low", "Nearly BK"], ["ok", "Playing"], ["fin", "Finished"]];

function loadPrefs() {
  try { return JSON.parse(localStorage.getItem("sdt-prefs") || "{}"); } catch { return {}; }
}
const PREFS = Object.assign({
  cols: [...ALL_COLS], sort: {key: "status", dir: 1}, states: ["bk", "low", "ok", "fin"],
}, loadPrefs());
// Columns added after someone saved their prefs start visible (unless they're in screenshot view).
{
  const known = PREFS.known || ALL_COLS.filter(k => k !== "hints");
  const isShot = SHOT_COLS.length === PREFS.cols.length && SHOT_COLS.every(k => PREFS.cols.includes(k));
  const added = ALL_COLS.filter(k => !known.includes(k));
  if (added.length && !isShot) PREFS.cols = ALL_COLS.filter(k => PREFS.cols.includes(k) || added.includes(k));
  PREFS.known = [...ALL_COLS];
}
function savePrefs() { try { localStorage.setItem("sdt-prefs", JSON.stringify(PREFS)); } catch { /* private mode */ } }

function cmp(a, b) {
  if (Array.isArray(a)) { for (let i = 0; i < a.length; i++) { const c = cmp(a[i], b[i]); if (c) return c; } return 0; }
  return a < b ? -1 : a > b ? 1 : 0;
}

function renderPlayerControls() {
  $("col-toggles").replaceChildren(...COLUMNS.map(c => h("label", {},
    h("input", {type: "checkbox", checked: PREFS.cols.includes(c.key), onchange: e => {
      PREFS.cols = e.target.checked ? ALL_COLS.filter(k => k === c.key || PREFS.cols.includes(k))
                                    : PREFS.cols.filter(k => k !== c.key);
      savePrefs(); renderPlayers(VIEW);
    }}), " ", c.label)));
  $("state-filters").replaceChildren(...STATE_FILTERS.map(([k, label]) => h("label", {class: "chip"},
    h("input", {type: "checkbox", checked: PREFS.states.includes(k), onchange: e => {
      PREFS.states = e.target.checked ? [...PREFS.states, k] : PREFS.states.filter(s => s !== k);
      savePrefs(); renderPlayers(VIEW);
    }}), " ", label)));
  const isShot = SHOT_COLS.length === PREFS.cols.length && SHOT_COLS.every(k => PREFS.cols.includes(k));
  $("preset-shot").textContent = isShot ? "All columns" : "Screenshot view";
}

function renderPlayers(v) {
  renderPlayerControls();
  const noSpheres = v.room.has_spheres === false;  // static bundles exported without --spheres
  const cols = COLUMNS.filter(c => PREFS.cols.includes(c.key) && !(noSpheres && c.key === "sphere"));
  const {key, dir} = PREFS.sort;
  const sortCol = COLUMNS.find(c => c.key === key && c.sort) || COLUMNS.find(c => c.key === "status");

  $("players-head").replaceChildren(...cols.map(c => {
    const active = c === sortCol;
    return h("th", {
      title: c.title || (c.sort ? "Click to sort" : null),
      class: (c.sort ? "sortable" : "") + (active ? " sorted" : ""),
      onclick: c.sort ? () => {
        PREFS.sort = {key: c.key, dir: active ? -dir : 1};
        savePrefs(); renderPlayers(VIEW);
      } : null,
    }, c.label, active ? (dir > 0 ? " ▲" : " ▼") : "");
  }));

  const q = $("player-search").value.trim().toLowerCase();
  const stateKey = s => (s === "goal" || s === "done") ? "fin" : s;
  const rows = v.players
    .filter(p => PREFS.states.includes(stateKey(p.state)))
    .filter(p => !q || p.name.toLowerCase().includes(q) || p.game.toLowerCase().includes(q))
    .sort((a, b) => dir * cmp(sortCol.sort(a), sortCol.sort(b)) || cmp(a.name.toLowerCase(), b.name.toLowerCase()));

  $("players").replaceChildren(...rows.map(p => h("tr", {}, ...cols.map(c => c.cell(p)))));
  if (!rows.length) $("players").append(h("tr", {}, h("td", {colSpan: cols.length, class: "muted"}, "No players match.")));

  const tracked = Math.min(...v.players.map(p => p.tracking_since || now()));
  const mins = Math.round((v.room.poll_seconds || 300) / 60);
  $("players-note").textContent = v.room.history_scope === "browser"
    ? `BK / un-BK and "last received" times are what this browser has seen while this page was open (it checks ` +
      `every ${mins} min), since ${new Date(tracked * 1000).toLocaleString()}. Changes made while it was closed show ` +
      `as a time range. Archipelago's tracker doesn't timestamp received items.`
    : `BK / un-BK and "last received" times come from this tracker's own polling (every ${mins} min, only while ` +
      `someone has this page open), first seen ${new Date(tracked * 1000).toLocaleString()}. Changes made while nobody ` +
      `was watching show as a time range. Archipelago's tracker doesn't timestamp received items.`;
}

// Copy the players table as a PNG (html2canvas is loaded on first use).
async function copyPlayersImage() {
  const btn = $("copy-img");
  btn.disabled = true;
  try {
    if (!window.html2canvas) await new Promise((res, rej) => {
      const s = document.createElement("script");
      s.src = "https://cdnjs.cloudflare.com/ajax/libs/html2canvas/1.4.1/html2canvas.min.js";
      s.onload = res; s.onerror = () => rej(new Error("could not load html2canvas"));
      document.head.append(s);
    });
    const el = $("players-capture");
    const bg = getComputedStyle(document.querySelector("section")).backgroundColor;
    const canvas = await html2canvas(el, {backgroundColor: bg, scale: 2, windowWidth: el.scrollWidth});
    const blob = await new Promise(r => canvas.toBlob(r, "image/png"));
    try {
      await navigator.clipboard.write([new ClipboardItem({"image/png": blob})]);
      btn.textContent = "Copied!";
    } catch {
      const a = h("a", {href: URL.createObjectURL(blob), download: "players.png"});
      a.click();
      btn.textContent = "Downloaded";
    }
  } catch (e) {
    alert("Couldn't make the image: " + e.message);
  } finally {
    setTimeout(() => { btn.textContent = "Copy as image"; btn.disabled = false; }, 1500);
  }
}

function renderHintFilters(v) {
  const sel = $("hint-player");
  if (sel.options.length > 1) return;
  for (const p of v.players) sel.append(h("option", {value: String(p.slot)}, p.name));
}

function hintKey(x) { return `${x.finder}:${x.location_id}`; }

function renderHints(v) {
  const who = $("hint-player").value;
  const q = $("hint-search").value.trim().toLowerCase();
  const showFound = $("hint-found").checked;
  let list = v.hints.filter(x =>
    (showFound || !x.found) &&
    (!who || String(x.finder) === who || String(x.receiver) === who) &&
    (!q || x.item.toLowerCase().includes(q) || x.location.toLowerCase().includes(q)));
  // unfound + not in logic first (those are the blocked ones), then in-logic, then found
  const rank = x => x.found ? 2 : x.in_logic ? 1 : 0;
  list.sort((a, b) => rank(a) - rank(b) || a.finder_name.localeCompare(b.finder_name));

  if (!list.length) { $("hints").replaceChildren(h("p", {class: "muted"}, "No hints match.")); return; }
  $("hints").replaceChildren(...list.map(renderHint));
}

// Hints section card: hint details on the left, logic needs on the right.
function renderHint(x) {
  const info = h("div", {class: "hint-info"},
    h("div", {}, h("span", {class: "hint-item"}, x.item), ` for ${x.receiver_name}`),
    h("div", {class: "hint-loc"}, `at ${x.location} in ${x.finder_name}'s world`),
    h("div", {class: "hint-pills"},
      x.status === "priority" ? h("span", {class: "pill priority"}, "priority") : h("span", {class: "pill status"}, x.status),
      " ",
      x.found ? h("span", {class: "pill goal"}, "found")
        : x.in_logic ? h("span", {class: "pill ok"}, `in logic for ${x.finder_name}`)
        : h("span", {class: "pill bk"}, "not in logic yet")));
  return h("div", {class: "hint" + (x.found ? " found" : "")}, info, needsPanel(x, "h:"));
}

// The right-hand panel shared by the Hints section and the players table.
function needsPanel(x, prefix) {
  const panel = h("div", {class: "hint-needs"});
  if (x.found) return panel;
  if (x.in_logic) {
    panel.append(h("div", {class: "holder"}, h("span", {class: "yes"}, `In logic: ${x.finder_name} can check it now`)));
  } else if (x.needs) {
    const key = prefix + hintKey(x);
    panel.append(h("details", {open: openDetails.has(key),
      ontoggle: e => e.target.open ? openDetails.add(key) : openDetails.delete(key)},
      h("summary", {}, needsSummary(x.needs, x.finder)), renderNeeds(x.needs, x.finder)));
  }
  return panel;
}

// Unfound hints for item `name` going to `slot` (i.e. the needed item has already been hinted).
function hintsFor(slot, name) {
  return HINTED.get(`${slot}|${name}`) || [];
}
let HINTED = new Map();
function indexHints(v) {
  HINTED = new Map();
  for (const x of v.hints) {
    if (x.found) continue;
    const k = `${x.receiver}|${x.item}`;
    if (!HINTED.has(k)) HINTED.set(k, []);
    HINTED.get(k).push(x);
  }
}
const hintedTag = () => h("span", {class: "tag-hinted", title: "This item has been hinted"}, "hinted");

function needsSummary(n, slot) {
  if (n.kind === "unknown") return "Requirements unknown";
  const parts = n.items.slice(0, 3).map(i => h("span", {class: "sum-item"},
    i.count > 1 ? `${i.name} ×${i.count}` : i.name, hintsFor(slot, i.name).length ? hintedTag() : null));
  const joined = [];
  parts.forEach((p, idx) => { if (idx) joined.push(n.kind === "any" ? " or " : " + "); joined.push(p); });
  return h("span", {}, n.kind === "any" ? "Needs " : "Needs all of: ", ...joined,
    n.items.length > 3 ? ` (+${n.items.length - 3} more)` : "");
}

function renderSpheres(v) {
  const firstOpen = v.spheres.findIndex(s => s.checked < s.total);
  $("spheres").replaceChildren(...v.spheres.filter(s => s.total).map((s, i) => {
    const done = s.checked >= s.total;
    const pct = 100 * s.checked / Math.max(s.total, 1);
    const row = h("div", {class: "sphere" + (done ? " done" : "")},
      h("div", {}, h("b", {}, `Sphere ${s.sphere}`)),
      h("div", {class: "bar"}, h("div", {style: `width:${pct}%`})),
      h("div", {class: "num small"}, `${s.checked}/${s.total}`));
    if (!done) {
      const who = h("div", {class: "who"}, "Open: " + s.open_by_player.join(", "));
      if (s.open_locations && s.sphere <= firstOpen + 2) {
        const key = "sphere:" + s.sphere;
        const det = h("details", {open: openDetails.has(key),
          ontoggle: e => e.target.open ? openDetails.add(key) : openDetails.delete(key)},
          h("summary", {}, "locations"),
          h("ul", {class: "needs"}, ...s.open_locations.map(l =>
            h("li", {class: "holder"}, h("b", {}, l.location), ` (${l.finder_name}) · `,
              l.in_logic ? h("span", {class: "yes"}, "in logic") : h("span", {class: "no"}, "not in logic")))));
        who.append(det);
      }
      row.append(who);
    }
    return row;
  }));
}

// ---- wiring
$("player-search").addEventListener("input", () => VIEW && renderPlayers(VIEW));
$("preset-shot").addEventListener("click", () => {
  const isShot = SHOT_COLS.length === PREFS.cols.length && SHOT_COLS.every(k => PREFS.cols.includes(k));
  PREFS.cols = isShot ? [...ALL_COLS] : [...SHOT_COLS];
  savePrefs(); VIEW && renderPlayers(VIEW);
});
$("copy-img").addEventListener("click", copyPlayersImage);
$("hint-player").addEventListener("change", () => VIEW && renderHints(VIEW));
$("hint-search").addEventListener("input", () => VIEW && renderHints(VIEW));
$("hint-found").addEventListener("change", () => VIEW && renderHints(VIEW));
$("refresh-btn").addEventListener("click", async () => {
  const btn = $("refresh-btn");
  btn.disabled = true;
  if (STATIC) { try { await load(true); } finally { btn.disabled = false; } return; }
  try {
    const r = await fetch(`/api/r/${encodeURIComponent(SLUG)}/refresh`, {method: "POST"});
    if (r.status === 429) showBanner("Refreshed very recently; try again in a minute.", false);
    else if (!r.ok) showBanner("Refresh failed: HTTP " + r.status, true);
    await load();
  } finally { btn.disabled = false; }
});

load();
// Only check in while the tab is visible: the server polls archipelago.gg only while pages check in.
setInterval(() => { if (!document.hidden) load(); }, 30000);
document.addEventListener("visibilitychange", () => { if (!document.hidden) load(); });
setInterval(() => VIEW && ($("refreshed").textContent = "Updated " + ago(VIEW.room.last_refresh)), 15000);
