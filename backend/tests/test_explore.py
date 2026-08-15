"""Разведка: карта прирастает ногами (D-152).

Проверяется то, ради чего разведка введена именно такой:

* заход стоит выносливости и времени, а без сил не начинается вовсе;
* находка — узел, связанный ребром с тем, откуда вышли: телепорта нет;
* порода жилы берётся из вольта (`gives` операции «Добыча»), а не из списка
  в коде — заведут пятую породу, она начнёт находиться сама;
* найденное **ничьё**: нашедший получает право первой ночи, а не собственность.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Catalog, Constants
from src.constants import registry as R
from src.engine import explore, world
from src.models.job import Job, JobKind, JobState
from src.models.world import Edge, Layer, Node, Vein
from src.units import MINUTES_PER_HOUR, SECONDS_PER_HOUR

#: Секунд в минуте — столько же, сколько минут в часе.
SECONDS_PER_MINUTE = MINUTES_PER_HOUR


async def _разведчик(session: AsyncSession):
    метка = uuid.uuid4().hex[:8]
    планета = await world.create_node(
        session, f"terra.{метка}", "Терра", area_m2=1, layer=Layer.SPACE
    )
    ворота = await world.create_node(
        session, f"terra.gate.{метка}", "Выход", area_m2=80,
        layer=Layer.PLANET, parent=планета,
    )
    identity = await world.create_identity(session, f"Разведчик-{метка}")
    body = await world.print_body(session, identity, ворота)
    return планета, ворота, body


async def _горожанин(session: AsyncSession, catalog):
    """Тело в городе: участок ищут изнутри города, а не с дороги (D-089)."""
    from src.engine import city as town

    метка = uuid.uuid4().hex[:8]
    планета = await world.create_node(
        session, f"terra.{метка}", "Терра", area_m2=1, layer=Layer.SPACE
    )
    представитель = await world.create_node(
        session, f"terra.city.{метка}", "Столица", area_m2=1,
        layer=Layer.PLANET, parent=планета,
    )
    ядро = await world.create_node(
        session, f"terra.city.{метка}.core", "Ядро", area_m2=100,
        parent=представитель, properties={"кольцо": 0},
    )
    город = await town.found(session, catalog, представитель, "Столица")
    ядро.owner_city_id = город.id
    await session.flush()
    identity = await world.create_identity(session, f"Горожанин-{метка}")
    body = await world.print_body(session, identity, ядро)
    return город, ядро, body


async def _исходить(session: AsyncSession, узел: Node, *, находок: int) -> None:
    """Проставить узлу счёт находок: столько раз отсюда уже уходили не зря."""
    узел.properties = {**(узел.properties or {}), explore.FOUND_HERE: находок}
    await session.flush()


async def _вернуть(session: AsyncSession, body) -> None:
    """Прокрутить заход до конца — так же, как это сделал бы воркер."""
    задание = (
        await session.execute(
            select(Job).where(
                Job.kind == JobKind.EXPLORE_SURVEY.value,
                Job.body_id == body.id,
                Job.state == JobState.PENDING,
            )
        )
    ).scalars().first()
    assert задание is not None
    await explore.returned(session, задание)
    задание.state = JobState.DONE
    await session.flush()


async def test_заход_стоит_выносливости(
    session: AsyncSession, constants: Constants
) -> None:
    """Платят по времени в поле: короткий заход дёшев, но не бесплатен (D-156)."""
    _, _, body = await _разведчик(session)
    было = float(body.stamina)
    await explore.survey(session, constants, body)
    списано = было - float(body.stamina)
    assert списано > 0, "разведка — работа, а не прогулка"
    assert списано < constants[R.EXPLORE_ATTEMPT_STAMINA], (
        "минутный заход не может стоить как заход полной длины"
    )


async def test_нехватка_сил_удлиняет_заход_а_не_запирает(
    session: AsyncSession, constants: Constants
) -> None:
    """Чего не хватило — разведчик досыпает в поле и продолжает.

    Заход по исхоженной окрестности идёт часами и стоит соответственно; тело с
    одной единицей уходит всё равно, но возвращается позже — на время сна по
    `body.hibernation_rate` — и с нулём выносливости.
    """
    _, ворота, body = await _разведчик(session)
    await _исходить(session, ворота, находок=10)
    body.stamina = Decimal("1")
    await session.flush()

    начало = datetime.now(UTC)
    заход = await explore.survey(session, constants, body, now=начало)
    assert float(body.stamina) == 0, "всё, что было, ушло в поле"

    #: Дольше потолка обычного захода: добавилось время сна.
    потолок = constants[R.EXPLORE_ATTEMPT_HOURS] * MINUTES_PER_HOUR
    шло = (заход.run_at - начало).total_seconds() / SECONDS_PER_MINUTE
    assert шло > потолок, "дефицит сил досыпается в поле, и заход длиннее"


async def test_разведчик_недоступен_как_спящий(
    session: AsyncSession, constants: Constants
) -> None:
    """Разведка — состояние тела: пока заход идёт, присутственное закрыто."""
    from src.engine import travel

    _, _, body = await _разведчик(session)
    await explore.survey(session, constants, body)
    with pytest.raises(travel.InField):
        await travel.require_here(session, body)


async def test_разведчик_никуда_не_уходит_ногами(
    session: AsyncSession, constants: Constants
) -> None:
    """Тело в поле — идти ему неоткуда: его нет в узле (D-152).

    Выход в дорогу проверяется той же дверью, что и всякое присутственное
    действие: держать для него отдельный список условий значит однажды забыть
    в нём строку — ровно так разведчик и уходил гулять по карте.
    """
    from src.engine import travel

    планета, ворота, body = await _разведчик(session)
    соседний = await world.create_node(
        session, f"terra.next.{uuid.uuid4().hex[:8]}", "Соседний", area_m2=100,
        layer=Layer.PLANET, parent=планета,
    )
    await travel.connect(session, ворота, соседний, base_seconds=30)

    await explore.survey(session, constants, body)
    with pytest.raises(travel.InField):
        await travel.depart(session, constants, body, соседний)
    assert body.node_id == ворота.id, "тело сдвинулось, оставаясь в разведке"

    #: Отменил заход — и дорога снова открыта.
    await explore.cancel(session, body)
    await travel.depart(session, constants, body, соседний)


async def test_отмена_возвращает_разведчика_сразу(
    session: AsyncSession, constants: Constants
) -> None:
    """Повернуть назад можно: заход снят, тело свободно, находки не будет."""
    from src.engine import travel

    _, _, body = await _разведчик(session)
    заход = await explore.survey(session, constants, body)
    await explore.cancel(session, body)

    await session.refresh(заход)
    assert заход.state is JobState.CANCELLED
    #: Тело снова в узле выхода и свободно для присутственного.
    await travel.require_here(session, body)
    #: Возвращаться повторно неоткуда.
    with pytest.raises(explore.NotOut):
        await explore.cancel(session, body)


async def test_второй_заход_одним_телом_не_идёт(
    session: AsyncSession, constants: Constants
) -> None:
    from src.engine import travel

    _, _, body = await _разведчик(session)
    await explore.survey(session, constants, body)
    with pytest.raises(travel.InField):
        await explore.survey(session, constants, body)


async def test_находка_встаёт_на_карту_ребром(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Найденный узел связан дорогой: по прямой в этом мире не ходят.

    Заход разыгрывается броском, поэтому проверяется не «нашёл всегда», а
    «если нашёл — нашёл правильно». Пустой заход — такая же норма.
    """
    _, ворота, body = await _разведчик(session)
    было = len((await session.execute(select(Node))).scalars().all())

    #: Несколько заходов подряд: с `explore.find_chance` меньше ста один заход
    #: может не дать ничего, и это не повод считать механику сломанной.
    for _ in range(12):
        body.stamina = Decimal(str(constants[R.BODY_STAMINA_MAX]))
        await session.flush()
        await explore.survey(session, constants, body, goal=explore.SITE)
        await _вернуть(session, body)

    узлы = (await session.execute(select(Node))).scalars().all()
    assert len(узлы) > было, "двенадцать заходов подряд не дали ничего"

    находки = [узел for узел in узлы if узел.key.startswith("terra.wild.")]
    for находка in находки:
        assert находка.layer is Layer.PLANET
        assert находка.owner_identity_id is None, "найденное ничьё"
        рёбра = (
            await session.execute(
                select(Edge).where(
                    (Edge.node_a_id == находка.id) | (Edge.node_b_id == находка.id)
                )
            )
        ).scalars().all()
        assert рёбра, "находка без дороги — это телепорт"


async def test_даль_растёт_и_дорога_дорожает(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Фронтир удаляется сам: находка от узла дали `d` встаёт на `d + 1`, и
    дорога к ней ровно во столько раз длиннее, во сколько велит вольт (D-180).
    """
    from src.engine import travel

    _, ворота, body = await _разведчик(session)
    #: Между заходами возвращаемся к воротам: удачный уводит на находку
    #: (D-185), а здесь проверяется первое кольцо от одного и того же узла.
    for _ in range(12):
        body.stamina = Decimal(str(constants[R.BODY_STAMINA_MAX]))
        body.node_id = ворота.id
        await session.flush()
        await explore.survey(session, constants, body, goal=explore.SITE)
        await _вернуть(session, body)

    находки = [
        узел
        for узел in (await session.execute(select(Node))).scalars().all()
        if узел.key.startswith("terra.wild.")
    ]
    assert находки, "двенадцать заходов подряд не дали ничего"

    #: Ворота города — даль 0, значит всё найденное отсюда встаёт на дали 1.
    for находка in находки:
        assert travel.reach_of(находка) == travel.reach_of(ворота) + 1
        ребро = (
            await session.execute(
                select(Edge).where(
                    (Edge.node_a_id == находка.id) | (Edge.node_b_id == находка.id)
                )
            )
        ).scalars().first()
        ожидаем = travel.frontier_seconds(constants, travel.reach_of(находка))
        assert ребро.base_seconds == pytest.approx(ожидаем, rel=0.01)

    #: Следующее кольцо дороже предыдущего — в этом весь смысл дали.
    шаги = [travel.frontier_seconds(constants, d) for d in (1, 2, 3, 4)]
    assert шаги == sorted(шаги) and шаги[0] < шаги[-1]
    рост = constants[R.TRAVEL_FRONTIER_GROWTH]
    assert шаги[1] == pytest.approx(шаги[0] * рост)


async def test_разведчик_остаётся_на_находке(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Нашёл — значит стоишь там, и следующий заход идёт уже оттуда (D-185).

    Отсюда цепочка: даль растёт шаг за шагом, а не звездой из одной точки.
    """
    from src.engine import travel

    _, ворота, body = await _разведчик(session)
    дали: list[int] = []
    for _ in range(14):
        body.stamina = Decimal(str(constants[R.BODY_STAMINA_MAX]))
        await session.flush()
        стояли = body.node_id
        await explore.survey(session, constants, body, goal=explore.SITE)
        await _вернуть(session, body)
        if body.node_id != стояли:
            узел = await session.get(Node, body.node_id)
            assert узел.key.startswith("terra.wild."), "ушли не на находку"
            дали.append(travel.reach_of(узел))

    assert дали, "четырнадцать заходов подряд не дали ни одной находки"
    #: Каждая следующая находка дальше предыдущей: фронтир двигают ногами.
    assert дали == sorted(дали)
    assert дали[0] == travel.reach_of(ворота) + 1


async def test_пустой_заход_оставляет_на_месте(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Идти было некуда: узел не появился, и разведчик там же, где вышел."""
    _, ворота, body = await _разведчик(session)
    #: Исхоженная окрестность отдаёт находку редко — тут это и нужно.
    await _исходить(session, ворота, находок=200)

    for _ in range(6):
        body.stamina = Decimal(str(constants[R.BODY_STAMINA_MAX]))
        body.node_id = ворота.id
        await session.flush()
        было = len((await session.execute(select(Node))).scalars().all())
        await explore.survey(session, constants, body, goal=explore.SITE)
        await _вернуть(session, body)
        стало = len((await session.execute(select(Node))).scalars().all())
        if стало == было:
            assert body.node_id == ворота.id, "пустой заход не двигает тело"


async def test_порода_берётся_из_вольта(
    constants: Constants, catalog: Catalog
) -> None:
    """Списка «какие бывают руды» в движке нет: он читает операцию «Добыча»."""
    import random

    добыча = next(
        op for op in catalog.recipes.operations if op.name == explore.MINING_OPERATION
    )
    выпало = {
        explore._resource(constants, catalog, random.Random(зерно))
        for зерно in range(200)
    }
    assert выпало, "порода не выбирается вовсе"
    assert выпало <= set(добыча.gives)
    #: Железо добывается быстрее прочего, значит и попадается чаще: вес — это
    #: темп из `harvest.rates`, второй таблицы редкости нет.
    assert "Железная руда" in выпало


async def test_у_жилы_есть_запас_и_богатство(
    session: AsyncSession, constants: Constants
) -> None:
    """Жилы конечны — это неотменяемо (столп П2)."""
    _, _, body = await _разведчик(session)
    for _ in range(12):
        body.stamina = Decimal(str(constants[R.BODY_STAMINA_MAX]))
        await session.flush()
        await explore.survey(session, constants, body, goal=explore.VEIN)
        await _вернуть(session, body)

    жилы = (await session.execute(select(Vein))).scalars().all()
    assert жилы, "двенадцать заходов за жилой не дали ни одной"
    богатство = constants[R.EXPLORE_VEIN_RICHNESS]
    for жила in жилы:
        assert жила.remaining > 0
        assert богатство.min <= float(жила.richness) <= богатство.max


# --- цена захода растёт с истощением места (D-156) --------------------------


async def test_первый_заход_идёт_минуты(
    session: AsyncSession, constants: Constants
) -> None:
    """Нехоженая окрестность отдаёт находку сразу.

    Первая локация обязана находиться за минуты: механика, ради которой карта
    прирастает ногами, не может открываться через шесть часов ожидания.
    """
    планета, ворота, body = await _разведчик(session)
    ушёл = datetime.now(UTC)
    задание = await explore.survey(session, constants, body, now=ушёл)

    заход = constants[R.EXPLORE_ATTEMPT_MINUTES]
    минут = (задание.run_at - ушёл).total_seconds() / SECONDS_PER_MINUTE
    assert заход.min <= минут <= заход.max


async def test_каждая_находка_удорожает_следующий_заход(
    session: AsyncSession, constants: Constants
) -> None:
    """Чем больше узлов открыто отсюда, тем дороже и реже следующий."""
    _, ворота, body = await _разведчик(session)
    ушёл = datetime.now(UTC)
    свежий = await explore.survey(session, constants, body, now=ушёл)
    свежих_минут = (свежий.run_at - ушёл).total_seconds() / SECONDS_PER_MINUTE
    свежий_шанс = explore.chance(constants, ворота)
    свежая_цена = float(свежий.payload["chance"])
    assert свежая_цена == pytest.approx(свежий_шанс)

    await _исходить(session, ворота, находок=4)
    свежий.state = JobState.DONE
    await session.flush()
    body.stamina = Decimal(str(constants[R.BODY_STAMINA_MAX]))
    исхоженный = await explore.survey(session, constants, body, now=ушёл)
    исхоженных_минут = (исхоженный.run_at - ушёл).total_seconds() / SECONDS_PER_MINUTE

    assert исхоженных_минут > свежих_минут, "исхоженное место обязано стоить дороже"
    assert explore.chance(constants, ворота) < свежий_шанс, "и находиться реже"


async def test_длительность_упирается_в_потолок(
    session: AsyncSession, constants: Constants
) -> None:
    """Рост не бесконечен: сутки на заход — это не сложность, а стена."""
    _, ворота, body = await _разведчик(session)
    await _исходить(session, ворота, находок=20)
    ушёл = datetime.now(UTC)
    задание = await explore.survey(session, constants, body, now=ушёл)
    часов = (задание.run_at - ушёл).total_seconds() / SECONDS_PER_HOUR
    assert часов == pytest.approx(constants[R.EXPLORE_ATTEMPT_HOURS])
    assert float(body.stamina) == pytest.approx(
        constants[R.BODY_STAMINA_MAX] - constants[R.EXPLORE_ATTEMPT_STAMINA]
    ), "заход полной длины стоит полную цену"


async def test_шанс_не_падает_ниже_пола(
    session: AsyncSession, constants: Constants
) -> None:
    """Исхоженная окрестность беднеет, но не запирается насовсем."""
    _, ворота, _ = await _разведчик(session)
    await _исходить(session, ворота, находок=200)
    assert explore.chance(constants, ворота) == pytest.approx(
        constants[R.EXPLORE_FIND_FLOOR]
    )


async def test_находка_истощает_место_а_пустой_заход_нет(
    session: AsyncSession, constants: Constants
) -> None:
    """Счёт растёт от удач: невезение не наказывает дважды.

    Удачный заход уводит разведчика на находку (D-185), поэтому между
    заходами тело возвращается к воротам — иначе истощался бы уже новый узел,
    а проверяем мы именно счёт исходного места.
    """
    _, ворота, body = await _разведчик(session)
    было = explore.found_here(ворота)
    находок = 0
    for _ in range(6):
        body.stamina = Decimal(str(constants[R.BODY_STAMINA_MAX]))
        body.node_id = ворота.id
        await session.flush()
        await explore.survey(session, constants, body)
        узлов = len((await session.execute(select(Node))).scalars().all())
        await _вернуть(session, body)
        if len((await session.execute(select(Node))).scalars().all()) > узлов:
            находок += 1
    assert находок, "шесть заходов по нехоженому месту не дали ничего"
    assert explore.found_here(ворота) == было + находок


async def test_свежая_находка_разведывается_снова_дёшево(
    session: AsyncSession, constants: Constants
) -> None:
    """Граница двигается: карта растёт вширь, а не звездой из точки рождения."""
    _, ворота, body = await _разведчик(session)
    await _исходить(session, ворота, находок=6)
    body.stamina = Decimal(str(constants[R.BODY_STAMINA_MAX]))
    await explore.survey(session, constants, body)
    await _вернуть(session, body)

    находки = [
        узел
        for узел in (await session.execute(select(Node))).scalars().all()
        if узел.key.startswith("terra.wild.")
    ]
    if not находки:
        pytest.skip("заход по исхоженному месту не дал находки — это норма")
    новый = находки[0]
    assert explore.chance(constants, новый) > explore.chance(constants, ворота)
    assert explore.found_here(новый) == 0


async def test_прогноз_показывает_цену_до_выхода(
    session: AsyncSession, constants: Constants
) -> None:
    """Цена, которую нельзя увидеть заранее, читается как случайность движка."""
    _, ворота, body = await _разведчик(session)
    свежий = await explore.outlook(session, constants, body)
    assert свежий is not None
    заход = constants[R.EXPLORE_ATTEMPT_MINUTES]
    assert свежий["minutes"] == {"min": заход.min, "max": заход.max}
    assert свежий["chance"] == pytest.approx(constants[R.EXPLORE_FIND_CHANCE])
    assert 0 < свежий["stamina"] < constants[R.EXPLORE_ATTEMPT_STAMINA]

    await _исходить(session, ворота, находок=4)
    исхоженный = await explore.outlook(session, constants, body)
    assert исхоженный is not None
    assert исхоженный["explored"] == 4
    assert исхоженный["minutes"]["max"] > свежий["minutes"]["max"]
    assert исхоженный["chance"] < свежий["chance"]
    assert исхоженный["stamina"] > свежий["stamina"]


# --- цели поиска (D-152) ----------------------------------------------------


async def test_участок_ищут_в_городе_и_он_городской(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Городскую землю не занимают — её раздаёт власть (D-089)."""
    город, ядро, body = await _горожанин(session, catalog)
    for _ in range(12):
        body.stamina = Decimal(str(constants[R.BODY_STAMINA_MAX]))
        await session.flush()
        await explore.survey(session, constants, body, goal=explore.LOT)
        await _вернуть(session, body)

    участки = [
        узел
        for узел in (await session.execute(select(Node))).scalars().all()
        if узел.properties.get("участок")
    ]
    assert участки, "двенадцать заходов в городе не дали ни одного участка"
    for участок in участки:
        assert участок.layer is Layer.CITY, "участок стоит в городе, а не в поле"
        assert участок.owner_city_id == город.id, "земля в кольцах — городская"
        assert участок.owner_identity_id is None, "раздаёт её власть, а не находка"


async def test_за_стенами_участок_не_ищут(
    session: AsyncSession, constants: Constants
) -> None:
    """За стенами городской застройки нет: искать там нечего.

    Отказ приходит **до** выхода: тратить три часа и выносливость на заведомо
    невозможную цель игрок не должен.
    """
    _, _, body = await _разведчик(session)
    было = float(body.stamina)
    with pytest.raises(explore.ExploreError):
        await explore.survey(session, constants, body, goal=explore.LOT)
    assert float(body.stamina) == было, "отказ не стоит выносливости"


async def test_названная_порода_находится_именно_она(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Ищут не «что-нибудь», а то, что нужно."""
    _, _, body = await _разведчик(session)
    for _ in range(20):
        body.stamina = Decimal(str(constants[R.BODY_STAMINA_MAX]))
        await session.flush()
        await explore.survey(
            session, constants, body, goal=explore.VEIN, resource="Медная руда"
        )
        await _вернуть(session, body)

    жилы = (await session.execute(select(Vein))).scalars().all()
    assert жилы, "двадцать заходов за медью не дали ни одной жилы"
    assert {жила.resource for жила in жилы} == {"Медная руда"}


async def test_редкое_ищется_хуже_частого(
    constants: Constants, catalog: Catalog
) -> None:
    """Иначе все искали бы только самое дорогое, и разведка стала бы краном."""
    железо = explore._aim(constants, catalog, explore.VEIN, "Железная руда")
    олово = explore._aim(constants, catalog, explore.VEIN, "Оловянная руда")
    вслепую = explore._aim(constants, catalog, explore.VEIN, None)
    assert вслепую == 1.0
    assert железо > олово, "редкая порода обязана искаться хуже частой"
    assert 0 < олово <= 1


async def test_несуществующую_породу_не_ищут(
    session: AsyncSession, constants: Constants
) -> None:
    _, _, body = await _разведчик(session)
    with pytest.raises(explore.ExploreError):
        await explore.survey(
            session, constants, body, goal=explore.VEIN, resource="Мифрил"
        )
