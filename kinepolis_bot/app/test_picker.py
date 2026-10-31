"""Test picker.py op een nagemaakte stoelenkaart (zelfde structuur als Kinepolis)."""
import glob
import random

from playwright.sync_api import sync_playwright

import picker

PITCH, ROWS = 50, 14
LABELS = {"normal": "Normale zitplaats", "wheelchair": "Rolstoelzitplaats",
          "companion": "Begeleiderszitplaats", "sofa-left": "Linker zitplaats bank",
          "sofa-right": "Rechter zitplaats bank"}


def build_html(seed: int = 3) -> str:
    """Zaal zoals Kortrijk zaal 6: rij 1 met rolstoelplaatsen, cosy-banken op rij 9-11."""
    rnd = random.Random(seed)
    seats = []
    for r in range(1, ROWS + 1):
        for c in range(1, 19):
            if c in (9, 10):                                          # gangpad
                continue
            kind = "normal"
            if r == 1 and c >= 16:
                kind = "wheelchair" if c < 18 else "companion"
            if 9 <= r <= 11:
                if c % 3 == 0:
                    continue                                          # ruimte tussen banken
                kind = "sofa-left" if c % 3 == 1 else "sofa-right"
            taken = kind == "normal" and rnd.random() < 0.3
            state = "unavailable" if taken else "available"
            label = "Niet beschikbare zitplaats" if taken else LABELS[kind]
            x = (18 - c) * PITCH                                       # gespiegeld, zoals in het echt
            y = (r - 1) * PITCH + (20 if r > 8 else 0)
            seats.append(
                f'<svg class="v-seat-picker-seat v-seat-picker-seat--{kind} '
                f'v-seat-picker-seat--{state} rule-all-areas-{kind}" x="{x}" y="{y}" width="45" '
                f'height="45" role="button" aria-label="{label} {r:02d}{c:02d}" aria-pressed="false" '
                f'aria-disabled="{str(taken).lower()}"><rect width="45" height="45"/></svg>')
    return f"""<html><body><svg viewBox="0 0 1000 800" width="1000" height="800">
    <g transform="translate(20, 30)">{''.join(seats)}</g></svg>
    <script>document.querySelectorAll('svg.v-seat-picker-seat--available').forEach(e =>
      e.addEventListener('click', () => e.setAttribute('aria-pressed',
        e.getAttribute('aria-pressed') === 'true' ? 'false' : 'true')))</script></body></html>"""


if __name__ == "__main__":
    exe = glob.glob("/opt/pw-browsers/chromium-*/chrome-linux64/chrome")[0]
    with sync_playwright() as p:
        b = p.chromium.launch(executable_path=exe, args=["--no-sandbox"])
        page = b.new_page(viewport={"width": 1100, "height": 900})
        page.set_content(build_html())

        full = picker.read_map(page)
        kinds = {k: sum(s["kind"] == k for s in full) for k in LABELS}
        print("zaal:", kinds, "| bezet:", sum(not s["free"] for s in full))
        assert kinds["sofa-left"] and kinds["wheelchair"] == 2 and kinds["companion"] == 1
        seats = picker.read_seats(page)
        assert all(s.label.startswith(("Normale", "Niet beschikbare")) for s in seats)
        assert max(s.x for s in seats) > 15 and min(s.x for s in seats) == 0

        for n in (1, 2, 4):
            g = picker.pick_best(page, n)
            assert g and len(g) == n and all(s.free for s in g)
            assert len({s.row for s in g}) == 1
            labels = [s.label for s in g]
            print(n, [l.split()[-1] for l in labels], "rij", g[0].row + 1)
            assert picker.click_seats(page, labels), "stoelen niet geselecteerd"
            for l in labels:                                    # terugklikken voor de volgende ronde
                page.locator(f'{picker.SEAT_SELECTOR}[aria-label="{l}"]').click()
        b.close()
    print("OK")
