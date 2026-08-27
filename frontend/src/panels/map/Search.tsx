// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

import { useEffect, useState } from "react";
import type { Look, Outlook } from "../../api";
import { Deadline } from "../../Deadline";
import { Hint } from "../../Hint";
import { useSession } from "../../actions";
import { LAYERS, type LayerId } from "./model";
import { long, price, spread } from "./words";

/**
 * Which map each goal grows.
 *
 * A plot and a room of the Forerunners appear in the built-up area, a new
 * place, a vein and a grove on the planet's surface -- that is where the engine
 * lays them (`Layer.CITY` and `Layer.PLANET` in `engine.explore`,
 * `engine.ruins`). So the button stands on the map it is about to change:
 * press "искать участок" looking at the city and the plot shows up in front of
 * you, rather than behind a tab you are not on.
 *
 * **This narrows nothing.** What may be sought where you stand stays the
 * server's answer alone (D-206, D-232) -- from inside a city the open world is
 * still open, and the note below says which map to open for it. The layer only
 * lays the allowed goals out by the place they belong to, and can never add
 * one the engine did not name.
 */
const GOAL_LAYER: Record<string, LayerId> = {
  lot: "city",
  room: "city",
  site: "planet",
  vein: "planet",
  forest: "planet",
};

/**
 * A goal nobody here has heard of belongs to the map now open.
 *
 * The engine's list of goals grows (`explore.GOALS`), and a client that did not
 * know a new one would drop its button off every layer at once -- the search
 * would look taken away rather than moved. Shown where the player is standing
 * is wrong at worst by a tab; invisible is wrong outright.
 */
const layerOf = (goal: string, here: LayerId): LayerId => GOAL_LAYER[goal] ?? here;

/** The goal in the player's own words, for the note that sends them to the right map. */
const GOAL_WORD: Record<string, string> = {
  lot: "участок",
  room: "помещения Предтеч",
  site: "новое место",
  vein: "жилу",
  forest: "лес",
};

/** Exploration from the map: **the server says what may be sought here** (D-152, D-232).
 *
 * The scout **leaves in person**: while the run goes, the body is in the field
 * and unavailable, as in sleep. Returning early is allowed -- the find then does not happen.
 *
 * The run's price is a property of the place (D-156): in untrodden surroundings
 * it is minutes and an almost certain find, in trodden ones hours and a roll.
 * The forecast is shown before leaving and updates on node change. */
export function Search({
  look,
  busy,
  act,
  layer,
}: {
  look: Look;
  busy: boolean;
  act: (what: () => Promise<unknown>) => Promise<void>;
  layer: LayerId;
}) {
  const session = useSession();
  const [speciesList, setSpeciesList] = useState<string[]>([]);
  const [species, setSpecies] = useState("");
  const [forecast, setForecast] = useState<Outlook | null>(null);
  //: Отдельный прогноз для леса: он сужает шанс на лесистость мира (D-191).
  const [woods, setWoods] = useState<Outlook | null>(null);
  //: What may be sought here at all is the server's answer (D-232). The map
  //: layer no longer decides it: inside a city of the Forerunners one opens
  //: their rooms, and offering "a lot" there would be promising a refusal.
  const [here, setHere] = useState<string[]>([]);
  const run = look.survey ?? null;
  //: What is possible here **and** belongs to the map now open, and what is
  //: possible here but belongs to another one. Both come out of the server's
  //: one answer: the second becomes a line saying where to look for it.
  const goals = here.filter((goal) => layerOf(goal, layer) === layer);
  const elsewhere = here.filter((goal) => layerOf(goal, layer) !== layer);
  //: The forecast is asked for the goal this map actually offers -- in a
  //: worked-out city of the Forerunners that is their rooms, and a chance shown
  //: for another search would be a lie (D-156, D-232). Kept as a string rather
  //: than as an expression over `here` in the dependencies: the effect must run
  //: again exactly once, when the server's answer changes what is on offer.
  const aim = goals[0] ?? "";
  //: A species asked for belongs to the place it was asked for in: carried into
  //: a city of the Forerunners it would put a vein's forecast under a button
  //: that opens rooms. Cleared with the node, so the choice is always about
  //: where one is standing.
  useEffect(() => setSpecies(""), [look.node?.key]);

  useEffect(() => {
    void session
      //: Прогноз просится под выбранную породу: редкая ищется хуже частой
      //: (D-151), и «шанс 90%» рядом с заказом золота был бы обманом.
      //: The forecast is asked for the goal this node actually offers: in a
      //: worked-out city of the Forerunners the honest answer is "never", and a
      //: promise made for another goal would be a lie (D-156, D-232).
      .send(
        "explore.goals",
        species ? { goal: "vein", resource: species } : aim ? { goal: aim } : {},
      )
      .then((answer) => {
        setSpeciesList((answer.resources as string[]) ?? []);
        setForecast((answer.outlook as Outlook | null) ?? null);
        setHere((answer.here as string[]) ?? []);
      })
      .catch(() => {
        setSpeciesList([]);
        setForecast(null);
        setHere([]);
      });
    //: Лес сужает шанс на лесистость мира (D-191), и это должно быть видно
    //: до выхода — как и с редкой породой.
    void session
      .send("explore.goals", { goal: "forest" })
      .then((answer) => setWoods((answer.outlook as Outlook | null) ?? null))
      .catch(() => setWoods(null));
    //: Заход меняет счёт находок узла, поэтому прогноз пересчитывается и по
    //: возвращении разведчика, а не только при переходе.
    //: The layer is not in the dependencies: the server's answer does not
    //: depend on it, and `aim` already changes when the layer changes what is
    //: on offer. Listing it as well would send two more `explore.goals` on
    //: every click of a map tab.
  }, [session, look.node?.key, run?.returns_at, species, aim]);

  if (!run && here.length === 0) return null;

  const seek = (goal: string, resource?: string) =>
    act(() => session.send("explore.survey", { goal, resource }));

  /** The layer in the player's words, for the note that names another map. */
  const mapWord = (one: LayerId) => LAYERS.find((option) => option.id === one)?.label ?? one;

  return (
    <div className="row search">
      {run ? (
        <>
          <span className="note">
            вы в разведке · вернётесь <Deadline until={run.returns_at} label="разведка" />
          </span>
          <button
            onClick={() => act(() => session.send("explore.cancel"))}
            disabled={busy}
          >
            Вернуться сейчас
          </button>
          <Hint>
            Повернуть назад можно в любой момент: находки не будет, потраченные
            силы не вернутся.
          </Hint>
        </>
      ) : (
        <>
          {/* One button per goal the server named for this place **and** for
              this map: a pier of the Forerunners offers both their rooms and
              the ice beyond it, and a city offers a lot beside the open world
              (D-206, D-232) -- the rooms and the plot on the built-up map, the
              ice and the field on the planet's, because that is where each of
              them appears. Anything the server did not name would be a promised
              refusal; anything named but belonging to the other map is a line
              below rather than a button missing without explanation. */}
          {goals.includes("room") && (
            <>
              <button onClick={() => seek("room")} disabled={busy}>
                Вскрыть следующее помещение
              </button>
              <Hint>
                Город Предтеч стоял здесь до вас: разведка не создаёт места, а
                открывает следующую дверь. Помещение приходит сразу с
                содержимым, и чем глубже от космодрома, тем оно богаче. Город
                конечен: чем больше вскрыто, тем чаще заход возвращается ни с
                чем, а потом и вовсе не с чем.
              </Hint>
            </>
          )}
          {goals.includes("lot") && (
            <>
              <button onClick={() => seek("lot")} disabled={busy}>
                Уйти искать участок
              </button>
              <Hint>
                Найденный участок встанет городской землёй: её выкупают у города
. Разведчик уходит сам, до возвращения недоступен, как во
                сне, и остаётся на находке.
              </Hint>
            </>
          )}
          {goals.includes("site") && (
            <button onClick={() => seek("site")} disabled={busy}>
              Уйти искать новое место
            </button>
          )}
          {goals.includes("vein") && (
            <>
              <button onClick={() => seek("vein", species || undefined)} disabled={busy}>
                Уйти искать жилу
              </button>
              <select value={species} onChange={(e) => setSpecies(e.target.value)}>
                <option value="">любую породу</option>
                {speciesList.map((name) => (
                  <option key={name}>{name}</option>
                ))}
              </select>
            </>
          )}
          {/* Woods are sought the way a vein is: a forest is a property of the
              place, and felling reads the same property (D-177, D-191). */}
          {goals.includes("forest") && (
            <button
              onClick={() => seek("forest")}
              disabled={busy}
              title={
                woods
                  ? `шанс ${woods.chance >= 1 ? Math.round(woods.chance) : woods.chance.toFixed(1)}%: лес ищется дольше прочего`
                  : "рубить древесину можно там, где лес"
              }
            >
              Уйти искать лес
            </button>
          )}
          {/* What this place offers but another map does. The button is not
              lost, it stands on the map it changes -- and this says which,
              because a button that vanished without a word reads as a
              mechanic that was taken away. */}
          {elsewhere.length > 0 && (
            <span className="note">
              отсюда ищут и{" "}
              {/* Goals grouped by the map each belongs to: from space both the
                  built-up map and the surface are "another layer", and naming
                  only the first one's layer would send the reader to a map
                  where half the buttons are not. */}
              {[...new Set(elsewhere.map((goal) => layerOf(goal, layer)))]
                .map(
                  (one) =>
                    `${elsewhere
                      .filter((goal) => layerOf(goal, layer) === one)
                      .map((goal) => GOAL_WORD[goal] ?? goal)
                      .join(", ")} — на слое «${mapWord(one)}»`,
                )
                .join(" · ")}
            </span>
          )}
          {goals.length > 0 && (
            <Hint>
              Разведчик уходит сам и до возвращения недоступен, как во сне.
              Кончатся силы — доспит в поле и продолжит. Нашёл — там и остаётся
; чем дальше от города находка, тем длиннее к ней дорога
. Лес попадается и сам собой, но заказанный ищется дольше. Кнопка
              стоит на том слое, куда ляжет находка: участок и помещение — в
              застройке, место, жила и лес — на поверхности планеты.
            </Hint>
          )}
        </>
      )}
      {!run && goals.length > 0 && forecast && <Forecast forecast={forecast} />}
    </div>
  );
}

/** What a run from here will cost (D-156). */
function Forecast({ forecast }: { forecast: Outlook }) {
  const { min, max } = forecast.minutes;
  const term = min === max ? long(min) : spread(min, max);
  //: The chance may be a fraction of a percent -- rounding to an integer would
  //: show zero where searching is still possible.
  const chance = forecast.chance >= 1
    ? Math.round(forecast.chance)
    : forecast.chance.toFixed(1);
  return (
    <span className="note">
      заход отсюда: {term} · шанс {chance}% · до {price(forecast.stamina)}{" "}
      выносливости
      {forecast.resource && (forecast.aim ?? 1) < 1 &&
        ` · ${forecast.resource.toLowerCase()} редка: шанс уже в ${(1 / (forecast.aim ?? 1)).toFixed(0)} раз`}
      {forecast.explored > 0 &&
        ` · окрестность исхожена: находок отсюда ${forecast.explored}`}
      {/* Теснота — свойство места, на которое игрок может ответить: отойти от
          скопления и идти от границы. Потому она названа отдельным числом, а не
          спрятана в общем шансе (D-207). */}
      {(forecast.crowding ?? 1) < 1 &&
        ` · тесно${forecast.anchor ? ` у ${forecast.anchor.toLowerCase()}` : ""}: шанс уже в ${(1 / (forecast.crowding ?? 1)).toFixed(1)} раз`}
    </span>
  );
}
