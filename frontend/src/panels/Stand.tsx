// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/**
 * The location as a place, not a stack of forms (D-050, 01-interaction-model).
 *
 * What was here before: every panel the node could produce, all expanded at
 * once, in the order they happened to be written in `App.tsx`. Measured on one
 * ordinary node -- seven sections, thirty-five explanations, and a coal station
 * with no fuel standing above the smelter the person had actually come for. On
 * a phone it was 2.4 screens of scrolling with the chat pushed off the end.
 *
 * What replaces it keeps the one good property untouched: **the objects come
 * from the node and nowhere else.** Put a furnace down and a furnace appears;
 * take it away and it is gone. There is no such thing as a "type of location"
 * in this code and there must never be -- the machine is what makes a place
 * what it is (D-106).
 *
 * What changes is how many are open at once, and what is legible while they are
 * closed:
 *
 * - a **closed row still says what is going on** -- "свободна · кач. 55",
 *   "партия · гвозди ×200" with its deadline bar, "занята · Тэрн", "нет
 *   топлива". Watching everything at once is the row's job; working is the
 *   open surface's job;
 * - **order comes from state**, not from source order: what is running first,
 *   then what is free, then what cannot be used and why, and the place itself last;
 * - **the node remembers what was open**. Walk into your own smithy and the
 *   forge is already there, as you left it;
 * - a **workbench opens in place**; a scene or a dense panel -- the face, the
 *   market, the administration -- takes the window whole and hides the row,
 *   because the vault asks the mining scene to suppress everything around it.
 */

import { useState, type ReactNode } from "react";
import { useBook } from "../actions";
import * as api from "../api";
import type { Look, RecipeBook } from "../api";
import { Deadline } from "../Deadline";
import { craftableAt } from "../recipes";
import { Admin } from "./Admin";
import { Farm } from "./Farm";
import { Forage } from "./Forage";
import { Kitchen } from "./Kitchen";
import { Library } from "./Library";
import { Market } from "./Market";
import { Mine } from "./Mine";
import { Mint } from "./Mint";
import { Nursery } from "./Nursery";
import { Convoy } from "./place/Convoy";
import { Gather } from "./place/Gather";
import { Ground } from "./place/Ground";
import { House } from "./place/House";
import { Plot } from "./place/Plot";
import { Reactor, reactorState } from "./place/Reactor";
import { disposes, gatherSigns, PLACES } from "./place/shared";
import { Plant } from "./Plant";
import { Rig } from "./Rig";
import { Ship } from "./Ship";
import { Workshop } from "./Workshop";
import type { PowSettings } from "../pow";
import { anyOfClass, classOf, firstOfClass } from "../classes";

/**
 * Thing classes the stand opens windows by (D-215): the terminal is the
 * market, the yard is the ship, the rig is drilling. Names of the machines
 * come from the catalog; the client knows only the class words, the same ones
 * the engine binds its behaviour to.
 */
const TERMINAL = "Терминал";
const SPACEPORT = "Верфь";
/** The ship's console: the bridge the ship is commanded from (D-230). */
const BRIDGE = "Рубка";
const RIG = "Буровая";
const KITCHEN = "Кухня";
const NURSERY = "Питомник";
const FUEL_PLANT = "Топливная станция";
const MINT = "Монетный двор";

type Kind =
  /** A workbench: opens in place, the rest of the node stays in view. */
  | "bench"
  /** A scene or a dense panel: takes the window and folds the row away. */
  | "full";

type Thing = {
  id: string;
  name: string;
  kind: Kind;
  /** Running first, then free, then refused, then the place itself. */
  rank: number;
  /** What it is doing, in the right-hand column of the row. */
  state?: ReactNode;
  /** Why it cannot be used -- shown on the row, never hidden in a `title`. */
  why?: ReactNode;
  /** A term to draw a deadline bar for. */
  running?: { until: string; since?: string | null };
  view: () => ReactNode;
};

/**
 * What was open, per node.
 *
 * Lives at module level for the same reason the map's layout does: the
 * component's life is shorter than the player's walk, and coming back to your
 * own yard should not mean choosing the forge again.
 */
const OPENED = new Map<string, string>();

/**
 * The place itself as a choice -- the row of objects with no window over it.
 *
 * Not an object's id: a node whose things are all full windows (an empty plot,
 * a square with one terminal) has no bench to fall back to, and without this
 * state "назад к месту" would lead back into the window it just left.
 */
const ROW = "row";

type Props = {
  look: Look;
  values: Record<string, any> | null;
  pow: PowSettings | null;
};

export function Stand({ look, values, pow }: Props) {
  const book = useBook();
  const here = look.node?.key ?? "";
  const [chosen, setChosen] = useState<string | null>(() => OPENED.get(here) ?? null);

  const things = assemble({ look, values, pow }, book);
  const bench = things.find((t) => t.kind === "bench");
  //: A remembered choice can vanish -- the machine was carried off while we
  //: were away. Then the first thing worth attention takes over.
  //: `ROW` is the one choice that may open nothing: where the node has no
  //: bench, the place is the row standing on its own.
  const open =
    chosen === ROW
      ? bench
      : (things.find((t) => t.id === chosen) ?? bench ?? things[0]);

  const show = (id: string) => {
    setChosen(id);
    if (here) OPENED.set(here, id);
  };

  if (things.length === 0) {
    return (
      <section>
        <h2>{look.node?.name}</h2>
        <p className="note">Здесь ничего не стоит — только дороги.</p>
      </section>
    );
  }

  //: A full-window surface suppresses the row: the vault asks exactly this of
  //: the mining scene, and a dense table has the same appetite for width.
  if (open && open.kind === "full") {
    return (
      <div className="stand">
        <div className="stand-back">
          <button type="button" className="quiet" onClick={() => show(ROW)}>
            ← назад к месту
          </button>
          <span className="note">{look.node?.name}</span>
        </div>
        <div className="stand-open">{open.view()}</div>
      </div>
    );
  }

  return (
    <div className="stand">
      <div className="objects">
        {things.map((thing) => (
          <button
            key={thing.id}
            type="button"
            className={`obj bare${thing.why ? " off" : ""}`}
            aria-pressed={thing.id === open?.id}
            onClick={() => show(thing.id)}
          >
            <span className="obj-name">{thing.name}</span>
            {thing.state && <span className="obj-state">{thing.state}</span>}
            {thing.why && <span className="obj-why">{thing.why}</span>}
            {thing.running && (
              <span className="obj-bar">
                <Deadline
                  until={thing.running.until}
                  since={thing.running.since}
                  label={thing.name}
                  size="row"
                />
              </span>
            )}
          </button>
        ))}
      </div>
      {open && <div className="stand-open">{open.view()}</div>}
    </div>
  );
}

/**
 * The node's objects, in the order they deserve attention.
 *
 * Every entry is conditional on something in `look`: this is the list the old
 * screen built too, only now it is one list instead of a dozen independent
 * `{has.x && <Panel/>}` lines, and each entry carries what it is doing.
 */
function assemble({ look, values, pow }: Props, book: RecipeBook | null): Thing[] {
  const things: Thing[] = [];
  const stations = api.stationsOf(look);
  const bench = look.bench ?? [];
  const batches = look.batches ?? [];

  /** The batch occupying this machine, if it is ours -- one under way, not one waiting (D-209). */
  const batchAt = (machine: string) =>
    batches.find((b) => b.station === machine && b.state === "running");
  /** The machine as it stands in the node: quality, condition, who is at it. */
  const standing = (machine: string) => bench.find((b) => b.goods === machine);

  const machineState = (machine: string) => {
    const it = standing(machine);
    if (!it) return undefined;
    const parts: string[] = [];
    if (it.busy) parts.push(it.mine ? "занята вами" : "занята");
    else parts.push("свободна");
    if (it.quality != null) parts.push(`кач. ${it.quality.toFixed(0)}`);
    if (it.condition < 100) parts.push(`сост. ${it.condition.toFixed(0)}`);
    return parts.join(" · ");
  };

  //: The face is a scene: it wants the window and the attention (D-143).
  if (look.veins?.length) {
    const open = look.mining;
    things.push({
      id: "mine",
      name: "Забой",
      kind: "full",
      rank: open ? 0 : 1,
      state: open ? "сессия идёт" : `жила: ${look.veins[0].resource}`,
      view: () => <Mine look={look} pow={pow} />,
    });
  }

  /**
   * Machines that have a surface of their own beyond ordinary craft: the pot
   * on the hearth, breeding in the nursery, minting, the firebox of a coal
   * station. Keyed by thing class, not by machine name (D-215): a second
   * hearth or a renamed mint opens the same window.
   */
  const SPECIAL: Record<string, () => ReactNode> = {
    [KITCHEN]: () => <Kitchen look={look} />,
    [NURSERY]: () => <Nursery look={look} />,
    [FUEL_PLANT]: () => <Plant look={look} />,
    [MINT]: () => <Mint look={look} values={values} />,
  };

  //: One machine is one row, whatever can be done at it. A hearth is both a
  //: pot and an ordinary bench, and listing it twice would say there are two
  //: hearths in the yard. The surface shows everything the machine offers.
  //:
  //: A row exists for every machine one can work at, known recipes or not:
  //: since D-209 a machine is also where one tries to make something without a
  //: recipe, and that is exactly the case with nothing on the list yet.
  //: Furniture, the terminal, the library and the like have windows of their
  //: own and are not benches.
  const workable = (machine: string) =>
    (book?.recipes ?? []).some(
      (r) => r.station !== undefined && (book?.synonyms?.[r.station] ?? r.station) === machine,
    ) || (book?.operations ?? []).some((o) =>
      (o.requires ?? []).some((w: string) => (book?.synonyms?.[w] ?? w) === machine),
    );
  for (const machine of stations) {
    const thingClass = classOf(book, machine);
    const special = thingClass === null ? undefined : SPECIAL[thingClass];
    const recipes = craftableAt(book, machine, look.knows).length > 0 || workable(machine);
    if (!special && !recipes) continue;
    const batch = batchAt(machine);
    things.push({
      id: `bench:${machine}`,
      name: machine,
      kind: "bench",
      rank: batch ? 0 : 1,
      state: batch ? `партия · ${batch.output}` : machineState(machine),
      running:
        batch && batch.ready_at ? { until: batch.ready_at, since: batch.started_at } : undefined,
      view: () => (
        <>
          {special?.()}
          {recipes && (
            <Workshop machine={machine} look={look} />
          )}
        </>
      ),
    });
  }

  const single = (
    id: string,
    name: string,
    kind: Kind,
    view: () => ReactNode,
    rank = 1,
    state?: ReactNode,
  ) => things.push({ id, name, kind, rank, state, view });

  //: A forest is as much a thing to work at as a furnace: extraction by the
  //: sign of the land stands next to the machines, one row per sign (D-177).
  for (const sign of gatherSigns(look, book)) {
    single(`gather:${sign}`, PLACES[sign] ?? sign, "bench", () => (
      <Gather look={look} sign={sign} />
    ));
  }

  //: A rig, only where there is one to work with: standing in the node, or in
  //: the hands waiting to be placed on a vein. The panel used to keep itself
  //: silent while the row above it did not, so "Буровая" stood in every
  //: location in the world -- including those with nothing to drill.
  const rigInHand = anyOfClass(book, look.inventory.map((t) => t.goods), RIG);
  if (look.rig_here || rigInHand) {
    single(
      "rig",
      "Буровая",
      "bench",
      () => <Rig look={look} />,
      1,
      look.rig_here ? undefined : "в руках: поставить на жилу",
    );
  }

  //: The ship, where there is one to command: standing at a spaceport or
  //: aboard. Aboard the row is the ship itself -- the map is already showing
  //: its rooms, and this panel adds what the map cannot: thrust against mass.
  const aboard = (look.node?.features ?? []).includes("борт");
  const yard = firstOfClass(book, stations, SPACEPORT);
  //: The console (D-230): the window of the bridge -- the space map, the
  //: ship's card, casting off and the passage. It answers only aboard; on the
  //: ground it stands as furniture, and the row says so instead of hiding.
  const bridge = firstOfClass(book, stations, BRIDGE);
  if (bridge !== undefined) {
    single(
      "console",
      bridge,
      "full",
      () => <Ship look={look} console />,
      aboard ? 0 : 2,
      aboard ? undefined : "работает только на борту корабля",
    );
  }
  if (bridge === undefined && (yard !== undefined || aboard)) {
    //: Aboard the window is the ship, on the ground it is the yard the ship is
    //: laid down and moored at: one panel, and its name says which of the two
    //: the player is looking at.
    single(
      "ship",
      aboard ? "Корабль" : yard ?? SPACEPORT,
      "full",
      () => <Ship look={look} />,
    );
  }

  //: Farming appears **with the first strip**, not with fertile ground: a
  //: strip is marked out in the land window ("Земля"), and the cycle that
  //: follows -- ploughing, sowing, the daily round, the harvest -- is a place
  //: of its own that has nowhere to happen until there is a strip. Empty, this
  //: window offered one button and looked like a mechanic nobody had started.
  //:
  //: Plots are the holder's business: on somebody else's land one farms by
  //: contract, not by this window (06-farming). Nobody's land outside a city is
  //: farmed by whoever comes (D-198), and there the window is for everyone.
  const strips = look.node?.plots ?? 0;
  if (strips > 0 && disposes(look)) {
    single(
      "farm",
      "Земледелие",
      "full",
      () => <Farm look={look} />,
      3,
      strips === 1 ? "одна делянка" : `делянок: ${strips}`,
    );
  }
  //: Foraging, where the land has room to walk and is ours or nobody's
  //: (D-210): the server decides, the row only reads. A find waiting for its
  //: decision wants attention first; a search under way carries its bar.
  const foraging = look.forage;
  if (foraging) {
    const searching = foraging.state === "searching" && foraging.ready_at;
    things.push({
      id: "forage",
      name: "Собирательство",
      kind: "full",
      rank: foraging.state === "idle" ? 2 : 0,
      state:
        foraging.state === "found" && foraging.found
          ? `нашлось: ${foraging.found.goods} ×${foraging.found.units}`
          : searching
            ? "идёт поиск"
            : `${foraging.area.toFixed(0)} м² пустой земли`,
      running: searching
        ? { until: foraging.ready_at as string, since: foraging.started_at }
        : undefined,
      view: () => <Forage look={look} />,
    });
  }
  //: A library and a hall are machines (D-176, D-215): both are read off the bench.
  if (anyOfClass(book, stations, "Библиотека")) {
    single("library", "Библиотека", "full", () => <Library look={look} />);
  }
  if (look.city && anyOfClass(book, stations, "Администрация")) {
    single("hall", "Администрация", "full", () => <Admin look={look} />);
  }
  const terminal = firstOfClass(book, stations, TERMINAL);
  if (terminal !== undefined) {
    const mine = (look.stall ?? []).length;
    single("market", terminal, "full",
      () => <Market look={look} values={values} />, 1,
      mine > 0 ? `вашего товара: ${mine}` : undefined);
  }

  //: The location's own windows, last and in one group: they are about the place
  //: rather than about work, and a machine deserves attention before a nameplate.
  //: Each is one intention -- storage, hauling, building, the land itself --
  //: instead of one "Место" holding whatever was left over.
  const own = disposes(look);
  const node = look.node;
  const home = api.houseOf(node);

  //: Storage of the place, for everyone (D-192, D-204): the floor and the
  //: chests answer one question -- "where do my things go here".
  const room = look.floor?.space;
  if (look.floor || (look.storages ?? []).length > 0) {
    single(
      "ground",
      (room?.roofed ?? 0) > 0 ? "В здании" : "На земле",
      "full",
      () => <Ground look={look} />,
      3,
      room ? `${room.used.toFixed(0)} / ${room.area.toFixed(0)} м²` : undefined,
    );
  }

  //: The wagon is an object of the node like any machine, only one harnesses
  //: to it instead of working at it (D-157).
  const convoy = look.convoy ?? null;
  const carts = (look.vehicles ?? []).filter((t) => !t.taken);
  if (convoy || carts.length > 0) {
    single(
      "convoy",
      "Обоз",
      "full",
      () => <Convoy look={look} />,
      3,
      convoy
        ? `трюм ${convoy.mass.toFixed(0)} из ${convoy.capacity.toFixed(0)} кг`
        : `стоит: ${carts[0].goods}`,
    );
  }

  //: The house is the holder's, and one window covers its whole story: build,
  //: demolish, and place the machines and furniture into it (D-106, D-205).
  if (own && node) {
    single(
      "house",
      "Дом",
      "full",
      () => <House look={look} />,
      3,
      home.area > 0
        ? `${home.area.toFixed(0)} м² в ${home.floors} эт. · мест ${home.used} из ${home.slots}`
          + (home.condition == null ? "" : ` · состояние ${home.condition.toFixed(0)}%`)
        : home.sites.length > 0
          ? "строится"
          : "не построен",
    );
  }

  //: The Forerunners' reactor: it feeds the city and it is running out (D-232).
  //: Its own thing of the place rather than a line under a machine, because the
  //: day it goes silent is the day the city must already be standing on its own
  //: coal -- and that day has to be seen from far off.
  if (node?.reactor_until) {
    single(
      "reactor",
      "Реактор Предтеч",
      "bench",
      () => <Reactor look={look} />,
      2,
      reactorState(node.reactor_until),
    );
  }

  //: The land itself: whose, the name, the door, the purchase and the founding
  //: of a city. Shown to guests too -- ownership is a public fact (D-178) --
  //: and an empty plot for sale is the main thing of its node, hence the rank.
  const forSale = Boolean(node && !node.owner && (api.isWild(node) || node.price !== undefined));
  const owned = Boolean(node?.owner || node?.owner_city);
  if (forSale || owned) {
    single(
      "plot",
      "Земля",
      "full",
      () => <Plot look={look} />,
      forSale ? 1 : 3,
      node?.cut_off
        ? "отключена за неуплату"
        : node?.gated
          ? "вход закрыт"
          : forSale
            ? node?.price !== undefined
              ? `продаётся за ${api.tk(node.price)} ₭`
              : "ничья земля"
            : api.isMine(look)
              ? undefined
              : node?.owner
                ? `хозяин ${node.owner}`
                : `город ${node?.owner_city}`,
    );
  }

  return things.sort((a, b) => a.rank - b.rank);
}
