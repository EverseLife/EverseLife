"""Голосование граждан (D-036, D-130, D-161).

Устав спрашивает, кто утверждает закон, с каким порогом, при каком кворуме и у
кого вообще есть голос. До этого движок исполнял одну ветку — «правитель
единолично», ту, что стоит умолчанием: города, выбравшего «голосованием
граждан», просто не могло существовать.

## Как устроено

Тот, кому устав дал вносить законы, не меняет закон, а **открывает
голосование** на `vote.duration` часов. По сроку задание журнала считает итог и
применяет его само — без чьего-либо участия, в том числе если все разошлись.

| Условие | Вопрос устава |
|---|---|
| у кого голос | `vote_qualification`: все граждане · по сроку проживания · по имуществу |
| кворум | `quorum`: не требуется либо доля имеющих право |
| порог | `law_threshold`: простое большинство · две трети · единогласно |

**Условия снимаются при открытии.** Устав, изменённый посреди голосования, не
переписывает правила уже идущего: иначе правитель, видя, что проигрывает,
поднимал бы порог на ходу. По той же причине в записи хранится число имевших
право голоса на момент созыва — кворум считается от него.

**Голос подаётся удалённо.** Это Сеть, а не присутственное действие: гражданин
голосует из дороги и из шахты. Присутствие нужно, чтобы **править** (D-155), а
голос — это не управление, это участие.

## Чего здесь нет

* **Тайного голосования.** Вольт называет видимость параметром устава, но
  вопроса под неё в `laws.json` нет, а заводить его в коде запрещено (D-065);
* **Ценза по вкладу в казну.** Учёта личного вклада нет ни в одной таблице, и
  вывести его из проводок нельзя: их основание не различает «пожертвовал» и
  «заплатил налог»;
* **Выборов, отзыва и правки устава.** Они лягут на эту же машину — у них те
  же ценз, кворум и порог, отличается только предмет.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Catalog, Constants
from src.constants import registry as R
from src.engine import events
from src.engine.jobs import enqueue, handler
from src.models.city import City, CouncilSeat, Power
from src.models.event import EventKind
from src.models.identity import Identity
from src.models.job import Job, JobKind
from src.models.vote import Ballot, Vote, VoteKind, VoteState
from src.units import PERCENT

#: Вопросы устава, из которых собирается процедура.
APPROVAL = "law_approval"
THRESHOLD = "law_threshold"
QUORUM = "quorum"
QUALIFICATION = "vote_qualification"

#: Варианты, которые движок исполняет. Остальные названы в шапке.
BY_CITIZENS = "citizens"
SIMPLE, TWO_THIRDS, UNANIMOUS = "simple", "two_thirds", "unanimous"
ALL, RESIDENCE, PROPERTY = "all", "residence", "property"


class VoteError(Exception):
    pass


class NoVoice(VoteError):
    """Голоса нет: гражданства либо ценза не хватает. Ценз — дело устава."""


class Closed(VoteError):
    """Голосование закрыто. Опоздавший голос итога не меняет."""


def answer(city: City, question: str, default: str) -> str:
    return str((city.charter or {}).get(question) or default)


def param(city: City, question: str) -> float:
    """Числовой параметр варианта устава: суток проживания, ТК имущества, %."""
    try:
        return float((city.charter_params or {}).get(question) or 0)
    except (TypeError, ValueError):  # pragma: no cover — параметр правит человек
        return 0.0


def by_citizens(city: City) -> bool:
    """Утверждает ли законы голосование граждан, а не правитель единолично."""
    return answer(city, APPROVAL, "ruler") == BY_CITIZENS


async def may_vote(
    session: AsyncSession, city: City, identity_id: uuid.UUID, *, now: datetime | None = None
) -> bool:
    """Есть ли у этого человека голос в этом городе (`vote_qualification`).

    Голос есть только у граждан (D-160): без этого демократия превращается в
    соревнование мультиаккаунтов, и весь политический слой обесценивается.
    """
    from src.engine import city as town

    moment = now or datetime.now(UTC)
    запись = await town.citizenship(session, identity_id)
    if запись is None or запись.city_id != city.id:
        return False

    ценз = answer(city, QUALIFICATION, ALL)
    if ценз == RESIDENCE:
        срок = timedelta(days=param(city, QUALIFICATION))
        return запись.since + срок <= moment
    if ценз == PROPERTY:
        from src.engine import ledger
        from src.models.ledger import AccountKind
        from src.units import money

        счёт = await ledger.account_for(session, AccountKind.IDENTITY, identity_id)
        return await ledger.balance(session, счёт.id) >= money(
            param(city, QUALIFICATION)
        )
    #: Ценз по вкладу в казну не исполняется: учёта вклада нет (D-161). Такой
    #: город голосует всеми гражданами, а не запирается наглухо.
    return True


async def electorate(
    session: AsyncSession,
    city: City,
    *,
    now: datetime | None = None,
    voters: str = "citizens",
) -> list[uuid.UUID]:
    """Кто имеет голос сейчас. От их числа считается кворум.

    Круг бывает двух видов: все граждане по цензу либо члены совета (D-164).
    """
    from src.engine import city as town

    if voters == COUNCIL_VOTERS:
        return [место.identity_id for место in await council_of(session, city)]

    имеют: list[uuid.UUID] = []
    for запись in await town.citizens_of(session, city):
        if await may_vote(session, city, запись.identity_id, now=now):
            имеют.append(запись.identity_id)
    return имеют


async def open_law(
    session: AsyncSession,
    constants: Constants,
    city: City,
    by: Identity,
    law_id: str,
    value,
    *,
    now: datetime | None = None,
) -> Vote:
    """Созвать голосование по код-закону. Итог применится сам, по сроку."""
    return await _open(
        session,
        constants,
        city,
        by,
        kind=VoteKind.LAW,
        subject={"law": law_id, "value": value},
        now=now,
    )


async def _open(
    session: AsyncSession,
    constants: Constants,
    city: City,
    by: Identity | None,
    *,
    kind: VoteKind,
    subject: dict,
    now: datetime | None = None,
) -> Vote:
    """Созыв: условия снимаются здесь и дальше не меняются (D-161)."""
    moment = now or datetime.now(UTC)
    круг = voters_for(city, kind)
    имеющие = await electorate(session, city, now=moment, voters=круг)
    закрытие = moment + timedelta(hours=constants[R.VOTE_DURATION])

    голосование = Vote(
        city_id=city.id,
        kind=kind,
        subject=subject,
        opened_by_identity_id=None if by is None else by.id,
        threshold=answer(city, THRESHOLD, SIMPLE),
        quorum_share=Decimal(
            str(param(city, QUORUM) if answer(city, QUORUM, "none") != "none" else 0)
        ),
        electorate=len(имеющие),
        voters=voters_for(city, kind),
        closes_at=закрытие,
    )
    session.add(голосование)
    await session.flush()

    event = await events.record(
        session,
        EventKind.VOTE_OPENED,
        actor_identity_id=None if by is None else by.id,
        node_id=city.node_id,
        city_id=str(city.id),
        vote_id=str(голосование.id),
        kind_of_vote=kind.value,
        subject=subject,
        electorate=голосование.electorate,
        closes_at=закрытие.isoformat(),
    )
    await enqueue(
        session,
        JobKind.VOTE_CLOSE,
        закрытие,
        payload={"vote": str(голосование.id)},
        dedup_key=f"vote.close:{голосование.id}",
        cause_event_id=event.id,
    )
    return голосование


async def cast(
    session: AsyncSession,
    city: City,
    identity: Identity,
    vote: Vote,
    yes: bool,
    *,
    now: datetime | None = None,
) -> Ballot:
    """Проголосовать. Удалённо: голос — это участие, а не управление."""
    moment = now or datetime.now(UTC)
    if vote.state is not VoteState.OPEN or vote.closes_at <= moment:
        raise Closed("голосование закрыто: опоздавший голос итога не меняет")
    if not await may_vote_in(session, city, identity.id, vote, now=moment):
        raise NoVoice(
            "голоса нет: в этом голосовании решают "
            + ("члены совета" if vote.voters == COUNCIL_VOTERS else "граждане")
        )

    бюллетень = (
        await session.execute(
            select(Ballot).where(
                Ballot.vote_id == vote.id, Ballot.identity_id == identity.id
            )
        )
    ).scalar_one_or_none()
    if бюллетень is None:
        бюллетень = Ballot(vote_id=vote.id, identity_id=identity.id, yes=yes)
        session.add(бюллетень)
    else:
        #: Передумать до срока можно: голосование идёт сутки, и запирать
        #: человека в первом решении незачем.
        бюллетень.yes = yes
    await session.flush()
    await events.record(
        session,
        EventKind.VOTE_CAST,
        actor_identity_id=identity.id,
        city_id=str(city.id),
        vote_id=str(vote.id),
        yes=yes,
    )
    return бюллетень


async def may_vote_in(
    session: AsyncSession,
    city: City,
    identity_id: uuid.UUID,
    vote: Vote,
    *,
    now: datetime | None = None,
) -> bool:
    """Есть ли голос **в этом** голосовании: круг снят при созыве (D-164)."""
    if vote.voters == COUNCIL_VOTERS:
        return await in_council(session, city, identity_id)
    return await may_vote(session, city, identity_id, now=now)


async def standing(session: AsyncSession, vote: Vote) -> tuple[int, int]:
    """Сколько за и сколько против прямо сейчас. Голосование открытое."""
    бюллетени = (
        await session.execute(select(Ballot).where(Ballot.vote_id == vote.id))
    ).scalars().all()
    за = sum(1 for б in бюллетени if б.yes)
    return за, len(бюллетени) - за


def passes(
    constants: Constants, vote: Vote, за: int, против: int
) -> tuple[bool, str]:
    """Прошло ли. Возвращает решение и причину — её видит игрок, а не только лог.

    Доли, стоящие за словами устава, лежат в `vote.thresholds`: «две трети» —
    это число, и числу место в вольте (D-065). Простое большинство берётся
    **строго больше** половины, прочие пороги — не меньше своей доли: иначе
    ровное деление голосов проходило бы как большинство.
    """
    подано = за + против
    нужен_кворум = float(vote.quorum_share) / PERCENT * vote.electorate
    if подано < нужен_кворум:
        return False, "кворум не собран"
    if подано == 0:
        return False, "не проголосовал никто"

    доли = constants[R.VOTE_THRESHOLDS]
    доля = доли.get(vote.threshold, доли.get(SIMPLE, 0))
    нужно = подано * доля
    хватило = за > нужно if vote.threshold == SIMPLE else за >= нужно
    названия = {
        SIMPLE: ("большинство за", "большинства нет"),
        TWO_THIRDS: ("две трети собраны", "двух третей нет"),
        UNANIMOUS: ("единогласно", "не единогласно"),
    }
    прошло, не_прошло = названия.get(vote.threshold, названия[SIMPLE])
    return хватило, (прошло if хватило else не_прошло)


@handler(JobKind.VOTE_CLOSE)
async def close(session: AsyncSession, job: Job) -> None:
    """Срок вышел: считаем итог и применяем его сами (D-161)."""
    from src.engine import city as town

    голосование = await session.get(Vote, uuid.UUID(job.payload["vote"]))
    if голосование is None or голосование.state is not VoteState.OPEN:
        #: Повтор задания после сбоя вторым решением не станет.
        return

    from src.constants import current

    город = await town.by_id(session, голосование.city_id)
    за, против = await standing(session, голосование)

    if голосование.kind is VoteKind.ELECTION:
        почему = await _finish_election(session, голосование, город)
        прошло = почему.startswith("избран")
    elif голосование.kind is VoteKind.COUNCIL:
        почему = await _finish_council(session, голосование, город)
        прошло = почему.startswith("избрано")
    else:
        прошло, почему = passes(current(), голосование, за, против)
        if прошло and город is not None and голосование.kind is VoteKind.LAW:
            закон = str(голосование.subject.get("law"))
            город.laws = {
                **(город.laws or {}),
                закон: голосование.subject.get("value"),
            }
        if голосование.kind is VoteKind.RECALL and город is not None:
            await _finish_recall(session, голосование, город, прошло)
        if прошло and голосование.kind is VoteKind.CHARTER and город is not None:
            await _finish_charter(session, голосование, город)

    голосование.state = VoteState.PASSED if прошло else VoteState.FAILED
    голосование.closed_at = job.run_at
    await session.flush()

    await events.record(
        session,
        EventKind.VOTE_CLOSED,
        node_id=None if город is None else город.node_id,
        city_id=str(голосование.city_id),
        vote_id=str(голосование.id),
        passed=прошло,
        why=почему,
        yes=за,
        no=против,
        electorate=голосование.electorate,
    )


async def open_votes(session: AsyncSession, city: City) -> list[Vote]:
    return list(
        (
            await session.execute(
                select(Vote).where(
                    Vote.city_id == city.id, Vote.state == VoteState.OPEN
                )
            )
        ).scalars().all()
    )


async def view(
    session: AsyncSession, catalog: Catalog, city: City, identity_id: uuid.UUID
) -> list[dict]:
    """Идущие голосования глазами клиента: предмет, сроки и свой голос."""
    итог: list[dict] = []
    for голосование in await open_votes(session, city):
        за, против = await standing(session, голосование)
        мой = (
            await session.execute(
                select(Ballot).where(
                    Ballot.vote_id == голосование.id,
                    Ballot.identity_id == identity_id,
                )
            )
        ).scalar_one_or_none()
        #: У выборов предмет — человек: клиенту нужны имена, а не ключи.
        кандидаты = []
        if голосование.kind in (VoteKind.ELECTION, VoteKind.COUNCIL):
            счёт = await tally(session, голосование)
            for сырой in голосование.subject.get("candidates") or []:
                кто = await session.get(Identity, uuid.UUID(сырой))
                кандидаты.append(
                    {
                        "id": сырой,
                        "name": None if кто is None else кто.name,
                        "votes": счёт.get(сырой, 0),
                    }
                )
        итог.append(
            {
                "id": str(голосование.id),
                "kind": голосование.kind.value,
                "law": голосование.subject.get("law"),
                "value": голосование.subject.get("value"),
                "candidates": кандидаты,
                "choice": (
                    None
                    if мой is None or мой.choice_identity_id is None
                    else str(мой.choice_identity_id)
                ),
                "closes_at": голосование.closes_at.isoformat(),
                "threshold": голосование.threshold,
                "quorum": float(голосование.quorum_share),
                "electorate": голосование.electorate,
                "yes": за,
                "no": против,
                "mine": None if мой is None else мой.yes,
                "voters": голосование.voters,
                "may_vote": await may_vote_in(
                    session, city, identity_id, голосование
                ),
            }
        )
    return итог


# --- выборы и отзыв (D-162) ---------------------------------------------------

#: Вопросы устава про смену власти.
SELECTION = "ruler_selection"
TERM = "ruler_term"
RECALL_RULE = "ruler_recall"

#: Варианты, которые движок исполняет.
ELECTED = "elected_citizens"
#: Правителя выбирает совет, а не весь город (D-165).
ELECTED_BY_COUNCIL = "elected_council"
RECALL_BY_CITIZENS = "by_citizens"
RECALL_BY_COUNCIL = "by_council"
FIXED_TERM = "fixed"


class NotElective(VoteError):
    """Устав не отдал власть выборам: сменяемость — тоже решение города."""


class NotCandidate(VoteError):
    """Выдвигаются граждане, и только пока идут выборы."""


def elects_ruler(city: City) -> bool:
    """Выбирается ли правитель вообще — всем городом либо советом (D-165)."""
    return answer(city, SELECTION, "founder") in (ELECTED, ELECTED_BY_COUNCIL)


def recallable(city: City) -> bool:
    return answer(city, RECALL_RULE, "never") in (
        RECALL_BY_CITIZENS,
        RECALL_BY_COUNCIL,
    )


async def open_election(
    session: AsyncSession,
    constants: Constants,
    city: City,
    by: Identity | None = None,
    *,
    now: datetime | None = None,
) -> Vote:
    """Созвать выборы правителя. Кандидаты выдвигаются, пока идёт голосование."""
    if not elects_ruler(city):
        raise NotElective(
            "устав города не отдал власть выборам: правитель определяется иначе"
        )
    return await _open(
        session,
        constants,
        city,
        by,
        kind=VoteKind.ELECTION,
        subject={"candidates": []},
        now=now,
    )


async def open_recall(
    session: AsyncSession,
    constants: Constants,
    city: City,
    by: Identity,
    *,
    now: datetime | None = None,
) -> Vote:
    """Созвать отзыв правителя. Прошло — должность снимается и идут выборы."""
    from src.engine import city as town

    if not recallable(city):
        raise NotElective("устав города не допускает отзыва правителя")
    правитель = await town.ruler(session, city)
    if правитель is None:
        raise VoteError("отзывать некого: правителя нет")
    return await _open(
        session,
        constants,
        city,
        by,
        kind=VoteKind.RECALL,
        subject={"office": str(правитель.id), "who": str(правитель.identity_id)},
        now=now,
    )


async def nominate(
    session: AsyncSession, city: City, who: Identity, vote: Vote, *, now=None
) -> Vote:
    """Выдвинуться в правители. Сам, а не по чьему-то представлению."""
    moment = now or datetime.now(UTC)
    if vote.kind not in (VoteKind.ELECTION, VoteKind.COUNCIL):
        raise NotCandidate("это не выборы: выдвигаться некуда")
    if vote.state is not VoteState.OPEN:
        raise NotCandidate("выдвигаются, пока идут выборы")
    if vote.closes_at <= moment:
        raise Closed("выборы закрыты")
    if not await may_vote_in(session, city, who.id, vote, now=moment):
        raise NotCandidate(
            "выдвигается тот, у кого есть голос в этих выборах: "
            + ("члены совета" if vote.voters == COUNCIL_VOTERS else "граждане")
        )

    кандидаты = list(vote.subject.get("candidates") or [])
    if str(who.id) in кандидаты:
        return vote
    кандидаты.append(str(who.id))
    vote.subject = {**vote.subject, "candidates": кандидаты}
    await session.flush()
    await events.record(
        session,
        EventKind.VOTE_NOMINATED,
        actor_identity_id=who.id,
        city_id=str(city.id),
        vote_id=str(vote.id),
        who=who.name,
    )
    return vote


async def choose(
    session: AsyncSession,
    city: City,
    identity: Identity,
    vote: Vote,
    candidate: Identity,
    *,
    now: datetime | None = None,
) -> Ballot:
    """Отдать голос кандидату. Один голос: передумать до срока можно."""
    moment = now or datetime.now(UTC)
    if vote.kind not in (VoteKind.ELECTION, VoteKind.COUNCIL):
        raise VoteError("это не выборы: здесь голосуют «за» или «против»")
    if vote.state is not VoteState.OPEN or vote.closes_at <= moment:
        raise Closed("голосование закрыто: опоздавший голос итога не меняет")
    if not await may_vote_in(session, city, identity.id, vote, now=moment):
        raise NoVoice(
            "голоса нет: в этих выборах решают "
            + ("члены совета" if vote.voters == COUNCIL_VOTERS else "граждане")
        )
    if str(candidate.id) not in (vote.subject.get("candidates") or []):
        raise NotCandidate(f"{candidate.name} не выдвигался")

    бюллетень = (
        await session.execute(
            select(Ballot).where(
                Ballot.vote_id == vote.id, Ballot.identity_id == identity.id
            )
        )
    ).scalar_one_or_none()
    if бюллетень is None:
        бюллетень = Ballot(
            vote_id=vote.id,
            identity_id=identity.id,
            yes=True,
            choice_identity_id=candidate.id,
        )
        session.add(бюллетень)
    else:
        бюллетень.choice_identity_id = candidate.id
    await session.flush()
    await events.record(
        session,
        EventKind.VOTE_CAST,
        actor_identity_id=identity.id,
        city_id=str(city.id),
        vote_id=str(vote.id),
        choice=candidate.name,
    )
    return бюллетень


async def tally(session: AsyncSession, vote: Vote) -> dict[str, int]:
    """Сколько у кого голосов. Голосование открытое, расклад виден всем."""
    бюллетени = (
        await session.execute(select(Ballot).where(Ballot.vote_id == vote.id))
    ).scalars().all()
    счёт: dict[str, int] = {}
    for б in бюллетени:
        if б.choice_identity_id is None:
            continue
        ключ = str(б.choice_identity_id)
        счёт[ключ] = счёт.get(ключ, 0) + 1
    return счёт


async def _finish_election(session: AsyncSession, vote: Vote, city) -> str:
    """Подвести выборы: у кого больше голосов, тот и правитель.

    Порога у выборов нет (D-162): требовать большинства от всех поданных
    значит подвесить город без правителя при трёх кандидатах. Кворум — общий.
    """
    from src.engine import city as town

    счёт = await tally(session, vote)
    подано = sum(счёт.values())
    нужен_кворум = float(vote.quorum_share) / PERCENT * vote.electorate
    if подано < нужен_кворум:
        return "кворум не собран"
    if not счёт:
        return "не проголосовал никто"

    лучший = max(счёт.values())
    победители = [кто for кто, голосов in счёт.items() if голосов == лучший]
    if len(победители) > 1:
        #: Ничья не решается движком: жребий — это отдельный вариант устава,
        #: и выдумывать его здесь нельзя (D-065).
        return "ничья: победитель не определён"

    победитель = await session.get(Identity, uuid.UUID(победители[0]))
    if победитель is None:  # pragma: no cover — кандидат живёт в личностях
        return "победитель исчез"
    await town.hand_over(session, city, победитель)
    vote.subject = {**vote.subject, "winner": str(победитель.id)}
    return f"избран {победитель.name}"


async def _finish_recall(session: AsyncSession, vote: Vote, city, прошло: bool) -> None:
    """Отзыв прошёл — должность снимается, и тут же созываются выборы."""
    from src.constants import current
    from src.engine import city as town

    if not прошло:
        return
    await town.dismiss(session, city)
    if elects_ruler(city):
        await open_election(session, current(), city, None)


# --- правка устава голосованием (D-163) ---------------------------------------

#: Вопрос устава о том, как правится сам устав, и его варианты.
AMENDMENT = "charter_amendment"
BY_RULER, NEVER = "ruler", "never"

#: Каким порогом голосуется правка. Ключи — варианты `charter_amendment`,
#: значения — пороги той же машины: у конституции свой порог, а не `law_threshold`.
AMENDMENT_THRESHOLD = {"two_thirds": TWO_THIRDS, "unanimous": UNANIMOUS}


class Sealed(VoteError):
    """Устав запечатан: `charter_amendment: never` исполняется буквально."""


def amends_by_vote(city: City) -> bool:
    """Правится ли устав голосованием, а не росчерком правителя."""
    return answer(city, AMENDMENT, BY_RULER) in AMENDMENT_THRESHOLD


def sealed(city: City) -> bool:
    return answer(city, AMENDMENT, BY_RULER) == NEVER


async def open_charter(
    session: AsyncSession,
    constants: Constants,
    city: City,
    by: Identity,
    question_id: str,
    option_id: str,
    value: float | None = None,
    *,
    now: datetime | None = None,
) -> Vote:
    """Созвать голосование о правке устава (D-163).

    Порог берётся из `charter_amendment`, а не из `law_threshold`: город вправе
    принимать законы простым большинством и требовать двух третей для
    конституции — вольт спрашивает об этом отдельно.
    """
    if sealed(city):
        raise Sealed("устав этого города не меняется: так решил он сам")
    голосование = await _open(
        session,
        constants,
        city,
        by,
        kind=VoteKind.CHARTER,
        subject={"question": question_id, "option": option_id, "param": value},
        now=now,
    )
    голосование.threshold = AMENDMENT_THRESHOLD[answer(city, AMENDMENT, BY_RULER)]
    await session.flush()
    return голосование


async def _finish_charter(session: AsyncSession, vote: Vote, city) -> None:
    """Правка принята: ответ устава меняется, как если бы его дал правитель."""
    вопрос = str(vote.subject.get("question"))
    устав = dict(city.charter or {})
    устав[вопрос] = vote.subject.get("option")
    city.charter = устав
    значение = vote.subject.get("param")
    if значение is not None:
        параметры = dict(city.charter_params or {})
        параметры[вопрос] = значение
        city.charter_params = параметры
    await session.flush()
    await events.record(
        session,
        EventKind.CITY_CHARTER_SET,
        node_id=city.node_id,
        city_id=str(city.id),
        question=вопрос,
        option=vote.subject.get("option"),
        by_vote=True,
    )


# --- совет (D-164) ------------------------------------------------------------

#: Вопрос устава о совете и его варианты.
COUNCIL = "council_exists"
NO_COUNCIL, ELECTED_COUNCIL, APPOINTED_COUNCIL = "none", "elected", "appointed"
#: Кто вносит закон: правитель либо совет.
LAWMAKER = "lawmaker"
BY_COUNCIL = "council"

#: Круги голосующих. Строкой, потому что их станет больше вместе с уставом.
CITIZENS, COUNCIL_VOTERS = "citizens", "council"


class NoCouncil(VoteError):
    """Совета в этом городе нет: устав ответил «совета нет»."""


def council_mode(city: City) -> str:
    return answer(city, COUNCIL, NO_COUNCIL)


def council_seats(city: City) -> int:
    """Сколько мест назначил устав. Ноль мест равен отсутствию совета."""
    return int(param(city, COUNCIL))


def has_council(city: City) -> bool:
    return council_mode(city) != NO_COUNCIL and council_seats(city) > 0


async def council_of(session: AsyncSession, city: City) -> list[CouncilSeat]:
    """Занятые места совета."""
    return list(
        (
            await session.execute(
                select(CouncilSeat).where(
                    CouncilSeat.city_id == city.id,
                    CouncilSeat.vacated_at.is_(None),
                )
            )
        ).scalars().all()
    )


async def in_council(
    session: AsyncSession, city: City, identity_id: uuid.UUID
) -> bool:
    return any(
        место.identity_id == identity_id for место in await council_of(session, city)
    )


def voters_for(city: City, kind: VoteKind) -> str:
    """Кто голосует по этому предмету (D-164, D-165).

    Круг определяется предметом **и** уставом: закон утверждает совет, если так
    сказано; правителя выбирает и отзывает тот, кому устав это отдал. Всё
    остальное — дело граждан.

    Пустая палата не запирает ни законы, ни власть: город с нулём мест решает
    сам, всем городом, а закон применяет тот, кто его внёс. Устав, который
    невозможно исполнить буквально, исполняется по смыслу, а не блокирует
    город навсегда.
    """
    советом = {
        VoteKind.LAW: answer(city, APPROVAL, "ruler") == BY_COUNCIL,
        VoteKind.ELECTION: answer(city, SELECTION, "founder") == ELECTED_BY_COUNCIL,
        VoteKind.RECALL: answer(city, RECALL_RULE, "never") == RECALL_BY_COUNCIL,
    }.get(kind, False)
    if советом and has_council(city):
        return COUNCIL_VOTERS
    return CITIZENS


async def may_propose(
    session: AsyncSession, city: City, identity_id: uuid.UUID
) -> bool:
    """Вправе ли этот человек вносить законы (`lawmaker`).

    Право `laws` вносит закон всегда — это власть. Совет добавляется к ней,
    когда устав отвечает «вносит совет»: тогда законодателей столько, сколько
    мест, и правитель среди них не единственный.
    """
    if answer(city, LAWMAKER, "ruler") != BY_COUNCIL:
        return False
    return await in_council(session, city, identity_id)


async def seat(
    session: AsyncSession, city: City, who: Identity, *, how: str
) -> CouncilSeat:
    """Посадить человека в совет. Мест не больше, чем назначил устав."""
    if not has_council(city):
        raise NoCouncil("устав этого города не заводит совета")
    занятые = await council_of(session, city)
    if any(место.identity_id == who.id for место in занятые):
        return next(м for м in занятые if м.identity_id == who.id)
    if len(занятые) >= council_seats(city):
        raise NoCouncil(
            f"в совете {council_seats(city)} мест, и все заняты: "
            "сначала освободить место"
        )

    место = CouncilSeat(city_id=city.id, identity_id=who.id, how=how)
    session.add(место)
    await session.flush()
    await events.record(
        session,
        EventKind.COUNCIL_SEATED,
        actor_identity_id=who.id,
        node_id=city.node_id,
        city_id=str(city.id),
        who=who.name,
        how=how,
    )
    return место


async def vacate(session: AsyncSession, city: City, who: Identity) -> bool:
    """Освободить место. Запись остаётся: кто голосовал — вопрос суда."""
    for место in await council_of(session, city):
        if место.identity_id != who.id:
            continue
        место.vacated_at = datetime.now(UTC)
        await session.flush()
        await events.record(
            session,
            EventKind.COUNCIL_VACATED,
            node_id=city.node_id,
            city_id=str(city.id),
            who=who.name,
        )
        return True
    return False


async def appoint_to_council(
    session: AsyncSession, city: City, by: Identity, who: Identity
) -> CouncilSeat:
    """Назначить в совет. Только там, где устав отдал места правителю."""
    from src.engine import city as town

    if council_mode(city) != APPOINTED_COUNCIL:
        raise NoCouncil(
            "места этого совета не назначают: устав отдал их выборам"
        )
    await town.require(session, by.id, city, Power.OFFICES)
    if not await may_vote(session, city, who.id):
        raise NoVoice("в совет садятся граждане, отвечающие цензу устава")
    return await seat(session, city, who, how=APPOINTED_COUNCIL)


async def open_council_election(
    session: AsyncSession,
    constants: Constants,
    city: City,
    by: Identity | None = None,
    *,
    now: datetime | None = None,
) -> Vote:
    """Созвать выборы в совет: побеждают столько, сколько мест."""
    if council_mode(city) != ELECTED_COUNCIL or council_seats(city) <= 0:
        raise NoCouncil("устав этого города не выбирает совет")
    return await _open(
        session,
        constants,
        city,
        by,
        kind=VoteKind.COUNCIL,
        subject={"candidates": [], "seats": council_seats(city)},
        now=now,
    )


async def _finish_council(session: AsyncSession, vote: Vote, city) -> str:
    """Подвести выборы в совет: места достаются набравшим больше голосов."""
    счёт = await tally(session, vote)
    подано = sum(счёт.values())
    нужен_кворум = float(vote.quorum_share) / PERCENT * vote.electorate
    if подано < нужен_кворум:
        return "кворум не собран"
    if not счёт:
        return "не проголосовал никто"

    мест = int(vote.subject.get("seats") or 0)
    #: Больше голосов — выше место; при равенстве порядок задан ключом, и это
    #: не жребий: жребий — отдельный вариант устава, его здесь нет (D-162).
    победители = sorted(счёт.items(), key=lambda пара: (-пара[1], пара[0]))[:мест]

    #: Прежний состав складывается целиком: выборы обновляют палату, а не
    #: дописывают в неё.
    for место in await council_of(session, city):
        кто = await session.get(Identity, место.identity_id)
        if кто is not None:
            await vacate(session, city, кто)
    посажено = 0
    for сырой, _ in победители:
        кто = await session.get(Identity, uuid.UUID(сырой))
        if кто is None:  # pragma: no cover — кандидат живёт в личностях
            continue
        await seat(session, city, кто, how=ELECTED_COUNCIL)
        посажено += 1
    return f"избрано мест: {посажено}"
