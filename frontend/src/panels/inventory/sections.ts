// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/**
 * The inventory table cut into sections: what a group is, what its header
 * says. Pure -- the panel renders what this hands it.
 */

import type { RecipeBook, Thing } from "../../api";
import { tally, trim } from "../../amounts";
import {
  arrange,
  groupId,
  groupKey,
  orderGroups,
  summarize,
  type Grouping,
  type Sorting,
  type Summary,
} from "../../arrange";
import { t } from "../../locale";
import type { Names } from "../../names";

/**
 * The table split into sections by the chosen axis, each sorted the chosen way.
 * One section without a title when there is no grouping.
 */
export function sections(
  things: Thing[],
  group: Grouping,
  sort: Sorting,
  desc: boolean,
  book: RecipeBook | null,
  names: Names | null,
): { id: string; title: string | null; rows: Thing[]; summary: Summary }[] {
  const ordered = arrange(things, sort, desc, names);
  if (group === "none") return [{ id: "", title: null, rows: ordered, summary: summarize([]) }];
  const buckets = new Map<string, Thing[]>();
  for (const thing of ordered) {
    const key = groupKey(book, names, thing, group);
    buckets.set(key, [...(buckets.get(key) ?? []), thing]);
  }
  return orderGroups([...buckets.keys()], group, things, names).map((title) => {
    const rows = buckets.get(title) ?? [];
    //: The title is what the header reads; the id is what the fold is stored
    //: under (D-251). Taken off the first row, because every row of a bucket
    //: gave the same answer -- that is what put them in one bucket.
    const first = rows[0];
    return {
      id: first ? groupId(book, first, group) : title,
      title,
      rows,
      summary: summarize(rows),
    };
  });
}

/**
 * What a folded group says about itself: how much, how good, of how many
 * stacks and how heavy.
 *
 * The count of stacks stays because it is the one thing the fold hides: two
 * lots of ore at 12 and at 13 read as one line here, and the player must see
 * that the line covers two of them before deciding to open it.
 */
export function sums(summary: Summary, stacks: number): string {
  const said: string[] = [];
  if (summary.goods != null) said.push(tally(summary.goods, summary.amount));
  if (summary.quality != null)
    said.push(t("ui-inventory-average", { quality: summary.quality.toFixed(0) }));
  said.push(positions(stacks));
  //: `trim`, the same spelling the rows use: one column, one rounding.
  said.push(t("ui-inventory-mass", { mass: trim(summary.mass) }));
  return ` · ${said.join(" · ")}`;
}

/** "1 позиция", "2 позиции", "5 позиций" -- the count decides the word.
 *
 * The choosing is the message's, not this function's: which counts take which
 * word is a fact about a language, and a language that has one form for all of
 * them -- or six -- cannot be served by a rule written in `if`s here.
 *
 * The number goes twice: as a number, which is the only thing Fluent's plural
 * rules can look at, and as the digits to print. Printing `$count` itself would
 * hand it to the locale's number format, and a thousand stacks would read
 * "1 000 позиций" where every other figure in the row reads "1000".
 */
function positions(count: number): string {
  return t("ui-inventory-positions", { count, shown: String(count) });
}
