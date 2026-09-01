# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""What a run turns up, and where (D-152, D-254).

Species come from the vault and from the land under the walk: woods where
asked, oil on Terra and never on Pyroxis, a vein with stock and richness, a
plot only inside walls. A named species is exactly what is found and found
worse than the common; and a crowded place searches worse without ever
locking. The run itself and its price live in `test_explore.py`.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from explore_kit import _return, _scout
from src.constants import Catalog, Constants, display_name
from src.constants import registry as R
from src.engine import explore, world
from src.models.world import Edge, Layer, Node, Vein
from src.units import PERCENT


async def _townsman(session: AsyncSession, catalog):
    """The body is in the city: a plot is sought from inside the city, not from the road (D-089)."""
    from src.engine import city as town

    stamp = uuid.uuid4().hex[:8]
    planet = await world.create_node(
        session, f"terra.{stamp}", "Терра", area_m2=1, layer=Layer.SPACE
    )
    delegate = await world.create_node(
        session,
        f"terra.city.{stamp}",
        "Столица",
        area_m2=1,
        layer=Layer.PLANET,
        parent=planet,
    )
    core = await world.create_node(
        session,
        f"terra.city.{stamp}.core",
        "Ядро",
        area_m2=100,
        parent=delegate,
        properties={"ring": 0},
    )
    #: The city's gate: the one node a road beyond the walls may be tied to (D-206).
    from src.engine import travel

    await world.create_node(
        session,
        f"terra.city.{stamp}.gate",
        "Выход из города",
        area_m2=80,
        parent=delegate,
        properties={"ring": 3, travel.EXIT: True},
    )
    city = await town.found(session, catalog, delegate, "Столица")
    core.owner_city_id = city.id
    await session.flush()
    identity = await world.create_identity(session, f"Горожанин-{stamp}")
    body = await world.print_body(session, identity, core)
    return city, core, body


async def test_woods_are_found_when_asked_for(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Asked for woods -- got woods (D-191): felling needs a place that has them (D-177)."""
    _, gate, body = await _scout(session)
    groves = []
    for _ in range(20):
        body.stamina = Decimal(str(constants[R.BODY_STAMINA_MAX]))
        body.node_id = gate.id
        await session.flush()
        await explore.survey(session, constants, body, goal=explore.FOREST)
        await _return(session, body)
        node = await session.get(Node, body.node_id)
        if node.key.startswith("terra.wild."):
            groves.append(node)

    assert groves, "twenty runs for woods gave not a single grove"
    for grove in groves:
        assert grove.properties.get(explore.WOODS) is True


async def test_woods_grow_by_themselves(session: AsyncSession, constants: Constants) -> None:
    """The world gets forested without asking: `explore.forest_share` of finds.

    Nobody's roll here (`who=None`): the share is a property of the world, and
    the memory of D-213 belongs to a scout. What the memory changes is the
    spread, not this mean -- which is why the number below still holds.
    """
    import random

    from src.units import PERCENT

    places = [
        await explore.properties(session, constants, random.Random(seed), vein=False)
        for seed in range(300)
    ]
    wooded = sum(1 for place in places if place[explore.WOODS])
    share = wooded / len(places) * PERCENT
    #: A roll is a roll: the order of magnitude is checked, not an exact number.
    assert abs(share - constants[R.EXPLORE_FOREST_SHARE]) < 15


async def test_aiming_for_woods_narrows_the_chance(constants: Constants, catalog: Catalog) -> None:
    """What is asked for narrows the chance by exactly the world's forest cover."""
    from src.units import PERCENT

    aim = explore.aim_at(constants, catalog, explore.FOREST, None)
    assert aim == pytest.approx(constants[R.EXPLORE_FOREST_SHARE] / PERCENT)
    assert aim < explore.aim_at(constants, catalog, explore.SITE, None)


async def test_species_taken_from_vault(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """There is no "which ores exist" list in the engine: it reads the "Mining" operation."""
    import random

    yield_ = next(op for op in catalog.recipes.operations if op.id == explore.MINING_OPERATION)
    rolled = {
        await explore.species_of(session, constants, catalog, random.Random(grain))
        for grain in range(200)
    }
    assert rolled, "порода не выбирается вовсе"
    assert rolled <= set(yield_.gives)
    #: Iron is mined faster than the rest, so it also turns up more often: the
    #: weight is the pace from `harvest.rates`, there is no second rarity table.
    assert "iron_ore" in rolled


async def test_oil_turns_up_on_terra_and_never_on_pyroxis(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Oil is in Terra's deck (D-252), and Pyroxis has none at all: a planet
    of lava held no organics (D-233). The zero weight drops the species from
    the deck rather than merely making it rare."""
    import random

    from src.models.world import Planet

    on_terra = {
        await explore.species_of(session, constants, catalog, random.Random(grain))
        for grain in range(300)
    }
    assert "crude_oil" in on_terra, "на Терре нефть находится"

    on_pyroxis = {
        await explore.species_of(
            session, constants, catalog, random.Random(grain), planet=Planet.PYROXIS
        )
        for grain in range(300)
    }
    assert "crude_oil" not in on_pyroxis, "на Пироксисе нефти не бывает"


async def test_vein_has_stock_and_richness(session: AsyncSession, constants: Constants) -> None:
    """Veins are finite -- that is irrevocable (pillar P2)."""
    _, _, body = await _scout(session)
    for _ in range(12):
        body.stamina = Decimal(str(constants[R.BODY_STAMINA_MAX]))
        await session.flush()
        await explore.survey(session, constants, body, goal=explore.VEIN)
        await _return(session, body)

    veins = (await session.execute(select(Vein))).scalars().all()
    assert veins, "двенадцать заходов за жилой не дали ни одной"
    richness = constants[R.EXPLORE_VEIN_RICHNESS]
    for vein in veins:
        assert vein.remaining > 0
        assert richness.min <= float(vein.richness) <= richness.max


# --- search goals (D-152) ----------------------------------------------------


async def test_plot_sought_in_city_and_is_civic(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Civic land is not taken -- the authority hands it out (D-089)."""
    city, core, body = await _townsman(session, catalog)
    for _ in range(12):
        body.stamina = Decimal(str(constants[R.BODY_STAMINA_MAX]))
        await session.flush()
        await explore.survey(session, constants, body, goal=explore.LOT)
        await _return(session, body)

    plots = [
        node
        for node in (await session.execute(select(Node))).scalars().all()
        if node.properties.get("plot")
    ]
    assert plots, "двенадцать заходов в городе не дали ни одного участка"
    for plot in plots:
        assert plot.layer is Layer.CITY, "участок стоит в городе, а не в поле"
        assert plot.owner_city_id == city.id, "земля в кольцах — городская"
        assert plot.owner_identity_id is None, "раздаёт её власть, а не находка"
        #: The plot is land, and land has soil (D-126, D-246): the mark alone
        #: used to arrive, and a missing property reads as nought -- every
        #: plot inside every city was barren rock and grew nothing.
        assert "fertility" in plot.properties, "у городского участка нет почвы"
        assert "water" in plot.properties, "у городского участка не разыграна вода"
        assert "wild" not in plot.properties, "земля в кольцах не дикая"
    assert any(float(plot.properties["fertility"]) > 0 for plot in plots), (
        "двенадцать участков подряд без плодородия — это не разыгранное свойство"
    )


async def test_find_beyond_the_walls_hangs_on_the_gate(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """A trail from the city starts at the gate, not where the scout stood (D-206).

    Until this, a run from the trading yard left a trail from the trading yard,
    and the market quietly became a second way out of the city -- which is
    exactly what happened to the capital.
    """
    from src.engine import city as town

    city, core, body = await _townsman(session, catalog)
    gate = await town.gate(session, city)
    assert gate is not None and gate.id != core.id

    for _ in range(12):
        body.stamina = Decimal(str(constants[R.BODY_STAMINA_MAX]))
        body.node_id = core.id
        await session.flush()
        await explore.survey(session, constants, body, goal=explore.VEIN)
        await _return(session, body)

    finds = [
        node
        for node in (await session.execute(select(Node))).scalars().all()
        if node.properties.get("wild")
    ]
    assert finds, "двенадцать заходов не дали ни одной находки"
    edges = (await session.execute(select(Edge))).scalars().all()
    for find in finds:
        ends = {
            edge.node_a_id if edge.node_b_id == find.id else edge.node_b_id
            for edge in edges
            if find.id in (edge.node_a_id, edge.node_b_id)
        }
        assert core.id not in ends, "тропа из города пошла мимо ворот"
        assert gate.id in ends, "находку не привязали к воротам"


async def test_plot_not_sought_outside_walls(session: AsyncSession, constants: Constants) -> None:
    """There is no city built-up area beyond the walls: nothing to seek there.

    The refusal comes **before** leaving: the player must not spend three
    hours and stamina on a goal that is impossible in advance.
    """
    _, _, body = await _scout(session)
    before = float(body.stamina)
    with pytest.raises(explore.ExploreError):
        await explore.survey(session, constants, body, goal=explore.LOT)
    assert float(body.stamina) == before, "отказ не стоит выносливости"


async def test_named_species_is_exactly_what_is_found(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """One seeks not "something" but what is needed."""
    _, _, body = await _scout(session)
    for _ in range(20):
        body.stamina = Decimal(str(constants[R.BODY_STAMINA_MAX]))
        await session.flush()
        await explore.survey(session, constants, body, goal=explore.VEIN, resource="copper_ore")
        await _return(session, body)

    veins = (await session.execute(select(Vein))).scalars().all()
    assert veins, "двадцать заходов за медью не дали ни одной жилы"
    assert {vein.resource for vein in veins} == {"copper_ore"}


async def test_a_found_vein_is_named_by_the_word_not_the_id(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """A node name is world text a player reads, so the id resolves to its word (D-251).

    Wave II made `gives` a list of stable ids, and the name went on being built
    out of whatever `species_of` returned: every vein found was written down as
    «Жила: iron_ore» -- and a node name is **persisted**, so each one stayed
    that way for good.
    """
    _, _, body = await _scout(session)
    for _ in range(20):
        body.stamina = Decimal(str(constants[R.BODY_STAMINA_MAX]))
        await session.flush()
        await explore.survey(session, constants, body, goal=explore.VEIN, resource="iron_ore")
        await _return(session, body)

    found = (
        (
            await session.execute(
                select(Node.name)
                .join(Vein, Vein.node_id == Node.id)
                .where(Vein.resource == "iron_ore")
            )
        )
        .scalars()
        .all()
    )
    assert found, "двадцать заходов за железом не дали ни одной жилы"
    #: The word itself is the vault's («Железная руда»), so it is asked for
    #: rather than spelled out here: a rename in the vault is not a broken test.
    word = display_name("iron_ore").lower()
    assert word != "iron_ore", "таблица имён не загружена -- проверка ничего не проверяет"
    for name in found:
        assert name == f"Жила: {word}"
        #: Not just this one id: no latin letter belongs in a Russian node name,
        #: and any key leaking through would carry one.
        assert not any("a" <= letter.lower() <= "z" for letter in name), name


async def test_rare_found_worse_than_common(constants: Constants, catalog: Catalog) -> None:
    """Otherwise everyone would seek only the most expensive, and exploration would become a
    faucet."""
    iron_ = explore.aim_at(constants, catalog, explore.VEIN, "iron_ore")
    tin = explore.aim_at(constants, catalog, explore.VEIN, "tin_ore")
    blindly = explore.aim_at(constants, catalog, explore.VEIN, None)
    assert blindly == 1.0
    assert iron_ > tin, "редкая порода обязана искаться хуже частой"
    assert 0 < tin <= 1


async def test_nonexistent_species_not_sought(session: AsyncSession, constants: Constants) -> None:
    _, _, body = await _scout(session)
    with pytest.raises(explore.ExploreError):
        await explore.survey(session, constants, body, goal=explore.VEIN, resource="Мифрил")


# --- crowding of the graph (D-207) -------------------------------------------


async def _spokes(session: AsyncSession, hub: Node, howmany: float) -> None:
    """Hang this many nodes on the hub: a star, the shape D-207 exists against."""
    from src.engine import travel

    stamp = uuid.uuid4().hex[:6]
    for index in range(int(howmany)):
        spoke = await world.create_node(
            session,
            f"terra.spoke.{stamp}.{index}",
            f"Луч {index}",
            area_m2=100,
            layer=Layer.PLANET,
            parent=hub.parent_id and await session.get(Node, hub.parent_id),
        )
        await travel.connect(session, hub, spoke, base_seconds=30)


async def test_crowded_node_searches_worse_than_a_roomy_one(
    session: AsyncSession, constants: Constants
) -> None:
    """A star of edges searches worse than a bare node (D-207).

    This is what turns a city outwards: the centre saturates, and the next find
    is sought where edges are few.
    """
    _, roomy, _ = await _scout(session)
    _, crowded, _ = await _scout(session)

    assert await explore.crowding(session, constants, roomy) == 1.0, "у пустого узла тесноты нет"
    await _spokes(session, roomy, constants[R.EXPLORE_CROWDING_FREE])
    assert await explore.crowding(session, constants, roomy) == 1.0, (
        "перекрёсток в норме рёбер ничего не стоит"
    )

    await _spokes(session, crowded, constants[R.EXPLORE_CROWDING_FREE] + 6)
    press = await explore.crowding(session, constants, crowded)
    assert press < 1.0, "звезда обязана искать хуже перекрёстка"
    assert press >= constants[R.EXPLORE_CROWDING_FLOOR] / PERCENT


async def test_crowding_never_locks_a_place(session: AsyncSession, constants: Constants) -> None:
    """The map is eternal (D-007): a crowded place searches badly, not never."""
    _, hub, _ = await _scout(session)
    await _spokes(session, hub, 60)
    assert await explore.crowding(session, constants, hub) == pytest.approx(
        constants[R.EXPLORE_CROWDING_FLOOR] / PERCENT
    )


async def test_neighbours_crowd_too_but_a_chain_does_not(
    session: AsyncSession, constants: Constants
) -> None:
    """Neighbours' edges count -- without the one leading back here.

    Otherwise a chain of nodes would read as a cluster, and the frontier -- a line
    of finds one after another -- would choke on itself.
    """
    from src.engine import travel

    _, hub, _ = await _scout(session)
    stamp = uuid.uuid4().hex[:6]
    chain = hub
    for index in range(6):
        next_ = await world.create_node(
            session,
            f"terra.chain.{stamp}.{index}",
            f"Звено {index}",
            area_m2=100,
            layer=Layer.PLANET,
        )
        await travel.connect(session, chain, next_, base_seconds=30)
        chain = next_
    #: The far end of a chain: one edge of its own, one neighbour with two.
    assert await explore.crowding(session, constants, chain) == 1.0

    #: And now the neighbour becomes a cluster -- the same end gets crowded.
    await _spokes(session, chain, constants[R.EXPLORE_CROWDING_FREE])
    neighbour = await explore.crowding(session, constants, chain)
    await _spokes(session, chain, 6)
    assert await explore.crowding(session, constants, chain) < neighbour


async def test_crowding_is_measured_at_the_anchor_not_at_the_origin(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """A wild find hangs on the city's gate (D-206), so the gate's crowding decides.

    Measuring the node the scout set out from would miss exactly the star the
    gate grows: everything beyond the walls couples to it.
    """
    from src.engine import travel

    _, core, body = await _townsman(session, catalog)
    gate = await travel.gate_of(session, core)
    assert gate is not None

    #: A plot is sought inside the walls and hangs where one stands.
    assert (await explore.anchor_of(session, core, explore.LOT)).id == core.id
    #: Everything else hangs on the gate.
    assert (await explore.anchor_of(session, core, explore.SITE)).id == gate.id

    await _spokes(session, gate, constants[R.EXPLORE_CROWDING_FREE] + 8)
    outlook = await explore.outlook(session, constants, body, goal=explore.SITE)
    assert outlook is not None
    assert outlook["crowding"] < 1.0
    assert outlook["anchor"] == gate.name
    #: A plot in the same city is unaffected: its anchor is the node one stands in.
    plot = await explore.outlook(session, constants, body, goal=explore.LOT)
    assert plot is not None
    assert plot["crowding"] == 1.0
