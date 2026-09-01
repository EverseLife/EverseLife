// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/**
 * The terminal's own shelf: what lies on the counter and how it comes back.
 *
 * Split out of `panels/Market.tsx` when the panel passed 800 lines (CLAUDE.md):
 * the left column chooses a position and writes an order, this is the counter
 * itself -- stacks, how much of each is still one's own to move, and the take.
 *
 * **A row is a stack, an order is a pair.** The engine frees goods by goods
 * and tier (`market.counter._free`), while the shelf shows the stacks that
 * pair is split into. So the free part is laid out over the rows -- by
 * `shareFree`, in the order a take would move them rather than the order they
 * arrived in: two rows of one pair must not each offer the whole remainder, or
 * the button promises four and two arrive.
 */

import { useState } from "react";

import type { RecipeBook, Thing } from "../../api";
import { Amount } from "../../Amount";
import { chosen, tally } from "../../amounts";
import { grip, noDrag } from "../../drag";
import { GoodsMark } from "../../Glyph";
import { onEnter } from "../../keys";
import { t } from "../../locale";
import { shareFree, type Position } from "../../market";
import { flavorText, goodsKeyName, tierName, type Names } from "../../names";

export function Shelf({
  things,
  book,
  names,
  choice,
  mark,
  take,
  busy,
  free,
}: {
  things: Thing[];
  book: RecipeBook | null;
  names: Names | null;
  choice: Position | null;
  mark: (p: Position) => void;
  take: (stack: Thing, amount: number) => void;
  busy: boolean;
  /** How much of this goods and tier is not under an order in this node. */
  free: (goods: string, tier: string) => number;
}) {
  //: How much of each stack to take back. Empty means the whole of it.
  const [parts, setParts] = useState<Record<string, number | null>>({});
  if (things.length === 0) {
    return <p className="note">{t("ui-market-terminal-empty")}</p>;
  }
  const loose = shareFree(things, free);
  return (
    <table>
      <tbody>
        {/* The row is `stack`, not `t`: `t` is the locale's now, and a stack
            shadowing it would turn a label into a call on a thing. */}
        {things.map((stack) => {
          const name = stack.key ?? stack.goods;
          const shown = stack.flavor ? flavorText(names, stack.flavor) : goodsKeyName(names, name);
          const selected = choice?.goods === name && choice?.tier === stack.tier;
          const mine = loose.get(stack.id) ?? 0;
          const held = stack.amount - mine;
          const part = chosen(parts[stack.id] ?? null, mine);
          return (
            <tr
              key={stack.id}
              className={`pick ${selected ? "picked" : ""}`}
              role="button"
              tabIndex={0}
              aria-pressed={selected}
              aria-label={t("ui-market-row", { goods: shown, tier: tierName(names, stack.tier) })}
              onClick={() => mark({ goods: name, tier: stack.tier })}
              onKeyDown={(e) => onEnter(e, () => mark({ goods: name, tier: stack.tier }))}
              {...grip({
                item: stack.id,
                goods: stack.goods,
                //: What may be dragged out is what may be taken by the button:
                //: they send the same command, and D-238 made them one pair.
                label: shown,
                amount: mine,
                zone: "terminal",
                tier: stack.tier,
                key: stack.key ?? undefined,
              })}
              //: After the spread, and it has to be: `grip` makes everything
              //: draggable, and a row under an order to the last unit would
              //: drag out at nought -- a counted piece skips the "how much"
              //: (`drag.askless`) and sends a take of zero, which is the very
              //: refusal this row exists to prevent.
              draggable={mine > 0}
            >
              <td>
                <GoodsMark book={book} goods={stack.goods} />
                {shown}
              </td>
              <td className="num">{tally(stack.goods, stack.amount)}</td>
              <td className="note">
                {stack.quality == null
                  ? ""
                  : `${stack.quality.toFixed(0)} · ${tierName(names, stack.tier)}`}
                {/* Said in the row and not in a tooltip on the dead button:
                    a disabled button takes no pointer, so its title never
                    reaches anybody. */}
                {held > 0 ? ` · ${t("ui-market-row-pledged", { held: tally(stack.goods, held) })}` : ""}
              </td>
              <td onClick={(e) => e.stopPropagation()} {...noDrag}>
                <Amount
                  goods={stack.goods}
                  value={parts[stack.id] ?? null}
                  max={mine}
                  onChange={(value) => setParts((was) => ({ ...was, [stack.id]: value }))}
                />
              </td>
              <td>
                <button
                  className="quiet"
                  onClick={(e) => {
                    e.stopPropagation();
                    take(stack, part);
                  }}
                  disabled={busy || part <= 0}
                >
                  {t("ui-market-take")}
                </button>
              </td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}
