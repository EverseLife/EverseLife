"""The world's skeleton: node, identity, body, property, knowledge.

Checked is the main distinction of the whole model: **knowledge lives in the
identity, property in the body** (D-011, D-012, D-033). All behaviour on
death follows from it, and a mistake here is costlier than anywhere else.
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Constants
from src.constants import registry as R
from src.engine import world
from src.models.event import Event
from src.models.identity import Knowledge
from src.models.inventory import Item
from src.units import amount_float


async def _settled_node(session: AsyncSession):
    node = await world.create_node(session, "terra.capital", "Столица", area_m2=200)
    identity = await world.create_identity(session, "Тэрн")
    body = await world.print_body(session, identity, node)
    return node, identity, body


async def test_body_printed_with_full_stamina(
    session: AsyncSession, constants: Constants
) -> None:
    _, _, body = await _settled_node(session)
    await session.commit()
    assert float(body.stamina) == constants[R.BODY_STAMINA_MAX]


async def test_body_has_inventory_from_birth(session: AsyncSession) -> None:
    _, _, body = await _settled_node(session)
    container = await world.body_container(session, body)
    assert container.owner_id == body.id


async def test_knowledge_copied_once(session: AsyncSession) -> None:
    """The Library does not refuse, but does not create a second copy in the head either (D-053)."""
    _, identity, _ = await _settled_node(session)

    assert await world.learn(session, identity, "Гвозди") is not None
    assert await world.learn(session, identity, "Гвозди") is None
    await session.commit()

    total = await session.scalar(
        select(func.count()).select_from(Knowledge).where(Knowledge.identity_id == identity.id)
    )
    assert total == 1


async def test_item_appearance_must_have_ground(session: AsyncSession) -> None:
    """Matter is not created out of nothing: any arrival has a named source (I1)."""
    node, identity, body = await _settled_node(session)
    container = await world.body_container(session, body)

    await world.grant_item(
        session, container, "Железная руда", amount=12.5, quality=60, origin="сценарий отладки"
    )
    await session.commit()

    item = (await session.execute(select(Item))).scalar_one()
    assert item.type_key == "Железная руда"
    assert amount_float(item.amount) == 12.5
    assert float(item.condition) == float(item.condition_cap)

    appearance = (
        await session.execute(select(Event).where(Event.kind == "item.created"))
    ).scalar_one()
    assert appearance.payload["origin"] == "сценарий отладки"


async def test_every_world_change_lands_in_journal(session: AsyncSession) -> None:
    await _settled_node(session)
    await session.commit()

    kinds = set(
        (await session.execute(select(Event.kind))).scalars().all()
    )
    assert {"identity.created", "body.printed"} <= kinds


async def test_event_remembers_which_numbers_it_happened_on(
    session: AsyncSession, constants: Constants
) -> None:
    """Examining an old episode after a balance edit otherwise proves nothing (D-065)."""
    await _settled_node(session)
    await session.commit()

    event = (
        await session.execute(select(Event).where(Event.kind == "body.printed"))
    ).scalar_one()
    assert event.constants_digest == constants.digest
