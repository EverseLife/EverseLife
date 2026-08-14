"""Суд: жалоба, дело, приговор, исполнение (D-095, D-117, D-166).

Проверяется то, ради чего суд введён:

* жалоба стоит пошлины, и пошлина идёт в казну города, а не в никуда;
* судит тот, кому город дал право `justice`, — и только он;
* приговор исполняется движком **сразу**, без стражи и без чужого участия;
* санкция, которую движок не умеет исполнять, отвергается вслух.
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


async def _суд(session: AsyncSession, catalog: Catalog):
    """Город с судьёй, истцом и ответчиком при деньгах."""
    метка = uuid.uuid4().hex[:8]
    планета = await world.create_node(
        session, f"terra.{метка}", "Терра", area_m2=1, layer=Layer.SPACE
    )
    представитель = await world.create_node(
        session, f"terra.city.{метка}", "Суд", area_m2=1,
        layer=Layer.PLANET, parent=планета,
    )
    ядро = await world.create_node(
        session, f"terra.city.{метка}.core", "Ядро", area_m2=100,
        parent=представитель, properties={"кольцо": 0},
    )
    город = await town.found(session, catalog, представитель, "Судоград")
    ядро.owner_city_id = город.id
    await session.flush()

    судья, _ = await _житель(session, ядро, город, "Судья", денег=0)
    await town.install_founder(session, город, судья)
    истец, _ = await _житель(session, ядро, город, "Истец", денег=100)
    ответчик, тело = await _житель(session, ядро, город, "Ответчик", денег=50)
    return город, ядро, судья, истец, ответчик, тело


async def _тело(session: AsyncSession, кто):
    from src.engine import death

    return await death.alive_body(session, кто.id)


async def _житель(
    session: AsyncSession, узел, город, имя: str, *, денег: float = 0
):
    identity = await world.create_identity(session, f"{имя}-{uuid.uuid4().hex[:6]}")
    body = await world.print_body(session, identity, узел)
    session.add(Citizen(identity_id=identity.id, city_id=город.id))
    if денег:
        счёт = await ledger.account_for(session, AccountKind.IDENTITY, identity.id)
        genesis = await ledger.account_for(session, AccountKind.GENESIS, None)
        await ledger.transfer(
            session, PostingReason.GENESIS,
            debit=genesis.id, credit=счёт.id, amount=money(денег), memo={},
        )
    await session.flush()
    return identity, body


# --- жалоба -----------------------------------------------------------------


async def test_пошлина_уходит_в_казну(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Хороший суд выгоден городу — в этом и смысл пошлины (D-117)."""
    город, _, _, истец, ответчик, _ = await _суд(session, catalog)
    было = await town.treasury_balance(session, город)

    дело = await justice.sue(
        session, constants, город, истец, ответчик, "увёл повозку"
    )

    стало = await town.treasury_balance(session, город)
    assert стало - было == money(constants[R.JUSTICE_COURT_FEE])
    assert дело.state is CaseState.OPEN


async def test_без_денег_на_пошлину_не_судятся(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    город, ядро, _, _, ответчик, _ = await _суд(session, catalog)
    нищий, _ = await _житель(session, ядро, город, "Нищий", денег=0)
    with pytest.raises(justice.CannotPayFee):
        await justice.sue(session, constants, город, нищий, ответчик, "обидел")


async def test_срок_давности_вышел(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Суд — не архив обид."""
    город, _, _, истец, ответчик, _ = await _суд(session, catalog)
    давно = datetime.now(UTC) - timedelta(
        days=constants[R.JUSTICE_CLAIM_WINDOW] + 1
    )
    with pytest.raises(justice.TooLate):
        await justice.sue(
            session, constants, город, истец, ответчик, "старая обида",
            happened_at=давно,
        )


# --- приговор ---------------------------------------------------------------


async def test_судит_только_имеющий_право(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    город, ядро, _, истец, ответчик, _ = await _суд(session, catalog)
    дело = await justice.sue(session, constants, город, истец, ответчик, "спор")
    посторонний, _ = await _житель(session, ядро, город, "Посторонний")

    with pytest.raises(justice.NotJudge):
        await justice.judge(
            session, constants, catalog, посторонний, дело, sanction=justice.FINE
        )


async def test_штраф_взыскивается_в_казну_а_остаток_становится_долгом(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    город, _, судья, истец, ответчик, _ = await _суд(session, catalog)
    дело = await justice.sue(session, constants, город, истец, ответчик, "порча")
    было = await town.treasury_balance(session, город)

    #: У ответчика полсотни, штраф — восемьдесят: взыскано сколько есть.
    наказание = await justice.judge(
        session, constants, catalog, судья, дело, sanction=justice.FINE, amount=80
    )

    счёт = await ledger.account_for(session, AccountKind.IDENTITY, ответчик.id)
    assert await ledger.balance(session, счёт.id) == 0, "взыскано всё, что было"
    assert await town.treasury_balance(session, город) - было == money(50)
    assert наказание.debt == money(30), "остаток записан долгом"
    assert дело.state is CaseState.JUDGED


async def test_заключение_держит_тело_в_узле(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Исполняет движок, а не стража: приговор не зависит от того, кто онлайн."""
    город, ядро, судья, истец, ответчик, тело = await _суд(session, catalog)
    куда = await world.create_node(
        session, f"terra.far.{uuid.uuid4().hex[:6]}", "Прочь", area_m2=100
    )
    await travel.connect(session, ядро, куда, base_seconds=60)

    дело = await justice.sue(session, constants, город, истец, ответчик, "снос")
    наказание = await justice.judge(
        session, constants, catalog, судья, дело, sanction=justice.PRISON, days=3
    )
    assert наказание.until is not None

    with pytest.raises(travel.Imprisoned):
        await travel.depart(session, constants, тело, куда)

    #: Срок вышел — задание журнала снимает санкцию, и дорога открыта.
    from sqlalchemy import select

    from src.models.job import Job, JobKind, JobState

    задание = (
        await session.execute(
            select(Job).where(
                Job.kind == JobKind.SANCTION_LIFT.value,
                Job.state == JobState.PENDING,
            )
        )
    ).scalars().first()
    assert задание is not None
    await justice.lift(session, задание)
    assert await justice.imprisoned(session, ответчик.id) is None
    assert await travel.depart(session, constants, тело, куда) is not None


async def test_заключение_не_дольше_потолка(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    город, _, судья, истец, ответчик, _ = await _суд(session, catalog)
    дело = await justice.sue(session, constants, город, истец, ответчик, "снос")
    сейчас = datetime.now(UTC)
    наказание = await justice.judge(
        session, constants, catalog, судья, дело,
        sanction=justice.PRISON, days=999, now=сейчас,
    )
    потолок = сейчас + timedelta(days=constants[R.JUSTICE_PRISON_MAX])
    assert наказание.until == потолок


async def test_изгнание_по_приговору_снимает_гражданство(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Высылка, а не смерть: изгнанный спокойно живёт в другом городе."""
    город, _, судья, истец, ответчик, _ = await _суд(session, catalog)
    дело = await justice.sue(session, constants, город, истец, ответчик, "измена")
    await justice.judge(
        session, constants, catalog, судья, дело, sanction=justice.EXILE
    )
    assert await town.citizenship(session, ответчик.id) is None


async def test_неисполнимая_санкция_отвергается_вслух(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Приговор без исполнения хуже, чем отказ от приговора (D-166)."""
    город, _, судья, истец, ответчик, _ = await _суд(session, catalog)
    дело = await justice.sue(session, constants, город, истец, ответчик, "спор")
    with pytest.raises(justice.Unenforceable):
        await justice.judge(
            session, constants, catalog, судья, дело, sanction="confiscation"
        )
    assert дело.state is CaseState.OPEN, "дело осталось нерассмотренным"


async def test_оправдание_тоже_приговор(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Висящих дел не бывает: каждое кончается решением."""
    город, _, судья, истец, ответчик, _ = await _суд(session, catalog)
    дело = await justice.sue(session, constants, город, истец, ответчик, "напраслина")
    наказание = await justice.judge(
        session, constants, catalog, судья, дело, verdict="не доказано"
    )
    assert наказание is None
    assert дело.state is CaseState.DISMISSED
    assert дело.verdict == "не доказано"
    assert not await justice.active(session, ответчик.id)


async def test_дважды_одно_дело_не_судят(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    город, _, судья, истец, ответчик, _ = await _суд(session, catalog)
    дело = await justice.sue(session, constants, город, истец, ответчик, "спор")
    await justice.judge(session, constants, catalog, судья, дело)
    with pytest.raises(justice.JusticeError):
        await justice.judge(
            session, constants, catalog, судья, дело, sanction=justice.FINE, amount=1
        )


async def test_право_суда_отдаётся_отдельно(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """`justice` — точечное право: судья не обязан быть правителем (D-155)."""
    город, ядро, судья, истец, ответчик, _ = await _суд(session, catalog)
    мировой, _ = await _житель(session, ядро, город, "Мировой")
    #: Назначение присутственно (D-155): судья идёт в администрацию.
    двор = await world.node_container(session, ядро)
    await world.grant_item(session, двор, town.HALL, quality=60, origin="тест")
    тело_судьи = await _тело(session, судья)
    await town.appoint(
        session, судья, город, мировой, title="Мировой судья",
        powers=[Power.JUSTICE.value], body=тело_судьи,
    )
    дело = await justice.sue(session, constants, город, истец, ответчик, "спор")
    await justice.judge(
        session, constants, catalog, мировой, дело, sanction=justice.FINE, amount=10
    )
    assert дело.state is CaseState.JUDGED
