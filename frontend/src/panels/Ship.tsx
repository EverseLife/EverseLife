// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/**
 * The ship: a group of nodes, not a vehicle (D-201, D-202).
 *
 * There is no "ship screen" and there must not be one -- the ship **is** the
 * map: one walks aboard along an edge and around it as around a city. What this
 * panel adds is what the map cannot say by itself, and it says it in two
 * windows rather than one (D-240):
 *
 * * **«Корабль»** -- in any compartment. The hull's card: the engines one by
 *   one, the mass split into hull, machines and cargo, the speed that follows,
 *   the air aboard, the name, and the floor plan the owner arranges. Nothing
 *   here moves the ship, so nothing here asks for the bridge;
 * * **«Консоль управления кораблём»** -- at the bridge and nowhere else. The
 *   ship's **own chart** of the sky, the two orders a hull takes -- casting off
 *   and the passage -- and the course, which is set on the chart.
 *
 * The console used to open the world map instead. A world map answers "what is
 * where"; a bridge asks "where can I go, for how long and at what cost", and
 * that answer is different for every hull because it comes from this ship's
 * thrust against this ship's mass. Two questions, two drawings.
 *
 * The one thing this panel must never do is spring a refusal after the hold is
 * loaded. Thrust-to-mass, the floor it must clear and the price of every course
 * are on screen **before** the undocking, refusals included, with the reason
 * named -- class or mass. A bare "unavailable" leaves nothing to act on.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import { standingMap, stationsOf, worldMap, type Look, type WorldMap } from "../api";
import { oneEach } from "./map/geo";
import { Deadline } from "../Deadline";
import { Rule } from "../Rule";
import { busyWith } from "../busy";
import { firstOfClass } from "../classes";
import { Refusal, useActions, useBook, useEdition, useNames, useSession } from "../actions";
import { t } from "../locale";
import { goodsName } from "../names";
import { planetName } from "../planets";
import { Chart } from "./ship/Chart";
import { Course } from "./ship/Course";
import { Drift, Passage, Ties } from "./ship/Voyage";
import { Landing } from "./ship/Landing";
import { Feed } from "./ship/Feed";
import { Plan } from "./ship/Plan";
import { autonomy, spelt, wanted, type Target, type Vessel } from "./ship/model";
import { term } from "./map/orbits";

/**
 * Thing classes, not item names (D-215): the foundation a node aboard is laid
 * from, and the yard it is laid at. The engine asks by the same words
 * (`ship.FOUNDATION`, `ship.SPACEPORT`); the names come from the catalog.
 */
const FOUNDATION = "ship_foundation";
const SPACEPORT = "shipyard";
/** The node property that marks a node as being aboard: it arrives in `features`. */
const ABOARD = "aboard";
/** The console's class: the ship is commanded from it (D-230). */
const BRIDGE = "bridge";
/** The ground console's class (D-242). Named here to say what to put up when
 *  the one standing here is somebody else's -- by its key, so the word the
 *  player reads is the vault's own and follows a rename (D-251). */
const GROUND_BRIDGE = "ground_bridge";
/**
 * The occupation a keel being laid is (D-211, `engine.occupation`).
 *
 * A keel takes `ship.foundation_hours` and the foundation goes out of the
 * pocket the moment the button is pressed. Without a line saying so the item
 * simply vanished and the node appeared eight hours later, which reads as a
 * broken button rather than as work. The line comes from `look.doings` -- the
 * server already names every occupation there, and a key of its own in the
 * ship's answer would repeat what the client has (D-225).
 */
const KEEL = "keel";
/** Below this share of a full tank the air reads as an alarm, not a number. */
const AIR_LOW = 0.25;

/**
 * The hull's card (D-230): the engines one by one, the mass by where it comes
 * from, the speed that follows, and the air. Numbers the owner can act on
 * separately -- a node is cut by not laying it, a machine by taking it down,
 * cargo by unloading -- where one total would say nothing.
 */
function Card({ v }: { v: Vessel }) {
  const names = useNames();
  const parts = v.mass_parts;
  const hours = autonomy(v.air);
  return (
    <table>
      <tbody>
        {v.engines.length === 0 && (
          <tr>
            <td>{t("ui-ship-engines")}</td>
            <td className="note">{t("ui-ship-engines-none")}</td>
          </tr>
        )}
        {v.engines.map((engine) => (
          <tr key={engine.name}>
            <td>{goodsName(names, engine.name)}</td>
            <td className="note">
              {t("ui-ship-engine-row", {
                count: String(engine.count),
                thrust: engine.thrust.toFixed(0),
                class: String(engine.class),
              })}
            </td>
          </tr>
        ))}
        <tr>
          <td>{t("ui-ship-mass")}</td>
          <td className="note">
            {t("ui-ship-mass-parts", {
              hull: parts.hull.toFixed(0),
              machines: parts.machines.toFixed(0),
              cargo: parts.cargo.toFixed(0),
            })}
          </td>
        </tr>
        <tr>
          <td>{t("ui-ship-speed")}</td>
          <td className="note">
            {t("ui-ship-ratio", { ratio: v.ratio.toFixed(2) })}
            {v.class != null && ` · ${t("ui-ship-class", { class: String(v.class) })}`}
            {v.ratio < v.min_ratio && ` · ${t("ui-ship-below-threshold")}`}
          </td>
        </tr>
        {/* The air (D-233, D-288): what stands on the life support's line.
            Shown always, because "nothing is being spent" is itself the answer
            to "how long can we stay out there". */}
        <tr>
          <td>{t("ui-ship-air")}</td>
          <td className="note">
            {t("ui-ship-air-line", { units: v.air.units.toFixed(0) })}
            {v.air.sealed
              ? hours != null
                ? ` · ${t("ui-ship-air-burn", {
                    spend: (-v.air.per_hour).toFixed(2),
                    term: term(hours),
                  })}`
                : ` · ${t("ui-ship-air-covered")}`
              : ` · ${t("ui-ship-air-outside")}`}
          </td>
        </tr>
      </tbody>
    </table>
  );
}

/** The air as a bar: the second scale of survival, read at a glance (D-233). */
function AirBar({ v }: { v: Vessel }) {
  if (!v.air.sealed) return null;
  const hours = autonomy(v.air);
  //: A full bar is a hull that makes as much as it breathes; the bar falls only
  //: once the tanks are actually going down, which is the only case worth a
  //: colour. Scaled by a day of the reserve it started the stretch with.
  const share = hours == null ? 1 : Math.min(1, hours / 24);
  return (
    <span className={`air-bar${share < AIR_LOW ? " low" : ""}`} aria-hidden="true">
      <span style={{ width: `${Math.round(share * 100)}%` }} />
    </span>
  );
}

/**
 * The climb: the one move a hull on the ground has (D-245).
 *
 * It used to be a button called "Отстыковаться" that cost nothing and took no
 * time, while coming back down to the very pad one had left was priced as a
 * whole passage between worlds. Now it is a leg like the others -- hours by the
 * planet's gravity, fuel out of the tanks -- and it may be countermanded while
 * it lasts.
 *
 * Two numbers, not one: what the climb burns, and what the round trip takes.
 * The difference is the descent home -- and since D-289 it is a warning, not
 * a lock: a hull that climbs short of it sits on its circle until fuel
 * reaches it, which is a place a rescue can be sent to.
 */
function Ascent({
  vessel,
  busy,
  ascend,
}: {
  vessel: Vessel;
  busy: boolean;
  ascend: () => void;
}) {
  const climb = vessel.climb;
  if (!climb) {
    return <p className="note">{t("ui-ship-no-orbit")}</p>;
  }
  //: Two thresholds, two different ends (D-289): short of the climb itself
  //: the hull does not leave the pad -- the engine refuses, so the button is
  //: dark; short only of the descent back it climbs and sits on its circle
  //: until fuel reaches it -- a warning, and the button stays lit.
  const dryClimb = climb.fuel != null && vessel.fuel < climb.fuel;
  const dry = !dryClimb && vessel.fuel < wanted(climb);
  return (
    <>
      <p>
        <b>{t("ui-ship-climb-to", { planet: planetName(climb.planet) })}</b> ·{" "}
        {climb.hours == null
          ? t("ui-ship-no-thrust")
          : t("ui-ship-leg-cost", {
              hours: climb.hours.toFixed(1),
              fuel: climb.fuel?.toFixed(0) ?? "",
            })}{" "}
        <button
          onClick={ascend}
          disabled={busy || !climb.reachable || !vessel.life_support || dryClimb}
          title={t(climb.reachable ? "ui-ship-ascend-hint" : "ui-ship-thrust-short")}
        >
          {t("ui-ship-ascend")}
        </button>
      </p>
      {!climb.reachable && climb.hours != null && (
        <p className="note">{t("ui-ship-ratio-short")}</p>
      )}
      {dryClimb ? (
        <p className="reason">
          {t("ui-ship-dry-climb", {
            fuel: vessel.fuel.toFixed(0),
            need: climb.fuel?.toFixed(0) ?? "",
          })}
        </p>
      ) : dry ? (
        <p className="reason">
          {t("ui-ship-dry-ascent", {
            fuel: vessel.fuel.toFixed(0),
            need: climb.needs?.toFixed(0) ?? "",
          })}
        </p>
      ) : (
        <p className="note">
          {t("ui-ship-reserve", {
            kept: ((climb.needs ?? 0) - (climb.fuel ?? 0)).toFixed(0),
          })}
        </p>
      )}
      {/* The warning D-288 asks the bridge for: not only the death, but the
          plumbing that leads to it. A hull whose life support stands on an
          empty line seals on lifting off and the crew suffocates on the way
          up -- which is what happened the first time this was flown. The
          engine does not refuse it, and should not: a hull may be flown empty
          on purpose, and since D-289 what the tanks lack is the console's
          warning rather than the helm's refusal. So it is named before the
          order, beside the fuel, which is named the same way.

          Only with somebody aboard -- an empty hull has no breath to lose --
          and only where there is a system at all: a hull without one is
          already told so on the line above, and a second line about that
          system's line would be a machine nobody has.

          Not gated on `air.sealed`, and worded for that: at the pier under a
          sky with air in it the hatch is open and the crew breathes fine, so
          the warning says what is aboard and what the shut hatch would mean.
          Gating it would hide it exactly where it is worth reading -- before
          the order, with the hatch still open. */}
      {vessel.crew > 0 && vessel.life_support && vessel.air.units <= 0 && (
        <p className="reason">
          {/* Two words for two troubles (D-288), chosen here rather than by a
              variant inside one message: a bottle to find is not a line to
              draw. The figure goes in spelled out, as every count in this
              window does -- handed to Fluent as a number it would come back
              with a thousands separator the card beside it does not use. */}
          {vessel.air.off_line > 0
            ? t("ui-ship-airless-stowed", { off: spelt(vessel.air.off_line) })
            : t("ui-ship-airless-none")}
        </p>
      )}
      <p className="note">{t("ui-ship-course-later")}</p>
    </>
  );
}


/** The nameplate: the owner's word, and the engine makes nothing of it (D-240). */
function Nameplate({
  vessel,
  busy,
  rename,
}: {
  vessel: Vessel;
  busy: boolean;
  rename: (name: string) => void;
}) {
  const [editing, setEditing] = useState(false);
  const [name, setName] = useState(vessel.name);
  if (!editing) {
    return (
      <button className="quiet" onClick={() => (setName(vessel.name), setEditing(true))}>
        {t("ui-ship-rename")}
      </button>
    );
  }
  return (
    <>
      <input
        value={name}
        onChange={(e) => setName(e.target.value)}
        aria-label={t("ui-ship-name-label")}
      />
      <button
        onClick={() => {
          rename(name);
          setEditing(false);
        }}
        disabled={busy || !name.trim()}
      >
        {t("ui-ship-name-set")}
      </button>
      <button className="quiet" onClick={() => setEditing(false)}>
        {t("ui-ship-cancel")}
      </button>
    </>
  );
}

export function Ship({
  look,
  console: atConsole = false,
  ground = false,
}: {
  look: Look;
  /** The bridge aboard: the ship one is standing in, and its orders. */
  console?: boolean;
  /** The ground console (D-242): every hull of one's own, and the same orders. */
  ground?: boolean;
}) {
  const session = useSession();
  const book = useBook();
  const names = useNames();
  //: This panel's own waiting and its own refusal: laying a keel must not grey
  //: out the chat and the map for eight hours of somebody else's work.
  const acting = useActions();
  const { busy, act } = acting;

  const [ships, setShips] = useState<Vessel[]>([]);
  const [name, setName] = useState("");
  const [course, setCourse] = useState<Target | null>(null);
  //: The arc under the slider's thumb, for the chart (D-289).
  const [plan, setPlan] = useState<[number, number][] | null>(null);
  //: The map read once for the console: the spheres for the chart -- the sky
  //: is answered to everybody (D-240), so this read works in flight, where
  //: the hull has no edges -- and the surface the landing is picked on
  //: (`Landing`). Two reads laid over each other: the anonymous snapshot
  //: (D-319 item 7), which carries every node of a planet's surface but is
  //: `map.public_delay_days` behind, and the body's own map, which carries
  //: only what it sees and remembers but is of this minute. Why both, and
  //: what it costs, is at the read below.
  const [world, setWorld] = useState<WorldMap | null>(null);
  const sky = useMemo(() => world?.nodes.filter((node) => node.orbit) ?? [], [world]);

  const aboard = (look.node?.features ?? []).includes(ABOARD);
  const atPort = firstOfClass(book, stationsOf(look), SPACEPORT) !== undefined;
  //: Orders are given at the bridge: a console standing in this very room,
  //: and the room aboard (D-230). The engine refuses otherwise; the panel
  //: does not offer what would be refused.
  const bridge = aboard && firstOfClass(book, stationsOf(look), BRIDGE) !== undefined;
  const foundationName = firstOfClass(
    book,
    look.inventory.map((thing) => thing.goods),
    FOUNDATION,
  );
  const foundation = look.inventory.find((thing) => thing.goods === foundationName);
  //: A keel of this body's already under way. It is what the yard is doing, so
  //: it is drawn even when nothing else about the ship exists yet -- the node
  //: is not there until the deadline.
  const keel = (look.doings ?? []).find((doing) => doing.kind === KEEL) ?? null;
  //: One pair of hands lays one keel (D-211). The button says which occupation
  //: is in the way **before** the press: a refusal collected after it says the
  //: same thing one step too late.
  const occupied = busyWith(look);

  const reload = useCallback(async () => {
    //: The ground console asks for the whole fleet: it may itself stand in a
    //: compartment of the flagship, and the plain reading would then collapse
    //: to that one hull and hide the rest (D-242).
    const answer = await session.send("ship.view", ground ? { fleet: true } : {});
    setShips((answer.ships ?? []) as Vessel[]);
  }, [session, ground]);
  //: Reread when the world says so (D-226), not on every look. A line drawn
  //: changes what the tanks are worth to the engines (D-288).
  const edition = useEdition("ship.", "transport.", "line.");

  useEffect(() => {
    void reload();
  }, [reload, edition]);

  //: Whether this window gives orders at all: the bridge aboard, or the ground
  //: console. The ship's own card gives none and asks for no chart.
  const orders = atConsole || ground;
  //: What the personal map is read from, said the same way the map window
  //: says it: the node stood in and the exits out of it. The same stand is
  //: the same map, and the two windows then share one read.
  const stand = `${look.node?.key ?? ""}|${(look.exits ?? []).map((path) => path.key).join("|")}`;
  useEffect(() => {
    if (!orders) return;
    //: Both maps, the crew's own laid over the public one (owner,
    //: 2026-09-08). The public snapshot is what the **world** knows, and
    //: choosing a pier by it is what D-319 item 7 asked for -- but it is
    //: served `map.public_delay_days` late, so a world younger than that
    //: delay has no public map at all, and its first crews met a globe with
    //: nothing on it. What one's own map knows fills that in: a pier one has
    //: seen or remembers is drawn even where the snapshot has not caught up.
    //: Either half may fail on its own and the other still draws a globe --
    //: caught apart for that reason, and not once around both.
    let live = true;
    void Promise.all([
      worldMap().catch(() => null),
      //: The map the window beside this one already read from this stand.
      standingMap(session.token, stand).catch(() => null),
    ]).then(([open, own]) => {
      if (!live) return;
      setWorld(
        open || own
          ? { ...(open ?? own!), nodes: oneEach([...(open?.nodes ?? []), ...(own?.nodes ?? [])]) }
          : null,
      );
    });
    //: A stand left while its answer was still coming must not paint the
    //: globe of the stand before it.
    return () => {
      live = false;
    };
  }, [orders, session.token, stand]);

  const go = (what: () => Promise<unknown>) =>
    act(async () => {
      await what();
      await reload();
    });

  if (atConsole && !aboard) {
    return (
      <section>
        <h2>{t("ui-ship-console")}</h2>
        <p className="note">{t("ui-ship-console-aground")}</p>
      </section>
    );
  }
  if (!ground && !aboard && !atPort) return null;

  //: Aboard the summary is about the ship underfoot; at a port -- about the
  //: ones that are yours, because from the pier one commands one's own. At the
  //: ground console -- about every hull of one's own, wherever it is: that is
  //: the whole point of it (D-242).
  const shown = ground
    ? ships
    : aboard
      ? ships
      : ships.filter((v) => v.docked === look.node?.key || v.docked == null);
  //: The bridge speaks about the hull one is standing in and no other: it
  //: commands its own ship (D-230). The ground console speaks about all of them.
  const commanded = atConsole ? shown.slice(0, 1) : shown;

  return (
    <section>
      <Refusal of={acting} />
      <h2>
        {t(
          ground
            ? "ui-ship-ground-console"
            : atConsole
              ? "ui-ship-console"
              : aboard
                ? "ui-ship-title"
                : "ui-ship-yard",
        )}
        <Rule>{t("ui-ship-rule")}</Rule>
      </h2>

      {commanded.map((v) => {
        //: A hull with no console of its own hears nothing from the ground
        //: (D-242). Said once, above, and every order greyed out with it --
        //: a refusal collected after the click says the same thing too late.
        const deaf = ground && !v.bridge;
        //: And a console one may not work at answers nothing either: orders
        //: are given from one's own (`ship.command`, the `may_build` right --
        //: the same flag the floor's window is worded by). Said here for the
        //: same reason as `deaf`: the chart, the slider and «Лететь» stood
        //: lit over a pult that refused every one of them, and the reason
        //: arrived only after the click.
        const borrowed = ground && look.floor?.mine === false;
        const mute = deaf || borrowed;
        return (
        <div key={v.ship}>
          <p className="sign">
            {t("ui-ship-sign", {
              name: v.name,
              nodes: String(v.nodes),
              thrust: v.thrust.toFixed(0),
              mass: v.mass.toFixed(0),
            })}
          </p>
          <AirBar v={v} />
          <p className="note">
            {t("ui-ship-ratio-line", {
              ratio: v.ratio.toFixed(2),
              min: v.min_ratio.toFixed(2),
            })}
            {v.ratio < v.min_ratio && <b> · {t("ui-ship-stuck")}</b>} ·{" "}
            {t("ui-ship-crew", {
              crew: String(v.crew),
              fuel: v.fuel.toFixed(0),
            })}
            {/* No number of people a system holds (D-288): what is worth a
                word is a hull with no system at all, which does not cast off. */}
            {!v.life_support && <b> · {t("ui-ship-no-life-support")}</b>}
            {v.stage === "orbit"
              ? ` · ${t("ui-ship-in-orbit", { planet: planetName(v.planet) })}`
              : v.docked
                ? ` · ${t("ui-ship-berthed", {
                    port: String(v.port),
                    berth: String(v.berth ?? "—"),
                  })}`
                : v.flight
                  ? ` · ${v.flight.star ? t("ui-ship-flight-star") : t("ui-ship-on-voyage", { name: v.flight.name })}`
                  : v.stage === "lost"
                    ? ` · ${t("ui-ship-lost-status")}`
                    : ` · ${t("ui-ship-adrift")}`}
          </p>
          {/* A passage is a term like any other, and every term in this world
              is drawn the same way. At the console the whole card stands
              instead (`Passage`); in the ship's own window the bar alone is
              what there is room for. */}
          {v.flight && !orders && (
            <Deadline
              until={v.flight.arrives_at}
              since={v.flight.started_at}
              label={t("ui-ship-flight-label")}
            />
          )}

          {orders ? (
            <>
              {/* A hull with no console of its own hears nothing from here: the
                  ground station talks to the bridge, and there is none. Said
                  before the buttons rather than as a refusal after them. */}
              {deaf && (
                <p className="reason">{t("ui-ship-deaf")}</p>
              )}
              {borrowed && (
                <p className="reason">
                  {t("ui-ship-console-borrowed", {
                    console: goodsName(
                      names,
                      firstOfClass(book, stationsOf(look), GROUND_BRIDGE) ?? "",
                    ),
                  })}
                </p>
              )}
              <Chart
                vessel={v}
                planets={sky}
                epoch={look.clock?.epoch ?? null}
                chosen={course}
                onChoose={setCourse}
                plan={plan}
              />
              {/* One stage, one set of orders (D-245). From the pad the
                  only move is up; under way the only move is back; from orbit
                  there are two, and the one wanted most often is down. */}
              {v.stage === "port" ? (
                <Ascent
                  vessel={v}
                  busy={busy || mute}
                  ascend={() => go(() => session.send("ship.ascend", { ship: v.ship }))}
                />
              ) : v.stage === "lost" ? (
                <p className="reason">{t("ui-ship-lost-note")}</p>
              ) : v.stage === "flight" ? (
                <Passage
                  v={v}
                  busy={busy}
                  mute={mute}
                  recall={() => go(() => session.send("ship.recall", { ship: v.ship }))}
                  cancel={() => go(() => session.send("ship.cancel", { ship: v.ship }))}
                  orbit={() => go(() => session.send("ship.orbit", { ship: v.ship }))}
                />
              ) : (
                <>
                  {/* Adrift, the hull has no planet under it to come down on;
                      what it has is a coast and a verdict (D-289). A course
                      it may lay from anywhere the tanks allow. */}
                  {v.stage === "adrift" ? (
                    <Drift
                      v={v}
                      busy={busy || mute}
                      dock={(other) =>
                        go(() => session.send("ship.dock", { ship: v.ship, ship_target: other }))
                      }
                      undock={() => go(() => session.send("ship.undock", { ship: v.ship }))}
                      orbit={() => go(() => session.send("ship.orbit", { ship: v.ship }))}
                    />
                  ) : (
                    <>
                      <Landing
                        vessel={v}
                        busy={busy || mute}
                        book={book}
                        clock={look.clock}
                        world={world}
                        land={(port) => go(() => session.send("ship.land", { ship: v.ship, port }))}
                      />
                      {/* In orbit a hull meets the others as any hull in the
                          sky does (D-354): the hold and the docking. */}
                      <div className="doing">
                        <Ties
                          v={v}
                          busy={busy || mute}
                          dock={(other) =>
                            go(() => session.send("ship.dock", { ship: v.ship, ship_target: other }))
                          }
                          undock={() => go(() => session.send("ship.undock", { ship: v.ship }))}
                        />
                      </div>
                    </>
                  )}
                  <Course
                    vessel={v}
                    target={course}
                    busy={busy || mute}
                    fly={(to, hours, via) =>
                      go(() =>
                        session.send(
                          "ship.fly",
                          "planet" in to
                            ? {
                                ship: v.ship,
                                planet: to.planet,
                                hours,
                                ...(via ? { via } : {}),
                              }
                            : { ship: v.ship, ship_target: to.ship, hours },
                        ),
                      )
                    }
                    onPlan={setPlan}
                  />
                </>
              )}
              {/* The plumbing (D-288): drawn from the console, because it is
                  an order about the hull like any other, and the engine
                  refuses it to anybody but the owner at a console. */}
              {v.yours && (
                <Feed
                  vessel={v}
                  busy={busy || mute}
                  plumb={(machine, port, vessels) =>
                    go(() => session.send("line.set", { ship: v.ship, machine, port, vessels }))
                  }
                />
              )}
            </>
          ) : (
            aboard && (
              <>
                <Card v={v} />
                {v.yours && (
                  <p>
                    <Nameplate
                      vessel={v}
                      busy={busy}
                      rename={(next) =>
                        go(() => session.send("ship.rename", { ship: v.ship, name: next }))
                      }
                    />
                  </p>
                )}
                {look.ships && (
                  <Plan
                    sight={look.ships}
                    here={look.node?.key ?? ""}
                    mine={v.yours}
                    grid={v.grid}
                    busy={busy}
                    onArrange={(spots) =>
                      go(() => session.send("ship.arrange", { ship: v.ship, spots }))
                    }
                  />
                )}
                {!bridge && (
                  <p className="note">{t("ui-ship-no-bridge")}</p>
                )}
              </>
            )
          )}
        </div>
        );
      })}

      {/* The keel itself: the eight hours between the foundation leaving the
          pocket and the node appearing. Without this the yard was silent for
          the whole of them. */}
      {!orders && keel && (
        <div className="doing">
          <span className="doing-what">
            {keel.title}: {keel.what}
          </span>
          {keel.until && <Deadline until={keel.until} label={t("ui-ship-keel-label")} />}
          <span className="doing-aside note">{t("ui-ship-keel-note")}</span>
        </div>
      )}

      {!orders && aboard && !keel && (
        <button
          onClick={() => go(() => session.send("ship.extend"))}
          disabled={busy || !foundation || occupied !== null}
          title={occupied ?? undefined}
        >
          {t("ui-ship-lay-keel")}
        </button>
      )}

      {!orders && !aboard && atPort && !keel && (
        <>
          <input
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder={t("ui-ship-name-placeholder")}
          />
          <button
            //: An unnamed hull is named by the engine, which has the word for
            //: it already (`ship.found`). Sending a default from here was the
            //: same word written twice, and one of the two in a language the
            //: window is not allowed to know (D-251).
            onClick={() => go(() => session.send("ship.found", { name }))}
            disabled={busy || !foundation || occupied !== null}
            title={occupied ?? undefined}
          >
            {t("ui-ship-lay-keel")}
          </button>
        </>
      )}

      {!orders && !keel && occupied !== null && <p className="note">{occupied}</p>}

      {!orders && !keel && !foundation && (
        <p className="note">
          {t("ui-ship-need-foundation", {
            goods:
              foundationName !== undefined
                ? goodsName(names, foundationName)
                : t("ui-ship-foundation-word"),
          })}
        </p>
      )}
    </section>
  );
}
