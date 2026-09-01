// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/**
 * The factory floor: the node editor of the automats (D-253, wave 5).
 *
 * Machines are cards, wires are curves, and the whole picture is the
 * building's own: columns follow the flow -- what feeds stands left of what
 * eats, computed from the wires the owner drew. A wire's mechanical meaning
 * is the tick's order (the chain flows within one pass); the rest of it is
 * exactly this picture.
 *
 * Programming is choosing (D-253): the recipe list is filtered on the spot
 * from what the client already holds -- the book, `auto.covers` and
 * `auto.barred_inputs` from the public constants, and the player's own
 * `knows` -- nothing travels that the client can derive (D-225). The server
 * re-checks every rule on `auto.program`; this filter only keeps the menu
 * honest.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import type { Look, RecipeBook } from "../api";
import type { Floor, FloorRow, Wire } from "../wire/automat";
import { classOf } from "../classes";
import { Rule } from "../Rule";
import { Refusal, useActions, useBook, useEdition, useNames, useSession } from "../actions";
import { t } from "../locale";
import { goodsName } from "../names";

/** The automat thing class (D-253): the window, like the engine, binds to it. */
const AUTOMATON = "automaton";

/** The card grid, in pixels: positions are computed, never measured. */
const CARD_W = 216;
const CARD_H = 96;
const GAP_X = 56;
const GAP_Y = 16;

type Props = {
  look: Look;
  values: Record<string, any> | null;
};

/** What this machine may be programmed with, by the same rules the server holds. */
function programmable(
  book: RecipeBook | null,
  values: Record<string, any> | null,
  machineKind: string,
  knows: string[],
): string[] {
  if (!book || !values) return [];
  const covers: Record<string, Record<string, number>> = values["auto.covers"] ?? {};
  const barred: Record<string, number> = values["auto.barred_inputs"] ?? {};
  const canon = (name: string | null | undefined) =>
    name == null ? null : (book.synonyms?.[name] ?? name);
  const covered = (station: string | null) =>
    station != null && Boolean(covers[station]?.[machineKind]);
  const clean = (names: string[]) => names.every((one) => !(canon(one)! in barred));

  const recipes = (book.recipes ?? [])
    .filter(
      (r) =>
        covered(canon(r.station ?? null)) &&
        r.kind !== "station" &&
        r.kind !== "money" &&
        !r.roles &&
        knows.includes(r.id ?? r.name) &&
        !((r.id ?? r.name) in barred) &&
        clean(r.inputs ?? []),
    )
    .map((r) => r.id ?? r.name);

  //: Operations are everyone's, at the furnace and in its automat alike:
  //: the station hides among `requires`, exactly as the engine reads it --
  //: and the pyroxite bar reaches their inputs too, as on the server.
  const operations = (book.operations ?? [])
    .filter(
      (o) =>
        (o.requires ?? []).some((w: string) => covered(canon(w))) &&
        clean(o.consumes ?? []),
    )
    .flatMap((o) => (Array.isArray(o.gives) ? o.gives : []))
    .filter((one: string) => !(one in barred));

  return [...new Set([...recipes, ...operations])];
}

/** Columns by the flow: feeders left of the fed (Kahn; a cycle keeps order). */
function columnsOf(items: string[], wires: Wire[]): Map<string, number> {
  const column = new Map<string, number>(items.map((one) => [one, 0]));
  //: A few passes settle any sane floor; a cycle simply stops moving.
  for (let pass = 0; pass < items.length; pass += 1) {
    let moved = false;
    for (const wire of wires) {
      const from = column.get(wire.from);
      const to = column.get(wire.to);
      if (from === undefined || to === undefined) continue;
      if (to <= from && from + 1 <= items.length) {
        column.set(wire.to, from + 1);
        moved = true;
      }
    }
    if (!moved) break;
  }
  return column;
}

export function Factory({ look, values }: Props) {
  const session = useSession();
  const book = useBook();
  const names = useNames();
  const acting = useActions();
  const { busy, act } = acting;

  const [floor, setFloor] = useState<FloorRow[]>([]);
  const [wires, setWires] = useState<Wire[]>([]);
  //: The armed out-port: the first click of a wire being drawn.
  const [armed, setArmed] = useState<string | null>(null);

  const reload = useCallback(async () => {
    const answer = (await session.send("auto.view")) as Floor;
    setFloor(answer.machines ?? []);
    setWires(answer.links ?? []);
  }, [session]);
  //: Reread when the world says so (D-226): a programme, a wire or a payout
  //: of this floor touches the node, never by a client timer.
  const edition = useEdition("automat.", "station.");
  useEffect(() => {
    void reload();
  }, [reload, edition, look.node?.key]);

  const go = (what: () => Promise<unknown>) =>
    act(async () => {
      await what();
      await reload();
    });

  //: The machines standing here that are automats, by class (D-215).
  const machines = useMemo(
    () => (look.bench ?? []).filter((b) => classOf(book, b.goods) === AUTOMATON),
    [look.bench, book],
  );
  const rows = useMemo(() => {
    const known = new Map(floor.map((row) => [row.item, row]));
    return machines.map((m) => ({
      bench: m,
      row: known.get(m.id) ?? null,
    }));
  }, [machines, floor]);

  //: `Look` carries knowledge flattened (`wire/look.compose`): the panel
  //: reads `knows` off the top, there is no `knowledge` object to reach into.
  const knows = look.knows ?? [];

  //: The picture: columns by flow, rows within a column by arrival.
  const layout = useMemo(() => {
    const ids = rows.map(({ bench }) => bench.id);
    const column = columnsOf(ids, wires);
    const depth = new Map<number, number>();
    const spots = new Map<string, { x: number; y: number }>();
    for (const id of ids) {
      const col = column.get(id) ?? 0;
      const row = depth.get(col) ?? 0;
      depth.set(col, row + 1);
      spots.set(id, { x: col * (CARD_W + GAP_X), y: row * (CARD_H + GAP_Y) });
    }
    const cols = Math.max(1, ...[...column.values()].map((c) => c + 1));
    const tall = Math.max(1, ...[...depth.values()]);
    return {
      spots,
      width: cols * (CARD_W + GAP_X) - GAP_X,
      height: tall * (CARD_H + GAP_Y) - GAP_Y,
    };
  }, [rows, wires]);

  if (machines.length === 0) return null;

  const wirePath = (wire: Wire): string | null => {
    const from = layout.spots.get(wire.from);
    const to = layout.spots.get(wire.to);
    if (!from || !to) return null;
    const x1 = from.x + CARD_W;
    const y1 = from.y + CARD_H / 2;
    const x2 = to.x;
    const y2 = to.y + CARD_H / 2;
    const bend = Math.max(24, (x2 - x1) / 2);
    return `M ${x1} ${y1} C ${x1 + bend} ${y1}, ${x2 - bend} ${y2}, ${x2} ${y2}`;
  };

  const drawTo = (target: string) => {
    if (armed === null || armed === target) {
      setArmed(null);
      return;
    }
    const source = armed;
    setArmed(null);
    void go(() => session.send("auto.link", { from: source, to: target }));
  };

  return (
    <section>
      <Refusal of={acting} />
      <h2>
        {t("ui-factory-title")}
        <Rule>{t("ui-factory-rule")}</Rule>
      </h2>
      <p className="note">{armed !== null ? t("ui-factory-wire-armed") : t("ui-factory-hint")}</p>

      <div className="factory" style={{ width: layout.width, height: layout.height }}>
        <svg
          className="factory-wires"
          width={layout.width}
          height={layout.height}
          aria-hidden="true"
        >
          {wires.map((wire) => {
            const path = wirePath(wire);
            if (path === null) return null;
            return (
              <g key={`${wire.from}:${wire.to}`} className="factory-wire-pair">
                {/* The wide invisible twin is the hit area: a hairline is not a button. */}
                <path
                  className="factory-wire-hit"
                  d={path}
                  onClick={() =>
                    go(() => session.send("auto.unlink", { from: wire.from, to: wire.to }))
                  }
                >
                  <title>{t("ui-factory-unlink")}</title>
                </path>
                <path className="factory-wire" d={path} />
              </g>
            );
          })}
        </svg>

        {rows.map(({ bench, row }) => {
          const spot = layout.spots.get(bench.id);
          if (!spot) return null;
          const options = programmable(book, values, bench.goods, knows);
          return (
            <div
              key={bench.id}
              className={"factory-card" + (armed === bench.id ? " armed" : "")}
              style={{ left: spot.x, top: spot.y, width: CARD_W, height: CARD_H }}
            >
              <button
                className="factory-port in"
                title={t("ui-factory-port-in")}
                disabled={busy || armed === null}
                onClick={() => drawTo(bench.id)}
              />
              <button
                className="factory-port out"
                title={t("ui-factory-port-out")}
                disabled={busy}
                onClick={() => setArmed(armed === bench.id ? null : bench.id)}
              />
              <p className="sign">{goodsName(names, bench.goods)}</p>
              <select
                value={row?.recipe ?? ""}
                disabled={busy}
                onChange={(e) => {
                  const chosen = e.target.value;
                  void go(() =>
                    chosen === ""
                      ? session.send("auto.stop", { machine: bench.id })
                      : session.send("auto.program", { machine: bench.id, recipe: chosen }),
                  );
                }}
              >
                <option value="">{t("ui-factory-idle")}</option>
                {options.map((one) => (
                  <option key={one} value={one}>
                    {goodsName(names, one)}
                  </option>
                ))}
                {row?.recipe != null && !options.includes(row.recipe) && (
                  <option value={row.recipe}>{goodsName(names, row.recipe)}</option>
                )}
              </select>
              {row != null && row.backlog > 0 && (
                <p className="note">{t("ui-factory-backlog", { backlog: row.backlog.toFixed(2) })}</p>
              )}
            </div>
          );
        })}
      </div>
    </section>
  );
}
