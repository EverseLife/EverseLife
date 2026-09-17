# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The landing's pictures of the planets: the game's own ground, baked flat (D-347).

The login screen draws the planet the map draws (`map/Planet`): the field of
the vault on the GPU, coloured by biome, lit by the relief. The landing has
none of that -- it is four hand-written pages with no build step, it must not
call the game (`landing/README.md`), and the whole machinery is thirteen
thousand lines of the client. So the ground is baked here instead: one
equirectangular picture a planet, the same field, the same palette, the same
rules, and `landing/space.js` wraps it round a sphere and lights it.

What stays live in the shader is what a picture cannot hold: where the sun
stands, the night's tint, the clouds, the rim of the air. What is baked is
what a picture can: the land, the water, the rivers, the relief, and the snow
and ice of the **mean** season -- the swing the client adds by latitude
(D-334) is nought halfway through the year, and that is the one moment a
still picture can honestly claim.

Run from the repository root, with the vault's build beside it:

    backend/.venv/Scripts/python.exe tools/planet_pictures.py

It reads the field out of the vault's build (`EVERSELIFE_VAULT_BUILD`, else
`../everselife-vault/build`) and the colours out of the client's stylesheets
(`frontend/src/theme.css`, `frontend/src/map.css`) -- so a biome recoloured
in the game recolours the landing on the next bake, and there is no second
table to keep in step. The pictures go to `landing/planets/` and are
committed: the landing's image is built from that folder alone.

Rebake when the field changes (a new `build/field/*.npz`), when the ground's
colours do, or when this file does. Nothing downstream would notice if you
did not: the page goes on serving last month's world, with every test green
and only a reader to see it. So the bake writes down the digest of everything
it read (`sources.json`), and `--check` compares those -- a handful of hashes,
no drawing, so it can hang off the commit hook, which is where it does hang.
Not the pictures' own bytes: two machines with different zlib encode the same
picture differently, and a check that cries wolf gets turned off. The hook
speaks and does not stop: what makes the pictures stale is a rebuild of the
vault's field, and whoever commits next is almost never the one who rebuilt
it. This exits 1 all the same -- it is the honest answer to the question, and
a hand asking it wants the answer.

Neither mode runs in CI: the bake needs numpy and the backend, and both need
the vault's build, which the landing's job has no business holding. What CI
holds is the other end of the link -- that every planet the shader names is a
file the landing serves, and that each is a PNG of the size the shader wraps
(`landing/test_planets.py`).
"""

from __future__ import annotations

import argparse
import functools
import hashlib
import json
import os
import re
import struct
import sys
import zlib
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from src import field as fields  # noqa: E402
from src.constants import bootstrap  # noqa: E402
from src.constants import registry as R  # noqa: E402
from src.engine import biome as biomes  # noqa: E402
from src.models.world import Planet  # noqa: E402

#: How wide the picture is; twice as wide as tall, as an equirectangular
#: picture of a whole sphere is. Terra is 780 300 cells of fifty metres
#: (D-328) -- a lattice of about 1250 by 625, and the smaller planets fewer --
#: while the globe on the landing is a third of a screen, where half the
#: sphere spans some four hundred pixels. A thousand across is more than the
#: card can show and less than the field has to give: finer would be bytes
#: nobody sees. A power of two, and checked as one: the shader wraps the
#: picture east to west (`REPEAT`), which WebGL 1 allows on a power of two
#: alone -- anything else is a black ball and a warning, with nothing wrong
#: at the baking end to see.
WIDTH = 1024
#: The picture is drawn at this many samples a texel across and averaged
#: down: the shader that draws the real thing reads four taps a pixel for
#: exactly this reason (`shade.AA_PX`) -- a coast picked once a texel is a
#: staircase of cells, and a river narrower than a texel is a row of dashes.
SUPER = 3

#: The light the relief is drawn by: north-west, forty-five degrees up, the
#: convention of every topographic map -- the client's shader
#: (`map/shade.ts`) and the vault's debug render both say the same thing, and
#: the picture has to agree with them. The sun of the moment is the shader's
#: business on the landing too: it moves, and a picture cannot.
SUN_AZIMUTH_DEG = 315.0
SUN_ALTITUDE_DEG = 45.0
#: The slope is drawn steeper than it is (`shade.EXAGGERATION`): on a ball of
#: twelve kilometres a rise of four hundred metres is nothing to the eye.
EXAGGERATION = 2.0
#: What the ground keeps in the full shadow of its own slope, and how far the
#: lie of the land -- a whole valley or a whole crest -- moves the tone; both
#: the shader's own (`shade.AMBIENT`, `RELIEF_DEPTH`, `RELIEF_M`).
AMBIENT = 0.5
RELIEF_DEPTH = 0.12
RELIEF_M = 80.0
#: How far off the point the mean of the ground about it is taken, texels:
#: the shader reads it three mip levels coarser (`shade.RELIEF_LEVELS`), which
#: is eight texels of its own picture.
RELIEF_TEXELS = 8
#: The water's edge between the cells, and the ramp a river narrower than a
#: texel is drawn by -- the shader's (`shade.BANK_SHARE`, `RIVER_FAINT`,
#: `RIVER_FULL`).
#:
#: The half is the third copy of one number: the vault writes the ribbon so
#: that it falls to it at the bank (`pipeline.ribbon`, held by
#: `test_the_river_ribbon_is_half_gone_at_its_bank`), the client cuts the
#: water at it (`shade.BANK_SHARE`, held by its twin). This copy has no test
#: of its own -- it draws a page, not a world -- so it is named here instead,
#: to be found by grep the day the knife moves.
BANK_SHARE = 0.5
RIVER_FAINT = 0.12
RIVER_FULL = 0.5
#: What share of the deep tone the water standing on land takes
#: (`shade.FAR_WATER_DEEP`). A whole planet in a card is the farthest frame
#: there is, and there a river reads by being darker than the ground: drawn
#: in the lake's own light tone every river was a bright thread.
FAR_WATER_DEEP = 0.4
#: The tone of snow and of frozen water (`map/season.ts`).
SNOW_TONE = (0.92, 0.94, 0.97)
ICE_TONE = (0.78, 0.86, 0.94)
#: How much of the high ground's grey the summits take, and from what share
#: of the rise (`fragment.ts`: the mountain level of the passport).
HIGH_MIX = 0.6

#: The biome a sea cell takes when the height read between the cells has come
#: up over nought -- the shore's last strip (`fragment.ts`, `u_shore`).
SHORE_BIOME = "coast"
#: The raster's word for a cell with no biome: water.
NO_BIOME = 255

#: Where the colours are declared. The biomes and the base scales are in the
#: client's theme, the water's tones in the map's stylesheet; both are read,
#: never copied.
THEME_CSS = ROOT / "frontend" / "src" / "theme.css"
MAP_CSS = ROOT / "frontend" / "src" / "map.css"
#: Where the pictures go. Inside the landing, because the landing's image is
#: built from that folder and must carry them.
OUT_DIR = ROOT / "landing" / "planets"
#: And the digests of everything they were made from, beside them.
SOURCES = "sources.json"
#: What the pictures depend on outside the vault's field: the colours, the
#: client's picture constants this file copies, and this file itself. A change
#: to any of them leaves the pictures showing a world that is no longer the
#: game's -- with nothing falling over to say so, which is the whole reason
#: the digests are written down.
WATCHED = (
    "frontend/src/theme.css",
    "frontend/src/map.css",
    "frontend/src/panels/map/shade.ts",
    "frontend/src/panels/map/season.ts",
    "tools/planet_pictures.py",
)


# ── The colours, read off the client's stylesheets ──────────────────────────


def _hex(colour: str) -> tuple[float, float, float]:
    """`#rrggbb` as three numbers nought to one."""
    text = colour.strip().lstrip("#")
    return tuple(int(text[i : i + 2], 16) / 255.0 for i in (0, 2, 4))  # type: ignore[return-value]


def _declared(css: str, block: str) -> dict[str, str]:
    """The custom properties of one selector's block, by name.

    The stylesheets declare each planet's scale in its own block
    (`:root[data-planet="pyroxis"]`), so the block is what is asked for. A
    property declared twice in it -- as `:root, :root[data-planet="terra"]`
    declares nothing twice -- keeps its last value, the cascade's rule.
    """
    at = css.find(block)
    if at < 0:
        raise SystemExit(f"{block}: no such block in the stylesheet")
    body = css[css.index("{", at) + 1 : css.index("}", at)]
    return {name: value.strip() for name, value in re.findall(r"(--[\w-]+)\s*:\s*([^;]+);", body)}


def _dark(value: str) -> str:
    """The dark member of a `light-dark()` pair, or the value itself.

    The landing has one ground and it is dark; the client picks between the
    two at runtime by `color-scheme`.
    """
    pair = re.fullmatch(r"light-dark\(\s*([^,]+),\s*([^)]+)\)", value.strip())
    return pair.group(2).strip() if pair else value.strip()


def _mix(
    expression: str, known: dict[str, tuple[float, float, float]]
) -> tuple[float, float, float]:
    """`color-mix(in srgb, var(--a) N%, var(--b))` worked out.

    The one form the map's tones are written in. In sRGB a mix is the plain
    weighted mean of the two, which is what CSS does and what the shader gets
    handed when it reads the tone off a probe element.
    """
    got = re.fullmatch(
        r"color-mix\(\s*in srgb,\s*var\((--[\w-]+)\)\s*([\d.]+)%\s*,"
        r"\s*(var\((--[\w-]+)\)|#[0-9a-fA-F]{6})\s*\)",
        expression.strip(),
    )
    if not got:
        raise SystemExit(f"unexpected colour expression: {expression}")
    first = known[got.group(1)]
    share = float(got.group(2)) / 100.0
    second = known[got.group(4)] if got.group(4) else _hex(got.group(3))
    return tuple(first[i] * share + second[i] * (1.0 - share) for i in range(3))  # type: ignore[return-value]


class Palette:
    """Every colour one planet's picture is drawn with.

    Each planet is drawn in its own skin: the sea of Pyroxis is mixed into
    that planet's own base scale, not Terra's, because on the landing the
    four stand side by side and a shared base would make four tints of one
    planet out of them. The client does the same thing when it is stood on
    one -- `data-planet` swaps the whole scale.
    """

    def __init__(self, planet: str) -> None:
        theme = THEME_CSS.read_text(encoding="utf-8")
        skin = (
            ':root, :root[data-planet="terra"]'
            if planet == "terra"
            else f':root[data-planet="{planet}"]'
        )
        scale = _declared(theme, skin)
        tints = _declared(theme, ":root {\n  --d-planet-terra")
        known = {name: _hex(value) for name, value in scale.items() if value.startswith("#")}
        known |= {
            f"--base-{name[4:]}": rgb for name, rgb in known.items() if name.startswith("--d-")
        }
        known["--pc"] = _hex(tints[f"--d-planet-{planet}"])
        self.tint = known["--pc"]
        self.biomes = {
            name[len("--biome-") :]: _hex(_dark(value))
            for name, value in _declared(theme, ":root {\n  --biome-tundra").items()
            if name.startswith("--biome-")
        }
        tones = _declared(MAP_CSS.read_text(encoding="utf-8"), ".ground-probe")
        self.sea_deep = _mix(tones["--gl-sea-deep"], known)
        self.lake = _mix(tones["--gl-lake"], known)
        self.lava_deep = _mix(tones["--gl-lava-deep"], known)
        self.lava_lake = _mix(tones["--gl-lava-lake"], known)
        self.high = _mix(tones["--gl-high"], known)

    def water(self, lava: bool) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
        """The surface tone and the deep one, for water or for lava.

        Lava brightens with its depth where water darkens: the light comes
        out of it rather than stopping in it (`map.css`).
        """
        return (self.lava_lake, self.lava_deep) if lava else (self.lake, self.sea_deep)


# ── The picture ─────────────────────────────────────────────────────────────


def _grid(width: int, height: int) -> tuple[np.ndarray, np.ndarray]:
    """The middle of every texel of an equirectangular picture, degrees.

    North up and the meridian in the middle, which is how the shader unwraps
    the sphere it draws (`space.js`).
    """
    lon = -180.0 + (np.arange(width) + 0.5) * (360.0 / width)
    lat = 90.0 - (np.arange(height) + 0.5) * (180.0 / height)
    return np.broadcast_arrays(lat[:, None], lon[None, :])


def _blur(values: np.ndarray, texels: int) -> np.ndarray:
    """The mean of the ground about each texel, over a square that many wide.

    Wrapped east to west and held at the poles: a picture of a sphere has no
    left edge, and a valley on the meridian must not read as a wall. A box
    over a running sum, so the width costs nothing.
    """

    def along(block: np.ndarray, axis: int, wrap: bool) -> np.ndarray:
        count = block.shape[axis]
        pad = [(0, 0), (0, 0)]
        pad[axis] = (texels, texels)
        wide = np.pad(block, pad, mode="wrap" if wrap else "edge")
        sums = np.cumsum(wide, axis=axis)
        #: The window stands **on** the texel, not behind it: taken from the
        #: running sum without this offset the box lies half a width to one
        #: side, and the lie of the land -- which is the point minus its
        #: surroundings -- comes out as a slope where the ground is flat.
        first = texels - texels // 2
        low = np.take(sums, np.arange(first - 1, first - 1 + count), axis)
        high = np.take(sums, np.arange(first + texels - 1, first + texels - 1 + count), axis)
        return (high - low) / texels

    return along(along(values, 1, True), 0, False)


def _hillshade(height_m: np.ndarray, radius_m: float) -> np.ndarray:
    """The light on the slope, nought (facing away) to one (facing the sun).

    The standard hillshade, with the ground's own metres under it: a texel is
    a longer piece of ground at the equator than near the pole, so the
    eastward step is stretched by the cosine of the latitude. Without that the
    slopes near the poles read as cliffs.
    """
    rows, cols = height_m.shape
    lat = np.deg2rad(90.0 - (np.arange(rows) + 0.5) * (180.0 / rows))[:, None]
    east_m = np.maximum(2.0 * np.pi * radius_m * np.cos(lat) / cols, 1e-6)
    north_m = np.pi * radius_m / rows
    #: The meridian wraps, the poles do not: the gradient across the seam is
    #: real ground, the one past the pole is a fold.
    dz_dx = (np.roll(height_m, -1, 1) - np.roll(height_m, 1, 1)) / (2.0 * east_m) * EXAGGERATION
    padded = np.pad(height_m, ((1, 1), (0, 0)), mode="edge")
    dz_dy = (padded[:-2] - padded[2:]) / (2.0 * north_m) * EXAGGERATION
    slope = np.arctan(np.hypot(dz_dx, dz_dy))
    aspect = np.arctan2(dz_dy, -dz_dx)
    zenith = np.deg2rad(90.0 - SUN_ALTITUDE_DEG)
    azimuth = np.deg2rad(90.0 - SUN_AZIMUTH_DEG)
    shade = np.cos(zenith) * np.cos(slope) + np.sin(zenith) * np.sin(slope) * np.cos(
        azimuth - aspect
    )
    return np.clip(shade, 0.0, 1.0)


def _smoothstep(low: float, high: float, value: np.ndarray) -> np.ndarray:
    t = np.clip((value - low) / (high - low), 0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)


def _down(values: np.ndarray, factor: int) -> np.ndarray:
    """The mean of each block of samples: the picture's own texel."""
    rows, cols = values.shape[0] // factor, values.shape[1] // factor
    tail = values.shape[2:]
    return values.reshape(rows, factor, cols, factor, *tail).mean(axis=(1, 3))


def picture(constants: object, planet: Planet, width: int) -> np.ndarray:
    """One planet's ground as an equirectangular picture, RGB nought to one."""
    field = fields.of(constants, planet)
    palette = Palette(planet.value)
    #: What lies in the planet's low ground: the vault says so for every
    #: planet (`terrain.fluid`), and the client reads the same word off the
    #: raster's passport. Not `field.wet`, which says whether the low ground
    #: holds anything at all.
    lava = constants[R.TERRAIN_FLUID][planet.value] == "lava"
    lake_col, deep_col = palette.water(lava)
    codes = biomes.codes(constants)
    #: A biome with no colour is a theme that has fallen behind the vault, and
    #: it stops the bake. The client shouts magenta at the same thing
    #: (`shade.paletteOf`); a picture cannot shout, so it refuses instead --
    #: painted quietly in the shore's tone it would be a whole region of the
    #: wrong ground with nothing anywhere to say so.
    missing = [name for name in [*codes, SHORE_BIOME] if name not in palette.biomes]
    if missing:
        raise SystemExit(f"no --biome-* colour in the theme for: {', '.join(missing)}")
    table = np.array([palette.biomes[name] for name in [*codes, SHORE_BIOME]], dtype=np.float64)
    shore = len(codes)
    raster = biomes.raster(constants, planet)

    height, sup = width // 2, width * SUPER
    lat, lon = _grid(sup, sup // 2)
    #: The height between the cells, as the engine and the shader read it:
    #: the sea is negative, a share of the rise, and the shore falls between
    #: the cells rather than along their edges.
    share = field.rings.between(field.height, lat, lon)
    cells = field.cells_at(lat, lon)
    lake_share = (field.water[cells] == fields.LAKE).astype(np.float64)
    stream = field.stream[cells] / 255.0
    #: Water where the sea is under nought, where a lake covers more than half
    #: the cell, or where the river's ribbon does -- the shader's own cut.
    #: Kept apart, because they are not drawn alike: a lake and the sea are
    #: areas and keep their share of the texel, a river is a line and goes
    #: through a ramp (`shade.ts`: "Lakes and the sea keep their share").
    sea = share < 0.0
    lake = lake_share > BANK_SHARE
    river = stream > BANK_SHARE

    code = raster[cells].astype(np.int64)
    code = np.where(code == NO_BIOME, shore, code)
    ground = table[code]

    #: Averaged down to the picture's own texels: a coast picked once a texel
    #: is a staircase, and a river narrower than a texel is a row of dashes.
    ground = _down(ground, SUPER)
    #: The height as the shader reads it, sea floor and all: the client lights
    #: the water by the slope of its own bed, and a sea flattened to nought
    #: before the light would be lit evenly from shore to deep.
    height_m = _down(share * field.relief_m, SUPER)
    rise = _down(share, SUPER)
    #: A river drawn as the share of the texel it wets would be a thread too
    #: faint to see; the ramp is what keeps a line a line (`shade.RIVER_*`).
    #: The sea and the lakes take their share as it is.
    areas = _down((sea | lake).astype(np.float64), SUPER)
    lines = _smoothstep(RIVER_FAINT, RIVER_FULL, _down(river.astype(np.float64), SUPER))
    wet = np.clip(areas + lines, 0.0, 1.0)[..., None]
    #: How deep the water reads: the sea by its own depth against the deepest
    #: the field has (`shade.deepOf`), the water standing on land by the far
    #: frame's share of the deep tone, for a card is nothing but a far frame.
    deepest = max(-float(field.height.min()), 1e-6)
    sunk = _down(np.minimum(share, 0.0), SUPER)
    deep = np.where(sunk < 0.0, np.clip(-sunk / deepest, 0.0, 1.0), FAR_WATER_DEEP)
    #: The snow and the ice the client lays, by the same law (D-334): the
    #: cell's mean warmth against the snow line, whole a band under it, and a
    #: dry cold keeping only its share; the water frozen below a line of its
    #: own. At the **mean** season, because a picture has no season: the swing
    #: the shader adds by latitude is nought halfway through the year.
    #:
    #: Not the field's own `ice` raster, which the vault writes by
    #: `terrain.ice_c` for the cap the world is born with: that is a colder
    #: line than the season's (`season.ice_c`), and read instead of it the
    #: picture froze noticeably less sea than the game's own globe shows.
    warmth = _down(field.temperature_c[cells].astype(np.float64), SUPER)
    rain = _down(field.rain[cells] / 255.0, SUPER)
    snow_c = float(constants[R.SEASON_SNOW_C])
    band_c = max(float(constants[R.SEASON_SNOW_BAND_C]), 1e-3)
    ice_c = float(constants[R.SEASON_ICE_C])
    dry_rain = max(float(constants[R.SEASON_SNOW_DRY_RAIN]) / 100.0, 1e-3)
    dry_keep = float(constants[R.SEASON_SNOW_DRY_SHARE]) / 100.0
    snow = (1.0 - _smoothstep(snow_c - band_c, snow_c, warmth)) * (
        dry_keep + (1.0 - dry_keep) * _smoothstep(0.0, dry_rain, rain)
    )
    frozen = (1.0 - _smoothstep(ice_c - band_c, ice_c, warmth))[..., None]

    #: The summits take the high ground's grey, as the map does.
    ground = (
        ground
        + (np.array(palette.high) - ground)
        * (HIGH_MIX * _smoothstep(field.mountain_level, 1.0, rise))[..., None]
    )

    shade = _hillshade(height_m, field.radius_m)
    lie = np.clip((height_m - _blur(height_m, RELIEF_TEXELS)) / RELIEF_M, -1.0, 1.0)
    tone = ((AMBIENT + (1.0 - AMBIENT) * shade) * (1.0 + RELIEF_DEPTH * lie))[..., None]

    #: The snow, over the ground and lit as the ground is (`fragment.ts`).
    ground = ground * tone
    if not lava:
        ground = ground + (np.array(SNOW_TONE) * tone - ground) * snow[..., None]
    water = np.array(lake_col) + (np.array(deep_col) - np.array(lake_col)) * deep[..., None]
    #: Frozen water takes the ice's tone: Aurora's ocean is fast ice, not open
    #: sea, and Terra's poles are capped. A planet whose fluid is lava has
    #: neither ice nor snow to give -- molten rock does not freeze at two
    #: degrees below, and nothing grows white on it.
    if not lava:
        water = water + (np.array(ICE_TONE) - water) * frozen
    #: The water takes a little of the slope's light too, as the shader gives
    #: it: a lake under a range is not a flat hole in the relief.
    water = water * (0.85 + 0.15 * shade)[..., None]
    painted = np.clip(ground * (1.0 - wet) + water * wet, 0.0, 1.0)
    assert painted.shape == (height, width, 3), painted.shape
    return painted


# ── PNG, written by hand ────────────────────────────────────────────────────

#: How many colours a picture is cut down to. A ground of muted biomes under
#: one hillshade is not a photograph: two hundred and fifty-six tones hold it
#: without a band anywhere, and a byte a texel instead of three is half the
#: bytes over the wire -- which on a landing page is the whole argument.
COLOURS = 256


def _palette(rgb: np.ndarray, colours: int) -> np.ndarray:
    """The colours of a picture, cut down by median cut.

    The classic: the box of all the pixels is split along its longest side at
    its own median, over and over, and each box gives its mean. The box split
    next is the one whose colours are worst served -- how many pixels it holds
    times how far they spread -- so a sea of two thousand blues does not take
    every slot from a coast of ten greens.
    """
    pixels = np.clip(np.rint(rgb * 255.0), 0, 255).astype(np.int16).reshape(-1, 3)
    boxes = [pixels]
    while len(boxes) < colours:
        boxes.sort(key=lambda box: len(box) * int((box.max(0) - box.min(0)).max()))
        worst = boxes.pop()
        if len(worst) < 2:
            boxes.append(worst)
            break
        side = int((worst.max(0) - worst.min(0)).argmax())
        sorted_box = worst[worst[:, side].argsort()]
        half = len(sorted_box) // 2
        boxes += [sorted_box[:half], sorted_box[half:]]
    return np.array([box.mean(0) for box in boxes], dtype=np.float64)


def _indices(rgb: np.ndarray, palette: np.ndarray) -> np.ndarray:
    """Each texel as the nearest colour of the palette."""
    pixels = np.clip(np.rint(rgb * 255.0), 0, 255).astype(np.int32).reshape(-1, 3)
    out = np.empty(len(pixels), dtype=np.uint8)
    #: In slices: the whole picture against the whole palette at once is a
    #: hundred and thirty million distances and a gigabyte of them.
    step = 1 << 16
    for at in range(0, len(pixels), step):
        piece = pixels[at : at + step]
        out[at : at + step] = ((piece[:, None, :] - palette[None, :, :]) ** 2).sum(2).argmin(1)
    return out.reshape(rgb.shape[:2])


def _filtered(rows: np.ndarray) -> bytes:
    """The scanlines under the filter that costs least, PNG's own heuristic.

    Written here rather than taken from a library because the backend has no
    image library at all, and a picture out of a palette is thirty lines. Only
    the filters that make sense of palette codes are tried: a code is a name,
    not a quantity, so averaging two of them says nothing -- but a row that
    repeats the row above still subtracts to nought, and that is most of a
    sea.
    """
    out = bytearray()
    height, width = rows.shape
    previous = np.zeros(width, dtype=np.uint8)
    for y in range(height):
        line = rows[y].astype(np.int16)
        tries = {
            0: line,
            1: (line - np.concatenate([[0], line[:-1]])) % 256,
            2: (line - previous) % 256,
        }
        #: PNG's own heuristic: the filter whose bytes are smallest read as
        #: signed, because that is what the compressor after them likes.
        best = min(tries, key=lambda kind: int(np.minimum(tries[kind], 256 - tries[kind]).sum()))
        out.append(best)
        out += tries[best].astype(np.uint8).tobytes()
        previous = rows[y]
    return bytes(out)


def png(rgb: np.ndarray) -> bytes:
    """A picture as PNG bytes: one byte a texel out of its own palette."""
    palette = _palette(rgb, COLOURS)
    codes = _indices(rgb, palette)
    height, width = codes.shape

    def chunk(kind: bytes, body: bytes) -> bytes:
        return (
            struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body))
        )

    header = struct.pack(">IIBBBBB", width, height, 8, 3, 0, 0, 0)
    table = np.clip(np.rint(palette), 0, 255).astype(np.uint8).tobytes()
    data = zlib.compress(_filtered(codes), 9)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"PLTE", table)
        + chunk(b"IDAT", data)
        + chunk(b"IEND", b"")
    )


def _vault_build() -> Path | None:
    """The vault's build beside the copy, when nothing names one.

    Walked up rather than taken as `../everselife-vault`: a session works in
    a worktree (`.claude/worktrees/<branch>`), and there the sibling folder is
    another worktree, not the vault. A worktree of the **vault** is named by
    `EVERSELIFE_VAULT_BUILD`, as everything else in this repository names it.
    """
    named = os.environ.get("EVERSELIFE_VAULT_BUILD")
    if named:
        return Path(named)
    for folder in (ROOT, *ROOT.parents):
        build = folder.parent / "everselife-vault" / "build"
        if build.is_dir():
            return build
    return None


def _digest(file: Path) -> str:
    """One file's sha256, or nothing at all if it is not there."""
    if not file.is_file():
        return ""
    got = hashlib.sha256()
    with file.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            got.update(block)
    return got.hexdigest()


def fingerprint(build: Path, width: int) -> dict[str, object]:
    """Everything the pictures were made from, by digest.

    Written beside them, and compared by `--check`. Not the pictures' own
    bytes: two machines with different zlib or numpy encode the same picture
    into different bytes, and a check that cries wolf gets turned off. The
    sources either changed or they did not, and that is a question about
    files, answerable without numpy, without the backend and in a blink.
    """
    return {
        "width": width,
        "field": {
            planet.value: [
                _digest(build / "field" / f"{planet.value}{end}") for end in (".npz", ".json")
            ]
            for planet in Planet
        },
        "watched": {name: _digest(ROOT / name) for name in WATCHED},
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=OUT_DIR, help="where the pictures go")
    parser.add_argument("--width", type=int, default=WIDTH, help="the picture's width in texels")
    parser.add_argument(
        "--check",
        action="store_true",
        help="say whether the pictures still match their sources, and write nothing",
    )
    args = parser.parse_args()
    #: A power of two or the shader cannot wrap it (see WIDTH).
    if args.width < 2 or args.width & (args.width - 1):
        parser.error(f"--width must be a power of two, not {args.width}")

    build = _vault_build()
    if build is None or not build.is_dir():
        #: A clone with no vault beside it can neither bake nor check, and
        #: says which of the two it was asked for rather than falling over:
        #: the check hangs off a commit hook, and a hook must not stop a
        #: commit in a copy that was never meant to draw a planet.
        where = "no vault build found: name one in EVERSELIFE_VAULT_BUILD"
        if args.check:
            print(f"planets: not checked -- {where}")
            return 0
        raise SystemExit(where)
    want = fingerprint(build, args.width)
    record = args.out / SOURCES
    if args.check:
        #: Nothing is loaded and nothing is drawn: the whole check is a few
        #: digests, so it can hang off a hook or a vault resync.
        have = json.loads(record.read_text(encoding="utf-8")) if record.is_file() else None
        missing = [p.value for p in Planet if not (args.out / f"{p.value}.png").is_file()]
        if missing:
            print("no picture for: " + ", ".join(missing), file=sys.stderr)
        if have != want:
            print(
                "the planets' pictures are older than what they are made of"
                + (f" ({', '.join(_moved(have, want))})" if have else "")
                + "; rebake with tools/planet_pictures.py",
                file=sys.stderr,
            )
        if missing or have != want:
            return 1
        print(f"planets: {len(Planet)} pictures, current")
        return 0

    args.out.mkdir(parents=True, exist_ok=True)
    for planet in Planet:
        body = png(picture(constants_of(build), planet, args.width))
        file = args.out / f"{planet.value}.png"
        file.write_bytes(body)
        #: The path as it is when it lies outside the copy: `--out` takes any
        #: folder, and a pretty relative path is not worth a traceback.
        try:
            shown = file.relative_to(ROOT)
        except ValueError:
            shown = file
        print(f"{shown}  {len(body) / 1024:.0f} KiB  {hashlib.sha256(body).hexdigest()[:12]}")
    #: `newline` explicitly: the repository is LF everywhere (`.gitattributes`),
    #: and python on Windows would otherwise write a record that differs from
    #: the one git keeps.
    record.write_text(
        json.dumps(want, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n"
    )
    return 0


def _moved(have: dict | None, want: dict) -> list[str]:
    """Which of the sources is not what it was, for the message."""
    if not isinstance(have, dict):
        return ["no record of what they were made from"]
    if have.get("width") != want["width"]:
        return [f"width {have.get('width')} -> {want['width']}"]
    gone = [
        name for name, got in want["watched"].items() if have.get("watched", {}).get(name) != got
    ]
    gone += [
        f"field/{planet}"
        for planet, got in want["field"].items()
        if have.get("field", {}).get(planet) != got
    ]
    return gone or ["nothing named, but the record differs"]


@functools.cache
def constants_of(build: Path):
    """The vault's constants, loaded once however many planets are drawn."""
    return bootstrap(build)[0]


if __name__ == "__main__":
    raise SystemExit(main())
