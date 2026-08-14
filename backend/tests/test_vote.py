"""Голосование граждан: срок, ценз, кворум, порог (D-036, D-161).

Проверяется то, ради чего процедура введена:

* город, отдавший утверждение гражданам, не меняет закон росчерком правителя;
* голос есть только у граждан, и только у отвечающих цензу устава;
* условия сняты при открытии: правитель не поднимает порог на ходу;
* итог применяется **сам**, заданием журнала, и без чьего-либо участия.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Catalog, Constants
from src.constants import registry as R
from src.engine import city as town
from src.engine import ledger, vote, world
from src.models.city import Citizen
from src.models.ledger import AccountKind, PostingReason
from src.models.vote import Vote, VoteKind, VoteState
from src.models.world import Layer
from src.units import money

ЗАКОН, ЗНАЧЕНИЕ = "tax_trade", "7"


async def _город(session: AsyncSession, catalog: Catalog, **устав):
    """Город, отдавший законы гражданам, и его правитель."""
    метка = uuid.uuid4().hex[:8]
    планета = await world.create_node(
        session, f"terra.{метка}", "Терра", area_m2=1, layer=Layer.SPACE
    )
    представитель = await world.create_node(
        session, f"terra.city.{метка}", "Вече", area_m2=1,
        layer=Layer.PLANET, parent=планета,
    )
    ядро = await world.create_node(
        session, f"terra.city.{метка}.core", "Ядро", area_m2=100,
        parent=представитель, properties={"кольцо": 0},
    )
    город = await town.found(session, catalog, представитель, "Вече")
    ядро.owner_city_id = город.id
    двор = await world.node_container(session, ядро)
    await world.grant_item(session, двор, town.HALL, quality=65, origin="тест")
    город.charter = {**город.charter, vote.APPROVAL: vote.BY_CITIZENS, **устав}
    await session.flush()

    правитель, тело = await _житель(session, ядро, город, "Правитель")
    await town.install_founder(session, город, правитель)
    return город, ядро, правитель, тело


async def _житель(session: AsyncSession, узел, город, имя: str, *, гражданин=True):
    identity = await world.create_identity(session, f"{имя}-{uuid.uuid4().hex[:6]}")
    body = await world.print_body(session, identity, узел)
    if гражданин:
        session.add(Citizen(identity_id=identity.id, city_id=город.id))
        await session.flush()
    return identity, body


async def _созвать(session, constants, catalog, город, правитель, тело) -> Vote:
    await town.set_law(
        session, constants, catalog, правитель, город, ЗАКОН, ЗНАЧЕНИЕ, body=тело
    )
    идут = await vote.open_votes(session, город)
    assert len(идут) == 1
    return идут[0]


# --- созыв ------------------------------------------------------------------


async def test_закон_уходит_на_голосование_а_не_применяется(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """`law_approval: citizens` — правитель созывает, а не решает."""
    город, _, правитель, тело = await _город(session, catalog)
    голосование = await _созвать(session, constants, catalog, город, правитель, тело)

    assert (город.laws or {}).get(ЗАКОН) != ЗНАЧЕНИЕ, "закон ещё не принят"
    assert голосование.subject == {"law": ЗАКОН, "value": ЗНАЧЕНИЕ}
    assert голосование.state is VoteState.OPEN


async def test_срок_голосования_из_вольта(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    город, _, правитель, тело = await _город(session, catalog)
    голосование = await _созвать(session, constants, catalog, город, правитель, тело)
    длится = голосование.closes_at - голосование.opened_at
    assert длится == pytest.approx(
        timedelta(hours=constants[R.VOTE_DURATION]), abs=timedelta(seconds=2)
    )


# --- у кого голос -----------------------------------------------------------


async def test_голосуют_граждане(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Без этого демократия — соревнование мультиаккаунтов (01-government-forms)."""
    город, ядро, правитель, тело = await _город(session, catalog)
    голосование = await _созвать(session, constants, catalog, город, правитель, тело)
    гость, _ = await _житель(session, ядро, город, "Гость", гражданин=False)

    with pytest.raises(vote.NoVoice):
        await vote.cast(session, город, гость, голосование, True)
    await vote.cast(session, город, правитель, голосование, True)
    assert await vote.standing(session, голосование) == (1, 0)


async def test_ценз_по_сроку_проживания(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Вчерашний гражданин не решает судьбу города, если устав так сказал."""
    город, ядро, правитель, тело = await _город(
        session, catalog, **{vote.QUALIFICATION: vote.RESIDENCE}
    )
    город.charter_params = {vote.QUALIFICATION: 30}
    await session.flush()

    новичок, _ = await _житель(session, ядро, город, "Новичок")
    assert not await vote.may_vote(session, город, новичок.id)

    старожил = await town.citizenship(session, правитель.id)
    старожил.since = datetime.now(UTC) - timedelta(days=60)
    await session.flush()
    assert await vote.may_vote(session, город, правитель.id)


async def test_имущественный_ценз(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    город, ядро, _, _ = await _город(
        session, catalog, **{vote.QUALIFICATION: vote.PROPERTY}
    )
    город.charter_params = {vote.QUALIFICATION: 100}
    await session.flush()

    бедный, _ = await _житель(session, ядро, город, "Бедный")
    богатый, _ = await _житель(session, ядро, город, "Богатый")
    счёт = await ledger.account_for(session, AccountKind.IDENTITY, богатый.id)
    genesis = await ledger.account_for(session, AccountKind.GENESIS, None)
    await ledger.transfer(
        session, PostingReason.GENESIS,
        debit=genesis.id, credit=счёт.id, amount=money(500), memo={},
    )

    assert not await vote.may_vote(session, город, бедный.id)
    assert await vote.may_vote(session, город, богатый.id)


# --- подсчёт ----------------------------------------------------------------


async def test_итог_применяется_сам_по_сроку(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Задание журнала считает и применяет — без чьего-либо участия."""
    город, ядро, правитель, тело = await _город(session, catalog)
    голосование = await _созвать(session, constants, catalog, город, правитель, тело)
    за_, _ = await _житель(session, ядро, город, "Сторонник")
    await vote.cast(session, город, правитель, голосование, True)
    await vote.cast(session, город, за_, голосование, True)

    await _подвести(session, голосование)
    assert голосование.state is VoteState.PASSED
    assert (город.laws or {}).get(ЗАКОН) == ЗНАЧЕНИЕ, "закон принят сам"


async def test_большинства_нет_закон_не_проходит(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    город, ядро, правитель, тело = await _город(session, catalog)
    голосование = await _созвать(session, constants, catalog, город, правитель, тело)
    против1, _ = await _житель(session, ядро, город, "Против")
    против2, _ = await _житель(session, ядро, город, "Тоже против")
    await vote.cast(session, город, правитель, голосование, True)
    await vote.cast(session, город, против1, голосование, False)
    await vote.cast(session, город, против2, голосование, False)

    await _подвести(session, голосование)
    assert голосование.state is VoteState.FAILED
    assert (город.laws or {}).get(ЗАКОН) != ЗНАЧЕНИЕ


async def test_кворум_не_собран(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Меньшинство не решает за город, если устав требует кворума."""
    город, ядро, правитель, тело = await _город(
        session, catalog, **{vote.QUORUM: "share"}
    )
    город.charter_params = {vote.QUORUM: 60}
    await session.flush()
    for номер in range(4):
        await _житель(session, ядро, город, f"Гражданин{номер}")

    голосование = await _созвать(session, constants, catalog, город, правитель, тело)
    assert голосование.electorate == 5
    await vote.cast(session, город, правитель, голосование, True)

    await _подвести(session, голосование)
    assert голосование.state is VoteState.FAILED, "один голос из пяти — не кворум"


async def test_условия_снимаются_при_открытии(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Правитель не поднимает порог, увидев, что проигрывает (D-161)."""
    город, ядро, правитель, тело = await _город(session, catalog)
    голосование = await _созвать(session, constants, catalog, город, правитель, тело)
    assert голосование.threshold == vote.SIMPLE

    город.charter = {**город.charter, vote.THRESHOLD: vote.UNANIMOUS}
    await session.flush()

    против, _ = await _житель(session, ядро, город, "Против")
    await vote.cast(session, город, правитель, голосование, True)
    await vote.cast(session, город, против, голосование, False)
    сторонник, _ = await _житель(session, ядро, город, "Сторонник")
    await vote.cast(session, город, сторонник, голосование, True)

    await _подвести(session, голосование)
    assert голосование.state is VoteState.PASSED, "судят по правилам созыва"


async def test_передумать_до_срока_можно(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    город, _, правитель, тело = await _город(session, catalog)
    голосование = await _созвать(session, constants, catalog, город, правитель, тело)
    await vote.cast(session, город, правитель, голосование, True)
    await vote.cast(session, город, правитель, голосование, False)
    assert await vote.standing(session, голосование) == (0, 1), "голос один"


async def test_опоздавший_голос_не_принимается(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    город, _, правитель, тело = await _город(session, catalog)
    голосование = await _созвать(session, constants, catalog, город, правитель, тело)
    поздно = голосование.closes_at + timedelta(minutes=1)
    with pytest.raises(vote.Closed):
        await vote.cast(session, город, правитель, голосование, True, now=поздно)


async def _подвести(session: AsyncSession, голосование: Vote) -> None:
    """Прокрутить подсчёт — так же, как это сделал бы воркер."""
    from sqlalchemy import select

    from src.models.job import Job, JobKind, JobState

    задание = (
        await session.execute(
            select(Job).where(
                Job.kind == JobKind.VOTE_CLOSE.value, Job.state == JobState.PENDING
            )
        )
    ).scalars().first()
    assert задание is not None
    await vote.close(session, задание)
    задание.state = JobState.DONE
    await session.flush()


# --- выборы и отзыв (D-162) -------------------------------------------------


async def _выборный(session: AsyncSession, catalog: Catalog, **устав):
    """Город, отдавший власть выборам."""
    return await _город(
        session,
        catalog,
        **{vote.SELECTION: vote.ELECTED, vote.RECALL_RULE: vote.RECALL_BY_CITIZENS},
        **устав,
    )


async def test_избранный_получает_власть(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Должность переходит по подсчёту, а не по назначению (D-162)."""
    город, ядро, правитель, _ = await _выборный(session, catalog)
    соперник, _ = await _житель(session, ядро, город, "Соперник")
    избиратель, _ = await _житель(session, ядро, город, "Избиратель")

    выборы = await vote.open_election(session, constants, город, правитель)
    await vote.nominate(session, город, правитель, выборы)
    await vote.nominate(session, город, соперник, выборы)
    await vote.choose(session, город, избиратель, выборы, соперник)
    await vote.choose(session, город, соперник, выборы, соперник)
    await vote.choose(session, город, правитель, выборы, правитель)

    await _подвести(session, выборы)
    новый = await town.ruler(session, город)
    assert новый is not None and новый.identity_id == соперник.id
    assert await town.may(session, соперник.id, город, "laws"), (
        "избранный получает набор прежнего правителя"
    )
    assert not await town.may(session, правитель.id, город, "laws"), (
        "прежняя должность сложена"
    )


async def test_выдвигаются_только_граждане(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    город, ядро, правитель, _ = await _выборный(session, catalog)
    выборы = await vote.open_election(session, constants, город, правитель)
    гость, _ = await _житель(session, ядро, город, "Гость", гражданин=False)
    with pytest.raises(vote.NotCandidate):
        await vote.nominate(session, город, гость, выборы)


async def test_за_невыдвинувшегося_не_голосуют(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    город, ядро, правитель, _ = await _выборный(session, catalog)
    выборы = await vote.open_election(session, constants, город, правитель)
    посторонний, _ = await _житель(session, ядро, город, "Посторонний")
    with pytest.raises(vote.NotCandidate):
        await vote.choose(session, город, правитель, выборы, посторонний)


async def test_ничья_власть_не_передаёт(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Жребий — отдельный вариант устава, и выдумывать его нельзя (D-065)."""
    город, ядро, правитель, _ = await _выборный(session, catalog)
    соперник, _ = await _житель(session, ядро, город, "Соперник")
    выборы = await vote.open_election(session, constants, город, правитель)
    await vote.nominate(session, город, правитель, выборы)
    await vote.nominate(session, город, соперник, выборы)
    await vote.choose(session, город, правитель, выборы, правитель)
    await vote.choose(session, город, соперник, выборы, соперник)

    await _подвести(session, выборы)
    assert выборы.state is VoteState.FAILED
    остался = await town.ruler(session, город)
    assert остался is not None and остался.identity_id == правитель.id


async def test_город_без_выборного_устава_их_не_созывает(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    город, _, правитель, _ = await _город(session, catalog)
    with pytest.raises(vote.NotElective):
        await vote.open_election(session, constants, город, правитель)


async def test_отзыв_снимает_правителя_и_созывает_выборы(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Город не остаётся без власти дольше одного голосования (D-162)."""
    город, ядро, правитель, _ = await _выборный(session, catalog)
    недовольный, _ = await _житель(session, ядро, город, "Недовольный")
    ещё_один, _ = await _житель(session, ядро, город, "Тоже недовольный")

    отзыв = await vote.open_recall(session, constants, город, недовольный)
    await vote.cast(session, город, недовольный, отзыв, True)
    await vote.cast(session, город, ещё_один, отзыв, True)
    await vote.cast(session, город, правитель, отзыв, False)

    await _подвести(session, отзыв)
    assert отзыв.state is VoteState.PASSED
    assert await town.ruler(session, город) is None, "должность снята"
    идут = await vote.open_votes(session, город)
    assert [г.kind for г in идут] == [VoteKind.ELECTION], "выборы созваны сразу"


async def test_отзыв_запрещённый_уставом_не_созывается(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    город, ядро, правитель, _ = await _город(session, catalog)
    кто_то, _ = await _житель(session, ядро, город, "Кто-то")
    with pytest.raises(vote.NotElective):
        await vote.open_recall(session, constants, город, кто_то)


# --- срок полномочий и правка устава (D-163) --------------------------------


async def test_срок_полномочий_снимает_должность_сам(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """«Избирается на тридцать суток» не должно значить «пока сам не вспомнит»."""
    from sqlalchemy import select as _select

    from src.models.job import Job, JobKind, JobState

    город, ядро, правитель, _ = await _выборный(session, catalog)
    город.charter = {**город.charter, vote.TERM: vote.FIXED_TERM}
    город.charter_params = {vote.TERM: 30}
    await session.flush()

    сменщик, _ = await _житель(session, ядро, город, "Сменщик")
    выборы = await vote.open_election(session, constants, город, правитель)
    await vote.nominate(session, город, сменщик, выборы)
    await vote.choose(session, город, правитель, выборы, сменщик)
    await _подвести(session, выборы)

    срок = (
        await session.execute(
            _select(Job).where(
                Job.kind == JobKind.RULER_TERM.value, Job.state == JobState.PENDING
            )
        )
    ).scalars().first()
    assert срок is not None, "срок поставлен при вступлении в должность"

    await town.term_ended(session, срок)
    assert await town.ruler(session, город) is None, "должность снята по сроку"
    идут = await vote.open_votes(session, город)
    assert VoteKind.ELECTION in [г.kind for г in идут], "выборный город идёт на выборы"


async def test_устав_правится_голосованием(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Правитель не запрещает себе отзыв там, где устав отдан гражданам."""
    город, ядро, правитель, тело = await _город(session, catalog)
    город.charter = {**город.charter, vote.AMENDMENT: "two_thirds"}
    await session.flush()
    сторонник, _ = await _житель(session, ядро, город, "Сторонник")

    #: Меняем отзыв с умолчания «нельзя» на «голосованием граждан»: правитель,
    #: которому устав отдан, такого себе не сделал бы.
    await town.set_charter(
        session, catalog, правитель, город, vote.RECALL_RULE,
        vote.RECALL_BY_CITIZENS, body=тело,
    )
    assert город.charter[vote.RECALL_RULE] != vote.RECALL_BY_CITIZENS, (
        "правка ушла на голосование, а не применилась"
    )

    голосование = (await vote.open_votes(session, город))[0]
    assert голосование.kind is VoteKind.CHARTER
    assert голосование.threshold == vote.TWO_THIRDS, "у конституции свой порог"

    await vote.cast(session, город, правитель, голосование, True)
    await vote.cast(session, город, сторонник, голосование, True)
    await _подвести(session, голосование)
    assert город.charter[vote.RECALL_RULE] == vote.RECALL_BY_CITIZENS, (
        "принятое применилось само"
    )


async def test_двух_третей_не_набралось(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    город, ядро, правитель, тело = await _город(session, catalog)
    город.charter = {**город.charter, vote.AMENDMENT: "two_thirds"}
    await session.flush()
    было = город.charter[vote.RECALL_RULE]
    против, _ = await _житель(session, ядро, город, "Против")

    await town.set_charter(
        session, catalog, правитель, город, vote.RECALL_RULE,
        vote.RECALL_BY_CITIZENS, body=тело,
    )
    голосование = (await vote.open_votes(session, город))[0]
    await vote.cast(session, город, правитель, голосование, True)
    await vote.cast(session, город, против, голосование, False)

    await _подвести(session, голосование)
    assert голосование.state is VoteState.FAILED
    assert город.charter[vote.RECALL_RULE] == было


async def test_запечатанный_устав_не_меняется(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """`never` исполняется буквально: изнутри устав не открыть (D-163)."""
    город, _, правитель, тело = await _город(session, catalog)
    город.charter = {**город.charter, vote.AMENDMENT: vote.NEVER}
    await session.flush()

    with pytest.raises(vote.Sealed):
        await town.set_charter(
            session, catalog, правитель, город, vote.RECALL_RULE,
            vote.RECALL_BY_CITIZENS, body=тело,
        )


# --- совет (D-164) ----------------------------------------------------------


async def _с_советом(session: AsyncSession, catalog: Catalog, *, мест: int, как: str):
    город, ядро, правитель, тело = await _город(session, catalog)
    город.charter = {**город.charter, vote.COUNCIL: как}
    город.charter_params = {vote.COUNCIL: мест}
    await session.flush()
    return город, ядро, правитель, тело


async def test_правитель_сажает_в_совет_и_мест_не_больше_чем_в_уставе(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    город, ядро, правитель, _ = await _с_советом(
        session, catalog, мест=1, как=vote.APPOINTED_COUNCIL
    )
    первый, _ = await _житель(session, ядро, город, "Советник")
    второй, _ = await _житель(session, ядро, город, "Второй")

    await vote.appoint_to_council(session, город, правитель, первый)
    assert await vote.in_council(session, город, первый.id)

    with pytest.raises(vote.NoCouncil):
        await vote.appoint_to_council(session, город, правитель, второй)


async def test_выборный_совет_набирает_столько_сколько_мест(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    город, ядро, правитель, _ = await _с_советом(
        session, catalog, мест=2, как=vote.ELECTED_COUNCIL
    )
    а, _ = await _житель(session, ядро, город, "А")
    б, _ = await _житель(session, ядро, город, "Б")
    в, _ = await _житель(session, ядро, город, "В")

    выборы = await vote.open_council_election(session, constants, город, правитель)
    for кто in (а, б, в):
        await vote.nominate(session, город, кто, выборы)
    await vote.choose(session, город, правитель, выборы, а)
    await vote.choose(session, город, а, выборы, а)
    await vote.choose(session, город, б, выборы, б)
    await vote.choose(session, город, в, выборы, в)

    await _подвести(session, выборы)
    места = {м.identity_id for м in await vote.council_of(session, город)}
    assert а.id in места, "больше всех голосов — место"
    assert len(места) == 2, "мест ровно столько, сколько назначил устав"


async def test_совет_утверждает_закон_вместо_граждан(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Та же машина, другой круг голосующих (D-164)."""
    город, ядро, правитель, тело = await _с_советом(
        session, catalog, мест=2, как=vote.APPOINTED_COUNCIL
    )
    город.charter = {**город.charter, vote.APPROVAL: vote.BY_COUNCIL}
    await session.flush()
    советник, _ = await _житель(session, ядро, город, "Советник")
    посторонний, _ = await _житель(session, ядро, город, "Горожанин")
    await vote.appoint_to_council(session, город, правитель, советник)

    голосование = await _созвать(session, constants, catalog, город, правитель, тело)
    assert голосование.voters == vote.COUNCIL_VOTERS
    assert голосование.electorate == 1, "кворум считается от совета, а не от города"

    with pytest.raises(vote.NoVoice):
        await vote.cast(session, город, посторонний, голосование, True)
    await vote.cast(session, город, советник, голосование, True)

    await _подвести(session, голосование)
    assert (город.laws or {}).get(ЗАКОН) == ЗНАЧЕНИЕ


async def test_член_совета_вносит_закон_без_права_laws(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """«Вносит совет» — значит законодателей столько, сколько мест."""
    город, ядро, правитель, _ = await _с_советом(
        session, catalog, мест=2, как=vote.APPOINTED_COUNCIL
    )
    город.charter = {**город.charter, vote.LAWMAKER: vote.BY_COUNCIL}
    await session.flush()
    советник, тело_советника = await _житель(session, ядро, город, "Советник")

    #: Без места в совете прав нет никаких.
    with pytest.raises(town.NotAllowed):
        await town.set_law(
            session, constants, catalog, советник, город, ЗАКОН, ЗНАЧЕНИЕ,
            body=тело_советника,
        )

    await vote.appoint_to_council(session, город, правитель, советник)
    await town.set_law(
        session, constants, catalog, советник, город, ЗАКОН, ЗНАЧЕНИЕ,
        body=тело_советника,
    )
    assert await vote.open_votes(session, город), "внесённое ушло на голосование"


async def test_пустая_палата_законы_не_запирает(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Совет из нуля мест равен отсутствию совета (D-164).

    Устав отдал утверждение палате, а палаты нет: закон применяет тот, кто его
    внёс. Иначе город, ответивший «утверждает совет» и не собравший его,
    остался бы без законодательства навсегда.
    """
    город, _, правитель, тело = await _с_советом(
        session, catalog, мест=0, как=vote.ELECTED_COUNCIL
    )
    город.charter = {**город.charter, vote.APPROVAL: vote.BY_COUNCIL}
    await session.flush()

    await town.set_law(
        session, constants, catalog, правитель, город, ЗАКОН, ЗНАЧЕНИЕ, body=тело
    )
    assert not await vote.open_votes(session, город), "голосовать некому"
    assert (город.laws or {}).get(ЗАКОН) == ЗНАЧЕНИЕ


async def test_города_без_совета_его_не_собирают(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    город, ядро, правитель, _ = await _город(session, catalog)
    кто_то, _ = await _житель(session, ядро, город, "Кто-то")
    with pytest.raises(vote.NoCouncil):
        await vote.appoint_to_council(session, город, правитель, кто_то)
    with pytest.raises(vote.NoCouncil):
        await vote.open_council_election(session, constants, город, правитель)


# --- совет выбирает и отзывает правителя (D-165) ----------------------------


async def test_совет_выбирает_правителя(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Парламентская республика отличается от прямой демократии кругом."""
    город, ядро, правитель, _ = await _с_советом(
        session, catalog, мест=2, как=vote.APPOINTED_COUNCIL
    )
    город.charter = {**город.charter, vote.SELECTION: vote.ELECTED_BY_COUNCIL}
    await session.flush()
    советник, _ = await _житель(session, ядро, город, "Советник")
    горожанин, _ = await _житель(session, ядро, город, "Горожанин")
    await vote.appoint_to_council(session, город, правитель, советник)

    выборы = await vote.open_election(session, constants, город, правитель)
    assert выборы.voters == vote.COUNCIL_VOTERS
    assert выборы.electorate == 1, "кворум считается от палаты"

    with pytest.raises(vote.NotCandidate):
        await vote.nominate(session, город, горожанин, выборы)
    await vote.nominate(session, город, советник, выборы)
    with pytest.raises(vote.NoVoice):
        await vote.choose(session, город, горожанин, выборы, советник)
    await vote.choose(session, город, советник, выборы, советник)

    await _подвести(session, выборы)
    новый = await town.ruler(session, город)
    assert новый is not None and новый.identity_id == советник.id


async def test_совет_отзывает_правителя(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    город, ядро, правитель, _ = await _с_советом(
        session, catalog, мест=1, как=vote.APPOINTED_COUNCIL
    )
    город.charter = {**город.charter, vote.RECALL_RULE: vote.RECALL_BY_COUNCIL}
    await session.flush()
    советник, _ = await _житель(session, ядро, город, "Советник")
    await vote.appoint_to_council(session, город, правитель, советник)

    отзыв = await vote.open_recall(session, constants, город, советник)
    assert отзыв.voters == vote.COUNCIL_VOTERS
    await vote.cast(session, город, советник, отзыв, True)

    await _подвести(session, отзыв)
    assert отзыв.state is VoteState.PASSED
    assert await town.ruler(session, город) is None


async def test_пустая_палата_не_запирает_власть(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Устав, неисполнимый буквально, исполняется по смыслу (D-165)."""
    город, _, правитель, _ = await _с_советом(
        session, catalog, мест=0, как=vote.ELECTED_COUNCIL
    )
    город.charter = {**город.charter, vote.SELECTION: vote.ELECTED_BY_COUNCIL}
    await session.flush()

    выборы = await vote.open_election(session, constants, город, правитель)
    assert выборы.voters == vote.CITIZENS, "выбирает весь город, раз палаты нет"
