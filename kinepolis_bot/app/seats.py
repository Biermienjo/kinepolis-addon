"""Stoelkeuze: kiest de beste N aaneensluitende stoelen uit een zaalplattegrond.

Platte datastructuur, los van de Kinepolis-API:
    Seat(row, col, x, free)
        row  = rijnummer, 0 = voorste rij
        col  = stoelindex in de rij (links -> rechts)
        x    = horizontale positie (zodat gangpaden meetellen); default = col
        free = nog beschikbaar?
Een groep moet in dezelfde rij liggen zonder gat (gangpad of bezette stoel).
"""
from __future__ import annotations

from dataclasses import dataclass
from itertools import groupby

# Zoetpunt: ongeveer 2/3 van de zaaldiepte (THX/SMPTE-advies is 1/2 tot 2/3).
# Bij IMAX met groot scherm schuift het zoetpunt iets naar achter.
SWEET_SPOT = {"default": 0.62, "imax": 0.58, "imax 70mm": 0.55}


@dataclass(frozen=True)
class Seat:
    row: int
    col: int
    free: bool = True
    x: float | None = None
    label: str = ""

    @property
    def pos(self) -> float:
        return self.col if self.x is None else self.x


def _score_seat(seat: Seat, n_rows: int, center_x: float, half_width: float,
                sweet: float) -> float:
    depth = seat.row / max(n_rows - 1, 1)          # 0 = voor, 1 = achter
    depth_pen = abs(depth - sweet) * 100           # 0..~60
    side_pen = abs(seat.pos - center_x) / max(half_width, 1) * 40   # 0..40
    last_row_pen = 8 if seat.row == n_rows - 1 else 0   # achterste rij: muur/geluid
    return 100 - depth_pen - side_pen - last_row_pen


def find_best_group(seats: list[Seat], n: int, fmt: str = "default",
                    max_gap: float = 1.5) -> list[Seat] | None:
    """Beste groep van n aaneensluitende vrije stoelen, of None als er geen is."""
    if n < 1 or not seats:
        return None
    sweet = SWEET_SPOT.get(fmt.lower(), SWEET_SPOT["default"])
    n_rows = max(s.row for s in seats) + 1
    xs = [s.pos for s in seats]
    center_x = (min(xs) + max(xs)) / 2
    half_width = (max(xs) - min(xs)) / 2

    best, best_score = None, float("-inf")
    by_row = sorted(seats, key=lambda s: (s.row, s.pos))
    for _, row_iter in groupby(by_row, key=lambda s: s.row):
        row = list(row_iter)
        for i in range(len(row) - n + 1):
            grp = row[i:i + n]
            if not all(s.free for s in grp):
                continue
            if any(b.pos - a.pos > max_gap for a, b in zip(grp, grp[1:])):
                continue   # gangpad ertussen
            scores = [_score_seat(s, n_rows, center_x, half_width, sweet) for s in grp]
            score = sum(scores) / n
            # Strafpunt voor een enkele lege stoel naast de groep (weeskind-stoel)
            left_free = i > 0 and row[i - 1].free and grp[0].pos - row[i - 1].pos <= max_gap \
                and (i < 2 or not row[i - 2].free)
            right_free = i + n < len(row) and row[i + n].free \
                and row[i + n].pos - grp[-1].pos <= max_gap \
                and (i + n + 1 >= len(row) or not row[i + n + 1].free)
            score -= 6 * (left_free + right_free)
            if score > best_score:
                best, best_score = grp, score
    return best


if __name__ == "__main__":
    # Zelftest: zaal van 14 rijen x 20 stoelen met gangpad na stoel 9, deels bezet.
    import random
    random.seed(1)
    hall = []
    for r in range(14):
        for c in range(20):
            hall.append(Seat(r, c, free=random.random() > 0.45,
                             x=c + (1 if c >= 10 else 0), label=f"R{r + 1}-{c + 1}"))
    for n in (1, 2, 4):
        g = find_best_group(hall, n, "imax 70mm")
        print(n, [s.label for s in g] if g else None)
