"""«Свод» (D-143).

Проверяется не «функция возвращает число», а то, ради чего механика написана:

* устойчивость свода **не утекает** наружу ни числом, ни производной;
* признак врёт, и по одному наблюдению свод не восстанавливается;
* ставка растёт по ходу сессии, и обрушение стоит всего добытого;
* крепь конечна: потолок делает сессию конечной, сколько бруса ни трать;
* соседи меняют выход в разные стороны на богатой и бедной жиле.
"""

from __future__ import annotations

import random
import uuid
from dataclasses import fields

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from octoverse.constants import Constants
from octoverse.constants import registry as R
from octoverse.engine import mining, world
from octoverse.engine.mining import Pace, SessionState
from octoverse.models.identity import Wound
from octoverse.models.inventory import Item
from octoverse.models.world import Vein

ORE = "Руда"


async def _забой(session: AsyncSession, *, richness: float = 60, remaining: float = 100_000):
    метка = uuid.uuid4().hex[:8]
    node = await world.create_node(session, f"terra.mine.{метка}", "Забой", area_m2=100)
    vein = await world.create_vein(session, node, ORE, richness=richness, remaining=remaining)
    identity = await world.create_identity(session, f"Шахтёр-{метка}")
    body = await world.print_body(session, identity, node)
    return node, vein, body


async def _инструмент(session: AsyncSession, body):
    container = await world.body_container(session, body)
    return await world.grant_item(
        session, container, "Железная кирка", quality=50, origin="сценарий теста"
    )


# --- скрытое состояние ------------------------------------------------------


def test_свод_не_выходит_наружу() -> None:
    """Игроку не показывается ни устойчивость, ни то, из чего её вывести."""
    видимое = {field.name for field in fields(mining.Sight)}
    assert "roof" not in видимое
    assert видимое == {"sign", "mined", "swings", "timbers", "stamina", "pace", "state"}


def test_признак_врёт_в_обе_стороны(constants: Constants) -> None:
    """Без шума полосы обратимы в арифметику, и скрытого числа больше нет."""
    bands = constants[R.MINE_SIGN_BANDS]
    граница = bands["сыплется пыль"]
    наблюдения = {
        mining.sign_of(constants, граница, random.Random(seed)) for seed in range(200)
    }
    assert len(наблюдения) > 1, "на границе полосы признак обязан быть неоднозначным"


def test_богатая_жила_даёт_меньше_устойчивости(constants: Constants) -> None:
    """За богатство платят риском."""
    бедная = mining.starting_roof(constants, 10)
    богатая = mining.starting_roof(constants, 90)
    assert богатая < бедная
    #: Но не ниже потолка крепи — иначе сессия кончалась бы, не начавшись.
    assert богатая >= constants[R.MINE_ROOF_TIMBER_CAP]


def test_час_добычи_равен_сессии_без_крепи(constants: Constants) -> None:
    """`mining.iron_per_hour` за час, шестнадцать ударов — короткая сессия."""
    ударов = constants[R.MINE_ROOF_START] / constants[R.MINE_ROOF_PER_SWING]
    assert mining.swing_hours(constants) * ударов == pytest.approx(1.0)


# --- ход сессии -------------------------------------------------------------


async def test_добытое_копится_и_уходит_в_инвентарь(
    session: AsyncSession, constants: Constants
) -> None:
    _, vein, body = await _забой(session)
    сессия = await mining.start(session, constants, body, vein)

    for _ in range(3):
        вид = await mining.swing(session, constants, сессия)
    assert вид.mined > 0
    assert вид.swings == 3

    добыча = await mining.leave(session, constants, сессия)
    await session.commit()

    инвентарь = await world.body_container(session, body)
    в_инвентаре = await session.scalar(
        select(func.coalesce(func.sum(Item.amount), 0)).where(
            Item.container_id == инвентарь.id, Item.type_key == ORE
        )
    )
    assert в_инвентаре > 0
    assert добыча == pytest.approx(вид.mined)


async def test_обрушение_стоит_всей_сессии(
    session: AsyncSession, constants: Constants
) -> None:
    """Ставка растёт по ходу сессии — в этом всё напряжение (D-143)."""
    _, vein, body = await _забой(session)
    кирка = await _инструмент(session, body)
    сессия = await mining.start(session, constants, body, vein, tool_item_id=кирка.id)

    #: Бьём до упора, не крепя. Свод обязан кончиться.
    вид = None
    for _ in range(100):
        вид = await mining.swing(session, constants, сессия, rng=random.Random(0))
        if вид.state is SessionState.COLLAPSED:
            break
    await session.commit()

    assert вид is not None and вид.state is SessionState.COLLAPSED
    assert вид.mined == 0, "обрушилось — потеряно всё добытое за сессию"

    #: Обрушение бьёт и по инструменту: обычный износ плюс `mine.collapse_wear`.
    ожидаемый = constants[R.WEAR_TOOL_PER_SESSION] + constants[R.MINE_COLLAPSE_WEAR]
    await session.refresh(кирка)
    assert float(кирка.condition) == pytest.approx(100 - ожидаемый)


async def test_обрушение_иногда_ранит(session: AsyncSession, constants: Constants) -> None:
    _, vein, body = await _забой(session)
    сессия = await mining.start(session, constants, body, vein)

    #: Зерно подобрано так, чтобы бросок попал в `mine.collapse_wound_chance`.
    удачливый = random.Random()
    удачливый.uniform = lambda a, b: a  # noqa: ARG005 — всегда нижняя граница
    for _ in range(100):
        вид = await mining.swing(session, constants, сессия, rng=удачливый)
        if вид.state is SessionState.COLLAPSED:
            break
    await session.commit()

    ран = await session.scalar(
        select(func.count()).select_from(Wound).where(Wound.body_id == body.id)
    )
    assert ран == 1
    assert constants[R.MINE_COLLAPSE_WOUND_CHANCE] > 0


async def test_крепь_держит_свод_но_не_бесконечно(
    session: AsyncSession, constants: Constants
) -> None:
    """Потолок `mine.roof_timber_cap` — главная ручка: сессия конечна."""
    _, vein, body = await _забой(session, richness=10)
    контейнер = await world.body_container(session, body)
    await world.grant_item(session, контейнер, mining.TIMBER, amount=50, origin="сценарий теста")

    сессия = await mining.start(session, constants, body, vein)
    for _ in range(10):
        await mining.timber(session, constants, сессия)

    assert float(сессия.roof) == pytest.approx(constants[R.MINE_ROOF_TIMBER_CAP])


async def test_без_крепи_не_укрепишься(session: AsyncSession, constants: Constants) -> None:
    """Крепь стоит бруса и верёвки — в этом весь смысл выбора."""
    _, vein, body = await _забой(session)
    сессия = await mining.start(session, constants, body, vein)
    with pytest.raises(mining.NoTimber):
        await mining.timber(session, constants, сессия)


async def test_быстрый_темп_даёт_больше_и_рискует_больше(
    session: AsyncSession, constants: Constants
) -> None:
    """Один множитель на выход, просадку свода и расход выносливости (D-091)."""
    _, vein_a, body_a = await _забой(session)
    ровно = await mining.start(session, constants, body_a, vein_a, pace=Pace.STEADY)
    спокойный = await mining.swing(session, constants, ровно)
    просадка_ровно = mining.starting_roof(constants, float(vein_a.richness)) - float(ровно.roof)

    _, vein_b, body_b = await _забой(session)
    быстро = await mining.start(session, constants, body_b, vein_b, pace=Pace.FAST)
    жадный = await mining.swing(session, constants, быстро)
    просадка_быстро = mining.starting_roof(constants, float(vein_b.richness)) - float(быстро.roof)

    k = constants[R.MINE_PACE_K]
    assert жадный.mined == pytest.approx(спокойный.mined * k, rel=0.01)
    assert просадка_быстро == pytest.approx(просадка_ровно * k)
    потрачено_ровно = constants[R.BODY_STAMINA_MAX] - спокойный.stamina
    потрачено_быстро = constants[R.BODY_STAMINA_MAX] - жадный.stamina
    assert потрачено_быстро == pytest.approx(потрачено_ровно * k)


# --- жила и соседи ----------------------------------------------------------


async def test_жила_конечна(session: AsyncSession, constants: Constants) -> None:
    """Выработанная жила исчезает. Это неотменяемо (столп П2)."""
    _, vein, body = await _забой(session, remaining=1)
    сессия = await mining.start(session, constants, body, vein)
    await mining.swing(session, constants, сессия)
    await session.commit()

    assert vein.remaining == 0
    assert vein.depleted_at is not None
    with pytest.raises(mining.VeinDepleted):
        await mining.swing(session, constants, сессия)


async def test_жила_беднеет_ступенями(session: AsyncSession, constants: Constants) -> None:
    _, vein, body = await _забой(session, richness=90, remaining=1_000_000)
    было = float(vein.richness)

    #: Двигаем счётчик выработки прямо за ступень — бить столько долго.
    from octoverse.units import amount

    vein.extracted = amount(constants[R.VEIN_DEPLETION_STEP]) - amount(1)
    сессия = await mining.start(session, constants, body, vein)
    await mining.swing(session, constants, сессия)
    await session.commit()

    assert float(vein.richness) == pytest.approx(было - constants[R.VEIN_RICHNESS_DECAY])


async def test_на_богатой_жиле_сосед_мешает(
    session: AsyncSession, constants: Constants
) -> None:
    """За такую жилу дерутся, её охраняют и лицензируют (D-099)."""
    богатая = constants[R.MINING_RICH_THRESHOLD] + 20
    _, vein, первый = await _забой(session, richness=богатая)
    сам = await mining.crowd_factor(constants, session, vein)

    await mining.start(session, constants, первый, vein)

    сосед = await world.create_identity(session, "Сосед")
    тело_соседа = await world.print_body(session, сосед, await _узел(session, vein))
    await mining.start(session, constants, тело_соседа, vein)

    вдвоём = await mining.crowd_factor(constants, session, vein)
    assert вдвоём < сам


async def test_на_бедной_жиле_сосед_помогает(
    session: AsyncSession, constants: Constants
) -> None:
    """Бедная жила кормит артель. Лучший вход в социальную игру (D-099)."""
    бедная = constants[R.MINING_RICH_THRESHOLD] - 20
    _, vein, первый = await _забой(session, richness=бедная)
    await mining.start(session, constants, первый, vein)

    второй = await world.create_identity(session, "Артельщик")
    тело = await world.print_body(session, второй, await _узел(session, vein))
    await mining.start(session, constants, тело, vein)

    assert await mining.crowd_factor(constants, session, vein) > 1.0


async def test_в_двух_забоях_сразу_не_бьют(
    session: AsyncSession, constants: Constants
) -> None:
    _, vein, body = await _забой(session)
    await mining.start(session, constants, body, vein)
    with pytest.raises(mining.SessionClosed):
        await mining.start(session, constants, body, vein)


async def test_до_жилы_надо_дойти_ногами(
    session: AsyncSession, constants: Constants
) -> None:
    """Информация идёт по Сети, материя требует присутствия (D-044)."""
    _, vein, _ = await _забой(session)
    другой = await world.create_node(session, "terra.far", "Далеко", area_m2=100)
    identity = await world.create_identity(session, "Гость")
    тело = await world.print_body(session, identity, другой)

    with pytest.raises(mining.NotHere):
        await mining.start(session, constants, тело, vein)


async def _узел(session: AsyncSession, vein: Vein):
    from octoverse.models.world import Node

    node = await session.get(Node, vein.node_id)
    assert node is not None
    return node
