"""Суд: жалоба, дело, приговор, исполнение (D-095, D-117, D-166).

Право `justice` было объявлено, четырнадцать примитивов санкций лежали в
`laws.json` — а суда не существовало. Всё, что движок не проверяет сам
(договор, слово-закон, снос чужого), обеспечивалось связкой «жалоба → суд →
санкция», у которой не было ни одного звена.

## Как устроено

**Жалоба** подаётся в конкретный город и стоит `justice.court_fee` в его казну:
пошлина — не барьер, а то, что делает хороший суд выгодным городу (D-117).
Срок давности — `justice.claim_window` суток: суд не архив обид.

**Дело** — истец, ответчик, суть претензии словами. Движок претензию не
осмысляет: разбирать её — работа судьи, а не кода.

**Приговор** называет санкцию из примитивов вольта. Движок исполняет три и
честно отказывает в остальных:

| Санкция | Что делает движок |
|---|---|
| `fine` | списывает со счёта в казну; чего нет — записано долгом |
| `prison` | держит тело в узле до срока, не дольше `justice.prison_max` |
| `exile` | снимает гражданство (D-160): высылка, а не смерть |

**Исполнение не зависит от того, онлайн ли стража:** санкция применяется в
момент приговора, срок снимается заданием журнала.

## Чего здесь нет

Обжалования, розыска сбежавшего, признания чужих приговоров и одиннадцати
остальных примитивов: конфискация требует описи имущества, арест — обратимой
заморозки, лишение лицензии — самих лицензий. Каждый из них отдельная
механика, а не строка в перечислении.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Catalog, Constants
from src.constants import registry as R
from src.engine import events, ledger
from src.engine.jobs import enqueue, handler
from src.models.city import City, Power
from src.models.event import EventKind
from src.models.identity import Identity
from src.models.job import Job, JobKind
from src.models.justice import Case, CaseState, Sanction
from src.models.ledger import AccountKind, PostingReason
from src.units import money, money_str

#: Примитивы, которые движок исполняет. Остальные названы в шапке: список
#: санкций живёт в вольте, а исполняет движок ровно то, что умеет.
FINE, PRISON, EXILE = "fine", "prison", "exile"

#: Свойство узла «тюрьма» (D-174): жила, принтер и терминал за одной стеной.
#: Ставит его город; движку тюрьма — это место, где отработка гасит долг.
PRISON_NODE = "тюрьма"
ENFORCED = (FINE, PRISON, EXILE)


class JusticeError(Exception):
    pass


class TooLate(JusticeError):
    """Срок давности вышел: суд — не архив обид."""


class CannotPayFee(JusticeError):
    """Пошлина не по карману. Суд стоит денег, и это решение города."""


class NotJudge(JusticeError):
    """Судит тот, кому город дал право `justice`."""


class Unenforceable(JusticeError):
    """Такую санкцию движок не исполняет — и молча делать вид не станет."""


async def sue(
    session: AsyncSession,
    constants: Constants,
    city: City,
    plaintiff: Identity,
    defendant: Identity,
    claim: str,
    *,
    happened_at: datetime | None = None,
    now: datetime | None = None,
) -> Case:
    """Подать жалобу. Пошлина уходит в казну города сразу (D-117)."""
    moment = now or datetime.now(UTC)
    суть = claim.strip()
    if not суть:
        raise JusticeError("жалоба без сути — не жалоба")
    if plaintiff.id == defendant.id:
        raise JusticeError("на себя не жалуются")
    if happened_at is not None:
        окно = timedelta(days=constants[R.JUSTICE_CLAIM_WINDOW])
        if happened_at + окно < moment:
            raise TooLate(
                f"с события прошло больше {constants[R.JUSTICE_CLAIM_WINDOW]:g} суток: "
                "срок давности вышел"
            )

    from src.engine import city as town

    пошлина = money(constants[R.JUSTICE_COURT_FEE])
    счёт = await ledger.account_for(session, AccountKind.IDENTITY, plaintiff.id)
    if await ledger.balance(session, счёт.id) < пошлина:
        raise CannotPayFee(
            f"пошлина суда {money_str(пошлина)} ₭, а на счету меньше"
        )
    казна = await town.treasury(session, city)
    await ledger.transfer(
        session,
        PostingReason.COURT_FEE,
        debit=счёт.id,
        credit=казна.id,
        amount=пошлина,
        memo={"пошлина суда": city.name},
    )

    дело = Case(
        city_id=city.id,
        plaintiff_identity_id=plaintiff.id,
        defendant_identity_id=defendant.id,
        claim=суть,
        fee=пошлина,
    )
    session.add(дело)
    await session.flush()
    await events.record(
        session,
        EventKind.CASE_OPENED,
        actor_identity_id=plaintiff.id,
        node_id=city.node_id,
        city_id=str(city.id),
        case_id=str(дело.id),
        against=defendant.name,
        claim=суть,
        fee=пошлина,
    )
    return дело


async def judge(
    session: AsyncSession,
    constants: Constants,
    catalog: Catalog,
    by: Identity,
    case: Case,
    *,
    sanction: str | None = None,
    days: float | None = None,
    amount: float | None = None,
    verdict: str = "",
    now: datetime | None = None,
) -> Sanction | None:
    """Вынести приговор. Без санкции — оправдание: висящих дел не бывает."""
    from src.engine import city as town

    moment = now or datetime.now(UTC)
    if case.state is not CaseState.OPEN:
        raise JusticeError("дело уже рассмотрено")
    city = await town.by_id(session, case.city_id)
    if city is None:  # pragma: no cover — дело без города это баг
        raise JusticeError("дело ссылается в никуда")
    if not await town.may(session, by.id, city, Power.JUSTICE):
        raise NotJudge("судит тот, кому город дал право justice")

    case.judge_identity_id = by.id
    case.judged_at = moment

    if sanction is None:
        case.state = CaseState.DISMISSED
        case.verdict = verdict or "отказано"
        await session.flush()
        await events.record(
            session,
            EventKind.CASE_JUDGED,
            actor_identity_id=by.id,
            node_id=city.node_id,
            city_id=str(city.id),
            case_id=str(case.id),
            sanction=None,
            verdict=case.verdict,
        )
        return None

    известные = {примитив.id for примитив in catalog.laws.sanctions}
    if sanction not in известные:
        raise JusticeError(f"нет такой санкции: {sanction}")
    if sanction not in ENFORCED:
        raise Unenforceable(
            f"«{sanction}» движок пока не исполняет: приговор без исполнения — "
            "хуже, чем отказ от приговора"
        )

    ответчик = await session.get(Identity, case.defendant_identity_id)
    if ответчик is None:  # pragma: no cover — личность вечна
        raise JusticeError("ответчик исчез")

    наказание = await _enforce(
        session,
        constants,
        city,
        case,
        ответчик,
        sanction,
        days=days,
        amount=amount,
        now=moment,
    )
    case.state = CaseState.JUDGED
    case.verdict = verdict or sanction
    await session.flush()
    await events.record(
        session,
        EventKind.CASE_JUDGED,
        actor_identity_id=by.id,
        node_id=city.node_id,
        city_id=str(city.id),
        case_id=str(case.id),
        sanction=sanction,
        verdict=case.verdict,
    )
    return наказание


async def _enforce(
    session: AsyncSession,
    constants: Constants,
    city: City,
    case: Case,
    who: Identity,
    kind: str,
    *,
    days: float | None,
    amount: float | None,
    now: datetime,
) -> Sanction:
    """Исполнить санкцию. Стража для этого не нужна: исполняет движок."""
    from src.engine import city as town

    наказание = Sanction(
        case_id=case.id, city_id=city.id, identity_id=who.id, kind=kind
    )

    if kind == FINE:
        присуждено = money(amount or 0)
        счёт = await ledger.account_for(session, AccountKind.IDENTITY, who.id)
        есть = await ledger.balance(session, счёт.id)
        взыскано = min(есть, присуждено)
        if взыскано > 0:
            казна = await town.treasury(session, city)
            await ledger.transfer(
                session,
                PostingReason.FINE,
                debit=счёт.id,
                credit=казна.id,
                amount=взыскано,
                memo={"штраф по делу": str(case.id)},
            )
        наказание.amount = присуждено
        #: Чего нет — то долг перед городом. Взыскания долга пока нет, и
        #: выдумывать его здесь нельзя: это отдельная механика (D-166).
        наказание.debt = присуждено - взыскано

    elif kind == PRISON:
        потолок = constants[R.JUSTICE_PRISON_MAX]
        срок = min(float(days or потолок), потолок)
        наказание.until = now + timedelta(days=срок)
        тело = await _body_of(session, who)
        наказание.node_id = None if тело is None else тело.node_id

    elif kind == EXILE:
        запись = await town.citizenship(session, who.id)
        if запись is not None and запись.city_id == city.id:
            await session.delete(запись)
            await session.flush()

    session.add(наказание)
    await session.flush()
    await events.record(
        session,
        EventKind.SANCTION_APPLIED,
        actor_identity_id=who.id,
        node_id=city.node_id,
        city_id=str(city.id),
        case_id=str(case.id),
        sanction=kind,
        amount=наказание.amount,
        debt=наказание.debt,
        until=None if наказание.until is None else наказание.until.isoformat(),
    )
    if наказание.until is not None:
        await enqueue(
            session,
            JobKind.SANCTION_LIFT,
            наказание.until,
            payload={"sanction": str(наказание.id)},
            dedup_key=f"sanction.lift:{наказание.id}",
        )
    return наказание


@handler(JobKind.SANCTION_LIFT)
async def lift(session: AsyncSession, job: Job) -> None:
    """Срок вышел: санкция снимается сама, без чьего-либо участия."""
    наказание = await session.get(Sanction, uuid.UUID(job.payload["sanction"]))
    if наказание is None or наказание.lifted_at is not None:
        return
    наказание.lifted_at = job.run_at
    await session.flush()
    await events.record(
        session,
        EventKind.SANCTION_LIFTED,
        actor_identity_id=наказание.identity_id,
        city_id=str(наказание.city_id),
        sanction=наказание.kind,
    )


async def active(
    session: AsyncSession, identity_id: uuid.UUID, kind: str | None = None
) -> list[Sanction]:
    """Действующие санкции на человеке."""
    условия = [Sanction.identity_id == identity_id, Sanction.lifted_at.is_(None)]
    if kind is not None:
        условия.append(Sanction.kind == kind)
    строки = (await session.execute(select(Sanction).where(*условия))).scalars().all()
    сейчас = datetime.now(UTC)
    return [
        наказание
        for наказание in строки
        if наказание.until is None or наказание.until > сейчас
    ]


async def imprisoned(
    session: AsyncSession, identity_id: uuid.UUID
) -> Sanction | None:
    """Заключение, если оно действует. Тело держат узлом, а не уговорами."""
    сидит = await active(session, identity_id, PRISON)
    return сидит[0] if сидит else None


async def cases_of(session: AsyncSession, city: City) -> list[Case]:
    return list(
        (
            await session.execute(
                select(Case)
                .where(Case.city_id == city.id)
                .order_by(Case.opened_at.desc())
            )
        ).scalars().all()
    )


async def view(session: AsyncSession, city: City) -> list[dict]:
    """Карточки дел этого города — то, что показывает клиент."""
    итог: list[dict] = []
    for дело in await cases_of(session, city):
        истец = await session.get(Identity, дело.plaintiff_identity_id)
        ответчик = await session.get(Identity, дело.defendant_identity_id)
        итог.append(
            {
                "id": str(дело.id),
                "plaintiff": None if истец is None else истец.name,
                "defendant": None if ответчик is None else ответчик.name,
                "claim": дело.claim,
                "state": дело.state.value,
                "verdict": дело.verdict,
                "opened_at": дело.opened_at.isoformat(),
            }
        )
    return итог


async def _body_of(session: AsyncSession, who: Identity):
    from src.engine import death

    return await death.alive_body(session, who.id)
