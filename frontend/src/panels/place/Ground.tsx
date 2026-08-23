// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov


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

import { Refusal, useActions } from "../../actions";
import type { Props } from "./shared";
import { Floor } from "./Floor";
import { Storages } from "./Storages";


/** Everything stored at the place: the floor and the chests, one window (D-181, D-192).
 *
 * The question the window answers is one -- "where do my things go here" -- and
 * the answers used to be scattered: the floor in a window of its own, the
 * chests among the sections of "Место". Now the floor comes first and the
 * chests follow: what lies takes area, what is chested does not, and seeing
 * both side by side is what makes that trade-off legible.
 *
 * The window is for everyone: whoever got in puts things down and picks them
 * up. What keeps a stranger's hands away is the shut door (D-204) and the
 * chest's own lock (D-181) -- not a rule against touching.
 */
export function Ground({ look }: Omit<Props, "busy" | "act" | "book">) {
  //: Own waiting and own refusal: a full yard must refuse this window, not the map.
  const acting = useActions();
  const { busy, act } = acting;
  if (!look.floor && (look.storages ?? []).length === 0) return null;

  return (
    <>
      <Refusal of={acting} />
      <Floor look={look} busy={busy} act={act} />
      <Storages look={look} busy={busy} act={act} />
    </>
  );
}
