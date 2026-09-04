// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/**
 * The cells a row of goods is made of, and the one line that names it.
 *
 * Shared rather than copied: the same weight column and the same "what kind of
 * thing is this" line stand in the list of things in the hands, in the terminal
 * list beneath it and in the gear block above it (D-305). A second copy would
 * drift on the first new field a thing grows, and the reader would see one
 * stack described two ways on one screen.
 */

import { varietyText, type Thing } from "../../api";
import { flavorText, goodsName, tierName, type Names } from "../../names";
import { t } from "../../locale";
import { trim } from "../../amounts";
import { weightOf } from "../../arrange";

/** One spelling of a row's name (D-251): the flavor by tokens, a written
 *  carrier as "carrier: recipe", everything else by its display word. */
export function labelOf(names: Names | null, thing: Thing): string {
  return thing.flavor
    ? flavorText(names, thing.flavor)
    : thing.recipe
      ? `${goodsName(names, thing.goods)}: ${goodsName(names, thing.recipe)}`
      : goodsName(names, thing.goods);
}

/**
 * What the stack weighs, and what one of it weighs.
 *
 * Two figures, because two questions are asked of the column: "how much of
 * the load is this" reads the whole, "what will one cost me to carry" reads
 * the unit -- and dividing in one's head across a list of thirty rows is not
 * reading. The unit goes under the whole as a note, written as the product
 * the whole is -- "0.2 x 47.5" -- and only where it adds anything: for a
 * stack of one the two figures are the same figure. The product, not words:
 * "0.2 kg each" pushed every name in the table onto a second line, and the
 * count beside the unit is what tells the reader which of the two figures
 * is the unit.
 *
 * `trim`, not a fixed decimal: a seed weighs a gram, and "0.0 kg" over a bag
 * of seeds is a lie the group header can afford (it sums hundreds) but a row
 * cannot.
 */
export function weightCell(thing: Thing) {
  return (
    <td className="num mass">
      {t("ui-inventory-mass", { mass: trim(weightOf(thing)) })}
      {thing.amount !== 1 && (
        <div className="note">
          {t("ui-inventory-mass-each", {
            //: The unit is the whole divided, not the catalog's `mass`: a
            //: vessel's whole counts its fill, and the two figures must agree.
            each: trim(weightOf(thing) / thing.amount),
            amount: trim(thing.amount),
          })}
        </div>
      )}
    </td>
  );
}

/** The one line that says what kind of thing this is. */
export function tells(thing: Thing, names: Names | null): string {
  const parts: string[] = [];
  if (thing.fineness != null) {
    parts.push(t("ui-inventory-fineness", { fineness: String(thing.fineness) }));
    if (thing.maker) parts.push(t("ui-inventory-maker", { maker: thing.maker }));
  } else if (thing.vigor != null) {
    parts.push(
      t("ui-inventory-vigor", {
        variety: varietyText(names, thing.variety) ?? t("ui-inventory-variety"),
        vigor: thing.vigor.toFixed(0),
      }),
    );
  } else if (thing.charge != null) {
    parts.push(t("ui-inventory-charge", { charge: thing.charge.toFixed(0) }));
  } else if (thing.quality != null) {
    parts.push(`${thing.quality.toFixed(0)} · ${tierName(names, thing.tier)}`);
  }
  if (thing.condition < 100)
    parts.push(t("ui-inventory-condition", { condition: thing.condition.toFixed(0) }));
  if (thing.spoils_at) parts.push(spoilAt(thing.spoils_at));
  return parts.join(" · ");
}

function spoilAt(when: string): string {
  const hours = (new Date(when).getTime() - Date.now()) / 3_600_000;
  if (hours <= 0) return t("ui-inventory-spoiled");
  if (hours < 24) return t("ui-inventory-spoils", { hours: String(Math.round(hours)) });
  return t("ui-inventory-keeps", { days: String(Math.round(hours / 24)) });
}
