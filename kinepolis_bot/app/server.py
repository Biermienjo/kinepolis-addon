#!/usr/bin/env python3
"""Webapp voor Home Assistant (Ingress) + scanner die voorstelt via Telegram.

- Scanner: per film, loopt oneindig. Nieuwe voorstellingen die passen komen in een
  wachtrij (beste eerst). De browser opent de zaal, kiest de beste plaatsen en stelt die
  voor op Telegram én in de app: Ja = in het mandje, Volgende, of Stop voorstellen.
- De browser (Playwright) draait in één werkthread; alle stappen gaan via een wachtrij.
- /vnc-ws koppelt noVNC aan x11vnc (localhost:5900): de live browser in de app.
Alle URL's zijn relatief, zodat het werkt onder het Ingress-pad van Home Assistant.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import json
import os
import queue
import random
import threading
import time
import traceback
import uuid
from pathlib import Path

import requests
from aiohttp import WSMsgType, web

import api
import core

WEB_DIR = Path(__file__).parent / "web"
NOVNC_DIR = Path(os.environ.get("NOVNC_DIR", "/usr/share/novnc"))
INGRESS_PEERS = {"172.30.32.2", "127.0.0.1", "::1"}
BUSY = {"loading", "booking"}
QUEUE_WAIT_S = 600          # bij een drop: tot 10 min wachten in een eventuele wachtrij
CATALOG_EVERY_S = 300       # zo vaak kijken of een film (op naam) al op Kinepolis staat


class Engine:
    """Boekingsstatus, voorstellen-wachtrij, scanners en Telegram-knoppen."""

    def __init__(self, store: core.Store, notifier: core.Notifier, booker_factory,
                 interval_s: int = 30, names=lambda: {}, catalog_urls: list[str] | None = None):
        self.store, self.notifier, self.booker_factory = store, notifier, booker_factory
        self.catalog_urls = catalog_urls or []
        self.catalog_status: dict = {}            # laatste catalogus-controle
        self.interval_s, self.names = interval_s, names
        self.lock = threading.RLock()
        self.job: dict = {"state": "idle", "message": "", "seq": 0, "proposal": False}
        self.shot: bytes | None = None
        self.candidates: list[dict] = []          # {"showing", "persons", "wid"}
        self.status: dict[str, dict] = {}         # per scanner: laatste controle, fouten
        self.tasks: queue.Queue = queue.Queue()
        self._booker = None
        self._tg_msg: int | None = None           # Telegram-bericht met de knoppen
        threading.Thread(target=self._work, daemon=True, name="browser").start()

    def start_background(self):
        threading.Thread(target=self._scan_loop, daemon=True, name="scanner").start()
        threading.Thread(target=self._catalog_loop, daemon=True, name="catalogus").start()
        if self.notifier.token:
            threading.Thread(target=self._telegram_loop, daemon=True, name="telegram").start()

    # ---------- status ----------
    def update(self, **fields):
        with self.lock:
            self.job.update(fields, seq=self.job["seq"] + 1)

    def snapshot(self) -> dict:
        with self.lock:
            return dict(self.job, queue=len(self.candidates))

    def film(self, movie_id: str | None = None) -> str:
        mid = movie_id or self.store.data.get("movie_id")
        return self.store.data["films"].get(mid, "")

    # ---------- werkthread ----------
    @property
    def booker(self):
        if self._booker is None:
            self._booker = self.booker_factory()
        return self._booker

    def _work(self):
        while True:
            fn, args = self.tasks.get()
            try:
                fn(*args)
            except Exception as e:
                traceback.print_exc()
                self.update(state="error", message=f"Fout: {e}", proposal=False)

    def _apply(self, res):
        if res.screenshot:
            self.shot = res.screenshot
        fields = {"state": res.status, "message": res.message, "seats": res.seats,
                  "has_shot": bool(self.shot)}
        if res.seat_map:
            fields["seat_map"] = res.seat_map
        if res.status == "choose":
            fields["suggested"] = res.seats
        self.update(**fields)

    def _on_wait(self, shot):
        if shot:
            self.shot = shot
        self.update(message="Wachten op de zaal (wachtrij?). Kijk mee in Live.", has_shot=bool(shot))

    def _open(self, showing: dict, persons: int, wid: str | None = None,
              movie_id: str | None = None):
        self.update(state="loading", message="Zaal openen…", showing=showing, persons=persons,
                    seat_map=[], seats=[], suggested=[], has_shot=False, proposal=bool(wid),
                    wid=wid, movie_id=movie_id or self.store.data.get("movie_id"))
        res = self.booker.open_seats(f"{api.WEB}/order/showtimes/{showing['id']}/seats", persons,
                                     wait_s=QUEUE_WAIT_S if wid else 60, on_wait=self._on_wait)
        self._apply(res)
        if not wid:
            return
        if res.status == "choose":
            self._send_proposal(res.seats)
        elif res.status == "no_seats":
            self._propose_next()                      # deze is vol: meteen de volgende
        elif res.status == "needs_human":
            self.notifier.send(f"✋ {self._title()}\n{res.message}\nOpen de Kinepolis-app.",
                               res.screenshot, urgent=True)

    def _title(self) -> str:
        j = self.snapshot()
        s = j.get("showing") or {}
        name = self.names().get(s.get("cinema"), s.get("cinema", ""))
        return (f"{self.film(j.get('movie_id'))}\n{core.when_long(s['start'])} · {name} zaal {s.get('hall')}"
                if s else "")

    def _send_proposal(self, labels: list[str]):
        j = self.snapshot()
        image = self.booker.seat_image(labels) or self.shot
        more = len(self.candidates)
        text = (f"🎬 {self._title()}\n💺 {j['persons']} pers.: {core.seat_text(labels)}\n\n"
                "Zal ik ze in je mandje zetten?")
        seq = j["seq"]
        buttons = [[{"text": "✅ Ja, in mijn mandje", "callback_data": f"yes:{seq}"}],
                   [{"text": f"➡️ Volgende{f' ({more})' if more else ''}", "callback_data": f"next:{seq}"},
                    {"text": "✖️ Stop voorstellen", "callback_data": f"skip:{seq}"}]]
        self.update(proposal_seq=seq)
        self._tg_msg = self.notifier.send_buttons(text, image, buttons, urgent=True)
        self.store.add_event(f"💡 Voorstel: {self._title().replace(chr(10), ' · ')} – "
                             f"{core.seat_text(labels)}", "proposal", j.get("showing"))

    def _propose_next(self):
        with self.lock:
            cand = self.candidates.pop(0) if self.candidates else None
        if cand is None:
            self.update(state="idle", message="", proposal=False, showing=None, seat_map=[])
            return
        self._open(cand["showing"], cand["persons"], cand["wid"], cand["movie_id"])

    def _checkout(self, labels: list[str]):
        self.update(state="booking", message="Stoelen aanklikken en tickets kiezen…", seats=labels)
        res = self.booker.checkout(labels)
        self._apply(res)
        j = self.snapshot()
        if res.status == "taken":                     # opnieuw kijken en opnieuw voorstellen
            with self.lock:
                self.candidates.insert(0, {"showing": j["showing"], "persons": j["persons"],
                                           "wid": j.get("wid") or "manual",
                                           "movie_id": j.get("movie_id")})
            self.notifier.send(f"⚠️ {res.message} Ik zoek opnieuw…")
            self._propose_next()
            return
        if res.status in ("in_cart", "needs_human"):
            icon = "✅" if res.status == "in_cart" else "✋"
            text = (f"{icon} {self._title()}\n💺 {core.seat_text(labels)}\n{res.message}\n"
                    "⏱ Kinepolis houdt stoelen maar een paar minuten vast: "
                    "open de Kinepolis-app → Live om te betalen.")
            self.store.add_event(f"{icon} {self._title().replace(chr(10), ' · ')}: {res.message}",
                                 res.status, j.get("showing"))
            buttons = ([[{"text": "⏹ Stop deze scanner", "callback_data": f"stop:{j['wid']}"}]]
                       if j.get("wid") and j["wid"] != "manual" else None)
            self.notifier.send_buttons(text, res.screenshot, buttons, urgent=True)

    # ---------- acties (vanuit app of Telegram) ----------
    def start_booking(self, showing: dict, persons: int):
        if self.snapshot()["state"] in BUSY:
            raise web.HTTPConflict(text="Er loopt al een boeking.")
        self.update(state="loading", message="In de wachtrij…", showing=showing, persons=persons,
                    proposal=False)
        self.tasks.put((self._open, (showing, persons)))

    def confirm(self, labels: list[str]):
        job = self.snapshot()
        if job["state"] != "choose":
            raise web.HTTPConflict(text="Er is geen stoelkeuze open.")
        if len(labels) != job["persons"]:
            raise web.HTTPBadRequest(text=f"Kies precies {job['persons']} stoelen.")
        free = {s["label"] for s in job.get("seat_map", []) if s["free"] and s["kind"] == "normal"}
        if not set(labels) <= free:
            raise web.HTTPBadRequest(text="Een van die stoelen is niet vrij.")
        self._clear_buttons("✅ Ja gekozen")
        self.update(state="booking", message="In de wachtrij…")
        self.tasks.put((self._checkout, (labels,)))

    def answer(self, kind: str, seq: int | None = None) -> str:
        """Antwoord op een voorstel: yes | next | skip. Geeft een korte bevestiging terug."""
        j = self.snapshot()
        if kind in ("yes", "next", "skip") and (j["state"] != "choose" or
                                                (seq is not None and seq != j.get("proposal_seq"))):
            return "Dit voorstel is niet meer actueel."
        if kind == "yes":
            self.confirm(j["suggested"])
            return "Komt eraan: ik zet ze in je mandje…"
        if kind == "next":
            self._clear_buttons("➡️ Volgende")
            self.update(state="loading", message="Volgend voorstel…")
            self.tasks.put((self._propose_next, ()))
            return "Ik zoek het volgende voorstel."
        if kind == "skip":
            with self.lock:
                self.candidates.clear()
            self._clear_buttons("✖️ Gestopt met voorstellen")
            self.update(state="idle", message="", proposal=False, showing=None, seat_map=[])
            return "Oké. Ik blijf scannen en meld me bij nieuwe voorstellingen."
        return ""

    def _clear_buttons(self, note: str):
        if self._tg_msg:
            self.notifier.tg("editMessageReplyMarkup", message_id=self._tg_msg,
                             reply_markup=json.dumps({"inline_keyboard": [[
                                 {"text": note, "callback_data": "noop"}]]}))
            self._tg_msg = None

    def refresh_shot(self):
        def grab():
            shot = self.booker.screenshot()
            if shot:
                self.shot = shot
                self.update(has_shot=True)
        self.tasks.put((grab, ()))

    # ---------- films op naam ----------
    def catalog_lookup(self, name: str, timeout: float = 90) -> list[dict] | None:
        """Zoekt `name` in de Kinepolis-catalogus (via de browser). None = geen antwoord."""
        done, box = threading.Event(), {}

        def task():
            try:
                box["films"] = self.booker.catalog(self.catalog_urls)
                self.catalog_status = {"last": time.time(), "count": len(box["films"]), "error": ""}
            finally:
                done.set()
        self.tasks.put((task, ()))
        if not done.wait(timeout) or "films" not in box:
            return None
        return core.match_films(box["films"], name)

    def _check_catalog(self):
        with self.store.lock:
            pending = {mid: name for mid, name in self.store.data["films"].items()
                       if mid.startswith(core.PENDING)}
        if not pending:
            return
        try:
            films = self.booker.catalog(self.catalog_urls)
            self.catalog_status = {"last": time.time(), "count": len(films),
                                   "error": "" if films else "Geen films gevonden op de pagina's"}
        except Exception as e:
            self.catalog_status = {"last": time.time(), "count": 0, "error": str(e)[:200]}
            raise
        for mid, name in pending.items():
            hits = core.match_films(films, name)
            if hits:
                self.resolve_film(mid, hits[0])

    def resolve_film(self, pending: str, film: dict):
        """Film staat nu op Kinepolis: scanners schakelen over op voorstellingen."""
        real, name = film["id"], film["title"]
        with self.store.lock:
            d = self.store.data
            d["films"].pop(pending, None)
            d["films"][real] = name
            for w in d["watches"]:
                if w["movie_id"] == pending:
                    w["movie_id"] = real
                    self.status.setdefault(w["wid"], {})["next"] = 0      # meteen scannen
            if d["movie_id"] == pending:
                d["movie_id"] = real
            self.store.save()
        text = f"🎬 {name} staat nu op Kinepolis! Ik scan vanaf nu naar voorstellingen.\n{film['url']}"
        self.store.add_event(text.split("\n")[0], "film")
        self.notifier.send(text, urgent=True)

    def _catalog_loop(self):
        while True:
            time.sleep(15)
            if time.time() - self.catalog_status.get("last", 0) < CATALOG_EVERY_S:
                continue
            with self.store.lock:
                waiting = any(m.startswith(core.PENDING) and any(
                    w["movie_id"] == m and not w.get("paused") for w in self.store.data["watches"])
                    for m in self.store.data["films"])
            if waiting and self.snapshot()["state"] not in BUSY:
                self.catalog_status["last"] = time.time()      # niet dubbel inplannen
                self.tasks.put((self._check_catalog, ()))

    # ---------- scanner ----------
    def scan(self, w: dict):
        """Eén controle van één scanner. Nieuwe passende voorstellingen -> wachtrij."""
        q = core.Query.from_json(w)
        found = q.rank(q.matches(api.fetch_showings(w["movie_id"], q.cinemas, q.date_from, q.date_to)))
        with self.store.lock:
            seen = set(self.store.data["seen"])
            new = [s for s in found if s.id not in seen]
            if new:
                self.store.data["seen"] = list(seen | {s.id for s in new})
                self.store.save()
        st = self.status.setdefault(w["wid"], {})
        st.update(last=time.time(), found=len(found), error="", fails=0)
        if not new:
            return
        self.store.add_event(f"🆕 {self.film(w['movie_id'])}: {len(new)} nieuwe voorstelling(en)",
                             "new", core.showing_json(new[0]))
        with self.lock:
            self.candidates += [{"showing": core.showing_json(s), "persons": q.persons,
                                 "wid": w["wid"], "movie_id": w["movie_id"]} for s in new]
            idle = self.job["state"] not in BUSY | {"choose"}
        if idle:
            self.update(state="loading", message="Nieuwe voorstelling gevonden! Zaal openen…")
            self.tasks.put((self._propose_next, ()))

    def _scan_loop(self):
        while True:
            time.sleep(2)
            with self.store.lock:
                watches = [dict(w) for w in self.store.data["watches"]]
            for w in watches:
                st = self.status.setdefault(w["wid"], {"next": 0})
                if (w.get("paused") or w["movie_id"].startswith(core.PENDING)
                        or time.time() < st.get("next", 0)):
                    continue
                try:
                    self.scan(w)
                    wait = self.interval_s
                except Exception as e:
                    st["fails"] = st.get("fails", 0) + 1
                    st["error"] = str(e)[:200]
                    wait = min(self.interval_s * 2 ** st["fails"], 600)   # rustig aan bij fouten
                    print(f"[scanner] {e}", flush=True)
                st["next"] = time.time() + wait * random.uniform(0.85, 1.15)

    # ---------- Telegram ----------
    def _telegram_loop(self):
        me = self.notifier.tg("getMe")
        if me:
            print(f"[telegram] verbonden als @{me.get('username')}", flush=True)
        elif "niet bereikbaar" in self.notifier.error:
            print("[telegram] Telegram is nu niet bereikbaar; ik probeer het straks opnieuw.", flush=True)
        else:
            print("[telegram] token wordt niet herkend: kijk telegram_token na in de configuratie "
                  "(zoals BotFather hem gaf, bv. 123456789:AAH...).", flush=True)
        offset, fails = 0, 0
        while True:
            try:
                ups = self.notifier.tg("getUpdates", offset=offset, timeout=30,
                                       allowed_updates='["message","callback_query"]', _timeout=40)
                if ups is None:                       # fout (bv. foute token): rustig opnieuw
                    fails += 1
                    time.sleep(min(10 * 2 ** fails, 600))
                    continue
                fails = 0
                for u in ups:
                    offset = u["update_id"] + 1
                    self._telegram_update(u)
            except Exception as e:
                print("[telegram]", e, flush=True)
                time.sleep(30)

    def _telegram_update(self, u: dict):
        if cb := u.get("callback_query"):
            if str(cb.get("message", {}).get("chat", {}).get("id")) != self.notifier.chat_id:
                return
            kind, _, arg = (cb.get("data") or "").partition(":")
            if kind == "stop":
                reply = self.stop_watch(arg)
            elif kind in ("yes", "next", "skip"):
                try:
                    reply = self.answer(kind, int(arg))
                except web.HTTPException as e:
                    reply = e.text
            else:
                reply = ""
            self.notifier.tg("answerCallbackQuery", callback_query_id=cb["id"], text=reply[:190])
            if reply:
                self.notifier.send(reply)
            return
        msg = u.get("message") or {}
        chat = str(msg.get("chat", {}).get("id"))
        print(f"[telegram] bericht van chat {chat}: {(msg.get('text') or '')[:40]!r}", flush=True)
        if chat != self.notifier.chat_id:
            # Alleen antwoorden met het chat-id (geen gegevens), zodat je het kunt invullen.
            print(f"[telegram] genegeerd: telegram_chat_id is {self.notifier.chat_id!r}, "
                  f"dit bericht kwam van {chat}", flush=True)
            self.notifier.tg("sendMessage", chat_id=chat,
                             text=f"Je chat-id is {chat}. Vul dat in bij telegram_chat_id "
                                  "in de Kinepolis-add-on en herstart.")
            return
        if (msg.get("text") or "").startswith(("/start", "/status", "/help")):
            self.notifier.send(self.status_text())

    def status_text(self) -> str:
        with self.store.lock:
            watches = list(self.store.data["watches"])
        if not watches:
            return "🎬 Geen scanners actief. Zet er een aan in de Kinepolis-app."
        lines = []
        for w in watches:
            st = self.status.get(w["wid"], {})
            ago = f"{int(time.time() - st['last'])} s geleden" if st.get("last") else "nog niet"
            lines.append(f"• {'⏸ ' if w.get('paused') else ''}{self.film(w['movie_id'])}: "
                         f"{core.Query.from_json(w).describe(self.names())}"
                         f"\n  gecontroleerd {ago}" + (f" ⚠️ {st['error']}" if st.get("error") else ""))
        return "🔎 Actieve scanners:\n" + "\n".join(lines)

    def pause_watch(self, wid: str, paused: bool):
        with self.store.lock:
            for w in self.store.data["watches"]:
                if w["wid"] == wid:
                    w["paused"] = paused
            self.store.save()
        if paused:
            with self.lock:
                self.candidates = [c for c in self.candidates if c["wid"] != wid]
        else:
            self.status.setdefault(wid, {})["next"] = 0      # meteen controleren

    def stop_watch(self, wid: str) -> str:
        with self.store.lock:
            before = len(self.store.data["watches"])
            self.store.data["watches"] = [w for w in self.store.data["watches"] if w["wid"] != wid]
            self.store.save()
        with self.lock:
            self.candidates = [c for c in self.candidates if c["wid"] != wid]
        self.status.pop(wid, None)
        return "⏹ Scanner gestopt." if len(self.store.data["watches"]) < before else ""


def load_aliases() -> str:
    return os.environ.get("LOCATIES") or core.load_options().get("locaties", "")


def names() -> dict[str, str]:
    """Cinemacode -> naam: geleerd van Kinepolis, overschreven door 'locaties'."""
    aliases = {v: k.title() for k, v in core.parse_aliases(load_aliases()).items()}
    api.KNOWN_SITES.update(aliases)
    return {**api.SITE_NAMES, **aliases}


# ---------------- web ----------------

@web.middleware
async def ingress_only(request, handler):
    if os.environ.get("INGRESS_ONLY") == "1" and request.remote not in INGRESS_PEERS:
        raise web.HTTPForbidden(text="Alleen via Home Assistant.")
    return await handler(request)


@web.middleware
async def errors(request, handler):
    try:
        return await handler(request)
    except web.HTTPException:
        raise
    except (ValueError, KeyError) as e:
        return web.json_response({"error": str(e)}, status=400)
    except (requests.RequestException, OSError) as e:
        return web.json_response({"error": f"Kinepolis niet bereikbaar: {e}"}, status=502)
    except Exception as e:
        traceback.print_exc()
        return web.json_response({"error": f"Fout: {e}"}, status=500)


def blocking(fn, *args):
    return asyncio.get_running_loop().run_in_executor(None, fn, *args)


def make_app(engine: Engine, store: core.Store) -> web.Application:
    routes = web.RouteTableDef()

    def movie_id() -> str:
        if not store.data.get("movie_id"):
            raise ValueError("Kies eerst een film.")
        return store.data["movie_id"]

    @routes.get("/")
    async def index(_):
        return web.FileResponse(WEB_DIR / "index.html", headers={"Cache-Control": "no-cache"})

    @routes.get("/api/state")
    async def state(_):
        d = store.data
        watches = []
        for w in d["watches"]:
            st = engine.status.get(w["wid"], {})
            watches.append({**w, "film": d["films"].get(w["movie_id"], w["movie_id"]),
                            "describe": core.Query.from_json(w).describe(names()),
                            "ago": int(time.time() - st["last"]) if st.get("last") else None,
                            "pending": w["movie_id"].startswith(core.PENDING),
                            "found": st.get("found"), "error": st.get("error", "")})
        return web.json_response({"films": d["films"], "movie_id": d["movie_id"], "names": names(),
                                  "watches": watches, "events": d["events"][::-1],
                                  "formats": core.FORMATS, "interval": engine.interval_s,
                                  "telegram": bool(engine.notifier.token),
                                  "telegram_error": engine.notifier.error,
                                  "pending": [m for m in d["films"] if m.startswith(core.PENDING)],
                                  "catalog_ago": int(time.time() - engine.catalog_status["last"])
                                  if engine.catalog_status.get("last") else None,
                                  "catalog_error": engine.catalog_status.get("error", ""),
                                  "catalog_every": CATALOG_EVERY_S})

    def select_film(mid: str, name: str):
        with store.lock:
            store.data["films"].setdefault(mid, name)
            store.data["movie_id"] = mid
            store.save()

    @routes.post("/api/film")
    async def add_film(request):
        """Link, bestaand id, naam (zoekt in de catalogus) of naam met wait=true (nog niet online)."""
        body = await request.json()
        text = (body.get("url") or body.get("name") or "").strip()
        if body.get("id") in store.data["films"]:
            select_film(body["id"], "")
            return web.json_response({"movie_id": body["id"]})
        if mid := api.movie_id_from(text) if "/" in text or text.isdigit() else None:
            if mid not in store.data["films"]:
                await blocking(api.fetch_showings, mid)          # bestaat de film?
            select_film(mid, api.FILM_TITLES.get(mid) or body.get("title")
                        or core.film_name_from(text, mid))
            return web.json_response({"movie_id": mid})
        if len(core.norm(text)) < 2:
            raise ValueError("Typ de naam van de film of plak de link van kinepolis.be.")
        hits = None
        if not body.get("wait"):
            hits = await blocking(engine.catalog_lookup, text)
            if hits:
                return web.json_response({"matches": hits[:8], "name": text})
        mid = core.pending_id(text)                            # nog niet op Kinepolis
        select_film(mid, text)
        # checked=False: de catalogus kon niet gelezen worden (browser bezig of site onbereikbaar)
        return web.json_response({"movie_id": mid, "pending": True, "checked": hits is not None
                                  or bool(body.get("wait"))})

    @routes.delete("/api/film/{mid}")
    async def del_film(request):
        mid = request.match_info["mid"]
        for w in [w for w in store.data["watches"] if w["movie_id"] == mid]:
            engine.stop_watch(w["wid"])
        with store.lock:
            store.data["films"].pop(mid, None)
            if store.data["movie_id"] == mid:
                store.data["movie_id"] = next(iter(store.data["films"]), None)
            store.save()
        return web.json_response({"ok": True})

    @routes.get("/api/locations")
    async def locations(_):
        # Ook cinema's zonder voorstellingen (nog niet in verkoop) kun je kiezen.
        n = names()
        counts = {code: 0 for code in n}
        showings = [] if movie_id().startswith(core.PENDING) else await blocking(api.fetch_showings, movie_id())
        for s in showings:
            if s.start > dt.datetime.now(api.TZ):
                counts[s.cinema] = counts.get(s.cinema, 0) + 1
        return web.json_response([{"code": c, "name": n.get(c, c), "count": k}
                                  for c, k in sorted(counts.items(), key=lambda x: n.get(x[0], x[0]))])

    @routes.post("/api/search")
    async def do_search(request):
        q = core.Query.from_json(await request.json())
        if movie_id().startswith(core.PENDING):
            return web.json_response({"results": [], "pending": True})
        return web.json_response({"results": await blocking(core.search, movie_id(), q)})

    @routes.post("/api/watch")
    async def add_watch(request):
        q = core.Query.from_json(await request.json())
        w = {**q.to_json(), "movie_id": movie_id(), "wid": uuid.uuid4().hex[:10],
             "created": dt.datetime.now(api.TZ).isoformat(timespec="seconds")}
        with store.lock:
            store.data["watches"].append(w)
            store.save()
        engine.status[w["wid"]] = {"next": 0}                # meteen een eerste controle
        return web.json_response({"ok": True, "wid": w["wid"]})

    @routes.post("/api/watch/{wid}/{action:pause|resume}")
    async def pause_watch(request):
        engine.pause_watch(request.match_info["wid"], request.match_info["action"] == "pause")
        return web.json_response({"ok": True})

    @routes.delete("/api/watch/{wid}")
    async def del_watch(request):
        engine.stop_watch(request.match_info["wid"])
        return web.json_response({"ok": True})

    @routes.post("/api/book/open")
    async def book_open(request):
        body = await request.json()
        engine.start_booking(body["showing"], int(body["persons"]))
        return web.json_response(engine.snapshot())

    @routes.post("/api/book/confirm")
    async def book_confirm(request):
        engine.confirm((await request.json())["labels"])
        return web.json_response(engine.snapshot())

    @routes.post("/api/book/{kind:next|skip}")
    async def book_answer(request):
        msg = engine.answer(request.match_info["kind"])
        return web.json_response({**engine.snapshot(), "note": msg})

    @routes.post("/api/book/reset")
    async def book_reset(_):
        if engine.snapshot()["state"] in BUSY:
            raise web.HTTPConflict(text="Even wachten, de browser is bezig.")
        engine.update(state="idle", message="", seat_map=[], seats=[], suggested=[], showing=None,
                      proposal=False)
        return web.json_response(engine.snapshot())

    @routes.get("/api/job")
    async def job(_):
        return web.json_response(engine.snapshot())

    @routes.post("/api/screenshot/refresh")
    async def shot_refresh(_):
        engine.refresh_shot()
        return web.json_response({"ok": True})

    @routes.get("/api/screenshot")
    async def screenshot(_):
        if not engine.shot:
            raise web.HTTPNotFound()
        return web.Response(body=engine.shot, content_type="image/png",
                            headers={"Cache-Control": "no-store"})

    @routes.get("/vnc-ws")
    async def vnc_ws(request):
        ws = web.WebSocketResponse(protocols=("binary",), max_msg_size=0)
        await ws.prepare(request)
        try:
            reader, writer = await asyncio.open_connection("127.0.0.1", 5900)
        except OSError:
            await ws.close(message=b"VNC niet bereikbaar")
            return ws

        async def to_vnc():
            async for msg in ws:
                if msg.type == WSMsgType.BINARY:
                    writer.write(msg.data)
                    await writer.drain()
                elif msg.type in (WSMsgType.CLOSE, WSMsgType.ERROR):
                    break

        async def from_vnc():
            while data := await reader.read(65536):
                await ws.send_bytes(data)

        tasks = [asyncio.ensure_future(to_vnc()), asyncio.ensure_future(from_vnc())]
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for t in tasks:
            t.cancel()
        writer.close()
        await ws.close()
        return ws

    app = web.Application(middlewares=[ingress_only, errors])
    app.add_routes(routes)
    app.router.add_static("/static/", WEB_DIR)
    if NOVNC_DIR.exists():
        app.router.add_static("/novnc/", NOVNC_DIR)
    return app


def main():
    opts = core.load_options()
    core.DATA.mkdir(parents=True, exist_ok=True)
    store = core.Store(core.DATA / "state.json")
    for w in store.data["watches"]:                       # oude opslag zonder id
        w.setdefault("wid", uuid.uuid4().hex[:10])
    store.save()
    notifier = core.Notifier(opts.get("telegram_token", ""), opts.get("telegram_chat_id", ""),
                             opts.get("ha_notify_service", ""))

    def booker_factory():
        from booker import Booker                    # playwright pas laden als nodig
        return Booker(str(core.DATA / "browser"),
                      executable_path=os.environ.get("CHROMIUM_PATH") or None)

    # Eigen pagina's + altijd die van de nieuwe site (oude instellingen blijven anders hangen).
    catalog_urls = list(dict.fromkeys(
        u.strip() for u in f"{opts.get('film_paginas', '')},{core.DEFAULT_FILM_PAGES}".split(",") if u.strip()))
    engine = Engine(store, notifier, booker_factory, int(opts.get("scan_interval_seconds", 30)), names,
                    catalog_urls)
    print(f"[telegram] {notifier.describe()}", flush=True)
    engine.tasks.put((lambda: engine.booker, ()))     # browser alvast starten
    engine.start_background()
    web.run_app(make_app(engine, store), host="0.0.0.0", port=int(os.environ.get("PORT", 8099)))


if __name__ == "__main__":
    main()
