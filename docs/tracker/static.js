"use strict";
// Serverless mode: evaluates an exported logic bundle (data/<slug>.json, made by export_bundle.py)
// against the live archipelago.gg tracker API, and produces the same "view" the Python server
// builds, so room.js renders it unchanged. Only loaded on the static site.

const SDTStatic = (() => {
  const POLL_SECONDS = 120;       // fetch the tracker at most this often, and only while the tab is visible
  const MANUAL_COOLDOWN = 60;
  const LOW_LOGIC = 3;
  const CHAIN_DEPTH = 3;
  const API = "https://archipelago.gg/api";

  let bundle = null, tracker = null, lastFetch = 0, lastError = null, view = null;

  async function getJSON(url) {
    const r = await fetch(url, {cache: "no-store"});
    if (!r.ok) throw new Error(`HTTP ${r.status} for ${url}`);
    return r.json();
  }

  async function getState(slug, force) {
    try {
      if (!bundle) { bundle = await getJSON(`data/${encodeURIComponent(slug)}.json`); bundle.slug = slug; }
    } catch (e) {
      return {phase: "error", error: `Could not load data/${slug}.json (${e.message})`, title: slug, view: null, admin: false};
    }
    const now = Date.now() / 1000;
    const due = !tracker || now - lastFetch >= POLL_SECONDS || (force && now - lastFetch >= MANUAL_COOLDOWN);
    if (due) {
      lastFetch = now;
      try {
        tracker = await getJSON(`${API}/tracker/${bundle.tracker_id}`);
        lastError = null;
        view = buildView(bundle, tracker, now);
      } catch (e) {
        lastError = `Last refresh failed: ${e.message}`;
      }
    }
    return {phase: "ready", error: lastError, title: bundle.title, view, admin: false,
            cooldown: force && !due};
  }

  // ------------------------------------------------------------------ logic

  function buildView(b, t, now) {
    const P = new Map();  // slot -> player runtime
    for (const p of b.players) {
      const locById = new Map(p.locs.map(l => [l[0], l]));
      P.set(p.slot, {
        def: p, locById, have: new Array(p.items.length).fill(0), checked: new Set(),
        reqOk: null, approx: new Set(p.approx),
      });
    }
    let received = {};
    for (const e of t.player_items_received) {
      const pr = P.get(e.player);
      if (!pr) continue;
      received[e.player] = e.items.length;
      for (const [itemId, locId, , flags] of e.items) {
        if (locId === -2) continue;  // start inventory: already baked into the requirements
        // Only progression copies count for logic; some games have filler copies of the same item id.
        if (!(flags & 1)) continue;
        const idx = pr.def.item_ids[String(itemId)];
        if (idx !== undefined) pr.have[idx]++;
      }
    }
    for (const e of t.player_checks_done) { const pr = P.get(e.player); if (pr) e.locations.forEach(id => pr.checked.add(id)); }
    const goals = new Set(t.player_status.filter(s => s.status === 30).map(s => s.player));
    const activity = Object.fromEntries(t.activity_timers.map(a => [a.player, a.time ? Date.parse(a.time) / 1000 : null]));

    const termOk = (term, have) => { for (let k = 0; k < term.length; k += 2) if (have[term[k]] < term[k + 1]) return false; return true; };
    for (const pr of P.values()) pr.reqOk = pr.def.reqs.map(terms => terms.some(term => termOk(term, pr.have)));
    const inLogic = (pr, locId) => { const l = pr.locById.get(locId); return !!l && pr.reqOk[l[2]]; };

    // hints (deduped by finder location), with names resolved from the bundle
    const hints = [], seen = new Set();
    for (const e of t.hints) for (const [recv, finder, locId, itemId, found, , , status] of e.hints) {
      const key = finder + ":" + locId;
      if (seen.has(key)) continue;
      seen.add(key);
      const fp = P.get(finder), rp = P.get(recv);
      if (!fp || !rp) continue;
      const loc = fp.locById.get(locId);
      if (!loc) continue;
      hints.push({
        finder, finder_name: fp.def.name, receiver: recv, receiver_name: rp.def.name,
        item: (b.games[rp.def.game] || {})[String(itemId)] || `#${itemId}`,
        location: loc[1], location_id: locId,
        status: {0: "unspecified", 10: "no priority", 20: "avoid", 30: "priority", 40: "found"}[status] ?? String(status),
        found: found || fp.checked.has(locId), in_logic: inLogic(fp, locId), needs: null,
      });
    }
    const unfoundHintsFor = (slot, name) => hints.filter(x => !x.found && x.receiver === slot && x.item === name);

    // What does `slot` still need for location `loc`? Mirrors the server's Needs.
    function needsOf(pr, loc) {
      const n = needsOfInner(pr, loc);
      if (pr.approx.has(loc[2]) && n.kind !== "in_logic") n.approx = true;
      return n;
    }
    function needsOfInner(pr, loc) {
      if (pr.reqOk[loc[2]]) return {kind: "in_logic", items: []};
      const terms = pr.def.reqs[loc[2]];
      if (!terms.length) return {kind: "unknown", items: []};
      const singles = new Set();
      let best = null, bestCost = Infinity;
      for (const term of terms) {
        const miss = [];
        for (let k = 0; k < term.length; k += 2) {
          const d = term[k + 1] - pr.have[term[k]];
          if (d > 0) miss.push([term[k], d]);
        }
        if (miss.length === 1 && miss[0][1] === 1) singles.add(miss[0][0]);
        const cost = miss.reduce((a, [, d]) => a + d, 0) * 100 + miss.length;
        if (cost < bestCost) { bestCost = cost; best = miss; }
      }
      const names = pr.def.items;
      if (singles.size) return {kind: "any", items: [...singles].map(i => ({name: names[i], count: 1})).sort((a, b) => a.name.localeCompare(b.name)).slice(0, 12)};
      return {kind: "all", items: best.map(([i, d]) => ({name: names[i], count: d})).sort((a, b) => a.name.localeCompare(b.name)).slice(0, 12)};
    }

    // Chains only follow hinted locations (this page is public: no placements in the bundle).
    function needsNode(slot, loc, depth, visited) {
      const pr = P.get(slot);
      const n = needsOf(pr, loc);
      for (const it of n.items) {
        it.holders = unfoundHintsFor(slot, it.name).map(hx => holderNode(hx, depth, visited));
      }
      return n;
    }
    function holderNode(hx, depth, visited) {
      const key = hx.finder + ":" + hx.location_id;
      const node = {location: hx.location, finder: hx.finder, finder_name: hx.finder_name, hinted: true,
                    checked: hx.found, in_logic: hx.in_logic};
      if (node.checked || node.in_logic) return node;
      if (visited.has(key)) node.cycle = true;
      else if (depth < CHAIN_DEPTH) {
        const fp = P.get(hx.finder);
        node.needs = needsNode(hx.finder, fp.locById.get(hx.location_id), depth + 1, new Set([...visited, key]));
      }
      return node;
    }
    for (const x of hints) {
      if (!x.found && !x.in_logic) {
        const fp = P.get(x.finder);
        x.needs = needsNode(x.finder, fp.locById.get(x.location_id), 0, new Set([x.finder + ":" + x.location_id]));
      }
    }

    // players
    const players = [];
    for (const pr of P.values()) {
      const p = pr.def;
      const unchecked = p.locs.filter(l => !pr.checked.has(l[0]));
      const avail = unchecked.filter(l => pr.reqOk[l[2]]);
      // Locations whose rule was too large to export exactly ("N of a group" rules): known
      // combinations are always right, but one we didn't list might also work -> "maybe".
      const uncertain = unchecked.filter(l => !pr.reqOk[l[2]] && pr.approx.has(l[2])).length;
      const checks = p.locs.length - unchecked.length;
      let state = goals.has(p.slot) ? "goal" : checks === p.locs.length ? "done"
        : avail.length === 0 ? "bk" : avail.length <= LOW_LOGIC ? "low" : "ok";
      let frontier = null;
      if (b.has_spheres) for (const l of unchecked) if (l[3] != null && (frontier === null || l[3] < frontier)) frontier = l[3];
      const unlocks = [];
      if (state === "bk" || state === "low") {
        const gain = new Map();
        for (const l of unchecked) {
          if (pr.reqOk[l[2]]) continue;
          const opens = new Set();
          for (const term of p.reqs[l[2]]) {
            let miss = null, n = 0;
            for (let k = 0; k < term.length; k += 2) {
              const d = term[k + 1] - pr.have[term[k]];
              if (d > 0) { n++; miss = d === 1 ? term[k] : -1; }
            }
            if (n === 1 && miss >= 0) opens.add(miss);
          }
          opens.forEach(i => gain.set(i, (gain.get(i) || 0) + 1));
        }
        [...gain].sort((a, c) => c[1] - a[1] || p.items[a[0]].localeCompare(p.items[c[0]])).slice(0, 6)
          .forEach(([i, g]) => unlocks.push({name: p.items[i], gain: g,
            holders: unfoundHintsFor(p.slot, p.items[i]).slice(0, 1).map(hx => holderNode(hx, CHAIN_DEPTH, new Set()))}));
      }
      players.push({slot: p.slot, name: p.name, game: p.game, checks, total: p.locs.length,
                    in_logic: avail.length, uncertain, frontier_sphere: frontier, state,
                    last_activity: activity[p.slot] ?? null, unlocks});
    }
    applyHistory(b, players, received, now);

    let spheres = [];
    if (b.has_spheres) {
      spheres = Array.from({length: b.sphere_count}, (_, i) => ({sphere: i, total: 0, checked: 0, open: new Set()}));
      for (const pr of P.values()) for (const l of pr.def.locs) {
        if (l[3] == null) continue;
        const s = spheres[l[3]];
        s.total++;
        if (pr.checked.has(l[0])) s.checked++; else s.open.add(pr.def.name);
      }
      spheres = spheres.map(s => ({sphere: s.sphere, total: s.total, checked: s.checked, open_by_player: [...s.open].sort()}));
    }

    return {
      admin: false,
      room: {slug: b.slug, title: b.title, poll_seconds: POLL_SECONDS, seed: b.seed, ap_version: b.ap_version,
             last_refresh: now, history_scope: "browser", has_spheres: b.has_spheres},
      players, hints, spheres,
    };
  }

  // BK / receive timing, remembered per browser (there is no server to remember it for everyone).
  function applyHistory(b, players, received, now) {
    const key = `sdt-history:${b.room_id}:${b.seed}`;
    let hist = {};
    try { hist = JSON.parse(localStorage.getItem(key) || "{}"); } catch { /* private mode */ }
    for (const p of players) {
      const bk = p.state === "bk", rc = received[p.slot] || 0;
      let r = hist[p.slot];
      if (!r) r = hist[p.slot] = {first: now, poll: now, bk, rc, bkFrom: null, bkAt: null, rFrom: null, rAt: null};
      else {
        if (r.bk !== bk) { r.bkFrom = r.poll; r.bkAt = now; r.bk = bk; }
        if (rc > r.rc) { r.rFrom = r.poll; r.rAt = now; }
        r.rc = rc; r.poll = now;
      }
      Object.assign(p, {tracking_since: r.first, bk_changed_from: r.bkFrom, bk_changed_at: r.bkAt,
                        last_received_from: r.rFrom, last_received: r.rAt});
    }
    try { localStorage.setItem(key, JSON.stringify(hist)); } catch { /* ignore */ }
  }

  return {getState};
})();
