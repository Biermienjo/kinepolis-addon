"""Test de OCAPI-bron met een echt antwoord van Kinepolis (testdata/ocapi_first.json).

In de testdata is KKOR-164639 als uitverkocht gemarkeerd om dat filter te testen.
"""
import copy
import datetime as dt
import json
from pathlib import Path

import api

FIRST = json.loads((Path(__file__).parent / "testdata" / "ocapi_first.json").read_text())
calls = []


class Resp:
    def __init__(self, status, body=None):
        self.status_code, self._body = status, body
        self.content = b"x" if body is not None else b""

    def json(self):
        return self._body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


TOKEN = "x." + api.base64.urlsafe_b64encode(
    api.json.dumps({"exp": api.time.time() + 12 * 3600}).encode()).decode().rstrip("=") + ".y"
provided = []
api.token_provider = lambda: provided.append(1) or TOKEN


def fake_get(url, params=None, headers=None, **kw):
    if (headers or {}).get("Authorization") != f"Bearer {TOKEN}":
        return Resp(401, {"error": "unauthorized"})                 # zoals Kinepolis
    calls.append((url.replace(api.OCAPI, ""), params))
    p = dict(params)
    if "siteIds" not in p:
        return Resp(400, {"error": "siteIds required"})            # 'overal' niet toegestaan
    if url.endswith("/first"):
        return Resp(200, FIRST)
    if url.endswith("/2026-11-03"):                                 # een dag later: 1 voorstelling
        d = copy.deepcopy(FIRST)
        d["businessDate"] = "2026-11-03"
        d["showtimes"] = [dict(d["showtimes"][0], id="KKOR-200001",
                               schedule={"startsAt": "2026-11-03T20:00:00+01:00"})]
        return Resp(200, d)
    return Resp(404)


api.cffi_requests.get = fake_get
real_now = api.dt.datetime


class Now(dt.datetime):
    @classmethod
    def now(cls, tz=None):
        return real_now(2026, 10, 31, 12, 0, tzinfo=tz)


api.dt.datetime = Now

# met een cinema
shows = api.fetch_showings("HO00013277", ["KKOR"], dt.date(2026, 10, 31), dt.date(2026, 11, 5))
assert [s.id for s in shows] == ["KKOR-164637", "KKOR-164638", "KKOR-164639", "KKOR-200001"], shows
assert "_" in dict(calls[0][1]), "eerste speeldag moet vers opgevraagd worden"
assert calls[1][0] == "showtimes/by-business-date/2026-11-02"
kept = api.filter_showings(shows, {"KKOR"}, dt.date(2026, 10, 31), dt.date(2026, 11, 5))
assert "KKOR-164639" not in [s.id for s in kept], "uitverkocht moet weg"
assert api.SITE_NAMES["KKOR"] == "Kortrijk" and api.FILM_TITLES["HO00013277"].startswith("Shaun")
print("met cinema:", [f"{s.id} {s.start:%d/%m %H:%M} zaal {s.hall}" for s in kept])

# 'overal': Kinepolis weigert zonder siteIds -> alle gekende cinema's
calls.clear()
api.KNOWN_SITES.update({"KBRU", "SL"})
shows = api.fetch_showings("HO00013277", [], dt.date(2026, 10, 31), dt.date(2026, 11, 1))
sites = [v for k, v in calls[-1][1] if k == "siteIds"]
assert sorted(sites) == ["KBRU", "KKOR", "SL"], sites
print("overal -> cinema's:", sorted(sites), "|", len(shows), "voorstellingen")

# sleutel: één keer opgehaald, daarna hergebruikt; vervallen -> 401 -> nieuwe
assert len(provided) == 1, provided
api._token["value"] = "oud.eyJleHAiOjk5OTk5OTk5OTl9.z"                # 'geldig' maar geweigerd
api.fetch_showings("HO00013277", ["KKOR"], dt.date(2026, 10, 31), dt.date(2026, 11, 1))
assert len(provided) == 2 and api._token["value"] == TOKEN, provided
print("sleutel: opgehaald", len(provided), "keer, vernieuwd na 401")

# oude en nieuwe links
assert api.movie_id_from("https://web.kinepolis.be/films/Shaun-Het-Schaap/HO00013277") == "HO00013277"
assert api.movie_id_from("https://kinepolis.be/nl/movies/detail/35300/HO00012154/0/the-odyssey/") == "HO00012154"
assert api.movie_id_from("35300") == "35300"
print("OK")
