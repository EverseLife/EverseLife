# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""Court: complaint, case, verdict, enforcement (D-095, D-117, D-166).

Checked is what the court was introduced for:

* a complaint costs a fee, and the fee goes to the city treasury, not into nowhere;
* whoever the city gave the `justice` right judges -- and only they;
* the verdict is enforced by the engine **at once**, without guards and without
  anybody's participation;
* a sanction the engine cannot enforce is rejected aloud.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Catalog, Constants
from src.constants import registry as R
from src.engine import city as town
from src.engine import justice, ledger, travel, world
from src.models.city import Citizen, Power
from src.models.justice import CaseState
from src.models.ledger import AccountKind, PostingReason
from src.models.world import Layer
from src.units import money


async def _court(session: AsyncSession, catalog: Catalog):
    """A city with a judge, a plaintiff and a defendant with money."""
    stamp = uuid.uuid4().hex[:8]
    planet = await world.create_node(
        session, f"terra.{stamp}", "Терра", area_m2=1, layer=Layer.SPACE
    )
    delegate = await world.create_node(
        session,
        f"terra.city.{stamp}",
        "Суд",
        area_m2=1,
        layer=Layer.PLANET,
        parent=planet,
    )
    #: The core is also this city's door (D-206): its whole built-up area is one
    #: node, and the road out of prison has to be tied to something.
    core = await world.create_node(
        session,
        f"terra.city.{stamp}.core",
        "Ядро",
        area_m2=100,
        parent=delegate,
        properties={"ring": 0, travel.EXIT: True},
    )
    city = await town.found(session, catalog, delegate, "Судоград")
    core.owner_city_id = city.id
    await session.flush()

    judge, _ = await _resident(session, core, city, "Судья", funds=0)
    await town.install_founder(session, city, judge)
    plaintiff, _ = await _resident(session, core, city, "Истец", funds=100)
    defendant, body = await _resident(session, core, city, "Ответчик", funds=50)
    return city, core, judge, plaintiff, defendant, body


async def _body(session: AsyncSession, who):
    from src.engine import death

    return await death.alive_body(session, who.id)


async def _resident(session: AsyncSession, node, city, name: str, *, funds: float = 0):
    identity = await world.create_identity(session, f"{name}-{uuid.uuid4().hex[:6]}")
    body = await world.print_body(session, identity, node)
    session.add(Citizen(identity_id=identity.id, city_id=city.id))
    if funds:
        account = await ledger.account_for(session, AccountKind.IDENTITY, identity.id)
        genesis = await ledger.account_for(session, AccountKind.GENESIS, None)
        await ledger.transfer(
            session,
            PostingReason.GENESIS,
            debit=genesis.id,
            credit=account.id,
            amount=money(funds),
            memo={},
        )
    await session.flush()
    return identity, body


# --- complaint ---------------------------------------------------------------


async def test_fee_goes_to_treasury(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """A good court is profitable for the city -- that is the point of the fee (D-117)."""
    city, _, _, plaintiff, defendant, _ = await _court(session, catalog)
    before = await town.treasury_balance(session, city)

    case = await justice.sue(session, constants, city, plaintiff, defendant, "увёл повозку")

    after = await town.treasury_balance(session, city)
    assert after - before == money(constants[R.JUSTICE_COURT_FEE])
    assert case.state is CaseState.OPEN


async def test_no_suit_without_money_for_fee(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    city, core, _, _, defendant, _ = await _court(session, catalog)
    pauper, _ = await _resident(session, core, city, "Нищий", funds=0)
    with pytest.raises(justice.CannotPayFee):
        await justice.sue(session, constants, city, pauper, defendant, "обидел")


async def test_limitation_period_expired(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """The court is not an archive of grudges."""
    city, _, _, plaintiff, defendant, _ = await _court(session, catalog)
    long_ago = datetime.now(UTC) - timedelta(days=constants[R.JUSTICE_CLAIM_WINDOW] + 1)
    with pytest.raises(justice.TooLate):
        await justice.sue(
            session,
            constants,
            city,
            plaintiff,
            defendant,
            "старая обида",
            happened_at=long_ago,
        )


# --- verdict -----------------------------------------------------------------


async def test_only_holder_of_right_judges(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    city, core, _, plaintiff, defendant, _ = await _court(session, catalog)
    case = await justice.sue(session, constants, city, plaintiff, defendant, "спор")
    stranger, _ = await _resident(session, core, city, "Посторонний")

    with pytest.raises(justice.NotJudge):
        await justice.judge(session, constants, catalog, stranger, case, sanction=justice.FINE)


async def test_fine_collected_to_treasury_and_remainder_becomes_debt(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    city, _, judge, plaintiff, defendant, _ = await _court(session, catalog)
    case = await justice.sue(session, constants, city, plaintiff, defendant, "порча")
    before = await town.treasury_balance(session, city)

    #: The defendant has fifty, the fine is eighty: collected what there is.
    penalty = await justice.judge(
        session, constants, catalog, judge, case, sanction=justice.FINE, amount=80
    )

    account = await ledger.account_for(session, AccountKind.IDENTITY, defendant.id)
    assert await ledger.balance(session, account.id) == 0, "взыскано всё, что было"
    assert await town.treasury_balance(session, city) - before == money(50)
    assert penalty.debt == money(30), "остаток записан долгом"
    assert case.state is CaseState.JUDGED


async def test_imprisonment_holds_body_in_node(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """The engine enforces, not guards: the verdict does not depend on who is online."""
    city, core, judge, plaintiff, defendant, body = await _court(session, catalog)
    dest = await world.create_node(
        session, f"terra.far.{uuid.uuid4().hex[:6]}", "Прочь", area_m2=100
    )
    await travel.connect(session, core, dest, base_seconds=60)

    case = await justice.sue(session, constants, city, plaintiff, defendant, "снос")
    penalty = await justice.judge(
        session, constants, catalog, judge, case, sanction=justice.PRISON, days=3
    )
    assert penalty.until is not None

    with pytest.raises(travel.Imprisoned):
        await travel.depart(session, constants, body, dest)

    #: The term is up -- the journal job lifts the sanction, and the road is open.
    from sqlalchemy import select

    from src.models.job import Job, JobKind, JobState

    job = (
        (
            await session.execute(
                select(Job).where(
                    Job.kind == JobKind.SANCTION_LIFT.value,
                    Job.state == JobState.PENDING,
                )
            )
        )
        .scalars()
        .first()
    )
    assert job is not None
    await justice.lift(session, job)
    assert await justice.imprisoned(session, defendant.id) is None
    assert await travel.depart(session, constants, body, dest) is not None


async def test_imprisonment_no_longer_than_ceiling(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    city, _, judge, plaintiff, defendant, _ = await _court(session, catalog)
    case = await justice.sue(session, constants, city, plaintiff, defendant, "снос")
    now_ = datetime.now(UTC)
    penalty = await justice.judge(
        session,
        constants,
        catalog,
        judge,
        case,
        sanction=justice.PRISON,
        days=999,
        now=now_,
    )
    ceiling = now_ + timedelta(days=constants[R.JUSTICE_PRISON_MAX])
    assert penalty.until == ceiling


async def test_exile_by_verdict_removes_citizenship(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Banishment, not death: the exiled lives quietly in another city."""
    city, _, judge, plaintiff, defendant, _ = await _court(session, catalog)
    case = await justice.sue(session, constants, city, plaintiff, defendant, "измена")
    await justice.judge(session, constants, catalog, judge, case, sanction=justice.EXILE)
    assert await town.citizenship(session, defendant.id) is None


async def test_unenforceable_sanction_rejected_aloud(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """A verdict without enforcement is worse than refusing a verdict (D-166)."""
    city, _, judge, plaintiff, defendant, _ = await _court(session, catalog)
    case = await justice.sue(session, constants, city, plaintiff, defendant, "спор")
    with pytest.raises(justice.Unenforceable):
        await justice.judge(session, constants, catalog, judge, case, sanction="confiscation")
    assert case.state is CaseState.OPEN, "дело осталось нерассмотренным"


async def test_acquittal_is_also_verdict(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """There are no hanging cases: each ends with a decision."""
    city, _, judge, plaintiff, defendant, _ = await _court(session, catalog)
    case = await justice.sue(session, constants, city, plaintiff, defendant, "напраслина")
    penalty = await justice.judge(session, constants, catalog, judge, case, verdict="не доказано")
    assert penalty is None
    assert case.state is CaseState.DISMISSED
    assert case.verdict == "не доказано"
    assert not await justice.active(session, defendant.id)


async def test_same_case_not_tried_twice(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    city, _, judge, plaintiff, defendant, _ = await _court(session, catalog)
    case = await justice.sue(session, constants, city, plaintiff, defendant, "спор")
    await justice.judge(session, constants, catalog, judge, case)
    with pytest.raises(justice.JusticeError):
        await justice.judge(
            session, constants, catalog, judge, case, sanction=justice.FINE, amount=1
        )


async def test_court_right_granted_separately(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """`justice` is a narrow right: the judge need not be the ruler (D-155)."""
    city, core, judge, plaintiff, defendant, _ = await _court(session, catalog)
    magistrate, _ = await _resident(session, core, city, "Мировой")
    #: Appointment is in-person (D-155): the judge goes to the administration.
    yard = await world.node_container(session, core)
    await world.grant_item(session, yard, town.HALL, quality=60, origin="тест")
    judge_body = await _body(session, judge)
    await town.appoint(
        session,
        judge,
        city,
        magistrate,
        title="Мировой судья",
        powers=[Power.JUSTICE.value],
        body=judge_body,
    )
    case = await justice.sue(session, constants, city, plaintiff, defendant, "спор")
    await justice.judge(
        session, constants, catalog, magistrate, case, sanction=justice.FINE, amount=10
    )
    assert case.state is CaseState.JUDGED
