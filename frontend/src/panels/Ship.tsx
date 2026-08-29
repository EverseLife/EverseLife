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

import { useCallback, useEffect, useState } from "react";
import { stationsOf, worldMap, type Look, type MapNode } from "../api";
import { Deadline } from "../Deadline";
import { Rule } from "../Rule";
import { busyWith } from "../busy";
import { firstOfClass } from "../classes";
import { Refusal, useActions, useBook, useEdition, useSession } from "../actions";
import { planetName } from "../planets";
import { Chart } from "./ship/Chart";
import { Plan } from "./ship/Plan";
import { autonomy, wanted, type Pad, type Route, type Vessel } from "./ship/model";
import { term } from "./map/orbits";

/**
 * Thing classes, not item names (D-215): the foundation a node aboard is laid
 * from, and the yard it is laid at. The engine asks by the same words
 * (`ship.FOUNDATION`, `ship.SPACEPORT`); the names come from the catalog.
 */
const FOUNDATION = "Основа корабля";
const SPACEPORT = "Верфь";
/** The node property that marks a node as being aboard: it arrives in `features`. */
const ABOARD = "борт";
/** The console's class: the ship is commanded from it (D-230). */
const BRIDGE = "Рубка";
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
  const parts = v.mass_parts;
  const hours = autonomy(v.air);
  return (
    <table>
      <tbody>
        {v.engines.length === 0 && (
          <tr>
            <td>двигатели</td>
            <td className="note">нет ни одного: корабль не летит</td>
          </tr>
        )}
        {v.engines.map((engine) => (
          <tr key={engine.name}>
            <td>{engine.name}</td>
            <td className="note">
              ×{engine.count} · тяга {engine.thrust.toFixed(0)} каждый · класс {engine.class}
            </td>
          </tr>
        ))}
        <tr>
          <td>масса</td>
          <td className="note">
            корпус {parts.hull.toFixed(0)} кг · станции {parts.machines.toFixed(0)} кг · груз{" "}
            {parts.cargo.toFixed(0)} кг
          </td>
        </tr>
        <tr>
          <td>скорость</td>
          <td className="note">
            {v.ratio.toFixed(2)} тяги на кг массы
            {v.class != null && ` · класс ${v.class}`}
            {v.ratio < v.min_ratio && " · ниже порога отрыва"}
          </td>
        </tr>
        {/* The air (D-233, D-234). Shown always, because "nothing is being
            spent" is itself the answer to "how long can we stay out there". */}
        <tr>
          <td>кислород</td>
          <td className="note">
            {v.air.units.toFixed(0)} в баках · вода {v.air.water.toFixed(0)}
            {v.air.sealed
              ? hours != null
                ? ` · расход ${(-v.air.per_hour).toFixed(2)} в час · хватит на ${term(hours)}`
                : " · жизнеобеспечение покрывает экипаж"
              : " · за бортом воздух, генерация спит"}
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
 * Two numbers, not one: what the climb burns, and what the tanks must hold for
 * it to be allowed at all. The difference is the descent home, which is kept
 * back rather than spent -- an orbit has no bunker.
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
    return <p className="note">Отсюда не подняться: у этой планеты нет орбитального узла.</p>;
  }
  const dry = vessel.fuel < wanted(climb);
  return (
    <>
      <p>
        <b>{climb.name}</b> ·{" "}
        {climb.hours == null
          ? "тяги нет вовсе: поставьте двигатель"
          : `${climb.hours.toFixed(1)} ч · ${climb.fuel?.toFixed(0)} топлива`}{" "}
        <button
          onClick={ascend}
          disabled={
            busy ||
            !climb.reachable ||
            vessel.crew > vessel.life_support ||
            dry
          }
          title={
            climb.reachable
              ? "подъём занимает время по тяжести планеты и тяге корпуса; его можно развернуть"
              : "тяги не хватает, чтобы оторваться: снимите массу или добавьте двигатель"
          }
        >
          Подняться на околопланетную орбиту
        </button>
      </p>
      {!climb.reachable && climb.hours != null && (
        <p className="note">Тяговооружённости не хватает: корабль не отрывается.</p>
      )}
      {dry ? (
        <p className="note">
          В баках {vessel.fuel.toFixed(0)}, а нужно {climb.needs?.toFixed(0)}: подъём и
          спуск обратно. На орбите не заправляют — рёбер к кораблю нет, и с борта не сойти.
        </p>
      ) : (
        <p className="note">
          Сверх расхода на подъём держится {((climb.needs ?? 0) - (climb.fuel ?? 0)).toFixed(0)} на
          спуск обратно: подниматься без топлива на спуск некуда.
        </p>
      )}
      <p className="note">
        Курс на другую планету задаётся уже с орбиты: сперва подъём, потом переход, потом
        выбор космодрома над планетой.
      </p>
    </>
  );
}

/**
 * Coming down, onto the planet the hull is already over.
 *
 * The pier is chosen here and not before the passage: with the planet already
 * below, which is the moment a crew actually knows what it is choosing between
 * and the moment a dark beacon actually matters (D-245).
 */
function Landing({
  vessel,
  busy,
  land,
}: {
  vessel: Vessel;
  busy: boolean;
  land: (port: string) => void;
}) {
  const home: Pad[] = vessel.landings;
  const cost = vessel.descent;
  const [chosen, setChosen] = useState("");
  if (home.length === 0 || !cost) {
    return (
      <p className="note">
        Садиться здесь некуда: ни одного космодрома с горящим маяком на этой планете.
        Курс на другую планету задаётся на карте.
      </p>
    );
  }
  const first = home[0];
  const port = chosen || first.node;
  return (
    <p>
      <b>Сесть на планету</b> · {planetName(vessel.planet)} · {cost.hours?.toFixed(1)} ч ·{" "}
      {cost.fuel?.toFixed(0)} топлива{" "}
      {home.length > 1 ? (
        <select
          value={port}
          onChange={(e) => setChosen(e.target.value)}
          aria-label="космодром для посадки"
        >
          {home.map((route) => (
            <option key={route.node} value={route.node}>
              {route.name}
            </option>
          ))}
        </select>
      ) : first.anywhere ? (
        <span
          className="note"
          title="здесь нет космодромов: узел посадки разыгрывается при заходе, и садятся туда, куда пустила скала"
        >
          посадка вслепую
        </span>
      ) : (
        <span className="note">{first.name}</span>
      )}{" "}
      <button
        onClick={() => land(port)}
        disabled={busy || !cost.reachable}
        title={
          cost.reachable
            ? "спуск идёт по тяжести планеты и тяге корпуса — чуть дешевле подъёма"
            : "тяги не хватает даже на посадку: снимите массу"
        }
      >
        Сесть
      </button>
    </p>
  );
}

/**
 * A passage under way: where it goes, how far it has got, and when it ends.
 *
 * The bar is the one every term in this world wears (D-238). It matters more
 * here than anywhere: a hull in flight takes no orders at all, so the only
 * thing the console can honestly offer is the answer to "how long".
 */
function Passage({
  v,
  busy,
  deaf,
  recall,
}: {
  v: Vessel;
  busy: boolean;
  /** No console aboard: the hull hears nothing from the ground (D-242). */
  deaf: boolean;
  recall: () => void;
}) {
  if (!v.flight) return null;
  return (
    <div className="doing">
      <span className="doing-what">
        {v.flight.back ? "разворот" : "рейс"} в «{v.flight.name}»
        {v.flight.planet && ` · ${planetName(v.flight.planet)}`}
      </span>
      <Deadline until={v.flight.arrives_at} since={v.flight.started_at} label="рейс" />
      <span className="doing-aside note">
        Время сосчитано на отходе и не пересчитывается: небо, повернувшееся под
        летящим кораблём, сделало бы рейс длиннее оплаченного. Курс менять
        нельзя.{!v.flight.back && " Но можно развернуться."}
      </span>
      {/* The helm may still go over (D-242): the way back is as long as the way
          out has been, and costs its own fuel. Named with the pier it aims at,
          because "cancel" alone would not say where the hull ends up. */}
      {/* Already going back: there is nothing left to turn (D-242). */}
      {!v.flight.back && (
        <button className="quiet" onClick={recall} disabled={busy || deaf || !v.left}>
          Развернуться{v.left ? ` в «${v.left}»` : ""}
        </button>
      )}
      {!v.flight.back && !v.left && (
        <span className="note">
          Неизвестно, откуда корабль ушёл: развернуться не к чему, он дойдёт до конца.
        </span>
      )}
    </div>
  );
}

/**
 * The course: what the chart's chosen planet costs, and the order to cross to it.
 *
 * One planet is one row, because a crossing goes orbit to orbit (D-245): the
 * sky has one distance to a world, and which pad the hull ends on is not
 * decided here at all -- it is chosen over the planet, once the hull is there.
 */
function Course({
  vessel,
  planet,
  busy,
  fly,
}: {
  vessel: Vessel;
  planet: string | null;
  busy: boolean;
  fly: (orbit: string) => void;
}) {
  if (planet === null) {
    return <p className="note">Курс задаётся на карте: выберите планету.</p>;
  }
  const routes: Route[] = vessel.routes.filter((route) => route.planet === planet);
  if (routes.length === 0) {
    return (
      <p className="note">
        Отсюда туда хода нет: либо маршрута в мире не заведено, либо на той планете не
        светит ни один маяк — корабль ушёл бы туда и остался на орбите.
      </p>
    );
  }
  const first = routes[0];
  const port = first.node;
  return (
    <p>
      <span
        className="planet-dot"
        style={{ background: `var(--planet-${planet})` }}
        aria-hidden="true"
      />
      <b>{planetName(planet)}</b> · {first.hours?.toFixed(1)} ч · {first.fuel?.toFixed(0)} топлива
      {!first.reachable && " · тяги не хватает: снимите массу"}{" "}
      <span className="note">{first.name}</span>{" "}
      <button
        onClick={() => fly(port)}
        disabled={busy || !first.reachable || vessel.fuel < wanted(first)}
        title={
          first.reachable
            ? "переход идёт с орбиты на орбиту; космодром выбирается уже над планетой"
            : "тяги не хватает, чтобы оторваться: снимите массу или добавьте двигатель"
        }
      >
        Лететь
      </button>
      {vessel.fuel < wanted(first) && (
        <span className="note">
          {" "}
          · в баках {vessel.fuel.toFixed(0)}, а нужно {first.needs?.toFixed(0)}: переход и
          посадка в конце
        </span>
      )}
    </p>
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
        Переименовать
      </button>
    );
  }
  return (
    <>
      <input value={name} onChange={(e) => setName(e.target.value)} aria-label="имя корабля" />
      <button
        onClick={() => {
          rename(name);
          setEditing(false);
        }}
        disabled={busy || !name.trim()}
      >
        Назвать
      </button>
      <button className="quiet" onClick={() => setEditing(false)}>
        Отмена
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
  //: This panel's own waiting and its own refusal: laying a keel must not grey
  //: out the chat and the map for eight hours of somebody else's work.
  const acting = useActions();
  const { busy, act } = acting;

  const [ships, setShips] = useState<Vessel[]>([]);
  const [name, setName] = useState("");
  const [course, setCourse] = useState<string | null>(null);
  //: The spheres for the chart. The sky is answered to everybody (D-240), so
  //: this read works in flight, where the hull has no edges and the world map
  //: would otherwise be able to say nothing at all about where it is.
  const [sky, setSky] = useState<MapNode[]>([]);

  const aboard = (look.node?.features ?? []).includes(ABOARD);
  const atPort = firstOfClass(book, stationsOf(look), SPACEPORT) !== undefined;
  //: Orders are given at the bridge: a console standing in this very room,
  //: and the room aboard (D-230). The engine refuses otherwise; the panel
  //: does not offer what would be refused.
  const bridge = aboard && firstOfClass(book, stationsOf(look), BRIDGE) !== undefined;
  const foundationName = firstOfClass(book, look.inventory.map((t) => t.goods), FOUNDATION);
  const foundation = look.inventory.find((t) => t.goods === foundationName);
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
  //: Reread when the world says so (D-226), not on every look.
  const edition = useEdition("ship.", "transport.");

  useEffect(() => {
    void reload();
  }, [reload, edition]);

  //: Whether this window gives orders at all: the bridge aboard, or the ground
  //: console. The ship's own card gives none and asks for no chart.
  const orders = atConsole || ground;
  useEffect(() => {
    if (!orders) return;
    void worldMap(session.token)
      .then((map) => setSky(map.nodes.filter((node) => node.orbit)))
      .catch(() => setSky([]));
  }, [orders, session]);

  const go = (what: () => Promise<unknown>) =>
    act(async () => {
      await what();
      await reload();
    });

  if (atConsole && !aboard) {
    return (
      <section>
        <h2>Консоль управления кораблём</h2>
        <p className="note">
          Консоль стоит на земле и молчит: она работает только в узле корабля —
          на основании, заложенном на космодроме из «узла космического корабля».
          Для приказов с земли есть другая вещь — «Наземная консоль управления».
        </p>
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
        {ground
          ? "Наземная консоль управления"
          : atConsole
            ? "Консоль управления кораблём"
            : aboard
              ? "Корабль"
              : "Космическая верфь"}
        <Rule>
          Корабль — не вещь, а группа узлов карты с одним выходом наружу. Швартовка и
          отход — появление и исчезновение одного ребра, а полёт это его отсутствие: с
          борта просто некуда сойти. Скорость выводится из тяги против массы, поэтому
          грузоподъёмности числом нет — перегруженный корабль остаётся в порту. Дорога
          идёт тремя ногами: подъём на околопланетную орбиту, переход с орбиты на
          орбиту, спуск на выбранный космодром. Курс задаётся на карте рубки: она
          показывает часы и топливо именно этого корпуса.
        </Rule>
      </h2>

      {commanded.map((v) => {
        //: A hull with no console of its own hears nothing from the ground
        //: (D-242). Said once, above, and every order greyed out with it --
        //: a refusal collected after the click says the same thing too late.
        const deaf = ground && !v.bridge;
        return (
        <div key={v.ship}>
          <p className="sign">
            {v.name} · {v.nodes} узл. · тяга {v.thrust.toFixed(0)} на массу {v.mass.toFixed(0)} кг
          </p>
          <AirBar v={v} />
          <p className="note">
            тяговооружённость {v.ratio.toFixed(2)} при нужных {v.min_ratio.toFixed(2)}
            {v.ratio < v.min_ratio && <b> · не отрывается</b>} · экипаж {v.crew} из{" "}
            {v.life_support} · топлива в баках {v.fuel.toFixed(0)}
            {v.stage === "orbit"
              ? ` · на околопланетной орбите ${planetName(v.planet)}`
              : v.docked
                ? ` · у верфи «${v.port}», место ${v.berth ?? "—"}`
                : v.flight
                  ? ` · в рейсе в «${v.flight.name}»`
                  : " · вне причала"}
          </p>
          {/* A passage is a term like any other, and every term in this world
              is drawn the same way. At the console the whole card stands
              instead (`Passage`); in the ship's own window the bar alone is
              what there is room for. */}
          {v.flight && !orders && (
            <Deadline until={v.flight.arrives_at} since={v.flight.started_at} label="рейс" />
          )}

          {orders ? (
            <>
              {/* A hull with no console of its own hears nothing from here: the
                  ground station talks to the bridge, and there is none. Said
                  before the buttons rather than as a refusal after them. */}
              {deaf && (
                <p className="reason">
                  Невозможно управлять. На борту нет «Консоли управления кораблём».
                </p>
              )}
              <Chart
                vessel={v}
                planets={sky}
                epoch={look.clock?.epoch ?? null}
                chosen={course}
                onChoose={setCourse}
              />
              {/* One stage, one set of orders (D-245). From the pad the
                  only move is up; under way the only move is back; from orbit
                  there are two, and the one wanted most often is down. */}
              {v.stage === "port" ? (
                <Ascent
                  vessel={v}
                  busy={busy || deaf}
                  ascend={() => go(() => session.send("ship.ascend", { ship: v.ship }))}
                />
              ) : v.stage === "flight" ? (
                <Passage
                  v={v}
                  busy={busy}
                  deaf={deaf}
                  recall={() => go(() => session.send("ship.recall", { ship: v.ship }))}
                />
              ) : (
                <>
                  <Landing
                    vessel={v}
                    busy={busy || deaf}
                    land={(port) => go(() => session.send("ship.land", { ship: v.ship, port }))}
                  />
                  <Course
                    vessel={v}
                    planet={course}
                    busy={busy || deaf}
                    fly={(port) => go(() => session.send("ship.fly", { ship: v.ship, port }))}
                  />
                </>
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
                  <p className="note">
                    Отстыковка и рейс отдаются от консоли управления: встаньте в отсек,
                    где она стоит. Без консоли на борту корабль никуда не летит.
                  </p>
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
          {keel.until && <Deadline until={keel.until} label="закладка" />}
          <span className="doing-aside note">
            основа списана, узел появится сам — стоять у верфи не нужно. Но руки
            заняты закладкой: до срока не выйдет ни спать, ни разведывать, ни
            встать к станции. Ходить можно.
          </span>
        </div>
      )}

      {!orders && aboard && !keel && (
        <button
          onClick={() => go(() => session.send("ship.extend"))}
          disabled={busy || !foundation || occupied !== null}
          title={occupied ?? undefined}
        >
          Заложить основание для космического корабля
        </button>
      )}

      {!orders && !aboard && atPort && !keel && (
        <>
          <input
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="Имя корабля"
          />
          <button
            onClick={() =>
              go(() => session.send("ship.found", { name: name || "Корабль" }))
            }
            disabled={busy || !foundation || occupied !== null}
            title={occupied ?? undefined}
          >
            Заложить основание для космического корабля
          </button>
        </>
      )}

      {!orders && !keel && occupied !== null && <p className="note">{occupied}</p>}

      {!orders && !keel && !foundation && (
        <p className="note">
          Нужна «{foundationName ?? "основа узла корабля"}» в руках — её делают в космической
          мастерской. Корабль растёт по узлу за раз: каждый следующий узел это и место, и
          лишняя масса.
        </p>
      )}
    </section>
  );
}
