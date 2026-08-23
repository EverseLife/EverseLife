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
 */

import { useCallback, useEffect, useState } from "react";
import { stationsOf, type Look, type Session, type RecipeBook } from "../api";
import { Rule } from "../Rule";
import { firstOfClass } from "../classes";
import { Refusal, useActions } from "../actions";

/**
 * Thing classes, not item names (D-215): the foundation a node aboard is laid
 * from, and the yard it is laid at. The engine asks by the same words
 * (`ship.FOUNDATION`, `ship.SPACEPORT`); the names come from the catalog.
 */
const FOUNDATION = "Основа корабля";
const SPACEPORT = "Верфь";
/** The node property that marks a node as being aboard: it arrives in `features`. */
const ABOARD = "борт";

type Route = {
  node: string;
  name: string;
  planet: string;
  class: number;
  hours: number | null;
  fuel: number | null;
  reachable: boolean;
};

type Vessel = {
  ship: string;
  name: string;
  nodes: number;
  mass: number;
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

export function Ship({ look, session, book }: { look: Look; session: Session; book: RecipeBook | null }) {
  //: This panel's own waiting and its own refusal: laying a keel must not grey
  //: out the chat and the map for eight hours of somebody else's work.
  const acting = useActions();
  const { busy, act } = acting;

  const [ships, setShips] = useState<Vessel[]>([]);
  const [name, setName] = useState("");

  const aboard = (look.node?.features ?? []).includes(ABOARD);
  const atPort = firstOfClass(book, stationsOf(look), SPACEPORT) !== undefined;
  const foundationName = firstOfClass(book, look.inventory.map((t) => t.goods), FOUNDATION);
  const foundation = look.inventory.find((t) => t.goods === foundationName);

  const reload = useCallback(async () => {
    const answer = await session.send("ship.view");
    setShips((answer.ships ?? []) as Vessel[]);
  }, [session]);

  useEffect(() => {
    void reload();
  }, [reload, look]);

  const go = (what: () => Promise<unknown>) =>
    act(async () => {
      await what();
      await reload();
    });

  if (!aboard && !atPort) return null;

  //: Aboard the summary is about the ship underfoot; at a port -- about the
  //: ones that are yours, because from the pier one commands one's own.
  const shown = aboard
    ? ships
    : ships.filter((v) => v.docked === look.node?.key || v.docked == null);

  return (
    <section>
      <Refusal of={acting} />
      <h2>
        {aboard ? "Корабль" : "Космическая верфь"}
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
            из {v.life_support} · топлива {v.fuel.toFixed(0)}
            {v.docked
              ? ` · у верфи «${v.port}», место ${v.berth ?? "—"}`
              : " · в полёте"}
          </p>

          {v.docked && (
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

          {!v.docked &&
            v.routes.map((route) => (
              <button
                key={route.node}
                onClick={() =>
                  go(() =>
                    session.send("ship.fly", { ship: v.ship, port: route.node }),
                  )
                }
                disabled={busy || !route.reachable}
                title={
                  route.reachable
                    ? undefined
                    : `нужен двигатель ${route.class} класса либо меньше массы`
                }
              >
                {route.name} · {route.hours?.toFixed(1)} ч ·{" "}
                {route.fuel?.toFixed(0)} топлива
                {!route.reachable && ` · класс ${route.class}`}
              </button>
            ))}
        </div>
      ))}

      {aboard && (
        <button
          onClick={() => go(() => session.send("ship.extend"))}
          disabled={busy || !foundation}
        >
          Заложить основание для космического корабля
        </button>
      )}

      {!aboard && atPort && (
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
            disabled={busy || !foundation}
          >
            Заложить основание для космического корабля
          </button>
        </>
      )}

      {!foundation && (
        <p className="note">
          Нужна «{foundationName ?? "основа узла корабля"}» в руках — её делают в космической мастерской. Корабль растёт по
          узлу за раз: каждый следующий узел это и место, и лишняя масса.
        </p>
      )}
    </section>
  );
}
