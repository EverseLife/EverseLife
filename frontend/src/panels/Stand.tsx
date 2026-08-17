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
import type { Look, Session } from "../api";
import { Deadline } from "../Deadline";
import { craftableAt } from "../recipes";
import { Admin } from "./Admin";
import { Farm } from "./Farm";
import { Kitchen } from "./Kitchen";
import { Library } from "./Library";
import { Market } from "./Market";
import { Mine } from "./Mine";
import { Mint } from "./Mint";
import { Nursery } from "./Nursery";
import { Place } from "./Place";
import { Plant } from "./Plant";
import { Rig } from "./Rig";
import { Ship } from "./Ship";
import { Workshop } from "./Workshop";
import type { PowSettings } from "../pow";

/** The terminal is the market building; everything else in a node is machines. */
const TERMINAL = "Терминал маркетплейса";

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

type Props = {
  look: Look;
  session: Session;
  book: any;
  values: Record<string, any> | null;
  pow: PowSettings | null;
};

export function Stand({ look, session, book, values, pow }: Props) {
  const here = look.node?.key ?? "";
  const [chosen, setChosen] = useState<string | null>(() => OPENED.get(here) ?? null);

  const things = assemble({ look, session, book, values, pow });
  //: A remembered choice can vanish -- the machine was carried off while we
  //: were away. Then the first thing worth attention takes over.
  const open =
    things.find((t) => t.id === chosen) ?? things.find((t) => t.kind === "bench") ?? things[0];

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
          <button className="quiet" onClick={() => show(firstBench(things)?.id ?? "")}>
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

const firstBench = (things: Thing[]) =>
  things.find((t) => t.kind === "bench") ?? things[0];

/**
 * The node's objects, in the order they deserve attention.
 *
 * Every entry is conditional on something in `look`: this is the list the old
 * screen built too, only now it is one list instead of a dozen independent
 * `{has.x && <Panel/>}` lines, and each entry carries what it is doing.
 */
function assemble({ look, session, book, values, pow }: Props): Thing[] {
  const things: Thing[] = [];
  const stations = look.node?.stations ?? [];
  const bench = look.bench ?? [];
  const batches = look.batches ?? [];

  /** The batch occupying this machine, if it is ours. */
  const batchAt = (machine: string) => batches.find((b) => b.station === machine);
  /** The machine as it stands in the node: quality, condition, who is at it. */
  const standing = (machine: string) => bench.find((b) => b.goods === machine);

  const machineState = (machine: string) => {
    const it = standing(machine);
    if (!it) return undefined;
    const parts: string[] = [];
    if (it.busy) parts.push(it.mine ? "занята вами" : "занята");
    else parts.push("свободна");
    if (it.quality !== null) parts.push(`кач. ${it.quality.toFixed(0)}`);
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
      view: () => <Mine look={look} session={session} pow={pow} />,
    });
  }

  /**
   * Machines that have a surface of their own beyond ordinary craft: the pot
   * on the hearth, breeding in the nursery, minting, the firebox of a coal station.
   */
  const SPECIAL: Record<string, () => ReactNode> = {
    "Очаг": () => <Kitchen look={look} session={session} />,
    "Селекционный питомник": () => <Nursery look={look} session={session} />,
    "Угольная станция": () => <Plant look={look} session={session} />,
    "Монетная станция": () => <Mint look={look} session={session} values={values} />,
  };

  //: One machine is one row, whatever can be done at it. A hearth is both a
  //: pot and an ordinary bench, and listing it twice would say there are two
  //: hearths in the yard. The surface shows everything the machine offers.
  for (const machine of stations) {
    const special = SPECIAL[machine];
    const recipes = craftableAt(book, machine, look.knows).length > 0;
    if (!special && !recipes) continue;
    const batch = batchAt(machine);
    things.push({
      id: `bench:${machine}`,
      name: machine,
      kind: "bench",
      rank: batch ? 0 : 1,
      state: batch ? `партия · ${batch.output}` : machineState(machine),
      running: batch ? { until: batch.ready_at, since: batch.started_at } : undefined,
      view: () => (
        <>
          {special?.()}
          {recipes && (
            <Workshop machine={machine} book={book} look={look} session={session} />
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

  //: A rig, only where there is one to work with: standing in the node, or in
  //: the hands waiting to be placed on a vein. The panel used to keep itself
  //: silent while the row above it did not, so "Буровая" stood in every
  //: location in the world -- including those with nothing to drill.
  const rigInHand = look.inventory.some((t) => t.goods === "Буровая установка");
  if (look.rig_here || rigInHand) {
    single(
      "rig",
      "Буровая",
      "bench",
      () => <Rig look={look} session={session} />,
      1,
      look.rig_here ? undefined : "в руках: поставить на жилу",
    );
  }

  //: The ship, where there is one to command: standing at a spaceport or
  //: aboard. Aboard the row is the ship itself -- the map is already showing
  //: its rooms, and this panel adds what the map cannot: thrust against mass.
  const shipHere =
    (look.node?.stations ?? []).includes("Космодром") ||
    (look.node?.features ?? []).includes("борт");
  if (shipHere) {
    single("ship", "Корабль", "full", () => <Ship look={look} session={session} />);
  }

  if ((look.node?.fertility ?? 0) > 0) {
    single("farm", "Делянки", "full", () => <Farm look={look} session={session} />);
  }
  if (look.node?.library) {
    single("library", "Библиотека", "full", () => <Library look={look} session={session} />);
  }
  if (look.city?.hall) {
    single("hall", "Администрация", "full", () => <Admin look={look} session={session} />);
  }
  if (stations.includes(TERMINAL)) {
    const mine = (look.stall ?? []).length;
    single("market", "Терминал маркетплейса", "full",
      () => <Market look={look} session={session} values={values} />, 1,
      mine > 0 ? `вашего товара: ${mine}` : undefined);
  }

  //: The place itself, last: the plot, the building, the machines standing
  //: here, the furniture, the chest, the floor and the convoy. Six sections of
  //: the old screen were never six things -- they were one, the place and what
  //: is in it.
  const room = look.floor?.space;
  things.push({
    id: "place",
    name: "Место",
    kind: "full",
    rank: 3,
    state: room ? `${room.used.toFixed(0)} / ${room.area.toFixed(0)} м²` : undefined,
    view: () => <Place look={look} session={session} book={book} />,
  });

  return things.sort((a, b) => a.rank - b.rank);
}
