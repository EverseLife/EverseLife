"""Банк: резерв, кредит, ключевая ставка (D-030, D-087, D-167).

Единственным источником денег до сих пор был `genesis`, то есть любая выдача
была бы чистой эмиссией, а «денежная политика» — словом.

## Резерв стерилизует, а не копит

Выдавая кредит, система берёт ТК из **резерва** — уже существующих денег,
собранных процентами. Чего не хватает, печатает через `genesis`. Погашение и
проценты возвращают ТК **в резерв**, а не в оборот. Отсюда инвариант, который
движок держит и проверяет:

    вся масса ТК = деньги на счетах + резерв системы

Цены зависят не от всей массы, а от **оборотной** — той, что на счетах.

## Ключевая ставка считается формулой, а не решается

    ставка = bank.base_rate
           + bank.rate_reaction_k     × (инфляция − bank.target_inflation)
           + bank.emission_reaction_k × (доля эмиссии − bank.emission_share_target)

с полом `bank.rate_floor`, потолком `bank.rate_cap` и шагом не больше
`bank.rate_step_max` за пересмотр. Алгоритм публичен и детерминирован: те же
входные данные дают тот же ответ, иначе банк превращается в скрытого NPC с
собственной волей (D-030). **Молчащий датчик не повод шевелить рычаг:** нет
данных по инфляции — нет и реакции на неё.

## Заём — договор

Ставка заёмщика фиксируется при выдаче и дальше не меняется, что бы банк ни
решил после. Залога нет (D-173): лимит выдаёт **труд** — оборот продаж,
возвращённые кредиты, стаж без просрочек и доверие, — и считается он публичной
формулой, как ставка.

## Банк двухуровневый (D-175)

Печатает деньги только столица. Гражданин занимает **у своего города** по
ставке «ключевая + маржа города» (код-закон `bank_margin`, потолок
`bank.city_margin_cap`); каждый такой заём ложится на кредитную линию города
перед столицей — `bank.debt_to_turnover_cap` от его оборота. Линия кончилась
или гражданства нет — прямой заём столицы по худшей ставке: выход есть всегда,
но дешёвый кредит — привилегия гражданства (D-160).

Маржа с каждого платежа процентов уходит в казну города, ключевая часть — в
резерв столицы. Так город зарабатывает на своих заёмщиках и отвечает за них
своей линией: сеньораж (D-171) отменён за ненадобностью.

## Чего здесь нет

Процента по вкладу — это доход без труда, то есть эмиссия в обход столпа П1
(D-087). И переработки за репорты: репорт «дефектная печать» снижает доверие и
режет лимит, но не убивает — необратимое делает только внеигровой саппорт.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Constants
from src.constants import registry as R
from src.engine import events, ledger
from src.engine.jobs import enqueue, handler
from src.models.bank import DefectReport, Loan, LoanState, RateDecision
from src.models.city import City, Power
from src.models.event import EventKind
from src.models.identity import Identity
from src.models.job import Job, JobKind
from src.models.ledger import AccountKind, LedgerAccount, PostingReason
from src.units import MONEY_SCALE, PERCENT, amount_float, money, money_str

#: Владелец счёта резерва. Резерв один на мир: банк — единая система, а не
#: набор предприятий (D-030, D-031).
RESERVE = uuid.UUID("00000000-0000-0000-0000-00000000ba17")


class BankError(Exception):
    pass


class TooMuch(BankError):
    """Столько не дают: без залога есть предел, под залог — норма залога."""


class NothingToRepay(BankError):
    pass


async def reserve_account(session: AsyncSession) -> LedgerAccount:
    """Счёт резерва системы. Заводится по первой надобности."""
    return await ledger.account_for(session, AccountKind.BANK_RESERVE, RESERVE)


async def reserve(session: AsyncSession) -> int:
    return await ledger.balance(session, (await reserve_account(session)).id)


async def key_rate(session: AsyncSession, constants: Constants) -> float:
    """Действующая ключевая ставка: последнее решение либо базовая."""
    решение = (
        await session.execute(
            select(RateDecision).order_by(RateDecision.decided_at.desc()).limit(1)
        )
    ).scalars().first()
    return (
        float(решение.rate) if решение is not None else constants[R.BANK_BASE_RATE]
    )


def compute_rate(
    constants: Constants,
    *,
    previous: float,
    inflation: float | None,
    emission_share: float | None,
) -> tuple[float, str]:
    """Публичная формула ставки. Возвращает ставку и объяснение словами.

    Объяснение — не украшение: алгоритм обязан быть не только детерминированным,
    но и читаемым, иначе спорить с денежной политикой нечем (D-030).
    """
    ставка = constants[R.BANK_BASE_RATE]
    причины = [f"база {ставка:g}"]

    if inflation is not None:
        цель = constants[R.BANK_TARGET_INFLATION]
        добавка = constants[R.BANK_RATE_REACTION_K] * (inflation - цель)
        ставка += добавка
        причины.append(f"инфляция {inflation:+.1f} против цели {цель:g} → {добавка:+.2f}")
    else:
        причины.append("инфляция не измерена: реакции нет")

    if emission_share is not None:
        цель = constants[R.BANK_EMISSION_SHARE_TARGET]
        добавка = constants[R.BANK_EMISSION_REACTION_K] * (emission_share - цель)
        ставка += добавка
        причины.append(
            f"эмиссия {emission_share:.0f}% против цели {цель:g} → {добавка:+.2f}"
        )

    #: Шаг ограничен: денежная политика не дёргается, иначе прогнозировать её
    #: невозможно, а прогноз — половина её смысла.
    шаг = constants[R.BANK_RATE_STEP_MAX]
    ставка = max(previous - шаг, min(previous + шаг, ставка))
    ставка = max(
        constants[R.BANK_RATE_FLOOR], min(constants[R.BANK_RATE_CAP], ставка)
    )
    return ставка, "; ".join(причины)


async def review_rate(
    session: AsyncSession, constants: Constants, *, now: datetime | None = None
) -> RateDecision:
    """Пересмотреть ставку по датчикам. Решение и его причина сохраняются."""
    moment = now or datetime.now(UTC)
    было = await key_rate(session, constants)
    инфляция = await _inflation(session, constants)
    доля_эмиссии = await _emission_share(session, constants, now=moment)
    ставка, почему = compute_rate(
        constants,
        previous=было,
        inflation=инфляция,
        emission_share=доля_эмиссии,
    )
    #: Инфляция за тревожной чертой возвращает ставку алгоритму на
    #: `bank.council_lockout` суток: политическое решение хорошо ровно до
    #: момента, когда цена ошибки — деньги у всех (D-172).
    блокировка = (
        moment + timedelta(days=constants[R.BANK_COUNCIL_LOCKOUT])
        if инфляция is not None and инфляция > constants[R.BANK_INFLATION_ALARM]
        else None
    )
    решение = RateDecision(
        rate=ставка,
        locked_until=блокировка,
        inflation=инфляция or 0,
        emission_share=доля_эмиссии or 0,
        why=почему,
        decided_at=moment,
    )
    session.add(решение)
    await session.flush()
    await events.record(
        session,
        EventKind.RATE_DECIDED,
        rate=ставка,
        was=было,
        why=почему,
    )
    return решение


async def borrow(
    session: AsyncSession,
    constants: Constants,
    catalog,
    who: Identity,
    amount: float,
    *,
    now: datetime | None = None,
) -> Loan:
    """Взять кредит. Деньги идут из резерва; недостающее печатается (D-087).

    Заём идёт через город гражданства (D-175): ставка — ключевая плюс маржа
    города, и заём занимает кредитную линию города перед столицей. Нет
    гражданства или линия исчерпана — прямой заём столицы по худшей ставке.
    """
    from src.engine import city as town

    moment = now or datetime.now(UTC)
    сумма = money(amount)
    if сумма <= 0:
        raise BankError("заём должен быть положительным")

    лимит, почему = await credit_limit(session, constants, who.id, now=moment)
    доступно = лимит - await debt_of(session, who.id)
    if сумма > доступно:
        raise TooMuch(
            f"столько не дают: доступно {money_str(max(0, доступно))} ₭ "
            f"из лимита {money_str(лимит)} ₭ ({почему})"
        )

    #: Город гражданства и его линия. Линия сжимается плавно: доступен ровно
    #: остаток, и набег «взять всё перед отсечением» упирается в арифметику.
    город = None
    маржа = 0.0
    запись = await town.citizenship(session, who.id)
    if запись is not None:
        кандидат = await town.by_id(session, запись.city_id)
        if кандидат is not None:
            _, _, свободно = await city_line(session, constants, кандидат, now=moment)
            if сумма <= свободно:
                город = кандидат
                маржа = city_margin(constants, catalog, кандидат)

    if город is not None:
        ставка = await key_rate(session, constants) + маржа
    else:
        #: Прямой заём столицы: выход для не-граждан и жителей отсечённых
        #: городов, но по верху вилки риска (D-175).
        ставка = await key_rate(session, constants) + constants[R.BANK_RISK_PREMIUM].max

    #: Резерв — стерилизатор: сначала тратим уже существующие ТК, и только
    #: недостающее печатаем. Печать видна отдельной проводкой и телеметрией.
    казна_резерва = await reserve_account(session)
    есть = await ledger.balance(session, казна_резерва.id)
    напечатано = max(0, сумма - есть)
    if напечатано > 0:
        genesis = await ledger.account_for(session, AccountKind.GENESIS, None)
        await ledger.transfer(
            session,
            PostingReason.GENESIS,
            debit=genesis.id,
            credit=казна_резерва.id,
            amount=напечатано,
            memo={"печать под кредит": who.name},
        )

    счёт = await ledger.account_for(session, AccountKind.IDENTITY, who.id)
    await ledger.transfer(
        session,
        PostingReason.LOAN,
        debit=казна_резерва.id,
        credit=счёт.id,
        amount=сумма,
        memo={"кредит": who.name},
    )

    заём = Loan(
        identity_id=who.id,
        principal=сумма,
        outstanding=сумма,
        rate=ставка,
        city_id=None if город is None else город.id,
        margin=маржа,
        printed=напечатано,
        taken_at=moment,
        accrued_at=moment,
        serviced_at=moment,
    )
    session.add(заём)
    await session.flush()
    await events.record(
        session,
        EventKind.LOAN_TAKEN,
        actor_identity_id=who.id,
        loan_id=str(заём.id),
        amount=сумма,
        rate=ставка,
        printed=напечатано,
        city=None if город is None else город.name,
        margin=маржа,
    )
    return заём


async def accrue(
    session: AsyncSession, constants: Constants, loan: Loan, *, now: datetime | None = None
) -> int:
    """Начислить проценты за прошедшие сутки. Возвращает начисленное.

    Года в мире нет — сутки Терры тридцатичасовые, — поэтому расчётный год
    задан вольтом (`bank.year_days`, D-167): число банковское, а не
    астрономическое.
    """
    moment = now or datetime.now(UTC)
    if loan.state is not LoanState.OPEN:
        return 0
    прошло = (moment - loan.accrued_at).total_seconds() / timedelta(days=1).total_seconds()
    if прошло <= 0:
        return 0
    в_сутки = float(loan.rate) / PERCENT / constants[R.BANK_YEAR_DAYS]
    начислено = int(loan.outstanding * в_сутки * прошло)
    loan.outstanding += начислено
    loan.interest_accrued += начислено
    loan.accrued_at = moment
    await session.flush()
    return начислено


async def repay(
    session: AsyncSession,
    constants: Constants,
    who: Identity,
    loan: Loan,
    amount: float | None = None,
    *,
    from_account=None,
    now: datetime | None = None,
) -> int:
    """Погасить долг. Деньги уходят **в резерв**, а не в оборот (D-087).

    Платить может **кто угодно** (D-063, D-168): за должника вправе рассчитаться
    третий — и город из своей казны тоже (`from_account`). Движок не
    спрашивает, зачем: деньги приняты, долг уменьшился.
    """
    moment = now or datetime.now(UTC)
    if loan.state is not LoanState.OPEN:
        raise NothingToRepay("этот заём уже закрыт")
    await accrue(session, constants, loan, now=moment)

    счёт = from_account or await ledger.account_for(
        session, AccountKind.IDENTITY, who.id
    )
    есть = await ledger.balance(session, счёт.id)
    хочет = loan.outstanding if amount is None else money(amount)
    платёж = min(хочет, loan.outstanding, есть)
    if платёж <= 0:
        raise NothingToRepay("платить нечем")

    await _settle(session, loan, счёт, платёж)
    loan.serviced_at = moment
    if loan.outstanding <= 0:
        loan.state = LoanState.REPAID
        loan.repaid_at = moment
    await session.flush()
    await events.record(
        session,
        EventKind.LOAN_REPAID,
        actor_identity_id=who.id,
        loan_id=str(loan.id),
        amount=платёж,
        left=loan.outstanding,
        closed=loan.state is LoanState.REPAID,
    )
    return платёж


async def _settle(session: AsyncSession, loan: Loan, счёт, платёж: int) -> None:
    """Провести платёж: проценты вперёд тела, маржа города — в его казну.

    Порядок обычный банковский, и он же делает «доход системы» измеримым
    (D-171): без раздельного учёта маржу города не отделить от ключевой части,
    которая стерилизуется в резерве столицы (D-175).
    """
    from src.engine import city as town

    проценты = min(платёж, max(0, loan.interest_accrued - loan.interest_paid))
    маржа_города = 0
    if проценты > 0 and loan.city_id is not None and float(loan.rate) > 0:
        маржа_города = int(проценты * float(loan.margin) / float(loan.rate))
    город = None if loan.city_id is None else await town.by_id(session, loan.city_id)

    if маржа_города > 0 and город is not None:
        await ledger.transfer(
            session,
            PostingReason.BANK_MARGIN,
            debit=счёт.id,
            credit=(await town.treasury(session, город)).id,
            amount=маржа_города,
            memo={"маржа города": город.name, "заём": str(loan.id)},
        )
    в_резерв = платёж - маржа_города
    if в_резерв > 0:
        await ledger.transfer(
            session,
            PostingReason.LOAN_REPAYMENT,
            debit=счёт.id,
            credit=(await reserve_account(session)).id,
            amount=в_резерв,
            memo={"погашение": str(loan.id)},
        )
    loan.interest_paid += проценты
    loan.outstanding -= платёж


async def loans_of(session: AsyncSession, identity_id: uuid.UUID) -> list[Loan]:
    return list(
        (
            await session.execute(
                select(Loan).where(
                    Loan.identity_id == identity_id, Loan.state == LoanState.OPEN
                )
            )
        ).scalars().all()
    )


async def circulating(session: AsyncSession) -> int:
    """Оборотная масса: деньги на счетах личностей и казн, без резерва.

    Цены зависят от неё, а не от всей массы: то, что лежит в резерве, из
    оборота вышло и ждёт следующего заёмщика (D-087).
    """
    итог = 0
    for вид in (AccountKind.IDENTITY, AccountKind.CITY_TREASURY, AccountKind.ESCROW):
        счета = (
            await session.execute(
                select(LedgerAccount.id).where(LedgerAccount.kind == вид)
            )
        ).scalars().all()
        for счёт in счета:
            итог += await ledger.balance(session, счёт)
    return итог


@handler(JobKind.RATE_REVIEW)
async def rate_review(session: AsyncSession, job: Job) -> None:
    """Пересмотр ставки по расписанию: раз в `bank.rate_review_period` суток."""
    from src.constants import current

    constants = current()
    await review_rate(session, constants, now=job.run_at)
    await schedule_review(session, constants, after=job.run_at)


async def schedule_review(
    session: AsyncSession, constants: Constants, *, after: datetime | None = None
) -> None:
    moment = after or datetime.now(UTC)
    срок = moment + timedelta(days=constants[R.BANK_RATE_REVIEW_PERIOD])
    await enqueue(
        session,
        JobKind.RATE_REVIEW,
        срок,
        dedup_key=f"bank.rate:{int(срок.timestamp())}",
    )


async def _inflation(session: AsyncSession, constants: Constants) -> float | None:
    """Инфляция по суточным метрикам. Нет данных — молчим, а не выдумываем."""
    from src.models.metrics import DailyMetric

    окно = int(constants[R.BANK_PRICE_INDEX_WINDOW])
    строки = (
        await session.execute(
            select(DailyMetric)
            .where(DailyMetric.key == PRICE_INDEX)
            .order_by(DailyMetric.day.desc())
            .limit(окно)
        )
    ).scalars().all()
    #: Одной точки для изменения мало: датчик молчит, пока не с чем сравнить.
    if len(строки) <= 1:
        return None
    новый, старый = float(строки[0].value), float(строки[-1].value)
    if старый <= 0:
        return None
    return (новый - старый) / старый * PERCENT


async def _emission_share(
    session: AsyncSession, constants: Constants, *, now: datetime
) -> float | None:
    """Доля напечатанного в выданном за окно. Датчик быстрый: видно раньше цен."""
    окно = now - timedelta(days=constants[R.BANK_PRICE_INDEX_WINDOW])
    строка = (
        await session.execute(
            select(func.sum(Loan.principal), func.sum(Loan.printed)).where(
                Loan.taken_at >= окно
            )
        )
    ).one()
    выдано, напечатано = строка[0] or 0, строка[1] or 0
    if выдано <= 0:
        return None
    return напечатано / выдано * PERCENT


# --- несостоятельность (D-063, D-168) -----------------------------------------


class Restrained(BankError):
    """Долг держит в узле: это физика мира, а не приговор города."""


def overdue_days(loan: Loan, now: datetime) -> float:
    """Сколько суток по займу не платили. Просрочка — это неоплата, не возраст."""
    return (now - loan.serviced_at).total_seconds() / timedelta(days=1).total_seconds()


def overdue(constants: Constants, loan: Loan, now: datetime) -> bool:
    return overdue_days(loan, now) > constants[R.DEBT_GRACE_PERIOD]


async def debt_of(session: AsyncSession, identity_id: uuid.UUID) -> int:
    """Весь непогашенный долг личности, минорными единицами."""
    return sum(заём.outstanding for заём in await loans_of(session, identity_id))


async def restrained(
    session: AsyncSession,
    constants: Constants,
    identity_id: uuid.UUID,
    *,
    now: datetime | None = None,
) -> Loan | None:
    """Держит ли долг этого человека в узле (D-063).

    Два условия сразу: долг больше всего, что есть на счету, и не обслуживается
    дольше `debt.prison_threshold`. Одного мало: заём, который человек честно
    гасит, свободы не отнимает, а бедность сама по себе не преступление.
    """
    moment = now or datetime.now(UTC)
    займы = await loans_of(session, identity_id)
    if not займы:
        return None
    долг = sum(заём.outstanding for заём in займы)
    счёт = await ledger.account_for(session, AccountKind.IDENTITY, identity_id)
    if долг <= await ledger.balance(session, счёт.id):
        return None
    порог = constants[R.DEBT_PRISON_THRESHOLD]
    просроченные = [
        заём for заём in займы if overdue_days(заём, moment) > порог
    ]
    if not просроченные:
        return None
    return max(просроченные, key=lambda заём: overdue_days(заём, moment))


async def collect(
    session: AsyncSession, constants: Constants, *, now: datetime | None = None
) -> int:
    """Принудительное удержание с просроченных долгов. Возвращает удержанное.

    Вольт говорит «процент дохода», но дохода как измеряемой величины нет:
    приход на счёт бывает от продажи, от казны и от подарка, и разделить их
    нельзя. Удержание с остатка — ближайшее честное приближение, и поведение то
    же: у работающего должника долг тает, у лежащего на печи — нет (D-168).
    """
    moment = now or datetime.now(UTC)
    доля = constants[R.DEBT_WORKOFF_RATE] / PERCENT
    удержано = 0

    займы = (
        await session.execute(select(Loan).where(Loan.state == LoanState.OPEN))
    ).scalars().all()
    for заём in займы:
        if not overdue(constants, заём, moment):
            continue
        await accrue(session, constants, заём, now=moment)
        счёт = await ledger.account_for(
            session, AccountKind.IDENTITY, заём.identity_id
        )
        есть = await ledger.balance(session, счёт.id)
        платёж = min(int(есть * доля), заём.outstanding)
        if платёж <= 0:
            continue

        await _settle(session, заём, счёт, платёж)
        #: Удержание — не платёж должника: просрочка не обнуляется им, иначе
        #: несостоятельный вечно висел бы в льготном периоде.
        if заём.outstanding <= 0:
            заём.state = LoanState.REPAID
            заём.repaid_at = moment
        удержано += платёж
        await events.record(
            session,
            EventKind.DEBT_WITHHELD,
            actor_identity_id=заём.identity_id,
            loan_id=str(заём.id),
            amount=платёж,
            left=заём.outstanding,
        )
    await session.flush()
    return удержано


# --- датчик цен и стерилизация (D-087, D-169) ---------------------------------

#: Имя измерения, под которым индекс ложится в суточные метрики.
PRICE_INDEX = "price_index"


async def price_index(
    session: AsyncSession, constants: Constants, *, now: datetime | None = None
) -> float | None:
    """Индекс цен из сделок игроков. Пусто — сделок не было, мерить нечего.

    Медиана по товару, взвешенная его долей в обороте: одна сделка по нелепой
    цене не должна двигать денежную политику, а хлеб важнее редкого сплава
    ровно настолько, насколько его больше покупают (D-087, D-169).
    """
    from src.models.market import Trade

    moment = now or datetime.now(UTC)
    сутки = timedelta(hours=constants[R.TIME_DAY_TERRA])
    сделки = (
        await session.execute(select(Trade).where(Trade.at >= moment - сутки))
    ).scalars().all()
    if not сделки:
        return None

    по_товарам: dict[str, list[int]] = {}
    оборот: dict[str, int] = {}
    for сделка in сделки:
        по_товарам.setdefault(сделка.type_key, []).append(сделка.price)
        оборот[сделка.type_key] = (
            оборот.get(сделка.type_key, 0) + сделка.price * сделка.amount
        )
    весь_оборот = sum(оборот.values())
    if весь_оборот <= 0:
        return None

    #: Медиана берётся общей: та же, что считает телеметрию. Второй копии
    #: формулы быть не должно — она разошлась бы с первой (D-139).
    from src.telemetry.metrics import median

    индекс = 0.0
    for товар, цены in по_товарам.items():
        индекс += median(цены) * оборот[товар] / весь_оборот
    return индекс


async def sterilize(
    session: AsyncSession, constants: Constants
) -> int:
    """Сжечь излишек резерва сверх `bank.reserve_cap` от оборота (D-169).

    Потолок считается долей от оборотной массы, а не абсолютной суммой: мир
    растёт, и то, что сегодня огромный резерв, через сто суток — мелочь.
    """
    в_резерве = await reserve(session)
    в_обороте = await circulating(session)
    потолок = int(в_обороте * constants[R.BANK_RESERVE_CAP] / PERCENT)
    излишек = в_резерве - потолок
    if излишек <= 0:
        return 0

    genesis = await ledger.account_for(session, AccountKind.GENESIS, None)
    await ledger.transfer(
        session,
        PostingReason.GENESIS,
        debit=(await reserve_account(session)).id,
        credit=genesis.id,
        amount=излишек,
        memo={"сжигание излишка резерва": money_str(излишек)},
    )
    await events.record(
        session,
        EventKind.RESERVE_BURNED,
        amount=излишек,
        reserve=в_резерве - излишек,
        circulating=в_обороте,
    )
    return излишек


# --- кредитная линия города (D-175) -------------------------------------------


async def _turnover_by_city(
    session: AsyncSession, since: datetime
) -> dict[uuid.UUID, int]:
    """Оборот городов за период: по сделкам на их территории.

    Оборот — единственная величина, которую нельзя нарисовать, не проведя
    настоящих сделок с настоящим товаром (D-171).
    """
    from src.engine import city as town
    from src.models.market import Trade
    from src.models.world import Node

    сделки = (
        await session.execute(select(Trade).where(Trade.at >= since))
    ).scalars().all()
    по_городам: dict[uuid.UUID, int] = {}
    чей: dict[uuid.UUID, uuid.UUID | None] = {}
    for сделка in сделки:
        if сделка.node_id not in чей:
            узел = await session.get(Node, сделка.node_id)
            город = None if узел is None else await town.of_node(session, узел)
            чей[сделка.node_id] = None if город is None else город.id
        город_id = чей[сделка.node_id]
        if город_id is None:
            continue
        по_городам[город_id] = по_городам.get(город_id, 0) + int(
            сделка.price * amount_float(сделка.amount)
        )
    return по_городам


# --- Совет городов и ставка (D-087, D-172) ------------------------------------


class NotCouncilTime(BankError):
    """Ставку решает алгоритм: либо городов мало, либо действует блокировка."""


class OutOfCorridor(BankError):
    """Совет спорит с алгоритмом, а не заменяет его: есть коридор."""


async def cities_with_hall(session: AsyncSession) -> int:
    """Сколько на планете городов **с администрацией**.

    Город без ратуши — не орган власти, а точка на карте: считать его при
    передаче ставки значило бы отдать деньги вывескам (D-172).
    """
    from src.engine import city as town
    from src.models.world import Node

    города = (await session.execute(select(City))).scalars().all()
    сколько = 0
    for город in города:
        узел = await session.get(Node, город.node_id)
        if узел is None:  # pragma: no cover — город без узла это баг
            continue
        for свой in (узел, *await _children(session, узел)):
            if await _has_hall(session, town, свой):
                сколько += 1
                break
    return сколько


async def _children(session: AsyncSession, node) -> list:
    from src.models.world import Node

    return list(
        (
            await session.execute(select(Node).where(Node.parent_id == node.id))
        ).scalars().all()
    )


async def _has_hall(session: AsyncSession, town, node) -> bool:
    from src.engine.world import node_container
    from src.models.inventory import Item

    двор = await node_container(session, node)
    имена = (
        await session.execute(
            select(Item.type_key).where(Item.container_id == двор.id).distinct()
        )
    ).scalars().all()
    return town.HALL in имена


async def locked_until(session: AsyncSession) -> datetime | None:
    """До какого момента ставка возвращена алгоритму аварийно (D-172)."""
    решение = (
        await session.execute(
            select(RateDecision).order_by(RateDecision.decided_at.desc()).limit(1)
        )
    ).scalars().first()
    if решение is None or not решение.locked_until:
        return None
    return решение.locked_until


async def council_decides(
    session: AsyncSession, constants: Constants, *, now: datetime | None = None
) -> bool:
    """Решает ли ставку Совет городов прямо сейчас."""
    moment = now or datetime.now(UTC)
    до = await locked_until(session)
    if до is not None and до > moment:
        return False
    порог = constants[R.BANK_COUNCIL_HANDOVER_CITIES]
    return await cities_with_hall(session) >= порог


async def council_set_rate(
    session: AsyncSession,
    constants: Constants,
    city: City,
    by: Identity,
    rate: float,
    *,
    now: datetime | None = None,
) -> RateDecision:
    """Решение Совета по ставке. Голос подаёт город, а не человек (D-172).

    Один город — один голос: собрание городов не собрание акционеров, и
    столица со стартовым преимуществом не должна фиксировать контроль навсегда.
    Здесь исполняется само решение; порядок его принятия — дело Совета.
    """
    from src.engine import city as town

    moment = now or datetime.now(UTC)
    if not await council_decides(session, constants, now=moment):
        raise NotCouncilTime(
            "ставку решает алгоритм: городов с администрацией меньше "
            f"{constants[R.BANK_COUNCIL_HANDOVER_CITIES]:g} либо действует блокировка"
        )
    #: Ставка — вопрос закона, а не казны.
    await town.require(session, by.id, city, Power.LAWS)

    рекомендация, почему = compute_rate(
        constants,
        previous=await key_rate(session, constants),
        inflation=await _inflation(session, constants),
        emission_share=await _emission_share(session, constants, now=moment),
    )
    коридор = constants[R.BANK_COUNCIL_RATE_DEVIATION]
    if abs(rate - рекомендация) > коридор:
        raise OutOfCorridor(
            f"алгоритм рекомендует {рекомендация:.2f}%, отклониться можно на "
            f"{коридор:g} п.п. — просят {rate:.2f}%"
        )
    ставка = max(
        constants[R.BANK_RATE_FLOOR], min(constants[R.BANK_RATE_CAP], rate)
    )

    решение = RateDecision(
        rate=ставка,
        why=(
            f"решение Совета городов ({city.name}); "
            f"алгоритм советовал {рекомендация:.2f}: {почему}"
        ),
        decided_at=moment,
    )
    session.add(решение)
    await session.flush()
    await events.record(
        session,
        EventKind.RATE_DECIDED,
        rate=ставка,
        advised=рекомендация,
        by_council=True,
        city=city.name,
    )
    return решение

# --- кредитный лимит из труда (D-173) ------------------------------------------


async def trust(
    session: AsyncSession, constants: Constants, identity_id: uuid.UUID
) -> float:
    """Доверие 0…1: каждый репорт «дефектная печать» режет его на
    `credit.report_penalty`, но не ниже `credit.trust_floor`.

    Репорты снижают кредит, а не хоронят человека: необратимое делает только
    внеигровой саппорт (D-173).
    """
    репортов = await session.scalar(
        select(func.count()).select_from(DefectReport).where(
            DefectReport.target_identity_id == identity_id
        )
    )
    доля = (PERCENT - constants[R.CREDIT_REPORT_PENALTY] * int(репортов or 0)) / PERCENT
    return max(constants[R.CREDIT_TRUST_FLOOR] / PERCENT, доля)


async def personal_turnover(
    session: AsyncSession,
    constants: Constants,
    identity_id: uuid.UUID,
    *,
    now: datetime | None = None,
) -> int:
    """Оборот продаж личности за `credit.window`, минорными единицами.

    Оборот нельзя нарисовать, не продав настоящий товар настоящему покупателю:
    поэтому лимит считается из него, а не из времени в игре (D-173).
    """
    from src.models.market import Order, Trade

    moment = now or datetime.now(UTC)
    окно = moment - timedelta(days=constants[R.CREDIT_WINDOW])
    сделки = (
        await session.execute(
            select(Trade)
            .join(Order, Order.id == Trade.sell_order_id)
            .where(Order.identity_id == identity_id, Trade.at >= окно)
        )
    ).scalars().all()
    return sum(int(сделка.price * amount_float(сделка.amount)) for сделка in сделки)


async def repaid_total(session: AsyncSession, identity_id: uuid.UUID) -> int:
    """Сумма возвращённых ранее кредитов: кредитная история — актив (D-173)."""
    итог = await session.scalar(
        select(func.coalesce(func.sum(Loan.principal), 0)).where(
            Loan.identity_id == identity_id, Loan.state == LoanState.REPAID
        )
    )
    return int(итог or 0)


async def credit_limit(
    session: AsyncSession,
    constants: Constants,
    identity_id: uuid.UUID,
    *,
    now: datetime | None = None,
) -> tuple[int, str]:
    """Кредитный лимит и объяснение словами — публично, как и ставка (D-030).

    База из вольта, плюс доля оборота продаж, плюс доля возвращённого,
    умножить на доверие и стаж. Труд, а не календарь: время в игре — самое
    дешёвое, что можно нафармить (D-173).
    """
    moment = now or datetime.now(UTC)
    база = money(constants[R.BANK_UNSECURED_LIMIT])
    оборот = await personal_turnover(session, constants, identity_id, now=moment)
    возвращено = await repaid_total(session, identity_id)
    лимит = (
        база
        + int(оборот * constants[R.CREDIT_TURNOVER_SHARE] / PERCENT)
        + int(возвращено * constants[R.CREDIT_REPAID_SHARE] / PERCENT)
    )
    причины = [
        f"база {money_str(база)}",
        f"оборот {money_str(оборот)} за {constants[R.CREDIT_WINDOW]:g} суток",
        f"возвращено ранее {money_str(возвращено)}",
    ]

    #: Стаж — множитель, а не основа: прибавка за историю без просрочек.
    займы = await loans_of(session, identity_id)
    без_просрочек = not any(overdue(constants, заём, moment) for заём in займы)
    if возвращено > 0 and без_просрочек:
        лимит = int(лимит * (1 + constants[R.CREDIT_NO_OVERDUE_BONUS] / PERCENT))
        причины.append("стаж без просрочек")

    вера = await trust(session, constants, identity_id)
    if вера < 1:
        лимит = int(лимит * вера)
        причины.append(f"доверие {вера * PERCENT:.0f}% по репортам")
    return лимит, "; ".join(причины)


async def report_defect(
    session: AsyncSession, reporter: Identity, target: Identity
) -> DefectReport:
    """Указать на дефектную печать. Один репорт от личности на личность."""
    if reporter.id == target.id:
        raise BankError("на себя не жалуются даже по лору")
    существует = (
        await session.execute(
            select(DefectReport).where(
                DefectReport.reporter_identity_id == reporter.id,
                DefectReport.target_identity_id == target.id,
            )
        )
    ).scalar_one_or_none()
    if существует is not None:
        return существует
    репорт = DefectReport(
        reporter_identity_id=reporter.id, target_identity_id=target.id
    )
    session.add(репорт)
    await session.flush()
    await events.record(
        session,
        EventKind.REPORT_FILED,
        actor_identity_id=reporter.id,
        target=target.name,
    )
    return репорт


async def withdraw_report(
    session: AsyncSession, reporter: Identity, target: Identity
) -> bool:
    """Отозвать свой репорт: ошибиться можно, а исправиться — нужно."""
    репорт = (
        await session.execute(
            select(DefectReport).where(
                DefectReport.reporter_identity_id == reporter.id,
                DefectReport.target_identity_id == target.id,
            )
        )
    ).scalar_one_or_none()
    if репорт is None:
        return False
    await session.delete(репорт)
    await session.flush()
    await events.record(
        session,
        EventKind.REPORT_WITHDRAWN,
        actor_identity_id=reporter.id,
        target=target.name,
    )
    return True


# --- линия города и маржа (D-175) ----------------------------------------------


def city_margin(constants: Constants, catalog, city) -> float:
    """Маржа города: код-закон `bank_margin` с потолком `bank.city_margin_cap`."""
    from src.engine import city as town

    сырое = town.law(catalog, city, "bank_margin")
    try:
        маржа = float(сырое)
    except (TypeError, ValueError):
        маржа = 0.0
    return max(0.0, min(constants[R.BANK_CITY_MARGIN_CAP], маржа))


async def city_outstanding(session: AsyncSession, city) -> int:
    """Сколько долга граждан висит на линии этого города перед столицей."""
    итог = await session.scalar(
        select(func.coalesce(func.sum(Loan.outstanding), 0)).where(
            Loan.city_id == city.id, Loan.state == LoanState.OPEN
        )
    )
    return int(итог or 0)


async def city_line(
    session: AsyncSession, constants: Constants, city, *, now: datetime | None = None
) -> tuple[int, int, int]:
    """Линия города: (позволено, занято, свободно), минорными единицами.

    Позволено — `bank.debt_to_turnover_cap` от оборота города за
    `credit.window`. Долг города переживает власть (D-175): смена правителя не
    гасит ничего, иначе «занять, раздать своим, переизбраться» — доминирующая
    стратегия.
    """
    moment = now or datetime.now(UTC)
    окно = moment - timedelta(days=constants[R.CREDIT_WINDOW])
    обороты = await _turnover_by_city(session, окно)
    оборот = обороты.get(city.id, 0)
    позволено = int(оборот * constants[R.BANK_DEBT_TO_TURNOVER_CAP] / PERCENT)
    занято = await city_outstanding(session, city)
    return позволено, занято, max(0, позволено - занято)


# --- тюремный зачёт (D-174) ----------------------------------------------------


async def prison_credit(
    session: AsyncSession,
    constants: Constants,
    city,
    debtor_identity_id: uuid.UUID,
    стоимость: int,
    *,
    now: datetime | None = None,
) -> int:
    """Казна платит справочную стоимость добытого в погашение долга заключённого.

    Круг замыкается (D-174, D-175): руда — городу, деньги казны — в погашение,
    погашение — в резерв столицы. Возвращает, сколько удалось зачесть; ноль —
    казна пуста, и руда остаётся заключённому.
    """
    from src.engine import city as town

    moment = now or datetime.now(UTC)
    казна = await town.treasury(session, city)
    if await ledger.balance(session, казна.id) < стоимость:
        return 0

    должник = await session.get(Identity, debtor_identity_id)
    займы = sorted(
        await loans_of(session, debtor_identity_id), key=lambda заём: заём.taken_at
    )
    зачтено = 0
    остаток = стоимость
    for заём in займы:
        if остаток <= 0:
            break
        платёж = min(остаток, заём.outstanding)
        if платёж <= 0:
            continue
        await repay(
            session, constants, должник, заём, платёж / MONEY_SCALE,
            from_account=казна, now=moment,
        )
        зачтено += платёж
        остаток -= платёж
    if зачтено > 0:
        await events.record(
            session,
            EventKind.PRISON_WORKOFF,
            actor_identity_id=debtor_identity_id,
            city_id=str(city.id),
            amount=зачтено,
        )
    return зачтено


@handler(JobKind.SEIGNIORAGE)
async def seigniorage_cancelled(session: AsyncSession, job: Job) -> None:
    """Сеньораж отменён (D-175): город зарабатывает маржой, а не раздачей.

    Вид задания остаётся в перечислении навсегда — журнал вечен, — а старое
    задание, пережившее отмену механики, закрывается без эффекта.
    """
    return
