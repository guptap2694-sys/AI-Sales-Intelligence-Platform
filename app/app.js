// Perimeter Radar — static prospecting app. No build step, no dependencies.
// Reads data/accounts.json (falls back to data/accounts.sample.json) or an inlined
// <script type="application/json" id="embedded-data"> when bundled by scripts/bundle_app.py.
// Relies only on fields in docs/CONTRACT.md §2–3 and tolerates nulls / missing keys.

/* ============================== constants ============================== */
const CATS = ["regulatory", "data_sensitivity", "attack_surface", "growth", "maturity_gap", "timing"];
const CAT_LABEL = {
  regulatory: "Regulatory", data_sensitivity: "Data sensitivity", attack_surface: "Attack surface",
  growth: "Growth", maturity_gap: "Maturity gap", timing: "Timing", other: "Other",
};
const BAND_ORDER = ["1-10", "11-50", "51-200", "201-500", "501-1000", "1001-5000", "5000+", "unknown"];
const TIERS = ["A", "B", "C", "D"];
const TIER_MIN = { A: 75, B: 60, C: 40, D: 0 };
const PLAYS = {
  compliance: { label: "Compliance", desc: "Lead with regulatory obligations, audit readiness and data-handling duties." },
  scale_up: { label: "Scale-up", desc: "Growing fast. Position security that scales with headcount and new systems." },
  exposed_surface: { label: "Exposed surface", desc: "Visible attack surface. Open with a free external exposure review." },
  modernise: { label: "Modernise", desc: "Maturity gap. Offer a managed baseline: MFA, EDR, backups, monitoring." },
  nurture: { label: "Nurture", desc: "Low urgency today. Add to a nurture sequence and watch for new signals." },
};
const FIT_LABEL = { size: "Company size", industry: "Industry", geo: "Territory", data: "Data profile" };
const LS = { tuner: "pr.tuner.v1", shortlist: "pr.shortlist.v1", theme: "pr.theme", heat: "pr.heat" };
const STAR_SVG = '<svg viewBox="0 0 20 20" aria-hidden="true"><path d="M10 2.6l2.2 4.6 5 .7-3.6 3.5.9 5-4.5-2.4-4.5 2.4.9-5L2.8 7.9l5-.7z" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round"/></svg>';
const SEARCH_SVG = '<svg viewBox="0 0 20 20" aria-hidden="true"><circle cx="8.5" cy="8.5" r="5.5" fill="none" stroke="currentColor" stroke-width="1.7"/><path d="M13 13l4 4" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"/></svg>';
const DL_SVG = '<svg viewBox="0 0 20 20" aria-hidden="true"><path d="M10 3v10m0 0l-4-4m4 4l4-4M4 16h12" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"/></svg>';
const FILTER_SVG = '<svg viewBox="0 0 20 20" aria-hidden="true"><path d="M3 5h14M6 10h8M8.5 15h3" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"/></svg>';

/* ============================== helpers ============================== */
const $ = (sel, root = document) => root.querySelector(sel);
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const num = (v) => { if (v === null || v === undefined || v === "") return null; const n = Number(v); return Number.isFinite(n) ? n : null; };
const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));
const fmt = (n) => (n === null || n === undefined ? "—" : Number(n).toLocaleString("en-AU"));
const pct = (a, b) => (b ? Math.round((a / b) * 100) : 0);
const tierOf = (p) => (p >= 75 ? "A" : p >= 60 ? "B" : p >= 40 ? "C" : "D");
const humanize = (id) => String(id).replace(/[_-]+/g, " ").replace(/^\w/, (c) => c.toUpperCase());
const money = (v) => { const n = num(v); if (n === null) return "—"; if (n >= 1e9) return `$${(n / 1e9).toFixed(1)}B`; if (n >= 1e6) return `$${(n / 1e6).toFixed(1)}M`; if (n >= 1e3) return `$${Math.round(n / 1e3)}K`; return `$${n}`; };
const catColor = (c) => (CATS.includes(c) ? `var(--cat-${c})` : "var(--muted)");
const playLabel = (p) => PLAYS[p]?.label || (p ? humanize(p) : "—");
function lsGet(k, fb) { try { const v = localStorage.getItem(k); return v === null ? fb : JSON.parse(v); } catch { return fb; } }
function lsSet(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch { /* storage blocked */ } }
function objNums(o) { const out = {}; if (o && typeof o === "object") for (const [k, v] of Object.entries(o)) { const n = num(v); if (n !== null) out[k] = n; } return out; }
const sumVals = (o) => Object.values(o).reduce((s, v) => s + v, 0);

/* ============================== state ============================== */
const S = {
  meta: {}, accounts: [], byId: new Map(), source: "",
  sigCatalog: new Map(), // id -> {id,label,category,count}
  facets: { countries: [], industries: [], bands: [], plays: [] },
  view: "list",
  f: defaultFilters(),
  sort: { key: "priority", dir: "desc" },
  rows: [], openId: null, selected: new Set(),
  shortlist: new Set(lsGet(LS.shortlist, [])),
  tuner: null, tunerBase: null, tuned: false,
  heatMetric: lsGet(LS.heat, "ab"),
};
function defaultFilters() { return { q: "", country: "", state: "", industries: [], bands: [], tiers: [], signals: [], sigMode: "any", plays: [], ai: false }; }

/* ============================== data loading ============================== */
async function loadData() {
  const emb = document.getElementById("embedded-data");
  if (emb && emb.textContent.trim().length > 2) return { doc: JSON.parse(emb.textContent), src: emb.dataset.source || "embedded" };
  const errors = [];
  for (const url of ["data/accounts.json", "data/accounts.sample.json"]) {
    try {
      const r = await fetch(url, { cache: "no-cache" });
      if (r.ok) return { doc: await r.json(), src: url };
      errors.push(`${url}: HTTP ${r.status}`);
    } catch (e) { errors.push(`${url}: ${e.message}`); }
  }
  throw new Error(errors.join("; "));
}

function normalise(doc) {
  const meta = doc && typeof doc.meta === "object" && doc.meta ? doc.meta : {};
  const w = meta.weights || {};
  let wf = num(w.fit), wi = num(w.intent);
  if (wf === null && wi === null) { wf = 0.5; wi = 0.5; } else if (wf === null) wf = 1 - wi; else if (wi === null) wi = 1 - wf;
  const tot = wf + wi || 1; wf /= tot; wi /= tot;
  meta._wf = wf; meta._wi = wi;
  const list = Array.isArray(doc?.accounts) ? doc.accounts : [];
  const accounts = [];
  list.forEach((raw, i) => {
    if (!raw || typeof raw !== "object") return;
    const a = raw;
    a.id = a.id != null ? String(a.id) : `row-${i}`;
    a.name = a.name ? String(a.name) : (a.domain || "Unnamed company");
    a.signals = (Array.isArray(a.signals) ? a.signals : []).filter((s) => s && typeof s === "object").map((s) => ({
      id: String(s.id ?? s.label ?? "signal"), label: String(s.label ?? humanize(s.id ?? "signal")),
      category: CATS.includes(s.category) ? s.category : "other", weight: num(s.weight) ?? 0, evidence: s.evidence ? String(s.evidence) : "",
    })).sort((x, y) => y.weight - x.weight);
    a.$fb = objNums(a.fit_breakdown);
    a.$ib = objNums(a.intent_breakdown);
    const fit = num(a.fit_score) ?? clamp(sumVals(a.$fb), 0, 100);
    const intent = num(a.intent_score) ?? clamp(sumVals(a.$ib), 0, 100);
    const pri = num(a.priority_score) ?? Math.round(wf * fit + wi * intent);
    a.$fit0 = fit; a.$intent0 = intent; a.$pri0 = pri;
    a.$tier0 = TIERS.includes(a.tier) ? a.tier : tierOf(pri);
    a.$band = BAND_ORDER.includes(a.size_band) ? a.size_band : (a.size_band ? String(a.size_band) : "unknown");
    a.$llm = a.llm && typeof a.llm === "object" ? a.llm : null;
    a.$sigIds = new Set(a.signals.map((s) => s.id));
    a.$hay = [a.name, a.domain, a.city, a.state, a.country, a.industry, a.segment, a.description, ...a.signals.map((s) => s.label)].filter(Boolean).join(" ").toLowerCase();
    accounts.push(a);
  });
  // baseline rank
  [...accounts].sort((x, y) => y.$pri0 - x.$pri0).forEach((a, i) => { a.$rank0 = i + 1; });
  return { meta, accounts };
}

function buildFacets() {
  const cnt = (key) => { const m = new Map(); for (const a of S.accounts) { const v = typeof key === "function" ? key(a) : a[key]; if (v) m.set(v, (m.get(v) || 0) + 1); } return m; };
  const countries = cnt("country");
  S.facets.countries = [...countries].sort((a, b) => b[1] - a[1]);
  S.facets.industries = [...cnt("industry")].sort((a, b) => b[1] - a[1]);
  const bands = cnt((a) => a.$band);
  S.facets.bands = [...bands].sort((a, b) => bandIdx(a[0]) - bandIdx(b[0]));
  S.facets.plays = [...cnt("play")].sort((a, b) => b[1] - a[1]);
  const cat = new Map();
  for (const a of S.accounts) for (const s of a.signals) {
    let e = cat.get(s.id);
    if (!e) { e = { id: s.id, labels: new Map(), category: s.category, count: 0 }; cat.set(s.id, e); }
    e.count++; e.labels.set(s.label, (e.labels.get(s.label) || 0) + 1);
  }
  for (const e of cat.values()) {
    // signal labels can embed numbers ("Headcount up 23%"); use one label if they all agree, else the id
    e.label = e.labels.size === 1 ? [...e.labels.keys()][0] : humanize(e.id);
    delete e.labels;
  }
  S.sigCatalog = new Map([...cat].sort((a, b) => b[1].count - a[1].count));
}
const bandIdx = (b) => { const i = BAND_ORDER.indexOf(b); return i === -1 ? 50 : i; };

/* ============================== scoring / ICP tuner ============================== */
function tunerDefaults() {
  return { wi: Math.round(S.meta._wi * 100), mult: Object.fromEntries(CATS.map((c) => [c, 1])), offBands: [], offCountries: [] };
}
function isTunerDefault(t) {
  const d = S.tunerBase;
  return t.wi === d.wi && CATS.every((c) => (t.mult[c] ?? 1) === 1) && !t.offBands.length && !t.offCountries.length;
}
function rescore() {
  const t = S.tuner;
  S.tuned = !isTunerDefault(t);
  const wi = t.wi / 100, wf = 1 - wi;
  const offB = new Set(t.offBands), offC = new Set(t.offCountries);
  for (const a of S.accounts) {
    if (!S.tuned) { a.$fit = a.$fit0; a.$intent = a.$intent0; a.$pri = a.$pri0; a.$tier = a.$tier0; continue; }
    // fit: zero the size / geo component when outside the target bands / countries, rescaled to the pipeline's fit scale
    let fit = a.$fit0;
    const fbSum = sumVals(a.$fb);
    if (fbSum > 0) {
      let adj = 0;
      for (const [k, v] of Object.entries(a.$fb)) {
        if (k === "size" && offB.has(a.$band)) continue;
        if (k === "geo" && a.country && offC.has(a.country)) continue;
        adj += v;
      }
      fit = a.$fit0 * (adj / fbSum);
    }
    // intent: sum of category points × multiplier, capped at 100 (contract §3)
    let intent = a.$intent0;
    if (Object.keys(a.$ib).length) {
      let s = 0;
      for (const [k, v] of Object.entries(a.$ib)) s += v * (t.mult[k] ?? 1);
      intent = Math.min(100, s);
    }
    a.$fit = Math.round(clamp(fit, 0, 100));
    a.$intent = Math.round(clamp(intent, 0, 100));
    a.$pri = Math.round(wf * a.$fit + wi * a.$intent);
    a.$tier = tierOf(a.$pri);
  }
  [...S.accounts].sort((x, y) => y.$pri - x.$pri || x.$rank0 - y.$rank0).forEach((a, i) => { a.$rank = i + 1; });
  const pill = $("#icp-pill"); if (pill) pill.hidden = !S.tuned;
}
function saveTuner() { lsSet(LS.tuner, S.tuner); }

/* ============================== filtering & sorting ============================== */
function matches(a, f) {
  if (f.country && a.country !== f.country) return false;
  if (f.state && a.state !== f.state) return false;
  if (f.industries.length && !f.industries.includes(a.industry)) return false;
  if (f.bands.length && !f.bands.includes(a.$band)) return false;
  if (f.tiers.length && !f.tiers.includes(a.$tier)) return false;
  if (f.plays.length && !f.plays.includes(a.play)) return false;
  if (f.ai && !a.$llm) return false;
  if (f.signals.length) {
    if (f.sigMode === "all") { if (!f.signals.every((s) => a.$sigIds.has(s))) return false; }
    else if (!f.signals.some((s) => a.$sigIds.has(s))) return false;
  }
  if (f.q) { for (const tok of f.q.toLowerCase().split(/\s+/)) if (tok && !a.$hay.includes(tok)) return false; }
  return true;
}
const SORTERS = {
  name: (a) => a.name.toLowerCase(),
  segment: (a) => (a.segment || "~").toLowerCase(),
  size: (a) => a.employee_count ?? (bandIdx(a.$band) < 50 ? [5, 30, 120, 350, 750, 3000, 8000, -1][bandIdx(a.$band)] : -1),
  location: (a) => [a.country, a.state, a.city].filter(Boolean).join(" ").toLowerCase() || "~",
  tier: (a) => TIERS.indexOf(a.$tier),
  priority: (a) => a.$pri,
  signals: (a) => a.signals.length,
  play: (a) => a.play || "~",
};
function computeRows() {
  let rows;
  if (S.view === "shortlist") rows = S.accounts.filter((a) => S.shortlist.has(a.id));
  else rows = S.accounts.filter((a) => matches(a, S.f));
  const get = SORTERS[S.sort.key] || SORTERS.priority;
  const dir = S.sort.dir === "asc" ? 1 : -1;
  rows.sort((x, y) => { const a = get(x), b = get(y); if (a < b) return -dir; if (a > b) return dir; return y.$pri - x.$pri || x.name.localeCompare(y.name); });
  S.rows = rows;
}
function activeFilterCount(f = S.f) {
  return (f.q ? 1 : 0) + (f.country ? 1 : 0) + (f.state ? 1 : 0) + f.industries.length + f.bands.length + f.tiers.length + f.signals.length + f.plays.length + (f.ai ? 1 : 0);
}

/* ============================== URL hash (shareable views) ============================== */
let lastHash = "";
function writeHash() {
  const p = new URLSearchParams();
  if (S.view !== "list") p.set("v", S.view);
  const f = S.f;
  if (f.q) p.set("q", f.q);
  if (f.country) p.set("co", f.country);
  if (f.state) p.set("st", f.state);
  if (f.industries.length) p.set("in", f.industries.join("~"));
  if (f.bands.length) p.set("sb", f.bands.join("~"));
  if (f.tiers.length) p.set("t", f.tiers.join(""));
  if (f.signals.length) p.set("sg", f.signals.join("~"));
  if (f.signals.length > 1 && f.sigMode === "all") p.set("sm", "all");
  if (f.plays.length) p.set("pl", f.plays.join("~"));
  if (f.ai) p.set("ai", "1");
  if (!(S.sort.key === "priority" && S.sort.dir === "desc")) p.set("s", `${S.sort.key}.${S.sort.dir}`);
  if (S.openId) p.set("a", S.openId);
  const h = p.toString();
  lastHash = h;
  try { history.replaceState(null, "", h ? `#${h}` : location.pathname + location.search); } catch { /* sandboxed */ }
}
function readHash() {
  let h = "";
  try { h = decodeURIComponent(location.hash.replace(/^#/, "")); } catch { h = location.hash.replace(/^#/, ""); }
  if (h === lastHash && h) return false;
  if (["list", "market", "tuner", "shortlist"].includes(h)) { S.view = h; return true; }
  if (!h.includes("=")) return false;
  const p = new URLSearchParams(h);
  const split = (k) => (p.get(k) ? p.get(k).split("~").filter(Boolean) : []);
  S.view = ["list", "market", "tuner", "shortlist"].includes(p.get("v")) ? p.get("v") : "list";
  S.f = {
    q: p.get("q") || "", country: p.get("co") || "", state: p.get("st") || "",
    industries: split("in"), bands: split("sb"), tiers: (p.get("t") || "").split("").filter((t) => TIERS.includes(t)),
    signals: split("sg"), sigMode: p.get("sm") === "all" ? "all" : "any", plays: split("pl"), ai: p.get("ai") === "1",
  };
  const s = (p.get("s") || "").split(".");
  S.sort = SORTERS[s[0]] ? { key: s[0], dir: s[1] === "asc" ? "asc" : "desc" } : { key: "priority", dir: "desc" };
  S.openId = p.get("a") || null;
  return true;
}

/* ============================== top-level render ============================== */
function setView(v) {
  S.view = v;
  closeFiltersSheet();
  for (const t of document.querySelectorAll(".tab")) t.setAttribute("aria-selected", String(t.dataset.view === v));
  render();
  writeHash();
}
function render() {
  const main = $("#main");
  main.classList.toggle("is-list", S.view === "list" || S.view === "shortlist");
  for (const t of document.querySelectorAll(".tab")) t.setAttribute("aria-selected", String(t.dataset.view === S.view));
  if (S.view === "market") renderMarket();
  else if (S.view === "tuner") renderTuner();
  else renderListView();
  updateShortlistCount();
}
function updateShortlistCount() { const el = $("#shortlist-count"); if (el) el.textContent = S.shortlist.size; }

/* ---------------------------- list view ---------------------------- */
function renderListView() {
  const isShort = S.view === "shortlist";
  $("#main").innerHTML = `
    <div class="list-view" style="${isShort ? "grid-template-columns:minmax(0,1fr)" : ""}">
      ${isShort ? "" : `<aside class="filters" id="filters" aria-label="Filters"></aside>`}
      <section class="results">
        <div class="results-head">
          <div>
            <h1>${isShort ? "Shortlist" : "Today’s priorities"}</h1>
            <div class="sub num" id="results-sub"></div>
          </div>
          <span class="spacer"></span>
          <div class="results-actions">
            ${isShort ? "" : `<button class="btn filters-toggle" id="filters-open" type="button">${FILTER_SVG} Filters <span class="count-pill" id="filters-n">0</span></button>`}
            ${isShort && S.shortlist.size ? `<button class="btn ghost" data-act="clear-shortlist" type="button">Clear shortlist</button>` : ""}
            <button class="btn primary" id="export-btn" type="button">${DL_SVG} <span id="export-label">Export CSV</span></button>
          </div>
        </div>
        <div class="active-chips" id="active-chips"></div>
        <div class="table-wrap" id="table-wrap" role="table" aria-label="Ranked accounts">
          <div class="grid-row thead" role="row" id="thead"></div>
          <div class="tbody" id="tbody"><div class="tbody-spacer" id="tbody-spacer"></div></div>
        </div>
      </section>
    </div>`;
  if (!isShort) renderFilters();
  renderHead();
  $("#tbody").addEventListener("scroll", scheduleWindow, { passive: true });
  refreshResults(true);
}

function renderHead() {
  const col = (key, label, cls = "", tip = "") => {
    const active = S.sort.key === key;
    const arr = active ? (S.sort.dir === "asc" ? "▲" : "▼") : "";
    return `<div class="cell ${cls}" role="columnheader" ${tip ? `data-tip="${esc(tip)}"` : ""} aria-sort="${active ? (S.sort.dir === "asc" ? "ascending" : "descending") : "none"}"><button class="sort" data-sort="${key}" ${active ? "data-active" : ""} type="button">${label}<span class="arr">${arr}</span></button></div>`;
  };
  const allSel = S.rows.length > 0 && S.rows.every((a) => S.selected.has(a.id));
  $("#thead").innerHTML = `
    <div class="cell col-sel"><input type="checkbox" id="sel-all" aria-label="Select all rows in view" ${allSel ? "checked" : ""}></div>
    <div class="cell" aria-hidden="true"></div>
    ${col("name", "Account")}${col("segment", "Segment", "col-seg")}${col("size", "Size", "col-size")}${col("location", "Location", "col-loc")}
    ${col("tier", "Tier")}${col("priority", "Priority", "", "Priority 0–100. Bars: fit (grey, who they are) and intent (teal, what is happening now)")}${col("signals", "Top signals", "col-sig")}${col("play", "Play", "col-play")}`;
}

function refreshResults(resetScroll) {
  computeRows();
  const total = S.accounts.length;
  const sub = $("#results-sub");
  const sortName = { name: "name", segment: "segment", size: "size", location: "location", tier: "tier", priority: "priority", signals: "signal count", play: "play" }[S.sort.key];
  if (sub) {
    const nA = S.rows.filter((a) => a.$tier === "A").length;
    sub.textContent = S.view === "shortlist"
      ? `${fmt(S.rows.length)} starred account${S.rows.length === 1 ? "" : "s"} · sorted by ${sortName}`
      : `${fmt(S.rows.length)} of ${fmt(total)} accounts · ${fmt(nA)} in tier A · sorted by ${sortName}${S.tuned ? " · custom ICP" : ""}`;
  }
  const n = activeFilterCount();
  const fn = $("#filters-n"); if (fn) fn.textContent = n;
  renderActiveChips();
  updateExportLabel();
  renderHead();
  const spacer = $("#tbody-spacer");
  if (!spacer) return;
  if (!S.rows.length) {
    spacer.style.height = "auto";
    spacer.innerHTML = S.view === "shortlist"
      ? `<div class="empty"><h3>No accounts starred yet</h3><p>Star accounts in Today’s priorities to collect them here for follow-up and export.</p></div>`
      : `<div class="empty"><h3>No accounts match these filters</h3><p>Remove a filter or <button class="mini-link" data-act="clear-filters" type="button">clear all filters</button>.</p></div>`;
    return;
  }
  spacer.style.height = `${S.rows.length * rowH()}px`;
  if (resetScroll) $("#tbody").scrollTop = 0;
  renderWindow();
}
function rowH() { return parseFloat(getComputedStyle(document.documentElement).getPropertyValue("--row-h")) || 60; }
let winRaf = 0;
function scheduleWindow() { if (!winRaf) winRaf = requestAnimationFrame(() => { winRaf = 0; renderWindow(); }); }
function renderWindow() {
  const body = $("#tbody"), spacer = $("#tbody-spacer");
  if (!body || !spacer || !S.rows.length) return;
  const rh = rowH();
  const top = body.scrollTop, h = body.clientHeight || 600;
  const start = Math.max(0, Math.floor(top / rh) - 6);
  const end = Math.min(S.rows.length, Math.ceil((top + h) / rh) + 6);
  let html = "";
  for (let i = start; i < end; i++) html += rowHtml(S.rows[i], i * rh);
  spacer.innerHTML = html;
}
function rowHtml(a, top) {
  const starred = S.shortlist.has(a.id), sel = S.selected.has(a.id);
  const loc = [a.city, a.state].filter(Boolean).join(", ") || a.country || "—";
  const loc2 = a.city ? [a.state, a.country].filter(Boolean).join(", ") : (a.state ? a.country || "" : "");
  const more = a.signals.length > 2 ? `<span class="more-n">+${a.signals.length - 2}</span>` : "";
  const sigs = a.signals.slice(0, 2).map((s, i) => `<span class="chip-line"><span class="chip" data-tip="${esc(s.label)}\n${esc(CAT_LABEL[s.category])} · +${s.weight}\n${esc(s.evidence)}"><span class="dot" style="background:${catColor(s.category)}"></span>${esc(s.label)}</span>${i === 1 ? more : ""}</span>`).join("");
  const moved = S.tuned && a.$tier !== a.$tier0 ? (TIERS.indexOf(a.$tier) < TIERS.indexOf(a.$tier0) ? `<span class="moved" title="Was tier ${a.$tier0}">▲</span>` : `<span class="moved down" title="Was tier ${a.$tier0}">▼</span>`) : "";
  const emp = a.employee_count != null ? `${fmt(a.employee_count)} staff` : "staff n/a";
  return `<div tabindex="0" class="row grid-row${S.openId === a.id ? " is-open" : ""}${sel ? " is-selected" : ""}" role="row" data-id="${esc(a.id)}" style="top:${top}px">
    <div class="cell col-sel"><input type="checkbox" data-act="sel" aria-label="Select ${esc(a.name)}" ${sel ? "checked" : ""}></div>
    <div class="cell"><button class="star" data-act="star" type="button" aria-pressed="${starred}" aria-label="${starred ? "Remove from" : "Add to"} shortlist">${STAR_SVG}</button></div>
    <div class="cell"><span class="acct-name">${esc(a.name)}</span>
      <span class="acct-sub desk-sub">${esc(a.domain || "no domain")}${a.$llm ? ' · <span class="ai-dot">AI brief</span>' : ""}</span>
      <span class="acct-sub mobile-sub">${esc(a.segment || a.industry || "")} · ${esc(loc)}</span></div>
    <div class="cell col-seg cell-2line"><span class="a">${esc(a.segment || "—")}</span><span class="b">${esc(a.industry || "")}<span class="size-inline"> · ${esc(a.$band)}</span></span></div>
    <div class="cell col-size cell-2line"><span class="a num">${esc(a.$band)}</span><span class="b num">${emp}</span></div>
    <div class="cell col-loc cell-2line"><span class="a">${esc(loc)}</span><span class="b">${esc(loc2)}</span></div>
    <div class="cell"><span class="tier tier-${a.$tier}" aria-label="Tier ${a.$tier}">${a.$tier}</span>${moved}</div>
    <div class="cell prio" data-tip="Priority ${a.$pri}\nFit ${a.$fit} · Intent ${a.$intent}"><span class="prio-num">${a.$pri}</span>
      <span class="fi" aria-label="Fit ${a.$fit}, intent ${a.$intent}"><span class="fi-bar f"><i style="width:${a.$fit}%"></i></span><span class="fi-bar i"><i style="width:${a.$intent}%"></i></span></span></div>
    <div class="cell col-sig chips">${sigs || '<span class="more-n">No signals</span>'}</div>
    <div class="cell col-play"><span class="play play-${esc(a.play || "nurture")}">${esc(playLabel(a.play))}</span></div>
  </div>`;
}

function renderFilters() {
  const el = $("#filters");
  if (!el) return;
  const f = S.f;
  const prevScroll = el.scrollTop;
  const indQ = $("#ind-search")?.value || "", sigQ = $("#sig-search")?.value || "";
  const states = new Map();
  for (const a of S.accounts) if (a.state && (!f.country || a.country === f.country)) states.set(a.state, (states.get(a.state) || 0) + 1);
  const stateOpts = [...states].sort((a, b) => b[1] - a[1]);
  const tierCounts = Object.fromEntries(TIERS.map((t) => [t, 0])); for (const a of S.accounts) tierCounts[a.$tier]++;
  const toggles = (list, sel, attr, labelFn = (v) => v) => list.map(([v, n]) => `<button class="toggle" type="button" data-${attr}="${esc(v)}" aria-pressed="${sel.includes(v)}">${esc(labelFn(v))} <span class="n">${fmt(n)}</span></button>`).join("");
  const industries = S.facets.industries.slice(0, 400);
  const sigs = [...S.sigCatalog.values()];
  el.innerHTML = `
    <div class="filters-head"><h2>Filters</h2>
      <span><button class="btn ghost" data-act="clear-filters" type="button" ${activeFilterCount() ? "" : "disabled"}>Clear all</button>
      <button class="btn ghost filters-close" data-act="close-filters" type="button" aria-label="Close filters">Done</button></span></div>
    <div class="fgroup"><label for="q">Search</label>
      <div class="search-wrap">${SEARCH_SVG}<input class="input" id="q" type="search" placeholder="Company, domain, city, signal…" value="${esc(f.q)}" autocomplete="off"></div></div>
    <div class="fgroup"><label for="f-country">Territory</label>
      <select class="select" id="f-country"><option value="">All countries</option>${S.facets.countries.map(([c, n]) => `<option value="${esc(c)}" ${f.country === c ? "selected" : ""}>${esc(c)} (${fmt(n)})</option>`).join("")}</select>
      <select class="select" id="f-state" ${stateOpts.length ? "" : "disabled"}><option value="">${f.country ? "All states / regions" : "All states (pick a country)"}</option>${stateOpts.map(([s, n]) => `<option value="${esc(s)}" ${f.state === s ? "selected" : ""}>${esc(s)} (${fmt(n)})</option>`).join("")}</select></div>
    <div class="fgroup"><span class="flabel">Tier <span class="mini">A ≥ 75 · B ≥ 60 · C ≥ 40</span></span>
      <div class="chipset">${TIERS.map((t) => `<button class="toggle" type="button" data-tier="${t}" aria-pressed="${f.tiers.includes(t)}"><span class="tier tier-${t}" style="width:18px;height:16px;font-size:.68rem">${t}</span><span class="n">${fmt(tierCounts[t])}</span></button>`).join("")}</div></div>
    <div class="fgroup"><span class="flabel">Company size</span><div class="chipset">${toggles(S.facets.bands, f.bands, "band")}</div></div>
    <div class="fgroup"><span class="flabel">Industry ${f.industries.length ? `<button class="mini-link" data-act="clear-ind" type="button">clear</button>` : ""}</span>
      <input class="input" id="ind-search" type="search" placeholder="Find industry" value="${esc(indQ)}" autocomplete="off">
      <div class="checklist" id="ind-list">${industries.map(([v, n]) => `<label class="check" data-name="${esc(v.toLowerCase())}"><span><input type="checkbox" data-ind="${esc(v)}" ${f.industries.includes(v) ? "checked" : ""}><span class="lbl">${esc(v)}</span></span><span class="n">${fmt(n)}</span></label>`).join("")}</div></div>
    <div class="fgroup"><span class="flabel">Signals
        <span class="seg" role="group" aria-label="Signal match mode"><button type="button" data-sigmode="any" aria-pressed="${f.sigMode === "any"}">Any</button><button type="button" data-sigmode="all" aria-pressed="${f.sigMode === "all"}">All</button></span></span>
      <input class="input" id="sig-search" type="search" placeholder="Find signal" value="${esc(sigQ)}" autocomplete="off">
      <div class="checklist" id="sig-list">${sigs.map((s) => `<label class="check" data-name="${esc((s.label + " " + CAT_LABEL[s.category]).toLowerCase())}"><span><input type="checkbox" data-sig="${esc(s.id)}" ${f.signals.includes(s.id) ? "checked" : ""}><span class="dot" style="background:${catColor(s.category)}"></span><span class="lbl" title="${esc(CAT_LABEL[s.category])}">${esc(s.label)}</span></span><span class="n">${fmt(s.count)}</span></label>`).join("")}</div></div>
    <div class="fgroup"><span class="flabel">Play</span><div class="chipset">${toggles(S.facets.plays, f.plays, "play", playLabel)}</div></div>
    <div class="fgroup"><label class="check"><input type="checkbox" id="f-ai" ${f.ai ? "checked" : ""}> Has AI brief <span class="n" style="color:var(--muted);font-size:var(--fs-xs)">${fmt(S.accounts.filter((a) => a.$llm).length)}</span></label></div>`;
  el.scrollTop = prevScroll;
  filterChecklist("ind-list", indQ); filterChecklist("sig-list", sigQ);
}
function filterChecklist(id, q) {
  const list = document.getElementById(id); if (!list) return;
  q = q.toLowerCase().trim();
  for (const l of list.children) l.hidden = q ? !l.dataset.name.includes(q) : false;
}
function renderActiveChips() {
  const el = $("#active-chips"); if (!el) return;
  if (S.view === "shortlist") { el.innerHTML = ""; return; }
  const f = S.f, out = [];
  const chip = (label, val, key, v = "") => out.push(`<span class="achip">${esc(label)}: <b>${esc(val)}</b><button type="button" data-rm="${key}" data-v="${esc(v)}" aria-label="Remove ${esc(label)} ${esc(val)}">×</button></span>`);
  if (f.q) chip("Search", f.q, "q");
  if (f.country) chip("Country", f.country, "country");
  if (f.state) chip("State", f.state, "state");
  f.tiers.forEach((t) => chip("Tier", t, "tiers", t));
  f.bands.forEach((b) => chip("Size", b, "bands", b));
  f.industries.forEach((i) => chip("Industry", i, "industries", i));
  f.signals.forEach((s) => chip(f.sigMode === "all" ? "Signal (all)" : "Signal", S.sigCatalog.get(s)?.label || humanize(s), "signals", s));
  f.plays.forEach((p) => chip("Play", playLabel(p), "plays", p));
  if (f.ai) chip("AI brief", "yes", "ai");
  if (out.length > 1) out.push(`<button class="mini-link" data-act="clear-filters" type="button">Clear all</button>`);
  el.innerHTML = out.join("");
}
function onFiltersChanged({ rerenderFilters = true } = {}) {
  if (rerenderFilters) renderFilters();
  refreshResults(true);
  writeHash();
}
function updateExportLabel() {
  const l = $("#export-label"); if (!l) return;
  const sel = S.rows.filter((a) => S.selected.has(a.id)).length;
  l.textContent = sel ? `Export ${fmt(sel)} selected` : `Export ${fmt(S.rows.length)}`;
  const b = $("#export-btn"); if (b) b.disabled = !S.rows.length;
}
function openFiltersSheet() { const f = $("#filters"); if (f) { f.classList.add("open"); $("#scrim").hidden = false; } }
function closeFiltersSheet() { const f = $("#filters"); if (f && f.classList.contains("open")) { f.classList.remove("open"); if (!S.openId) $("#scrim").hidden = true; } }

/* ---------------------------- drawer ---------------------------- */
function openDrawer(id) {
  const a = S.byId.get(id);
  if (!a) return;
  S.openId = id;
  const d = $("#drawer");
  d.innerHTML = drawerHtml(a);
  d.classList.add("open");
  d.setAttribute("aria-hidden", "false");
  $("#scrim").hidden = false;
  d.scrollTop = 0;
  d.focus({ preventScroll: true });
  for (const r of document.querySelectorAll(".row.is-open")) r.classList.remove("is-open");
  const r = document.querySelector(`.row[data-id="${CSS.escape(id)}"]`); if (r) r.classList.add("is-open");
  writeHash();
}
function closeDrawer() {
  if (!S.openId) return;
  S.openId = null;
  const d = $("#drawer");
  d.classList.remove("open");
  d.setAttribute("aria-hidden", "true");
  if (!$("#filters")?.classList.contains("open")) $("#scrim").hidden = true;
  for (const r of document.querySelectorAll(".row.is-open")) r.classList.remove("is-open");
  writeHash();
}
function drawerHtml(a) {
  const t = S.tuner, wi = t.wi / 100, wf = 1 - wi;
  const starred = S.shortlist.has(a.id);
  const loc = [a.city, a.state, a.country].filter(Boolean).join(", ");
  const site = a.website || (a.domain ? `https://${a.domain}` : null);
  // fit breakdown
  const fbEntries = Object.entries(a.$fb);
  const fbMax = Math.max(35, ...fbEntries.map(([, v]) => v));
  const offB = new Set(t.offBands), offC = new Set(t.offCountries);
  const fbRows = fbEntries.length ? fbEntries.map(([k, v]) => {
    const zeroed = S.tuned && ((k === "size" && offB.has(a.$band)) || (k === "geo" && a.country && offC.has(a.country)));
    return `<div class="bd-row${v === 0 || zeroed ? " zero" : ""}"><span class="k">${esc(FIT_LABEL[k] || humanize(k))}</span><span class="bd-track"><i style="width:${zeroed ? 0 : (v / fbMax) * 100}%"></i></span><span class="v">${zeroed ? "0*" : fmt(Math.round(v))}</span></div>`;
  }).join("") : `<p class="desc">No fit breakdown in data.</p>`;
  // intent breakdown (always list the six categories)
  const ibKeys = [...new Set([...CATS, ...Object.keys(a.$ib)])];
  const ibVals = ibKeys.map((k) => [k, (a.$ib[k] ?? 0) * (S.tuned ? (t.mult[k] ?? 1) : 1)]);
  const ibMax = Math.max(30, ...ibVals.map(([, v]) => v));
  const ibRows = ibVals.map(([k, v]) => `<div class="bd-row${v === 0 ? " zero" : ""}"><span class="k"><span class="dot" style="background:${catColor(k)}"></span>${esc(CAT_LABEL[k] || humanize(k))}</span><span class="bd-track"><i style="width:${(v / ibMax) * 100}%;background:${catColor(k)}"></i></span><span class="v">${fmt(Math.round(v))}</span></div>`).join("");
  const sigs = a.signals.length ? a.signals.map((s) => `<div class="sig"><span class="dot" style="background:${catColor(s.category)}"></span>
      <span><span class="t">${esc(s.label)}</span> <span class="cat">· ${esc(CAT_LABEL[s.category])}</span></span><span class="w">+${fmt(s.weight)}</span>
      ${s.evidence ? `<span class="ev">${esc(s.evidence)}</span>` : `<span class="ev">No evidence text supplied</span>`}</div>`).join("")
    : `<p class="desc">No buying signals detected. Score comes from ICP fit only.</p>`;
  const tierNote = S.tuned && a.$tier !== a.$tier0 ? ` <span class="moved${TIERS.indexOf(a.$tier) > TIERS.indexOf(a.$tier0) ? " down" : ""}">was ${a.$tier0} (${a.$pri0})</span>` : "";
  const play = PLAYS[a.play];
  return `
  <div class="d-head">
    <div><h2>${esc(a.name)}</h2></div>
    <div class="d-actions">
      <button class="star" data-act="star" data-id="${esc(a.id)}" type="button" aria-pressed="${starred}" aria-label="${starred ? "Remove from" : "Add to"} shortlist" style="width:34px;height:34px">${STAR_SVG}</button>
      <button class="icon-btn" data-act="close-drawer" type="button" aria-label="Close detail"><svg viewBox="0 0 20 20" aria-hidden="true"><path d="M5 5l10 10M15 5L5 15" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/></svg></button>
    </div>
    <div class="d-sub">${a.domain ? `<span>${site ? `<a href="${esc(site)}" target="_blank" rel="noopener">${esc(a.domain)}</a>` : esc(a.domain)}</span>` : ""}<span>${esc(a.segment || a.industry || "")}</span>${loc ? `<span>${esc(loc)}</span>` : ""}<span class="num">Rank #${fmt(a.$rank)}</span></div>
  </div>
  <div class="d-body">
    <section class="d-sec">
      <h3>Why this score</h3>
      <div class="score-hero">
        <div class="score-big"><span class="n">${a.$pri}</span><span class="tier tier-${a.$tier} lg">${a.$tier}</span></div>
        <div class="formula">priority = <b>${wf.toFixed(2)}</b> × fit <b>${a.$fit}</b> + <b>${wi.toFixed(2)}</b> × intent <b>${a.$intent}</b>${tierNote}<br>${S.tuned ? "Custom ICP weights from the tuner." : `Pipeline weights · ${esc(S.meta.scoring_version || "rules")}`}</div>
      </div>
      <div class="bd-grid">
        <div class="bd"><h4>ICP fit <span>${a.$fit}/100</span></h4>${fbRows}${S.tuned && fbRows.includes("0*") ? '<p class="desc" style="font-size:var(--fs-xs)">* zeroed: outside your target sizes or countries</p>' : ""}</div>
        <div class="bd"><h4>Intent <span>${a.$intent}/100</span></h4>${ibRows}</div>
      </div>
    </section>
    <section class="d-sec"><h3>Signals <span>${a.signals.length} detected</span></h3><div class="sig-list">${sigs}</div></section>
    <section class="d-sec"><h3>Recommended approach</h3>
      <div class="approach">
        <div class="box"><span class="eyebrow">Persona</span><span class="v">${esc(a.persona || a.$llm?.outreach?.persona || "Not specified")}</span><span class="d">Who to contact first</span></div>
        <div class="box"><span class="eyebrow">Play</span><span class="v play-text">${esc(playLabel(a.play))}</span><span class="d">${esc(play?.desc || "")}</span></div>
      </div>
    </section>
    ${aiSection(a)}
    <section class="d-sec"><h3>Company facts</h3>
      ${a.description ? `<p class="desc">${esc(a.description)}</p>` : ""}
      <dl class="kv">
        <dt>Industry</dt><dd>${esc(a.industry || "—")}${a.sub_industry ? ` · ${esc(a.sub_industry)}` : ""}</dd>
        <dt>Employees</dt><dd class="num">${a.employee_count != null ? fmt(a.employee_count) : "Unknown"} <span style="color:var(--muted)">(${esc(a.$band)})</span></dd>
        <dt>Revenue</dt><dd class="num">${money(a.revenue_usd)}${num(a.revenue_usd) != null ? " USD" : ""}</dd>
        <dt>Founded</dt><dd class="num">${esc(a.founded_year ?? "—")}</dd>
        <dt>Location</dt><dd>${esc(loc || "—")}</dd>
        ${a.employee_growth_pct != null ? `<dt>Headcount growth</dt><dd class="num">${esc(a.employee_growth_pct)}%</dd>` : ""}
        ${a.job_postings != null ? `<dt>Open roles</dt><dd class="num">${fmt(a.job_postings)}</dd>` : ""}
        <dt>Links</dt><dd>${[site ? `<a href="${esc(site)}" target="_blank" rel="noopener">Website</a>` : "", a.linkedin_url ? `<a href="${esc(a.linkedin_url)}" target="_blank" rel="noopener">LinkedIn</a>` : ""].filter(Boolean).join(" · ") || "—"}</dd>
        <dt>Tech stack</dt><dd>${Array.isArray(a.technologies) && a.technologies.length ? `<span class="techs">${a.technologies.map((x) => `<span>${esc(x)}</span>`).join("")}</span>` : "—"}</dd>
      </dl>
    </section>
  </div>`;
}
function aiSection(a) {
  const L = a.$llm, m = S.meta.llm || {};
  if (!L) {
    const enriched = S.accounts.filter((x) => x.$llm).length;
    const topN = num(m.top_n) ?? enriched;
    return `<section class="d-sec"><h3>AI assessment</h3><div class="not-ai">
      <strong>Not AI-enriched (outside top ${fmt(topN)}). Rules-only score.</strong>
      <span>To keep model cost predictable, only the highest-priority accounts get an AI need assessment, brief and outreach draft${num(m.calls) != null ? ` (${fmt(m.calls)} model calls${num(m.cost_usd) != null ? `, $${Number(m.cost_usd).toFixed(2)}` : ""} this run)` : ""}. Everything above comes from deterministic rules and quoted field evidence.</span></div></section>`;
  }
  const lvl = String(L.need_level || "").toLowerCase();
  const bars = { low: 1, medium: 2, high: 3 }[lvl] || 0;
  const conf = num(L.confidence);
  const briefHtml = briefToHtml(L.brief);
  const o = L.outreach && typeof L.outreach === "object" ? L.outreach : null;
  const pv = L.prompt_versions && typeof L.prompt_versions === "object" ? Object.entries(L.prompt_versions) : [];
  const mailto = o ? `mailto:?subject=${encodeURIComponent(o.subject || "")}&body=${encodeURIComponent(o.body || "")}` : "";
  return `<section class="d-sec"><h3>AI assessment ${m.synthetic ? '<span style="text-transform:none;letter-spacing:0;font-weight:400">Synthetic sample text</span>' : ""}</h3>
    <div class="ai-row">
      ${lvl ? `<span class="need"><span class="need-bars" aria-hidden="true"><i class="${bars >= 1 ? "on" : ""}"></i><i class="${bars >= 2 ? "on" : ""}"></i><i class="${bars >= 3 ? "on" : ""}"></i></span>${esc(lvl)} need</span>` : ""}
      ${conf != null ? `<span class="conf">Confidence <span class="track"><i style="width:${clamp(conf, 0, 1) * 100}%"></i></span><span class="num">${Math.round(conf * 100)}%</span></span>` : ""}
    </div>
    ${L.rationale ? `<p class="rationale">${esc(L.rationale)}</p>` : ""}
    ${briefHtml ? `<div><div class="eyebrow" style="margin-bottom:6px">Account brief</div>${briefHtml}</div>` : ""}
    ${o ? `<div class="outreach">
      <div class="o-head"><span class="lab">Outreach draft${o.persona ? ` · for ${esc(o.persona)}` : ""}</span><strong>${esc(o.subject || "(no subject)")}</strong></div>
      <pre id="o-body">${esc(o.body || "")}</pre>
      <div class="o-actions">
        <button class="btn" data-act="copy-email" data-id="${esc(a.id)}" type="button">Copy email</button>
        <button class="btn ghost" data-act="copy-body" data-id="${esc(a.id)}" type="button">Copy body only</button>
        <a class="btn ghost" href="${esc(mailto)}" target="_blank" rel="noopener">Open in mail</a>
      </div></div>` : ""}
    <div class="trace"><span>model <b>${esc(L.model || "unknown")}</b></span>${pv.map(([k, v]) => `<span>${esc(k)} <b>${esc(v)}</b></span>`).join("")}</div>
  </section>`;
}
function briefToHtml(brief) {
  if (!brief) return "";
  if (Array.isArray(brief)) brief = brief.join("\n");
  const lines = String(brief).split(/\n+/).map((l) => l.trim()).filter(Boolean);
  const bullets = lines.filter((l) => /^([-*•]|\d+[.)])\s+/.test(l));
  if (bullets.length >= Math.max(1, lines.length - 1)) {
    return `<ul class="brief">${lines.map((l) => `<li>${esc(l.replace(/^([-*•]|\d+[.)])\s+/, ""))}</li>`).join("")}</ul>`;
  }
  return lines.map((l) => `<p class="rationale">${esc(l)}</p>`).join("");
}

/* ---------------------------- market view ---------------------------- */
function renderMarket() {
  computeRowsForMarket();
  const rows = S.marketRows, n = rows.length;
  const nA = rows.filter((a) => a.$tier === "A").length;
  const avg = n ? Math.round(rows.reduce((s, a) => s + a.$pri, 0) / n) : 0;
  const ai = rows.filter((a) => a.$llm).length;
  const nf = activeFilterCount();
  $("#main").innerHTML = `<div class="page">
    <div class="page-head"><div><h1>Market overview</h1>
      <p>${nf ? `Showing ${fmt(n)} accounts that match your ${nf} active filter${nf > 1 ? "s" : ""}.` : `All ${fmt(n)} scored accounts.`} Click any bar or cell to open those accounts in Today’s priorities.</p></div>
      <span class="spacer"></span>${nf ? `<button class="btn" data-act="clear-filters" type="button">Clear filters</button>` : ""}</div>
    <div class="tiles">
      <div class="tile"><span class="l">Accounts in view</span><span class="v">${fmt(n)}</span><span class="s">of ${fmt(S.accounts.length)} scored</span></div>
      <div class="tile"><span class="l">Tier A (priority ≥ 75)</span><span class="v">${fmt(nA)}</span><span class="s">${pct(nA, n)}% of view${S.tuned ? " · custom ICP" : ""}</span></div>
      <div class="tile"><span class="l">Average priority</span><span class="v">${avg}</span><span class="s">0–100 scale</span></div>
      <div class="tile"><span class="l">AI-enriched</span><span class="v">${fmt(ai)}</span><span class="s">${S.meta.llm?.synthetic ? "synthetic sample text" : "need, brief and outreach"}</span></div>
    </div>
    <div class="panels">
      <div class="panel"><div class="panel-head"><div><h2>Tier distribution</h2><p>Accounts per tier on priority score, and the play each tier mix calls for</p></div>${tierLegend()}</div><div class="hbars">${tierBars(rows)}</div>
        <div class="eyebrow" style="margin-top:4px">Recommended play</div><div class="hbars">${playBars(rows)}</div></div>
      <div class="panel"><div class="panel-head"><div><h2>Territory</h2><p>Accounts by country, split by tier</p></div>${tierLegend()}</div><div class="hbars">${territoryBars(rows, "country")}</div>
        ${statesBlock(rows)}</div>
      <div class="panel wide"><div class="panel-head"><div><h2>Segment heatmap</h2><p>Industry × company size. ${S.heatMetric === "ab" ? "Cell = number of tier A + B accounts." : "Cell = average priority score."}</p></div>
        <span class="seg" role="group" aria-label="Heatmap metric"><button type="button" data-heat="ab" aria-pressed="${S.heatMetric === "ab"}">A + B count</button><button type="button" data-heat="avg" aria-pressed="${S.heatMetric === "avg"}">Avg priority</button></span></div>
        ${heatmap(rows)}</div>
      <div class="panel wide"><div class="panel-head"><div><h2>Signal coverage</h2><p>Share of accounts in view showing each signal (top 14). Gridlines at 25%, 50%, 75%.</p></div>
        <div class="legend">${CATS.map((c) => `<span><span class="dot" style="background:${catColor(c)}"></span>${CAT_LABEL[c]}</span>`).join("")}</div></div>
        <div class="hbars">${signalBars(rows)}</div></div>
    </div></div>`;
}
function computeRowsForMarket() { S.marketRows = S.accounts.filter((a) => matches(a, S.f)); }
function tierLegend() { return `<div class="legend">${TIERS.map((t) => `<span><span class="dot" style="background:var(--tier-${t.toLowerCase()})"></span>${t}</span>`).join("")}</div>`; }
function tierBars(rows) {
  const c = Object.fromEntries(TIERS.map((t) => [t, 0])); for (const a of rows) c[a.$tier]++;
  const max = Math.max(1, ...Object.values(c));
  return TIERS.map((t) => `<button class="hbar" type="button" data-go-tier="${t}" data-tip="Tier ${t}: ${fmt(c[t])} accounts (${pct(c[t], rows.length)}%)\nClick to filter">
    <span class="k"><span class="tier tier-${t}">${t}</span><span>${t === "D" ? "below 40" : `≥ ${TIER_MIN[t]}`}</span></span>
    <span class="track"><i style="width:${(c[t] / max) * 100}%;background:var(--tier-${t.toLowerCase()})"></i></span>
    <span class="v">${fmt(c[t])} · ${pct(c[t], rows.length)}%</span></button>`).join("");
}
function playBars(rows) {
  const groups = groupBy(rows, (a) => a.play || "nurture");
  if (!groups.length) return "";
  return stackedBars(groups.map((g) => ({ ...g, label: playLabel(g.key) })), (g) => `data-go-play="${esc(g.key)}"`, (g) => `${playLabel(g.key)}: ${PLAYS[g.key]?.desc || ""}`);
}
function stackedBars(groups, attrFn, tipPrefix) {
  const max = Math.max(1, ...groups.map((g) => g.total));
  return groups.map((g) => `<button class="hbar" type="button" ${attrFn(g)} data-tip="${esc(tipPrefix(g))}\n${TIERS.map((t) => `${t}: ${fmt(g.t[t])}`).join(" · ")}\nClick to filter">
    <span class="k"><span>${esc(g.label || g.key)}</span></span>
    <span class="track" style="width:${(g.total / max) * 100}%">${TIERS.filter((t) => g.t[t]).map((t) => `<i style="flex:${g.t[t]} 1 0;background:var(--tier-${t.toLowerCase()})"></i>`).join("")}</span>
    <span class="v">${fmt(g.total)} · ${fmt(g.t.A)} A</span></button>`).join("");
}
function groupBy(rows, keyFn) {
  const m = new Map();
  for (const a of rows) { const k = keyFn(a); if (!k) continue; let g = m.get(k); if (!g) { g = { key: k, total: 0, t: { A: 0, B: 0, C: 0, D: 0 } }; m.set(k, g); } g.total++; g.t[a.$tier]++; }
  return [...m.values()].sort((a, b) => b.total - a.total);
}
function territoryBars(rows) {
  const groups = groupBy(rows, (a) => a.country || "Unknown").slice(0, 8);
  if (!groups.length) return `<p class="desc">No accounts in view.</p>`;
  return stackedBars(groups, (g) => (g.key === "Unknown" ? "disabled" : `data-go-country="${esc(g.key)}"`), (g) => g.key);
}
function statesBlock(rows) {
  const country = S.f.country || groupBy(rows, (a) => a.country)[0]?.key;
  if (!country) return "";
  const groups = groupBy(rows.filter((a) => a.country === country), (a) => a.state).slice(0, 10);
  if (groups.length < 2) return "";
  return `<div class="eyebrow" style="margin-top:4px">States and regions · ${esc(country)}</div><div class="hbars">${stackedBars(groups, (g) => `data-go-country="${esc(country)}" data-go-state="${esc(g.key)}"`, (g) => `${g.key}, ${country}`)}</div>`;
}
function heatmap(rows) {
  const inds = groupBy(rows, (a) => a.industry).slice(0, 14).map((g) => g.key);
  const bands = BAND_ORDER.filter((b) => rows.some((a) => a.$band === b));
  if (!inds.length || !bands.length) return `<p class="desc">No accounts in view.</p>`;
  const cells = new Map();
  for (const a of rows) {
    const k = `${a.industry}|${a.$band}`;
    let c = cells.get(k); if (!c) { c = { n: 0, ab: 0, sum: 0 }; cells.set(k, c); }
    c.n++; c.sum += a.$pri; if (a.$tier === "A" || a.$tier === "B") c.ab++;
  }
  const val = (c) => (!c ? null : S.heatMetric === "ab" ? c.ab : Math.round(c.sum / c.n));
  const vals = [...cells.values()].map(val).filter((v) => v !== null);
  const lo = S.heatMetric === "ab" ? 0 : Math.min(...vals), hi = Math.max(1, ...vals);
  let html = `<div class="heat-wrap"><div class="heat" style="grid-template-columns:minmax(120px,190px) repeat(${bands.length}, minmax(52px,1fr))"><span></span>${bands.map((b) => `<span class="hh">${esc(b)}</span>`).join("")}`;
  for (const ind of inds) {
    html += `<span class="rh" title="${esc(ind)}">${esc(ind)}</span>`;
    for (const b of bands) {
      const c = cells.get(`${ind}|${b}`);
      const v = val(c);
      if (!c || (S.heatMetric === "ab" && v === 0)) {
        html += `<button class="c empty-c" type="button" ${c ? `data-go-ind="${esc(ind)}" data-go-band="${esc(b)}"` : "disabled"} data-tip="${esc(ind)} · ${esc(b)}\n${c ? `${fmt(c.n)} accounts, none in tier A or B` : "No accounts"}">${c ? "0" : "·"}</button>`;
        continue;
      }
      const tnorm = hi === lo ? 1 : (v - lo) / (hi - lo);
      const p = Math.round(14 + tnorm * 86);
      html += `<button class="c" type="button" data-go-ind="${esc(ind)}" data-go-band="${esc(b)}" style="background:color-mix(in oklab, var(--accent) ${p}%, var(--heat-0));color:${p > 56 ? "var(--accent-ink)" : "var(--ink)"}" data-tip="${esc(ind)} · ${esc(b)} staff\n${fmt(c.ab)} of ${fmt(c.n)} accounts in tier A/B · avg priority ${Math.round(c.sum / c.n)}\nClick to filter">${v}</button>`;
    }
  }
  html += `</div></div><div class="scale"><span>${S.heatMetric === "ab" ? "fewer" : lo}</span><span class="ramp" style="background:linear-gradient(90deg, color-mix(in oklab, var(--accent) 14%, var(--heat-0)), var(--accent))"></span><span>${S.heatMetric === "ab" ? `${hi} A/B accounts` : hi}</span></div>`;
  return html;
}
function signalBars(rows) {
  const counts = new Map();
  for (const a of rows) for (const id of a.$sigIds) counts.set(id, (counts.get(id) || 0) + 1);
  const top = [...counts].sort((a, b) => b[1] - a[1]).slice(0, 14);
  if (!top.length) return `<p class="desc">No signals in view.</p>`;
  return top.map(([id, n]) => {
    const s = S.sigCatalog.get(id) || { label: humanize(id), category: "other" };
    return `<button class="hbar" type="button" data-go-sig="${esc(id)}" data-tip="${esc(s.label)}\n${esc(CAT_LABEL[s.category])} · ${fmt(n)} accounts (${pct(n, rows.length)}%)\nClick to filter">
      <span class="k"><span class="dot" style="background:${catColor(s.category)}"></span><span>${esc(s.label)}</span></span>
      <span class="track"><span class="grid-bg"></span><i style="width:${(n / rows.length) * 100}%;background:${catColor(s.category)}"></i></span>
      <span class="v">${pct(n, rows.length)}% · ${fmt(n)}</span></button>`;
  }).join("");
}
function goList(patch) {
  S.f = { ...S.f, ...patch };
  S.view = "list";
  render();
  writeHash();
}

/* ---------------------------- tuner view ---------------------------- */
function renderTuner() {
  const t = S.tuner;
  const countries = S.facets.countries.slice(0, 30);
  $("#main").innerHTML = `<div class="page">
    <div class="page-head"><div><h1>ICP tuner</h1>
      <p>Re-weight what matters for your territory and see how the ranking moves. Priority = fit weight × fit + intent weight × intent. Intent is the sum of signal points per category × your multiplier, capped at 100. Settings stay in this browser and apply across every view.</p></div>
      <span class="spacer"></span><button class="btn" data-act="reset-tuner" type="button">Reset to pipeline defaults</button></div>
    <div class="tuner">
      <div class="panel" id="tuner-controls">
        <div class="slider-row"><label for="t-wi">Fit vs intent <span class="val" id="t-wi-val"></span></label>
          <input type="range" id="t-wi" min="0" max="100" step="5" value="${t.wi}">
          <div class="weight-ends" style="grid-column:1/-1"><span>All fit (who they are)</span><span>All intent (what is happening)</span></div></div>
        <div class="eyebrow">Signal category multipliers</div>
        ${CATS.map((c) => `<div class="slider-row"><label for="t-m-${c}"><span style="display:flex;gap:6px;align-items:center"><span class="dot" style="background:${catColor(c)}"></span>${CAT_LABEL[c]}</span><span class="val" id="t-m-${c}-val">${(t.mult[c] ?? 1).toFixed(1)}×</span></label>
          <input type="range" id="t-m-${c}" data-mult="${c}" min="0" max="3" step="0.1" value="${t.mult[c] ?? 1}"></div>`).join("")}
        <div class="fgroup"><span class="flabel">Target company sizes <span class="mini">off = size fit set to 0</span></span>
          <div class="chipset">${S.facets.bands.map(([b, n]) => `<button class="toggle" type="button" data-tband="${esc(b)}" aria-pressed="${!t.offBands.includes(b)}">${esc(b)} <span class="n">${fmt(n)}</span></button>`).join("")}</div></div>
        <div class="fgroup"><span class="flabel">Target countries <span class="mini">off = territory fit set to 0</span></span>
          <div class="chipset">${countries.map(([c, n]) => `<button class="toggle" type="button" data-tcountry="${esc(c)}" aria-pressed="${!t.offCountries.includes(c)}">${esc(c)} <span class="n">${fmt(n)}</span></button>`).join("")}</div></div>
      </div>
      <div class="panel" id="tuner-impact"></div>
    </div></div>`;
  updateTunerLabels();
  renderTunerImpact();
}
function updateTunerLabels() {
  const t = S.tuner;
  const v = $("#t-wi-val"); if (v) v.textContent = `fit ${100 - t.wi}% · intent ${t.wi}%`;
  for (const c of CATS) { const el = document.getElementById(`t-m-${c}-val`); if (el) el.textContent = `${(t.mult[c] ?? 1).toFixed(1)}×`; }
}
function renderTunerImpact() {
  const el = $("#tuner-impact"); if (!el) return;
  const base = Object.fromEntries(TIERS.map((x) => [x, 0])), now = Object.fromEntries(TIERS.map((x) => [x, 0]));
  for (const a of S.accounts) { base[a.$tier0]++; now[a.$tier]++; }
  const d = now.A - base.A;
  const entered = S.accounts.filter((a) => a.$tier === "A" && a.$tier0 !== "A").sort((x, y) => (y.$pri - y.$pri0) - (x.$pri - x.$pri0) || y.$pri - x.$pri);
  const left = S.accounts.filter((a) => a.$tier0 === "A" && a.$tier !== "A").length;
  const max = Math.max(1, ...TIERS.map((x) => Math.max(base[x], now[x])));
  let movers = entered.slice(0, 10), moversTitle = "Accounts moving into tier A";
  if (!movers.length) { movers = [...S.accounts].filter((a) => a.$rank < a.$rank0).sort((x, y) => (y.$rank0 - y.$rank) - (x.$rank0 - x.$rank)).slice(0, 10); moversTitle = "Biggest rank gains"; }
  el.innerHTML = `
    <div class="panel-head"><div><h2>Impact on your book</h2><p>Compared with the pipeline’s default scoring (${esc(S.meta.scoring_version || "rules")})</p></div></div>
    <div class="big-delta"><span class="n">${fmt(now.A)}</span><span class="from">tier A accounts, was ${fmt(base.A)}</span>
      <span class="delta ${d > 0 ? "up" : d < 0 ? "down" : "flat"}">${d > 0 ? "+" : ""}${fmt(d)}</span></div>
    <p class="desc" style="margin:0">${fmt(entered.length)} account${entered.length === 1 ? "" : "s"} move into tier A, ${fmt(left)} drop out.</p>
    <div class="cmp">${TIERS.map((x) => `<div class="cmp-row"><span class="tier tier-${x}">${x}</span>
      <span class="cmp-bars" data-tip="Tier ${x}: ${fmt(base[x])} by default, ${fmt(now[x])} with your ICP"><i class="base" style="width:${(base[x] / max) * 100}%"></i><i style="width:${(now[x] / max) * 100}%;background:var(--tier-${x.toLowerCase()})"></i></span>
      <span class="v">${fmt(base[x])} → <b>${fmt(now[x])}</b></span></div>`).join("")}
      <div class="legend"><span><span class="dot" style="background:var(--surface-3)"></span>Pipeline default</span><span><span class="dot" style="background:var(--tier-a)"></span>Your ICP</span></div></div>
    <div><div class="eyebrow" style="margin:6px 0">${moversTitle}</div>
      ${movers.length ? `<div class="table-scroll"><table class="movers"><thead><tr><th>Account</th><th>Segment</th><th class="r">Priority</th><th class="r">Tier</th></tr></thead><tbody>
      ${movers.map((a) => `<tr data-open="${esc(a.id)}"><td><strong>${esc(a.name)}</strong></td><td>${esc(a.segment || "—")}</td><td class="r">${a.$pri0} → <b>${a.$pri}</b></td><td class="r">${a.$tier0} → <span class="tier tier-${a.$tier}">${a.$tier}</span></td></tr>`).join("")}
      </tbody></table></div>` : `<p class="desc">No movement yet. Move a slider to see which accounts climb.</p>`}</div>
    <div><button class="btn primary" data-act="view-tuned" type="button">View re-ranked priorities</button></div>`;
}
let tunerRaf = 0;
function onTunerChanged() {
  saveTuner();
  updateTunerLabels();
  if (tunerRaf) return;
  tunerRaf = requestAnimationFrame(() => { tunerRaf = 0; rescore(); renderTunerImpact(); });
}

/* ---------------------------- export ---------------------------- */
const CSV_COLS = ["id", "name", "domain", "website", "linkedin_url", "industry", "segment", "country", "state", "city", "employee_count", "size_band",
  "priority_score", "tier", "fit_score", "intent_score", "pipeline_priority_score", "pipeline_tier", "top_signals", "signal_evidence", "persona", "play",
  "ai_need_level", "ai_confidence", "outreach_subject", "outreach_body", "shortlisted"];
function csvFor(rows) {
  const q = (v) => { const s = v === null || v === undefined ? "" : String(v); return /[",\n\r]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s; };
  const lines = [CSV_COLS.join(",")];
  for (const a of rows) {
    const o = a.$llm?.outreach || {};
    lines.push([a.id, a.name, a.domain, a.website, a.linkedin_url, a.industry, a.segment, a.country, a.state, a.city, a.employee_count, a.$band,
      a.$pri, a.$tier, a.$fit, a.$intent, a.$pri0, a.$tier0, a.signals.slice(0, 3).map((s) => s.label).join("; "), a.signals.slice(0, 3).map((s) => s.evidence).join("; "),
      a.persona, a.play, a.$llm?.need_level, a.$llm?.confidence, o.subject, o.body, S.shortlist.has(a.id) ? "yes" : "no"].map(q).join(","));
  }
  return lines.join("\r\n");
}
function openExport() {
  const sel = S.rows.filter((a) => S.selected.has(a.id));
  const rows = sel.length ? sel : S.rows;
  const what = sel.length ? `${fmt(rows.length)} selected account${rows.length === 1 ? "" : "s"}` : S.view === "shortlist" ? `your ${fmt(rows.length)} shortlisted accounts` : `all ${fmt(rows.length)} accounts in the current view`;
  const csv = csvFor(rows);
  S.exportCsv = csv;
  const m = $("#modal");
  m.innerHTML = `<div class="modal-card"><h2>Export for CRM</h2>
    <p>CSV of ${what}, in the current sort order${S.tuned ? ", scored with your custom ICP" : ""}. Columns:</p>
    <div class="cols">${CSV_COLS.join(", ")}</div>
    <p>If your browser blocks the download, copy the CSV and paste it into a file or straight into a spreadsheet.</p>
    <div class="actions"><button class="btn ghost" data-act="close-modal" type="button">Cancel</button>
      <button class="btn" data-act="copy-csv" type="button">Copy CSV</button>
      <button class="btn primary" data-act="download-csv" type="button">${DL_SVG} Download .csv</button></div></div>`;
  m.hidden = false;
  m.querySelector(".btn.primary").focus();
}
function downloadCsv() {
  try {
    const blob = new Blob(["﻿" + S.exportCsv], { type: "text/csv;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url; a.download = `perimeter-radar-${new Date().toISOString().slice(0, 10)}.csv`;
    document.body.appendChild(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 4000);
    toast("Download started. If nothing happens, use Copy CSV.");
  } catch { toast("Download blocked here. Use Copy CSV instead."); }
}

/* ---------------------------- clipboard, toast, tooltip ---------------------------- */
function copyText(text, okMsg) {
  const fallback = () => {
    const ta = document.createElement("textarea");
    ta.value = text; ta.setAttribute("readonly", ""); ta.style.position = "fixed"; ta.style.opacity = "0";
    document.body.appendChild(ta); ta.select();
    let ok = false; try { ok = document.execCommand("copy"); } catch { ok = false; }
    ta.remove();
    toast(ok ? okMsg : "Copy is blocked here. Select the text and copy it manually.");
  };
  try {
    if (navigator.clipboard?.writeText) navigator.clipboard.writeText(text).then(() => toast(okMsg), fallback);
    else fallback();
  } catch { fallback(); }
}
let toastTimer = 0;
function toast(msg) {
  const t = $("#toast"); t.textContent = msg; t.hidden = false;
  clearTimeout(toastTimer); toastTimer = setTimeout(() => { t.hidden = true; }, 2600);
}
function initTooltip() {
  const tip = $("#tip");
  let cur = null;
  document.addEventListener("pointerover", (e) => {
    const el = e.target.closest?.("[data-tip]");
    if (!el || e.pointerType === "touch") { if (cur && !el) { tip.hidden = true; cur = null; } return; }
    cur = el; tip.textContent = el.dataset.tip; tip.hidden = false; place(e);
  });
  document.addEventListener("pointermove", (e) => { if (cur) place(e); }, { passive: true });
  document.addEventListener("pointerout", (e) => { if (cur && !cur.contains(e.relatedTarget)) { tip.hidden = true; cur = null; } });
  document.addEventListener("scroll", () => { tip.hidden = true; cur = null; }, true);
  function place(e) {
    const r = tip.getBoundingClientRect();
    let x = e.clientX + 14, y = e.clientY + 16;
    if (x + r.width > innerWidth - 8) x = e.clientX - r.width - 14;
    if (y + r.height > innerHeight - 8) y = e.clientY - r.height - 12;
    tip.style.left = `${Math.max(8, x)}px`; tip.style.top = `${Math.max(8, y)}px`;
  }
}

/* ---------------------------- theme ---------------------------- */
function applyTheme(mode) {
  const root = document.documentElement;
  if (mode === "light" || mode === "dark") root.setAttribute("data-theme", mode); else root.removeAttribute("data-theme");
  const b = $("#theme-btn"); if (b) { b.title = `Theme: ${mode}`; b.setAttribute("aria-label", `Theme: ${mode}. Click to change.`); }
}

/* ---------------------------- events ---------------------------- */
function toggleStar(id) {
  if (S.shortlist.has(id)) S.shortlist.delete(id); else S.shortlist.add(id);
  lsSet(LS.shortlist, [...S.shortlist]);
  updateShortlistCount();
  const on = S.shortlist.has(id);
  for (const b of document.querySelectorAll(`[data-act="star"]`)) {
    const rid = b.dataset.id || b.closest(".row")?.dataset.id;
    if (rid === id) { b.setAttribute("aria-pressed", String(on)); b.setAttribute("aria-label", `${on ? "Remove from" : "Add to"} shortlist`); }
  }
  toast(on ? "Added to shortlist" : "Removed from shortlist");
  if (S.view === "shortlist") refreshResults(false);
}
function bindEvents() {
  document.addEventListener("click", (e) => {
    const t = e.target;
    const tab = t.closest(".tab"); if (tab) { setView(tab.dataset.view); return; }
    if (t.closest("#icp-pill")) { setView("tuner"); return; }
    if (t.closest("#theme-btn")) {
      const order = ["system", "light", "dark"]; const cur = lsGet(LS.theme, "system");
      const next = order[(order.indexOf(cur) + 1) % 3]; lsSet(LS.theme, next); applyTheme(next); toast(`Theme: ${next}`); return;
    }
    if (t.closest("#scrim")) { closeDrawer(); closeFiltersSheet(); return; }
    if (t.closest("#filters-open")) { openFiltersSheet(); return; }
    if (t.closest("#export-btn")) { openExport(); return; }
    if (t.id === "modal") { $("#modal").hidden = true; return; }
    const act = t.closest("[data-act]")?.dataset.act;
    const actEl = t.closest("[data-act]");
    if (act) {
      const id = actEl.dataset.id || actEl.closest(".row")?.dataset.id;
      switch (act) {
        case "star": e.stopPropagation(); toggleStar(id); return;
        case "sel": {
          e.stopPropagation();
          if (actEl.checked) S.selected.add(id); else S.selected.delete(id);
          actEl.closest(".row")?.classList.toggle("is-selected", actEl.checked);
          updateExportLabel(); renderHead(); return;
        }
        case "close-drawer": closeDrawer(); return;
        case "close-filters": closeFiltersSheet(); return;
        case "clear-filters": S.f = defaultFilters(); if (S.view === "market") { render(); writeHash(); } else onFiltersChanged(); return;
        case "clear-ind": S.f.industries = []; onFiltersChanged(); return;
        case "clear-shortlist": S.shortlist.clear(); lsSet(LS.shortlist, []); updateShortlistCount(); render(); toast("Shortlist cleared"); return;
        case "copy-email": { const o = S.byId.get(id)?.$llm?.outreach || {}; copyText(`Subject: ${o.subject || ""}\n\n${o.body || ""}`, "Email copied"); return; }
        case "copy-body": { const o = S.byId.get(id)?.$llm?.outreach || {}; copyText(o.body || "", "Body copied"); return; }
        case "close-modal": $("#modal").hidden = true; return;
        case "copy-csv": copyText(S.exportCsv || "", "CSV copied to clipboard"); return;
        case "download-csv": downloadCsv(); return;
        case "reset-tuner": S.tuner = tunerDefaults(); saveTuner(); rescore(); renderTuner(); toast("Tuner reset to pipeline defaults"); return;
        case "view-tuned": S.sort = { key: "priority", dir: "desc" }; setView("list"); return;
      }
    }
    // sort
    const sortBtn = t.closest("[data-sort]");
    if (sortBtn) {
      const k = sortBtn.dataset.sort;
      S.sort = S.sort.key === k ? { key: k, dir: S.sort.dir === "asc" ? "desc" : "asc" } : { key: k, dir: ["name", "segment", "location", "play", "tier"].includes(k) ? "asc" : "desc" };
      refreshResults(true); writeHash(); return;
    }
    // active chip removal
    const rm = t.closest("[data-rm]");
    if (rm) {
      const k = rm.dataset.rm, v = rm.dataset.v;
      if (Array.isArray(S.f[k])) S.f[k] = S.f[k].filter((x) => x !== v);
      else if (k === "ai") S.f.ai = false;
      else { S.f[k] = ""; if (k === "country") S.f.state = ""; }
      onFiltersChanged(); return;
    }
    // filter toggles
    const tog = t.closest("[data-tier],[data-band],[data-play]");
    if (tog && t.closest("#filters")) {
      const [key, v] = tog.dataset.tier ? ["tiers", tog.dataset.tier] : tog.dataset.band ? ["bands", tog.dataset.band] : ["plays", tog.dataset.play];
      S.f[key] = S.f[key].includes(v) ? S.f[key].filter((x) => x !== v) : [...S.f[key], v];
      onFiltersChanged(); return;
    }
    const sm = t.closest("[data-sigmode]");
    if (sm) { S.f.sigMode = sm.dataset.sigmode; onFiltersChanged(); return; }
    // market drill-downs
    const heatBtn = t.closest("[data-heat]");
    if (heatBtn) { S.heatMetric = heatBtn.dataset.heat; lsSet(LS.heat, S.heatMetric); renderMarket(); return; }
    const go = t.closest("[data-go-tier],[data-go-country],[data-go-ind],[data-go-sig],[data-go-play]");
    if (go) {
      const d = go.dataset;
      if (d.goTier) goList({ tiers: [d.goTier] });
      else if (d.goInd) goList({ industries: [d.goInd], bands: [d.goBand] });
      else if (d.goPlay) goList({ plays: [d.goPlay] });
      else if (d.goSig) goList({ signals: [d.goSig], sigMode: "any" });
      else if (d.goCountry) goList({ country: d.goCountry, state: d.goState || "" });
      return;
    }
    // tuner toggles
    const tb = t.closest("[data-tband],[data-tcountry]");
    if (tb) {
      const isBand = tb.dataset.tband !== undefined;
      const key = isBand ? "offBands" : "offCountries", v = isBand ? tb.dataset.tband : tb.dataset.tcountry;
      const list = S.tuner[key];
      S.tuner[key] = list.includes(v) ? list.filter((x) => x !== v) : [...list, v];
      tb.setAttribute("aria-pressed", String(!S.tuner[key].includes(v)));
      onTunerChanged(); return;
    }
    const mover = t.closest("[data-open]");
    if (mover) { openDrawer(mover.dataset.open); return; }
    // row click → drawer
    const row = t.closest(".row");
    if (row && !t.closest("input,button,a")) { openDrawer(row.dataset.id); return; }
  });
  document.addEventListener("change", (e) => {
    const t = e.target;
    if (t.id === "f-country") { S.f.country = t.value; S.f.state = ""; onFiltersChanged(); }
    else if (t.id === "f-state") { S.f.state = t.value; onFiltersChanged(); }
    else if (t.id === "f-ai") { S.f.ai = t.checked; onFiltersChanged(); }
    else if (t.id === "sel-all") {
      if (t.checked) S.rows.forEach((a) => S.selected.add(a.id)); else S.rows.forEach((a) => S.selected.delete(a.id));
      renderWindow(); updateExportLabel();
    } else if (t.dataset.ind !== undefined) {
      const v = t.dataset.ind; S.f.industries = t.checked ? [...S.f.industries, v] : S.f.industries.filter((x) => x !== v); onFiltersChanged();
    } else if (t.dataset.sig !== undefined) {
      const v = t.dataset.sig; S.f.signals = t.checked ? [...S.f.signals, v] : S.f.signals.filter((x) => x !== v); onFiltersChanged();
    }
  });
  let qTimer = 0;
  document.addEventListener("input", (e) => {
    const t = e.target;
    if (t.id === "q") { clearTimeout(qTimer); qTimer = setTimeout(() => { S.f.q = t.value.trim(); onFiltersChanged({ rerenderFilters: false }); const cb = $('[data-act="clear-filters"]'); if (cb) cb.disabled = !activeFilterCount(); }, 120); }
    else if (t.id === "ind-search") filterChecklist("ind-list", t.value);
    else if (t.id === "sig-search") filterChecklist("sig-list", t.value);
    else if (t.id === "t-wi") { S.tuner.wi = Number(t.value); onTunerChanged(); }
    else if (t.dataset.mult) { S.tuner.mult[t.dataset.mult] = Math.round(Number(t.value) * 10) / 10; onTunerChanged(); }
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") {
      if (!$("#modal").hidden) { $("#modal").hidden = true; return; }
      if (S.openId) { closeDrawer(); return; }
      closeFiltersSheet();
    } else if (e.key === "/" && !/INPUT|TEXTAREA|SELECT/.test(document.activeElement?.tagName || "")) {
      const q = $("#q"); if (q) { e.preventDefault(); q.focus(); }
    } else if ((e.key === "Enter" || e.key === " ") && document.activeElement?.classList.contains("row")) {
      e.preventDefault(); openDrawer(document.activeElement.dataset.id);
    }
  });
  window.addEventListener("hashchange", () => {
    if (readHash()) { render(); if (S.openId) openDrawer(S.openId); else closeDrawer(); }
  });
  let rTimer = 0;
  window.addEventListener("resize", () => { clearTimeout(rTimer); rTimer = setTimeout(() => { if ($("#tbody")) refreshResults(false); }, 120); });
}

/* ---------------------------- boot ---------------------------- */
function renderMeta() {
  const m = S.meta;
  const parts = [`${fmt(S.accounts.length)} accounts`];
  if (num(m.rows_total) && m.rows_total !== S.accounts.length) parts[0] += ` of ${fmt(m.rows_total)}`;
  if (m.generated_at) { const d = new Date(m.generated_at); if (!isNaN(d)) parts.push(`scored ${d.toLocaleDateString("en-AU", { day: "numeric", month: "short", year: "numeric" })}`); }
  if (m.scoring_version) parts.push(m.scoring_version);
  const llm = m.llm || {};
  if (llm.enabled) parts.push(llm.synthetic ? "AI text: synthetic" : `AI: ${fmt(llm.calls)} calls${num(llm.cost_usd) != null ? `, $${Number(llm.cost_usd).toFixed(2)}` : ""}`);
  $("#meta-line").textContent = parts.join(" · ");
  const ban = $("#demo-banner");
  if (m.synthetic) {
    ban.innerHTML = `<span class="tag">Demo data (synthetic)</span><span class="long"><strong>These companies are invented.</strong> Accounts, signals and AI text are generated sample data for demonstrating the workflow. Run the pipeline to load real accounts.</span><span class="short"><strong>Invented companies</strong> for demonstration only.</span>`;
    ban.hidden = false;
  } else if (/sample/.test(S.source)) {
    ban.innerHTML = `<span class="tag">Sample file</span><span>Loaded ${esc(S.source)} because data/accounts.json was not found.</span>`;
    ban.hidden = false;
  }
}
async function boot() {
  applyTheme(lsGet(LS.theme, "system"));
  bindEvents();
  initTooltip();
  let loaded;
  try { loaded = await loadData(); } catch (err) {
    $("#main").innerHTML = `<div class="error-box"><h2>No account data found</h2><p>Perimeter Radar looks for <code>data/accounts.json</code>, then <code>data/accounts.sample.json</code>, next to this page. Build it with <code>python -m pipeline.build --out app/data/accounts.json</code>, or serve the app folder over HTTP (opening the file directly blocks loading).</p><p class="desc">${esc(err.message)}</p></div>`;
    $("#meta-line").textContent = "No data loaded";
    return;
  }
  const { meta, accounts } = normalise(loaded.doc);
  S.meta = meta; S.accounts = accounts; S.source = loaded.src;
  S.byId = new Map(accounts.map((a) => [a.id, a]));
  buildFacets();
  S.tunerBase = tunerDefaults();
  const saved = lsGet(LS.tuner, null);
  S.tuner = tunerDefaults();
  if (saved && typeof saved === "object") {
    if (Number.isFinite(saved.wi)) S.tuner.wi = clamp(saved.wi, 0, 100);
    if (saved.mult) for (const c of CATS) if (Number.isFinite(saved.mult[c])) S.tuner.mult[c] = clamp(saved.mult[c], 0, 3);
    if (Array.isArray(saved.offBands)) S.tuner.offBands = saved.offBands.filter((b) => typeof b === "string");
    if (Array.isArray(saved.offCountries)) S.tuner.offCountries = saved.offCountries.filter((b) => typeof b === "string");
  }
  // drop stale shortlist ids
  for (const id of [...S.shortlist]) if (!S.byId.has(id)) S.shortlist.delete(id);
  rescore();
  readHash();
  renderMeta();
  render();
  if (S.openId && S.byId.has(S.openId)) openDrawer(S.openId); else S.openId = null;
  document.documentElement.dataset.ready = "1";
}
boot();
