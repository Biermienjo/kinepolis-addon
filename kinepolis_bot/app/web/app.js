"use strict";
// Kinepolis-app: alle URL's relatief, zodat het werkt onder het Ingress-pad van Home Assistant.

const $ = (s) => document.querySelector(s);
const h = (s) => String(s ?? "").replace(/[&<>"']/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const BUSY = ["loading", "booking"];

const S = {
  films: {}, movieId: null, names: {}, watches: [], events: [], formats: [],
  locations: [], persons: 2, cinemas: new Set(), fmt: "", timeFrom: "",
  dayMode: "all", days: new Set(), calMonth: null,
  interval: 30, telegram: false, pending: [], catalogAgo: null, catalogError: "", catalogEvery: 300,
  results: null, job: { state: "idle", seq: -1 }, picked: [], view: "search",
};

// ---------- hulpjes ----------
async function call(path, opts = {}) {
  const r = await fetch(path, {
    method: opts.method || (opts.body ? "POST" : "GET"),
    headers: opts.body ? { "Content-Type": "application/json" } : {},
    body: opts.body ? JSON.stringify(opts.body) : undefined,
  });
  const txt = await r.text();
  let data; try { data = JSON.parse(txt); } catch { data = { error: txt }; }
  if (!r.ok) throw new Error(data.error || txt || r.statusText);
  return data;
}
function toast(msg, bad = false) {
  const t = $("#toast");
  t.textContent = msg; t.className = "show" + (bad ? " bad" : "");
  clearTimeout(t._h); t._h = setTimeout(() => (t.className = ""), 3200);
}
async function busy(btn, fn) {
  const old = btn.innerHTML; btn.disabled = true; btn.innerHTML = '<span class="spin"></span>';
  try { await fn(); } catch (e) { toast(e.message, true); } finally { btn.disabled = false; btn.innerHTML = old; }
}
const iso = (d) => new Date(d.getTime() - d.getTimezoneOffset() * 60000).toISOString().slice(0, 10);
const addDays = (n) => { const d = new Date(); d.setDate(d.getDate() + n); return d; };
const cinemaName = (c) => S.names[c] || c;
// Altijd Belgische tijd, ook als je gsm in een andere tijdzone staat.
const TZ = "Europe/Brussels";
const parts = (iso) => Object.fromEntries(new Intl.DateTimeFormat("nl-BE", { timeZone: TZ, weekday: "long",
  day: "numeric", month: "numeric", hour: "2-digit", minute: "2-digit", hour12: false })
  .formatToParts(new Date(iso)).map((p) => [p.type, p.value]));
const hhmm = (iso) => { const p = parts(iso); return `${p.hour}:${p.minute}`; };
// Vandaag in België als "YYYY-MM-DD" (en-CA geeft precies dat formaat).
const todayBE = () => new Intl.DateTimeFormat("en-CA", { timeZone: TZ }).format(new Date());
const dayLabel = (iso) => { const p = parts(iso); return `${p.weekday} ${p.day}/${p.month}`; };

// ---------- navigatie ----------
function show(view) {
  S.view = view;
  document.querySelectorAll(".view").forEach((v) => v.classList.toggle("active", v.id === "view-" + view));
  document.querySelectorAll(".tabs button").forEach((b) => b.classList.toggle("active", b.dataset.view === view));
  if (view === "live") loadLive();
  if (view === "scan") refreshScanners();
  window.scrollTo(0, 0);
}
document.querySelectorAll(".tabs button").forEach((b) => (b.onclick = () => show(b.dataset.view)));

// ---------- films ----------
function renderFilms() {
  const ids = Object.keys(S.films);
  $("#films").innerHTML = ids.map((id) =>
    `<button class="chip film ${id === S.movieId ? "on" : ""}" data-id="${h(id)}">${S.pending.includes(id) ? "⏳ " : ""}${h(S.films[id])}` +
    (id === S.movieId ? `<span class="x" data-del="${h(id)}" title="Verwijder">✕</span>` : "") + `</button>`
  ).join("") + `<button class="chip add" id="film-new">＋ Film</button>`;
  $("#film-add").hidden = ids.length > 0;
  $("#search-form").hidden = !S.movieId;
  $("#film-new").onclick = () => { $("#film-add").hidden = false; $("#film-url").focus(); };
  document.querySelectorAll(".chip.film").forEach((b) => (b.onclick = async (e) => {
    const del = e.target.dataset.del;
    if (del) {
      if (!confirm(`${S.films[del]} verwijderen? Scanners voor deze film stoppen ook.`)) return;
      await call("api/film/" + encodeURIComponent(del), { method: "DELETE" });
    } else {
      await call("api/film", { body: { id: b.dataset.id } });
    }
    S.results = null; S.cinemas.clear();
    await loadState(); renderFilms(); renderResults(); loadLocations();
  }));
}
async function filmChosen(r) {
  $("#film-url").value = ""; $("#film-matches").innerHTML = "";
  S.results = null; S.cinemas.clear();
  await loadState(); renderFilms(); renderResults(); loadLocations();
  if (!r.pending) return toast("Film toegevoegd");
  await loadState();
  if (S.catalogShot) $("#film-matches").innerHTML = `<p class="hint">Niet gevonden op Kinepolis.
    <a href="api/catalog-shot" target="_blank" rel="noopener">📷 Wat zag de browser?</a> ${h(S.catalogNote || "")}</p>`;
  toast(r.checked ? "Nog niet op Kinepolis: start een scanner, ik wacht tot hij verschijnt."
                  : "Kon Kinepolis nu niet doorzoeken. Start een scanner, ik blijf kijken.");
}
function renderMatches(r) {
  $("#film-matches").innerHTML = `<p class="hint">Gevonden op Kinepolis:</p>` +
    r.matches.map((m, i) => `<div class="match"><div><div class="t">${h(m.title)}</div>
      <div class="sub">film ${h(m.id)}</div></div><button class="btn primary small" data-m="${i}">Kies</button></div>`).join("") +
    `<button class="btn ghost wide" id="film-wait">Staat er niet bij: wacht op “${h(r.name)}”</button>`;
  document.querySelectorAll("#film-matches [data-m]").forEach((b) => (b.onclick = () => busy(b, async () => {
    const m = r.matches[b.dataset.m];
    filmChosen(await call("api/film", { body: { url: m.url, title: m.title } }));
  })));
  $("#film-wait").onclick = (e) => busy(e.currentTarget, async () =>
    filmChosen(await call("api/film", { body: { name: r.name, wait: true } })));
}
async function addFilm(e) {
  const text = $("#film-url").value.trim();
  if (!text) return;
  await busy(e.currentTarget || $("#film-save"), async () => {
    toast(/^https?:|^\d+$/.test(text) ? "Film ophalen…" : "Zoeken op Kinepolis… (even geduld)");
    const r = await call("api/film", { body: { url: text } });
    if (r.matches) renderMatches(r); else await filmChosen(r);
  });
}
$("#film-save").onclick = addFilm;
$("#film-url").onkeydown = (e) => { if (e.key === "Enter") addFilm({ currentTarget: $("#film-save") }); };

// ---------- zoekformulier ----------
document.querySelectorAll("[data-step]").forEach((b) => (b.onclick = () => {
  S.persons = Math.min(12, Math.max(1, S.persons + Number(b.dataset.step)));
  $("#persons").textContent = S.persons;
}));

async function loadLocations() {
  if (!S.movieId) return;
  $("#cinemas").innerHTML = '<span class="hint">Laden…</span>';
  try { S.locations = await call("api/locations"); }
  catch (e) { $("#cinemas").innerHTML = `<span class="hint">${h(e.message)}</span>`; return; }
  renderCinemas();
}
function renderCinemas() {
  const order = [...S.cinemas];
  $("#cinemas").innerHTML =
    `<button class="chip ${S.cinemas.size ? "" : "on"}" data-c="">Overal</button>` +
    S.locations.map((l) => {
      const n = order.indexOf(l.code);
      return `<button class="chip ${n >= 0 ? "on" : ""}" data-c="${h(l.code)}">` +
        (n >= 0 && order.length > 1 ? `<b class="nr">${n + 1}</b>` : "") +
        `${h(l.name)}${l.count ? `<small>${l.count}</small>` : ""}</button>`;
    }).join("") +
    (order.length > 1 ? '<p class="hint">Volgorde = voorkeur: bij gelijke dag kiest de scanner eerst nr. 1.</p>' : "");
  document.querySelectorAll("#cinemas .chip").forEach((b) => (b.onclick = () => {
    const c = b.dataset.c;
    if (!c) S.cinemas.clear(); else S.cinemas.has(c) ? S.cinemas.delete(c) : S.cinemas.add(c);
    renderCinemas();
  }));
}

// ---------- wanneer kan je: alle dagen of gekozen dagen (kalender) ----------
const MONTHS = ["januari", "februari", "maart", "april", "mei", "juni", "juli", "augustus",
  "september", "oktober", "november", "december"];
function renderDays() {
  const n = S.days.size;
  $("#day-mode").innerHTML =
    `<button class="chip ${S.dayMode === "all" ? "on" : ""}" data-mode="all">Alle dagen</button>` +
    `<button class="chip ${S.dayMode === "pick" ? "on" : ""}" data-mode="pick">Kies dagen${n ? `<small>${n}</small>` : ""}</button>`;
  document.querySelectorAll("#day-mode .chip").forEach((b) => (b.onclick = () => { S.dayMode = b.dataset.mode; renderDays(); }));
  const cal = $("#calendar");
  cal.hidden = S.dayMode !== "pick";
  if (cal.hidden) return;
  const today = todayBE();
  if (!S.calMonth) S.calMonth = new Date(+today.slice(0, 4), +today.slice(5, 7) - 1, 1);
  const m = S.calMonth, y = m.getFullYear(), mo = m.getMonth();
  const thisMonth = new Date(+today.slice(0, 4), +today.slice(5, 7) - 1, 1);
  const first = new Date(y, mo, 1), days = new Date(y, mo + 1, 0).getDate();
  const offset = (first.getDay() + 6) % 7;                      // maandag eerst
  let cells = "";
  for (let i = 0; i < offset; i++) cells += "<span></span>";
  for (let d = 1; d <= days; d++) {
    const key = iso(new Date(y, mo, d)), past = key < today;
    cells += `<button class="day ${S.days.has(key) ? "on" : ""} ${key === today ? "today" : ""}" data-d="${key}"
      ${past ? "disabled" : ""}>${d}</button>`;
  }
  cal.innerHTML = `<div class="cal-head">
      <button class="round small" data-nav="-1" aria-label="Vorige maand" ${m <= thisMonth ? "disabled" : ""}>‹</button>
      <b>${MONTHS[mo]} ${y}</b>
      <button class="round small" data-nav="1" aria-label="Volgende maand">›</button></div>
    <div class="cal-grid">${["ma", "di", "wo", "do", "vr", "za", "zo"].map((w) => `<i>${w}</i>`).join("")}${cells}</div>
    <div class="cal-foot"><button class="chip" id="cal-weekends">Weekends ${MONTHS[mo]}</button>
      <span class="hint">${n ? `${n} dag${n === 1 ? "" : "en"} gekozen` : "Tik de dagen aan waarop je kunt."}</span>
      ${n ? '<button class="btn ghost small" id="cal-clear">Wis</button>' : ""}</div>`;
  cal.querySelectorAll("[data-d]").forEach((b) => (b.onclick = () => {
    S.days.has(b.dataset.d) ? S.days.delete(b.dataset.d) : S.days.add(b.dataset.d); renderDays();
  }));
  cal.querySelectorAll("[data-nav]").forEach((b) => (b.onclick = () => {
    S.calMonth = new Date(y, mo + Number(b.dataset.nav), 1); renderDays();
  }));
  $("#cal-weekends").onclick = () => {
    const keys = [];
    for (let d = 1; d <= days; d++) {
      const dt = new Date(y, mo, d), key = iso(dt);
      if ([5, 6, 0].includes(dt.getDay()) && key >= today) keys.push(key);
    }
    const allOn = keys.every((k) => S.days.has(k));
    keys.forEach((k) => (allOn ? S.days.delete(k) : S.days.add(k)));
    renderDays();
  };
  $("#cal-clear")?.addEventListener("click", () => { S.days.clear(); renderDays(); });
}

const TIMES = [["", "Altijd"], ["17:00", "vanaf 17u"], ["19:00", "vanaf 19u"], ["20:00", "vanaf 20u"]];
function renderTimes() {
  $("#times").innerHTML = TIMES.map(([v, t]) => `<button class="chip ${S.timeFrom === v ? "on" : ""}" data-t="${v}">${t}</button>`).join("");
  document.querySelectorAll("#times .chip").forEach((b) => (b.onclick = () => { S.timeFrom = b.dataset.t; renderTimes(); }));
}

function renderFormats() {
  $("#formats").innerHTML = [["", "Alle"], ...S.formats.map((f) => [f, f])]
    .map(([v, t]) => `<button class="chip ${S.fmt === v ? "on" : ""}" data-f="${h(v)}">${h(t)}</button>`).join("");
  document.querySelectorAll("#formats .chip").forEach((b) => (b.onclick = () => { S.fmt = b.dataset.f; renderFormats(); }));
}

function query(extra = {}) {
  const days = S.dayMode === "pick" ? [...S.days].sort() : [];
  if (S.dayMode === "pick" && !days.length) throw new Error("Tik minstens één dag aan, of kies 'Alle dagen'.");
  return { persons: S.persons, cinemas: [...S.cinemas], days, fmt: S.fmt, time_from: S.timeFrom, ...extra };
}

$("#do-search").onclick = (e) => busy(e.currentTarget, async () => {
  S.results = (await call("api/search", { body: query() })).results;
  S.resultPersons = S.persons;
  renderResults();
  $("#results").scrollIntoView({ behavior: "smooth" });
});
async function startScanner() {
  await call("api/watch", { body: query() });
  toast("🔔 Scanner gestart");
  await loadState(); show("scan");
}
$("#do-scan").onclick = (e) => busy(e.currentTarget, startScanner);

// ---------- resultaten ----------
function renderResults() {
  const box = $("#results");
  if (S.results === null) { box.innerHTML = ""; return; }
  if (!S.results.length) {
    const pend = S.pending.includes(S.movieId);
    box.innerHTML = `<div class="empty"><span class="big">${pend ? "⏳" : "🍿"}</span>${pend
      ? "Deze film staat nog niet op Kinepolis. Start een scanner: ik kijk of hij verschijnt en zoek dan meteen voorstellingen."
      : `Nog geen voorstellingen met genoeg plaats voor ${S.resultPersons}.`}
      ${pend ? "" : "<br>Start een scanner: zodra er iets is, stel ik je de beste plaatsen voor."}
      <div class="actions center"><button class="btn primary" id="cta-scan">🔔 Start scanner</button></div></div>`;
    $("#cta-scan").onclick = (e) => busy(e.currentTarget, startScanner);
    return;
  }
  let html = "", day = "";
  S.results.forEach((s, i) => {
    const dk = dayLabel(s.start);
    if (dk !== day) { day = dk; html += `<div class="day">${dk}</div>`; }
    const time = hhmm(s.start);
    const free = s.free == null ? "" :
      `<span class="free ${s.free < S.resultPersons * 4 ? "low" : ""}">● ${s.free} vrij</span>`;
    html += `<div class="show"><div class="time">${time}</div>
      <div class="where">${h(cinemaName(s.cinema))}</div>
      <button class="btn primary" data-i="${i}">Kies stoelen</button>
      <div class="meta"><span>zaal ${h(s.hall)}</span>${s.formats.map((f) => `<span class="badge">${h(f)}</span>`).join("")}${free}</div></div>`;
  });
  box.innerHTML = html;
  box.querySelectorAll("[data-i]").forEach((b) => (b.onclick = () => busy(b, async () => {
    S.picked = [];
    S.job = await call("api/book/open", { body: { showing: S.results[b.dataset.i], persons: S.resultPersons } });
    renderBook(); show("book"); poll();
  })));
}

// ---------- boeking ----------
const STEP = { loading: 0, choose: 1, no_seats: 1, booking: 2, in_cart: 3, needs_human: 2, error: 0 };

function seatColor(s, picked) {
  if (picked) return "var(--pick)";
  if (!s.free) return "var(--taken)";
  if (s.kind.startsWith("sofa")) return "var(--cosy)";
  return "var(--accent)";
}
function seatMapSvg(map, picked, interactive) {
  if (!map.length) return "";
  const U = 26, size = 22, top = 46, left = 30;
  const rows = [...new Set(map.map((s) => s.row))].sort((a, b) => a - b);
  const rowY = Object.fromEntries(rows.map((r, i) => [r, top + i * U * 1.08]));
  const maxX = Math.max(...map.map((s) => s.x));
  const W = left + (maxX + 1) * U + 8, H = top + rows.length * U * 1.08 + 4;
  let svg = `<svg viewBox="0 0 ${W} ${H}" xmlns="http://www.w3.org/2000/svg">`;
  svg += `<path d="M${left + 20} 8 H${W - 28} L${W - 40} 22 H${left + 32} Z" fill="#2a303c"/>`;
  svg += `<text x="${(left + W) / 2}" y="19" fill="#8e97a6" font-size="10" text-anchor="middle" letter-spacing="2">SCHERM</text>`;
  rows.forEach((r) => (svg += `<text x="4" y="${rowY[r] + size / 2 + 4}" fill="#8e97a6" font-size="10">${String(r).padStart(2, "0")}</text>`));
  for (const s of map) {
    const isPick = picked.includes(s.label);
    const can = interactive && s.free && s.kind === "normal";
    const x = left + s.x * U, y = rowY[s.row];
    const special = s.kind === "wheelchair" || s.kind === "companion";
    svg += `<g class="seat ${can ? "" : "locked"}" data-l="${h(s.label)}">` +
      `<rect x="${x}" y="${y}" width="${size}" height="${size}" rx="5" fill="${special ? "none" : seatColor(s, isPick)}"` +
      (special ? ` stroke="${s.free ? "var(--accent)" : "var(--taken)"}" stroke-width="2"` : "") + `/>` +
      (isPick ? `<path d="M${x + 6} ${y + 11.5} l4 4 l7 -8" stroke="#052417" stroke-width="2.4" fill="none" stroke-linecap="round"/>` : "") +
      (special ? `<text x="${x + size / 2}" y="${y + 15}" font-size="11" text-anchor="middle" fill="var(--accent)">${s.kind === "wheelchair" ? "♿" : "•"}</text>` : "") +
      `<title>Rij ${s.code?.slice(0, 2)} stoel ${s.code?.slice(2)}</title></g>`;
  }
  return svg + "</svg>";
}
const LEGEND = `<div class="legend"><span><i style="background:var(--pick)"></i>Jouw keuze</span>
  <span><i style="background:var(--accent)"></i>Vrij</span><span><i style="background:var(--taken)"></i>Bezet</span>
  <span><i style="background:var(--cosy)"></i>Cosy (niet gekozen)</span></div>`;
// Vrije gewone stoelen per rij, als aaneengesloten blokken (gangpad of bezet = breuk).
function blocks(map) {
  const rows = {};
  map.filter((s) => s.kind === "normal").forEach((s) => (rows[s.row] ||= []).push(s));
  const out = [];
  for (const seats of Object.values(rows)) {
    seats.sort((a, b) => a.x - b.x);
    let cur = [];
    for (const s of seats) {
      if (s.free && cur.length && s.x - cur[cur.length - 1].x <= 1.5) cur.push(s);
      else { if (cur.length) out.push(cur); cur = s.free ? [s] : []; }
    }
    if (cur.length) out.push(cur);
  }
  return out;
}
// Groep van n naast elkaar met de getikte stoel erin, zo dicht mogelijk rond die stoel.
function groupAround(map, label, n) {
  const blk = blocks(map).find((b) => b.some((s) => s.label === label));
  if (!blk || blk.length < n) return null;
  const i = blk.findIndex((s) => s.label === label);
  const start = Math.min(Math.max(0, i - Math.floor((n - 1) / 2)), blk.length - n);
  return blk.slice(start, start + n).map((s) => s.label);
}
function together(map, labels) {
  return blocks(map).some((b) => labels.every((l) => b.some((s) => s.label === l)));
}
// ['Normale zitplaats 0811', '.. 0812'] -> 'rij 8, stoelen 11 en 12' (zoals in Telegram)
function seatText(labels) {
  const rows = {};
  for (const l of labels) { const m = l.match(/(\d{2})(\d{2})\s*$/); if (m) (rows[+m[1]] ||= []).push(+m[2]); }
  return Object.entries(rows).sort((a, b) => a[0] - b[0]).map(([r, n]) => {
    n.sort((a, b) => a - b);
    return n.length > 1 ? `rij ${r}, stoelen ${n.slice(0, -1).join(", ")} en ${n.at(-1)}` : `rij ${r}, stoel ${n[0]}`;
  }).join(" · ") || labels.join(", ");
}

function renderBook() {
  const j = S.job, box = $("#book");
  $("#book-dot").hidden = !["choose", "in_cart", "needs_human"].includes(j.state);
  if (j.state === "idle" || !j.showing) {
    box.innerHTML = `<div class="empty"><span class="big">💺</span>Nog geen boeking.<br>Kies een voorstelling bij <b>Zoeken</b>.</div>`;
    return;
  }
  const s = j.showing;
  const film = S.films[j.movie_id || S.movieId] || "";
  const step = STEP[j.state] ?? 0;
  let html = `<div class="card"><div class="book-head"><div>
      <div class="title">${h(film)}</div>
      <div class="sub">${dayLabel(s.start)} om ${hhmm(s.start)}
        · ${h(cinemaName(s.cinema))} zaal ${h(s.hall)} · ${j.persons} pers.</div></div>
      ${BUSY.includes(j.state) ? "" : '<button class="btn ghost small" id="book-close">Sluit</button>'}</div>
    <div class="steps">${["Zaal", "Stoelen", "Tickets", "Mandje"].map((t, i) =>
      `<div class="${i < step || j.state === "in_cart" ? "done" : i === step ? "now" : ""}">${t}</div>`).join("")}</div></div>`;

  if (BUSY.includes(j.state)) {
    html += `<div class="status"><span class="spin"></span><div>${h(j.message)}</div></div>`;
    if (j.state === "booking") html += `<div class="map">${seatMapSvg(j.seat_map || [], j.seats || [], false)}</div>`;
  } else if (j.state === "choose") {
    const key = s.id + "|" + (j.suggested || []).join();
    if (S.pickKey !== key) { S.picked = [...(j.suggested || [])]; S.pickKey = key; }
    const ok = S.picked.length === j.persons;
    const changed = S.picked.join() !== (j.suggested || []).join();
    html += `<div class="status ok"><span class="ico">${j.proposal ? "💡" : "✨"}</span><div>${j.proposal ? "Voorstel van je scanner" : "Beste plaatsen"}: <b>${h(seatText(j.suggested || []))}</b>.<br>
      <span class="sub">Tik waar je wilt zitten: de hele groep verhuist mee. Tik een groene stoel om hem los te laten.</span></div></div>
      <div class="map">${seatMapSvg(j.seat_map, S.picked, true)}</div>${LEGEND}
      ${ok && !together(j.seat_map, S.picked) ? '<div class="status warn"><span class="ico">↔️</span><div>Jullie zitten niet naast elkaar.</div></div>' : ""}
      <div class="pickinfo"><span><b>${S.picked.length}</b> / ${j.persons} gekozen</span>
        ${changed ? '<button class="btn ghost small" id="reset-pick">Terug naar voorstel</button>' : ""}</div>
      <div class="actions"><button class="btn pick big" id="confirm" ${ok ? "" : "disabled"}>✅ In mijn mandje</button></div>
      ${j.proposal ? `<div class="actions"><button class="btn ghost big" id="next">➡️ Volgende${j.queue ? ` (${j.queue})` : ""}</button>
        <button class="btn ghost big" id="skip">✖️ Stop voorstellen</button></div>` : ""}
      <p class="hint">Er wordt nog niets betaald. Na het mandje vul je zelf je gegevens in.
        ${j.proposal ? "Tot je ja zegt, houdt Kinepolis de stoelen niet vast." : ""}</p>`;
  } else {
    const kind = { in_cart: ["ok", "✅"], needs_human: ["warn", "✋"], no_seats: ["warn", "😕"], error: ["bad", "⚠️"] }[j.state] || ["", "ℹ️"];
    html += `<div class="status ${kind[0]}"><span class="ico">${kind[1]}</span><div>${h(j.message)}
      ${j.seats?.length ? `<br><b>${h(seatText(j.seats))}</b>` : ""}</div></div>`;
    if (["in_cart", "needs_human"].includes(j.state)) {
      html += `<div class="actions"><button class="btn primary big" id="go-live">Open live browser${j.state === "in_cart" ? " om te betalen" : ""}</button></div>
        <p class="hint">⏱ Kinepolis houdt stoelen maar een paar minuten vast.</p>`;
    }
    if (j.state === "no_seats" && j.seat_map?.length) html += `<div class="map">${seatMapSvg(j.seat_map, [], false)}</div>${LEGEND}`;
    if (j.has_shot) html += `<img class="shot" alt="Screenshot van de browser" src="api/screenshot?s=${j.seq}">`;
  }
  box.innerHTML = html;

  box.querySelectorAll(".seat:not(.locked)").forEach((g) => (g.onclick = () => {
    const l = g.dataset.l, i = S.picked.indexOf(l);
    if (i >= 0) S.picked.splice(i, 1);                             // los laten
    else if (S.picked.length < j.persons) S.picked.push(l);          // aanvullen
    else {
      const grp = groupAround(j.seat_map, l, j.persons);
      if (grp) S.picked = grp;
      else { S.picked.shift(); S.picked.push(l); toast(`Daar is geen plek voor ${j.persons} naast elkaar.`); }
    }
    renderBook();
  }));
  $("#reset-pick")?.addEventListener("click", () => { S.picked = [...j.suggested]; renderBook(); });
  $("#confirm")?.addEventListener("click", (e) => busy(e.currentTarget, async () => {
    S.job = await call("api/book/confirm", { body: { labels: S.picked } }); renderBook(); poll();
  }));
  $("#go-live")?.addEventListener("click", () => show("live"));
  for (const kind of ["next", "skip"]) {
    $("#" + kind)?.addEventListener("click", (e) => busy(e.currentTarget, async () => {
      const r = await call("api/book/" + kind, { method: "POST" });
      S.job = r; renderBook(); if (r.note) toast(r.note); poll();
    }));
  }
  $("#book-close")?.addEventListener("click", async () => {
    if (j.state === "in_cart" && !confirm("Boeking sluiten? Je mandje blijft in de live browser staan.")) return;
    S.job = await call("api/book/reset", { method: "POST" }); renderBook();
  });
}

let polling = false;
async function poll() {
  if (polling) return; polling = true;
  try {
    while (true) {
      const j = await call("api/job");
      if (j.seq !== S.job.seq) { S.job = j; renderBook(); }
      if (!BUSY.includes(j.state)) break;
      await new Promise((r) => setTimeout(r, 1000));
    }
  } catch (e) { toast(e.message, true); } finally { polling = false; }
}

// ---------- live ----------
function liveUrl() {
  const base = location.pathname.replace(/[^/]*$/, "");
  return `novnc/vnc.html?autoconnect=1&reconnect=1&resize=scale&show_dot=1&path=${encodeURIComponent(base.slice(1) + "vnc-ws")}`;
}
function loadLive() {
  const f = $("#live");
  if (!f.src) f.src = liveUrl();
  $("#live-full").href = liveUrl();
}

// ---------- scanners ----------
const ago = (s) => s == null ? "nog niet" : s < 90 ? `${s} s geleden` : `${Math.round(s / 60)} min geleden`;
function renderScanners() {
  $("#tg-warn").hidden = S.telegram && !S.telegramError;
  if (S.telegram && S.telegramError) {
    const tokenFout = /not found|unauthorized/i.test(S.telegramError);
    $("#tg-warn").innerHTML = `Telegram gaf een fout: <b>${h(S.telegramError)}</b>.` + (tokenFout
      ? `<br>Kijk <b>telegram_token</b> na bij de add-on (precies zoals BotFather hem gaf, bv. 123456789:AAH…) en herstart.`
      : "");
  }
  $("#scan-count").textContent = S.watches.length ? `${S.watches.filter((w) => !w.paused).length} actief` : "";
  $("#watches").innerHTML = S.watches.length ? S.watches.map((w) => `<div class="card scanner ${w.paused ? "paused" : ""}">
      <div class="scan-top"><div class="t">${h(w.film)}</div>
        <span class="state ${w.paused ? "" : w.error ? "err" : w.pending ? "wait" : "on"}">${w.paused ? "⏸ gepauzeerd"
          : w.error ? "⚠️ fout" : w.pending ? "⏳ wacht op film" : "● actief"}</span></div>
      <div class="d">${h(w.describe)}</div>
      <div class="d small">${w.paused ? "Scant niet." : w.pending
        ? `Kijkt elke ${Math.round(S.catalogEvery / 60)} min of de film op Kinepolis staat · laatst ${ago(S.catalogAgo)}` +
          (S.catalogError ? `<br><span class="err">${h(S.catalogError)}</span>` : "") +
          (S.catalogShot ? `<br><a href="api/catalog-shot" target="_blank" rel="noopener">📷 Wat zag de browser?</a>` : "")
        : `Elke ~${S.interval} s · gecontroleerd ${ago(w.ago)}` +
        (w.found != null ? ` · ${w.found} passende voorstelling${w.found === 1 ? "" : "en"}` : "")}
        ${w.error ? `<br><span class="err">${h(w.error)}</span>` : ""}</div>
      <div class="actions"><button class="btn ghost small" data-p="${w.wid}" data-a="${w.paused ? "resume" : "pause"}">${w.paused ? "▶️ Hervat" : "⏸ Pauzeer"}</button>
        <button class="btn ghost small" data-stop="${w.wid}">⏹ Stop</button></div></div>`).join("")
    : `<div class="empty"><span class="big">🔔</span>Nog geen scanners.<br>Kies een film, stel in en tik <b>Start scanner</b>.</div>`;
  document.querySelectorAll("[data-p]").forEach((b) => (b.onclick = () => busy(b, async () => {
    await call(`api/watch/${b.dataset.p}/${b.dataset.a}`, { method: "POST" }); await refreshScanners();
  })));
  document.querySelectorAll("[data-stop]").forEach((b) => (b.onclick = () => {
    if (!confirm("Deze scanner stoppen?")) return;
    busy(b, async () => { await call("api/watch/" + b.dataset.stop, { method: "DELETE" }); await refreshScanners(); });
  }));
  $("#events").innerHTML = S.events.length ? S.events.map((e) => {
    const p = parts(e.time);
    return `<div class="event"><time>${p.day}/${p.month} ${p.hour}:${p.minute}</time><div>${h(e.text)}</div></div>`;
  }).join("") : `<div class="empty">Nog geen meldingen.</div>`;
}
async function refreshScanners() {
  try {
    const before = S.movieId, films = JSON.stringify(S.films);
    await loadState(); renderScanners();
    if (JSON.stringify(S.films) !== films) renderFilms();      // bv. film is net op Kinepolis verschenen
    if (S.movieId !== before) { S.results = null; renderResults(); loadLocations(); }
  } catch (e) { toast(e.message, true); }
}
$("#scan-new").onclick = () => {
  show("search");
  if (!S.movieId) { $("#film-add").hidden = false; $("#film-url").focus(); }
  else setTimeout(() => $("#do-scan").scrollIntoView({ behavior: "smooth", block: "center" }), 50);
  toast("Kies film en instellingen, tik dan 🔔 Start scanner");
};

// Op de achtergrond: voorstellen van scanners oppikken en scanners verversen.
setInterval(async () => {
  try {
    if (!polling) {
      const j = await call("api/job");
      if (j.seq !== S.job.seq) {
        const fresh = j.proposal && j.state === "choose" && S.job.state !== "choose";
        S.job = j; renderBook();
        if (BUSY.includes(j.state)) poll();
        if (fresh && S.view !== "book") toast("💡 Nieuw voorstel van je scanner: zie Boeking");
      }
    }
    if (S.view === "scan") await refreshScanners();
  } catch { /* volgende ronde */ }
}, 4000);

// ---------- start ----------
async function loadState() {
  const st = await call("api/state");
  Object.assign(S, { films: st.films, movieId: st.movie_id, names: Object.fromEntries(Object.entries(st.names).map(([k, v]) => [k, v])),
    watches: st.watches, events: st.events, formats: st.formats, interval: st.interval, telegram: st.telegram,
    pending: st.pending, telegramError: st.telegram_error, catalogShot: st.catalog_shot, catalogNote: st.catalog_note, catalogAgo: st.catalog_ago, catalogError: st.catalog_error, catalogEvery: st.catalog_every });
}
(async () => {
  try {
    await loadState();
    renderFilms(); renderFormats(); renderTimes(); renderDays();
    loadLocations();
    S.job = await call("api/job"); renderBook();
    if (BUSY.includes(S.job.state)) poll();
    if (["choose", "in_cart", "needs_human"].includes(S.job.state)) show("book");
  } catch (e) { toast(e.message, true); }
})();
