"""Test booker.py tegen een nagemaakte Kinepolis-site (verkeer wordt afgevangen).

De stoelpagina volgt de echte structuur; de ticketpagina is een gok (nooit gezien),
dus deze test bewijst de flow, niet dat de echte ticketpagina zo werkt.
"""
import glob
import re
import tempfile

from booker import Booker
from test_picker import build_html

SEATS_HTML = build_html().replace("</body>", """
<button id="go" disabled onclick="
  const sel = document.querySelectorAll('.v-seat-picker-seat--selected').length;
  location.href = '/order/showtimes/KKOR-1/tickets?n=' + sel">Tickets selecteren</button>
<script>setTimeout(() => document.getElementById('go').disabled = false, 2000)</script>
</body>""")

TICKETS_HTML = """<html><body>
<div class="ticket"><span>Volwassene</span><span>€ 13,90</span>
  <button aria-label="Verlaag">-</button><span id="q">0</span>
  <button aria-label="Verhoog" onclick="q.textContent = +q.textContent + 1">+</button></div>
<div class="ticket"><span>Kind</span><button aria-label="Verhoog">+</button></div>
<button onclick="location.href='/order/customer-details?q=' + q.textContent">Doorgaan</button>
</body></html>"""

CUSTOMER_HTML = "<html><body><h1>Je gegevens</h1><button>Betalen</button></body></html>"


def serve(route):
    url = route.request.url
    body = SEATS_HTML if "/seats" in url else TICKETS_HTML if "/tickets" in url else CUSTOMER_HTML
    route.fulfill(status=200, content_type="text/html", body=body)


def mock_booker(profile: str) -> Booker:
    exe = glob.glob("/opt/pw-browsers/chromium-*/chrome-linux64/chrome")[0]
    b = Booker(profile, headless=True, executable_path=exe)
    b.context.route(re.compile(r"https://web\.kinepolis\.be/.*"), serve)
    return b


if __name__ == "__main__":
    with tempfile.TemporaryDirectory() as profile:
        b = mock_booker(profile)
        url = "https://web.kinepolis.be/order/showtimes/KKOR-1/seats"

        res = b.open_seats(url, 2)
        print(res.status, "|", res.message, "|", res.seats, "| zaal:", len(res.seat_map))
        assert res.status == "choose" and len(res.seats) == 2 and res.seat_map

        res = b.checkout(res.seats)
        print(res.status, "|", res.message, "| screenshot:", bool(res.screenshot))
        assert res.status == "in_cart", res.status
        assert "customer-details?q=2" in b.page.url, b.page.url   # 2x Volwassene, niet betaald

        # sleutel opvangen. Echte vorm: JWT van moviexchange met rol CXM_JourneyViewer.
        import base64 as b64, json as js
        enc = lambda d: b64.urlsafe_b64encode(js.dumps(d).encode()).decode().rstrip("=")
        jwt = (enc({"alg": "RS256"}) + "." +
               enc({"iss": "https://auth.moviexchange.com/", "role": "CXM_JourneyViewer", "exp": 9999999999})
               + ".handtekeningxyz123")
        b.context.route(re.compile(r"https://digital-api\.kinepolis\.be/.*"),
                        lambda r: r.fulfill(status=200, content_type="application/json", body="[]",
                                            headers={"access-control-allow-origin": "*",
                                                     "access-control-allow-headers": "*"}))

        # a) filmlijst doet niets; pas de filmpagina gebruikt de sleutel (in een header)
        b.context.route("**/films", lambda r: r.fulfill(status=200, content_type="text/html",
            body='<a href="/films/Shaun-Het-Schaap/HO00013277">Shaun</a>'))
        b.context.route("**/films/Shaun-Het-Schaap/HO00013277", lambda r: r.fulfill(
            status=200, content_type="text/html", body="<script>fetch('https://digital-api.kinepolis.be/"
            f"ocapi/v1/x',{{headers:{{Authorization:'Bearer {jwt}'}}}})</script>"))
        tok = b.api_token(["https://web.kinepolis.be/films"], wait_s=4)
        assert tok == jwt, tok
        print("sleutel via filmpagina (header): ok")

        # b) sleutel staat alleen in sessionStorage
        b.context.route("**/films/Shaun-Het-Schaap/HO00013277", lambda r: r.fulfill(
            status=200, content_type="text/html",
            body=f"<script>sessionStorage.setItem('auth', JSON.stringify({{token:'{jwt}'}}))</script>"))
        tok = b.api_token(["https://web.kinepolis.be/films"], wait_s=4)
        assert tok == jwt, tok
        print("sleutel via opslag: ok")

        # c) geen sleutel te vinden -> None, en geen eindeloos doorklikken
        b.context.route("**/films/Shaun-Het-Schaap/HO00013277", lambda r: r.fulfill(
            status=200, content_type="text/html", body='<a href="/films/Andere/HO00000001">x</a>'))
        t0 = __import__("time").time()
        assert b.api_token(["https://web.kinepolis.be/films"], wait_s=2) is None
        print(f"geen sleutel: None na {__import__('time').time() - t0:.0f} s")

        # d) lange filmlijst die per 10 bijlaadt bij scrollen en oude kaarten weghaalt;
        #    Dune als 56ste, met de echte linkvorm van www.kinepolis.be
        cards = [f'["/nl/films/film-{i}/HO{i:08d}/", "Film {i}"]' for i in range(60)]
        cards[55] = '["/nl/films/dune-part-three/HO00013472/", "Dune Part Three"]'
        lazy = ("<html><body style='margin:0'><div id=g></div><script>"
                f"const all=[{','.join(cards)}];let n=0;"
                "function more(){if(n>=all.length)return;const g=document.getElementById('g');"
                "for(const [h,t] of all.slice(n,n+10)){const a=document.createElement('a');a.href=h;"
                "a.textContent=t;a.style='display:block;height:300px';g.appendChild(a)}n+=10;"
                "while(g.children.length>20)g.removeChild(g.firstChild);}"   # virtualisatie
                "more();more();addEventListener('scroll',()=>{if(innerHeight+scrollY>"
                "document.body.scrollHeight-400)more()});</script></body></html>")
        b.context.route("https://www.kinepolis.be/nl/films/", lambda r: r.fulfill(
            status=200, content_type="text/html; charset=utf-8", body=lazy))
        found = b.catalog(["https://www.kinepolis.be/nl/films/"])
        import core
        hits = core.match_films(found, "dune part 3")
        print(f"catalogus: {len(found)} films, 'dune part 3' ->", [(h["id"], h["title"]) for h in hits])
        assert len(found) == 60 and hits and hits[0]["id"] == "HO00013472", (len(found), hits)

        res = b.open_seats(url, 40)
        print(res.status, "|", res.message)
        assert res.status == "no_seats"
        b.close()
    print("OK")
