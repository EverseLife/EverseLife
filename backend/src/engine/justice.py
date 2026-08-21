"""Court: complaint, case, verdict, enforcement (D-095, D-117, D-166).

The `justice` right was declared, fourteen sanction primitives lay in
`laws.json` -- and the court did not exist. Everything the engine does not
check itself (contract, word-law, demolishing what is not yours) was ensured
by the chain "complaint -> court -> sanction", which had not a single link.

## How it works

A **complaint** is filed in a specific city and costs `justice.court_fee` to
its treasury: the fee is not a barrier but what makes a good court profitable
for the city (D-117). The limitation period is `justice.claim_window` days:
the court is not an archive of grudges.

A **case** is plaintiff, defendant, the substance of the claim in words. The
engine does not interpret the claim: examining it is the judge's work, not code's.

A **verdict** names a sanction from the vault primitives. The engine enforces
three and honestly refuses the rest:

| Sanction | What the engine does |
|---|---|
| `fine` | writes off from the account to the treasury; what is missing is recorded as debt |
| `prison` | holds the body in a node until the term, no longer than `justice.prison_max` |
| `exile` | removes citizenship (D-160): banishment, not death |

**Enforcement does not depend on whether guards are online:** the sanction is
applied at the moment of the verdict, the term is lifted by a journal job.

## What is not here

Appeals, hunting an escapee, recognition of foreign verdicts and the eleven
remaining primitives: confiscation needs an inventory of property, arrest a
reversible freeze, licence revocation the licences themselves. Each of them is
a separate mechanic, not a line in an enumeration.
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

#: Primitives the engine enforces. The rest are named in the header: the
#: sanction list lives in the vault, and the engine enforces exactly what it can.
FINE, PRISON, EXILE = "fine", "prison", "exile"

#: The node property "prison" is a legacy of old worlds (D-174). The new order
#: is the "Penal colony" machine (D-176): the authority builds a prison like any building.
PRISON_NODE = "тюрьма"
#: Thing class from `build/recipes.json` (D-215): a civic-land node with any
#: machine of this class is a penal colony.
KATORGA = "Каторга"
ENFORCED = (FINE, PRISON, EXILE)


async def is_prison(session: AsyncSession, node) -> bool:
    """Whether this node is a prison: the "Penal colony" machine or the old property (D-176)."""
    from src.engine import world

    if (node.properties or {}).get(PRISON_NODE):
        return True
    return await world.has_station(session, node, KATORGA)


async def held(
    session: AsyncSession, constants: Constants, identity_id: uuid.UUID
) -> bool:
    """Whether the prison holds the person: by verdict or by debt (D-166, D-168).

    By this sign the prison printer and the penal face open -- and close for
    everyone else (D-174, D-176).
    """
    from src.engine import bank

    if await imprisoned(session, identity_id) is not None:
        return True
    return await bank.restrained(session, constants, identity_id) is not None


async def prisons_of(session: AsyncSession, city: City) -> list:
    """The city's penal colonies: nodes of its land with the "Penal colony" machine (D-176)."""
    from src.models.world import Node

    nodes = (
        await session.execute(select(Node).where(Node.owner_city_id == city.id))
    ).scalars().all()
    result = []
    for node in nodes:
        if await is_prison(session, node):
            result.append(node)
    return result


class JusticeError(Exception):
    pass


class TooLate(JusticeError):
    """The limitation period has expired: the court is not an archive of grudges."""


class CannotPayFee(JusticeError):
    """The fee is unaffordable. Court costs money, and that is the city's decision."""


class NotJudge(JusticeError):
    """Whoever the city gave the `justice` right judges."""


class Unenforceable(JusticeError):
    """The engine does not enforce such a sanction -- and will not silently pretend to."""


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
    """File a complaint. The fee goes to the city treasury at once (D-117)."""
    moment = now or datetime.now(UTC)
    essence = claim.strip()
    if not essence:
        raise JusticeError("жалоба без сути — не жалоба")
    if plaintiff.id == defendant.id:
        raise JusticeError("на себя не жалуются")
    if happened_at is not None:
        window = timedelta(days=constants[R.JUSTICE_CLAIM_WINDOW])
        if happened_at + window < moment:
            raise TooLate(
                f"с события прошло больше {constants[R.JUSTICE_CLAIM_WINDOW]:g} суток: "
                "срок давности вышел"
            )

    from src.engine import city as town

    duty = money(constants[R.JUSTICE_COURT_FEE])
    account = await ledger.account_for(session, AccountKind.IDENTITY, plaintiff.id)
    if await ledger.balance(session, account.id) < duty:
        raise CannotPayFee(
            f"пошлина суда {money_str(duty)} ₭, а на счету меньше"
        )
    treasury = await town.treasury(session, city)
    await ledger.transfer(
        session,
        PostingReason.COURT_FEE,
        debit=account.id,
        credit=treasury.id,
        amount=duty,
        memo={"пошлина суда": city.name},
    )

    case = Case(
        city_id=city.id,
        plaintiff_identity_id=plaintiff.id,
        defendant_identity_id=defendant.id,
        claim=essence,
        fee=duty,
    )
    session.add(case)
    await session.flush()
    await events.record(
        session,
        EventKind.CASE_OPENED,
        actor_identity_id=plaintiff.id,
        node_id=city.node_id,
        city_id=str(city.id),
        case_id=str(case.id),
        against=defendant.name,
        claim=essence,
        fee=duty,
    )
    return case


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
    prison_node: str | None = None,
    now: datetime | None = None,
) -> Sanction | None:
    """Deliver a verdict. Without a sanction it is an acquittal: there are no hanging cases."""
    from src.engine import city as town

    moment = now or datetime.now(UTC)
    if case.state is not CaseState.OPEN:
        raise JusticeError("дело уже рассмотрено")
    city = await town.by_id(session, case.city_id)
    if city is None:  # pragma: no cover -- a case without a city is a bug
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

    known = {primitive.id for primitive in catalog.laws.sanctions}
    if sanction not in known:
        raise JusticeError(f"нет такой санкции: {sanction}")
    if sanction not in ENFORCED:
        raise Unenforceable(
            f"«{sanction}» движок пока не исполняет: приговор без исполнения — "
            "хуже, чем отказ от приговора"
        )

    defendant = await session.get(Identity, case.defendant_identity_id)
    if defendant is None:  # pragma: no cover -- the identity is eternal
        raise JusticeError("ответчик исчез")

    penalty = await _enforce(
        session,
        constants,
        city,
        case,
        defendant,
        sanction,
        days=days,
        amount=amount,
        prison_node=prison_node,
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
    return penalty


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
    prison_node: str | None = None,
    now: datetime,
) -> Sanction:
    """Enforce a sanction. No guards are needed for that: the engine enforces."""
    from src.engine import city as town

    penalty = Sanction(
        case_id=case.id, city_id=city.id, identity_id=who.id, kind=kind
    )

    if kind == FINE:
        awarded = money(amount or 0)
        account = await ledger.account_for(session, AccountKind.IDENTITY, who.id)
        have = await ledger.balance(session, account.id)
        collected_ = min(have, awarded)
        if collected_ > 0:
            treasury = await town.treasury(session, city)
            await ledger.transfer(
                session,
                PostingReason.FINE,
                debit=account.id,
                credit=treasury.id,
                amount=collected_,
                memo={"штраф по делу": str(case.id)},
            )
        penalty.amount = awarded
        #: What is missing is a debt to the city. Debt collection does not exist
        #: yet, and inventing it here is not allowed: a separate mechanic (D-166).
        penalty.debt = awarded - collected_

    elif kind == PRISON:
        ceiling = constants[R.JUSTICE_PRISON_MAX]
        term = min(float(days or ceiling), ceiling)
        penalty.until = now + timedelta(days=term)
        body = await _body_of(session, who)
        #: Where to imprison is the court's decision (D-176): one penal colony --
        #: there; several -- the judge names which; none -- hold where it caught them.
        cell_ = await _prison_choice(session, city, prison_node)
        if cell_ is not None and body is not None:
            #: Taken away from the machine: the running work freezes where it
            #: was, and waits for the sentence to end (D-209).
            from src.engine import craft

            await craft.freeze(session, body, now=now)
            body.node_id = cell_.id
            penalty.node_id = cell_.id
        else:
            penalty.node_id = None if body is None else body.node_id

    elif kind == EXILE:
        entry = await town.citizenship(session, who.id)
        if entry is not None and entry.city_id == city.id:
            await session.delete(entry)
            await session.flush()

    session.add(penalty)
    await session.flush()
    await events.record(
        session,
        EventKind.SANCTION_APPLIED,
        actor_identity_id=who.id,
        node_id=city.node_id,
        city_id=str(city.id),
        case_id=str(case.id),
        sanction=kind,
        amount=penalty.amount,
        debt=penalty.debt,
        until=None if penalty.until is None else penalty.until.isoformat(),
    )
    if penalty.until is not None:
        await enqueue(
            session,
            JobKind.SANCTION_LIFT,
            penalty.until,
            payload={"sanction": str(penalty.id)},
            dedup_key=f"sanction.lift:{penalty.id}",
        )
    return penalty


@handler(JobKind.SANCTION_LIFT)
async def lift(session: AsyncSession, job: Job) -> None:
    """The term is up: the sanction is lifted by itself, without anybody's participation."""
    penalty = await session.get(Sanction, uuid.UUID(job.payload["sanction"]))
    if penalty is None or penalty.lifted_at is not None:
        return
    penalty.lifted_at = job.run_at
    await session.flush()
    await events.record(
        session,
        EventKind.SANCTION_LIFTED,
        actor_identity_id=penalty.identity_id,
        city_id=str(penalty.city_id),
        sanction=penalty.kind,
    )


async def active(
    session: AsyncSession, identity_id: uuid.UUID, kind: str | None = None
) -> list[Sanction]:
    """Sanctions in force on the person."""
    conditions = [Sanction.identity_id == identity_id, Sanction.lifted_at.is_(None)]
    if kind is not None:
        conditions.append(Sanction.kind == kind)
    lines = (await session.execute(select(Sanction).where(*conditions))).scalars().all()
    now_ = datetime.now(UTC)
    return [
        penalty
        for penalty in lines
        if penalty.until is None or penalty.until > now_
    ]


async def imprisoned(
    session: AsyncSession, identity_id: uuid.UUID
) -> Sanction | None:
    """Imprisonment, if in force. The body is held by the node, not by persuasion."""
    sits = await active(session, identity_id, PRISON)
    return sits[0] if sits else None


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
    """This city's case cards -- what the client shows."""
    result: list[dict] = []
    for case in await cases_of(session, city):
        plaintiff = await session.get(Identity, case.plaintiff_identity_id)
        defendant = await session.get(Identity, case.defendant_identity_id)
        result.append(
            {
                "id": str(case.id),
                "plaintiff": None if plaintiff is None else plaintiff.name,
                "defendant": None if defendant is None else defendant.name,
                "claim": case.claim,
                "state": case.state.value,
                "verdict": case.verdict,
                "opened_at": case.opened_at.isoformat(),
            }
        )
    return result


async def _prison_choice(session: AsyncSession, city: City, prison_node: str | None):
    """The penal colony the verdict sends to. Invents nothing: the choice is the court's."""
    penal_face = await prisons_of(session, city)
    if prison_node is not None:
        chosen = next((node for node in penal_face if node.key == prison_node), None)
        if chosen is None:
            raise JusticeError(f"«{prison_node}» — не каторга этого города")
        return chosen
    if len(penal_face) > 1:
        raise JusticeError(
            "в городе несколько каторг: суд называет, в какую отправить"
        )
    return penal_face[0] if penal_face else None


async def _body_of(session: AsyncSession, who: Identity):
    from src.engine import death

    return await death.alive_body(session, who.id)
