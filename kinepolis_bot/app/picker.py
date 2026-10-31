"""Leest de stoelenkaart uit de Kinepolis-pagina en klikt stoelen aan.

Elke stoel is een <svg class="v-seat-picker-seat v-seat-picker-seat--normal
v-seat-picker-seat--available" role="button" aria-label="Normale zitplaats 0617">
(rij 06, stoel 17, zoals op het scherm). Soorten: normal, wheelchair, companion,
sofa-left, sofa-right. Positie komt uit het scherm zelf, dus spiegeling of
zone-nummering in de API maakt niet uit.
"""
from __future__ import annotations

import re

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


# Stand van stoelen, gezocht op hun code ("0617"): het label kan veranderen na een klik.
_STATE_JS = """([sel, codes]) => codes.map(code => {
  const el = [...document.querySelectorAll(sel)]
    .find(e => (e.getAttribute('aria-label') || '').trim().endsWith(code));
  if (!el) return {code, found: false};
  return {code, found: true, label: el.getAttribute('aria-label') || '',
          pressed: el.getAttribute('aria-pressed'), cls: el.getAttribute('class') || '',
          disabled: el.getAttribute('aria-disabled'),
          html: el.cloneNode(false).outerHTML.slice(0, 500)};
})"""


def seat_code(label: str) -> str:
    m = re.search(r"(\d{4})\s*$", label)
    return m[1] if m else label


def seat_states(page, labels: list[str]) -> list[dict]:
    return page.evaluate(_STATE_JS, [SEAT_SELECTOR, [seat_code(label) for label in labels]])


def is_selected(state: dict, original_label: str) -> bool:
    """Gekozen? aria-pressed, een class met 'selected', of een ander label dan voorheen."""
    if not state.get("found"):
        return False
    if state.get("pressed") == "true" or re.search(r"selected|chosen", state.get("cls", ""), re.I):
        return True
    label = state.get("label", "")
    return label != original_label and not label.startswith("Niet beschikbare")


def is_free(state: dict) -> bool:
    """Nog te krijgen (vrij of al door ons gekozen)?"""
    return (state.get("found") and state.get("disabled") != "true"
            and "--unavailable" not in state.get("cls", "")
            and not state.get("label", "").startswith("Niet beschikbare"))


def click_seats(page, labels: list[str]) -> tuple[bool, list[dict]]:
    """Klikt de stoelen aan (die nog niet gekozen zijn) en kijkt of ze gekozen zijn."""
    before = seat_states(page, labels)
    for label, state in zip(labels, before):
        if not is_selected(state, label):
            page.locator(f'{SEAT_SELECTOR}[aria-label$="{seat_code(label)}"]').first.click(timeout=10_000)
            page.wait_for_timeout(300)
    after = seat_states(page, labels)
    return all(is_selected(s, label) for s, label in zip(after, labels)), after
