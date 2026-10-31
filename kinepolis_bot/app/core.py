"""Gedeelde logica: instellingen, opslag, zoeken en (optionele) Telegram-meldingen."""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import threading
import unicodedata
from difflib import SequenceMatcher
from dataclasses import dataclass
from pathlib import Path

import requests

import api

DATA = Path(os.environ.get("DATA_DIR", "/data"))
DEFAULT_ALIASES = {"kortrijk": "KKOR", "brussel": "KBRU", "leuven": "SL"}
DEFAULT_FILM_PAGES = "https://www.kinepolis.be/nl/films/, https://web.kinepolis.be/films"
DAYS = ["ma", "di", "wo", "do", "vr", "za", "zo"]
# Herkenbare formaten in rawSessionAttributes (vrije tekst van Kinepolis).
FORMATS = ["IMAX", "70mm", "Laser ULTRA", "4DX", "ScreenX", "Dolby Atmos", "3D", "OV"]


def load_options() -> dict:
    f = DATA / "options.json"
    opts = json.loads(f.read_text()) if f.exists() else {}
    opts.setdefault("telegram_token", os.environ.get("TG_TOKEN", ""))
    opts.setdefault("telegram_chat_id", os.environ.get("TG_CHAT_ID", ""))
    opts.setdefault("locaties", os.environ.get("LOCATIES", ""))
    # Pagina's waar de scanner zoekt of een film (op naam) al op Kinepolis staat.
    opts.setdefault("film_paginas", DEFAULT_FILM_PAGES)
    opts.setdefault("scan_interval_seconds", 30)
    opts.setdefault("ha_notify_service", os.environ.get("HA_NOTIFY", ""))
    return opts


def parse_aliases(text: str) -> dict[str, str]:
    """'gent=KGEN, antwerpen=KANT' -> {'gent': 'KGEN', ...} bovenop de standaard."""
    out = dict(DEFAULT_ALIASES)
    for part in re.split(r"[,;\n]", text or ""):
        if "=" in part:
            name, code = part.split("=", 1)
            out[name.strip().lower()] = code.strip().upper()
    return out


def film_name_from(url: str, movie_id: str) -> str:
    """Titel van een film: uit de API (als al gezien), anders uit de link."""
    if movie_id in api.FILM_TITLES:
        return api.FILM_TITLES[movie_id]
    m = (re.search(r"/movies/detail/\d+/[^/]+/[^/]+/([^/?#]+)", url)
         or re.search(r"/films/([^/?#]+)/HO\d{8}", url))
    return m[1].replace("-", " ").title() if m else f"Film {movie_id}"


def norm(text: str) -> str:
    """'The Odyssey: Part II!' -> 'the odyssey part ii' (zonder accenten)."""
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return " ".join(re.sub(r"[^a-z0-9]+", " ", text.lower()).split())


# "Part 3" = "Part III" = "Part Three" (ook in het Frans/Nederlands)
_SAME = {w: str(n) for n, ws in enumerate(
    [(), ("one", "i", "een", "un"), ("two", "ii", "twee", "deux"), ("three", "iii", "drie", "trois"),
     ("four", "iv", "vier", "quatre"), ("five", "v", "vijf", "cinq")]) for w in ws}


def match_films(catalog: list[dict], name: str) -> list[dict]:
    """Films waarvan de titel of de naam in de link alle woorden van `name` bevat."""
    words = [_SAME.get(w, w) for w in norm(name).split()]
    if not words:
        return []
    def close(w: str, hay: list[str]) -> bool:          # "duen" ~ "dune"
        return len(w) >= 4 and any(len(h) >= 4 and SequenceMatcher(None, w, h).ratio() >= 0.75
                                   for h in hay)

    hits = []
    for f in catalog:
        hay = [_SAME.get(w, w) for w in f"{norm(f['title'])} {norm(f['slug'])}".split()]
        exact_words = all(w in hay for w in words)
        if exact_words or all(w in hay or close(w, hay) for w in words):
            exact = norm(f["title"]) == norm(name) or norm(f["slug"]) == norm(name)
            hits.append((not exact_words, not exact, len(f["slug"]), f))
    return [f for *_, f in sorted(hits, key=lambda x: x[:3])]


PENDING = "naam:"            # film-id van een film die nog niet op Kinepolis staat


def pending_id(name: str) -> str:
    return PENDING + norm(name)


def formats_of(attrs: str) -> list[str]:
    low = attrs.lower()
    return [f for f in FORMATS if f.lower() in low]


def when(d: dt.datetime) -> str:
    return f"{DAYS[d.weekday()]} {d:%d/%m %H:%M}"


LONG_DAYS = ["maandag", "dinsdag", "woensdag", "donderdag", "vrijdag", "zaterdag", "zondag"]


def when_long(iso: str) -> str:
    d = dt.datetime.fromisoformat(iso).astimezone(api.TZ)
    return f"{LONG_DAYS[d.weekday()]} {d.day}/{d.month} om {d:%H:%M}"


def seat_text(labels: list[str]) -> str:
    """['Normale zitplaats 0811', '.. 0812'] -> 'rij 8, stoel 11 en 12'."""
    rows: dict[int, list[int]] = {}
    for label in labels:
        m = re.search(r"(\d{2})(\d{2})\s*$", label)
        if m:
            rows.setdefault(int(m[1]), []).append(int(m[2]))
    parts = []
    for row, nums in sorted(rows.items()):
        nums.sort()
        seats = ", ".join(map(str, nums[:-1])) + f" en {nums[-1]}" if len(nums) > 1 else str(nums[0])
        parts.append(f"rij {row}, stoel{'en' if len(nums) > 1 else ''} {seats}")
    return " · ".join(parts) or ", ".join(labels)


@dataclass
class Query:
    persons: int
    cinemas: list[str]          # volgorde = voorkeur (eerste = liefst)
    date_from: dt.date
    date_to: dt.date
    fmt: str = ""
    auto: bool = False          # meteen de beste plaatsen in het mandje
    time_from: str = ""         # "19:00" = niet vroeger dan

    def describe(self, names: dict[str, str] | None = None) -> str:
        names = names or {}
        where = " > ".join(names.get(c, c) for c in self.cinemas) or "overal"
        span = (f"{self.date_from:%d/%m}" if self.date_from == self.date_to else
                f"{self.date_from:%d/%m/%y} – {self.date_to:%d/%m/%y}")
        return (f"{self.persons} pers. · {where} · {span}" + (f" · {self.fmt}" if self.fmt else "")
                + (f" · vanaf {self.time_from}" if self.time_from else ""))

    def to_json(self) -> dict:
        return {"persons": self.persons, "cinemas": self.cinemas,
                "from": self.date_from.isoformat(), "to": self.date_to.isoformat(),
                "fmt": self.fmt, "auto": self.auto, "time_from": self.time_from}

    @classmethod
    def from_json(cls, d: dict) -> "Query":
        persons = int(d["persons"])
        if not 1 <= persons <= 12:
            raise ValueError("Aantal personen moet tussen 1 en 12 liggen.")
        cinemas = list(dict.fromkeys(c.upper() for c in d.get("cinemas", [])))
        time_from = (d.get("time_from") or "").strip()
        if time_from and not re.fullmatch(r"\d{2}:\d{2}", time_from):
            raise ValueError("Tijd moet zoals 19:00 zijn.")
        q = cls(persons, cinemas, dt.date.fromisoformat(d["from"]), dt.date.fromisoformat(d["to"]),
                d.get("fmt", "").strip(), bool(d.get("auto", False)), time_from)
        if q.date_to < q.date_from:
            q.date_from, q.date_to = q.date_to, q.date_from
        return q

    def matches(self, showings: list[api.Showing]) -> list[api.Showing]:
        found = api.filter_showings(showings, set(self.cinemas), self.date_from,
                                    self.date_to, self.fmt)
        return [s for s in found if not self.time_from or f"{s.start:%H:%M}" >= self.time_from]

    def rank(self, showings: list[api.Showing]) -> list[api.Showing]:
        """Voor automatisch boeken: vroegste dag eerst, dan je favoriete cinema, dan uur."""
        pref = {c: i for i, c in enumerate(self.cinemas)}
        return sorted(showings, key=lambda s: (s.start.date(), pref.get(s.cinema, 99), s.start))


def showing_json(s: api.Showing, free: int | None = None) -> dict:
    return {"id": s.id, "cinema": s.cinema, "hall": s.hall, "start": s.start.isoformat(),
            "when": when(s.start), "formats": formats_of(s.attrs), "free": free}


def search(movie_id: str, q: Query, limit: int = 40) -> list[dict]:
    out = []
    for s in q.matches(api.fetch_showings(movie_id, q.cinemas, q.date_from, q.date_to))[:limit]:
        free = api.free_seats(s.id)
        if free is None or free >= q.persons:
            out.append(showing_json(s, free))
    return out


class Store:
    """Films, gevolgde zoekopdrachten, al geziene voorstellingen en meldingen."""

    def __init__(self, path: Path):
        self.path = path
        self.lock = threading.RLock()
        self.data = json.loads(path.read_text()) if path.exists() else {}
        for key, default in (("films", {}), ("movie_id", None), ("watches", []),
                             ("seen", []), ("events", [])):
            self.data.setdefault(key, default)

    def save(self):
        with self.lock:
            self.data["seen"] = self.data["seen"][-3000:]
            self.data["events"] = self.data["events"][-50:]
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.data, indent=2))
            tmp.replace(self.path)

    def add_event(self, text: str, kind: str = "info", showing: dict | None = None):
        with self.lock:
            self.data["events"].append({"time": dt.datetime.now(api.TZ).isoformat(timespec="seconds"),
                                        "text": text, "kind": kind, "showing": showing})
            self.save()


class Supervisor:
    """Praat met Home Assistant over deze add-on zelf: versie en bijwerken."""

    base = "http://supervisor"

    def __init__(self):
        self.token = os.environ.get("SUPERVISOR_TOKEN", "")

    def _call(self, method: str, path: str, timeout: int = 60) -> dict:
        r = requests.request(method, self.base + path, timeout=timeout,
                             headers={"Authorization": f"Bearer {self.token}"})
        r.raise_for_status()
        return (r.json() or {}).get("data") or {}

    def version(self) -> dict:
        """{'installed', 'latest', 'update'}; leeg buiten Home Assistant."""
        if not self.token:
            return {}
        info = self._call("GET", "/addons/self/info", 15)
        return {"installed": info.get("version"), "latest": info.get("version_latest"),
                "update": bool(info.get("update_available")), "slug": info.get("slug")}

    def check(self) -> dict:
        """Laat Home Assistant opnieuw op GitHub kijken en geef de versies terug."""
        if not self.token:
            return {}
        self._call("POST", "/store/reload", 120)
        return self.version()


class Notifier:
    """Meldingen via de Home Assistant-app (push) en/of Telegram. Beide optioneel."""

    def __init__(self, token: str = "", chat_id: str = "", ha_service: str = ""):
        # Vaak geplakt met spaties, aanhalingstekens of "bot" ervoor: dat ruimen we op.
        token = (token or "").strip().strip("\"'").strip()
        self.token = re.sub(r"^bot(?=\d+:)", "", token)
        self.chat_id = str(chat_id or "").strip().strip("\"'").strip()
        self.error = ""                         # laatste Telegram-fout, voor de app
        self.ha_service = ha_service.strip().removeprefix("notify.")
        self.ha_token = os.environ.get("SUPERVISOR_TOKEN", "")
        self._panel_url: str | None = None

    @property
    def panel_url(self) -> str:
        """Link naar de app in HA (tik op de melding). De naam hangt af van waar de add-on vandaan komt."""
        if self._panel_url is None:
            slug = os.environ.get("ADDON_SLUG", "")
            if not slug and self.ha_token:
                try:
                    r = requests.get("http://supervisor/addons/self/info", timeout=10,
                                     headers={"Authorization": f"Bearer {self.ha_token}"})
                    slug = r.json().get("data", {}).get("slug", "")
                except (requests.RequestException, ValueError):
                    pass
            self._panel_url = f"/hassio/ingress/{slug}" if slug else "/"
        return self._panel_url

    def describe(self) -> str:
        """Korte status voor het log bij het opstarten (zonder de token te tonen)."""
        if not self.token:
            return "Telegram uit: telegram_token is leeg."
        return f"Telegram aan: token …{self.token[-4:]}, chat-id {self.chat_id or '(leeg!)'}"

    @property
    def enabled(self) -> bool:
        return bool(self.token and self.chat_id) or bool(self.ha_service and self.ha_token)

    def _ha(self, path: str, payload: dict):
        try:
            r = requests.post(f"http://supervisor/core/api/services/{path}", json=payload, timeout=15,
                              headers={"Authorization": f"Bearer {self.ha_token}"})
            if not r.ok:
                print("[ha-melding]", r.status_code, r.text[:200], flush=True)
        except requests.RequestException as e:
            print("[ha-melding]", e, flush=True)

    def tg(self, method: str, files=None, _timeout: int = 30, **params):
        """Ruwe Telegram-aanroep; geeft 'result' terug of None."""
        if not (self.token and self.chat_id):
            return None
        try:
            r = requests.post(f"https://api.telegram.org/bot{self.token}/{method}", data=params,
                              files=files, timeout=_timeout)
            body = r.json()
            if not body.get("ok"):
                self.error = f"{method}: {body.get('description')}"
                print(f"[telegram] {self.error}", flush=True)
                return None
            if method == "getUpdates":
                self.error = ""
            return body.get("result")
        except (requests.RequestException, ValueError) as e:
            self.error = f"Telegram niet bereikbaar ({type(e).__name__})"
            print("[telegram]", e, flush=True)
            return None

    def _ha_push(self, text: str, urgent: bool):
        if not self.ha_token:
            return
        if urgent:                              # blijft staan in Home Assistant zelf
            self._ha("persistent_notification/create",
                     {"title": "Kinepolis", "message": text, "notification_id": "kinepolis"})
        if self.ha_service:
            data = {"url": self.panel_url, "clickAction": self.panel_url}
            if urgent:
                data |= {"push": {"interruption-level": "time-sensitive"},
                         "priority": "high", "ttl": 0, "channel": "Kinepolis"}
            self._ha(f"notify/{self.ha_service}", {"title": "🎬 Kinepolis", "message": text, "data": data})

    def send_buttons(self, text: str, photo: bytes | None, buttons, urgent: bool = False) -> int | None:
        """Melding in HA + Telegram-bericht (met foto en knoppen). Geeft het bericht-id terug."""
        self._ha_push(text, urgent)
        markup = {"reply_markup": json.dumps({"inline_keyboard": buttons})} if buttons else {}
        if photo:
            res = self.tg("sendPhoto", files={"photo": ("kinepolis.png", photo, "image/png")},
                          _timeout=60, chat_id=self.chat_id, caption=text[:1000], **markup)
        else:
            res = self.tg("sendMessage", chat_id=self.chat_id, text=text[:4000], **markup)
        return res.get("message_id") if res else None

    def send(self, text: str, photo: bytes | None = None, urgent: bool = False) -> int | None:
        return self.send_buttons(text, photo, None, urgent)
