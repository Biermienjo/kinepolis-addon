"""Boekt in een echte (zichtbare) Chromium: stoelen kiezen, tickets, tot het mandje.

Stopt altijd voor klantgegevens/betalen. Lukt een stap niet automatisch (Turnstile-
uitdaging, onbekende knop), dan blijft de pagina open zodat je live verdergaat.

Bekend uit de echte site: stoelenkaart (zie picker.py), knop "Tickets selecteren",
Turnstile op de stoelpagina. NIET gezien: de ticketpagina; die stap zoekt op tekst.
"""
from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass, field

from playwright.sync_api import Error as PwError
from playwright.sync_api import TimeoutError as PwTimeout
from playwright.sync_api import sync_playwright

import picker
from seats import find_best_group

TICKETS_BUTTON = re.compile(r"tickets selecteren", re.I)
CONTINUE_BUTTON = re.compile(r"^\s*(doorgaan|ga verder|verder|volgende|bevestig\w*)\b", re.I)
PAY_WORDS = re.compile(r"betaal|betalen|pay", re.I)
COOKIE_BUTTON = re.compile(r"alles accepteren|accepteer alle|alle cookies accepteren|akkoord", re.I)

# Zoekt bij het tickettype (bv. "Volwassene") de +-knop in hetzelfde blok en klikt n keer.
_ADD_TICKETS_JS = """([label, n]) => {
  const re = new RegExp('^\\\\s*' + label, 'i');
  const plus = /^\\s*\\+\\s*$|verhoog|toevoeg|increase|add|plus/i;
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  let node;
  while ((node = walker.nextNode())) {
    if (!re.test(node.textContent)) continue;
    for (let el = node.parentElement, d = 0; el && d < 6; el = el.parentElement, d++) {
      const btn = [...el.querySelectorAll('button,[role=button]')].find(b =>
        plus.test(b.textContent) || plus.test(b.getAttribute('aria-label') || ''));
      if (btn) { for (let i = 0; i < n; i++) btn.click(); return true; }
    }
  }
  return false;
}"""


# Kleurt stoelen groen voor de screenshot (Kinepolis gebruikt CSS-variabelen per stoel).
_HIGHLIGHT_JS = """([sel, labels, on]) => labels.forEach(l => {
  const el = document.querySelector(`${sel}[aria-label="${l}"]`);
  if (!el) return;
  for (const v of ['--seat-icon-fill-color', '--seat-icon-stroke-color'])
    on ? el.style.setProperty(v, '#3ddc97') : el.style.removeProperty(v);
  el.style.outline = on ? '3px solid #3ddc97' : '';
})"""


# Alle links naar filmpagina's: nieuwe site /films/<naam>/<HO-code>,
# oude site /<taal>/movies/detail/<id>/<HO-code>/<x>/<naam>/.
_CATALOG_JS = """() => [...document.querySelectorAll('a[href*="/movies/detail/"], a[href*="/films/"]')].map(a => ({
  href: a.href,
  text: (a.getAttribute('title') || a.getAttribute('aria-label') || a.innerText || '').trim(),
}))"""
_FILM_RE = re.compile(r"/movies/detail/\d+/(HO\d{8})/[^/]+/([^/?#]+)|/films/([^/?#]+)/(HO\d{8})")


@dataclass
class BookingResult:
    status: str          # choose | in_cart | needs_human | no_seats | taken | error
    message: str
    seats: list[str] = field(default_factory=list)       # aria-labels
    seat_map: list[dict] = field(default_factory=list)   # hele zaal (picker.read_map)
    screenshot: bytes | None = None


class Booker:
    """Eén browser. Alleen aanroepen vanuit één en dezelfde thread (Playwright sync)."""

    def __init__(self, profile_dir: str, headless: bool = False,
                 executable_path: str | None = None):
        self._pw = sync_playwright().start()
        self.context = self._pw.chromium.launch_persistent_context(
            profile_dir, headless=headless, executable_path=executable_path,
            no_viewport=True, locale="nl-BE", timezone_id="Europe/Brussels",
            # venster vult het virtuele scherm (zie run.sh): gsm-formaat, zodat Kinepolis
            # de mobiele site toont en je die in de app comfortabel bedient
            args=["--no-sandbox", "--window-position=0,0",
                  f"--window-size={os.environ.get('BROWSER_WINDOW', '480,1000')}"])
        self.page = self.context.pages[0] if self.context.pages else self.context.new_page()

    def close(self):
        self.context.close()
        self._pw.stop()

    def screenshot(self) -> bytes | None:
        try:
            return self.page.screenshot(full_page=False)
        except PwError:
            return None

    def _done(self, status, message, seats=(), seat_map=()) -> BookingResult:
        return BookingResult(status, message, list(seats), list(seat_map), self.screenshot())

    def _accept_cookies(self):
        self._accept_cookies_on(self.page)

    def open_seats(self, seats_url: str, persons: int, fmt: str = "default",
                   wait_s: int = 60, on_wait=None) -> BookingResult:
        """Opent de zaal; geeft de kaart en de beste stoelen terug (nog niets aangeklikt).

        Bij een drop kan Kinepolis een wachtrij tonen: we wachten tot `wait_s` seconden en
        roepen ondertussen on_wait(screenshot) aan, zodat je live kunt meekijken.
        """
        page = self.page
        page.goto(seats_url, wait_until="domcontentloaded", timeout=90_000)
        self._accept_cookies()
        deadline = time.time() + wait_s
        while True:
            try:
                page.wait_for_selector(picker.SEAT_SELECTOR, timeout=10_000)
                break
            except PwTimeout:
                if time.time() > deadline:
                    return self._done("needs_human", "Stoelenkaart verscheen niet (wachtrij of "
                                                     "controle?). Kijk in de live browser.")
                self._accept_cookies()
                if on_wait:
                    on_wait(self.screenshot())
        seat_map = picker.read_map(page)
        group = find_best_group(picker.to_seats(seat_map), persons, fmt)
        if not group:
            return self._done("no_seats", f"Geen {persons} aaneengesloten gewone stoelen vrij.",
                              seat_map=seat_map)
        return self._done("choose", "Beste stoelen gekozen. Pas aan of zet ze in je mandje.",
                          [s.label for s in group], seat_map)

    def api_token(self, urls: list[str], wait_s: int = 25) -> str | None:
        """Toegangssleutel (Bearer) die de Kinepolis-site zelf gebruikt voor zijn API.

        Opent een Kinepolis-pagina in een apart tabblad en leest de Authorization-header
        van de eerste aanvraag naar digital-api.kinepolis.be.
        """
        page = self.context.new_page()
        seen = []
        page.on("request", lambda r: seen.append(r) if "digital-api.kinepolis.be" in r.url else None)
        try:
            for url in urls:
                try:
                    page.goto(url, wait_until="domcontentloaded", timeout=45_000)
                    self._accept_cookies_on(page)
                    deadline = time.time() + wait_s
                    while time.time() < deadline:
                        for r in list(seen):
                            try:
                                auth = r.all_headers().get("authorization", "")
                            except PwError:
                                continue
                            if auth.lower().startswith("bearer "):
                                return auth[7:].strip()
                        page.wait_for_timeout(500)
                except PwError as e:
                    print(f"[sleutel] {url}: {e}", flush=True)
        finally:
            page.close()
        return None

    def catalog(self, urls: list[str]) -> list[dict]:
        """Films die op deze Kinepolis-pagina's gelinkt staan: [{id, slug, title, url}].

        Gebruikt een apart tabblad, zodat een open stoelkeuze niet verstoord wordt.
        """
        films: dict[str, dict] = {}
        page = self.context.new_page()
        try:
            for url in urls:
                try:
                    page.goto(url, wait_until="domcontentloaded", timeout=45_000)
                    self._accept_cookies_on(page)
                    try:
                        page.wait_for_load_state("networkidle", timeout=10_000)
                    except PwTimeout:
                        pass
                    for _ in range(6):                       # lijsten laden soms pas bij scrollen
                        page.mouse.wheel(0, 3000)
                        page.wait_for_timeout(400)
                    for a in page.evaluate(_CATALOG_JS):
                        m = _FILM_RE.search(a["href"])
                        if not m:
                            continue
                        ho, slug = (m[1], m[2]) if m[1] else (m[4], m[3])
                        title = a["text"].split("\n")[0].strip()[:80]
                        film = films.setdefault(ho, {"id": ho, "slug": slug.lower(), "url": a["href"],
                                                     "title": slug.replace("-", " ").title()})
                        if len(title) > 2 and not title.lower().startswith(("meer", "koop", "tickets")):
                            film["title"] = title
                except PwError as e:
                    print(f"[catalogus] {url}: {e}", flush=True)
        finally:
            page.close()
        return list(films.values())

    def _accept_cookies_on(self, page):
        for frame in page.frames:
            try:
                frame.get_by_role("button", name=COOKIE_BUTTON).first.click(timeout=1500)
                return
            except (PwTimeout, PwError):
                continue

    def seat_image(self, labels: list[str]) -> bytes | None:
        """Screenshot van de zaal met de voorgestelde stoelen groen gekleurd (alleen visueel)."""
        page = self.page
        try:
            page.evaluate(_HIGHLIGHT_JS, [picker.SEAT_SELECTOR, labels, True])
            target = page.locator("svg.v-seat-picker-map").first
            shot = (target.screenshot(timeout=5_000) if target.count()
                    else page.screenshot(full_page=False))
            page.evaluate(_HIGHLIGHT_JS, [picker.SEAT_SELECTOR, labels, False])
            return shot
        except PwError:
            return self.screenshot()

    def still_free(self, labels: list[str]) -> bool:
        # Een verkochte stoel krijgt een ander label ("Niet beschikbare zitplaats ..").
        free = {s["label"]: s["free"] for s in picker.read_map(self.page)}
        return all(free.get(label) for label in labels)

    def checkout(self, labels: list[str], ticket_label: str = "Volwassene") -> BookingResult:
        """Klikt de stoelen aan en gaat door tot na de ticketkeuze. Nooit betalen."""
        page, persons = self.page, len(labels)
        try:
            page.wait_for_selector(picker.SEAT_SELECTOR, timeout=5_000)
        except PwTimeout:
            return self._done("needs_human", "De zaal is niet meer open in de browser.", labels)
        if not self.still_free(labels):
            return self._done("taken", "Iemand was sneller: niet alle stoelen zijn nog vrij.", labels)
        if not picker.click_seats(page, labels):
            return self._done("needs_human", "Stoelen aanklikken lukte niet. "
                                             "Kies ze in de live browser.", labels)

        # Turnstile draait op deze pagina; de knop wordt pas bruikbaar als die klaar is.
        try:
            btn = page.get_by_role("button", name=TICKETS_BUTTON)
            btn.wait_for(state="visible", timeout=15_000)
            page.wait_for_function("b => !b.disabled && b.getAttribute('aria-disabled') !== 'true'",
                                   arg=btn.element_handle(), timeout=90_000)
            btn.click()
            page.wait_for_url(lambda u: "/seats" not in u, timeout=45_000)
        except PwTimeout:
            return self._done("needs_human",
                              "Stoelen gekozen, maar 'Tickets selecteren' ging niet door "
                              "(waarschijnlijk Turnstile). Rond af in de live browser.", labels)

        try:
            page.wait_for_load_state("networkidle", timeout=15_000)
        except PwTimeout:
            pass                                        # tracking houdt het netwerk bezig
        if not page.evaluate(_ADD_TICKETS_JS, [ticket_label, persons]):
            return self._done("needs_human",
                              f"Stoelen staan vast, maar ik vond '{ticket_label}' niet. "
                              "Kies de tickets in de live browser.", labels)

        try:
            cont = page.get_by_role("button", name=CONTINUE_BUTTON).first
            cont.wait_for(state="visible", timeout=10_000)
            if PAY_WORDS.search(cont.inner_text()):
                raise PwTimeout("betaalknop")             # nooit betalen
            url_before = page.url
            cont.click()
            page.wait_for_url(lambda u: u != url_before, timeout=30_000)
        except PwTimeout:
            return self._done("needs_human",
                              f"{persons}x {ticket_label} gekozen, maar doorgaan lukte niet. "
                              "Controleer in de live browser.", labels)

        return self._done("in_cart", "Stoelen en tickets staan in je mandje. "
                                     "Vul je gegevens in en betaal in de live browser.", labels)
