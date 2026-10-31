"""Eind-tot-eind: app + scanners + Telegram-knoppen tegen een nagemaakte Kinepolis.

Bedient de app op telefoonformaat, drukt Telegram-knoppen in (nagemaakt) en bewaart
screenshots in SHOTS_DIR (standaard ./shots). Geen echt netwerk.
"""
import asyncio
import datetime as dt
import glob
import os
import re
import tempfile
import threading
import time

os.environ["DATA_DIR"] = tempfile.mkdtemp()
os.environ["LOCATIES"] = "kortrijk=KKOR, brussel=KBRU, gent=KGEN"

from aiohttp import web  # noqa: E402
from playwright.sync_api import expect, sync_playwright  # noqa: E402

import api  # noqa: E402
import core  # noqa: E402
import server  # noqa: E402
from booker import Booker  # noqa: E402
from test_booker import serve  # noqa: E402

SHOTS = os.environ.get("SHOTS_DIR", "shots")
os.makedirs(SHOTS, exist_ok=True)
PORT = 8765
EXE = glob.glob("/opt/pw-browsers/chromium-*/chrome-linux64/chrome")[0]

# ---- nagemaakte Kinepolis ----
# Oude site-links (/movies/detail/..) en nieuwe (/films/<naam>/<HO>) door elkaar, zoals in het echt.
CATALOG = {"HO00012154": ("The Odyssey", "/nl/movies/detail/35300/HO00012154/0/the-odyssey/"),
           "HO00012155": ("The Odyssey – IMAX 70mm Experience",
                          "/films/The-Odyssey-IMAX-70mm-Experience/HO00012155")}


def catalog_page(route):
    links = "".join(f'<a href="{href}" title="{t}"><img alt=""></a>' for t, href in CATALOG.values())
    route.fulfill(status=200, content_type="text/html; charset=utf-8",
                  body=f"<html><body>{links}</body></html>")


day = (dt.datetime.now(api.TZ) + dt.timedelta(days=2)).replace(hour=18, minute=0, second=0, microsecond=0)
SHOWS = {"HO00012154": [api.Showing("KKOR-1", "KKOR", "6", day, "IMAX Laser"),
                   api.Showing("KBRU-3", "KBRU", "1", day + dt.timedelta(hours=2), "IMAX 70mm"),
                   api.Showing("KKOR-5", "KKOR", "2", day + dt.timedelta(days=1), "4DX")]}
api.fetch_showings = lambda mid, *a, **k: SHOWS.get(mid, [])
api.free_seats = lambda sid: 150


class FakeNotifier(core.Notifier):
    """Telegram zonder netwerk: bewaart alles wat verstuurd wordt."""

    def __init__(self):
        super().__init__("token", "42")
        self.sent, self._id = [], 0

    def tg(self, method, files=None, _timeout=30, **params):
        if method == "getUpdates":
            time.sleep(1)
            return []
        self._id += 1
        self.sent.append({"method": method, "photo": bool(files), **params})
        return {"message_id": self._id}

    def texts(self):
        return [m.get("caption") or m.get("text") or "" for m in self.sent]


def factory():
    b = Booker(tempfile.mkdtemp(), headless=True, executable_path=EXE)
    b.context.route(re.compile(r"https://web\.kinepolis\.be/.*"), serve)
    b.context.route(re.compile(r"https://kinepolis\.be/.*"), catalog_page)
    return b


def wait_for(cond, timeout=90, what=""):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return
        time.sleep(0.3)
    raise AssertionError(f"time-out: {what} | job={engine.snapshot().get('state')}")


def press(kind: str):
    """Telegram-knop indrukken voor het huidige voorstel."""
    seq = engine.snapshot().get("proposal_seq")
    engine._telegram_update({"callback_query": {"id": "1", "data": f"{kind}:{seq}",
                                                "message": {"chat": {"id": 42}}}})


# ---- server in een thread ----
store = core.Store(core.DATA / "state.json")
tg = FakeNotifier()
engine = server.Engine(store, tg, factory, interval_s=3, names=server.names,
                       catalog_urls=["https://kinepolis.be/nl/movies"])
engine.start_background()


def run_server():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    runner = web.AppRunner(server.make_app(engine, store))
    loop.run_until_complete(runner.setup())
    loop.run_until_complete(web.TCPSite(runner, "127.0.0.1", PORT).start())
    loop.run_forever()


threading.Thread(target=run_server, daemon=True).start()
time.sleep(1)

with sync_playwright() as p:
    b = p.chromium.launch(executable_path=EXE, args=["--no-sandbox"])
    page = b.new_page(viewport={"width": 390, "height": 844}, device_scale_factor=2, has_touch=True,
                      is_mobile=True, timezone_id="America/New_York", locale="nl-BE")
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(f"http://127.0.0.1:{PORT}/")

    # 1. film op naam zoeken -> kiezen uit wat op Kinepolis staat
    page.fill("#film-url", "odyssey")
    page.click("#film-save")
    expect(page.locator(".match")).to_have_count(2, timeout=60_000)
    page.screenshot(path=f"{SHOTS}/1-film-zoeken.png")
    page.locator("[data-m='0']").click()
    expect(page.locator(".chip.film.on")).to_contain_text("The Odyssey")

    # 2. voorkeur: eerst Brussel, dan Kortrijk -> scanner starten
    page.click("#cinemas >> text=Brussel")
    page.click("#cinemas >> text=Kortrijk")
    expect(page.locator("#cinemas .nr")).to_have_count(2)
    page.screenshot(path=f"{SHOTS}/2-instellen.png", full_page=True)
    page.click("#do-scan")
    expect(page.locator(".scanner")).to_have_count(1)

    # 3. scanner vindt voorstellingen -> voorstel (Brussel eerst: zelfde dag, voorkeur 1)
    wait_for(lambda: engine.snapshot()["state"] == "choose" and engine.snapshot()["proposal"],
             what="eerste voorstel")
    assert engine.snapshot()["showing"]["id"] == "KBRU-3", engine.snapshot()["showing"]
    prop = [m for m in tg.sent if m["method"] == "sendPhoto"][-1]
    assert prop["photo"] and "Zal ik ze in je mandje zetten" in prop["caption"], prop
    assert "yes:" in prop["reply_markup"] and "next:" in prop["reply_markup"]
    print("Telegram-voorstel:\n" + prop["caption"])
    page.click(".tabs >> text=Boeking")
    expect(page.locator("#next")).to_be_visible()
    page.screenshot(path=f"{SHOTS}/3-voorstel.png", full_page=True)

    # 4. Telegram: Volgende -> Kortrijk dezelfde dag; dan Ja -> mandje
    press("next")
    wait_for(lambda: engine.snapshot()["state"] == "choose"
             and engine.snapshot()["showing"]["id"] == "KKOR-1", what="tweede voorstel")
    press("yes")
    wait_for(lambda: engine.snapshot()["state"] == "in_cart", what="mandje")
    done = [m for m in tg.sent if m["method"] == "sendPhoto"][-1]
    assert "mandje" in done["caption"] and "stop:" in done["reply_markup"], done
    print("\nTelegram-resultaat:\n" + done["caption"])
    expect(page.locator("#go-live")).to_be_visible(timeout=10_000)
    page.screenshot(path=f"{SHOTS}/4-mandje.png", full_page=True)

    # 5. tweede scanner op naam voor een film die nog niet op Kinepolis staat
    page.click(".tabs >> text=Zoeken")
    page.click("#film-new")
    page.fill("#film-url", "Dune part 3")
    page.click("#film-save")
    expect(page.locator(".chip.film.on")).to_contain_text("⏳", timeout=60_000)
    page.click("#do-search")
    expect(page.locator("#results")).to_contain_text("nog niet op Kinepolis")
    page.click("#cta-scan")
    expect(page.locator(".scanner")).to_have_count(2)
    expect(page.locator(".scanner .state.wait")).to_have_count(1)

    # pauzeren en hervatten werkt
    page.locator(".scanner").first.locator("[data-a='pause']").click()
    expect(page.locator(".scanner.paused")).to_have_count(1)
    page.screenshot(path=f"{SHOTS}/5-scanners.png", full_page=True)
    page.locator(".scanner.paused [data-a='resume']").click()
    expect(page.locator(".scanner.paused")).to_have_count(0)

    # 6. de film verschijnt op Kinepolis -> scanner schakelt vanzelf over
    CATALOG["HO00020000"] = ("Dune: Part Three", "/films/Dune-Part-Three/HO00020000")
    SHOWS["HO00020000"] = []
    engine.tasks.put((engine._check_catalog, ()))
    wait_for(lambda: "HO00020000" in store.data["films"], 60, "film opgedoken")
    assert all(not w["movie_id"].startswith(core.PENDING) for w in store.data["watches"])
    assert any("Dune: Part Three staat nu op Kinepolis" in t for t in tg.texts()), tg.texts()
    page.click(".tabs >> text=Zoeken")
    page.click(".tabs >> text=Scanners")
    expect(page.locator(".scanner .state.wait")).to_have_count(0)
    expect(page.locator(".scanner").nth(1)).to_contain_text("Dune: Part Three")
    expect(page.locator(".chip.film.on")).to_have_text("Dune: Part Three✕")      # geen ⏳ meer
    page.screenshot(path=f"{SHOTS}/6-film-verschenen.png", full_page=True)

    # en zodra er voorstellingen komen: voorstel (oneindig doorscannen)
    engine.update(state="idle", proposal=False)
    SHOWS["HO00020000"] = [api.Showing("KGEN-7", "KGEN", "3", day + dt.timedelta(days=5), "IMAX")]
    wait_for(lambda: engine.snapshot()["state"] == "choose"
             and engine.snapshot()["showing"]["id"] == "KGEN-7", what="voorstel Dune")
    wait_for(lambda: any("Dune: Part Three" in (m.get("caption") or "") and "Zal ik" in m["caption"]
                         for m in tg.sent), 30, "Telegram-voorstel Dune")

    assert not errors, errors
    b.close()
print("\nOK, screenshots in", SHOTS)
