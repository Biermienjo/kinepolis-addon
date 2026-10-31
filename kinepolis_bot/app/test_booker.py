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
  const sel = [...document.querySelectorAll('[aria-pressed=true]')].length;
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

        res = b.open_seats(url, 40)
        print(res.status, "|", res.message)
        assert res.status == "no_seats"
        b.close()
    print("OK")
