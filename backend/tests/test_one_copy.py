# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""A file of the suite is one module, and its state is one copy.

`tests/` has no `__init__.py`, so pytest imports every file under its bare
name: `conftest`, `automat_kit`, `test_session`. The directory above it is a
namespace package all the same (`pythonpath = ["."]`), so naming a sibling
through it -- `tests.conftest` -- is not another spelling of the same import.
It reads the same file a second time and binds it under a second name. Python
then holds two module objects for one source, and every module-level name in
it exists twice.

Nothing fails when that happens, which is the whole danger. Helpers are
usually stateless, both copies behave alike, the suite stays green, and the
second copy costs only its import. The one that cost more was
`conftest._schema_built` -- the flag that says the schema has been built for
this run. The second copy starts at `False`, so the first `reset` reached
through the prefix found no schema and built one: a `drop_all` / `create_all`
in the middle of a run, measured at two to four seconds a time.

Ruff carries most of the weight and carries it earlier: `TID251` refuses the
name `tests` anywhere in a file, in a function body as readily as at the top,
and it refuses it at the commit rather than at the end of a run. What a linter
cannot see is a name assembled while the suite runs -- `importlib`, an import
mode changed, an `__init__.py` or a `sys.path` entry that turns every bare
import into a second copy at a stroke. Those show at collection, when every
module-level import of every collected file has run, and that is what this
reads. It is the condition and not the spelling, and its reach is what has
been imported by the time it runs: a duplicate that only a test body would
create is ruff's to catch, not this one's.
"""

from __future__ import annotations

import sys
from collections import defaultdict
from pathlib import Path

TESTS = Path(__file__).resolve().parent


def test_no_file_of_the_suite_is_loaded_twice() -> None:
    """One source, one module object."""
    names: defaultdict[Path, list[str]] = defaultdict(list)
    for name, module in list(sys.modules.items()):
        source = getattr(module, "__file__", None)
        if source and (found := Path(source).resolve()).is_relative_to(TESTS):
            names[found].append(name)

    twice = {found.name: sorted(bound) for found, bound in names.items() if len(bound) > 1}
    assert not twice, (
        f"one file, two modules, two copies of its state: {twice}. "
        "A sibling of the suite is reached through the `tests` package: name it "
        "by its bare name instead (`from conftest import reset`)."
    )
