# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The cross-branch head check must read every migration it is handed.

`tools/check_migration_parents.py` gathers the migrations of every branch on
the machine and asks whether they will make one chain. Both halves of that
gathering have failed by *reading nothing and saying nothing* -- the byte
counts of `cat-file --batch` against a decoded string, and a pathspec read
against the current directory -- and each is written up where it happened,
in `_read` and `_blobs`.

What both earn is a test, because neither is loud. A migration missed leaves
its parent unclaimed and the forecast invents a head: wrong in the harmless
direction. Miss one half of a genuine collision, though, and its revision is
nowhere at all -- the head count comes out at one, the check exits 0, and
that is the "молчаливое «чисто»" the module itself calls worse than a
refusal, over the single fault it exists to catch. Two branches building on
one parent reached main four times on 2026-09-02.

So what is asked here is a count, and it is asked of real git in a throwaway
repository: this one's object store is not written to, and no database is
needed.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from tools.check_migration_parents import VERSIONS, Unusable, _blobs, _read, parse

#: A migration shaped as alembic writes them, carrying the Russian this
#: repository puts in them -- the note above the revision and the wording of a
#: refusal. It is test data and not prose of ours: those characters are two
#: bytes apiece and one character apiece, and that gap is the whole defect.
BODY = '''\
"""Шаг {number}: {title}.

Ревизия: {revision}.
"""

revision = "{revision}"
down_revision = {parent}


def upgrade() -> None:
    #: «Нельзя копать глубже» -- отказ, который прочтёт игрок.
    op.execute("select 1")
'''


def _repo(tmp_path: Path) -> Path:
    """A repository of its own: `hash-object -w` must not write into ours."""
    subprocess.run(  # noqa: S603, S607 -- our own git, fixed args
        ["git", "init", "--quiet", str(tmp_path)], capture_output=True, check=True
    )
    return tmp_path


def _store(repo: Path, body: str) -> str:
    """The blob sha of `body`, written into `repo` -- as `ls-tree` would give it."""
    done = subprocess.run(  # noqa: S603, S607 -- our own git, fixed args
        ["git", "hash-object", "-w", "--stdin"],
        input=body.encode("utf-8"),
        capture_output=True,
        cwd=repo,
        check=True,
    )
    return done.stdout.decode("ascii").strip()


def _six(repo: Path) -> dict[str, str]:
    """Six migrations in a chain, every one of them holding Russian."""
    written: dict[str, str] = {}
    for number in range(6):
        parent = "None" if number == 0 else f'"rev{number - 1:04d}"'
        body = BODY.format(
            number=number, title="перепись", revision=f"rev{number:04d}", parent=parent
        )
        written[_store(repo, body)] = body
    assert len(written) == 6, "шесть разных тел должны дать шесть разных блобов"
    return written


def test_every_blob_of_the_batch_comes_back_though_the_bodies_are_russian(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """As many blobs back as were asked for, each body byte for byte.

    The count is the assertion that matters. Sliced by a byte count after
    decoding, the batch loses a record for every one that holds non-ASCII
    ahead of it -- and loses it without a word, which is what makes the head
    count downstream a number nobody can trust.
    """
    repo = _repo(tmp_path)
    monkeypatch.chdir(repo)
    written = _six(repo)

    read = _read(set(written))

    assert set(read) == set(written), (
        f"из {len(written)} блобов вернулось {len(read)}; "
        f"потеряны: {sorted(set(written) - set(read))}"
    )
    for sha, body in written.items():
        assert read[sha] == body, f"{sha}: тело пришло не тем, чем было записано"

    #: And through the parser the check itself runs on them: a body cut short
    #: mid-file can still hold `revision` and lie about having been read whole.
    revisions = sorted(found[0] for found in map(parse, read.values()) if found is not None)
    assert revisions == [f"rev{number:04d}" for number in range(6)], revisions


def test_a_batch_that_loses_its_place_refuses_instead_of_answering(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A record the reader cannot make sense of stops it.

    Skipping the record -- `continue`, the obvious thing -- is what turns a
    desynchronised parse into an invented head, or into a collision nobody is
    told about. An object git has not got stands in that place here: the
    answer is `<sha> missing`, a header with no body and nothing to skip past,
    and the reader must say so rather than hand back a shorter dictionary.
    """
    repo = _repo(tmp_path)
    monkeypatch.chdir(repo)
    kept = next(iter(_six(repo)))
    absent = "0" * 40

    with pytest.raises(Unusable) as refused:
        _read({kept, absent})

    assert absent in str(refused.value), str(refused.value)


def test_the_branches_are_read_from_wherever_the_check_is_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`backend/` must give the same answer as the root, and it did not.

    A pathspec is read against the current directory unless `--full-tree` is
    given, so from `backend/` -- where `pytest`, `alembic` and `ruff` are run
    here -- `backend/migrations/versions` became `backend/backend/...` and
    every branch answered with an empty listing. No error: the forecast simply
    carried on with the worktree files alone, and any branch without a
    worktree on disk was gone from it. The same silent "clean" as a lost blob,
    and reached by nothing worse than `cd backend`.
    """
    repo = _repo(tmp_path)
    versions = repo / VERSIONS
    versions.mkdir(parents=True)
    body = BODY.format(number=0, title="перепись", revision="rev0000", parent="None")
    (versions / "rev0000_perepis.py").write_text(body, encoding="utf-8")
    subprocess.run(  # noqa: S603, S607 -- our own git, fixed args
        ["git", "add", "-A"], capture_output=True, cwd=repo, check=True
    )
    #: An identity spelled out here: a CI runner need not have one configured.
    subprocess.run(  # noqa: S603, S607 -- our own git, fixed args
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "one"],
        capture_output=True,
        cwd=repo,
        check=True,
    )

    seen = {}
    for where in (repo, repo / "backend"):
        monkeypatch.chdir(where)
        seen[where.name] = _blobs("HEAD")

    assert seen[repo.name] and all(found == seen[repo.name] for found in seen.values()), (
        f"каталог запуска меняет ответ: { ({name: len(found) for name, found in seen.items()}) }"
    )
