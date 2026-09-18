# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The planet's picture over HTTP: squeezed once, named by what it holds,
kept by the browser for as long as it is what it says (2026-09-18).

The rasters and the sketch are constants of the vault, megabytes of them,
and the map could not draw the real planet until they had come -- seconds on
a slow line, with the old vector ground standing in meanwhile. Four things
made them cost more than they had to, and this module is the answer to all
four:

- **squeezed on every request.** The API's gzip middleware deflated the same
  megabyte and a half for every reader: CPU per request, and a wait in the
  queue of the threads it squeezes large bodies in. A `Packed` holds the
  bytes squeezed once, and the middleware leaves an answer that already says
  how it is encoded alone;
- **named by the wrong thing.** The `ETag` was the digest of the constants,
  which does not cover the field's own files in the vault's build: a new
  field under the same constants kept the old name. The name here is a hash
  of the bytes themselves;
- **never answered with `304`.** The `ETag` was sent and a browser asking
  `If-None-Match` got the whole body again, so after the hour of `max-age`
  every visit downloaded the planet anew;
- **kept by an hour, not by what they are.** The rasters are now asked for
  by the version of the whole set (`version`, which the sketch's raster
  passport carries) and kept a year under that address (`PICTURE_KEEP_S`);
  the sketch is asked every visit (`no-cache`) and answered with no body
  while nothing changed. So the passport a browser reads and the rasters it
  reads by it are always one set: kept apart by two ages, an old passport
  could have met new rasters, and read their codes by another table.
"""

from __future__ import annotations

import gzip
import hashlib
import json
from dataclasses import dataclass

from fastapi import Request, Response

from src.constants import Constants
from src.engine import rasters, terrain
from src.models.world import Planet
from src.runtime import PICTURE_KEEP_S

#: How hard the bytes are squeezed: once per process, so the level is not a
#: cost per request -- and past six these rasters shrink by a few bytes in a
#: million (measured on Terra's, 2026-09-18), so six it is.
LEVEL = 6
#: How much of the hash names the bytes: sixteen hex digits, as the
#: constants' digest is named.
TAG_HEX = 16
#: What a squeezed body's name is told from the plain one's by: the two are
#: different bodies, and a strong name belongs to one body (RFC 9110 8.8.1).
SQUEEZED = ".gz"


@dataclass(frozen=True, slots=True)
class Packed:
    """A constant of the vault as the route sends it: the bytes, the same
    squeezed, and the name of what they hold."""

    raw: bytes
    squeezed: bytes
    tag: str


def pack(raw: bytes) -> Packed:
    """The bytes squeezed and named. Kept by the caller for the process: the
    point is to do this once."""
    return Packed(
        raw=raw,
        squeezed=gzip.compress(raw, compresslevel=LEVEL, mtime=0),
        tag=hashlib.sha256(raw).hexdigest()[:TAG_HEX],
    )


def packed(raw: bytes) -> Packed:
    """The one `Packed` of these bytes, made the first time they are asked
    for. Keyed by the bytes object itself: the engine keeps its rasters and
    sketches for the process, so the same answer is the same object."""
    key = id(raw)
    got = _PACKED.get(key)
    if got is None or got.raw is not raw:
        got = pack(raw)
        _PACKED[key] = got
    return got


#: Every `Packed` made, by the id of its bytes; each holds its bytes, so an
#: id here cannot be taken by another object while its entry lives.
_PACKED: dict[int, Packed] = {}


def version(constants: Constants, planet: Planet) -> str:
    """The name of a planet's whole picture: every raster it serves, at every
    fineness, in one hash. The rasters are asked for by it and change with
    it, together, as one set."""
    got = _VERSIONS.get(planet)
    if got is None or got[0] is not constants:
        whole = hashlib.sha256()
        for nside, kind in rasters.served(constants, planet):
            raw = rasters.raster_bytes(constants, planet, kind, nside)
            if raw is not None:
                whole.update(packed(raw).tag.encode())
        got = (constants, whole.hexdigest()[:TAG_HEX])
        _VERSIONS[planet] = got
    return got[1]


#: The picture's version by planet, with the constants it was named under.
_VERSIONS: dict[Planet, tuple[Constants, str]] = {}


def sketch_bytes(constants: Constants, planet: Planet) -> bytes:
    """The planet's sketch as the JSON the route used to render from it --
    the same separators and the same refusal of a NaN as FastAPI's own
    `JSONResponse` -- with the picture's `version` in its raster passport,
    written once per sketch, which the engine keeps."""
    sketch = terrain.sketch(constants, planet)
    got = _SKETCHES.get(id(sketch))
    if got is None or got[0] is not sketch:
        #: The version is the transport's, not the field's: the engine's
        #: sketch is left as it is, and the answer carries it.
        told = {**sketch, "raster": {**sketch["raster"], "version": version(constants, planet)}}
        text = json.dumps(told, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
        got = (sketch, text.encode())
        _SKETCHES[id(sketch)] = got
    return got[1]


#: The sketches as JSON, by the id of the sketch, which each entry holds.
_SKETCHES: dict[int, tuple[dict, bytes]] = {}


def warm(constants: Constants) -> None:
    """Squeeze and name every planet's picture now: the rasters at every
    fineness they are served at, and the sketch with the version. A second
    of deflate in a request would be paid by whoever came first, as the
    cutting would (`rasters.warm`), so it is paid for at startup beside it."""
    for planet in Planet:
        #: The sketch names the version, and the version packs every raster.
        packed(sketch_bytes(constants, planet))


def answer(request: Request, got: Packed, media_type: str, lasting: bool = False) -> Response:
    """The answer for a browser: nothing but the name if it holds these
    bytes already, else the bytes -- squeezed if it takes them so. Kept a
    year where the address names the version (`lasting`), and otherwise
    asked about at every use."""
    squeeze = _takes_gzip(request.headers.get("accept-encoding"))
    tag = f'"{got.tag}{SQUEEZED}"' if squeeze else f'"{got.tag}"'
    headers = {
        "Cache-Control": (
            f"public, max-age={PICTURE_KEEP_S}, immutable" if lasting else "public, no-cache"
        ),
        "ETag": tag,
        "Vary": "Accept-Encoding",
    }
    if _holds(request.headers.get("if-none-match"), got.tag):
        return Response(status_code=304, headers=headers)
    if squeeze:
        return Response(
            content=got.squeezed,
            media_type=media_type,
            headers={**headers, "Content-Encoding": "gzip"},
        )
    return Response(content=got.raw, media_type=media_type, headers=headers)


def _takes_gzip(accepted: str | None) -> bool:
    """Whether `Accept-Encoding` takes gzip -- read as the API's gzip
    middleware reads it, by the word alone: a plain body sent where it sees
    the word, it would squeeze anyway, and a weight of nought on gzip is
    something no browser sends."""
    return "gzip" in (accepted or "")


def _holds(asked: str | None, tag: str) -> bool:
    """Whether `If-None-Match` names these bytes, plain or squeezed: a list
    of names, each quoted, perhaps weak (`W/`) -- a proxy that squeezed the
    body itself may have weakened ours -- or the star that names anything."""
    if not asked:
        return False
    for name in asked.split(","):
        name = name.strip().removeprefix("W/")
        if name == "*" or name in (f'"{tag}"', f'"{tag}{SQUEEZED}"'):
            return True
    return False
