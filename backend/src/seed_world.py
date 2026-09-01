# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The starting world's layout as data, and its interpreter (D-243).

`seed.py` used to hold the capital node by node in code, and editing the world
meant editing a thousand-line script. Now the layout -- nodes, edges with their
seconds, veins, machines and stocks -- lives in the vault (`data/world.yaml`,
edited visually or by hand, checked by `tools/build.py`), and the engine reads
the snapshot `build/world.json` next to the constants it already reads.

This module is the interpreter. It owns two things and nothing else:

* **laying the layout onto a session** -- `apply` walks the scenario in file
  order and adds what is missing. A node found by key is left alone (the world
  is eternal, D-007); a missing one is created whole, with its veins and
  stocks. Machines are ensured by name every run, so a world laid out before a
  machine was added to the scenario catches up on the next deploy -- exactly
  what the hand-written catch-up blocks used to do one machine at a time;
* **assembly by recipe** (D-216): a machine is not conjured but built from the
  vault's composition, input by input down to raw material. Only raw material
  arrives from nowhere, with a named ground (pillar P1). A recipe without a
  composition, an input nobody makes, a circle in the ladder -- each stops the
  world from being created, loudly.

What the scenario deliberately does not know -- planets and orbits, the city
as an institution, founders, the surfaces of Pyroxis and Aurora -- stays in
`seed.py` and `seed_surfaces.py`: those are rules, not layout.
"""

from __future__ import annotations

import json
import logging
import random
from dataclasses import dataclass, field, replace
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import ConstantError, Constants, current_catalog, load_renames
from src.constants import registry as R
from src.constants.renames import RenameTable
from src.engine import goods, places, ruins, travel, world
from src.models.inventory import Item
from src.models.world import Layer, Node, Planet, Surface
from src.settings import settings
from src.units import amount as to_amount
from src.units import amount_float

log = logging.getLogger("everselife.seed")

#: The edge length meaning "by the node's distance" (D-180): the transit is
#: priced by how far beyond the walls the destination lies, not by a number
#: somebody typed.
BY_REACH = "reach"


@dataclass(frozen=True, slots=True)
class Stock:
    """A thing lying somewhere from the world's first day."""

    name: str
    amount: float
    quality: float
    origin: str
    #: Kept in stock by the catch-up too: a world that ran out is restocked on
    #: deploy. For everything else the seed lays the stock once and the world
    #: lives its own life from then on.
    ensure: bool = False


@dataclass(frozen=True, slots=True)
class Machine:
    """A station or furniture to stand in the node, assembled by recipe."""

    #: Exactly one of the two: a concrete thing, or "any of the class" (D-215).
    name: str | None
    thing_class: str | None
    quality: float


@dataclass(frozen=True, slots=True)
class VeinSpec:
    resource: str
    richness: float
    remaining: float


@dataclass(frozen=True, slots=True)
class NodeSpec:
    key: str
    name: str
    layer: Layer
    planet: Planet
    parent: str | None
    anchor: str | None
    area_m2: float
    #: A pinned place on the group's map, or None -- the engine seats the node
    #: next to its anchor (D-237).
    place: tuple[float, float] | None
    #: An institutional city is founded here (D-154). The founding itself is
    #: `seed.py`'s: a charter and a treasury are rules, not layout.
    city: bool
    properties: dict
    machines: tuple[Machine, ...]
    relics: tuple[str, ...]
    veins: tuple[VeinSpec, ...]
    items: tuple[Stock, ...]


@dataclass(frozen=True, slots=True)
class EdgeSpec:
    a: str
    b: str
    #: None -- a city step, rolled; BY_REACH -- by distance (D-180); a number
    #: -- exactly that many seconds.
    seconds: float | str | None
    surface: Surface


@dataclass(frozen=True, slots=True)
class Scenario:
    nodes: tuple[NodeSpec, ...]
    edges: tuple[EdgeSpec, ...]
    #: Starting inventories by identity name. The identities themselves are
    #: `seed.py`'s business (D-187) -- the scenario only says what they carry.
    pockets: dict[str, tuple[Stock, ...]]


@dataclass(slots=True)
class Applied:
    """What the layout pass left behind, for the steps that follow it."""

    #: Every scenario node as it stands in the session, found or created.
    nodes: dict[str, Node] = field(default_factory=dict)
    #: Keys created by this very run: veins and starting stocks go only here.
    created: set[str] = field(default_factory=set)

    def city_nodes(self, scenario: Scenario) -> list[Node]:
        return [self.nodes[spec.key] for spec in scenario.nodes if spec.city]

    def descendants(self, scenario: Scenario, root: str) -> list[Node]:
        """The scenario nodes under this one, any depth -- a city's own land."""
        family = {root}
        found: list[Node] = []
        for spec in scenario.nodes:
            if spec.parent in family:
                family.add(spec.key)
                found.append(self.nodes[spec.key])
        return found


#: Water is the one property whose VALUE is a word of the vault ("нет"/"река");
#: everything else is numbers and booleans.
_WATER_VALUES = {"нет": world.NO_WATER, "река": world.RIVER}


def _renamed_properties(properties: dict, renames: RenameTable) -> dict:
    keys = renames.node_properties
    out = {}
    for key, value in properties.items():
        key = keys.get(key, key)
        if key == "water" and isinstance(value, str):
            value = _WATER_VALUES.get(value, value)
        out[key] = value
    return out


def load_scenario(build_dir: Path | None = None) -> Scenario:
    """The layout snapshot the vault built (`build/world.json`).

    The vault writes the layout in its own language -- Russian names for
    machines, veins, stocks and property words. This is the world's side of
    the D-251 load-time seam: everything is translated to ids right here, and
    the seed below never sees a Russian identifier.
    """
    source = build_dir or settings().vault_build_path
    path = source / "world.json"
    renames = load_renames(source)
    #: Not `goods`: that name is the engine module imported above.
    thing = renames.goods_id

    def klass(name: str | None) -> str | None:
        if name is None:
            return None
        return renames.classes.get(name, name)

    doc = json.loads(path.read_text(encoding="utf-8"))
    return Scenario(
        nodes=tuple(
            NodeSpec(
                key=node["key"],
                name=node["name"],
                layer=Layer(node["layer"]),
                planet=Planet(node["planet"]),
                parent=node.get("parent"),
                anchor=node.get("anchor"),
                area_m2=float(node["area_m2"]),
                place=(
                    (float(node["place"]["x"]), float(node["place"]["y"]))
                    if node.get("place")
                    else None
                ),
                city=bool(node.get("city")),
                properties=_renamed_properties(dict(node.get("properties") or {}), renames),
                machines=tuple(
                    Machine(
                        name=thing(machine["name"]) if machine.get("name") else None,
                        thing_class=klass(machine.get("class")),
                        quality=float(machine["quality"]),
                    )
                    for machine in node.get("machines") or []
                ),
                relics=tuple(klass(relic) for relic in node.get("relics") or []),
                veins=tuple(
                    VeinSpec(
                        resource=thing(vein["resource"]),
                        richness=float(vein["richness"]),
                        remaining=float(vein["remaining"]),
                    )
                    for vein in node.get("veins") or []
                ),
                items=_renamed_stocks(_stocks(node.get("items") or []), thing),
            )
            for node in doc["nodes"]
        ),
        edges=tuple(
            EdgeSpec(
                a=edge["a"],
                b=edge["b"],
                seconds=edge.get("seconds"),
                surface=Surface(edge.get("surface") or "road"),
            )
            for edge in doc["edges"]
        ),
        pockets={
            owner: _renamed_stocks(_stocks(grants), thing)
            for owner, grants in (doc.get("pockets") or {}).items()
        },
    )


def _stocks(items: list[dict]) -> tuple[Stock, ...]:
    return tuple(
        Stock(
            name=item["name"],
            amount=float(item.get("amount", 1)),
            quality=float(item["quality"]),
            origin=item["origin"],
            ensure=bool(item.get("ensure")),
        )
        for item in items
    )


def _renamed_stocks(stocks: tuple[Stock, ...], goods) -> tuple[Stock, ...]:
    """Stock names to D-251 ids; the rest of the row travels as written."""
    return tuple(replace(stock, name=goods(stock.name)) for stock in stocks)


def one_of(thing_class: str) -> str:
    """A concrete thing of the class: a world holds things, not classes (D-215).

    The engine binds behaviour to a class -- «Терминал», «Верфь» -- and the
    scenario may name the class instead of a member. Asked through the catalog,
    so a rename in the vault carries the layout with it.

    A **made** thing, never a relic (D-232): what a city stands on is
    assembled by recipe, and relics are laid by `relics`, not here.
    """
    members = current_catalog().recipes.made_of_class(thing_class)
    if not members:
        raise RuntimeError(f"класс «{thing_class}» пуст: миру нечего поставить")
    return members[0]


async def apply(session: AsyncSession, constants: Constants) -> tuple[Scenario, Applied]:
    """Lay the vault's scenario onto the session. What `seed` and `catch_up` call."""
    scenario = load_scenario()
    return scenario, await lay(session, constants, scenario)


async def lay(session: AsyncSession, constants: Constants, scenario: Scenario) -> Applied:
    """Lay a scenario onto the session: add what is missing, touch nothing else.

    Idempotent by construction, and therefore one code path for the first seed
    and every catch-up: a fresh world is simply the case where everything is
    missing. A node found by key keeps whatever the world has done to it; a
    created one arrives whole -- veins, stocks, its place on the map. Machines
    and `ensure` stocks are topped up on every run, which is how an old world
    learns a machine the scenario gained after it was laid.

    Takes the scenario rather than reading it, so the rule can be tested on a
    layout written for the test: what this function promises is about *any*
    scenario, and a test that could only phrase itself in terms of the
    capital's own workshop would go stale the day the capital is rearranged.
    """
    applied = Applied()
    for spec in scenario.nodes:
        node = (
            await session.execute(select(Node).where(Node.key == spec.key))
        ).scalar_one_or_none()
        if node is None:
            node = await _lay_node(session, spec, applied)
            applied.created.add(spec.key)
        applied.nodes[spec.key] = node
        for machine in spec.machines:
            name = machine.name or one_of(machine.thing_class)
            await machine_if_missing(session, node, name, machine.quality)
        for thing_class in spec.relics:
            #: `grant_relic` steps aside when a machine of the class already
            #: stands here -- laying is idempotent like the rest.
            await ruins.grant_relic(
                session, node, thing_class, origin=f"наследие Предтеч: {node.name}"
            )
        yard = await world.node_container(session, node)
        for stock in spec.items:
            fresh = spec.key in applied.created
            if fresh or (stock.ensure and not await present_in(session, yard, stock.name)):
                await world.grant_item(
                    session,
                    yard,
                    stock.name,
                    amount=stock.amount,
                    quality=stock.quality,
                    origin=stock.origin,
                )
    await _lay_edges(session, constants, scenario, applied)
    await session.flush()
    return applied


async def _lay_node(session: AsyncSession, spec: NodeSpec, applied: Applied) -> Node:
    parent = applied.nodes.get(spec.parent) if spec.parent else None
    if parent is None and spec.parent is not None:
        #: An external parent -- a planet the engine laid before the layout.
        parent = (await session.execute(select(Node).where(Node.key == spec.parent))).scalar_one()
    properties = dict(spec.properties)
    if spec.place is not None:
        #: A pinned place: written before creation, so `places.assign` keeps it
        #: -- the one property of a node that never changes afterwards (D-237).
        properties[places.PLACE] = {places.PLACE_X: spec.place[0], places.PLACE_Y: spec.place[1]}
    anchor = applied.nodes.get(spec.anchor) if spec.anchor else None
    node = await world.create_node(
        session,
        spec.key,
        spec.name,
        planet=spec.planet,
        area_m2=spec.area_m2,
        layer=spec.layer,
        parent=parent,
        anchor=anchor,
        properties=properties,
    )
    #: Veins are laid with the node and never again (pillar P2): the world
    #: works them out, and a deploy must not refill what the world spent.
    for vein in spec.veins:
        await world.create_vein(
            session, node, vein.resource, richness=vein.richness, remaining=vein.remaining
        )
    return node


async def _lay_edges(
    session: AsyncSession, constants: Constants, scenario: Scenario, applied: Applied
) -> None:
    step = constants[R.TRAVEL_CITY_STEP]
    for spec in scenario.edges:
        a, b = applied.nodes[spec.a], applied.nodes[spec.b]
        if spec.seconds == BY_REACH:
            reach = max(travel.reach_of(a), travel.reach_of(b))
            seconds = travel.frontier_seconds(constants, reach)
        elif spec.seconds is None:
            #: A city step, rolled -- but by the edge's own name, so two
            #: servers laying the same world lay the same seconds (D-007).
            dice = random.Random("|".join(sorted((spec.a, spec.b))))
            seconds = dice.uniform(step.min, step.max)
        else:
            seconds = float(spec.seconds)
        #: `connect` is idempotent: an existing road keeps its length and its
        #: wear -- what it is worth in seconds is the world's business now.
        await travel.connect(session, a, b, base_seconds=seconds, surface=spec.surface)


async def outfit(session: AsyncSession, container, stocks: tuple[Stock, ...]) -> None:
    """Lay the starting inventory into a container, thing by thing."""
    for stock in stocks:
        await world.grant_item(
            session,
            container,
            stock.name,
            amount=stock.amount,
            quality=stock.quality,
            origin=stock.origin,
        )


async def machine_if_missing(session: AsyncSession, node: Node, name: str, quality: float) -> None:
    """Place a machine if the node does not have it yet. Does not create a second one."""
    yard = await world.node_container(session, node)
    if not await present_in(session, yard, name):
        await assemble(session, yard, name, quality=quality)


async def present_in(session: AsyncSession, container, name: str) -> bool:
    found = await session.scalar(
        select(Item.id).where(Item.container_id == container.id, Item.type_key == name).limit(1)
    )
    return found is not None


#: Energy is not a thing (D-071): it lives in a pool or in a battery, and it
#: cannot be put into a container. Compositions do call for it -- silicon is
#: smelted with current -- so the assembly skips it: the Forerunners had power.
INTANGIBLE = "energy"


def _composition(book, name: str) -> dict[str, float] | None:
    """What a thing is made of: a recipe's composition, or an operation's spend.

    `None` means the ladder ends here: this is raw material, and the world
    hands it over. An extracting operation -- felling, mining -- spends nothing
    and therefore ends the descent too.
    """

    try:
        recipe = book.recipe(name)
    except ConstantError:
        recipe = None
    if recipe is not None and recipe.amounts:
        return {book.resolve(item): value for item, value in recipe.amounts.items()}
    for operation in book.operations:
        if name in operation.gives:
            spent = operation.amounts.get(name) or {}
            return {book.resolve(i): v for i, v in spent.items()} or None
    return None


async def assemble(
    session: AsyncSession,
    container,
    name: str,
    *,
    quality: float,
    amount: float = 1.0,
    seen: tuple[str, ...] = (),
    laid: set | None = None,
) -> None:
    """Assemble a thing in the container: its inputs by their own steps, then it.

    Quality is declared by the scenario rather than derived by the ladder: the
    Forerunners' craftsmanship is a decision about the world, not a consequence
    of proportions. Matter, on the other hand, is counted honestly, by the
    vault's own amounts (D-216).

    `laid` is the set of stacks this assembly created. It is what makes the
    spend safe on a **living** world: the yard being assembled into may already
    hold coal a player hauled there, and taking that instead of the coal this
    very assembly laid down would be a quiet theft with the totals still
    adding up. Passed down the recursion so an input's own inputs are counted
    in it too.
    """

    catalog = current_catalog()
    book = catalog.recipes
    name = book.resolve(name)
    if name in seen:
        raise RuntimeError("круг в лестнице: " + " → ".join((*seen, name)))
    if name == INTANGIBLE:
        return
    if laid is None:
        laid = set()

    per_unit = _composition(book, name)
    if per_unit is None:
        stack = await world.grant_item(
            session,
            container,
            name,
            amount=amount,
            quality=quality,
            origin="наследие Предтеч: сырьё столицы",
        )
        laid.add(stack.id)
        return

    for item, per in per_unit.items():
        if item == INTANGIBLE:
            continue
        #: A counted thing goes into the work whole (D-212): nobody spends half
        #: an ingot.
        need = goods.whole(item, per * amount, up=True, catalog=catalog)
        await assemble(
            session, container, item, quality=quality, amount=need, seen=(*seen, name), laid=laid
        )
    await _spend(session, container, per_unit, amount, catalog, laid)
    stack = await world.grant_item(
        session,
        container,
        name,
        amount=amount,
        quality=quality,
        origin=f"наследие Предтеч: собрано по рецепту «{name}»",
    )
    laid.add(stack.id)


async def _spend(
    session: AsyncSession, container, per_unit: dict, units: float, catalog, laid: set
) -> None:
    """Write off what went into the article. Short means a data defect, not a game one.

    Only the stacks this assembly laid down are spent, and they are taken under
    a row lock: the yard is a living container, and a player may be putting
    something down in it in the same second.

    A stack of the same thing at the same quality that was already lying here
    folds into the arrival (D-214) and so counts as ours -- but only what the
    recipe asks for is taken off it, and the rest stays where it lay. What the
    set does keep out is everything the assembly never touched: coal of
    another quality, somebody's ingots, a chest's worth of ore.
    """
    for name, per in per_unit.items():
        if name == INTANGIBLE:
            continue
        left = to_amount(goods.whole(name, per * units, up=True, catalog=catalog))
        stacks = (
            (
                await session.execute(
                    select(Item)
                    .where(
                        Item.container_id == container.id,
                        Item.type_key == name,
                        Item.id.in_(laid),
                    )
                    .with_for_update()
                )
            )
            .scalars()
            .all()
        )
        for stack in stacks:
            if left <= 0:
                break
            take = min(left, stack.amount)
            if take == stack.amount:
                laid.discard(stack.id)
                await session.delete(stack)
            else:
                stack.amount -= take
            left -= take
        if left > 0:
            raise RuntimeError(f"на сборку не хватило «{name}»: недостаёт {amount_float(left):g}")
    await session.flush()
