"""Журнал событий — первичное хранилище.

Требование дизайна: суд, метрики и расследования опираются на полное событийное
логирование (01-tech-notes). Состояние мира — производное: любое изменение
сначала становится событием, и только потом отражается в таблицах состояния.

Восстановить это задним числом невозможно, поэтому пишется с первого дня всё —
даже то, что пока никто не читает.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import BigInteger, DateTime, Index, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base


class EventKind(StrEnum):
    """Виды событий. Список растёт вместе с реестром действий (06-actions)."""

    # мир и служебное
    WORLD_BOOTSTRAPPED = "world.bootstrapped"
    CONSTANTS_CHANGED = "constants.changed"
    TICK_RAN = "tick.ran"

    # личность и тело
    IDENTITY_CREATED = "identity.created"
    BODY_PRINTED = "body.printed"
    BODY_DIED = "body.died"
    #: Печать заказана и оплачена; тело приходит по сроку (D-028, D-033).
    BODY_PRINT_ORDERED = "body.print_ordered"
    BODY_SLEPT = "body.slept"
    MEAL_EATEN = "meal.eaten"
    BODY_WOKE = "body.woke"
    KNOWLEDGE_LEARNED = "knowledge.learned"

    # имущество
    ITEM_CREATED = "item.created"
    ITEM_MOVED = "item.moved"
    ITEM_CONSUMED = "item.consumed"
    ITEM_WORN = "item.worn"

    # снаряжение и носимое (D-146)
    GEAR_EQUIPPED = "gear.equipped"
    GEAR_UNEQUIPPED = "gear.unequipped"

    # добыча (D-143)
    MINING_STARTED = "mining.started"
    MINING_SWING = "mining.swing"
    MINING_TIMBERED = "mining.timbered"
    MINING_LEFT = "mining.left"
    MINING_COLLAPSED = "mining.collapsed"

    # перемещение (D-107)
    TRAVEL_STARTED = "travel.started"
    TRAVEL_ARRIVED = "travel.arrived"

    # дороги как работа на ребре (D-107, D-158)
    ROAD_WORK_STARTED = "road.work_started"
    ROAD_LAID = "road.laid"
    #: Заросло: покрытие опустилось на ступень без содержания.
    ROAD_DECAYED = "road.decayed"

    # транспорт и обоз (D-157)
    TRANSPORT_HARNESSED = "transport.harnessed"
    TRANSPORT_UNHARNESSED = "transport.unharnessed"
    TRANSPORT_LOADED = "transport.loaded"
    TRANSPORT_UNLOADED = "transport.unloaded"
    #: Обоз встал: транспорт кончился износом, груз остался лежать в узле.
    TRANSPORT_BROKE = "transport.broke"

    # крафт (D-092, D-133)
    CRAFT_STARTED = "craft.started"
    CRAFT_FINISHED = "craft.finished"

    # земля и земледелие (D-118)
    LAND_CLAIMED = "land.claimed"
    #: Выкуп городской земли: цена от удалённости, выручка в казну (D-089).
    LAND_BOUGHT = "land.bought"
    #: Ценная бумага на участок: выдана, выставлена, продана (D-116).
    DEED_ISSUED = "deed.issued"
    DEED_OFFERED = "deed.offered"
    DEED_SOLD = "deed.sold"
    #: Бумага погашена: земля ушла городу при основании (D-159). Городская
    #: земля бумагой не торгуется — её раздаёт власть.
    DEED_RETIRED = "deed.retired"
    #: Здание построено на участке (D-106, D-125).
    BUILDING_BUILT = "building.built"
    PLOT_MARKED = "farm.marked"
    PLOT_PLOWED = "farm.plowed"
    PLOT_SOWN = "farm.sown"
    PLOT_CARED = "farm.cared"
    PLOT_HARVESTED = "farm.harvested"

    # энергия (D-071, D-082, D-085)
    ENERGY_PRODUCED = "energy.produced"
    ENERGY_CHARGED = "energy.charged"
    ENERGY_DRAWN = "energy.drawn"
    # счётчик и содержание узла (D-135, D-149)
    UTILITY_METERED = "utility.metered"
    UTILITY_PAID = "utility.paid"
    UTILITY_CUT_OFF = "utility.cut_off"

    # станки (D-150)
    STATION_PLACED = "station.placed"
    STATION_TAKEN = "station.taken"

    # разведка (D-152)
    EXPLORE_STARTED = "explore.started"
    EXPLORE_FOUND = "explore.found"
    EXPLORE_EMPTY = "explore.empty"
    #: Разведчик повернул назад: заход отменён, находка не состоялась.
    EXPLORE_CANCELLED = "explore.cancelled"

    # таможня (D-123)
    CUSTOMS_CROSSED = "customs.crossed"
    CUSTOMS_REFUSED = "customs.refused"

    # город и власть (D-127, D-130, D-153, D-154)
    CITY_FOUNDED = "city.founded"
    CITY_LAW_SET = "city.law_set"
    CITY_CHARTER_SET = "city.charter_set"
    CITY_OFFICE_APPOINTED = "city.office_appointed"
    CITY_OFFICE_REVOKED = "city.office_revoked"
    CITY_TREASURY_SPENT = "city.treasury_spent"
    CITY_GRANT_PAID = "city.grant_paid"
    #: Гражданство (D-160): попросили либо позвали, приняли, выходят, кончилось.
    CITIZENSHIP_REQUESTED = "city.citizenship_requested"
    CITIZENSHIP_GRANTED = "city.citizenship_granted"
    CITIZENSHIP_LEAVING = "city.citizenship_leaving"
    CITIZENSHIP_ENDED = "city.citizenship_ended"
    #: Голосование граждан (D-161): созвано, голос подан, итог подведён.
    VOTE_OPENED = "city.vote_opened"
    VOTE_CAST = "city.vote_cast"
    #: Выдвинулся в правители (D-162).
    VOTE_NOMINATED = "city.vote_nominated"
    #: Совет (D-164): место занято и место освобождено.
    COUNCIL_SEATED = "city.council_seated"
    COUNCIL_VACATED = "city.council_vacated"
    #: Суд (D-166): дело заведено, приговор вынесен, санкция применена и снята.
    CASE_OPENED = "justice.case_opened"
    CASE_JUDGED = "justice.case_judged"
    SANCTION_APPLIED = "justice.sanction_applied"
    SANCTION_LIFTED = "justice.sanction_lifted"
    #: Банк (D-167): ставка пересмотрена, кредит выдан и погашен.
    RATE_DECIDED = "bank.rate_decided"
    LOAN_TAKEN = "bank.loan_taken"
    LOAN_REPAID = "bank.loan_repaid"
    #: Несостоятельность (D-168): удержано принудительно, свобода ограничена.
    DEBT_WITHHELD = "bank.debt_withheld"
    DEBT_RESTRAINED = "bank.debt_restrained"
    #: Излишек резерва сожжён: денег в мире стало меньше (D-169).
    RESERVE_BURNED = "bank.reserve_burned"
    #: Процентный доход вернулся в казны городов по обороту (D-171).
    #: Отменено D-175: значение осталось ради старых событий.
    SEIGNIORAGE_PAID = "bank.seigniorage_paid"
    #: Тюремная отработка: казна заплатила за руду в погашение долга (D-174).
    PRISON_WORKOFF = "bank.prison_workoff"
    #: Репорт «дефектная печать»: снижает доверие, а не убивает (D-173).
    REPORT_FILED = "identity.report_filed"
    REPORT_WITHDRAWN = "identity.report_withdrawn"
    VOTE_CLOSED = "city.vote_closed"

    # рынок (D-047, D-127)
    MARKET_LOADED = "market.loaded"
    MARKET_TAKEN = "market.taken"
    ORDER_PLACED = "market.order_placed"
    ORDER_CANCELLED = "market.order_cancelled"
    ORDER_EXPIRED = "market.order_expired"
    RESERVATION_HELD = "market.reservation_held"
    RESERVATION_LAPSED = "market.reservation_lapsed"
    TRADE_EXECUTED = "market.trade"

    # деньги
    LEDGER_POSTED = "ledger.posted"


class Event(Base):
    __tablename__ = "event"
    __table_args__ = (
        Index("ix_event_kind_at", "kind", "at"),
        Index("ix_event_actor_at", "actor_identity_id", "at"),
        Index("ix_event_node_at", "node_id", "at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    kind: Mapped[str] = mapped_column(nullable=False)

    actor_identity_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    node_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)

    payload: Mapped[dict[str, Any]] = mapped_column(nullable=False, default=dict)

    #: Отпечаток набора констант, действовавшего в момент события. Без него
    #: разбор старого эпизода после правки баланса ничего не доказывает (D-065).
    constants_digest: Mapped[str | None] = mapped_column(nullable=True)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Event {self.id} {self.kind}>"
