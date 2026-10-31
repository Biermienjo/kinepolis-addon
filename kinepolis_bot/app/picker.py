"""Leest de stoelenkaart uit de Kinepolis-pagina en klikt stoelen aan.

Elke stoel is een <svg class="v-seat-picker-seat v-seat-picker-seat--normal
v-seat-picker-seat--available" role="button" aria-label="Normale zitplaats 0617">
(rij 06, stoel 17, zoals op het scherm). Soorten: normal, wheelchair, companion,
sofa-left, sofa-right. Positie komt uit het scherm zelf, dus spiegeling of
zone-nummering in de API maakt niet uit.
"""
from __future__ import annotations

from seats import Seat, find_best_group

SEAT_SELECTOR = "svg.v-seat-picker-seat"

_EXTRACT_JS = """(sel) => [...document.querySelectorAll(sel)].map(el => {
  const r = el.getBoundingClientRect();
  const label = el.getAttribute('aria-label') || '';
  const m = label.match(/(\\d{2})(\\d{2})\\s*$/);
  const kind = ([...el.classList].find(c => /^v-seat-picker-seat--/.test(c)
      && !/--(available|unavailable|selected)$/.test(c)) || '').replace('v-seat-picker-seat--', '');
  return {
    label,
    code: m ? m[1] + m[2] : null,
    row: m ? +m[1] : null,
    cx: r.x + r.width / 2,
    kind,
    free: el.classList.contains('v-seat-picker-seat--available')
          && el.getAttribute('aria-disabled') !== 'true',
  };
})"""


def read_map(page) -> list[dict]:
    """Hele zaal: per stoel label, code ("0617"), rij, x (in stoelbreedtes), soort, vrij."""
    raw = [s for s in page.evaluate(_EXTRACT_JS, SEAT_SELECTOR) if s["row"] is not None]
    if not raw:
        return []
    by_row: dict[int, list[float]] = {}
    for s in raw:
        by_row.setdefault(s["row"], []).append(s["cx"])
    diffs = [b - a for xs in by_row.values()
             for a, b in zip(sorted(xs), sorted(xs)[1:]) if b - a > 1]
    pitch = min(diffs) if diffs else 1.0     # afstand tussen buurstoelen
    left = min(s["cx"] for s in raw)
    for s in raw:
        s["x"] = round((s.pop("cx") - left) / pitch, 2)
    return raw


def to_seats(seat_map: list[dict]) -> list[Seat]:
    """Alleen gewone stoelen (cosy en rolstoel vallen af) voor de stoelkeuze."""
    return [Seat(row=s["row"] - 1, col=int(s["code"][2:]), free=s["free"], x=s["x"],
                 label=s["label"]) for s in seat_map if s["kind"] == "normal"]


def read_seats(page) -> list[Seat]:
    return to_seats(read_map(page))


def pick_best(page, persons: int, fmt: str = "default") -> list[Seat] | None:
    return find_best_group(read_seats(page), persons, fmt)


def click_seats(page, labels: list[str]) -> bool:
    """Klikt de stoelen (aria-labels) aan en controleert of ze echt geselecteerd zijn."""
    for label in labels:
        page.locator(f'{SEAT_SELECTOR}[aria-label="{label}"]').click()
    return all(
        page.locator(f'{SEAT_SELECTOR}[aria-label="{label}"]').get_attribute("aria-pressed") == "true"
        for label in labels)
