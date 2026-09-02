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
import { useBook, useNames } from "../actions";
import { t } from "../locale";
import { className, goodsName, propertyName, type Names } from "../names";
import * as api from "../api";
import type { Look, RecipeBook } from "../api";
import { Deadline } from "../Deadline";
import { Hint } from "../Hint";
import { craftableAt } from "../recipes";
import { Admin } from "./Admin";
import { Farm } from "./Farm";
import { Forage } from "./Forage";
import { Factory } from "./Factory";
import { Kitchen } from "./Kitchen";
import { Library } from "./Library";
import { Market } from "./Market";
import { Mine } from "./Mine";
import { Mint } from "./Mint";
import { Nursery } from "./Nursery";
import { Berth } from "./place/Berth";
import { Convoy } from "./place/Convoy";
import { Gather } from "./place/Gather";
import { Ground } from "./place/Ground";
import { House } from "./place/House";
import { Plot } from "./place/Plot";
import { Reactor, reactorState } from "./place/Reactor";
import { disposes, gatherSigns, PLACES } from "./place/shared";
import { Storey } from "./place/Storey";
import { Plant } from "./Plant";
import { Rig } from "./Rig";
import { Ship } from "./Ship";
import { Workshop } from "./Workshop";
import type { PowSettings } from "../pow";
import { TERMINAL, anyOfClass, classOf, firstOfClass } from "../classes";

/**
 * Thing classes the stand opens windows by (D-215): the terminal is the
 * market, the yard is the ship, the rig is drilling. Names of the machines
 * come from the catalog; the client knows only the class words, the same ones
 * the engine binds its behaviour to.
 */
const SPACEPORT = "shipyard";
/** The ship's console: the bridge the ship is commanded from (D-230). */
const BRIDGE = "bridge";
/** The same console on the ground (D-242): one's own hulls, wherever they are. */
const GROUND_BRIDGE = "ground_bridge";
const RIG = "rig";
const KITCHEN = "kitchen";
const AUTOMATON = "automaton";
const NURSERY = "nursery";
const FUEL_PLANT = "fuel_plant";
const MINT = "mint";

type Kind =
  /** A workbench: opens in place, the rest of the node stays in view. */
  | "bench"
  /** A scene or a dense panel; since D-238 it opens under the tiles like
   *  everything else -- the word only ranks what deserves the window first. */
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
  /** What the window is for -- the tile's "?" hint (D-238). */
  about?: string;
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
 * The place itself as a choice: the tiles standing alone, with no window
 * open under them.
 *
 * Not an object's id, because it is not an object: it is the state a click on
 * the open tile folds back into (D-238). Without it a node whose things are
 * all windows would have nothing to close into.
 */
const ROW = "row";

type Props = {
  look: Look;
  values: Record<string, any> | null;
  pow: PowSettings | null;
};

export function Stand({ look, values, pow }: Props) {
  const book = useBook();
  const names = useNames();
  const here = look.node?.key ?? "";
  const [chosen, setChosen] = useState<string | null>(() => OPENED.get(here) ?? null);

  const things = assemble({ look, values, pow }, book, names);
  const bench = things.find((t) => t.kind === "bench");
  //: A remembered choice can vanish -- the machine was carried off while we
  //: were away. Then the first thing worth attention takes over.
  //: `ROW` is the closed state: the tiles stand on their own, no window under
  //: them -- clicking the open tile again folds its window away (D-238).
  const open =
    chosen === ROW
      ? undefined
      : (things.find((t) => t.id === chosen) ?? bench ?? things[0]);

  const show = (id: string) => {
    setChosen(id);
    if (here) OPENED.set(here, id);
  };

  if (things.length === 0) {
    return (
      <section>
        <h2>{look.node?.name}</h2>
        <p className="note">{t("ui-stand-nothing")}</p>
      </section>
    );
  }

  //: One rule for every tile (D-238): the window opens under the tiles, the
  //: tiles never leave the screen, and the open tile folds its window away
  //: when clicked again. No full-window takeover, no "назад к месту".
  return (
    <div className="stand">
      <div className="objects">
        {things.map((thing) => (
          <div className="obj-wrap" key={thing.id}>
            <button
              type="button"
              className={`obj bare${thing.why ? " off" : ""}`}
              aria-pressed={thing.id === open?.id}
              onClick={() => show(thing.id === open?.id ? ROW : thing.id)}
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
            {/* The "?" beside the tile, not inside it: a button may not nest
                a button, and the hint must not open the window. */}
            {thing.about && (
              <span className="obj-hint">
                <Hint>{thing.about}</Hint>
              </span>
            )}
          </div>
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
function assemble(
  { look, values, pow }: Props,
  book: RecipeBook | null,
  names: Names | null,
): Thing[] {
  const things: Thing[] = [];
  //: With the names: the tiles are laid out for the player, and within one
  //: rank they should read in Russian order, not in the ASCII of the ids.
  const stations = api.stationsOf(look, names);
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
    if (it.busy) parts.push(it.mine ? t("ui-stand-busy-mine") : t("ui-stand-busy"));
    else parts.push(t("ui-stand-free"));
    if (it.quality != null) {
      parts.push(t("ui-stand-quality", { quality: it.quality.toFixed(0) }));
    }
    if (it.condition < 100) {
      parts.push(t("ui-stand-condition", { condition: it.condition.toFixed(0) }));
    }
    return parts.join(" · ");
  };

  //: The face is a scene: it wants the window and the attention (D-143).
  if (look.veins?.length) {
    const open = look.mining;
    things.push({
      id: "mine",
      name: t("ui-stand-mine"),
      kind: "full",
      rank: open ? 0 : 1,
      state: open
        ? t("ui-stand-mine-going")
        : t("ui-stand-mine-vein", { goods: goodsName(names, look.veins[0].resource) }),
      about: t("ui-stand-mine-about"),
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
    [AUTOMATON]: () => <Factory look={look} values={values} />,
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
  //: One factory floor, however many automat kinds stand here: the window
  //: is about the whole floor, and three tiles must not mount three of it.
  let factoryShown = false;
  for (const machine of stations) {
    const thingClass = classOf(book, machine);
    let special = thingClass === null ? undefined : SPECIAL[thingClass];
    if (thingClass === AUTOMATON) {
      if (factoryShown) special = undefined;
      factoryShown = true;
    }
    const recipes = craftableAt(book, machine, look.knows).length > 0 || workable(machine);
    if (!special && !recipes) continue;
    //: The tile's hint: what the window is for, in one line. The special
    //: classes add their trade -- the hearth cooks, the mint strikes coin.
    const TRADES: Record<string, string> = {
      [KITCHEN]: "ui-stand-trade-kitchen",
      [AUTOMATON]: "ui-stand-trade-factory",
      [NURSERY]: "ui-stand-trade-nursery",
      [FUEL_PLANT]: "ui-stand-trade-fuel-plant",
      [MINT]: "ui-stand-trade-mint",
    };
    const batch = batchAt(machine);
    things.push({
      id: `bench:${machine}`,
      name: goodsName(names, machine),
      kind: "bench",
      rank: batch ? 0 : 1,
      state: batch
        ? t("ui-stand-batch", { goods: goodsName(names, batch.output) })
        : machineState(machine),
      running:
        batch && batch.ready_at ? { until: batch.ready_at, since: batch.started_at } : undefined,
      about:
        t("ui-stand-bench-about", { machine: goodsName(names, machine) }) +
        (thingClass && TRADES[thingClass] ? t(TRADES[thingClass]) : ""),
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
    about?: string,
  ) => things.push({ id, name, kind, rank, state, about, view });

  //: A forest is as much a thing to work at as a furnace: extraction by the
  //: sign of the land stands next to the machines, one row per sign (D-177).
  for (const sign of gatherSigns(look, book)) {
    single(
      `gather:${sign}`,
      PLACES[sign] ? t(PLACES[sign]) : propertyName(names, sign),
      "bench",
      () => <Gather look={look} sign={sign} />,
      1,
      undefined,
      t("ui-stand-gather-about"),
    );
  }

  //: A rig, only where there is one to work with: standing in the node, or in
  //: the hands waiting to be placed on a vein. The panel used to keep itself
  //: silent while the row above it did not, so "Буровая" stood in every
  //: location in the world -- including those with nothing to drill.
  const rigInHand = anyOfClass(book, look.inventory.map((t) => t.goods), RIG);
  if (look.rig_here || rigInHand) {
    single(
      "rig",
      t("ui-stand-rig"),
      "bench",
      () => <Rig look={look} />,
      1,
      look.rig_here ? undefined : t("ui-stand-rig-in-hands"),
      t("ui-stand-rig-about"),
    );
  }

  //: The ship, where there is one to command: standing at a spaceport or
  //: aboard. Aboard the row is the ship itself -- the map is already showing
  //: its rooms, and this panel adds what the map cannot: thrust against mass.
  const aboard = (look.node?.features ?? []).includes("aboard");
  //: Which floor of a house one stands on (D-247). Upstairs there is no land,
  //: so the two windows about land have nothing to answer and one window
  //: answers instead -- the same shape a compartment aboard has.
  const storey = look.node?.storey ?? null;
  const yard = firstOfClass(book, stations, SPACEPORT);
  //: The console (D-230): the window of the bridge -- the space map, the
  //: ship's card, casting off and the passage. It answers only aboard; on the
  //: ground it stands as furniture, and the row says so instead of hiding.
  const bridge = firstOfClass(book, stations, BRIDGE);
  if (bridge !== undefined) {
    single(
      "console",
      goodsName(names, bridge),
      "full",
      () => <Ship look={look} console />,
      aboard ? 0 : 2,
      aboard ? undefined : t("ui-stand-console-aground"),
      t("ui-stand-console-about"),
    );
  }
  //: The ground console (D-242): every hull of one's own, and the same orders
  //: the bridge gives. It exists because a crew that dies in flight leaves a
  //: hull with no edges -- unreachable on foot and deaf to every order -- and
  //: this world does not build traps with no way out.
  const groundBridge = firstOfClass(book, stations, GROUND_BRIDGE);
  if (groundBridge !== undefined) {
    single(
      "ground",
      goodsName(names, groundBridge),
      "full",
      () => <Ship look={look} ground />,
      1,
      undefined,
      t("ui-stand-ground-console-about"),
    );
  }

  //: The ship's own card stands in **every** compartment (D-240). It used to
  //: be hidden wherever the console was, so the room the bridge stood in --
  //: usually the base -- was the one room aboard with no way to read the hull.
  //: Nothing on the card moves the ship, so nothing on it asks for the bridge.
  if (yard !== undefined || aboard) {
    //: Aboard the window is the ship, on the ground it is the yard the ship is
    //: laid down and moored at: one panel, and its name says which of the two
    //: the player is looking at.
    single(
      "ship",
      aboard
        ? t("ui-stand-ship")
        : yard !== undefined
          ? goodsName(names, yard)
          : className(names, SPACEPORT),
      "full",
      () => <Ship look={look} />,
      1,
      undefined,
      aboard ? t("ui-stand-ship-about") : t("ui-stand-yard-about"),
    );
  }

  //: The garden appears **with the first strip**, not with fertile ground: a
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
      t("ui-stand-farm"),
      "full",
      () => <Farm look={look} />,
      3,
      t("ui-stand-farm-strips", { count: strips }),
      t("ui-stand-farm-about"),
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
      name: t("ui-stand-forage"),
      kind: "full",
      rank: foraging.state === "idle" ? 2 : 0,
      state:
        foraging.state === "found" && foraging.found
          ? t("ui-stand-forage-found", {
              goods: goodsName(names, foraging.found.goods),
              units: foraging.found.units,
            })
          : searching
            ? t("ui-stand-forage-searching")
            : t("ui-stand-forage-area", { area: foraging.area.toFixed(0) }),
      running: searching
        ? { until: foraging.ready_at as string, since: foraging.started_at }
        : undefined,
      about: t("ui-stand-forage-about"),
      view: () => <Forage look={look} />,
    });
  }
  //: A library and a hall are machines (D-176, D-215): both are read off the bench.
  if (anyOfClass(book, stations, "library")) {
    single(
      "library",
      t("ui-stand-library"),
      "full",
      () => <Library look={look} />,
      1,
      undefined,
      t("ui-stand-library-about"),
    );
  }
  if (look.city && anyOfClass(book, stations, "administration")) {
    single(
      "hall",
      t("ui-stand-hall"),
      "full",
      () => <Admin look={look} />,
      1,
      undefined,
      t("ui-stand-hall-about"),
    );
  }
  const terminal = firstOfClass(book, stations, TERMINAL);
  if (terminal !== undefined) {
    const mine = (look.stall ?? []).length;
    single("market", goodsName(names, terminal), "full",
      () => <Market look={look} />, 1,
      mine > 0 ? t("ui-stand-market-mine", { count: mine }) : undefined,
      t("ui-stand-market-about"));
  }

  //: The location's own windows, last and in one group: they are about the place
  //: rather than about work, and a machine deserves attention before a nameplate.
  //: Each is one intention -- storage, hauling, building, the land itself --
  //: instead of one "Место" holding whatever was left over.
  const own = disposes(look);
  const node = look.node;
  const home = api.houseOf(node);

  //: Storage of the place, for everyone (D-192, D-204): the floor and the
  //: chests answer one question -- "where do my things go here". It is not a
  //: window of its own any more (D-238): things lie **in** something, and the
  //: surface belongs to whatever holds it -- a roofed room to the building,
  //: bare ground to the land. Two windows about one place were two answers to
  //: the same question.
  //:
  //: The floor comes with every node one stands in, so in practice there is
  //: always a surface and the roof alone decides whose window holds it;
  //: `stores` is the guard for a node that arrives without either, and it is
  //: what keeps an empty tile from appearing then.
  const room = look.floor?.space;
  //: The floor exists only where a house does, so its area is the answer to
  //: "is there one" -- there is no second field saying the same (D-225).
  const roofed = (room?.area ?? 0) > 0;
  const stores = Boolean(look.floor) || (look.storages ?? []).length > 0;
  //: The open ground beside the house (D-244). A node has two surfaces now, and
  //: the land window owns this one: it is there whether a house stands or not,
  //: and it is gone only when the house covers the whole plot -- then there is
  //: no ground left to put anything on.
  const outside = look.ground;
  const openGround =
    (outside?.space.area ?? 0) > 0 || (outside?.things.length ?? 0) > 0;

  //: The wagon is an object of the node like any machine, only one harnesses
  //: to it instead of working at it (D-157).
  const convoy = look.convoy ?? null;
  const carts = (look.vehicles ?? []).filter((t) => !t.taken);
  if (convoy || carts.length > 0) {
    single(
      "convoy",
      t("ui-stand-convoy"),
      "full",
      () => <Convoy look={look} />,
      3,
      convoy
        ? t("ui-stand-convoy-hold", {
            mass: convoy.mass.toFixed(0),
            capacity: convoy.capacity.toFixed(0),
          })
        : t("ui-stand-convoy-standing", { goods: goodsName(names, carts[0].goods) }),
      t("ui-stand-convoy-about"),
    );
  }

  //: The building is the holder's, and one window covers its whole story:
  //: build, demolish, place the machines and furniture into it (D-106,
  //: D-205) -- and what lies on its floor and in its chests. A guest gets the
  //: window too, with the storage half alone: the floor of a room one stands
  //: in is everybody's business (D-192), the building of it is not.
  //: Aboard neither window has an answer (D-240): there is no ground under a
  //: hull, so a compartment is not built, not bought, not fenced and no city is
  //: founded in it. One window instead, and it answers the one question a
  //: compartment has -- what stands in it and what lies in it.
  if (aboard) {
    single(
      "berth",
      t("ui-stand-berth"),
      "full",
      () => <Berth look={look} />,
      3,
      room
        ? t("ui-stand-floor", {
            used: room.used.toFixed(0),
            area: room.area.toFixed(0),
          })
        : undefined,
      t("ui-stand-berth-about"),
    );
  }
  if (storey !== null) {
    single(
      "storey",
      t("ui-stand-storey"),
      "full",
      () => <Storey look={look} />,
      3,
      room
        ? t("ui-stand-floor", {
            used: room.used.toFixed(0),
            area: room.area.toFixed(0),
          })
        : undefined,
      t("ui-stand-storey-about"),
    );
  }
  if (storey === null && !aboard && ((own && node) || (roofed && stores))) {
    single(
      "house",
      t("ui-stand-house"),
      "full",
      () => (
        <>
          {own && node && <House look={look} values={values} />}
          {roofed && stores && <Ground look={look} where="floor" />}
        </>
      ),
      3,
      home.area > 0
        ? t("ui-stand-house-size", { area: home.area.toFixed(0), floors: home.floors })
          + (roofed && room
            ? t("ui-stand-house-floor", {
                used: room.used.toFixed(0),
                area: room.area.toFixed(0),
              })
            : "")
          + (home.condition == null
            ? ""
            : t("ui-stand-house-condition", { condition: home.condition.toFixed(0) }))
        : home.sites.length > 0
          ? t("ui-stand-house-building")
          : t("ui-stand-house-absent"),
      t("ui-stand-house-about"),
    );
  }

  //: The Forerunners' reactor: it feeds the city and it is running out (D-232).
  //: Its own thing of the place rather than a line under a machine, because the
  //: day it goes silent is the day the city must already be standing on its own
  //: coal -- and that day has to be seen from far off.
  if (node?.reactor_until) {
    single(
      "reactor",
      t("ui-stand-reactor"),
      "bench",
      () => <Reactor look={look} />,
      2,
      reactorState(node.reactor_until),
      t("ui-stand-reactor-about"),
    );
  }

  //: The land itself: whose, the name, the door, the purchase and the founding
  //: of a city -- and, under an open sky, whatever lies on it (D-238). Shown
  //: to guests too: ownership is a public fact (D-178), and what lies on the
  //: ground is taken by whoever was let in (D-192). An empty plot for sale is
  //: the main thing of its node, hence the rank.
  const forSale = Boolean(node && !node.owner && (api.isWild(node) || node.price !== undefined));
  const owned = Boolean(node?.owner || node?.owner_city);
  //: The land window is shown for what it always was -- ownership, the door,
  //: the purchase -- and now also for the ground itself, which a built-up plot
  //: has as much as an empty one.
  const bare = openGround;
  if (storey === null && !aboard && (forSale || owned || bare)) {
    single(
      "plot",
      t("ui-stand-plot"),
      "full",
      () => (
        <>
          <Plot look={look} />
          {openGround && <Ground look={look} where="ground" />}
        </>
      ),
      forSale ? 1 : 3,
      node?.cut_off
        ? t("ui-stand-plot-cut-off")
        : node?.gated
          ? t("ui-stand-plot-gated")
          : forSale
            ? node?.price !== undefined
              ? t("ui-stand-plot-price", { price: api.tk(node.price) })
              : t("ui-stand-plot-wild")
            : openGround && outside
              ? t("ui-stand-plot-ground", {
                  used: outside.space.used.toFixed(0),
                  area: outside.space.area.toFixed(0),
                })
              : api.isMine(look)
                ? undefined
                : node?.owner
                  ? t("ui-stand-plot-owner", { owner: node.owner })
                  : t("ui-stand-plot-city", { city: node?.owner_city ?? "" }),
      //: On land nobody holds and nobody sells, the window has no plot half
      //: to show: the hint must promise only what the player will find.
      forSale || owned ? t("ui-stand-plot-about") : t("ui-stand-plot-bare-about"),
    );
  }

  return things.sort((a, b) => a.rank - b.rank);
}
