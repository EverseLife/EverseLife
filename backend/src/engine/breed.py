# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

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
not a window (D-067). Distance is the largest relative difference across
traits, in percent, taken against the existing cultivar (D-260).

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
* **Traits beyond four numbers**: diseases and crowding are inherited but
  affect nothing yet -- their mechanics do not exist (OQ-098).
"""

from __future__ import annotations

import random
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from statistics import fmean

from sqlalchemy import Select, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Catalog, Constants
from src.constants import registry as R
from src.constants.catalog import Plant
from src.engine import events, luck, travel, world
from src.engine.errors import Refusal, left_to_say
from src.models.event import EventKind
from src.models.identity import Body, BodyState, Identity, KnowledgeKind
from src.models.inventory import Item
from src.models.plant import Nursery, Variety
from src.models.world import Node
from src.units import PERCENT, amount, amount_float

#: Thing class from `build/recipes.json` (D-215): crossing happens only where
#: a machine of the nursery class stands.
NURSERY = "nursery"

#: Full strength of a seed batch. Not a balance number but "one hundred percent
#: of what the cultivar can": the losses themselves are set by `breed.*`.
FULL_VIGOR = PERCENT

#: Traits that are inherited. The numbers are the same as the crop's in
#: plants.json: a cultivar must substitute for the crop without unit conversion.
TRAITS = ("yield_per_m2", "cycle_days", "fertility", "spoilage_k", "hardiness", "density_risk")


class BreedError(Refusal):
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
        "density_risk": plant.traits.density_risk,
    }


def base_line(catalog: Catalog, culture_id: str) -> Variety:
    """The crop's base cultivar as a transient object: same numbers, no row.

    `landrace` is get-or-create, so a read path must not call it ("reads do
    not write", review 2026-08-23): a survey of a plot whose cultivar row is
    missing -- old plots, a dangling reference -- shows the base line's
    numbers from an object that is never added to the session.
    """
    plant = catalog.plants.by_id(culture_id)
    #: `name` stays empty like `landrace` leaves it (D-251): an authorless
    #: cultivar is shown by its plants-domain key (`shown_as`), never by a
    #: stored word.
    return Variety(
        culture_id=culture_id,
        name=None,
        author_identity_id=None,
        generation=0,
        stable=True,
        wild=False,
        traits=traits_of_plant(plant),
    )


async def landrace(session: AsyncSession, catalog: Catalog, culture_id: str) -> Variety:
    """The crop's base cultivar: nobody's, stable, created at first need.

    It is "what grows for everyone": the reference point from which the breeder
    goes up, and an abandoned seed fund goes down.
    """
    plant = catalog.plants.by_id(culture_id)
    lookup = select(Variety).where(
        Variety.culture_id == culture_id,
        Variety.author_identity_id.is_(None),
        Variety.stable.is_(True),
        Variety.wild.is_(False),
    )
    found = (await session.execute(lookup)).scalars().first()
    if found is not None:
        return found

    #: No literal name (D-251): an authorless cultivar is shown by its
    #: plants-domain key (`shown_as`), and `name` stays what the model says it
    #: is -- the creator's mark. A stored display word would freeze one
    #: language into a row every language reads.
    variety = Variety(
        culture_id=culture_id,
        name=None,
        author_identity_id=None,
        generation=0,
        stable=True,
        traits=traits_of_plant(plant),
    )
    return await _create_once(session, variety, lookup)


async def wild_ancestor(
    session: AsyncSession, constants: Constants, catalog: Catalog, culture_id: str
) -> Variety:
    """The crop's wild ancestor: nobody's, stable, created at first need (D-260).

    A distinct cultivar, not the base one with a discount: its traits differ
    (`breed.wild_traits` -- lower yield, higher hardiness), and that difference
    is the whole point. It is the second parent D-057 intended: crossing wild
    with base gets a real spread, and breeding stops being self times self.
    """
    plant = catalog.plants.by_id(culture_id)
    lookup = select(Variety).where(
        Variety.culture_id == culture_id,
        Variety.author_identity_id.is_(None),
        Variety.wild.is_(True),
    )
    found = (await session.execute(lookup)).scalars().first()
    if found is not None:
        return found

    factors = constants[R.BREED_WILD_TRAITS]
    traits = {key: value * factors.get(key, 1.0) for key, value in traits_of_plant(plant).items()}
    #: Nameless like the base cultivar: the wild flag plus the culture is the
    #: whole identity, and the display word comes from the plants domain
    #: (`spelt_wild`) in whatever language is reading (D-251).
    variety = Variety(
        culture_id=culture_id,
        name=None,
        author_identity_id=None,
        generation=0,
        stable=True,
        wild=True,
        traits=traits,
    )
    return await _create_once(session, variety, lookup)


async def _create_once(
    session: AsyncSession, variety: Variety, lookup: Select[tuple[Variety]]
) -> Variety:
    """Insert an authorless cultivar, or take the one a rival session just made.

    Two sessions ask for a culture's lazy cultivar in the same second -- two
    `forage.take` of one culture's seeds is enough -- and both select nothing
    and both insert. The partial unique index on `(culture_id, wild)` for
    authorless rows makes the second insert refuse instead of doubling the
    cultivar; the savepoint keeps the caller's transaction alive through the
    refusal, and the loser rereads the winner's row. The reread sees it:
    the violation is raised only once the winner's insert is committed.
    """
    try:
        async with session.begin_nested():
            session.add(variety)
            await session.flush()
    except IntegrityError:
        return (await session.execute(lookup)).scalars().one()
    return variety


def distance(new: dict[str, float], reference: dict[str, float]) -> float:
    """How much a seedling differs from an existing cultivar, in percent.

    The **largest** relative difference across traits is taken, not the mean:
    "noticeably differs" means differs in at least something. A cultivar with
    double yield is another cultivar even if in everything else it copies its
    parent; averaging across traits would drown such a difference in zeros.

    The difference is relative to the **existing** cultivar, not to the larger
    of the two (D-260): dividing by the larger let a worsening through the
    viability gate and never an improvement -- x0.85 measured 15% while x1.15
    measured 13% -- so the gate bred degradations by arithmetic alone.

    The vault does not set the metric -- it sets only the threshold
    (`breed.distinctness_threshold`), and the choice here belongs to the engine.
    """
    diffs: list[float] = []
    for key in TRAITS:
        a, b = new.get(key), reference.get(key)
        if a is None or b is None:
            continue
        base = abs(b)
        if base <= 0:
            continue
        diffs.append(abs(a - b) / base * PERCENT)
    return max(diffs) if diffs else 0.0


async def inherit(
    constants: Constants,
    a: dict[str, float],
    b: dict[str, float],
    *,
    rng: random.Random,
    session: AsyncSession | None = None,
    who: uuid.UUID | None = None,
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
    #: not within the parents' range. The shift has its own vault quantity
    #: (D-260): tied to the inheritance drift it sat exactly on the viability
    #: threshold, and the whole branch balanced on a float boundary.
    #: The chance keeps a memory (D-213): a breeder who never once saw a new
    #: trait in twenty crossings has the same complaint as a scout who never
    #: found anything, and the announced share is unchanged by the memory.

    novel = (
        await luck.hit(
            session, who, luck.BREED_NOVEL, constants[R.BREED_NOVEL_TRAIT_CHANCE], dice=rng
        )
        if session is not None
        else rng.random() < constants[R.BREED_NOVEL_TRAIT_CHANCE] / PERCENT
    )
    if novel and child:
        key = rng.choice(sorted(child))
        child[key] *= 1 + rng.choice((-1, 1)) * constants[R.BREED_NOVEL_TRAIT_SHIFT] / PERCENT
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
    raise BreedError(key="breed-no-drift-in-formula", formula=text)


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
        raise BreedError(key="breed-dead-sows")
    await travel.require_here(session, body)

    #: What stands (D-278): a nursery lying in its crate breeds nothing.
    machines = await world.thing_kinds(session, await _node(session, body))
    if not machines & set(world.station_names(NURSERY)):
        raise NoNursery(key="breed-no-nursery", station=NURSERY)

    cultivar_a = await _variety_of(session, seeds_a)
    cultivar_b = await _variety_of(session, seeds_b)
    if cultivar_a.culture_id != cultivar_b.culture_id:
        #: A culture id, not a goods id: `NAME()` must not be asked for it. Its
        #: domains are goods, stations and classes, and `beans` lives in the
        #: first of them as the bean **grain** -- so NAME would answer with a
        #: word from the wrong domain for some cultures and the bare id for the
        #: rest. Until the vault names cultures, the id travels as it did.
        raise WrongCulture(
            key="breed-different-cultures",
            one=cultivar_a.culture_id,
            other=cultivar_b.culture_id,
        )
    if seeds_a.id == seeds_b.id:
        raise BreedError(key="breed-one-batch")

    #: The nursery's sowing norm is the same as the field's: it is a patch, after all.
    norm = amount(constants[R.FARM_SEED_RATE] * constants[R.FARM_PLOT_MIN_AREA])
    for batch in (seeds_a, seeds_b):
        if batch.amount < norm:
            raise NotSeeds(key="breed-not-enough-seeds", need=amount_float(norm))
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
        raise BreedError(key="breed-nursery-done")
    if moment < nursery.ready_at:
        raise BreedError(
            key="breed-nursery-not-ready",
            inner={"left": [left_to_say(nursery.ready_at)]},
        )

    dice = rng or random.Random(str(nursery.id))
    father = await session.get(Variety, nursery.parent_a_id)
    mother = await session.get(Variety, nursery.parent_b_id)
    if father is None or mother is None:  # pragma: no cover
        raise BreedError(key="breed-parent-gone")

    signs = await inherit(
        constants,
        father.traits,
        mother.traits,
        rng=dice,
        session=session,
        who=body.identity_id,
    )
    threshold = constants[R.BREED_DISTINCTNESS_THRESHOLD]
    neighbours = (
        (await session.execute(select(Variety).where(Variety.culture_id == father.culture_id)))
        .scalars()
        .all()
    )
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
            #: A key or a mark, never a display literal (D-251): the payload
            #: outlives every rename, so it records what the row *is*.
            too_close_to=(
                plant_key(catalog, similar)
                if similar.author_identity_id is None
                else similar.name or str(similar.id)
            ),
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
            session,
            identity,
            agrotech_key(hybrid),
            kind=KnowledgeKind.AGROTECH,
            discovered=True,
        )

    plant = catalog.plants.by_id(father.culture_id)
    pocket = await world.body_container(session, body)
    bred = Item(
        container_id=pocket.id,
        type_key=plant.seed,
        amount=amount(float(nursery.seeds)),
        variety_id=hybrid.id,
        #: Heterosis (D-260): the F1 lot outdoes both parents through lot
        #: strength -- and exactly one sowing long, `next_vigor` caps the rest.
        vigor=Decimal(str(FULL_VIGOR + constants[R.BREED_HYBRID_VIGOR])),
        maker_identity_id=body.identity_id,
        made_at=moment,
        made_node_id=nursery.node_id,
    )
    session.add(bred)
    await world.stack_up(session, bred)
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


def next_vigor(constants: Constants, variety: Variety, vigor: float, *, selected: bool) -> float:
    """Strength of the next seed generation.

    Selection is work: it holds the fund. Without selection the fund degrades,
    and a hybrid additionally segregates. Both vault quantities are negative.

    Heterosis is not inherited (D-260): whatever strength above one hundred an
    F1 lot carried, its offspring starts from one hundred at best -- the bonus
    lives exactly one sowing, and that is the hybrid seller's whole business.
    """
    ceiling = min(vigor, FULL_VIGOR)
    if selected:
        return ceiling
    loss = constants[R.BREED_DEGRADATION_PER_GEN]
    if not variety.stable:
        loss += constants[R.BREED_HYBRID_DECAY]
    return max(0.0, ceiling + loss)


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


async def name_variety(session: AsyncSession, body: Body, variety: Variety, name: str) -> Variety:
    """Name a bred cultivar. The author's name is attached to it forever."""
    if not variety.stable:
        raise NotStable(key="breed-not-stable")
    if variety.author_identity_id != body.identity_id:
        raise BreedError(key="breed-not-the-author")
    pure = name.strip()
    if not pure:
        raise BreedError(key="breed-empty-name")
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
    #: One cultivar at one strength is one seed lot: a second harvest adds to
    #: the sack rather than putting a second sack beside it (D-214).
    return await world.stack_up(session, item)


def plant_key(catalog: Catalog, variety: Variety) -> str:
    """The plants-domain key an authorless cultivar is shown by (D-251).

    The base cultivar is shown as its crop; the wild ancestor by the wild key
    the vault pinned in `renames.json` (`spelt_wild`). A snapshot too old to
    carry the wild key degrades to the crop's own -- an untranslated word, not
    a broken row.
    """
    if variety.wild:
        plant = catalog.plants.by_id(variety.culture_id)
        return plant.wild_id or plant.id
    return variety.culture_id


def shown_as(catalog: Catalog, variety: Variety) -> dict[str, str | int]:
    """How the wire names a cultivar (D-251): key, literal or generation.

    An authorless cultivar travels as its plants-domain key and the client
    reads it in the player's language via `/public/renames`. An author's name
    is a mark, not copy -- it travels as written. A nameless hybrid travels as
    its generation and the words are the client's (`ui-nursery-hybrid`): a
    sentence composed here would be composed in one language only.
    """
    if variety.author_identity_id is None:
        return {"key": plant_key(catalog, variety)}
    if variety.name:
        return {"name": variety.name}
    return {"hybrid": variety.generation}


def agrotech_key(variety: Variety) -> str:
    """What keys a cultivar's agrotech.

    For base cultivars -- the crop's id: their care text is common and lies in
    the Library (D-053). For a bred one -- its own id: the author alone reads
    the text, and tells whom they like (D-296).
    """
    return variety.culture_id if variety.author_identity_id is None else str(variety.id)


async def _variety_of(session: AsyncSession, item: Item) -> Variety:
    if item.variety_id is None:
        raise NotSeeds(key="breed-not-variety-seeds", goods=item.type_key)
    variety = await session.get(Variety, item.variety_id)
    if variety is None:  # pragma: no cover -- a cultivar is never deleted
        raise BreedError(key="breed-variety-gone")
    return variety


async def _node(session: AsyncSession, body: Body):
    node = await session.get(Node, body.node_id)
    if node is None:  # pragma: no cover
        raise BreedError(key="breed-body-off-node")
    return node
