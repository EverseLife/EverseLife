"""The event journal -- the primary store.

A design requirement: court, metrics and investigations rely on complete event
logging (01-tech-notes). World state is derived: any change first becomes an
event, and only then is reflected in state tables.

This cannot be reconstructed retroactively, so everything is written from day
one -- even what nobody reads yet.
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
    """Event kinds. The list grows with the action registry (06-actions)."""

    # world and housekeeping
    WORLD_BOOTSTRAPPED = "world.bootstrapped"
    CONSTANTS_CHANGED = "constants.changed"
    TICK_RAN = "tick.ran"

    # identity and body
    IDENTITY_CREATED = "identity.created"
    BODY_PRINTED = "body.printed"
    BODY_DIED = "body.died"
    #: A print is ordered and paid; the body arrives on schedule (D-028, D-033).
    BODY_PRINT_ORDERED = "body.print_ordered"
    BODY_SLEPT = "body.slept"
    MEAL_EATEN = "meal.eaten"
    BODY_WOKE = "body.woke"
    KNOWLEDGE_LEARNED = "knowledge.learned"

    # property
    ITEM_CREATED = "item.created"
    ITEM_MOVED = "item.moved"
    ITEM_CONSUMED = "item.consumed"
    ITEM_WORN = "item.worn"

    # gear and carried load (D-146)
    GEAR_EQUIPPED = "gear.equipped"
    GEAR_UNEQUIPPED = "gear.unequipped"

    # mining (D-143)
    MINING_STARTED = "mining.started"
    MINING_SWING = "mining.swing"
    MINING_TIMBERED = "mining.timbered"
    MINING_LEFT = "mining.left"
    MINING_COLLAPSED = "mining.collapsed"

    # movement (D-107)
    TRAVEL_STARTED = "travel.started"
    TRAVEL_ARRIVED = "travel.arrived"
    #: Turned back from the road: the body stays where it left from (D-194).
    TRAVEL_CANCELLED = "travel.cancelled"

    # roads as work on an edge (D-107, D-158)
    ROAD_WORK_STARTED = "road.work_started"
    ROAD_LAID = "road.laid"
    #: Overgrown: the surface dropped a tier without maintenance.
    ROAD_DECAYED = "road.decayed"

    # transport and convoy (D-157)
    TRANSPORT_HARNESSED = "transport.harnessed"
    TRANSPORT_UNHARNESSED = "transport.unharnessed"
    TRANSPORT_LOADED = "transport.loaded"
    TRANSPORT_UNLOADED = "transport.unloaded"
    #: The convoy stopped: the vehicle ran out by wear, the cargo stayed in the node.
    TRANSPORT_BROKE = "transport.broke"

    # craft (D-092, D-133)
    CRAFT_STARTED = "craft.started"
    CRAFT_FINISHED = "craft.finished"

    # land and farming (D-118)
    #: A plot handed to a person. It used to be "took wild land on foot"; since
    #: D-198 title comes from a city only, and the name stayed for the history
    #: already written under it.
    LAND_CLAIMED = "land.claimed"
    #: Purchase of civic land: the price by distance, proceeds to the treasury (D-089).
    LAND_BOUGHT = "land.bought"
    #: A plot was named (D-178). The node key stays the same.
    LAND_RENAMED = "land.renamed"
    #: A deed for a plot: issued, listed, sold (D-116).
    DEED_ISSUED = "deed.issued"
    DEED_OFFERED = "deed.offered"
    DEED_SOLD = "deed.sold"
    #: A deed cancelled: the land went to the city at founding (D-159). Civic
    #: land is not traded by deed -- the authority hands it out.
    DEED_RETIRED = "deed.retired"
    #: A building was built on a plot (D-106, D-125).
    BUILDING_BUILT = "building.built"
    PLOT_MARKED = "farm.marked"
    PLOT_PLOWED = "farm.plowed"
    PLOT_SOWN = "farm.sown"
    PLOT_CARED = "farm.cared"
    PLOT_HARVESTED = "farm.harvested"

    # energy (D-071, D-082, D-085)
    ENERGY_PRODUCED = "energy.produced"
    ENERGY_CHARGED = "energy.charged"
    ENERGY_DRAWN = "energy.drawn"
    #: Fuel poured into a station standing in the node (D-189).
    ENERGY_FUELLED = "energy.fuelled"
    # node meter and maintenance (D-135, D-149)
    UTILITY_METERED = "utility.metered"
    UTILITY_PAID = "utility.paid"
    UTILITY_CUT_OFF = "utility.cut_off"

    # machines (D-150)
    STATION_PLACED = "station.placed"
    STATION_TAKEN = "station.taken"

    # storages: chest and shelf (D-181)
    STORAGE_PUT = "storage.put"
    STORAGE_TAKEN = "storage.taken"

    #: Put down on the floor of a place and picked up from it (D-192).
    ITEM_DROPPED = "item.dropped"
    ITEM_PICKED = "item.picked"

    # exploration (D-152)
    EXPLORE_STARTED = "explore.started"
    EXPLORE_FOUND = "explore.found"
    EXPLORE_EMPTY = "explore.empty"
    #: The scout turned back: the run is cancelled, the find did not happen.
    EXPLORE_CANCELLED = "explore.cancelled"

    # customs (D-123)
    CUSTOMS_CROSSED = "customs.crossed"
    CUSTOMS_REFUSED = "customs.refused"

    # city and authority (D-127, D-130, D-153, D-154)
    CITY_FOUNDED = "city.founded"
    CITY_LAW_SET = "city.law_set"
    CITY_CHARTER_SET = "city.charter_set"
    CITY_OFFICE_APPOINTED = "city.office_appointed"
    CITY_OFFICE_REVOKED = "city.office_revoked"
    CITY_TREASURY_SPENT = "city.treasury_spent"
    CITY_GRANT_PAID = "city.grant_paid"
    #: The city rewrote its word to newcomers (D-183). It is in the journal
    #: because a promise on the card is grounds for a lawsuit, and "what was
    #: written then" must be preserved.
    CITY_DESCRIBED = "city.described"
    #: Citizenship (D-160): applied or invited, admitted, leaving, ended.
    CITIZENSHIP_REQUESTED = "city.citizenship_requested"
    CITIZENSHIP_GRANTED = "city.citizenship_granted"
    CITIZENSHIP_LEAVING = "city.citizenship_leaving"
    CITIZENSHIP_ENDED = "city.citizenship_ended"
    #: Citizens' vote (D-161): convened, vote cast, result tallied.
    VOTE_OPENED = "city.vote_opened"
    VOTE_CAST = "city.vote_cast"
    #: Nominated for ruler (D-162).
    VOTE_NOMINATED = "city.vote_nominated"
    #: Council (D-164): a seat taken and a seat vacated.
    COUNCIL_SEATED = "city.council_seated"
    COUNCIL_VACATED = "city.council_vacated"
    #: Court (D-166): a case opened, a verdict delivered, a sanction applied and lifted.
    CASE_OPENED = "justice.case_opened"
    CASE_JUDGED = "justice.case_judged"
    SANCTION_APPLIED = "justice.sanction_applied"
    SANCTION_LIFTED = "justice.sanction_lifted"
    #: Bank (D-167): the rate reviewed, a loan issued and repaid.
    RATE_DECIDED = "bank.rate_decided"
    LOAN_TAKEN = "bank.loan_taken"
    LOAN_REPAID = "bank.loan_repaid"
    #: Insolvency (D-168): withheld by force, freedom restricted.
    DEBT_WITHHELD = "bank.debt_withheld"
    DEBT_RESTRAINED = "bank.debt_restrained"
    #: Reserve surplus burned: there is less money in the world (D-169).
    RESERVE_BURNED = "bank.reserve_burned"
    #: Interest income returned to city treasuries by turnover (D-171).
    #: Cancelled by D-175: the value remains for old events.
    SEIGNIORAGE_PAID = "bank.seigniorage_paid"
    #: Prison labour: the treasury paid for ore toward the debt (D-174).
    PRISON_WORKOFF = "bank.prison_workoff"
    #: A "defective print" report: lowers trust rather than kills (D-173).
    REPORT_FILED = "identity.report_filed"
    REPORT_WITHDRAWN = "identity.report_withdrawn"
    VOTE_CLOSED = "city.vote_closed"

    # market (D-047, D-127)
    MARKET_LOADED = "market.loaded"
    MARKET_TAKEN = "market.taken"
    ORDER_PLACED = "market.order_placed"
    ORDER_CANCELLED = "market.order_cancelled"
    ORDER_EXPIRED = "market.order_expired"
    RESERVATION_HELD = "market.reservation_held"
    RESERVATION_LAPSED = "market.reservation_lapsed"
    TRADE_EXECUTED = "market.trade"

    # money
    LEDGER_POSTED = "ledger.posted"
    #: A transfer from account to account between identities (D-190).
    MONEY_TRANSFERRED = "money.transferred"


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

    #: Fingerprint of the constant set in force at the moment of the event.
    #: Without it examining an old episode after a balance edit proves nothing (D-065).

    constants_digest: Mapped[str | None] = mapped_column(nullable=True)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Event {self.id} {self.kind}>"
