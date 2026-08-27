// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/**
 * The ship: a group of nodes, not a vehicle (D-201, D-202).
 *
 * There is no "ship screen" and there must not be one -- the ship **is** the
 * map: one walks aboard along an edge and around it as around a city. This
 * panel is only what the map cannot show by itself: how much thrust stands
 * against how much mass, and what that buys in hours and fuel on every route.
 *
 * The one thing it must never do is spring a refusal after the hold is loaded.
 * Thrust-to-mass, the floor it must clear and the price of every route are on
 * screen **before** the undocking, refusals included, with the reason named --
 * class or mass. A bare "unavailable" leaves nothing to act on.
 *
 * The **console** (D-230) is this panel opened at the bridge: the space map
 * on top, the ship's card under it -- the engines one by one, the mass split
 * into hull, machines and cargo, the speed that follows -- and the two orders
 * a ship takes, casting off and the passage. Elsewhere aboard the card is
 * read-only: the engine refuses an order given away from the console, and the
 * panel says so before the button is pressed.
 */

import { useCallback, useEffect, useState } from "react";
import { stationsOf, type Look } from "../api";
import { Deadline } from "../Deadline";
import { Rule } from "../Rule";
import { busyWith } from "../busy";
import { firstOfClass } from "../classes";
import { Refusal, useActions, useBook, useEdition, useSession } from "../actions";
import { planetName } from "../planets";
import { GraphMap } from "./GraphMap";

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

type Route = {
  node: string;
  name: string;
  planet: string;
  /**
   * What the ship is: the weakest engine aboard. Not a demand of the route --
   * no route makes one (D-235) -- but the number the fuel was computed with.
   */
  class: number | null;
  hours: number | null;
  fuel: number | null;
  /** Enough thrust to leave the ground at all. Class closes no route. */
  reachable: boolean;
  /**
   * The whole planet stands behind this row: it takes a landing anywhere on
   * its surface (D-233), and the node named here is only the one the console
   * happened to pick. There is no port to choose, so no picker is drawn --
   * until the map grows a "садиться сюда" gesture, the hull comes down where
   * the row says.
   */
  anywhere?: boolean;
};

type Engine = { name: string; count: number; thrust: number; class: number };

type Vessel = {
  ship: string;
  name: string;
  nodes: number;
  mass: number;
  /** Where the mass comes from: what to cut is read off this, not off the total (D-230). */
  mass_parts: { hull: number; machines: number; cargo: number };
  engines: Engine[];
  thrust: number;
  ratio: number;
  min_ratio: number;
  class: number | null;
  crew: number;
  life_support: number;
  fuel: number;
  docked: string | null;
  port: string | null;
  /** Which berth of that port: the gangway is as long as its number (D-201). */
  berth: number | null;
  connector: string | null;
  routes: Route[];
};

/**
 * Where to fly, by planet (D-230). A planet is one price -- the sky has one
 * distance to it -- and may have hundreds of ports (Aurora): one row per
 * planet with the port chosen in it, not a button per pier.
 *
 * A planet with no ports at all (Pyroxis) has nothing to choose: the node is
 * rolled at the landing, and the row says so instead of naming a pier that
 * will not be the one (D-233, D-235).
 */
function Routes({
  vessel,
  busy,
  fly,
}: {
  vessel: Vessel;
  busy: boolean;
  fly: (port: string) => void;
}) {
  const [chosen, setChosen] = useState<Record<string, string>>({});
  const planets = new Map<string, Route[]>();
  for (const route of vessel.routes) {
    planets.set(route.planet, [...(planets.get(route.planet) ?? []), route]);
  }
  return (
    <>
      {[...planets.entries()].map(([planet, routes]) => {
        const first = routes[0];
        const port = chosen[planet] ?? first.node;
        return (
          <p key={planet}>
            <b>{planetName(planet)}</b> · {first.hours?.toFixed(1)} ч ·{" "}
            {first.fuel?.toFixed(0)} топлива
            {!first.reachable && " · тяги не хватает: снимите массу"}{" "}
            {routes.length > 1 ? (
              <select
                value={port}
                onChange={(e) => setChosen((was) => ({ ...was, [planet]: e.target.value }))}
                aria-label={`космодром на планете ${planetName(planet)}`}
              >
                {routes.map((route) => (
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
              onClick={() => fly(port)}
              disabled={busy || !first.reachable}
              title={
                first.reachable
                  ? undefined
                  : "тяги не хватает, чтобы оторваться: снимите массу или добавьте двигатель"
              }
            >
              Лететь
            </button>
          </p>
        );
      })}
    </>
  );
}

/**
 * The ship's card (D-230): the engines one by one, the mass by where it comes
 * from, and the speed that follows. Three numbers the owner can act on
 * separately -- a node is cut by not laying it, a machine by taking it down,
 * cargo by unloading -- where one total would say nothing.
 */
function Card({ v }: { v: Vessel }) {
  const parts = v.mass_parts;
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
            корпус {parts.hull.toFixed(0)} кг · станции {parts.machines.toFixed(0)} кг ·
            груз {parts.cargo.toFixed(0)} кг
          </td>
        </tr>
        <tr>
          <td>скорость</td>
          <td className="note">
            {v.ratio.toFixed(2)} тяги на кг массы
            {v.class != null && ` · маршруты до ${v.class} класса`}
            {v.ratio < v.min_ratio && " · ниже порога отрыва"}
          </td>
        </tr>
      </tbody>
    </table>
  );
}

/**
 * The cheapest passage the ship could make from here.
 *
 * The engine refuses to undock without fuel for the way back, and that way
 * back is the cheapest passage there is -- so the cheapest route on the board
 * is the number to compare against. No routes to compare with (a single port
 * in the world): let the engine speak, and it names the figure in its refusal.
 */
function cheapest(v: Vessel): number {
  const fuels = v.routes
    .map((route) => route.fuel)
    .filter((fuel): fuel is number => fuel != null);
  return fuels.length ? Math.min(...fuels) : 0;
}

export function Ship({ look, console: atConsole = false }: { look: Look; console?: boolean }) {
  const session = useSession();
  const book = useBook();
  //: This panel's own waiting and its own refusal: laying a keel must not grey
  //: out the chat and the map for eight hours of somebody else's work.
  const acting = useActions();
  const { busy, act } = acting;

  const [ships, setShips] = useState<Vessel[]>([]);
  const [name, setName] = useState("");

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
    const answer = await session.send("ship.view");
    setShips((answer.ships ?? []) as Vessel[]);
  }, [session]);
  //: Reread when the world says so (D-226), not on every look.
  const edition = useEdition("ship.", "transport.");

  useEffect(() => {
    void reload();
  }, [reload, edition]);

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
          Поставьте её на борт, и здесь откроются карта космоса и рейс.
        </p>
      </section>
    );
  }
  if (!aboard && !atPort) return null;

  //: Aboard the summary is about the ship underfoot; at a port -- about the
  //: ones that are yours, because from the pier one commands one's own.
  const shown = aboard
    ? ships
    : ships.filter((v) => v.docked === look.node?.key || v.docked == null);

  return (
    <section>
      <Refusal of={acting} />
      {bridge && (
        //: The space layer at the bridge: where the ship is and where it may go.
        //: The map's own "enter" leads nowhere from here -- one is already aboard.
        <GraphMap look={look} onEnter={() => undefined} initialLayer="space" />
      )}
      <h2>
        {bridge ? "Консоль управления кораблём" : aboard ? "Корабль" : "Космическая верфь"}
        <Rule>
          Корабль — не вещь, а группа узлов карты с одним выходом наружу. Стыковка и
          отстыковка — появление и исчезновение одного ребра, а полёт это его
          отсутствие: с борта просто некуда сойти. Скорость выводится из тяги против
          массы, поэтому грузоподъёмности числом нет — перегруженный корабль остаётся в
          порту.
        </Rule>
      </h2>

      {shown.map((v) => (
        <div key={v.ship}>
          <p className="sign">
            {v.name} · {v.nodes} узл. · тяга {v.thrust.toFixed(0)} на массу{" "}
            {v.mass.toFixed(0)} кг
          </p>
          <p className="note">
            тяговооружённость {v.ratio.toFixed(2)} при нужных{" "}
            {v.min_ratio.toFixed(2)}
            {v.ratio < v.min_ratio && <b> · не отрывается</b>} · экипаж {v.crew}{" "}
            из {v.life_support} · топлива в баках {v.fuel.toFixed(0)}
            {v.docked
              ? ` · у верфи «${v.port}», место ${v.berth ?? "—"}`
              : " · в полёте"}
          </p>

          {aboard && <Card v={v} />}

          {aboard && !bridge && (
            <p className="note">
              Отстыковка и рейс отдаются от консоли управления: встаньте в отсек,
              где она стоит. Без консоли на борту корабль никуда не летит.
            </p>
          )}

          {bridge && v.docked && (
            <>
              <button
                onClick={() => go(() => session.send("ship.undock", { ship: v.ship }))}
                disabled={
                  busy ||
                  v.ratio < v.min_ratio ||
                  v.crew > v.life_support ||
                  v.fuel < cheapest(v)
                }
              >
                Отстыковаться
              </button>
              {v.fuel < cheapest(v) && (
                <p className="note">
                  Топлива меньше, чем нужно на один рейс. Отстыкованному кораблю
                  его не привезут: рёбер к нему нет, и с борта не сойти.
                </p>
              )}
            </>
          )}

          {bridge && !v.docked && (
            <Routes
              vessel={v}
              busy={busy}
              fly={(port) => go(() => session.send("ship.fly", { ship: v.ship, port }))}
            />
          )}
        </div>
      ))}

      {/* The keel itself: the eight hours between the foundation leaving the
          pocket and the node appearing. Without this the yard was silent for
          the whole of them. */}
      {keel && (
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

      {aboard && !keel && (
        <button
          onClick={() => go(() => session.send("ship.extend"))}
          disabled={busy || !foundation || occupied !== null}
          title={occupied ?? undefined}
        >
          Заложить основание для космического корабля
        </button>
      )}

      {!aboard && atPort && !keel && (
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

      {!keel && occupied !== null && <p className="note">{occupied}</p>}

      {!keel && !foundation && (
        <p className="note">
          Нужна «{foundationName ?? "основа узла корабля"}» в руках — её делают в космической мастерской. Корабль растёт по
          узлу за раз: каждый следующий узел это и место, и лишняя масса.
        </p>
      )}
    </section>
  );
}
