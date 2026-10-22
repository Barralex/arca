"""The masthead: a still night scene. The ark rides a calm sea under a crescent moon, the wind crosses the sky and
the dove flies back with the olive branch, next to the name.

It is drawn on a pixel grid at twice the terminal's vertical resolution (half blocks: a cell holds a top and a
bottom pixel), so the shapes and gradients stay smooth. Nothing moves: the scene is built once per size. Narrow or
short terminals get a single line instead.
"""
from __future__ import annotations

import math
import random

from rich.color import Color
from rich.style import Style
from rich.text import Text
from textual.widget import Widget

Rgb = tuple[int, int, int]

HEIGHT = 12  # cells; 24 pixels
HORIZON = 17  # first sea pixel row
FULL_MIN_WIDTH = 96
FULL_MIN_HEIGHT = 30


def hexrgb(value: str) -> Rgb:
    return int(value[1:3], 16), int(value[3:5], 16), int(value[5:7], 16)


def lerp(a: Rgb, b: Rgb, k: float) -> Rgb:
    k = max(0.0, min(1.0, k))
    return (round(a[0] + (b[0] - a[0]) * k), round(a[1] + (b[1] - a[1]) * k), round(a[2] + (b[2] - a[2]) * k))


SKY_TOP, SKY_LOW = hexrgb("#060a19"), hexrgb("#1f2b55")
SEA_TOP, SEA_LOW = hexrgb("#14305a"), hexrgb("#07152b")
SWELL, CREST, FOAM = hexrgb("#24508a"), hexrgb("#3f7cb8"), hexrgb("#a9d4f0")
MOON, EARTHSHINE, HALO, GLINT = hexrgb("#f4e7b0"), hexrgb("#1c2550"), hexrgb("#3a4f8f"), hexrgb("#d9c98a")
WIND = hexrgb("#3a4a80")
STARS = (hexrgb("#ffffff"), hexrgb("#b9c8ec"), hexrgb("#6b7fae"))
NAME_TOP, NAME_LOW, NAME_SHADOW = hexrgb("#e3fbff"), hexrgb("#3fb3d6"), hexrgb("#08112a")

# 5x7 letters, drawn at 2 cells wide and 2 pixels tall per dot.
FONT = {
    "A": [".###.", "#...#", "#...#", "#####", "#...#", "#...#", "#...#"],
    "R": ["####.", "#...#", "#...#", "####.", "#.#..", "#..#.", "#...#"],
    "C": [".####", "#....", "#....", "#....", "#....", "#....", ".####"],
}
WORD = "ARCA"
LETTER_W, LETTER_GAP = 10, 2

ARK_W = 30
ARK_COLORS = {
    "r": hexrgb("#7a3326"),  # roof, shaded side
    "R": hexrgb("#b4553d"),  # roof, moonlit side
    "e": hexrgb("#5a241b"),  # roof edge
    "w": hexrgb("#c79f63"),  # cabin wall
    "d": hexrgb("#9c773f"),  # cabin wall in shadow
    "y": hexrgb("#ffd97a"),  # lit window
    "o": hexrgb("#e09a3e"),  # window glow edge
    "H": hexrgb("#8a5634"),  # hull, light plank
    "h": hexrgb("#6a3f25"),  # hull
    "k": hexrgb("#432616"),  # hull seam
}

DOVE = [
    "     ss  ",
    "    sww  ",
    " bwwwwwws",
    "gl wwwss ",
    " gl   w  ",
]
DOVE_COLORS = {"w": hexrgb("#f6f7fb"), "s": hexrgb("#b9c3d6"), "b": hexrgb("#e3a24f"), "g": hexrgb("#5f8f3a"),
               "l": hexrgb("#9ccc5a")}


def ark_sprite() -> list[str]:
    """The ark, 30 x 14 pixels: a gabled cabin with lit windows on a hull with raised bow and stern."""
    rows = [[" "] * ARK_W for _ in range(14)]
    centre = ARK_W // 2
    for i in range(5):  # roof
        half = 1 + i * 2
        for x in range(centre - half, centre + half + 1):
            rows[i][x] = "r" if x < centre else "R"
        rows[i][centre - half] = rows[i][centre + half] = "e"
    for y in range(5, 10):  # cabin
        for x in range(9, 22):
            rows[y][x] = "d" if x >= 19 else "w"
    for y in (6, 7):  # three lit windows
        for x0 in (10, 14, 18):
            rows[y][x0] = "o"
            rows[y][x0 + 1] = "y"
    for y, (x0, x1) in zip(range(10, 14), ((0, 29), (1, 28), (2, 27), (4, 25)), strict=True):  # hull
        for x in range(x0, x1 + 1):
            rows[y][x] = "H" if y == 10 else "h"
    for x in range(3, 27, 6):
        rows[11][x] = "k"
    rows[13] = [c if c == " " else "k" for c in rows[13]]
    for y in (8, 9):  # bow and stern posts
        rows[y][0] = rows[y][ARK_W - 1] = "H"
    rows[9][1] = rows[9][ARK_W - 2] = "H"
    return ["".join(row) for row in rows]


class Scene:
    """A width x 24 pixel canvas, painted back to front."""

    def __init__(self, width: int, seed: int = 11):
        self.w = width
        self.h = HEIGHT * 2
        self.rand = random.Random(seed)
        self.px: list[list[Rgb]] = [[(0, 0, 0)] * width for _ in range(self.h)]

    def put(self, x: int, y: int, colour: Rgb) -> None:
        if 0 <= x < self.w and 0 <= y < self.h:
            self.px[y][x] = colour

    def paint(self) -> Scene:
        ark_x = 4
        name_x = ark_x + ARK_W + 6
        name_end = name_x + len(WORD) * LETTER_W + (len(WORD) - 1) * LETTER_GAP
        # Wide: the moon sits well clear of the name with the dove between them. Narrow: it moves closer to the
        # edge and the dove stays home, rather than the moon sliding off the canvas.
        moon_x = self.w - 14 if self.w - 14 >= name_end + 16 else self.w - 8
        self._sky()
        self._stars(avoid=(name_x - 2, name_end + 2))
        self._wind(avoid=(name_x - 2, name_end + 2))
        self._moon(moon_x, 6)
        self._sea(moon_x)
        self._ark(ark_x, HORIZON + 1 - 13)
        self._name(name_x, 2)
        if moon_x - name_end > 18:
            self._sprite(DOVE, DOVE_COLORS, name_end + (moon_x - name_end) // 2 - 6, 3)
        self._crests(ark_x)
        return self

    # --- layers -----------------------------------------------------------------------------------------------------

    def _sky(self) -> None:
        for y in range(HORIZON):
            colour = lerp(SKY_TOP, SKY_LOW, (y / (HORIZON - 1)) ** 1.6)
            self.px[y] = [colour] * self.w

    def _stars(self, avoid: tuple[int, int]) -> None:
        for _ in range(self.w // 4):
            x, y = self.rand.randrange(self.w), self.rand.randrange(HORIZON - 3)
            if avoid[0] <= x <= avoid[1] and 1 <= y <= 17:
                continue
            self.put(x, y, self.rand.choices(STARS, weights=(1, 3, 6))[0])

    def _wind(self, avoid: tuple[int, int]) -> None:
        # Thin gusts that fade in and out and curl up at the end; they pass behind the name, never across it.
        for y, start, length in ((5, 0.03, 26), (12, 0.62, 24), (3, 0.80, 16), (15, 0.40, 14)):
            x0 = int(self.w * start)
            for i in range(length):
                x = x0 + i
                if avoid[0] <= x <= avoid[1] or (i * 7 + y) % 13 == 0:
                    continue
                fade = math.sin(math.pi * i / length)
                self.put(x, y, lerp(self.px[y][x] if 0 <= x < self.w else WIND, WIND, 0.85 * fade))
            end = x0 + length
            if not avoid[0] <= end <= avoid[1]:
                self.put(end, y - 1, lerp(SKY_LOW, WIND, 0.6))

    def _moon(self, cx: int, cy: int) -> None:
        r = 3.6
        for y in range(max(cy - 8, 0), min(cy + 9, HORIZON)):
            for x in range(cx - 9, cx + 10):
                if not 0 <= x < self.w:
                    continue
                d = math.hypot(x - cx, y - cy)
                if d <= r:
                    lit = math.hypot(x - cx - 1.8, y - cy + 1.0) > r - 0.4
                    self.put(x, y, MOON if lit else EARTHSHINE)
                elif d <= r + 5:  # a soft glow, brightest at the rim, fading into the sky
                    self.put(x, y, lerp(self.px[y][x], HALO, 0.55 * (1 - (d - r) / 5) ** 1.5))

    def _sea(self, moon_x: int) -> None:
        for y in range(HORIZON, self.h):
            depth = (y - HORIZON) / (self.h - HORIZON - 1)
            base = lerp(SEA_TOP, SEA_LOW, depth)
            for x in range(self.w):
                wave = math.sin(x * 0.29 + y * 1.9) + 0.7 * math.sin(x * 0.11 - y * 0.8)
                colour = base
                if wave > 1.25:
                    colour = lerp(base, SWELL, 0.9 - depth * 0.4)
                if wave > 1.55:
                    colour = CREST
                # The moon's road on the water: short horizontal ripples, longer and fainter with depth.
                spread = 1 + int(depth * 5)
                if y % 2 == 0 and abs(x - moon_x) <= spread and (x * 3 + y * 5) % 7:
                    colour = lerp(colour, GLINT, 0.85 - depth * 0.35)
                self.px[y][x] = colour
        for x in range(self.w):  # a lighter line where sea meets sky
            self.px[HORIZON][x] = lerp(self.px[HORIZON][x], SWELL, 0.5)

    def _ark(self, x0: int, y0: int) -> None:
        self._sprite(ark_sprite(), ARK_COLORS, x0, y0)
        for y in range(y0 + 5, y0 + 10):  # warm light spilling from the windows onto the hull top
            for x in range(x0 + 9, x0 + 22):
                if self.px[y][x] == ARK_COLORS["y"]:
                    self.put(x, y0 + 10, lerp(ARK_COLORS["H"], ARK_COLORS["y"], 0.25))

    def _crests(self, ark_x: int) -> None:
        # Foam where the hull meets the water, drawn last so the ark sits in the sea, not on it.
        y = HORIZON + 1
        for x in range(ark_x - 3, ark_x + ARK_W + 3):
            if (x + 1) % 3:
                self.put(x, y, FOAM if (x % 5) else CREST)

    def _name(self, x0: int, y0: int) -> None:
        dots: list[tuple[int, int]] = []
        for i, letter in enumerate(WORD):
            lx = x0 + i * (LETTER_W + LETTER_GAP)
            for fy, line in enumerate(FONT[letter]):
                for fx, dot in enumerate(line):
                    if dot == "#":
                        for dx in range(2):
                            for dy in range(2):
                                dots.append((lx + fx * 2 + dx, y0 + fy * 2 + dy))
        for x, y in dots:  # a drop shadow one pixel down and right
            self.put(x + 1, y + 1, NAME_SHADOW)
        span = 14
        for x, y in dots:
            k = (y - y0) / (span - 1)
            colour = lerp(NAME_TOP, NAME_LOW, k)
            if (y - y0) % 2 == 0 and (x + y) % 2 == 0:
                colour = lerp(colour, NAME_TOP, 0.25)  # a faint scanline texture, like a printed sign
            self.put(x, y, colour)

    def _sprite(self, rows: list[str], colours: dict[str, Rgb], x0: int, y0: int) -> None:
        for y, line in enumerate(rows):
            for x, key in enumerate(line):
                if key in colours:
                    self.put(x0 + x, y0 + y, colours[key])

    # --- output -----------------------------------------------------------------------------------------------------

    def to_text(self) -> Text:
        text = Text(no_wrap=True, overflow="crop")
        styles: dict[tuple[Rgb, Rgb], Style] = {}
        for row in range(HEIGHT):
            top, bottom = self.px[row * 2], self.px[row * 2 + 1]
            for x in range(self.w):
                pair = (top[x], bottom[x])
                style = styles.get(pair)
                if style is None:
                    style = styles[pair] = Style(color=Color.from_rgb(*pair[0]), bgcolor=Color.from_rgb(*pair[1]))
                text.append("▀", style)
            if row < HEIGHT - 1:
                text.append("\n")
        return text


class Masthead(Widget):
    ALLOW_SELECT = False
    DEFAULT_CSS = "Masthead { height: auto; }"

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._cache: tuple[int, bool, Text] | None = None

    def full(self) -> bool:
        return self.size.width >= FULL_MIN_WIDTH and self.app.size.height >= FULL_MIN_HEIGHT

    def get_content_height(self, container, viewport, width: int) -> int:
        return HEIGHT if self.full() else 1

    def on_resize(self) -> None:
        self.refresh(layout=True)

    def render(self) -> Text:
        width, full = self.size.width, self.full()
        if self._cache is None or self._cache[:2] != (width, full):
            text = Scene(width).paint().to_text() if full else Text("▲ ARCA", style="bold #4fd1e8")
            self._cache = (width, full, text)
        return self._cache[2]
