# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""Cities, offices, laws, votes, courts.

Split out of `api/session.py` (review 2026-08-23, wave 3): the
socket loop stayed there, the commands live by domain.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.commands.common import _alive, _body, _identity, _node, goods_key
from src.api.commands.views import _city, _identity_by_name
from src.api.registry import Refused, command
from src.constants import current, current_catalog
from src.engine import (
    bank,
    emission,
    justice,
    panel,
    vote,
    works_city,
    world,
)
from src.engine import city as town
from src.models.bank import Loan
from src.models.city import Citizen, Office, Power
from src.models.emission import EmissionState
from src.models.identity import Identity
from src.models.justice import Case
from src.models.vote import Vote
from src.models.world import Node, is_plot


@command("city.found")
async def _city_found(state: dict, db: AsyncSession, message: dict) -> dict:
    """Found a city where you stand: a planet node nobody owns, or your own,
    with four machines standing in a building on it.

    Land outside a city belongs to nobody and cannot be bought (D-198): a wild
    node needs no title, whoever comes may build there. What is needed is the
    building and the machines in it -- bioprinter, town hall, market terminal,
    power source (`station.place`); the refusal names whichever is missing.
    The entry threshold is those buildings, not a coin (D-023, D-098, D-159).
    The land then goes to the city: from then on the authority hands it out,
    not the yard owner (D-089).
    """
    body = await _alive(state, db)
    city = await town.establish(
        db, current(), current_catalog(), body, str(message.get("name") or "")
    )
    return {"city": str(city.id), "name": city.name}


@command("city.join")
async def _city_join(state: dict, db: AsyncSession, message: dict) -> dict:
    """Apply for citizenship. What comes of it is decided by the city charter (D-160)."""
    body = await _alive(state, db)
    city = await _city(state, db, message)
    result = await town.join(db, body, city)

    citizen = isinstance(result, Citizen)
    return {
        "citizen": citizen,
        "city": city.name,
        "waiting": not citizen,
    }


@command("city.leave")
async def _city_leave(state: dict, db: AsyncSession, message: dict) -> dict:
    """Leave the city. At once, and only an open loan holds it (D-281).

    Remote, like a vote (D-161): belonging is a record about the person, and
    one does not walk to the hall to stop being of a city.
    """
    identity = await _identity(state, db)
    city = await town.leave(db, identity)
    return {"left": None if city is None else city.name}


@command("city.invite")
async def _city_invite(state: dict, db: AsyncSession, message: dict) -> dict:
    """Invite a person to become a citizen. Right `citizens`."""
    identity = await _identity(state, db)
    city = await _city(state, db, message)
    whom = await _identity_by_name(db, str(message["who"]))
    await town.invite(db, identity, city, whom)
    return {"invited": whom.name}


@command("city.admit")
async def _city_admit(state: dict, db: AsyncSession, message: dict) -> dict:
    """Approve a citizenship application. Right `citizens`."""
    identity = await _identity(state, db)
    city = await _city(state, db, message)
    whom = await _identity_by_name(db, str(message["who"]))
    await town.admit(db, identity, city, whom)
    return {"admitted": whom.name}


@command("city.exile")
async def _city_exile(state: dict, db: AsyncSession, message: dict) -> dict:
    """Exile from the city. A sanction, not a personnel decision: right `justice`."""
    identity = await _identity(state, db)
    city = await _city(state, db, message)
    whom = await _identity_by_name(db, str(message["who"]))
    await town.exile(db, identity, city, whom)
    return {"exiled": whom.name}


@command("city.citizens")
async def _city_citizens(state: dict, db: AsyncSession, message: dict) -> dict:
    """City residents and the application queue. Remote: reference, not a decision."""
    city = await _city(state, db, message)
    residents = []
    for entry in await town.citizens_of(db, city):
        who = await db.get(Identity, entry.identity_id)
        residents.append(
            {
                "name": None if who is None else who.name,
                "since": entry.since.isoformat(),
            }
        )
    orders = []
    for order in await town.requests_of(db, city):
        who = await db.get(Identity, order.identity_id)
        orders.append({"name": None if who is None else who.name, "kind": order.kind})
    return {
        "admission": town.admission(city),
        "citizens": sorted(residents, key=lambda zh: zh["name"] or ""),
        "requests": orders,
    }


@command("city.votes")
async def _city_votes(state: dict, db: AsyncSession, message: dict) -> dict:
    """Ongoing city polls. Remote: can be viewed from anywhere."""
    city = await _city(state, db, message)
    return {"votes": await vote.view(db, city, state["identity_id"])}


async def _poll(db: AsyncSession, message: dict):
    """The poll the message names, and the city whose poll it is.

    The city comes from the **ballot**, not from the ground under the voter:
    a vote is cast from the road, from the mine and from another planet
    (D-161), and reading the city off the body refused whoever was standing
    in none -- and would have answered the wrong city's poll for whoever was
    standing in somebody else's. Whether this person has a voice in this poll
    is the engine's own check (`may_vote_in`), and it is the one that matters.

    **No living body is asked for**, and that is the point rather than an
    oversight: the electorate is captured at convening from the citizens
    (D-160), the dead among them included, so a body waiting to be printed
    that could not answer would be a voice counted into the quorum and unable
    to reach it. An identity in the cloud still holds its account and its
    citizenship (D-012); what it cannot do is anything by hand, and a ballot
    is not by hand.

    Locked: `nominate` reads the candidate list, adds a name and writes the
    whole list back, so two people standing at once lost one of themselves.
    """
    poll = await db.get(Vote, uuid.UUID(str(message["vote"])), with_for_update=True)
    if poll is None:
        raise Refused(key="cmd-no-such-vote")
    city = await town.by_id(db, poll.city_id)
    if city is None:  # pragma: no cover -- a poll of a city that is gone
        raise Refused(key="cmd-no-such-vote")
    return poll, city


@command("city.vote")
async def _city_vote(state: dict, db: AsyncSession, message: dict) -> dict:
    """Vote. A vote is participation, not governing: cast over the Net (D-161)."""
    identity = await _identity(state, db)
    poll, city = await _poll(db, message)
    await vote.cast(db, city, identity, poll, bool(message.get("yes")))
    pro, contra = await vote.standing(db, poll)
    return {"yes": pro, "no": contra}


@command("city.election")
async def _city_election(state: dict, db: AsyncSession, message: dict) -> dict:
    """Convene a ruler election (D-162). Candidates nominate themselves as it goes."""
    identity = await _identity(state, db)
    city = await _city(state, db, message)
    poll = await vote.open_election(db, current(), city, identity)
    return {"vote": str(poll.id), "closes_at": poll.closes_at.isoformat()}


@command("city.recall")
async def _city_recall(state: dict, db: AsyncSession, message: dict) -> dict:
    """Convene a ruler recall. If it passes, the office is vacated and an election follows."""
    identity = await _identity(state, db)
    city = await _city(state, db, message)
    poll = await vote.open_recall(db, current(), city, identity)
    return {"vote": str(poll.id), "closes_at": poll.closes_at.isoformat()}


@command("city.nominate")
async def _city_nominate(state: dict, db: AsyncSession, message: dict) -> dict:
    """Nominate yourself for ruler. Yourself, not on somebody's proposal."""
    identity = await _identity(state, db)
    poll, city = await _poll(db, message)
    await vote.nominate(db, city, identity, poll)
    return {"nominated": identity.name}


@command("city.choose")
async def _city_choose(state: dict, db: AsyncSession, message: dict) -> dict:
    """Cast a vote for a candidate in the election."""
    identity = await _identity(state, db)
    poll, city = await _poll(db, message)
    candidate = await db.get(Identity, uuid.UUID(message["candidate"]))
    if candidate is None:
        raise Refused(key="cmd-no-such-identity")
    await vote.choose(db, city, identity, poll, candidate)
    return {"chosen": candidate.name}


@command("city.council")
async def _city_council(state: dict, db: AsyncSession, message: dict) -> dict:
    """Council membership and how it is assembled (D-164). Remote: reference."""
    city = await _city(state, db, message)
    places = []
    for place in await vote.council_of(db, city):
        who = await db.get(Identity, place.identity_id)
        places.append({"name": None if who is None else who.name, "how": place.how})
    return {
        "mode": vote.council_mode(city),
        "seats": vote.council_seats(city),
        "members": places,
    }


@command("city.council_seat")
async def _city_council_seat(state: dict, db: AsyncSession, message: dict) -> dict:
    """Appoint to the council or vacate a seat. Only where seats are appointed."""
    identity = await _identity(state, db)
    city = await _city(state, db, message)
    whom = await _identity_by_name(db, str(message["who"]))
    if message.get("out"):
        await town.require(db, identity.id, city, Power.OFFICES)
        removed = await vote.vacate(db, city, whom)
        return {"vacated": removed}
    await vote.appoint_to_council(db, city, identity, whom)
    return {"seated": whom.name}


@command("city.council_election")
async def _city_council_election(state: dict, db: AsyncSession, message: dict) -> dict:
    """Convene a council election: as many win as there are seats."""
    identity = await _identity(state, db)
    city = await _city(state, db, message)
    poll = await vote.open_council_election(db, current(), city, identity)
    return {"vote": str(poll.id), "closes_at": poll.closes_at.isoformat()}


@command("city.sue")
async def _city_sue(state: dict, db: AsyncSession, message: dict) -> dict:
    """File a complaint with the city court. The fee goes to the treasury at once (D-117)."""
    identity = await _identity(state, db)
    city = await _city(state, db, message)
    defendant = await _identity_by_name(db, str(message["who"]))
    case = await justice.sue(
        db, current(), city, identity, defendant, str(message.get("claim") or "")
    )
    return {"case": str(case.id)}


@command("city.judge")
async def _city_judge(state: dict, db: AsyncSession, message: dict) -> dict:
    """Deliver a verdict. Without a sanction it is an acquittal: there are no hanging cases."""
    identity = await _identity(state, db)
    case = await db.get(Case, uuid.UUID(message["case"]))
    if case is None:
        raise Refused(key="cmd-no-such-case")
    sanction = message.get("sanction") or None
    penalty = await justice.judge(
        db,
        current(),
        current_catalog(),
        identity,
        case,
        sanction=None if sanction is None else str(sanction),
        days=None if message.get("days") is None else float(message["days"]),
        amount=None if message.get("amount") is None else float(message["amount"]),
        verdict=str(message.get("verdict") or ""),
        #: Where to imprison when there are several penal faces -- the court names it (D-176).
        prison_node=None if message.get("prison") is None else str(message["prison"]),
    )
    return {"judged": case.state.value, "sanction": None if penalty is None else penalty.kind}


@command("city.cases")
async def _city_cases(state: dict, db: AsyncSession, message: dict) -> dict:
    """City cases and sanction primitives from the vault. Remote: reference."""
    city = await _city(state, db, message)
    return {
        "cases": await justice.view(db, city),
        "sanctions": [
            {
                "id": primitive.id,
                "name": primitive.name,
                "enforced": primitive.id in justice.ENFORCED,
            }
            for primitive in current_catalog().laws.sanctions
        ],
        #: The city's penal faces (D-176): with several, the court names which
        #: one to send to -- the client needs the list.
        "prisons": [
            {"key": node.key, "name": node.name} for node in await justice.prisons_of(db, city)
        ],
    }


async def _ours(db: AsyncSession, city, loan: Loan) -> bool:
    """Whether this loan is the city's business -- and that is its line, only.

    A loan issued through the city sits on its line with the capital (D-175),
    and repaying it frees that line. Nothing else makes a loan a city's: the
    treasury may not pay for somebody whose debt it never stood behind.

    Whose citizen the debtor is **now** used to count as well, and the owner
    struck that out: the money in this loan is the other city's, and the city
    that took the debtor in has no share in it. The case is not hypothetical
    since D-281 -- one leaves no city while owing it, so a debtor abroad is
    somebody's exile, and their debt stays with the city that exiled them.
    That is the price of exiling a debtor, and it is meant to be felt.
    """
    return loan.city_id == city.id


@command("city.bail")
async def _city_bail(state: dict, db: AsyncSession, message: dict) -> dict:
    """The city settles a debt of its own citizen out of the treasury (D-175, D-283).

    In person and by treasury right: spending is an authority decision (D-155).

    Two different deeds behind one door since D-283, and whose money is in the
    loan tells them apart. Against the capital's -- a loan from before that
    decision -- the treasury pays and money moves. Against the city's own the
    treasury would be paying itself, so nothing moves and the debt is
    forgiven: that costs the city its claim, which is real, but it costs the
    debtor nothing, so it must not buy them a credit limit (D-280).
    """

    identity = await _identity(state, db)
    body = await _alive(state, db)
    city = await _city(state, db, message)
    await town.require_at_hall(db, body, city)
    await town.require(db, identity.id, city, Power.TREASURY)
    loan = await db.get(Loan, uuid.UUID(message["loan"]))
    if loan is None:
        raise Refused(key="cmd-no-such-loan")
    #: Its own line or its own citizen, and no one else's (D-280). The treasury
    #: used to settle any loan by number, and under D-280 every such payment
    #: also buys the debtor a credit limit -- so a city could raise a stranger's
    #: limit with public money, and get its share of the margin back on top.
    if not await _ours(db, city, loan):
        raise Refused(key="cmd-loan-not-ours")
    treasury = await town.treasury(db, city)
    forgiven = loan.identity_id is not None and loan.city_id == city.id
    paid = await bank.repay(
        db,
        current(),
        identity,
        loan,
        None if message.get("amount") is None else float(message["amount"]),
        from_account=treasury,
        earns_history=not forgiven,
    )
    return {"paid": paid, "left": loan.outstanding, "forgiven": forgiven}


@command("city.survey")
async def _city_survey(state: dict, db: AsyncSession, message: dict) -> dict:
    """City summary: charter, laws, offices, treasury and own powers.

    Remote read: city figures are not tied to a place. Any city may be viewed --
    your own by body, or one named by node key.
    """
    city = await _city(state, db, message)
    summary = await town.survey(db, current(), current_catalog(), city)
    summary["powers"] = sorted(await town.powers_of(db, state["identity_id"], city))
    #: Whether decisions are made here: governing is in-person (D-155), and the
    #: client needs to know whether to show buttons or send you to the town hall.
    body = await _body(db, state["identity_id"])
    summary["at_hall"] = False
    if body is not None:
        node = await db.get(Node, body.node_id)
        summary["at_hall"] = (
            node is not None
            and node.owner_city_id == city.id
            and (await world.has_station(db, node, town.HALL))
        )
    #: Free and allotted plots: land allotment is the first thing people enter
    #: the administration for (D-089).
    plots = (await db.execute(select(Node).where(Node.owner_city_id == city.id))).scalars().all()
    summary["lots"] = [
        {
            "key": node.key,
            "name": node.name,
            "area": float(node.area_m2),
            "owner": None if node.owner_identity_id is None else str(node.owner_identity_id),
            "free": node.owner_identity_id is None and is_plot(node),
        }
        for node in plots
        if is_plot(node)
    ]
    summary["citizens"] = await _citizens(db)
    #: The mint (D-270): the flag and the counter travel only with the capital
    #: -- a city that is not it has nothing to say here (D-225).
    if city.capital:
        summary["capital"] = True
        summary["emission"] = await emission.view(
            db, current(), city, state["identity_id"], now=datetime.now(UTC)
        )
    return {"city": summary}


@command("city.panel")
async def _city_panel(state: dict, db: AsyncSession, message: dict) -> dict:
    """The city's economic panel. Remote read (D-140).

    The public snapshot is visible to all, guests included: prices and turnover
    are common knowledge (D-047). The full set with the treasury by grounds --
    to those with the `dashboard` right. Nothing personal in either snapshot.
    """
    city = await _city(state, db, message)
    full = await town.may(db, state["identity_id"], city, Power.DASHBOARD)
    summary = await panel.collect(db, current(), city, full=full)
    summary["full"] = full
    return {"panel": summary}


@command("city.law")
async def _city_law(state: dict, db: AsyncSession, message: dict) -> dict:
    """Write a code-law. In person and by narrow right (D-154, D-155)."""
    identity = await _identity(state, db)
    city = await _city(state, db, message)
    await town.set_law(
        db,
        current(),
        current_catalog(),
        identity,
        city,
        str(message["law"]),
        _law_value(message["value"]),
        body=await _body(db, identity.id),
    )
    return {"law": message["law"], "value": message["value"]}


def _law_value(value: object) -> str:
    """A law is stored as text, and a law that is a table must stay readable.

    Half the code-laws are numbers and words, and those are their own text. The
    other half are tables and lists -- duties by goods, banned items -- and the
    client sends those as JSON, which arrives here as a dict or a list. Handed
    to `str()` they became a Python repr with single quotes: valid Python,
    invalid JSON, and unreadable to everybody afterwards -- `customs.rates`
    could not parse it and answered "no rates", the panel could not parse it
    and drew "the border is open". A duty entered by the authority quietly did
    nothing at all. So a table goes back to the JSON it arrived as.
    """
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False)


@command("city.charter")
async def _city_charter(state: dict, db: AsyncSession, message: dict) -> dict:
    """Answer a charter question."""
    identity = await _identity(state, db)
    city = await _city(state, db, message)
    param = message.get("param")
    await town.set_charter(
        db,
        current_catalog(),
        identity,
        city,
        str(message["question"]),
        str(message["option"]),
        None if param is None else float(param),
        body=await _body(db, identity.id),
    )
    return {"question": message["question"], "option": message["option"]}


@command("city.about")
async def _city_about(state: dict, db: AsyncSession, message: dict) -> dict:
    """Rewrite the city's word to newcomers (D-183)."""
    identity = await _identity(state, db)
    city = await _city(state, db, message)
    await town.describe(
        db,
        identity,
        city,
        str(message.get("text") or ""),
        body=await _body(db, identity.id),
    )
    return {"about": city.about}


@command("city.appoint")
async def _city_appoint(state: dict, db: AsyncSession, message: dict) -> dict:
    """Appoint to an office. Only what you have yourself can be given."""
    identity = await _identity(state, db)
    city = await _city(state, db, message)
    to_whom = await _identity_by_name(db, str(message["whom"]))
    #: A right is a string: broad (`treasury`) or narrow (`law:import_duty`).
    #: No need to check the list here: the engine matches rights against what
    #: the appointer has, and a nonexistent right simply opens nothing.
    powers = tuple(str(raw) for raw in message.get("powers") or ())
    office = await town.appoint(
        db,
        identity,
        city,
        to_whom,
        title=str(message.get("title") or "Должность"),
        powers=powers,
        body=await _body(db, identity.id),
    )
    return {"office": str(office.id), "whom": to_whom.name}


@command("city.revoke")
async def _city_revoke(state: dict, db: AsyncSession, message: dict) -> dict:
    """Strip an office: `office` is its id. Right `offices` or the ruler's (D-162)."""
    identity = await _identity(state, db)
    city = await _city(state, db, message)
    office = await db.get(Office, uuid.UUID(message["office"]))
    if office is None:
        raise Refused(key="cmd-no-such-office")
    await town.revoke(db, identity, city, office, body=await _body(db, identity.id))
    return {"revoked": str(office.id)}


@command("city.spend")
async def _city_spend(state: dict, db: AsyncSession, message: dict) -> dict:
    """Pay from the treasury. Salary, reward and contract are one posting."""
    identity = await _identity(state, db)
    city = await _city(state, db, message)
    to_whom = await _identity_by_name(db, str(message["whom"]))
    total = int(message["amount"])
    await town.spend(
        db,
        identity,
        city,
        to_whom,
        total,
        memo=str(message.get("memo") or ""),
        body=await _body(db, identity.id),
    )
    return {"spent": total, "whom": to_whom.name}


@command("city.allot")
async def _city_allot(state: dict, db: AsyncSession, message: dict) -> dict:
    """Allot a civic plot to a resident: one's own home starts here (D-089)."""
    identity = await _identity(state, db)
    city = await _city(state, db, message)
    plot = await _node(db, str(message["node"]))
    to_whom = await _identity_by_name(db, str(message["whom"]))
    await town.allot(db, identity, city, plot, to_whom, body=await _body(db, identity.id))
    return {"allotted": plot.key, "whom": to_whom.name}


# --- city orders on the works board and the treasury as a borrower (D-248) ----


@command("city.works_repair")
async def _city_works_repair(state: dict, db: AsyncSession, message: dict) -> dict:
    """Order the mending of the city plot.

    The offer covers the materials the worker walls in; the open order
    licenses anyone to repair here (D-248).
    """
    order = await works_city.post_repair_order(
        db,
        current(),
        await _city(state, db, message),
        await _identity(state, db),
        await _alive(state, db),
        await _node(db, str(message.get("node") or "")),
        offer=float(message.get("offer") or 0),
    )
    return {"order": str(order.id), "tariff": order.tariff}


@command("city.works_build")
async def _city_works_build(state: dict, db: AsyncSession, message: dict) -> dict:
    """Order a building on the city plot.

    The licence covers kind, footprint and floors to the letter; the offer
    compensates the worker's materials (D-248).
    """
    order = await works_city.post_build_order(
        db,
        current(),
        await _city(state, db, message),
        await _identity(state, db),
        await _alive(state, db),
        await _node(db, str(message.get("node") or "")),
        building_kind=str(message.get("kind") or ""),
        footprint=float(message.get("footprint") or 0),
        floors=int(message.get("floors") or 1),
        offer=float(message.get("offer") or 0),
    )
    return {"order": str(order.id), "tariff": order.tariff}


@command("city.works_fuel")
async def _city_works_fuel(state: dict, db: AsyncSession, message: dict) -> dict:
    """Order fuel hauled to a city station.

    The price per unit is the city's own offer; the fund subsidises the haul
    labour by the cargo's mass (D-248).
    """
    order = await works_city.post_fuel_order(
        db,
        current(),
        current_catalog(),
        await _city(state, db, message),
        await _identity(state, db),
        await _alive(state, db),
        await _node(db, str(message.get("node") or "")),
        type_key=goods_key(message["fuel"]) if message.get("fuel") else "",
        amount=float(message.get("amount") or 0),
        price_per_unit=float(message.get("price") or 0),
    )
    return {"order": str(order.id), "tariff": order.tariff}


@command("city.works_cancel")
async def _city_works_cancel(state: dict, db: AsyncSession, message: dict) -> dict:
    """Withdraw the city's order.

    The unpaid remainder returns to its pockets and the licence closes with
    the order (D-248).
    """
    try:
        order_id = uuid.UUID(str(message.get("order") or ""))
    except ValueError as bad:
        raise Refused(key="cmd-no-such-work-order") from bad
    to_treasury, to_fund = await works_city.cancel_city_order(
        db,
        await _city(state, db, message),
        await _identity(state, db),
        await _alive(state, db),
        order_id,
    )
    return {"returned": to_treasury, "to_fund": to_fund}


@command("city.loans")
async def _city_loans(state: dict, db: AsyncSession, message: dict) -> dict:
    """The treasury's own loans and the city line. Remote: public figures (D-030)."""
    city = await _city(state, db, message)
    constants = current()
    permitted, occupied, free = await bank.city_line(db, constants, city)
    return {
        "line": {"permitted": permitted, "occupied": occupied, "free": free},
        "loans": [
            {
                "id": str(loan.id),
                "principal": loan.principal,
                "outstanding": loan.outstanding + bank.accruable(constants, loan),
                "rate": float(loan.rate),
                "taken_at": loan.taken_at.isoformat(),
            }
            for loan in await works_city.treasury_loans(db, city)
        ],
    }


@command("city.borrow")
async def _city_borrow(state: dict, db: AsyncSession, message: dict) -> dict:
    """The treasury borrows from the CB (D-248): key rate, no margin, on the city line."""
    loan = await works_city.borrow_for_works(
        db,
        current(),
        await _city(state, db, message),
        await _identity(state, db),
        await _alive(state, db),
        float(message.get("amount") or 0),
    )
    return {"loan": str(loan.id), "rate": float(loan.rate)}


@command("city.emission_propose")
async def _city_emission_propose(state: dict, db: AsyncSession, message: dict) -> dict:
    """Propose to print `amount` into the capital's treasury (D-270); the
    proposer's hand is the first signature, and one holder alone prints at once."""
    proposal = await emission.propose(
        db,
        current(),
        await _city(state, db, message),
        await _identity(state, db),
        await _alive(state, db),
        float(message.get("amount") or 0),
    )
    return {"proposal": str(proposal.id), "printed": proposal.state is EmissionState.PRINTED}


@command("city.emission_sign")
async def _city_emission_sign(state: dict, db: AsyncSession, message: dict) -> dict:
    """Sign a proposal to print (D-270); the hand that completes the share prints."""
    try:
        proposal_id = uuid.UUID(str(message.get("proposal") or ""))
    except ValueError as bad:
        raise Refused(key="cmd-no-such-proposal") from bad
    proposal = await emission.sign(
        db,
        current(),
        await _city(state, db, message),
        await _identity(state, db),
        await _alive(state, db),
        proposal_id,
    )
    return {"proposal": str(proposal.id), "printed": proposal.state is EmissionState.PRINTED}


@command("city.loan_repay")
async def _city_loan_repay(state: dict, db: AsyncSession, message: dict) -> dict:
    """Repay the treasury's own loan from the treasury."""
    city = await _city(state, db, message)
    try:
        loan_id = uuid.UUID(str(message.get("loan") or ""))
    except ValueError as bad:
        raise Refused(key="cmd-no-such-loan") from bad
    loan = await db.get(Loan, loan_id)
    if loan is None:
        raise Refused(key="cmd-no-such-loan")
    paid = await works_city.repay_for_works(
        db,
        current(),
        city,
        await _identity(state, db),
        await _alive(state, db),
        loan,
        None if message.get("amount") is None else float(message["amount"]),
    )
    return {"paid": paid, "left": loan.outstanding}


async def _citizens(db: AsyncSession) -> list[str]:
    """Who can be appointed or paid at all. Names are public (D-058)."""
    rows = await db.execute(select(Identity.name).order_by(Identity.name))
    return [row[0] for row in rows]
