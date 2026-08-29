# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""Draw the English social card, og-en.png.

The headline is drawn into the picture, so the Russian card cannot stand in
for the English pages: it would show a Russian sentence in every Discord and
Twitter preview. This is not a retouch of that file either -- the headline
crosses the orbits there, and painting over the words would take the orbits
with them. The card is drawn again out of the same pieces the site is drawn
from: the tokens of site.css, the planetary system of the hero (the radii of
the SVG in index.html, scaled, with the star past the edge as it is on the
Russian card), the site's own typefaces. Only the wordmark is lifted from
og.png, where it stands on a flat field and crops without a seam.

The lines are placed by the ink they leave, not by the box the font reports:
the two cards sit side by side in a preview, and a headline eight pixels lower
than its Russian twin is the kind of thing only a measurement catches.

Everything this card says in English is in `CARD`; a third language is another
entry there, not another copy of the file.

Run it when the headline changes, and not otherwise -- the card is committed,
and the service only serves it:

    pip install pillow fonttools brotli
    python landing/og_en.py

Pillow and fontTools are build-time tools here, like `pyftsubset` for the
fonts, and deliberately not in `requirements.txt` -- that is what the
container installs.
"""

import math
import pathlib
import random

from fontTools.ttLib import TTFont
from PIL import Image, ImageDraw, ImageFont

LANDING = pathlib.Path(__file__).parent
#: The site ships woff2; FreeType reads neither woff nor woff2, so the faces
#: are unpacked here on the way. Scratch, not something to keep.
TTF = LANDING / ".og-fonts"

W, H = 1200, 630

# site.css tokens, verbatim.
BG = (7, 11, 20)  # --bg-950
INK = (237, 241, 248)  # --ink
SKY = (158, 219, 255)  # --sky
MUTED = (135, 148, 174)  # --muted
TERRA = (127, 184, 232)
AURORA = (214, 228, 245)
PYRO = (240, 137, 90)
AQUA = (91, 200, 165)

#: Where the Russian card's ink sits, measured off og.png: the top of each
#: line and the left margin they share. Every language goes in the same places.
LEFT = 48
TOPS = {"one": 188, "accent": 305, "three": 436, "kicker": 563}

#: The one language-shaped thing in this file: what the card says, and where
#: it is written. The three lines are the page's own `h1`, split as the page
#: splits it, with the middle one carrying the accent.
CARD = {
    "file": "og-en.png",
    "one": "Not a game. An",
    "accent": "immersive",
    "three": "universe",
    "kicker": "MMO SANDBOX WITH ENDLESS PROGRESSION",
}


def ttf(name: str) -> pathlib.Path:
    """A woff2 from the site, unpacked so FreeType can read it."""
    TTF.mkdir(exist_ok=True)
    out = TTF / (name.removesuffix(".woff2") + ".ttf")
    if not out.exists():
        font = TTFont(LANDING / "fonts" / name)
        font.flavor = None
        font.save(out)
    return out


def onest(size: int, weight: int) -> ImageFont.FreeTypeFont:
    font = ImageFont.truetype(ttf("onest.woff2"), size)
    font.set_variation_by_axes([weight])
    return font


def stars(draw: ImageDraw.ImageDraw) -> None:
    """A sparse, uneven field -- and nothing at all behind the words."""
    random.seed(20260830)
    for _ in range(380):
        x, y = random.uniform(0, W), random.uniform(0, H)
        if x < 800 and 150 < y < 600:
            continue  # the words live here
        shine = random.random()
        r = 1.5 if shine > 0.95 else 1.0
        tone = int(70 + 120 * shine**2)
        draw.ellipse((x - r, y - r, x + r, y + r), fill=(tone, tone + 5, tone + 12))


#: The hero's system, as index.html lays it out: the star at the middle with
#: orbits at r = 60/136/172/220 and dotted belts between them. Here the star
#: sits past the right edge, as it does on the Russian card, so the orbits
#: only sweep in and leave the words alone.
CX, CY, K = 1295, 300, 2.55


def system(draw: ImageDraw.ImageDraw) -> None:
    def ring(radius: float) -> None:
        r = radius * K
        draw.ellipse((CX - r, CY - r, CX + r, CY + r), outline=(28, 38, 56), width=1)

    def belt(radius: float, count: int) -> None:
        r = radius * K
        for i in range(count):
            a = 2 * math.pi * i / count
            x, y = CX + r * math.cos(a), CY + r * math.sin(a)
            draw.ellipse((x - 1, y - 1, x + 1, y + 1), fill=(42, 49, 60))

    def planet(orbit: float, degrees: float, r: int, colour, filled: bool = True) -> None:
        """A world on its orbit, ringed the way the hero rings them."""
        a = math.radians(degrees)
        x, y = CX + orbit * K * math.cos(a), CY + orbit * K * math.sin(a)
        halo = r + 11
        draw.ellipse((x - halo, y - halo, x + halo, y + halo), fill=BG)
        draw.ellipse((x - halo, y - halo, x + halo, y + halo), outline=colour, width=2)
        if filled:
            draw.ellipse((x - r, y - r, x + r, y + r), fill=colour)
        else:
            draw.ellipse((x - r, y - r, x + r, y + r), outline=colour, width=2)

    for radius in (60, 136, 172, 220):
        ring(radius)
    for radius, count in ((90, 150), (99, 165), (108, 175), (196, 300), (202, 310)):
        belt(radius, count)
    planet(172, 200, 10, AQUA, filled=False)
    planet(60, 212, 11, PYRO)
    planet(136, 155, 15, TERRA)
    planet(220, 150, 11, AURORA)


def write(
    draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont, top: int, colour
) -> tuple[int, int, int, int]:
    """Put a line so that its ink starts at (LEFT, top), and say where it ended.

    Pillow measures from the font's own box, which starts above the tallest
    letter and to the left of the first stem; drawing at those numbers leaves
    every line sitting a few pixels off from its Russian twin.
    """
    box = draw.textbbox((0, 0), text, font=font)
    draw.text((LEFT - box[0], top - box[1]), text, font=font, fill=colour)
    return draw.textbbox((LEFT - box[0], top - box[1]), text, font=font)


def main() -> None:
    card = Image.new("RGB", (W, H), BG)
    draw = ImageDraw.Draw(card)

    stars(draw)
    system(draw)

    # The wordmark, lifted from the Russian card: it stands on a flat field
    # there, so the crop carries no seam.
    mark = Image.open(LANDING / "og.png").convert("RGB").crop((40, 40, 180, 120))
    card.paste(mark, (40, 40))

    head = onest(94, 700)
    accent = ImageFont.truetype(ttf("literata-italic.woff2"), 104)
    write(draw, CARD["one"], head, TOPS["one"], INK)
    word = write(draw, CARD["accent"], accent, TOPS["accent"], SKY)
    write(draw, CARD["three"], head, TOPS["three"], INK)

    # The site underlines the accent word with a hand-drawn stroke that never
    # quite settles; here the same wobble, as a sine under the word.
    under = word[3] + 8
    for i in range(word[0] - 8, word[2] + 10):
        y = under + 3.0 * math.sin((i - word[0]) / (word[2] - word[0]) * 3.4 * math.pi)
        draw.ellipse((i - 1.8, y - 1.8, i + 1.8, y + 1.8), fill=SKY)

    # The line underneath, in the mono voice the site keeps for labels.
    kicker = ImageFont.truetype(ttf("plexmono.woff2"), 18)
    x = float(LEFT)
    for letter in CARD["kicker"]:
        box = draw.textbbox((0, 0), letter, font=kicker)
        draw.text((x, TOPS["kicker"] - box[1]), letter, font=kicker, fill=MUTED)
        x += draw.textlength(letter, font=kicker) + 2.6  # the site's own tracking

    out = LANDING / CARD["file"]
    card.save(out, optimize=True)
    print(f"wrote {out} ({out.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
