# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The world page's machinery: its scripts and the planets they draw.

Apart from `test_app.py`, which is about pages, addresses and what search
engines are told. What is pinned here is the one thing on the site that is not
written by hand: the grounds of the four planets, baked out of the game's own
field by `tools/planet_pictures.py` and served as files. Both ends of that link
break silently -- a planet renamed on one side, a picture that never landed, a
script that stopped parsing -- and every one of them leaves the page serving
200 with its subject gone.

Run from `landing/`: `python -m pytest -q`.
"""

import re
import shutil
import struct
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import PLANET_CACHE, app

client = TestClient(app)

#: The eight bytes every PNG begins with.
PNG_MAGIC = bytes([0x89]) + b"PNG" + bytes([0x0D, 0x0A, 0x1A, 0x0A])


def test_the_scripts_parse() -> None:
    """Both scripts are valid JavaScript.

    Nothing else here would notice if they were not: the pages are served as
    files, the tests read them as text, and a browser that cannot parse a
    script says so only in its own console. `space.js` in particular carries a
    shader as a template literal, where one stray backtick in a GLSL comment
    ends the string and takes the whole file down -- the page then loses its
    sky and its planets and goes on serving 200.
    """
    node = shutil.which("node")
    if not node:
        pytest.skip("no node to parse with")
    for name in ("site.js", "space.js"):
        done = subprocess.run(
            [node, "--check", str(Path(__file__).parent / name)],
            capture_output=True,
            text=True,
        )
        assert done.returncode == 0, f"{name}: {done.stderr}"


def test_every_planet_the_shader_asks_for_is_served() -> None:
    """The world page draws four grounds, and the four files must be here.

    They are not written by hand: `tools/planet_pictures.py` bakes them out of
    the game's field, and the shader names them one by one. A planet renamed on
    one side and not the other is a world that goes on turning as a dark ball --
    no error anywhere, and the page's whole subject gone.
    """
    shader = (Path(__file__).parent / "space.js").read_text(encoding="utf-8")
    asked = set(re.findall(r'file:\s*"([a-z]+)"', shader))
    assert len(asked) == 4, f"the shader names {sorted(asked)}, not four planets"
    for name in sorted(asked):
        answer = client.get(f"/planets/{name}.png")
        assert answer.status_code == 200, name
        assert answer.headers["content-type"] == "image/png", name
        assert answer.headers["cache-control"] == PLANET_CACHE, name
        #: A real PNG of the size the shader needs. The encoder is written by
        #: hand (`tools/planet_pictures.py` has no image library to call), and
        #: a header it got wrong would be a black ball on the page and nothing
        #: at all in any log. The width must be a power of two as well: the
        #: shader wraps the picture east to west, which WebGL 1 allows on a
        #: power of two alone.
        head = answer.content[:26]
        assert head[:8] == PNG_MAGIC, name
        width, height = struct.unpack(">II", head[16:24])
        assert (width, height) == (1024, 512), f"{name}: {width}x{height}"
        assert width & (width - 1) == 0, name
    #: And nothing but those: the route answers off a list of the files that
    #: are there, so a name that is not one of them cannot reach a file. The
    #: escaped path is what a client actually sends -- an unescaped `..` is
    #: resolved away before the request leaves.
    assert client.get("/planets/nope.png").status_code == 404
    assert client.get("/planets/..%2Fapp.py").status_code == 404
