"""Kinepolis-API's: voorstellingen per film en vrije plaatsen.

Twee bronnen:
- OCAPI (nieuwe site, web.kinepolis.be): film = HO-code (bv. HO00013277).
  GET /ocapi/v1/showtimes/by-business-date/first?filmIds=..&siteIds=..  -> eerste speeldag
  GET /ocapi/v1/showtimes/by-business-date/<datum>?filmIds=..&siteIds=.. -> die dag
- Programmatie-API (oude site): film = getal (bv. 35300), zoals in de odyssey-scanner.
De voorstellings-id is in beide gevallen "<cinema>-<sessie>", bv. KKOR-164637 ->
https://web.kinepolis.be/order/showtimes/KKOR-164637/seats
"""
from __future__ import annotations

import base64
import datetime as dt
import json
import re
import threading
import secrets
import string
import time
from dataclasses import dataclass
from zoneinfo import ZoneInfo

from curl_cffi import requests as cffi_requests

TZ = ZoneInfo("Europe/Brussels")
PROG_API = "https://kinepolisweb-programmation.kinepolis.com/api/"
OCAPI = "https://digital-api.kinepolis.be/ocapi/v1/"
WEB = "https://web.kinepolis.be"
SITE = "https://www.kinepolis.be"
HO_RE = re.compile(r"HO\d{8}")
WINDOW_DAYS = 14            # zoveel dagen na de eerste speeldag halen we op
DAY_CACHE_S = 300           # latere dagen veranderen zelden: 5 min bewaren

# Geleerd uit de antwoorden: cinemacode -> naam, film-id -> titel.
SITE_NAMES: dict[str, str] = {}
KNOWN_SITES: set[str] = set()   # uit de instelling 'locaties' (gezet door de server)
FILM_TITLES: dict[str, str] = {}


@dataclass(frozen=True)
class Showing:
    id: str
    cinema: str
    hall: str
    start: dt.datetime
    attrs: str
    sold_out: bool = False

    @property
    def seats_url(self) -> str:
        return f"{WEB}/order/showtimes/{self.id}/seats"


def movie_id_from(text: str) -> str | None:
    """Film-id uit een link of tekst: HO-code (nieuwe site) of getal (oude site)."""
    if m := HO_RE.search(text or ""):
        return m[0]
    m = re.search(r"/movies/detail/(\d+)", text or "") or re.fullmatch(r"\s*(\d+)\s*", text or "")
    return m.group(1) if m else None


# ---------------- OCAPI (nieuwe site) ----------------

def _correlation_id() -> str:
    alphabet = string.ascii_lowercase + string.digits
    return "OCC-" + "".join(secrets.choice(alphabet) for _ in range(33))


# Toegangssleutel (anoniem, ±12 uur geldig) die de Kinepolis-site zelf gebruikt.
# token_provider haalt een nieuwe op (de server laat de browser dat doen).
token_provider = None
_token = {"value": "", "exp": 0.0, "retry_at": 0.0}
_token_lock = threading.Lock()


def _jwt_exp(tok: str) -> float:
    try:
        payload = tok.split(".")[1]
        return float(json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))["exp"])
    except Exception:
        return time.time() + 3600


def api_token(force: bool = False) -> str:
    """Geldige sleutel (5 min marge), of een nieuwe via token_provider."""
    with _token_lock:
        if not force and _token["value"] and time.time() < _token["exp"] - 300:
            return _token["value"]
        if token_provider is not None and time.time() >= _token["retry_at"]:
            tok = token_provider()
            if not tok:                               # niet gelukt: browser even met rust laten
                _token["retry_at"] = time.time() + 300
                print("[sleutel] geen Kinepolis-sleutel kunnen opvangen; opnieuw over 5 min", flush=True)
            else:
                _token["retry_at"] = 0.0
                _token.update(value=tok, exp=_jwt_exp(tok))
                print(f"[sleutel] nieuwe Kinepolis-sleutel, geldig tot "
                      f"{dt.datetime.fromtimestamp(_token['exp'], TZ):%H:%M}", flush=True)
        return _token["value"]


def _headers() -> dict:
    h = {"Accept": "application/json", "Origin": SITE, "Referer": SITE + "/",
         "correlationid": _correlation_id()}
    if tok := api_token():
        h["Authorization"] = f"Bearer {tok}"
    return h


def _ocapi(path: str, params: list[tuple[str, str]]) -> dict | None:
    """GET op OCAPI. None = geen voorstellingen (404/204)."""
    r = cffi_requests.get(OCAPI + path, params=params, impersonate="chrome124", timeout=20,
                          headers=_headers())
    if r.status_code == 401:                       # sleutel vervallen of ontbrak: vernieuwen
        api_token(force=True)
        r = cffi_requests.get(OCAPI + path, params=params, impersonate="chrome124", timeout=20,
                              headers=_headers())
        if r.status_code == 401:
            raise RuntimeError("Kinepolis weigert de toegang (401): kon geen geldige sleutel "
                               "ophalen via de browser.")
    if r.status_code in (204, 404):
        return None
    r.raise_for_status()
    return r.json() if r.content else None


def _text(obj) -> str:
    return (obj or {}).get("text", "") if isinstance(obj, dict) else str(obj or "")


def parse_ocapi(data: dict | None) -> list[Showing]:
    """Antwoord van by-business-date -> voorstellingen (en leer cinema- en filmnamen)."""
    if not data:
        return []
    rel = data.get("relatedData") or {}
    screens = {s["id"]: _text(s.get("name")) for s in rel.get("screens", [])}
    attrs = {a["id"]: _text(a.get("name")) for a in rel.get("attributes", [])}
    for s in rel.get("sites", []):
        SITE_NAMES[s["id"]] = re.sub(r"^Kinepolis\s+", "", _text(s.get("name"))) or s["id"]
    for f in rel.get("films", []):
        FILM_TITLES[f["id"]] = _text(f.get("title")) or f["id"]
    out = []
    for st in data.get("showtimes", []):
        try:
            start = dt.datetime.fromisoformat(st["schedule"]["startsAt"]).astimezone(TZ)
        except (KeyError, TypeError, ValueError):
            continue
        hall = re.sub(r"^Zaal\s*", "", screens.get(st.get("screenId"), st.get("screenId", "")))
        out.append(Showing(id=st["id"], cinema=st.get("siteId") or st["id"].split("-")[0],
                           hall=hall, start=start,
                           attrs=" ".join(attrs.get(a, "") for a in st.get("attributeIds", [])),
                           sold_out=bool(st.get("isSoldOut"))))
    return out


_day_cache: dict[tuple, tuple[float, list[Showing]]] = {}


def _params(film: str, sites: list[str]) -> list[tuple[str, str]]:
    return [("filmIds", film)] + [("siteIds", s) for s in sites]


def fetch_ocapi(film: str, sites: list[str], date_from: dt.date | None = None,
                date_to: dt.date | None = None) -> list[Showing]:
    """Voorstellingen van een HO-film: eerste speeldag (altijd vers) + volgende dagen."""
    if not sites:
        try:
            return _fetch_ocapi(film, [], date_from, date_to)
        except Exception:
            known = sorted(KNOWN_SITES | set(SITE_NAMES))   # zonder cinema's mocht niet
            if not known:
                raise
            return _fetch_ocapi(film, known, date_from, date_to)
    return _fetch_ocapi(film, sites, date_from, date_to)


def _fetch_ocapi(film: str, sites: list[str], date_from: dt.date | None,
                 date_to: dt.date | None) -> list[Showing]:
    # Eerste speeldag telkens vers (Kinepolis cachet 10 min): zo zie je een drop meteen.
    first = None
    for bust in (True, False):
        try:
            p = _params(film, sites) + ([("_", str(int(time.time())))] if bust else [])
            first = _ocapi("showtimes/by-business-date/first", p)
            break
        except Exception:
            if not bust:
                raise
    shows = parse_ocapi(first)
    if not first or not first.get("businessDate"):
        return shows
    day = dt.date.fromisoformat(first["businessDate"]) + dt.timedelta(days=1)
    start = max(day, date_from or day)
    end = min(date_to or start + dt.timedelta(days=WINDOW_DAYS),
              dt.date.fromisoformat(first["businessDate"]) + dt.timedelta(days=WINDOW_DAYS))
    d = start
    while d <= end:
        key = (film, tuple(sites), d)
        hit = _day_cache.get(key)
        if hit and time.time() - hit[0] < DAY_CACHE_S:
            shows += hit[1]
        else:
            day_shows = parse_ocapi(_ocapi(f"showtimes/by-business-date/{d.isoformat()}",
                                           _params(film, sites)))
            _day_cache[key] = (time.time(), day_shows)
            shows += day_shows
        d += dt.timedelta(days=1)
    return sorted({s.id: s for s in shows}.values(), key=lambda s: s.start)


# ---------------- programmatie-API (oude site) ----------------

def fetch_programmation(movie_id: str, country: str = "BE/NL", channel: str = "Cinema",
                        sales: str = "KinepolisBelgium") -> list[Showing]:
    url = f"{PROG_API}Sessions/{country}/{movie_id}/WWW/{channel}/{sales}"
    r = cffi_requests.get(url, impersonate="chrome124", timeout=30, headers={"Accept": "*/*"})
    r.raise_for_status()
    out = []
    for s in r.json():
        if not isinstance(s, dict) or not s.get("complexOperator") or not s.get("vistaSessionId"):
            continue
        try:
            start = dt.datetime.fromisoformat(s["showtime"]).astimezone(TZ)
        except (KeyError, TypeError, ValueError):
            continue
        out.append(Showing(id=f"{s['complexOperator']}-{s['vistaSessionId']}",
                           cinema=s["complexOperator"], hall=str(s.get("hall", "")),
                           start=start, attrs=str(s.get("rawSessionAttributes", ""))))
    return sorted(out, key=lambda x: x.start)


def fetch_showings(movie_id: str, sites: list[str] | None = None,
                   date_from: dt.date | None = None, date_to: dt.date | None = None) -> list[Showing]:
    """Alle voorstellingen van een film (HO-code of oud getal)."""
    if HO_RE.fullmatch(movie_id):
        return fetch_ocapi(movie_id, list(sites or []), date_from, date_to)
    return fetch_programmation(movie_id)


def filter_showings(showings: list[Showing], cinemas: set[str], date_from: dt.date,
                    date_to: dt.date, fmt: str = "") -> list[Showing]:
    return [s for s in showings
            if (not cinemas or s.cinema in cinemas)
            and date_from <= s.start.date() <= date_to
            and (not fmt or fmt.lower() in s.attrs.lower())
            and not s.sold_out
            and s.start > dt.datetime.now(TZ)]


def free_seats(showing_id: str) -> int | None:
    """Aantal vrije plaatsen (incl. cosy/rolstoel, dus een bovengrens). None = onbekend."""
    try:
        data = _ocapi(f"showtimes/{showing_id}/seat-availability", [])
        return int(data["summary"]["availableCount"]) if data else None
    except Exception:
        return None
