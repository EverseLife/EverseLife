"""Breeding: cultivars, seeds, crossing, degradation (D-057, D-067).

The task all this exists for: **give an experienced farmer a real advantage
where there are neither skills nor levels**. The advantage here is property
(seeds) and knowledge (agrotech), not a character stat.

## What is what

* **Crop** -- Terra's eight base ones, set by the vault, immutable;
* **Cultivar** (`models.plant.Variety`) -- a line within a crop with its own
  numbers. A crop's base cultivar is nobody's and created lazily; the rest are
  bred by players;
* **Seeds** -- an item (`type_key` from `plants.json`) carrying a reference to
  the cultivar and the **batch strength** `vigor`, %. Strength is not quality:
  grain quality decides how it feeds, strength what grows from sowing.

## Where each formula came from

**Inheritance.** `breed.inherit_drift` is written by the vault verbatim:
`mean(parents) +- 0.15 * spread(parents)`. An offspring's trait is the parents'
mean plus a random deviation proportional to how far the parents diverged.
Similar parents give predictable offspring, different ones a lottery.

**Novel trait.** `breed.novel_trait_chance` is the probability that the
offspring gets something neither parent had. Implemented as a noticeable shift
of one random trait: otherwise a "novel trait" would have to be invented as a
list the vault does not have.

**Distinctness threshold.** `breed.distinctness_threshold` -- a new cultivar
is viable only if it noticeably differs from those already existing in this
crop. Otherwise **the bed simply does not sprout**: the gate is built into
biology, not the interface, and the player gets the refusal from the field,
not a window (D-067). Distance is computed as the mean relative difference
across traits, in percent.

**Segregation and degradation.** Hybrid seeds are unstable: without selection
the next generation loses `breed.hybrid_decay`. Any seed fund without
selection loses `breed.degradation_per_gen` per generation. Both quantities
are given negative -- we add rather than subtract: the sign belongs to the vault.

**Stabilisation.** `breed.generations_to_stabilize` generations of
**selection** -- and a hybrid becomes a cultivar its creator may name.
Selection meanwhile holds the batch strength: it is the breeder's work.

## What is not here yet

* **Agrotech as knowledge** (D-057): the engine already distinguishes
  cultivars, but the interface shows norms to everyone alike. The split
  "symptoms without knowledge -- norms with knowledge" waits for closing the
  OQ about the five care parameters;
* **Wild ancestors in wild nodes**: gathering the first seeds by hand arrives
  with exploration;
* **Traits beyond four numbers**: diseases and crowding are inherited but
  affect nothing yet -- their mechanics do not exist (OQ-098).
"""

from __future__ import annotations

import random
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from statistics import fmean

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Catalog, Constants
from src.constants import registry as R
from src.constants.catalog import Plant
from src.engine import events, travel, world
from src.models.event import EventKind
from src.models.identity import Body, BodyState, Identity, Knowledge, KnowledgeKind
from src.models.inventory import Item
from src.models.plant import Nursery, Variety
from src.models.world import Node
from src.units import PERCENT, amount, amount_float

#: Building from `build/recipes.json`: crossing happens only in a nursery.
NURSERY = "Селекционный питомник"

#: Full strength of a seed batch. Not a balance number but "one hundred percent
#: of what the cultivar can": the losses themselves are set by `breed.*`.
FULL_VIGOR = PERCENT

#: Traits that are inherited. The numbers are the same as the crop's in
#: plants.json: a cultivar must substitute for the crop without unit conversion.
TRAITS = ("yield_per_m2", "cycle_days", "fertility", "spoilage_k", "hardiness")


class BreedError(Exception):
    pass


class NotSeeds(BreedError):
    """Not seeds. One sows and crosses with seeds, not harvest."""


class WrongCulture(BreedError):
    """Cultivars of one crop are crossed: there is no interspecies in this game."""


class NoNursery(BreedError):
    """No nursery in the node: crossing needs a place, like every machine."""


class NotStable(BreedError):
    """A hybrid is not a cultivar yet: a name goes to what gives a stable result."""


def traits_of_plant(plant: Plant) -> dict[str, float]:
    """The crop's numbers in the form of cultivar traits."""
    return {
        "yield_per_m2": plant.yield_per_m2,
        "cycle_days": plant.cycle_days,
        "fertility": plant.requires.fertility,
        "spoilage_k": plant.traits.spoilage_k,
        "hardiness": plant.traits.hardiness,
    }


async def landrace(session: AsyncSession, catalog: Catalog, culture_id: str) -> Variety:
    """The crop's base cultivar: nobody's, stable, created at first need.

    It is "what grows for everyone": the reference point from which the breeder
    goes up, and an abandoned seed fund goes down.
    """
    plant = catalog.plants.by_id(culture_id)
    found = (
        await session.execute(
            select(Variety).where(
                Variety.culture_id == culture_id,
                Variety.author_identity_id.is_(None),
                Variety.stable.is_(True),
            )
        )
    ).scalars().first()
    if found is not None:
        return found

    variety = Variety(
        culture_id=culture_id,
        name=plant.name,
        author_identity_id=None,
        generation=0,
        stable=True,
        traits=traits_of_plant(plant),
    )
    session.add(variety)
    await session.flush()
    return variety


def distance(one: dict[str, float], other: dict[str, float]) -> float:
    """How much two cultivars differ, in percent.

    The **largest** relative difference across traits is taken, not the mean:
    "noticeably differs" means differs in at least something. A cultivar with
    double yield is another cultivar even if in everything else it copies its
    parent; averaging across traits would drown such a difference in zeros.

    The vault does not set the metric -- it sets only the threshold
    (`breed.distinctness_threshold`), and the choice here belongs to the engine.
    """
    diffs: list[float] = []
    for key in TRAITS:
        a, b = one.get(key), other.get(key)
        if a is None or b is None:
            continue
        base = max(abs(a), abs(b))
        if base <= 0:
            continue
        diffs.append(abs(a - b) / base * PERCENT)
    return max(diffs) if diffs else 0.0


def inherit(
    constants: Constants,
    a: dict[str, float],
    b: dict[str, float],
    *,
    rng: random.Random,
) -> dict[str, float]:
    """Offspring traits: `mean(parents) +- 0.15 * spread(parents)` (vault).

    The formula is written in `breed.inherit_drift` as text -- the engine must
    execute it, not invent it. The deviation coefficient is read from there too.
    """
    drift = _drift_share(constants)
    child: dict[str, float] = {}
    for key in TRAITS:
        one, other = a.get(key), b.get(key)
        if one is None or other is None:
            continue
        mean = fmean((one, other))
        spread = abs(one - other)
        child[key] = mean + rng.uniform(-drift, drift) * spread

    #: Occasionally a trait appears that neither parent had. The only honest
    #: way to show that with numbers is to shift one trait **from the middle**,
    #: not within the parents' range. The shift coefficient is the same as for
    #: inheritance: the vault sets no second one.
    if rng.uniform(0, PERCENT) < constants[R.BREED_NOVEL_TRAIT_CHANCE] and child:
        key = rng.choice(sorted(child))
        child[key] *= 1 + rng.choice((-1, 1)) * drift
    return child


def _drift_share(constants: Constants) -> float:
    """The deviation coefficient from the vault formula `breed.inherit_drift`.

    The formula is stored as a string ("mean(parents) +- 0.15 * spread(parents)"),
    and the number is taken from it: keeping a second copy of the coefficient
    in code would introduce a balance number past the vault (D-065).
    """
    formula = constants[R.BREED_INHERIT_DRIFT]
    text = formula if isinstance(formula, str) else str(formula)
    _, _, tail = text.partition("±")
    for token in tail.replace("*", " ").split():
        try:
            return float(token)
        except ValueError:
            continue
    raise BreedError(f"из формулы {text!r} не вычитать коэффициент отклонения")


async def cross(
    session: AsyncSession,
    constants: Constants,
    catalog: Catalog,
    body: Body,
    seeds_a: Item,
    seeds_b: Item,
    *,
    now: datetime | None = None,
    rng: random.Random | None = None,
) -> Nursery:
    """Cross two cultivars in the nursery. Costs seeds, a place and a full cycle.

    The result does not come at once: breeding is an occupation of weeks, not
    an evening. Seedlings are checked for distinctness only at the end (D-067)
    -- before that the breeder does not know whether something new came out.
    """
    moment = now or datetime.now(UTC)
    if body.state is not BodyState.ALIVE:
        raise BreedError("мёртвое тело не сеет")
    await travel.require_here(session, body)

    node = await world.node_container(session, await _node(session, body))
    machines = (
        await session.execute(
            select(Item.type_key).where(Item.container_id == node.id).distinct()
        )
    ).scalars().all()
    if NURSERY not in machines:
        raise NoNursery(f"в узле нет постройки «{NURSERY}»")

    cultivar_a = await _variety_of(session, seeds_a)
    cultivar_b = await _variety_of(session, seeds_b)
    if cultivar_a.culture_id != cultivar_b.culture_id:
        raise WrongCulture(
            f"{cultivar_a.culture_id} и {cultivar_b.culture_id} — разные культуры: "
            "скрещивают сорта одной"
        )
    if seeds_a.id == seeds_b.id:
        raise BreedError("нужны две партии семян: сорт сам с собой не скрещивают")

    #: The nursery's sowing norm is the same as the field's: it is a patch, after all.
    norm = amount(constants[R.FARM_SEED_RATE] * constants[R.FARM_PLOT_MIN_AREA])
    for batch in (seeds_a, seeds_b):
        if batch.amount < norm:
            raise NotSeeds(
                f"на питомник нужно {amount_float(norm):g} семян каждого сорта"
            )
    for batch in (seeds_a, seeds_b):
        batch.amount -= norm
        if batch.amount <= 0:
            await session.delete(batch)
    await session.flush()

    plant = catalog.plants.by_id(cultivar_a.culture_id)
    cycle = timedelta(hours=plant.cycle_days * constants[R.TIME_DAY_TERRA])
    nursery = Nursery(
        body_id=body.id,
        node_id=body.node_id,
        parent_a_id=cultivar_a.id,
        parent_b_id=cultivar_b.id,
        seeds=Decimal(str(amount_float(norm))),
        ready_at=moment + cycle,
    )
    session.add(nursery)
    await session.flush()

    await events.record(
        session,
        EventKind.PLOT_SOWN,
        actor_identity_id=body.identity_id,
        node_id=body.node_id,
        work="cross",
        nursery=str(nursery.id),
        culture=cultivar_a.culture_id,
        parents=[str(cultivar_a.id), str(cultivar_b.id)],
    )
    return nursery


async def gather_cross(
    session: AsyncSession,
    constants: Constants,
    catalog: Catalog,
    body: Body,
    nursery: Nursery,
    *,
    now: datetime | None = None,
    rng: random.Random | None = None,
) -> Variety | None:
    """Collect the nursery seedlings. Empty means it did not sprout (D-067).

    A cultivar too similar to existing ones does not germinate at all: the
    breeder gets not an engine refusal but an empty bed.
    """
    moment = now or datetime.now(UTC)
    await travel.require_here(session, body)
    if nursery.done:
        raise BreedError("этот питомник уже разобран")
    if moment < nursery.ready_at:
        raise BreedError(f"питомник созреет к {nursery.ready_at.isoformat()}")

    dice = rng or random.Random(str(nursery.id))
    father = await session.get(Variety, nursery.parent_a_id)
    mother = await session.get(Variety, nursery.parent_b_id)
    if father is None or mother is None:  # pragma: no cover
        raise BreedError("родительский сорт исчез")

    signs = inherit(constants, father.traits, mother.traits, rng=dice)
    threshold = constants[R.BREED_DISTINCTNESS_THRESHOLD]
    neighbours = (
        await session.execute(
            select(Variety).where(Variety.culture_id == father.culture_id)
        )
    ).scalars().all()
    similar = next(
        (nb for nb in neighbours if distance(signs, nb.traits) < threshold),
        None,
    )

    nursery.done = True
    if similar is not None:
        await session.flush()
        await events.record(
            session,
            EventKind.PLOT_HARVESTED,
            actor_identity_id=body.identity_id,
            node_id=nursery.node_id,
            work="cross",
            nursery=str(nursery.id),
            sprouted=False,
            too_close_to=similar.name or str(similar.id),
        )
        return None

    hybrid = Variety(
        culture_id=father.culture_id,
        name=None,
        author_identity_id=body.identity_id,
        parent_a_id=father.id,
        parent_b_id=mother.id,
        generation=1,
        stable=False,
        traits=signs,
    )
    session.add(hybrid)
    await session.flush()
    nursery.result_variety_id = hybrid.id

    #: The creator knows the agrotech of their cultivar, and nobody else knows
    #: it (D-057). Not a reward but a consequence: they bred it.
    identity = await session.get(Identity, body.identity_id)
    if identity is not None:
        await world.learn(
            session, identity, agrotech_key(hybrid),
            kind=KnowledgeKind.AGROTECH, discovered=True,
        )

    plant = catalog.plants.by_id(father.culture_id)
    pocket = await world.body_container(session, body)
    session.add(
        Item(
            container_id=pocket.id,
            type_key=plant.seed,
            amount=amount(float(nursery.seeds)),
            variety_id=hybrid.id,
            vigor=Decimal(str(FULL_VIGOR)),
            maker_identity_id=body.identity_id,
            made_at=moment,
            made_node_id=nursery.node_id,
        )
    )
    await session.flush()

    await events.record(
        session,
        EventKind.PLOT_HARVESTED,
        actor_identity_id=body.identity_id,
        node_id=nursery.node_id,
        work="cross",
        nursery=str(nursery.id),
        sprouted=True,
        variety=str(hybrid.id),
    )
    return hybrid


def next_vigor(
    constants: Constants, variety: Variety, vigor: float, *, selected: bool
) -> float:
    """Strength of the next seed generation.

    Selection is work: it holds the fund. Without selection the fund degrades,
    and a hybrid additionally segregates. Both vault quantities are negative.
    """
    if selected:
        return vigor
    loss = constants[R.BREED_DEGRADATION_PER_GEN]
    if not variety.stable:
        loss += constants[R.BREED_HYBRID_DECAY]
    return max(0.0, vigor + loss)


async def select_generation(
    session: AsyncSession, constants: Constants, variety: Variety
) -> Variety:
    """Count a generation of selection. So many generations -- and the hybrid became a cultivar."""
    if variety.stable:
        return variety
    variety.generation += 1
    threshold = constants[R.BREED_GENERATIONS_TO_STABILIZE]
    if variety.generation >= threshold.max:
        variety.stable = True
    await session.flush()
    return variety


async def name_variety(
    session: AsyncSession, body: Body, variety: Variety, name: str
) -> Variety:
    """Name a bred cultivar. The author's name is attached to it forever."""
    if not variety.stable:
        raise NotStable(
            "сорт ещё не постоянен: имя даётся тому, что даёт тот же результат "
            "из раза в раз"
        )
    if variety.author_identity_id != body.identity_id:
        raise BreedError("называет сорт тот, кто его вывел")
    pure = name.strip()
    if not pure:
        raise BreedError("имя пустое")
    variety.name = pure
    await session.flush()
    await events.record(
        session,
        EventKind.KNOWLEDGE_LEARNED,
        actor_identity_id=body.identity_id,
        work="variety",
        variety=str(variety.id),
        name=pure,
    )
    return variety


async def seed_lot(
    session: AsyncSession,
    catalog: Catalog,
    container_id: uuid.UUID,
    variety: Variety,
    quantity: float,
    vigor: float,
    *,
    now: datetime | None = None,
) -> Item:
    """Put a batch of the cultivar's seeds into a container."""
    plant = catalog.plants.by_id(variety.culture_id)
    item = Item(
        container_id=container_id,
        type_key=plant.seed,
        amount=amount(quantity),
        variety_id=variety.id,
        vigor=Decimal(str(vigor)),
        made_at=now,
    )
    session.add(item)
    await session.flush()
    return item


def agrotech_key(variety: Variety) -> str:
    """What keys a cultivar's agrotech.

    For base cultivars -- the crop's name: their agrotech is common and lies in
    the Library (D-053). For a bred one -- its own id: only the author knows
    it, until they sell the carrier themselves.
    """
    return variety.culture_id if variety.author_identity_id is None else str(variety.id)


async def knows_agrotech(
    session: AsyncSession, identity_id: uuid.UUID, variety: Variety
) -> bool:
    """Whether the identity knows what this cultivar needs.

    Knowledge does not forbid sowing or harvesting -- it decides **whether the
    farmer sees norms or only symptoms** (D-057). There is and will be no
    "cannot plant" wall here: the newcomer runs into their own ignorance, and
    that is curable.
    """
    found = await session.execute(
        select(Knowledge).where(
            Knowledge.identity_id == identity_id,
            Knowledge.kind == KnowledgeKind.AGROTECH,
            Knowledge.key == agrotech_key(variety),
        )
    )
    return found.scalar_one_or_none() is not None


async def copy_agrotech(
    session: AsyncSession, catalog: Catalog, body: Body, culture_id: str
) -> Knowledge | None:
    """Take the agrotech of a base crop in the Library: free, but on foot.

    The eight base ones lie there for everyone (D-053). The agrotech of a bred
    cultivar does not go into the Library -- only the author knows it.
    """
    await travel.require_here(session, body)
    node = await session.get(Node, body.node_id)
    #: The library is a machine (D-176): agrotech is taken where it stands.
    if node is None or not await world.is_library(session, node):
        raise BreedError("Библиотека не работает удалённо: за знанием надо прийти")

    plant = catalog.plants.by_id(culture_id)
    identity = await session.get(Identity, body.identity_id)
    if identity is None:  # pragma: no cover
        raise BreedError("тело без личности")
    return await world.learn(
        session, identity, plant.id, kind=KnowledgeKind.AGROTECH
    )


async def _variety_of(session: AsyncSession, item: Item) -> Variety:
    if item.variety_id is None:
        raise NotSeeds(f"{item.type_key!r} — не семена сорта")
    variety = await session.get(Variety, item.variety_id)
    if variety is None:  # pragma: no cover -- a cultivar is never deleted
        raise BreedError("сорт этих семян исчез")
    return variety


async def _node(session: AsyncSession, body: Body):
    node = await session.get(Node, body.node_id)
    if node is None:  # pragma: no cover
        raise BreedError("тело вне узла")
    return node
