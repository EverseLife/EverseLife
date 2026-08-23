// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/** What the location's panels share: their props, the place signs, the checks. */


/**
 * The location and everything on it (D-089, D-106, D-116, D-150, D-204, D-205).
 *
 * The windows are cut by intent, not by where the code happened to grow, and
 * each stands on its own in the location's row (`Stand.tsx`):
 *
 * - **Участок** -- everything about the land itself: whose it is and what it is
 *   called, the door and the two lists (D-204), buying an empty plot, founding
 *   a city (D-159). Shut stops entry, never passage, so a neighbour is never
 *   cut off from their home;
 * - **Дом** -- build, then furnish: the walls and their demolition (D-205), and
 *   the machines and furniture that go into the house and take its slots
 *   (D-106, D-150). Working at somebody's machine is another matter: the
 *   machine has a row of its own;
 * - **На земле** -- storage, for everyone: the floor where whoever got in puts
 *   things down and picks them up (D-192, D-204), and the chests standing in
 *   the room (D-181). The door and the chest are the protection, not a rule;
 * - **Обоз** -- the wagon: harnessing, and the hold that carries what hands
 *   cannot (D-157);
 * - **Лес / Камни / Луг** -- extraction by the sign of the land (D-177), one
 *   row per sign, next to the other work of the place.
 *
 * Citizenship lives in the administration window (`Admin.tsx`): one joins a
 * city where the city makes its decisions (D-155, D-160). The former "Место"
 * window -- seven unrelated sections under one name -- is gone.
 */

import type { RecipeBook } from "../../api";
import * as api from "../../api";
import type { Look } from "../../api";


export type Props = {
  look: Look;
  busy: boolean;
  act: (what: () => Promise<unknown>) => Promise<void>;
};

/** Whether the viewer disposes of this node: the holder, or the authority on civic land.
 *
 * Repeats `station.may_build` on the client: the same three cases, and the
 * windows of the location are shown by them.
 */
export function disposes(look: Look): boolean {
  const node = look.node;
  if (!node) return false;
  if (node.owner) return api.isMine(look);
  //: Nobody's land outside a city: work on it is open to everyone (D-198).
  if (api.isWild(node)) return true;
  return Boolean(look.city?.powers.includes("laws"));
}

/** Human-readable titles of place signs.
 *
 * The keys are the node properties themselves, and those come from the vault
 * in Russian -- they are game data, not identifiers. A key translated to
 * English silently stopped matching and the window showed the raw property.
 */
export const PLACES: Record<string, string> = {
  лес: "Лес",
  камни: "Камни",
  луг: "Луг",
};

/** Signs of the land offering extraction to this viewer: one row per sign (D-177).
 *
 * The row (`Stand.tsx`) asks what stands here; a forest is as much a thing to
 * work at as a furnace, so each sign earns a row of its own instead of hiding
 * in a catch-all window. Somebody else's forest belongs to its owner: own and
 * nobody's land only.
 */
export function gatherSigns(look: Look, book: RecipeBook | null): string[] {
  const node = look.node;
  if (!node || !(api.isMine(look) || api.isWild(node))) return [];
  const signs: string[] = [];
  for (const operation of book?.operations ?? []) {
    const sign = operation.place;
    if (sign && (node.features ?? []).includes(sign) && !signs.includes(sign)) {
      signs.push(sign);
    }
  }
  return signs;
}

/** What in the hands is equipment of this kind: the kind comes from vault data (D-090). */
export function placeable(look: Look, book: RecipeBook | null, kind: "station" | "furniture") {
  return look.inventory.filter((thing) =>
    (book?.recipes ?? []).some(
      (r) => r.name === thing.goods && r.kind === kind,
    ),
  );
}

