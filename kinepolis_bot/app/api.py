"""Kinepolis-API's: programmatie (voorstellingen per film) en OCAPI (vrije plaatsen).

Voorstellingen komen uit dezelfde Sessions-API als de odyssey-scanner. De
voorstellings-id voor de bestelsite is "<complexOperator>-<vistaSessionId>",
bv. KKOR-164609 -> https://web.kinepolis.be/order/showtimes/KKOR-164609/seats
"""
from __future__ import annotations

import datetime as dt
import re
import secrets
import string
from dataclasses import dataclass
from zoneinfo import ZoneInfo

from curl_cffi import requests as cffi_requests

TZ = ZoneInfo("Europe/Brussels")
PROG_API = "https://kinepolisweb-programmation.kinepolis.com/api/"
OCAPI = "https://digital-api.kinepolis.be/ocapi/v1/"
WEB = "https://web.kinepolis.be"


@dataclass(frozen=True)
class Showing:
    id: str
    cinema: str
    hall: str
    start: dt.datetime
    attrs: str

    @property
    def seats_url(self) -> str:
        return f"{WEB}/order/showtimes/{self.id}/seats"


def movie_id_from(text: str) -> str | None:
    """Film-id uit een kinepolis.be-link (/movies/detail/35300/...) of een kaal getal."""
    m = re.search(r"/movies/detail/(\d+)", text) or re.fullmatch(r"\s*(\d+)\s*", text)
    return m.group(1) if m else None


def fetch_showings(movie_id: str, country: str = "BE/NL", channel: str = "Cinema",
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


def filter_showings(showings: list[Showing], cinemas: set[str], date_from: dt.date,
                    date_to: dt.date, fmt: str = "") -> list[Showing]:
    return [s for s in showings
            if (not cinemas or s.cinema in cinemas)
            and date_from <= s.start.date() <= date_to
            and (not fmt or fmt.lower() in s.attrs.lower())
            and s.start > dt.datetime.now(TZ)]


def _correlation_id() -> str:
    alphabet = string.ascii_lowercase + string.digits
    return "OCC-" + "".join(secrets.choice(alphabet) for _ in range(33))


def free_seats(showing_id: str) -> int | None:
    """Aantal vrije plaatsen (incl. cosy/rolstoel, dus een bovengrens). None = onbekend."""
    try:
        r = cffi_requests.get(f"{OCAPI}showtimes/{showing_id}/seat-availability",
                              impersonate="chrome124", timeout=20,
                              headers={"Accept": "application/json", "Origin": WEB,
                                       "Referer": WEB + "/", "correlationid": _correlation_id()})
        if r.status_code != 200:
            return None
        return int(r.json()["summary"]["availableCount"])
    except Exception:
        return None
