"""Сессия клиента — единственное место, где игрок действует.

Античит держится не на защите клиента, а на том, что **API действий не
существует** (60-meta/01-anti-cheat). Присутственное действие идёт только
отсюда и только после платы устройства (D-110).

Протокол намеренно скучный: JSON поверх WebSocket, одна команда — один ответ.
Ответ на любую команду добычи — `Sight`, то есть ровно то, что игрок видит.
Устойчивости свода там нет: она не «скрыта в интерфейсе», её не существует
в ответе вовсе.

Крафт живёт здесь по той же причине, хотя партия и идёт офлайн: **запуск** —
присутственное действие, и удобного REST для него не будет никогда. Прогноз
(`craft.plan`) и запуск (`craft.start`) разбирают заявку одним и тем же кодом —
иначе игрок видел бы одно число, а получал другое (D-092).

**Опознание аккаунта — заглушка разработки.** Настоящая аутентификация
приезжает вместе с подпиской (Э7, D-027), и притворяться, что она уже есть,
хуже, чем честно назвать заглушку заглушкой.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import asdict
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import current, current_catalog
from src.db.base import session_factory
from src.engine import (
    bank,
    breed,
    chat,
    coin,
    craft,
    death,
    energy,
    estate,
    explore,
    farm,
    food,
    gear,
    justice,
    ledger,
    market,
    mining,
    panel,
    rest,
    rig,
    road,
    station,
    transport,
    travel,
    utility,
    vote,
    world,
)
from src.engine import city as town
from src.engine import pow as device
from src.models.chat import Utterance
from src.models.city import Office, Power
from src.models.craft import BatchState, CraftBatch
from src.models.identity import Body, BodyState, Identity, Knowledge, KnowledgeKind
from src.models.inventory import Item
from src.models.justice import Case
from src.models.ledger import AccountKind
from src.models.market import (
    Order,
    OrderSide,
    OrderState,
    Reservation,
    ReservationState,
)
from src.models.mining import MiningSession, Pace, PowChallenge, SessionState
from src.models.plant import Nursery, Variety
from src.models.rig import Rig as RigRow
from src.models.vote import Vote
from src.models.world import Edge, Layer, Node, Vein
from src.telemetry import metrics
from src.units import amount_float, money_str

log = logging.getLogger(__name__)

router = APIRouter(tags=["сессия"])


class Refused(Exception):
    """Команда отклонена по правилам игры. Это не ошибка сервера."""


@router.websocket("/session/ws")
async def play(socket: WebSocket) -> None:
    await socket.accept()
    state: dict[str, Any] = {"identity_id": None}

    try:
        while True:
            message = await socket.receive_json()
            try:
                answer = await _dispatch(state, message)
            except Refused as refusal:
                answer = {"refused": str(refusal)}
            except mining.MiningError as refusal:
                answer = {"refused": str(refusal)}
            except craft.CraftError as refusal:
                answer = {"refused": str(refusal)}
            except market.MarketError as refusal:
                answer = {"refused": str(refusal)}
            except travel.TravelError as refusal:
                answer = {"refused": str(refusal)}
            except chat.ChatError as refusal:
                answer = {"refused": str(refusal)}
            except rest.RestError as refusal:
                answer = {"refused": str(refusal)}
            except farm.FarmError as refusal:
                answer = {"refused": str(refusal)}
            except food.FoodError as refusal:
                answer = {"refused": str(refusal)}
            except coin.CoinError as refusal:
                answer = {"refused": str(refusal)}
            except breed.BreedError as refusal:
                answer = {"refused": str(refusal)}
            except energy.EnergyError as refusal:
                answer = {"refused": str(refusal)}
            except rig.RigError as refusal:
                answer = {"refused": str(refusal)}
            except gear.GearError as refusal:
                answer = {"refused": str(refusal)}
            except station.StationError as refusal:
                answer = {"refused": str(refusal)}
            except transport.TransportError as refusal:
                answer = {"refused": str(refusal)}
            except road.RoadError as refusal:
                answer = {"refused": str(refusal)}
            except vote.VoteError as refusal:
                answer = {"refused": str(refusal)}
            except justice.JusticeError as refusal:
                answer = {"refused": str(refusal)}
            except bank.BankError as refusal:
                answer = {"refused": str(refusal)}
            except explore.ExploreError as refusal:
                answer = {"refused": str(refusal)}
            except death.DeathError as refusal:
                answer = {"refused": str(refusal)}
            except utility.UtilityError as refusal:
                answer = {"refused": str(refusal)}
            except town.CityError as refusal:
                answer = {"refused": str(refusal)}
            except estate.EstateError as refusal:
                answer = {"refused": str(refusal)}
            except device.PowError as refusal:
                answer = {"refused": str(refusal)}
            await socket.send_json(answer)
    except WebSocketDisconnect:
        #: Уход игрока не закрывает сессию добычи: она живёт до «уйти» либо
        #: до обрушения. Добытое лежит в забое и ждёт решения.
        log.info("сессия отключилась, личность %s", state.get("identity_id"))


async def _dispatch(state: dict[str, Any], message: dict[str, Any]) -> dict[str, Any]:
    command = message.get("cmd")
    if command is None:
        raise Refused("команда не названа")

    async with session_factory()() as db, db.begin():
        if command == "hello":
            return await _hello(state, db, message)
        #: Новый игрок — до опознания: опознавать ещё некого.
        if command == "join":
            return await _join(state, db, message)

        identity_id = state.get("identity_id")
        if identity_id is None:
            raise Refused("сначала hello")

        handler = _COMMANDS.get(command)
        if handler is None:
            raise Refused(f"нет такой команды: {command}")
        return await handler(state, db, message)


async def _hello(state: dict, db: AsyncSession, message: dict) -> dict:
    """Заглушка опознания: клиент называет личность по имени."""
    name = message.get("name")
    identity = (
        await db.execute(select(Identity).where(Identity.name == name))
    ).scalar_one_or_none()
    if identity is None:
        raise Refused(f"нет личности {name!r}")

    state["identity_id"] = identity.id
    body = await _body(db, identity.id)
    return {
        "hello": identity.name,
        #: Клиент считает плату устройства сам, а в оценку входит его аккаунт
        #: (D-112). Это не пропуск: опознание всё равно заглушка до Э7.
        "account": str(identity.account_id),
        "body": None if body is None else str(body.id),
        "node": None if body is None else str(body.node_id),
        "constants": current().digest,
    }


async def _join(state: dict, db: AsyncSession, message: dict) -> dict:
    """Новая личность и первое тело у биопринтера.

    На счету **ноль**: мир денег не выдаёт (D-153). Если город, где стоит
    биопринтер, решил платить подъёмные, они придут из его казны — и это будет
    видно в ответе. Ноль в ответе тоже честный: город беден или не платит.
    """
    name = str(message.get("name") or "").strip()
    if not name:
        raise Refused("имя не названо")

    где = await world.spawn_point(db)
    if где is None:
        raise Refused("мир ещё не создан: печататься негде")
    try:
        identity, body = await world.spawn(db, name, где)
    except ValueError as refusal:
        raise Refused(str(refusal)) from refusal

    state["identity_id"] = identity.id
    return {
        "hello": identity.name,
        "account": str(identity.account_id),
        "body": str(body.id),
        "node": str(body.node_id),
        "money": await _money(db, identity.id),
        "constants": current().digest,
    }


async def _look(state: dict, db: AsyncSession, message: dict) -> dict:
    """Что игрок видит о себе: тело, карман, счёт, дела, свои ордера.

    Личное идёт только сюда. Публичное чтение (`/public/*`) — про цены и
    справочники, и своего кармана там нет и не будет: цены знают все, а что у
    кого в мешке — нет.
    """
    identity = await _identity(state, db)
    body = await _body(db, identity.id)
    constants = current()

    seen: dict[str, Any] = {
        "identity": identity.name,
        "money": await _money(db, identity.id),
        "knows": await _knowledge(db, identity.id),
        #: Взятая агротехника — отдельным списком: клиент показывает в
        #: Библиотеке, какие культуры уже изучены, и не даёт брать дважды.
        "agrotech": await _knowledge(db, identity.id, kind=KnowledgeKind.AGROTECH),
        "orders": await _orders(db, identity.id),
        "reservations": await _reservations(db, identity.id),
        "batches": await _batches(db, identity.id),
    }
    if body is None:
        #: Тела нет — личность в облаке (D-012). Она по-прежнему распоряжается
        #: счётом и ордерами, но ничего не делает руками, поэтому наружу идёт
        #: единственное, что сейчас имеет смысл: где напечататься и почём.
        идёт = await death.pending(db, identity.id)
        return {
            "look": seen
            | {
                "body": None,
                "node": None,
                "inventory": [],
                "printers": await death.printers(db, constants, identity.id),
                "printing": (
                    None if идёт is None else {"ready_at": идёт.run_at.isoformat()}
                ),
            }
        }

    node = await db.get(Node, body.node_id)
    идёт = await travel.current(db, body)
    seen["body"] = {
        "id": str(body.id),
        "stamina": float(body.stamina),
        "sleeping_since": (
            None if body.sleeping_since is None else body.sleeping_since.isoformat()
        ),
        "sleeping_home": body.sleeping_home,
        "satiated_until": (
            None if body.satiated_until is None else body.satiated_until.isoformat()
        ),
    }

    #: В пути тело нигде: узел показывается тот, откуда вышли, но всё
    #: присутственное закрыто, и клиент обязан это видеть (D-107).
    seen["travel"] = None
    if идёт is not None:
        цель = await db.get(Node, идёт.to_node_id)
        откуда = await db.get(Node, идёт.from_node_id)
        seen["travel"] = {
            "to": цель.name if цель else "?",
            "to_key": цель.key if цель else "",
            "from_key": откуда.key if откуда else "",
            "started_at": идёт.started_at.isoformat(),
            "arrives_at": идёт.arrives_at.isoformat(),
        }
        #: Автопуть (D-045): показываем конечную цель и сколько узлов впереди —
        #: идущий обязан понимать, куда его несёт и как долго ещё.
        if идёт.plan:
            финал = await db.get(Node, uuid.UUID(идёт.plan[-1]))
            seen["travel"]["final"] = финал.name if финал else "?"
            seen["travel"]["final_key"] = финал.key if финал else ""
            seen["travel"]["legs_left"] = len(идёт.plan)

    хозяин = (
        None
        if node.owner_identity_id is None
        else await db.get(Identity, node.owner_identity_id)
    )
    станции = await _stations(db, node)
    seen["node"] = {
        "key": node.key,
        "name": node.name,
        "layer": node.layer.value,
        #: Библиотека — станок (D-176); свойство узла — наследие старых миров.
        "library": await world.is_library(db, node),
        #: Что в узле есть — по этому клиент решает, какие сцены показывать.
        "stations": станции,
        #: Свойства-признаки места («лес», «выход»): по ним клиент показывает
        #: добычу места (D-177) и прочие окна, привязанные к земле, а не к станку.
        "features": sorted(
            имя for имя, значение in (node.properties or {}).items()
            if значение is True
        ),
        #: Плодородие — свойство места (D-126): по нему видна сцена делянок.
        "fertility": float(node.properties.get("плодородие", 0) or 0),
        #: Чей участок: хозяйство ведёт владелец, чужое — по договору (D-116).
        "owner": None if хозяин is None else хозяин.name,
        "mine": node.owner_identity_id == identity.id,
        "city": node.owner_city_id is not None,
        #: Дикий и ничей: такой узел занимают присутственно (06-farming, D-152).
        "wild": node.owner_identity_id is None and node.owner_city_id is None,
        #: Отключён за неуплату: станки не работают, и игрок обязан это видеть
        #: сразу, иначе счётчик превращается в ловушку (D-149).
        "cut_off": await utility.cut_off(db, node),
        "area": float(node.area_m2),
    }
    #: Здание и вместимость: станок занимает площадь (D-106), и игрок обязан
    #: видеть, сколько мест осталось, до того как понесёт станок через полгорода.
    всего_мест, занято_мест = await estate.slots(db, constants, node)
    seen["node"]["building"] = {
        "area": await estate.built_area(db, node),
        "slots": всего_мест,
        "used": занято_мест,
    }
    #: Пустой городской участок продаётся: цену назначает город от удалённости
    #: до биопринтера (D-089). Игрок обязан видеть её до выкупа. Застройка и
    #: жилы города не продаются — у них цены нет вовсе.
    seen["node"]["price"] = None
    if (
        node.owner_identity_id is None
        and node.owner_city_id is not None
        and await estate.is_vacant(db, constants, node)
    ):
        город_участка = await town.by_id(db, node.owner_city_id)
        if город_участка is not None:
            try:
                seen["node"]["price"] = await estate.price_of(
                    db, constants, current_catalog(), город_участка, node
                )
            except estate.NotForSale:
                seen["node"]["price"] = None
    #: Город, на территории которого стоим, и свои полномочия в нём: по ним
    #: клиент решает, показывать ли администрацию (D-154).
    город = await town.of_node(db, node)
    seen["city"] = None
    if город is not None:
        seen["city"] = {
            "id": str(город.id),
            "name": город.name,
            "node": (await db.get(Node, город.node_id)).key,
            #: Права строками: крупные и точечные вперемешку (D-155).
            "powers": sorted(await town.powers_of(db, identity.id, город)),
            #: Управление присутственно: клиент показывает его только в
            #: администрации, а не в сайдбаре (D-155).
            "hall": town.HALL in станции,
            #: Гражданство (D-160): своё состояние в этом городе и порядок
            #: приёма. Клиент по ним решает, что показывать — «вступить»,
            #: «заявка подана» или «выйти».
            "citizen": await town.is_citizen(db, identity.id, город),
            "admission": town.admission(город),
            "requested": await town.request_of(db, identity.id, город) is not None,
        }
    #: Где состоит личность вообще: гражданство одно, и видно оно отовсюду —
    #: это запись о человеке, а не о месте.
    своё = await town.citizenship(db, identity.id)
    seen["citizenship"] = None
    if своё is not None:
        родной = await town.by_id(db, своё.city_id)
        seen["citizenship"] = {
            "city": None if родной is None else родной.name,
            "since": своё.since.isoformat(),
            "leaving_at": (
                None if своё.leaving_at is None else своё.leaving_at.isoformat()
            ),
        }
    #: Основание города (D-023, D-159): показывается только там, где оно вообще
    #: возможно — на своём узле планеты вне чужого города. Список недостающего
    #: обязан быть виден заранее: порог входа — постройки, и человек должен
    #: понимать, каких именно ему не хватает, а не упираться в отказ.
    seen["foundation"] = None
    if (
        город is None
        and node.layer is Layer.PLANET
        and node.owner_identity_id == identity.id
    ):
        seen["foundation"] = {
            "missing": list(await town.missing_for_foundation(db, node)),
            "needs": [
                {"role": роль, "any_of": list(чем)}
                for роль, чем in town.foundation_needs()
            ],
        }
    #: Идущий заход разведки: карта прирастает ногами, и ждать приходится
    #: по-настоящему (D-152).
    заход = await explore.pending(db, body)
    seen["survey"] = None if заход is None else {"returns_at": заход.run_at.isoformat()}

    seen["exits"] = [
        {
            "key": путь.key,
            "name": путь.name,
            "surface": путь.surface.value,
            "seconds": round(путь.seconds),
            #: Цена дороги телом (D-147): игрок обязан видеть её до выхода.
            #: Три знака, а не два: шаг по городу стоит тысячных, и округление
            #: до сотых показало бы ноль там, где цена есть.
            "stamina": round(
                travel.stamina_cost(
                    constants, путь.seconds,
                    transport=await travel.has_transport(db, body),
                ),
                3,
            ),
        }
        for путь in await travel.exits(db, constants, node)
    ]
    #: Что стоит в узле и кем занято: станок отдаётся одному (D-150).
    seen["bench"] = await _bench(db, node, body)
    #: Мебель отдельно от станков: за ней не работают, она обустраивает быт,
    #: и клиент показывает её своим окном.
    seen["furniture"] = await _bench(db, node, body, furniture=True)
    #: Свои ценные бумаги и бумаги, выставленные на продажу: электронные
    #: документы живут в Сети и видны отовсюду (D-116).
    seen["deeds"] = await _deeds(db, identity.id)
    seen["inventory"] = await _things(db, constants, await world.body_container(db, body))
    seen["stall"] = await _things(db, constants, await market.stall(db, node, identity.id))
    #: Носимое: сколько несёт, сколько может и что надето (D-146). Предел —
    #: причина существования повозок, и игрок обязан видеть его числом.
    надето = await gear.equipped(db, body)
    seen["carry"] = {
        "load": round(await gear.load_of(db, current_catalog(), body), 2),
        "capacity": round(await gear.capacity(db, constants, current_catalog(), body), 2),
        "slots": list(current_catalog().recipes.gear_slots),
        "equipped": {
            слот: {"id": str(вещь.id), "goods": вещь.type_key}
            for слот, вещь in надето.items()
        },
    }
    #: Обоз: во что впряжён, что везёт и сколько ещё влезет (D-157). Предел рук
    #: без этого — тупик: игрок обязан видеть, чем его обходят.
    seen["convoy"] = await transport.view(db, constants, current_catalog(), body)
    #: Что стоит в узле из транспорта: впрягаются в то, что рядом.
    seen["vehicles"] = await _vehicles(db, constants, node)
    #: Открытый забой переживает уход игрока: сессия живёт до «уйти» либо до
    #: обрушения. Клиент, вернувшись, обязан увидеть её на месте.
    открытая = (
        await db.execute(
            select(MiningSession).where(
                MiningSession.body_id == body.id,
                MiningSession.state == SessionState.ACTIVE,
            )
        )
    ).scalars().first()
    seen["mining"] = (
        None if открытая is None
        else _sight(открытая, await mining.sight(db, constants, открытая))
    )
    #: Каторжный забой видит только тот, кого держит тюрьма (D-174, D-176):
    #: постороннему жилы тюрьмы не показываются, как и тюремный принтер.
    забой_скрыт = await justice.is_prison(db, node) and not await justice.held(
        db, constants, identity.id
    )
    seen["veins"] = [] if забой_скрыт else [
        {"id": str(vein.id), "resource": vein.resource, "richness": float(vein.richness)}
        for vein in (
            await db.execute(select(Vein).where(Vein.node_id == node.id))
        ).scalars().all()
    ]
    return {"look": seen}


async def _challenge(state: dict, db: AsyncSession, message: dict) -> dict:
    """Выдать задачу платы устройства. Клиент считает её в Web Worker."""
    identity = await db.get(Identity, state["identity_id"])
    if identity is None:  # pragma: no cover
        raise Refused("личность исчезла")
    task = await device.issue(db, current(), identity.account_id)
    return {"challenge": str(task.id), "nonce": task.nonce.hex()}


async def _mine_start(state: dict, db: AsyncSession, message: dict) -> dict:
    """Открыть забой. Без оплаченной задачи сессия не начинается."""
    constants = current()
    body = await _body(db, state["identity_id"])
    if body is None:
        raise Refused("нет живого тела")

    task = await db.get(PowChallenge, uuid.UUID(message["challenge"]))
    if task is None or task.account_id != (await db.get(Identity, body.identity_id)).account_id:
        raise Refused("задача не ваша")
    await device.verify(db, constants, task, bytes.fromhex(message["answer"]))

    vein = await db.get(Vein, uuid.UUID(message["vein"]))
    if vein is None:
        raise Refused("нет такой жилы")

    session = await mining.start(
        db,
        constants,
        body,
        vein,
        tool_item_id=_optional_uuid(message.get("tool")),
        pace=Pace(message.get("pace", Pace.STEADY.value)),
    )
    task.spent_on_session_id = session.id
    state["session_id"] = session.id
    return _sight(session, await mining.sight(db, constants, session))


async def _mine_swing(state: dict, db: AsyncSession, message: dict) -> dict:
    session = await _active(state, db)
    return _sight(session, await mining.swing(db, current(), session))


async def _mine_timber(state: dict, db: AsyncSession, message: dict) -> dict:
    session = await _active(state, db)
    return _sight(session, await mining.timber(db, current(), session))


async def _mine_pace(state: dict, db: AsyncSession, message: dict) -> dict:
    session = await _active(state, db)
    pace = Pace(message["pace"])
    return _sight(session, await mining.set_pace(db, current(), session, pace))


async def _mine_leave(state: dict, db: AsyncSession, message: dict) -> dict:
    session = await _active(state, db)
    haul = await mining.leave(db, current(), session)
    state.pop("session_id", None)
    return {"left": True, "haul": haul}


async def _craft_plan(state: dict, db: AsyncSession, message: dict) -> dict:
    """Прогноз до партии. Ничего не тратит и ничего не резервирует (D-092)."""
    body = await _alive(state, db)
    output, units, extra = _craft_request(message)
    plan = await craft.plan(db, current(), current_catalog(), body, output, units, **extra)
    return {"plan": asdict(plan)}


async def _craft_start(state: dict, db: AsyncSession, message: dict) -> dict:
    """Запустить партию. Дальше она идёт сама, в том числе пока игрок офлайн."""
    body = await _alive(state, db)
    output, units, extra = _craft_request(message)
    batch = await craft.start(db, current(), current_catalog(), body, output, units, **extra)
    return {
        "batch": str(batch.id),
        "output": batch.output,
        "quality": float(batch.quality),
        "ready_at": batch.ready_at.isoformat(),
    }


async def _craft_repair(state: dict, db: AsyncSession, message: dict) -> dict:
    """Починить вещь: состояние вернётся, потолок опустится (15-quality)."""
    body = await _alive(state, db)
    item = await _own_item(db, body, message["item"])
    batch = await craft.repair(db, current(), current_catalog(), body, item)
    return {"batch": str(batch.id), "ready_at": batch.ready_at.isoformat()}


async def _craft_recycle(state: dict, db: AsyncSession, message: dict) -> dict:
    """Разобрать вещь на часть материалов. Возврат всегда меньше вложенного."""
    body = await _alive(state, db)
    item = await _own_item(db, body, message["item"])
    batch = await craft.recycle(db, current(), current_catalog(), body, item)
    return {"batch": str(batch.id), "ready_at": batch.ready_at.isoformat()}


async def _library_copy(state: dict, db: AsyncSession, message: dict) -> dict:
    """Взять рецепт в Библиотеке: бесплатно, без условий, но только придя (D-053)."""
    body = await _alive(state, db)
    key = message["recipe"]
    await craft.copy_recipe(db, current_catalog(), body, key)
    return {"learned": key}


async def _land_claim(state: dict, db: AsyncSession, message: dict) -> dict:
    """Занять участок: присутственно, в диком узле (06-farming)."""
    body = await _alive(state, db)
    node = await db.get(Node, body.node_id)
    if node is None:  # pragma: no cover
        raise Refused("тело вне узла")
    try:
        await world.claim_node(db, body, node)
    except world.LandError as refusal:
        raise Refused(str(refusal)) from refusal
    return {"claimed": node.key}


async def _land_buy(state: dict, db: AsyncSession, message: dict) -> dict:
    """Выкупить пустой городской участок: цена от удалённости до биопринтера.

    Выручка в казну города, покупателю — ценная бумага (D-089, D-116).
    """
    body = await _alive(state, db)
    node = await db.get(Node, body.node_id)
    if node is None:  # pragma: no cover
        raise Refused("тело вне узла")
    deed = await estate.buy(db, current(), current_catalog(), body, node)
    return {
        "bought": node.key,
        "deed": str(deed.id),
        "paid": deed.paid,
        "money": await _money(db, state["identity_id"]),
    }


async def _build_construct(state: dict, db: AsyncSession, message: dict) -> dict:
    """Построить здание на своём участке. Материалы сразу, здание по сроку."""
    body = await _alive(state, db)
    node = await db.get(Node, body.node_id)
    if node is None:  # pragma: no cover
        raise Refused("тело вне узла")
    job = await estate.construct(db, current(), body, node, float(message["area"]))
    return {"building": True, "ready_at": job.run_at.isoformat()}


async def _deed_offer(state: dict, db: AsyncSession, message: dict) -> dict:
    """Выставить свою бумагу на продажу: всем либо адресно. Удалённое."""
    identity = await _identity(state, db)
    deed = await _deed(db, message)
    кому = message.get("to")
    await estate.offer_deed(
        db, identity, deed,
        int(message.get("price") or 0),
        to=None if not кому else await _identity_by_name(db, str(кому)),
    )
    return {"offered": str(deed.id), "price": deed.sale_price}


async def _deed_buy(state: dict, db: AsyncSession, message: dict) -> dict:
    """Купить выставленную бумагу: деньги продавцу, титул покупателю. Удалённое."""
    identity = await _identity(state, db)
    deed = await _deed(db, message)
    await estate.buy_deed(db, identity, deed)
    return {"deed": str(deed.id), "money": await _money(db, identity.id)}


async def _deed_market(state: dict, db: AsyncSession, message: dict) -> dict:
    """Бумаги, которые можно купить: открытые и адресованные этой личности."""
    rows = await estate.deeds_on_sale(db, state["identity_id"])
    return {"deeds": [await _deed_view(db, deed) for deed in rows]}


async def _deed(db: AsyncSession, message: dict):
    from src.models.estate import Deed

    deed = await db.get(Deed, uuid.UUID(message["deed"]))
    if deed is None:
        raise Refused("нет такой бумаги")
    return deed


async def _deed_view(db: AsyncSession, deed) -> dict[str, Any]:
    node = await db.get(Node, deed.node_id)
    владелец = await db.get(Identity, deed.owner_identity_id)
    кому = (
        None
        if deed.sale_to_identity_id is None
        else await db.get(Identity, deed.sale_to_identity_id)
    )
    return {
        "id": str(deed.id),
        "node": None if node is None else node.key,
        "name": None if node is None else node.name,
        "area": None if node is None else float(node.area_m2),
        "owner": None if владелец is None else владелец.name,
        "paid": deed.paid,
        "sale_price": deed.sale_price,
        "sale_to": None if кому is None else кому.name,
        "issued_at": deed.issued_at.isoformat(),
    }


async def _deeds(db: AsyncSession, identity_id: uuid.UUID) -> list[dict[str, Any]]:
    """Свои бумаги — для вкладки «хозяйство» сайдбара (D-116)."""
    return [
        await _deed_view(db, deed) for deed in await estate.deeds_of(db, identity_id)
    ]


async def _farm_mark(state: dict, db: AsyncSession, message: dict) -> dict:
    body = await _alive(state, db)
    plot = await farm.mark(
        db, current(), body,
        name=str(message.get("name", "")),
        area=float(message["area"]),
    )
    return {"plot": str(plot.id)}


async def _farm_plow(state: dict, db: AsyncSession, message: dict) -> dict:
    body = await _alive(state, db)
    plot = await farm.plow(db, current(), body, await _plot(db, message))
    return {"plowing": str(plot.id)}


async def _farm_sow(state: dict, db: AsyncSession, message: dict) -> dict:
    """Посеять семенами сорта: у партии есть и сорт, и своя сила (D-057)."""
    body = await _alive(state, db)
    seeds = await _own_item(db, body, message["seeds"])
    plot = await farm.sow(
        db, current(), current_catalog(), body, await _plot(db, message), seeds
    )
    return {
        "sown": str(plot.id),
        "culture": plot.culture_id,
        "vigor": None if plot.seed_vigor is None else float(plot.seed_vigor),
    }


async def _farm_care(state: dict, db: AsyncSession, message: dict) -> dict:
    body = await _alive(state, db)
    plot = await farm.care(db, current(), body, await _plot(db, message))
    return {"cared": str(plot.id), "credits": plot.care_credits}


async def _farm_harvest(state: dict, db: AsyncSession, message: dict) -> dict:
    """Убрать урожай. С отбором фонд держит силу, без — вырождается (D-067)."""
    body = await _alive(state, db)
    got = await farm.harvest(
        db, current(), current_catalog(), body, await _plot(db, message),
        select_seed=bool(message.get("select")),
    )
    return {"harvested": got, "selected": bool(message.get("select"))}


async def _breed_cross(state: dict, db: AsyncSession, message: dict) -> dict:
    """Скрестить два сорта в питомнике: результат — через полный цикл."""
    body = await _alive(state, db)
    один = await _own_item(db, body, message["a"])
    другой = await _own_item(db, body, message["b"])
    питомник = await breed.cross(db, current(), current_catalog(), body, один, другой)
    return {"nursery": str(питомник.id), "ready_at": питомник.ready_at.isoformat()}


async def _breed_gather(state: dict, db: AsyncSession, message: dict) -> dict:
    """Забрать всходы. Пусто — сорт вышел слишком похожим и не взошёл (D-067)."""
    body = await _alive(state, db)
    питомник = await db.get(Nursery, uuid.UUID(message["nursery"]))
    if питомник is None:
        raise Refused("нет такого питомника")
    сорт = await breed.gather_cross(db, current(), current_catalog(), body, питомник)
    if сорт is None:
        return {"sprouted": False}
    return {"sprouted": True, "variety": str(сорт.id), "traits": сорт.traits}


async def _breed_name(state: dict, db: AsyncSession, message: dict) -> dict:
    """Назвать выведенный сорт: имя автора закрепляется за ним навсегда."""
    body = await _alive(state, db)
    сорт = await db.get(Variety, uuid.UUID(message["variety"]))
    if сорт is None:
        raise Refused("нет такого сорта")
    сорт = await breed.name_variety(db, body, сорт, str(message["name"]))
    return {"variety": str(сорт.id), "name": сорт.name}


async def _breed_agrotech(state: dict, db: AsyncSession, message: dict) -> dict:
    """Взять агротехнику базовой культуры в Библиотеке: бесплатно, но ногами."""
    body = await _alive(state, db)
    знание = await breed.copy_agrotech(
        db, current_catalog(), body, str(message["culture"])
    )
    return {"learned": знание is not None, "culture": message["culture"]}


async def _breed_varieties(state: dict, db: AsyncSession, message: dict) -> dict:
    """Свои сорта и идущие скрещивания. Удалённое: смотреть можно откуда угодно."""
    body = await _body(db, state["identity_id"])
    сорта = (
        await db.execute(
            select(Variety).where(Variety.author_identity_id == state["identity_id"])
        )
    ).scalars().all()
    питомники = (
        await db.execute(
            select(Nursery).where(
                Nursery.body_id == (body.id if body else None),
                Nursery.done.is_(False),
            )
        )
    ).scalars().all() if body else []
    return {
        "varieties": [
            {
                "id": str(с.id),
                "name": с.name,
                "culture": с.culture_id,
                "stable": с.stable,
                "generation": с.generation,
                "traits": с.traits,
            }
            for с in сорта
        ],
        "nurseries": [
            {"id": str(п.id), "ready_at": п.ready_at.isoformat()} for п in питомники
        ],
    }


async def _farm_split(state: dict, db: AsyncSession, message: dict) -> dict:
    body = await _alive(state, db)
    piece = await farm.split(
        db, current(), body, await _plot(db, message),
        float(message["area"]),
        name=str(message.get("name", "")),
    )
    return {"piece": str(piece.id)}


async def _farm_survey(state: dict, db: AsyncSession, message: dict) -> dict:
    """Сводка хозяйства. Удалённое: читается хоть с дороги (D-118)."""
    rows = await farm.survey(db, current(), current_catalog(), state["identity_id"])
    return {"plots": rows}


async def _plot(db: AsyncSession, message: dict):
    from src.models.farm import Plot

    plot = await db.get(Plot, uuid.UUID(message["plot"]))
    if plot is None:
        raise Refused("нет такой делянки")
    return plot


async def _food_eat(state: dict, db: AsyncSession, message: dict) -> dict:
    """Съесть порцию. Работает и в дороге: сухарь в пути — норма (D-091)."""
    body = await _body(db, state["identity_id"])
    if body is None:
        raise Refused("нет живого тела")
    item = await _own_item(db, body, message["item"])
    restored = await food.eat(db, current(), current_catalog(), body, item)
    return {"restored": round(restored, 2), "stamina": float(body.stamina)}


async def _cook_pot(state: dict, db: AsyncSession, message: dict) -> dict:
    """Сварить котёл: роли вместо состава, порций — cook.pot_portions (D-119)."""
    body = await _alive(state, db)
    batch = await craft.cook(
        db, current(), current_catalog(), body,
        str(message["output"]),
        dict(message.get("filling") or {}),
    )
    return {
        "batch": str(batch.id),
        "flavor": batch.flavor,
        "quality": float(batch.quality),
        "ready_at": batch.ready_at.isoformat(),
    }


async def _coin_mint(state: dict, db: AsyncSession, message: dict) -> dict:
    """Отчеканить монету. Проба одна на весь мир — 900‰, выбора нет (D-016)."""
    body = await _alive(state, db)
    batch = await coin.mint(
        db, current(), current_catalog(), body,
        str(message["coin"]),
        float(message["count"]),
    )
    return {
        "batch": str(batch.id),
        "coin": batch.output,
        "units": amount_float(batch.units),
        "fineness": float(batch.fineness),
        "spent": batch.spent,
        "ready_at": batch.ready_at.isoformat(),
    }


async def _coin_melt(state: dict, db: AsyncSession, message: dict) -> dict:
    """Переплавить монеты: металла вернётся по их пробе, минус угар."""
    body = await _alive(state, db)
    item = await _own_item(db, body, message["item"])
    batch = await coin.melt(
        db, current(), current_catalog(), body, item, float(message["count"])
    )
    return {
        "batch": str(batch.id),
        "coin": batch.output,
        "units": amount_float(batch.units),
        "fineness": float(batch.fineness),
        "ready_at": batch.ready_at.isoformat(),
    }


async def _gear_equip(state: dict, db: AsyncSession, message: dict) -> dict:
    """Надеть вещь. Слот один на вещь: три рюкзака не наденешь (D-146)."""
    body = await _alive(state, db)
    item = await _own_item(db, body, message["item"])
    слот = await gear.equip(db, current(), current_catalog(), body, item)
    return {
        "equipped": слот,
        "goods": item.type_key,
        "capacity": round(
            await gear.capacity(db, current(), current_catalog(), body), 2
        ),
    }


async def _gear_unequip(state: dict, db: AsyncSession, message: dict) -> dict:
    """Снять надетое. Вещь остаётся в руках — она и так была там."""
    body = await _alive(state, db)
    снято = await gear.unequip(db, body, str(message["slot"]))
    return {
        "unequipped": None if снято is None else снято.type_key,
        "capacity": round(
            await gear.capacity(db, current(), current_catalog(), body), 2
        ),
    }


async def _world_metrics(state: dict, db: AsyncSession, message: dict) -> dict:
    """Сводка мира: агрегаты и проверки инвариантов (60-meta/04).

    Удалённое чтение: цифры мира не привязаны к месту. Персонального здесь нет
    ничего — только агрегаты, и это не служебная тайна, а решение о приватности.
    """
    constants = current()
    return {
        "metrics": await metrics.collect(db, constants),
        "invariants": await metrics.invariants(db, constants),
    }


async def _market_offers(state: dict, db: AsyncSession, message: dict) -> dict:
    """Чужие заявки на продажу в узле: то, что можно забронировать (D-047).

    Стакан публичен уровнями, а бронь берётся из конкретной заявки — значит
    заявки надо назвать. Имя продавца не показывается: в стакане торгуют
    товаром, а не репутацией.
    """
    identity_id = state["identity_id"]
    узел = await _node(db, message["node"]) if message.get("node") else None
    if узел is None:
        body = await _body(db, identity_id)
        if body is None:
            raise Refused("нет живого тела")
        узел = await db.get(Node, body.node_id)

    rows = (
        await db.execute(
            select(Order).where(
                Order.node_id == узел.id,
                Order.side == OrderSide.SELL,
                Order.state == OrderState.ACTIVE,
                Order.identity_id != identity_id,
            ).order_by(Order.price)
        )
    ).scalars().all()
    return {
        "offers": [
            {
                "id": str(заявка.id),
                "goods": заявка.type_key,
                "tier": заявка.tier,
                "price": заявка.price,
                "left": amount_float(заявка.amount_left),
            }
            for заявка in rows
            if заявка.amount_left > 0
        ]
    }


async def _market_reserve(state: dict, db: AsyncSession, message: dict) -> dict:
    """Забронировать партию с задатком. Удалённое: бронь и есть план рейса."""
    identity = await _identity(state, db)
    order = await db.get(Order, uuid.UUID(message["order"]))
    if order is None:
        raise Refused("нет такой заявки")
    бронь = await market.reserve(
        db, current(), identity, order, float(message["amount"])
    )
    return {
        "reservation": str(бронь.id),
        "deposit": money_str(бронь.deposit),
        "expires_at": бронь.expires_at.isoformat(),
        "money": await _money(db, identity.id),
    }


async def _market_redeem(state: dict, db: AsyncSession, message: dict) -> dict:
    """Выкупить бронь: доплатить остаток и забрать. Присутственно (D-047)."""
    body = await _alive(state, db)
    бронь = await db.get(Reservation, uuid.UUID(message["reservation"]))
    if бронь is None:
        raise Refused("нет такой брони")
    сделка = await market.redeem(db, current(), current_catalog(), body, бронь)
    return {
        "trade": str(сделка.id),
        "goods": сделка.type_key,
        "amount": amount_float(сделка.amount),
        "money": await _money(db, state["identity_id"]),
    }


async def _rig_place(state: dict, db: AsyncSession, message: dict) -> dict:
    """Поставить буровую на жилу. Дальше она работает без игрока (D-115)."""
    body = await _alive(state, db)
    item = await _own_item(db, body, message["item"])
    vein = await db.get(Vein, uuid.UUID(message["vein"]))
    if vein is None:
        raise Refused("нет такой жилы")
    установка = await rig.place(db, body, item, vein)
    return {"rig": str(установка.id), "vein": vein.resource}


async def _rig_status(state: dict, db: AsyncSession, message: dict) -> dict:
    """Что стоит в узле: бункер, топливо, состояние. Присутственная сцена."""
    body = await _body(db, state["identity_id"])
    if body is None:
        raise Refused("нет живого тела")
    return {"rigs": await rig.status(db, current(), body.node_id)}


async def _rig_empty(state: dict, db: AsyncSession, message: dict) -> dict:
    """Вывезти бункер. Ногами: без возчика предприятие стоит."""
    body = await _alive(state, db)
    установка = await db.get(RigRow, uuid.UUID(message["rig"]))
    if установка is None:
        raise Refused("нет такой установки")
    взято = await rig.empty_hopper(db, current(), body, установка)
    return {"taken": взято}


async def _energy_grid(state: dict, db: AsyncSession, message: dict) -> dict:
    """Пул города, где стоишь. Пустой пул виден всем: это политика (D-071)."""
    body = await _body(db, state["identity_id"])
    if body is None:
        raise Refused("нет живого тела")
    node = await db.get(Node, body.node_id)
    pool = await energy.pool_of(db, current(), node, create=False)
    if pool is None:
        return {"grid": None}
    await energy.produce(db, current(), pool)
    город = await db.get(Node, pool.node_id)
    return {
        "grid": {
            "city": город.name if город else "?",
            "stored": round(float(pool.stored), 1),
            "tariff": float(pool.tariff),
        }
    }


async def _energy_charge(state: dict, db: AsyncSession, message: dict) -> dict:
    """Зарядить аккумулятор из пула по тарифу. Присутственно и платно (D-085)."""
    body = await _alive(state, db)
    item = await _own_item(db, body, message["item"])
    сколько = message.get("amount")
    дали = await energy.charge_battery(
        db, current(), body, item, None if сколько is None else float(сколько)
    )
    return {
        "charged": round(дали, 2),
        "charge": round(float(item.charge), 2),
        "money": await _money(db, state["identity_id"]),
    }


async def _rest_sleep(state: dict, db: AsyncSession, message: dict) -> dict:
    """Лечь спать. Восстановление идёт офлайн — тик ему не нужен (D-091)."""
    body = await _alive(state, db)
    await rest.sleep(db, current(), body)
    return {"sleeping": True, "home": body.sleeping_home}


async def _rest_wake(state: dict, db: AsyncSession, message: dict) -> dict:
    body = await _alive(state, db)
    restored = await rest.wake(db, current(), body)
    return {"woke": True, "restored": round(restored, 2), "stamina": float(body.stamina)}


async def _chat_say(state: dict, db: AsyncSession, message: dict) -> dict:
    """Сказать в локации. Тип обязателен: речь, действие или вне игры (D-050)."""
    body = await _alive(state, db)
    said = await chat.say(
        db,
        current(),
        body,
        str(message.get("text", "")),
        kind=Utterance(message.get("kind", Utterance.SPEECH.value)),
        quiet=bool(message.get("quiet", False)),
    )
    return {"said": str(said.id), "leaked": said.leaked}


async def _chat_hear(state: dict, db: AsyncSession, message: dict) -> dict:
    """Что слышно и кто с кем шепчется. Разговор в комнате — только из комнаты."""
    body = await _alive(state, db)
    return {
        "lines": [
            {
                "id": line.id,
                "who": line.who,
                "kind": line.kind.value,
                "quiet": line.quiet,
                "text": line.text,
                "overheard": line.overheard,
                "source": line.source,
                "at": line.at.isoformat(),
            }
            for line in await chat.hear(db, body)
        ],
        "circles": [
            {
                "id": circle.id,
                "name": circle.name,
                "members": list(circle.members),
                "mine": circle.mine,
            }
            for circle in await chat.circles(db, body)
        ],
    }


async def _chat_gather(state: dict, db: AsyncSession, message: dict) -> dict:
    """Собрать кружок. Видно всем: «эти о чём-то договариваются» (D-043)."""
    body = await _alive(state, db)
    group = await chat.gather(db, body, name=message.get("name") or None)
    return {"circle": str(group.id)}


async def _chat_join(state: dict, db: AsyncSession, message: dict) -> dict:
    body = await _alive(state, db)
    await chat.join(db, body, uuid.UUID(message["circle"]))
    return {"joined": message["circle"]}


async def _chat_leave(state: dict, db: AsyncSession, message: dict) -> dict:
    await chat.leave_groups(db, state["identity_id"])
    return {"left": True}


async def _travel_go(state: dict, db: AsyncSession, message: dict) -> dict:
    """Выйти в узел — хоть несоседний: маршрут строится сам (D-045, D-107)."""
    body = await _alive(state, db)
    цель = await _node(db, message["node"])
    переход = await travel.depart(db, current(), body, цель)
    return {
        "travel": str(переход.id),
        "to": цель.name,
        "arrives_at": переход.arrives_at.isoformat(),
        "legs_left": len(переход.plan or []),
    }


async def _market_load(state: dict, db: AsyncSession, message: dict) -> dict:
    """Загрузить товар в терминал. Присутственное: везут ногами (D-047)."""
    body = await _alive(state, db)
    moved = await market.load(
        db, current(), body, message["goods"], float(message["amount"])
    )
    return {"loaded": moved, "goods": message["goods"]}


async def _market_take(state: dict, db: AsyncSession, message: dict) -> dict:
    """Забрать своё из терминала — и купленное тоже. Тоже ногами."""
    body = await _alive(state, db)
    moved = await market.take(
        db,
        current(),
        body,
        message["goods"],
        float(message["amount"]),
        tier=message.get("tier"),
    )
    return {"taken": moved, "goods": message["goods"]}


async def _market_sell(state: dict, db: AsyncSession, message: dict) -> dict:
    """Выставить заявку на продажу. Удалённое: товар уже привезён."""
    identity = await _identity(state, db)
    node = await _node(db, message["node"])
    fill = await market.sell(
        db,
        current(),
        current_catalog(),
        identity,
        node,
        type_key=message["goods"],
        tier=message["tier"],
        price=int(message["price"]),
        quantity=float(message["amount"]),
    )
    return _fill(fill)


async def _market_buy(state: dict, db: AsyncSession, message: dict) -> dict:
    """Купить: лимитная заявка от присутствующего тела.

    Узел не называется намеренно — покупают там, где стоят.
    """
    body = await _alive(state, db)
    fill = await market.buy(
        db,
        current(),
        current_catalog(),
        body,
        type_key=message["goods"],
        tier=message["tier"],
        price=int(message["price"]),
        quantity=float(message["amount"]),
    )
    return _fill(fill)


async def _market_cancel(state: dict, db: AsyncSession, message: dict) -> dict:
    """Снять ордер. Распоряжение присутствия не требует."""
    identity_id = state["identity_id"]
    order = await db.get(Order, uuid.UUID(message["order"]))
    if order is None:
        raise Refused("нет такого ордера")
    await market.cancel(db, order, by=identity_id)
    return {"cancelled": str(order.id)}


async def _body_printers(state: dict, db: AsyncSession, message: dict) -> dict:
    """Где можно напечататься и почём (D-033). Читается из облака — всегда.

    Личность живёт в Сети, а не в теле: список доступен и мёртвому, в этом
    весь смысл — заложника у города не существует.
    """
    идёт = await death.pending(db, state["identity_id"])
    return {
        "printers": await death.printers(db, current(), state["identity_id"]),
        "printing": None if идёт is None else {"ready_at": идёт.run_at.isoformat()},
    }


async def _body_print(state: dict, db: AsyncSession, message: dict) -> dict:
    """Заказать печать тела. Плата снимается вперёд, тело приходит по сроку."""
    identity = await _identity(state, db)
    node = await _node(db, str(message["node"]))
    job = await death.order(db, current(), current_catalog(), identity, node)
    return {
        "printing": {"node": node.key, "ready_at": job.run_at.isoformat()},
        "money": await _money(db, identity.id),
    }


async def _station_place(state: dict, db: AsyncSession, message: dict) -> dict:
    """Поставить станок в узел. Присутственно и только у себя (D-150)."""
    body = await _alive(state, db)
    item = await _own_item(db, body, message["item"])
    await station.place(db, current_catalog(), body, item)
    return {"placed": item.type_key}


async def _station_take(state: dict, db: AsyncSession, message: dict) -> dict:
    """Забрать станок обратно в руки. Занятый работой не отдаётся."""
    body = await _alive(state, db)
    item = await db.get(Item, uuid.UUID(message["item"]))
    if item is None:
        raise Refused("нет такого предмета")
    await station.take(db, current_catalog(), body, item)
    return {"taken": item.type_key}


async def _road_lay(state: dict, db: AsyncSession, message: dict) -> dict:
    """Уложить ступень покрытия на ребро либо подсыпать просевшую (D-158).

    Полотно списывается сразу, дорога ложится по сроку: работа идёт офлайн,
    как всякая длительная.
    """
    body = await _alive(state, db)
    edge = await db.get(Edge, uuid.UUID(message["edge"]))
    if edge is None:
        raise Refused("нет такого ребра")
    job = await road.lay(
        db, current(), current_catalog(), body, edge,
        mend=bool(message.get("mend")),
    )
    return {"road": str(job.id), "ready_at": job.run_at.isoformat()}


async def _road_here(state: dict, db: AsyncSession, message: dict) -> dict:
    """Дороги из этого узла: что уложено, что просело и чего это стоит."""
    body = await _alive(state, db)
    return {"roads": await road.view(db, current(), body)}


async def _transport_harness(state: dict, db: AsyncSession, message: dict) -> dict:
    """Впрячься в стоящий здесь транспорт (D-157)."""
    body = await _alive(state, db)
    item = await db.get(Item, uuid.UUID(message["item"]))
    if item is None:
        raise Refused("нет такого предмета")
    await transport.harness(db, current(), current_catalog(), body, item)
    return {"harnessed": item.type_key}


async def _transport_unharness(state: dict, db: AsyncSession, message: dict) -> dict:
    """Распрячься. Обоз с грузом остаётся стоять здесь."""
    body = await _alive(state, db)
    вагон = await transport.unharness(db, body)
    return {"unharnessed": None if вагон is None else вагон.type_key}


async def _transport_load(state: dict, db: AsyncSession, message: dict) -> dict:
    """Погрузить из рук в трюм. Присутственно: на ходу не перекладывают."""
    body = await _alive(state, db)
    item = await db.get(Item, uuid.UUID(message["item"]))
    if item is None:
        raise Refused("нет такого предмета")
    сколько = message.get("amount")
    перенесено = await transport.load(
        db, current(), current_catalog(), body, item,
        None if сколько is None else float(сколько),
    )
    return {"loaded": перенесено}


async def _transport_unload(state: dict, db: AsyncSession, message: dict) -> dict:
    """Выгрузить из трюма в руки. Предел рук при этом никуда не девается."""
    body = await _alive(state, db)
    item = await db.get(Item, uuid.UUID(message["item"]))
    if item is None:
        raise Refused("нет такого предмета")
    сколько = message.get("amount")
    перенесено = await transport.unload(
        db, current(), current_catalog(), body, item,
        None if сколько is None else float(сколько),
    )
    return {"unloaded": перенесено}


async def _explore_survey(state: dict, db: AsyncSession, message: dict) -> dict:
    """Уйти в разведку за названным. Находка придёт по сроку, в том числе офлайн.

    Цель выбирает игрок: участок в городе, место под город или жила — и у жилы
    можно назвать породу. Названная ищется хуже: целиться в редкое значит чаще
    возвращаться ни с чем (D-152).
    """
    body = await _alive(state, db)
    job = await explore.survey(
        db, current(), body,
        goal=str(message.get("goal") or explore.SITE),
        resource=message.get("resource") or None,
    )
    return {"survey": str(job.id), "returns_at": job.run_at.isoformat()}


async def _explore_cancel(state: dict, db: AsyncSession, message: dict) -> dict:
    """Повернуть назад: заход отменяется, тело снова свободно в узле выхода.

    Выносливость не возвращается, находка не состоится (D-152). Намеренно не
    `_alive` + `require_here`: разведчик и есть тот, кому присутственное
    закрыто, и вернуться — единственное, что ему доступно.
    """
    body = await _body(db, state["identity_id"])
    if body is None:
        raise Refused("нет живого тела")
    job = await explore.cancel(db, body)
    return {"cancelled": str(job.id)}


async def _explore_goals(state: dict, db: AsyncSession, message: dict) -> dict:
    """Что можно искать и во что обойдётся заход отсюда.

    Список пород — из вольта (D-151). Прогноз — по месту: цена разведки растёт
    с каждой находкой от этого узла, и увидеть её игрок обязан до выхода,
    иначе она читается как случайность движка (D-156).
    """
    #: Список целей — справка, и мёртвому она тоже положена: прогноза у него
    #: просто нет, потому что выходить в поле некому.
    body = await _body(db, state["identity_id"])
    return {
        "goals": list(explore.GOALS),
        "resources": list(explore.mineable(current_catalog())),
        "outlook": None if body is None else await explore.outlook(db, current(), body),
    }


async def _utility_holdings(state: dict, db: AsyncSession, message: dict) -> dict:
    """Своё хозяйство и счета за быт. Удалённое: платят не ногами."""
    return {"holdings": await utility.holdings(db, current(), state["identity_id"])}


async def _utility_pay(state: dict, db: AsyncSession, message: dict) -> dict:
    """Погасить долг узла и включить его обратно."""
    identity = await _identity(state, db)
    node = await _node(db, message["node"])
    paid = await utility.pay(db, current(), identity, node)
    return {"paid": paid, "money": await _money(db, identity.id)}


async def _city_found(state: dict, db: AsyncSession, message: dict) -> dict:
    """Основать город на своём узле планеты (D-023, D-098, D-159).

    Порог входа — четыре постройки, а не монета. Земля при этом уходит городу:
    её дальше раздаёт власть, а не хозяин двора (D-089).
    """
    body = await _alive(state, db)
    город = await town.establish(
        db, current(), current_catalog(), body, str(message.get("name") or "")
    )
    return {"city": str(город.id), "name": город.name}


async def _city_join(state: dict, db: AsyncSession, message: dict) -> dict:
    """Проситься в граждане. Что выйдет — решает устав города (D-160)."""
    body = await _alive(state, db)
    город = await _city(state, db, message)
    итог = await town.join(db, body, город)
    from src.models.city import Citizen

    гражданин = isinstance(итог, Citizen)
    return {
        "citizen": гражданин,
        "city": город.name,
        "waiting": not гражданин,
    }


async def _city_leave(state: dict, db: AsyncSession, message: dict) -> dict:
    """Заявить о выходе. Гражданство спадёт через `city.exit_delay` (D-160)."""
    identity = await _identity(state, db)
    запись = await town.leave(db, current(), identity)
    return {"leaves_at": запись.leaving_at.isoformat()}


async def _city_invite(state: dict, db: AsyncSession, message: dict) -> dict:
    """Позвать человека в граждане. Право `citizens`."""
    identity = await _identity(state, db)
    город = await _city(state, db, message)
    кого = await _identity_by_name(db, str(message["who"]))
    await town.invite(db, identity, город, кого)
    return {"invited": кого.name}


async def _city_admit(state: dict, db: AsyncSession, message: dict) -> dict:
    """Одобрить заявку в граждане. Право `citizens`."""
    identity = await _identity(state, db)
    город = await _city(state, db, message)
    кого = await _identity_by_name(db, str(message["who"]))
    await town.admit(db, identity, город, кого)
    return {"admitted": кого.name}


async def _city_exile(state: dict, db: AsyncSession, message: dict) -> dict:
    """Изгнать из города. Санкция, а не кадровое решение: право `justice`."""
    identity = await _identity(state, db)
    город = await _city(state, db, message)
    кого = await _identity_by_name(db, str(message["who"]))
    await town.exile(db, identity, город, кого)
    return {"exiled": кого.name}


async def _city_citizens(state: dict, db: AsyncSession, message: dict) -> dict:
    """Жители города и очередь заявок. Удалённое: это справка, а не решение."""
    город = await _city(state, db, message)
    жители = []
    for запись in await town.citizens_of(db, город):
        кто = await db.get(Identity, запись.identity_id)
        жители.append(
            {
                "name": None if кто is None else кто.name,
                "since": запись.since.isoformat(),
                "leaving_at": (
                    None if запись.leaving_at is None else запись.leaving_at.isoformat()
                ),
            }
        )
    заявки = []
    for заявка in await town.requests_of(db, город):
        кто = await db.get(Identity, заявка.identity_id)
        заявки.append({"name": None if кто is None else кто.name, "kind": заявка.kind})
    return {
        "admission": town.admission(город),
        "citizens": sorted(жители, key=lambda ж: ж["name"] or ""),
        "requests": заявки,
    }


async def _city_votes(state: dict, db: AsyncSession, message: dict) -> dict:
    """Идущие голосования города. Удалённое: смотреть можно откуда угодно."""
    город = await _city(state, db, message)
    return {
        "votes": await vote.view(db, current_catalog(), город, state["identity_id"])
    }


async def _city_vote(state: dict, db: AsyncSession, message: dict) -> dict:
    """Проголосовать. Голос — участие, а не управление: подаётся по Сети (D-161)."""
    identity = await _identity(state, db)
    город = await _city(state, db, message)
    голосование = await db.get(Vote, uuid.UUID(message["vote"]))
    if голосование is None or голосование.city_id != город.id:
        raise Refused("нет такого голосования в этом городе")
    await vote.cast(db, город, identity, голосование, bool(message.get("yes")))
    за, против = await vote.standing(db, голосование)
    return {"yes": за, "no": против}


async def _city_election(state: dict, db: AsyncSession, message: dict) -> dict:
    """Созвать выборы правителя (D-162). Кандидаты выдвигаются по ходу."""
    identity = await _identity(state, db)
    город = await _city(state, db, message)
    голосование = await vote.open_election(db, current(), город, identity)
    return {"vote": str(голосование.id), "closes_at": голосование.closes_at.isoformat()}


async def _city_recall(state: dict, db: AsyncSession, message: dict) -> dict:
    """Созвать отзыв правителя. Прошло — должность снимается, идут выборы."""
    identity = await _identity(state, db)
    город = await _city(state, db, message)
    голосование = await vote.open_recall(db, current(), город, identity)
    return {"vote": str(голосование.id), "closes_at": голосование.closes_at.isoformat()}


async def _city_nominate(state: dict, db: AsyncSession, message: dict) -> dict:
    """Выдвинуться в правители. Сам, а не по чьему-то представлению."""
    identity = await _identity(state, db)
    город = await _city(state, db, message)
    голосование = await db.get(Vote, uuid.UUID(message["vote"]))
    if голосование is None or голосование.city_id != город.id:
        raise Refused("нет такого голосования в этом городе")
    await vote.nominate(db, город, identity, голосование)
    return {"nominated": identity.name}


async def _city_choose(state: dict, db: AsyncSession, message: dict) -> dict:
    """Отдать голос кандидату на выборах."""
    identity = await _identity(state, db)
    город = await _city(state, db, message)
    голосование = await db.get(Vote, uuid.UUID(message["vote"]))
    if голосование is None or голосование.city_id != город.id:
        raise Refused("нет такого голосования в этом городе")
    кандидат = await db.get(Identity, uuid.UUID(message["candidate"]))
    if кандидат is None:
        raise Refused("нет такой личности")
    await vote.choose(db, город, identity, голосование, кандидат)
    return {"chosen": кандидат.name}


async def _city_council(state: dict, db: AsyncSession, message: dict) -> dict:
    """Состав совета и как он собирается (D-164). Удалённое: это справка."""
    город = await _city(state, db, message)
    места = []
    for место in await vote.council_of(db, город):
        кто = await db.get(Identity, место.identity_id)
        места.append({"name": None if кто is None else кто.name, "how": место.how})
    return {
        "mode": vote.council_mode(город),
        "seats": vote.council_seats(город),
        "members": места,
    }


async def _city_council_seat(state: dict, db: AsyncSession, message: dict) -> dict:
    """Назначить в совет либо освободить место. Только там, где места назначают."""
    identity = await _identity(state, db)
    город = await _city(state, db, message)
    кого = await _identity_by_name(db, str(message["who"]))
    if message.get("out"):
        await town.require(db, identity.id, город, Power.OFFICES)
        снято = await vote.vacate(db, город, кого)
        return {"vacated": снято}
    await vote.appoint_to_council(db, город, identity, кого)
    return {"seated": кого.name}


async def _city_council_election(state: dict, db: AsyncSession, message: dict) -> dict:
    """Созвать выборы в совет: побеждают столько, сколько мест."""
    identity = await _identity(state, db)
    город = await _city(state, db, message)
    голосование = await vote.open_council_election(db, current(), город, identity)
    return {"vote": str(голосование.id), "closes_at": голосование.closes_at.isoformat()}


async def _city_sue(state: dict, db: AsyncSession, message: dict) -> dict:
    """Подать жалобу в суд города. Пошлина уходит в казну сразу (D-117)."""
    identity = await _identity(state, db)
    город = await _city(state, db, message)
    ответчик = await _identity_by_name(db, str(message["who"]))
    дело = await justice.sue(
        db, current(), город, identity, ответчик, str(message.get("claim") or "")
    )
    return {"case": str(дело.id)}


async def _city_judge(state: dict, db: AsyncSession, message: dict) -> dict:
    """Вынести приговор. Без санкции — оправдание: висящих дел не бывает."""
    identity = await _identity(state, db)
    дело = await db.get(Case, uuid.UUID(message["case"]))
    if дело is None:
        raise Refused("нет такого дела")
    санкция = message.get("sanction") or None
    наказание = await justice.judge(
        db, current(), current_catalog(), identity, дело,
        sanction=None if санкция is None else str(санкция),
        days=None if message.get("days") is None else float(message["days"]),
        amount=None if message.get("amount") is None else float(message["amount"]),
        verdict=str(message.get("verdict") or ""),
        #: Куда сажать при нескольких каторгах — называет суд (D-176).
        prison_node=None if message.get("prison") is None else str(message["prison"]),
    )
    return {"judged": дело.state.value, "sanction": None if наказание is None else наказание.kind}


async def _city_cases(state: dict, db: AsyncSession, message: dict) -> dict:
    """Дела города и примитивы санкций из вольта. Удалённое: это справка."""
    город = await _city(state, db, message)
    return {
        "cases": await justice.view(db, город),
        "sanctions": [
            {
                "id": примитив.id,
                "name": примитив.name,
                "enforced": примитив.id in justice.ENFORCED,
            }
            for примитив in current_catalog().laws.sanctions
        ],
        #: Каторги города (D-176): при нескольких суд называет, в какую
        #: отправить, — клиенту нужен список.
        "prisons": [
            {"key": узел.key, "name": узел.name}
            for узел in await justice.prisons_of(db, город)
        ],
    }


async def _bank_view(state: dict, db: AsyncSession, message: dict) -> dict:
    """Банк глазами игрока: ставка с объяснением, свои займы, резерв (D-167)."""
    from src.models.bank import RateDecision

    constants = current()
    решение = (
        await db.execute(
            select(RateDecision).order_by(RateDecision.decided_at.desc()).limit(1)
        )
    ).scalars().first()
    займы = []
    for заём in await bank.loans_of(db, state["identity_id"]):
        await bank.accrue(db, constants, заём)
        займы.append(
            {
                "id": str(заём.id),
                "principal": заём.principal,
                "outstanding": заём.outstanding,
                "rate": float(заём.rate),
                "taken_at": заём.taken_at.isoformat(),
            }
        )
    return {
        "rate": await bank.key_rate(db, constants),
        "why": None if решение is None else решение.why,
        #: Резерв и оборот публичны: денежная политика не бывает тайной (D-030).
        "reserve": await bank.reserve(db),
        "circulating": await bank.circulating(db),
        #: Лимит — публичная формула из труда (D-173): игрок видит и число,
        #: и то, из чего оно сложилось, до похода за кредитом.
        "limit": (пределы := await bank.credit_limit(db, constants, state["identity_id"]))[0],
        "limit_why": пределы[1],
        "loans": займы,
    }


async def _bank_borrow(state: dict, db: AsyncSession, message: dict) -> dict:
    """Взять кредит. Деньги идут из резерва; недостающее печатается (D-087)."""
    identity = await _identity(state, db)
    заём = await bank.borrow(
        db, current(), current_catalog(), identity, float(message["amount"])
    )
    return {"loan": str(заём.id), "rate": float(заём.rate)}


async def _bank_repay(state: dict, db: AsyncSession, message: dict) -> dict:
    """Погасить долг. Деньги уходят в резерв, а не в оборот."""
    from src.models.bank import Loan

    identity = await _identity(state, db)
    заём = await db.get(Loan, uuid.UUID(message["loan"]))
    if заём is None or заём.identity_id != identity.id:
        raise Refused("нет такого займа")
    заплачено = await bank.repay(
        db, current(), identity, заём,
        None if message.get("amount") is None else float(message["amount"]),
    )
    return {"paid": заплачено, "left": заём.outstanding}


def _now():
    from datetime import UTC, datetime

    return datetime.now(UTC)


async def _bank_council(state: dict, db: AsyncSession, message: dict) -> dict:
    """Кто решает ставку сейчас и в каком коридоре (D-172). Удалённое: справка."""
    constants = current()
    from src.constants import registry as R

    рекомендация, почему = bank.compute_rate(
        constants,
        previous=await bank.key_rate(db, constants),
        inflation=await bank._inflation(db, constants),
        emission_share=await bank._emission_share(
            db, constants, now=_now()
        ),
    )
    до = await bank.locked_until(db)
    return {
        "council_decides": await bank.council_decides(db, constants),
        "cities_with_hall": await bank.cities_with_hall(db),
        "handover_at": constants[R.BANK_COUNCIL_HANDOVER_CITIES],
        "advised": рекомендация,
        "why": почему,
        "corridor": constants[R.BANK_COUNCIL_RATE_DEVIATION],
        "locked_until": None if до is None else до.isoformat(),
    }


async def _bank_council_rate(state: dict, db: AsyncSession, message: dict) -> dict:
    """Голос города по ставке. Подаёт держатель права `laws` (D-172)."""
    identity = await _identity(state, db)
    город = await _city(state, db, message)
    решение = await bank.council_set_rate(
        db, current(), город, identity, float(message["rate"])
    )
    return {"rate": float(решение.rate), "why": решение.why}


async def _person_report(state: dict, db: AsyncSession, message: dict) -> dict:
    """Указать на дефектную печать (D-173). Снижает доверие, а не убивает."""
    identity = await _identity(state, db)
    кого = await _identity_by_name(db, str(message["who"]))
    await bank.report_defect(db, identity, кого)
    return {"reported": кого.name}


async def _person_unreport(state: dict, db: AsyncSession, message: dict) -> dict:
    """Отозвать свой репорт: ошибиться можно, исправиться — нужно."""
    identity = await _identity(state, db)
    кого = await _identity_by_name(db, str(message["who"]))
    return {"withdrawn": await bank.withdraw_report(db, identity, кого)}


async def _city_bail(state: dict, db: AsyncSession, message: dict) -> dict:
    """Город гасит долг гражданина из казны (D-175): освобождает свою линию.

    Присутственно и по праву казны: трата — решение власти (D-155).
    """
    from src.models.bank import Loan

    identity = await _identity(state, db)
    body = await _alive(state, db)
    город = await _city(state, db, message)
    await town.require_at_hall(db, body, город)
    await town.require(db, identity.id, город, Power.TREASURY)
    заём = await db.get(Loan, uuid.UUID(message["loan"]))
    if заём is None:
        raise Refused("нет такого займа")
    казна = await town.treasury(db, город)
    заплачено = await bank.repay(
        db, current(), identity, заём,
        None if message.get("amount") is None else float(message["amount"]),
        from_account=казна,
    )
    return {"paid": заплачено, "left": заём.outstanding}


async def _city_survey(state: dict, db: AsyncSession, message: dict) -> dict:
    """Сводка города: устав, законы, должности, казна и свои полномочия.

    Удалённое чтение: цифры города не привязаны к месту. Смотреть можно любой
    город — свой по телу либо названный ключом узла.
    """
    город = await _city(state, db, message)
    сводка = await town.survey(db, current(), current_catalog(), город)
    сводка["powers"] = sorted(await town.powers_of(db, state["identity_id"], город))
    #: Здесь ли принимаются решения: управление присутственно (D-155), и
    #: клиенту нужно знать, показывать кнопки или отправлять в ратушу.
    body = await _body(db, state["identity_id"])
    сводка["at_hall"] = False
    if body is not None:
        узел = await db.get(Node, body.node_id)
        сводка["at_hall"] = узел is not None and узел.owner_city_id == город.id and (
            town.HALL in await _stations(db, узел)
        )
    #: Свободные и розданные участки: раздача земли — первое, ради чего в
    #: администрацию заходят (D-089).
    участки = (
        await db.execute(select(Node).where(Node.owner_city_id == город.id))
    ).scalars().all()
    сводка["lots"] = [
        {
            "key": узел.key,
            "name": узел.name,
            "area": float(узел.area_m2),
            "owner": None if узел.owner_identity_id is None else str(узел.owner_identity_id),
            "free": узел.owner_identity_id is None and bool(узел.properties.get("участок")),
        }
        for узел in участки
        if узел.properties.get("участок")
    ]
    сводка["citizens"] = await _citizens(db)
    return {"city": сводка}


async def _city_panel(state: dict, db: AsyncSession, message: dict) -> dict:
    """Экономическая панель города. Удалённое чтение (D-140).

    Публичный срез виден всем, включая гостей: цены и обороты — общее знание
    (D-047). Полный набор с казной по основаниям — тем, у кого есть право
    `dashboard`. Персонального нет ни в одном из срезов.
    """
    город = await _city(state, db, message)
    полный = await town.may(db, state["identity_id"], город, Power.DASHBOARD)
    сводка = await panel.collect(db, current(), город, full=полный)
    сводка["full"] = полный
    return {"panel": сводка}


async def _city_law(state: dict, db: AsyncSession, message: dict) -> dict:
    """Записать код-закон. Присутственно и по точечному праву (D-154, D-155)."""
    identity = await _identity(state, db)
    город = await _city(state, db, message)
    await town.set_law(
        db, current(), current_catalog(), identity, город,
        str(message["law"]), str(message["value"]),
        body=await _body(db, identity.id),
    )
    return {"law": message["law"], "value": message["value"]}


async def _city_charter(state: dict, db: AsyncSession, message: dict) -> dict:
    """Ответить на вопрос устава."""
    identity = await _identity(state, db)
    город = await _city(state, db, message)
    параметр = message.get("param")
    await town.set_charter(
        db, current_catalog(), identity, город,
        str(message["question"]), str(message["option"]),
        None if параметр is None else float(параметр),
        body=await _body(db, identity.id),
    )
    return {"question": message["question"], "option": message["option"]}


async def _city_appoint(state: dict, db: AsyncSession, message: dict) -> dict:
    """Назначить должность. Отдать можно только то, что есть у себя."""
    identity = await _identity(state, db)
    город = await _city(state, db, message)
    кому = await _identity_by_name(db, str(message["whom"]))
    #: Право — строка: крупное (`treasury`) либо точечное (`law:import_duty`).
    #: Проверять его список здесь незачем: движок сверяет права с тем, что есть
    #: у назначающего, а несуществующее право просто ничего не открывает.
    полномочия = tuple(str(raw) for raw in message.get("powers") or ())
    office = await town.appoint(
        db, identity, город, кому,
        title=str(message.get("title") or "Должность"),
        powers=полномочия,
        body=await _body(db, identity.id),
    )
    return {"office": str(office.id), "whom": кому.name}


async def _city_revoke(state: dict, db: AsyncSession, message: dict) -> dict:
    identity = await _identity(state, db)
    город = await _city(state, db, message)
    office = await db.get(Office, uuid.UUID(message["office"]))
    if office is None:
        raise Refused("нет такой должности")
    await town.revoke(db, identity, город, office, body=await _body(db, identity.id))
    return {"revoked": str(office.id)}


async def _city_spend(state: dict, db: AsyncSession, message: dict) -> dict:
    """Заплатить из казны. Жалованье, награда и подряд — это одна проводка."""
    identity = await _identity(state, db)
    город = await _city(state, db, message)
    кому = await _identity_by_name(db, str(message["whom"]))
    сумма = int(message["amount"])
    await town.spend(
        db, identity, город, кому, сумма,
        memo=str(message.get("memo") or ""),
        body=await _body(db, identity.id),
    )
    return {"spent": сумма, "whom": кому.name}


async def _city_allot(state: dict, db: AsyncSession, message: dict) -> dict:
    """Выделить городской участок жителю: свой дом начинается отсюда (D-089)."""
    identity = await _identity(state, db)
    город = await _city(state, db, message)
    участок = await _node(db, str(message["node"]))
    кому = await _identity_by_name(db, str(message["whom"]))
    await town.allot(
        db, identity, город, участок, кому, body=await _body(db, identity.id)
    )
    return {"allotted": участок.key, "whom": кому.name}


async def _city(state: dict, db: AsyncSession, message: dict):
    """Город, о котором речь: названный ключом узла либо тот, где стоит тело."""
    if message.get("city"):
        узел = await _node(db, str(message["city"]))
    else:
        body = await _body(db, state["identity_id"])
        if body is None:
            raise Refused("нет живого тела: назовите город явно")
        узел = await db.get(Node, body.node_id)
    город = await town.of_node(db, узел)
    if город is None:
        raise Refused("здесь нет города: за стенами законов нет")
    return город


async def _identity_by_name(db: AsyncSession, name: str) -> Identity:
    found = (
        await db.execute(select(Identity).where(Identity.name == name))
    ).scalar_one_or_none()
    if found is None:
        raise Refused(f"нет личности {name!r}")
    return found


async def _citizens(db: AsyncSession) -> list[str]:
    """Кого вообще можно назначить и кому заплатить. Имена публичны (D-058)."""
    rows = await db.execute(select(Identity.name).order_by(Identity.name))
    return [row[0] for row in rows]


_COMMANDS = {
    "look": _look,
    "pow.challenge": _challenge,
    "mine.start": _mine_start,
    "mine.swing": _mine_swing,
    "mine.timber": _mine_timber,
    "mine.pace": _mine_pace,
    "mine.leave": _mine_leave,
    "craft.plan": _craft_plan,
    "craft.start": _craft_start,
    "craft.repair": _craft_repair,
    "craft.recycle": _craft_recycle,
    "library.copy": _library_copy,
    "travel.go": _travel_go,
    "rest.sleep": _rest_sleep,
    "rest.wake": _rest_wake,
    "food.eat": _food_eat,
    "cook.pot": _cook_pot,
    "coin.mint": _coin_mint,
    "coin.melt": _coin_melt,
    "energy.grid": _energy_grid,
    "energy.charge": _energy_charge,
    "gear.equip": _gear_equip,
    "gear.unequip": _gear_unequip,
    "world.metrics": _world_metrics,
    "market.offers": _market_offers,
    "market.reserve": _market_reserve,
    "market.redeem": _market_redeem,
    "rig.place": _rig_place,
    "rig.status": _rig_status,
    "rig.empty": _rig_empty,
    "land.claim": _land_claim,
    "land.buy": _land_buy,
    "build.construct": _build_construct,
    "deed.offer": _deed_offer,
    "deed.buy": _deed_buy,
    "deed.market": _deed_market,
    "farm.mark": _farm_mark,
    "farm.plow": _farm_plow,
    "farm.sow": _farm_sow,
    "farm.care": _farm_care,
    "farm.harvest": _farm_harvest,
    "farm.split": _farm_split,
    "breed.cross": _breed_cross,
    "breed.gather": _breed_gather,
    "breed.name": _breed_name,
    "breed.varieties": _breed_varieties,
    "breed.agrotech": _breed_agrotech,
    "farm.survey": _farm_survey,
    "chat.say": _chat_say,
    "chat.hear": _chat_hear,
    "chat.gather": _chat_gather,
    "chat.join": _chat_join,
    "chat.leave": _chat_leave,
    "market.load": _market_load,
    "market.take": _market_take,
    "market.sell": _market_sell,
    "market.buy": _market_buy,
    "market.cancel": _market_cancel,
    "body.printers": _body_printers,
    "body.print": _body_print,
    "station.place": _station_place,
    "station.take": _station_take,
    "road.lay": _road_lay,
    "road.here": _road_here,
    "transport.harness": _transport_harness,
    "transport.unharness": _transport_unharness,
    "transport.load": _transport_load,
    "transport.unload": _transport_unload,
    "explore.survey": _explore_survey,
    "explore.cancel": _explore_cancel,
    "explore.goals": _explore_goals,
    "utility.holdings": _utility_holdings,
    "utility.pay": _utility_pay,
    "city.found": _city_found,
    "city.join": _city_join,
    "city.leave": _city_leave,
    "city.invite": _city_invite,
    "city.admit": _city_admit,
    "city.exile": _city_exile,
    "city.citizens": _city_citizens,
    "city.votes": _city_votes,
    "city.vote": _city_vote,
    "city.election": _city_election,
    "city.recall": _city_recall,
    "city.nominate": _city_nominate,
    "city.choose": _city_choose,
    "city.council": _city_council,
    "city.council_seat": _city_council_seat,
    "city.council_election": _city_council_election,
    "city.sue": _city_sue,
    "city.judge": _city_judge,
    "city.cases": _city_cases,
    "bank.view": _bank_view,
    "bank.borrow": _bank_borrow,
    "bank.repay": _bank_repay,
    "bank.council": _bank_council,
    "bank.council_rate": _bank_council_rate,
    "person.report": _person_report,
    "person.unreport": _person_unreport,
    "city.bail": _city_bail,
    "city.survey": _city_survey,
    "city.panel": _city_panel,
    "city.law": _city_law,
    "city.charter": _city_charter,
    "city.appoint": _city_appoint,
    "city.revoke": _city_revoke,
    "city.spend": _city_spend,
    "city.allot": _city_allot,
}


def _fill(fill: market.Fill) -> dict[str, Any]:
    """Что вышло из заявки: сам ордер и сделки, если они случились."""
    return {
        "order": str(fill.order.id),
        "state": fill.order.state.value,
        "left": amount_float(fill.order.amount_left),
        "traded": fill.traded,
        "trades": [
            {"price": trade.price, "amount": amount_float(trade.amount)}
            for trade in fill.trades
        ],
    }


def _craft_request(message: dict) -> tuple[str, float, dict[str, Any]]:
    """Разбор заявки на партию — одинаковый у прогноза и у запуска.

    Иначе прогноз считался бы по одной заявке, а партия шла бы по другой.
    """
    return (
        message["output"],
        float(message.get("units", 1)),
        {
            "tool_item_id": _optional_uuid(message.get("tool")),
            "proportions": message.get("proportions"),
            #: «Поставить на автомат» — решение мастера: объём вместо качества,
            #: и счёт за энергию (D-035, D-058).
            "auto": bool(message.get("auto", False)),
        },
    )


def _sight(session: MiningSession, sight: mining.Sight) -> dict[str, Any]:
    """Наружу уходит только то, что видит игрок.

    Собирается из `Sight`, а не из модели сессии, — чтобы скрытое число
    физически не могло попасть в ответ по недосмотру.
    """
    payload = asdict(sight)
    payload["pace"] = sight.pace.value
    payload["state"] = sight.state.value
    payload["session"] = str(session.id)
    return payload


async def _body(db: AsyncSession, identity_id: uuid.UUID) -> Body | None:
    stmt = select(Body).where(Body.identity_id == identity_id, Body.state == BodyState.ALIVE)
    return (await db.execute(stmt)).scalars().first()


async def _alive(state: dict, db: AsyncSession) -> Body:
    """Тело, которым действуют. Материя требует присутствия (D-044)."""
    body = await _body(db, state["identity_id"])
    if body is None:
        raise Refused("нет живого тела")
    return body


async def _bench(
    db: AsyncSession, node: Node, body: Body, *, furniture: bool = False
) -> list[dict[str, Any]]:
    """Станки узла поимённо: качество, состояние и кем занят (D-150).

    Отдельно от `stations`: тот список отвечает на вопрос «какие сцены
    показывать», а этот — «за какой станок можно встать прямо сейчас».
    С `furniture=True` — то же самое про мебель: кровать и стеллаж не станки,
    и клиент показывает их отдельным окном.
    """
    from src.constants.catalog import ItemKind

    ожидаемый = ItemKind.FURNITURE if furniture else ItemKind.STATION
    книга = current_catalog().recipes
    where = await world.node_container(db, node)
    items = (
        await db.execute(select(Item).where(Item.container_id == where.id))
    ).scalars().all()

    out: list[dict[str, Any]] = []
    for item in items:
        try:
            рецепт = книга.recipe(item.type_key)
        except Exception:  # noqa: BLE001 — сырьё у станка рецептом не описано
            continue
        if рецепт.kind is not ожидаемый:
            continue
        out.append(
            {
                "id": str(item.id),
                "goods": item.type_key,
                "quality": None if item.quality is None else float(item.quality),
                "condition": float(item.condition),
                "busy": item.busy_body_id is not None,
                "mine": item.busy_body_id == body.id,
            }
        )
    return sorted(out, key=lambda станок: станок["goods"])


async def _vehicles(db: AsyncSession, constants, node: Node) -> list[dict[str, Any]]:
    """Транспорт, стоящий в этом узле (D-157).

    Отдельно от станков: в повозку не встают работать, в неё впрягаются. Занят
    ли транспорт чужой упряжкой, видно сразу — иначе игрок узнавал бы об этом
    только отказом.
    """
    from src.models.travel import Harness

    каталог = current_catalog()
    где = await world.node_container(db, node)
    вещи = (
        await db.execute(select(Item).where(Item.container_id == где.id))
    ).scalars().all()
    впряжены = set((await db.execute(select(Harness.item_id))).scalars().all())
    out: list[dict[str, Any]] = []
    for item in вещи:
        if not transport.is_vehicle(каталог, item.type_key):
            continue
        try:
            грузоподъёмность = transport.capacity(constants, item.type_key)
        except transport.NotVehicle:
            #: Вольт не назвал его грузоподъёмности — показываем как есть,
            #: а откажет упряжка: врать числом хуже, чем не показать его.
            грузоподъёмность = None
        out.append(
            {
                "id": str(item.id),
                "goods": item.type_key,
                "condition": float(item.condition),
                "capacity": грузоподъёмность,
                "speed_k": transport.speed(constants, item.type_key),
                "taken": item.id in впряжены,
            }
        )
    return sorted(out, key=lambda телега: телега["goods"])


async def _stations(db: AsyncSession, node: Node) -> list[str]:
    """Станки и терминал, стоящие в узле. Сцена узла собирается из них."""
    where = await world.node_container(db, node)
    rows = await db.execute(
        select(Item.type_key).where(Item.container_id == where.id).distinct()
    )
    return sorted(row[0] for row in rows)


async def _money(db: AsyncSession, identity_id: uuid.UUID) -> str:
    """Счёт личности. Баланс — сумма проводок, поля «деньги» не существует."""
    account = await ledger.account_for(db, AccountKind.IDENTITY, identity_id)
    return money_str(await ledger.balance(db, account.id))


async def _things(db: AsyncSession, constants, container) -> list[dict[str, Any]]:
    """Содержимое контейнера так, как его видит хозяин: с числом и со ступенью."""
    items = (
        await db.execute(select(Item).where(Item.container_id == container.id))
    ).scalars().all()
    catalog = current_catalog()
    #: Клеймо показывается именем: чья это работа, игрок обязан видеть (D-058).
    клейма = await _makers(db, items)
    сорта = await _varieties(db, items)
    return [
        {
            "id": str(item.id),
            "goods": item.type_key,
            "amount": amount_float(item.amount),
            "quality": None if item.quality is None else float(item.quality),
            "tier": market.tier_of(constants, None if item.quality is None
                                   else float(item.quality)),
            "condition": float(item.condition),
            "flavor": item.flavor,
            "food": _edible(catalog, item.type_key),
            "ingredient": catalog.recipes.is_ingredient(item.type_key),
            "spoils_at": None if item.spoils_at is None else item.spoils_at.isoformat(),
            #: Проба монеты видна всем: пробирного инструмента в данных вольта
            #: нет, а прятать пробу без способа её узнать нельзя (OQ 01-currency).
            "fineness": None if item.fineness is None else float(item.fineness),
            "maker": клейма.get(item.maker_identity_id),
            #: Вес и слот — из данных вольта (D-146). Незнакомому каталогу
            #: предмету масса не приписывается: дыра должна быть видна.
            "mass": catalog.recipes.mass_of(item.type_key),
            "slot": catalog.recipes.slot_of(item.type_key),
            #: У семян: чей сорт и сколько силы осталось в партии (D-057).
            "variety": сорта.get(item.variety_id),
            "vigor": None if item.vigor is None else float(item.vigor),
            #: У аккумулятора: заряд с учётом саморазряда — то, что в нём
            #: действительно есть сейчас, а не то, что залили вчера (D-071).
            "charge": (
                None if item.charge is None
                else round(energy.charge_of(constants, item), 1)
            ),
        }
        for item in items
    ]


async def _varieties(db: AsyncSession, items) -> dict[uuid.UUID, str]:
    """Имена сортов по семенам. У безымянного гибрида — честное «гибрид»."""
    ids = {item.variety_id for item in items if item.variety_id is not None}
    if not ids:
        return {}
    rows = await db.execute(select(Variety).where(Variety.id.in_(ids)))
    return {
        сорт.id: сорт.name or f"гибрид, поколение {сорт.generation}"
        for сорт in rows.scalars().all()
    }


async def _makers(db: AsyncSession, items) -> dict[uuid.UUID, str]:
    """Имена мастеров по клеймам предметов, одним запросом."""
    ids = {item.maker_identity_id for item in items if item.maker_identity_id is not None}
    if not ids:
        return {}
    rows = await db.execute(
        select(Identity.id, Identity.name).where(Identity.id.in_(ids))
    )
    return {row[0]: row[1] for row in rows}


def _edible(catalog, type_key: str) -> bool:
    try:
        return catalog.recipes.recipe(type_key).food
    except Exception:  # noqa: BLE001 — сырьё рецептом не описано
        return False


async def _knowledge(
    db: AsyncSession,
    identity_id: uuid.UUID,
    *,
    kind: KnowledgeKind = KnowledgeKind.RECIPE,
) -> list[str]:
    rows = await db.execute(
        select(Knowledge.key).where(
            Knowledge.identity_id == identity_id, Knowledge.kind == kind
        )
    )
    return sorted(row[0] for row in rows)


async def _orders(db: AsyncSession, identity_id: uuid.UUID) -> list[dict[str, Any]]:
    rows = (
        await db.execute(
            select(Order).where(
                Order.identity_id == identity_id, Order.state == OrderState.ACTIVE
            )
        )
    ).scalars().all()
    return [
        {
            "id": str(order.id),
            "side": order.side.value,
            "goods": order.type_key,
            "tier": order.tier,
            "price": order.price,
            "left": amount_float(order.amount_left),
        }
        for order in rows
    ]


async def _reservations(db: AsyncSession, identity_id: uuid.UUID) -> list[dict[str, Any]]:
    """Свои брони: где, что, до какого срока и сколько внесено задатком."""
    rows = (
        await db.execute(
            select(Reservation, Node.name, Node.key)
            .join(Node, Node.id == Reservation.node_id)
            .where(
                Reservation.buyer_identity_id == identity_id,
                Reservation.state == ReservationState.HELD,
            )
        )
    ).all()
    return [
        {
            "id": str(бронь.id),
            "goods": бронь.type_key,
            "tier": бронь.tier,
            "amount": amount_float(бронь.amount),
            "price": бронь.price,
            "deposit": бронь.deposit,
            "node": имя,
            "node_key": ключ,
            "expires_at": бронь.expires_at.isoformat(),
        }
        for бронь, имя, ключ in rows
    ]


async def _batches(db: AsyncSession, identity_id: uuid.UUID) -> list[dict[str, Any]]:
    """Дела: длительные работы, которые идут сами, в том числе пока игрок офлайн."""
    rows = (
        await db.execute(
            select(CraftBatch)
            .join(Body, Body.id == CraftBatch.body_id)
            .where(Body.identity_id == identity_id, CraftBatch.state == BatchState.RUNNING)
        )
    ).scalars().all()
    return [
        {
            "id": str(batch.id),
            "work": batch.kind.value,
            "output": batch.output,
            "units": amount_float(batch.units),
            "quality": float(batch.quality),
            "ready_at": batch.ready_at.isoformat(),
        }
        for batch in rows
    ]


async def _own_item(db: AsyncSession, body: Body, item_id: str) -> Item:
    """Вещь в руках. Чинят и разбирают своё, а не то, что лежит рядом."""
    from src.engine.world import body_container

    item = await db.get(Item, uuid.UUID(item_id))
    inventory = await body_container(db, body)
    if item is None or item.container_id != inventory.id:
        raise Refused("этой вещи у вас нет")
    return item


async def _identity(state: dict, db: AsyncSession) -> Identity:
    """Личность. Ею распоряжаются удалённо — и когда тело мертво, тоже."""
    identity = await db.get(Identity, state["identity_id"])
    if identity is None:  # pragma: no cover
        raise Refused("личность исчезла")
    return identity


async def _node(db: AsyncSession, key: str) -> Node:
    """Узел по устойчивому ключу: ордерами распоряжаются откуда угодно."""
    node = (await db.execute(select(Node).where(Node.key == key))).scalar_one_or_none()
    if node is None:
        raise Refused(f"нет узла {key!r}")
    return node


async def _active(state: dict, db: AsyncSession) -> MiningSession:
    session_id = state.get("session_id")
    if session_id is None:
        #: Клиент мог переподключиться — ищем открытую сессию тела.
        body = await _body(db, state["identity_id"])
        if body is None:
            raise Refused("нет живого тела")
        found = (
            await db.execute(
                select(MiningSession).where(
                    MiningSession.body_id == body.id,
                    MiningSession.state == SessionState.ACTIVE,
                )
            )
        ).scalars().first()
        if found is None:
            raise Refused("сессия не открыта")
        state["session_id"] = found.id
        return found

    session = await db.get(MiningSession, session_id)
    if session is None:  # pragma: no cover
        raise Refused("сессия исчезла")
    return session


def _optional_uuid(value: str | None) -> uuid.UUID | None:
    return None if value is None else uuid.UUID(value)
